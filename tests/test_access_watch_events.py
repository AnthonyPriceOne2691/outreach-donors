"""Сторож входов и прав — события доступа (`ops/access_events.py`, `ops/access_event_words.py`,
`ops/access_mark.py`), аудит 20.11.2026.

Здесь держится:

- **какие записи — события:** заведение учётки и правка доступа с целью «user:N»; общая «правка»
  письма или ящика, вход и смена пароля — нет;
- **новый админ — своим сообщением:** заведён админом, получил роль «Админ» или право «заводить
  учётки», снова включён; заведён командой на сервере — «кто — команда на сервере»;
- **смена доступа — что добавлено и что снято** против прежнего, чья учётка и кто менял; прежнего
  журнал не знает — тем, что в записи;
- **события прохода — одним сообщением со списком** в потолке Telegram;
- **о каждом событии — один раз:** курсор и сказанное в окне сверки, через перезапуск (Redis);
  Telegram не принял — ещё раз и только то, что не принял, даже когда событие вышло из окна сверки;
  бот не настроен — строка журнала и есть сказанное; Redis молчит — память процесса, а прежнее
  потом не теряет и не повторяет; запись, зафиксированная позже чужой, не теряется; база из копии —
  с конца; события сверх пачки — следующим проходом; битая память — не падение;
- **роли словами — как на экране учёток.**
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from backend.config import alerts as alerts_cfg
from backend.features.access.administration import create_user, update_access
from backend.features.access.repository import AccessRepository, actor_of
from backend.features.core.domain import AuditAction, UserRole
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.ops import access_events, access_mark, alarm_feed
from backend.features.ops.access_event_words import ADMIN, CHANGED, ROLE_TITLES, notices
from backend.features.ops.access_events import Access, Batch, Event
from backend.features.ops.access_mark import Mark
from backend.features.ops.alarms import Concern, marked
from backend.shared.alerts import MAX_CHARS, SOURCE
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import WATCH_MINUTE
from tests.test_alarm_concern import _plain
from tests.test_mail_watch_seams import FakeRedis

BOSS = "boss@team.example.test"
NEW = "new@team.example.test"
OP = "op@team.example.test"
OLD = "old@team.example.test"
PASSWORD = "пароль-для-теста"

#: Минута событий — за минуту до прохода (03:10 МСК): проход идёт минутой ленты тревог.
AT = WATCH_MINUTE - timedelta(minutes=1)
#: Давно — за окном сверки: о таком сторож с последней записи не говорит.
LONG_AGO = WATCH_MINUTE - timedelta(days=3)

LABELS_TS = Path(__file__).resolve().parent.parent / "frontend" / "src" / "api" / "labels.ts"

WHERE = "Где смотреть: экран «Журнал действий»."


async def stamp(session: AsyncSession, at: datetime) -> None:
    """Записи журнала этой транзакции — на минуту `at`. Время записи ставит база (начало
    транзакции, одно на все её записи), а слова называют минуту — тесту нужна своя."""
    await session.execute(
        update(AuditLogModel)
        .where(AuditLogModel.created_at == func.now())
        .values(created_at=at)
        .execution_options(synchronize_session="fetch")
    )


async def account(
    session: AsyncSession, email: str, role: UserRole = UserRole.OPERATOR
) -> UserModel:
    """Учётка, заведённая давно: о её заведении сторож уже не говорит."""
    user = await AccessRepository(session).create(email=email, password=PASSWORD, role=role)
    await stamp(session, LONG_AGO)
    return user


@pytest.fixture
async def boss(session: AsyncSession) -> UserModel:
    """Админ, который заводит учётки и меняет доступ."""
    return await account(session, BOSS, UserRole.ADMIN)


async def changed(
    session: AsyncSession,
    author: UserModel,
    user: UserModel,
    *,
    at: datetime = AT,
    **access: object,
) -> None:
    """Правка доступа тем же путём, что раздел «Учётки», — минутой `at`."""
    await update_access(session, user_id=user.id, author=actor_of(author), **access)  # type: ignore[arg-type]
    await stamp(session, at)


async def read(
    session: AsyncSession, *, cursor: int | None = None, told: set[int] | None = None
) -> Batch:
    return await access_events.read(session, cursor=cursor, told=told or set(), now=WATCH_MINUTE)


def said(batch: Batch) -> list[str]:
    """Сообщения прохода — как они уйдут: с пометкой последней строкой."""
    return [marked(notice.text, notice.concern) for notice in notices(batch.events, WATCH_MINUTE)]


async def top(session: AsyncSession) -> int:
    found = await session.scalar(select(func.max(AuditLogModel.id)))
    assert found is not None
    return found


# --- новый админ -----------------------------------------------------------------------------


async def test_a_new_admin_is_its_own_alarm(session: AsyncSession, boss: UserModel) -> None:
    await create_user(session, email=NEW, role=UserRole.ADMIN, author=actor_of(boss))
    await stamp(session, AT)

    assert said(await read(session)) == [
        f"тревога: Новый админ — {NEW}. В 03:10 МСК.\n"
        f"Заведена с ролью «Админ»; кто — {BOSS}.\n"
        f"{WHERE}\n{ADMIN.words}"
    ]


async def test_an_admin_made_on_the_server_names_the_command(session: AsyncSession) -> None:
    """Запись без автора — учётку завели командой на сервере (`outreach user-add`)."""
    await AccessRepository(session).create(email=NEW, password=PASSWORD, role=UserRole.ADMIN)
    await stamp(session, AT)

    [text] = said(await read(session))

    assert text.startswith(f"тревога: Новый админ — {NEW}. В 03:10 МСК.\n")
    assert f"Заведена с ролью «Админ»; кто — команда на сервере.\n{WHERE}" in text


async def test_an_operator_raised_to_admin_says_what_was_added(
    session: AsyncSession, boss: UserModel
) -> None:
    op = await account(session, OP)
    await changed(session, boss, op, role=UserRole.ADMIN)

    assert said(await read(session)) == [
        f"тревога: Учётка стала админом — {OP}. В 03:10 МСК.\n"
        "Роль «Оператор» → «Админ»; добавлено: «отправлять письма», «домены рассылки», "
        f"«заводить учётки», «менять потолки расхода»; кто — {BOSS}.\n"
        f"{WHERE}\n{ADMIN.words}"
    ]


async def test_the_right_to_make_accounts_is_an_admin_too(
    session: AsyncSession, boss: UserModel
) -> None:
    """Оператор с правом «заводить учётки» заведёт себе админа сам — значит, он уже админ."""
    op = await account(session, OP)
    await changed(session, boss, op, permissions={"users": True})

    [text] = said(await read(session))

    assert text.startswith(f"тревога: Учётка стала админом — {OP}. В 03:10 МСК.\n")
    assert f"Добавлено: «заводить учётки»; кто — {BOSS}." in text


async def test_an_admin_switched_back_on_is_an_admin_again(
    session: AsyncSession, boss: UserModel
) -> None:
    second = await account(session, NEW, UserRole.ADMIN)
    await changed(session, boss, second, at=LONG_AGO, is_active=False)
    await changed(session, boss, second, is_active=True)

    [text] = said(await read(session))

    assert text.startswith(f"тревога: Учётка стала админом — {NEW}. В 03:10 МСК.\n")
    assert f"Учётка снова включена; кто — {BOSS}." in text


# --- смена доступа ---------------------------------------------------------------------------


async def test_a_granted_right_names_whose_who_and_what(
    session: AsyncSession, boss: UserModel
) -> None:
    op = await account(session, OP)
    await changed(session, boss, op, permissions={"send": True})

    assert said(await read(session)) == [
        f"тревога: Изменён доступ учётки {OP}. В 03:10 МСК.\n"
        f"Добавлено: «отправлять письма»; кто — {BOSS}.\n"
        f"{WHERE}\n{CHANGED.words}"
    ]


async def test_a_role_change_says_what_was_taken(session: AsyncSession, boss: UserModel) -> None:
    op = await account(session, OP)
    await changed(session, boss, op, role=UserRole.DONORS)

    [text] = said(await read(session))

    assert (
        "Роль «Оператор» → «Аутрич»; снято: «Продажи», «Рекламодатели», «смотреть расход»; "
        f"кто — {BOSS}." in text
    )


async def test_a_switched_off_account_is_said_without_the_list_of_rights(
    session: AsyncSession, boss: UserModel
) -> None:
    op = await account(session, OP)
    await changed(session, boss, op, is_active=False)

    [text] = said(await read(session))

    assert f"Учётка отключена; кто — {BOSS}." in text
    assert "снято" not in text


async def test_a_new_operator_is_said_as_an_account_made(
    session: AsyncSession, boss: UserModel
) -> None:
    await create_user(session, email=NEW, role=UserRole.OPERATOR, author=actor_of(boss))
    await stamp(session, AT)

    [text] = said(await read(session))

    assert text.startswith(f"тревога: Заведена учётка {NEW}. В 03:10 МСК.\n")
    assert f"Заведена с ролью «Оператор»; кто — {BOSS}." in text


async def test_without_the_account_history_the_record_speaks_for_itself(
    session: AsyncSession,
) -> None:
    """Учётки без записи о заведении журнал не знает: что добавилось — не видно, видно записанное."""
    session.add_all(
        [
            AuditLogModel(
                action=AuditAction.USER_UPDATED,
                target="user:900001",
                details={"permissions": {"send": True, "sales": False}},
                created_at=AT,
            ),
            AuditLogModel(
                action=AuditAction.USER_UPDATED,
                target="user:900002",
                details={"role": "admin"},
                created_at=AT,
            ),
        ]
    )
    await session.flush()

    admins, others = said(await read(session))

    assert admins.startswith("тревога: Учётка стала админом — учётка №900002. В 03:10 МСК.\n")
    assert "Роль — «Админ»; кто — команда на сервере." in admins
    assert (
        "Поверх роли выдано: «отправлять письма»; снято с роли: «Продажи»; кто — команда на сервере."
        in others
    )


async def test_other_records_are_not_access_events(session: AsyncSession, boss: UserModel) -> None:
    """«Правка» в журнале общая — письмо, ящик; вход и смена пароля — тоже не события доступа."""
    journal = AccessRepository(session)
    await journal.record(AuditAction.USER_UPDATED, author_id=boss.id, target="sender:3")
    await journal.record(AuditAction.USER_UPDATED, author_id=boss.id, target="message:9")
    await journal.note_login(boss.id)
    await journal.set_password(boss.id, "другой-пароль-для-теста", one_time=False)
    await stamp(session, AT)

    batch = await read(session)

    assert batch.events == []
    assert batch.reach == await top(session)


async def test_changes_of_one_pass_are_one_message(session: AsyncSession, boss: UserModel) -> None:
    op = await account(session, OP)
    await changed(session, boss, op, at=AT - timedelta(minutes=2), permissions={"send": True})
    await create_user(session, email=NEW, role=UserRole.DONORS, author=actor_of(boss))
    await stamp(session, AT - timedelta(minutes=1))
    await changed(session, boss, op, is_active=False)

    assert said(await read(session)) == [
        "тревога: Изменён доступ учёток — 3. С 03:08 МСК.\n"
        f"• 03:08 {OP} — добавлено: «отправлять письма»; кто — {BOSS}\n"
        f"• 03:09 {NEW} — заведена с ролью «Аутрич»; кто — {BOSS}\n"
        f"• 03:10 {OP} — учётка отключена; кто — {BOSS}\n"
        f"{WHERE}\n{CHANGED.words}"
    ]


def _event(number: int, whose: str) -> Event:
    return Event(
        id=number,
        at=AT,
        created=False,
        whose=whose,
        author=BOSS,
        details={"permissions": {"send": True}},
        before=Access(UserRole.OPERATOR),
        after=Access(UserRole.OPERATOR, overrides={"send": True}),
    )


def test_a_long_list_names_twenty_and_counts_the_rest() -> None:
    [notice] = notices([_event(n, f"op{n:02}@team.example.test") for n in range(30)], WATCH_MINUTE)
    text = marked(notice.text, notice.concern)

    assert text.count("\n• ") == 21
    assert text.endswith(f"\n• и ещё 10\n{WHERE}\n{CHANGED.words}")


def test_long_lines_are_cut_to_the_telegram_ceiling_and_keep_the_mark() -> None:
    whose = "x" * 300 + "@team.example.test"
    [notice] = notices([_event(n, f"{n:02}{whose}") for n in range(30)], WATCH_MINUTE)
    text = marked(notice.text, notice.concern)
    shown = text.count("\n• ") - 1

    assert 0 < shown < 20
    assert text.endswith(f"\n• и ещё {30 - shown}\n{WHERE}\n{CHANGED.words}")
    assert len(f"{SOURCE}: {text}") <= MAX_CHARS


# --- слова -----------------------------------------------------------------------------------


@pytest.mark.parametrize("concern", [ADMIN, CHANGED], ids=["новый админ", "смена доступа"])
def test_the_marks_are_plain_and_say_what_to_do(concern: Concern) -> None:
    _plain(concern)
    assert concern.cause


def test_roles_are_named_like_the_users_screen() -> None:
    """Слова ролей — копия экрана учёток: две копии расходятся молча."""
    source = LABELS_TS.read_text(encoding="utf-8")
    found = re.search(r"const ROLE_TITLES\b[^=]*=\s*\{(.*?)\n\};", source, re.S)
    assert found is not None, f"в {LABELS_TS.name} нет ROLE_TITLES"
    screen = dict(re.findall(r"^\s*([a-z_]+): '([^']+)',", found.group(1), re.M))

    assert screen == {role.value: title for role, title in ROLE_TITLES.items()}
    assert set(ROLE_TITLES) == set(UserRole)


# --- о каждом событии — один раз --------------------------------------------------------------


class Telegram:
    """Подставной Telegram сторожа: что ему сказали и что он ответил — по очереди из `answers`,
    кончились — `accepts`."""

    def __init__(self) -> None:
        self.said: list[str] = []
        self.answers: list[bool] = []
        self.accepts = True

    async def send(self, text: str) -> bool:
        self.said.append(text)
        return self.answers.pop(0) if self.answers else self.accepts

    @property
    def titles(self) -> list[str]:
        return [text.splitlines()[0] for text in self.said]


@pytest.fixture
def telegram(monkeypatch: pytest.MonkeyPatch) -> Telegram:
    found = Telegram()
    monkeypatch.setattr(access_mark, "send_alert", found.send)
    return found


@pytest.fixture
def bot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Бот тревог задан: отказ Telegram — не сказанное."""
    monkeypatch.setattr(alerts_cfg, "TELEGRAM_BOT_TOKEN", "made-up-token")
    monkeypatch.setattr(alerts_cfg, "TELEGRAM_CHAT_ID", "made-up-chat")


@pytest.fixture
def redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    """Redis памяти — подделкой: переживает «перезапуск» (новый `Mark`)."""
    found = FakeRedis()
    monkeypatch.setattr(alarm_feed, "connection", lambda: found)
    return found


async def one_pass(session: AsyncSession, mark: Mark, now: datetime = WATCH_MINUTE) -> None:
    """Проход сторожа по событиям: прежнее из Redis, чтение, сказанное."""
    mark.recall()
    batch = await access_events.read(session, cursor=mark.cursor, told=set(mark.told), now=now)
    await mark.tell(batch, now)


@pytest.mark.usefixtures("bot")
async def test_an_event_is_said_once_even_across_a_restart(
    session: AsyncSession, boss: UserModel, telegram: Telegram, redis: FakeRedis
) -> None:
    op = await account(session, OP)
    await changed(session, boss, op, permissions={"send": True})
    mark = Mark()
    await one_pass(session, mark)
    await one_pass(session, mark)
    await one_pass(session, Mark())  # перезапуск процесса разбора

    assert telegram.titles == [f"тревога: Изменён доступ учётки {OP}. В 03:10 МСК."]
    assert redis.hashes[access_mark.key()]["cursor"] == str(await top(session))


@pytest.mark.usefixtures("bot")
async def test_a_refused_message_is_said_again_and_only_it(
    session: AsyncSession, boss: UserModel, telegram: Telegram
) -> None:
    """Новый админ ушёл, а смену доступа Telegram не принял: следующий проход повторяет только её."""
    op = await account(session, OP)
    await create_user(session, email=NEW, role=UserRole.ADMIN, author=actor_of(boss))
    await stamp(session, AT)
    await changed(session, boss, op, permissions={"send": True})
    telegram.answers = [True, False]
    mark = Mark()

    for _ in range(3):
        await one_pass(session, mark)

    assert telegram.titles == [
        f"тревога: Новый админ — {NEW}. В 03:10 МСК.",
        f"тревога: Изменён доступ учётки {OP}. В 03:10 МСК.",
        f"тревога: Изменён доступ учётки {OP}. В 03:10 МСК.",
    ]


@pytest.mark.usefixtures("bot")
async def test_telegram_down_longer_than_the_window_loses_nothing(
    session: AsyncSession, boss: UserModel, telegram: Telegram
) -> None:
    """Telegram молчит дольше окна сверки: курсор стоит перед несказанным, и оно приходит, когда
    Telegram снова принимает, — хотя окно его давно не читает."""
    op = await account(session, OP)
    await changed(session, boss, op, permissions={"send": True})
    telegram.accepts = False
    mark = Mark()

    await one_pass(session, mark)
    telegram.accepts = True
    await one_pass(session, mark, now=WATCH_MINUTE + timedelta(minutes=30))
    await one_pass(session, mark, now=WATCH_MINUTE + timedelta(minutes=31))

    assert telegram.titles == [f"тревога: Изменён доступ учётки {OP}. В 03:10 МСК."] * 2


async def test_without_a_bot_the_journal_line_is_what_was_said(
    session: AsyncSession, boss: UserModel, telegram: Telegram
) -> None:
    """Бот не настроен — `send_alert` пишет громкую строку журнала и отвечает «нет»: это и есть
    сказанное, как у лент тревог, — иначе строка повторялась бы каждую минуту."""
    telegram.accepts = False
    op = await account(session, OP)
    await changed(session, boss, op, permissions={"send": True})
    mark = Mark()
    for _ in range(3):
        await one_pass(session, mark)

    assert len(telegram.said) == 1


async def test_a_record_committed_after_a_newer_one_is_not_lost(
    session: AsyncSession, boss: UserModel
) -> None:
    """Номер даёт вставка, а видна запись после фиксации: курсор уже прошёл правку — окно сверки её
    читает; старое под курсором — нет."""
    op = await account(session, OP)
    await changed(session, boss, op, permissions={"send": True})
    await AccessRepository(session).record(
        AuditAction.RUN_STARTED, author_id=boss.id, target="run:1"
    )
    await stamp(session, AT)

    batch = await read(session, cursor=await top(session))

    assert [(event.whose, event.created) for event in batch.events] == [(OP, False)]


async def test_a_cursor_past_the_journal_reads_from_its_end(
    session: AsyncSession, boss: UserModel
) -> None:
    """База восстановлена из копии: курсор дальше последней записи — читать с её конца, а курсор
    вернуть туда, иначе новые записи под старыми номерами ждали бы, пока номера дорастут."""
    op = await account(session, OP)
    await changed(session, boss, op, permissions={"send": True})

    batch = await read(session, cursor=await top(session) + 1000)

    assert [event.whose for event in batch.events] == [OP]
    assert batch.reach == await top(session)


async def test_events_past_the_batch_come_next_pass(
    session: AsyncSession, boss: UserModel, telegram: Telegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(access_events, "BATCH", 2)
    people = [await account(session, f"op{number}@team.example.test") for number in range(3)]
    for person in people:
        await changed(session, boss, person, permissions={"send": True})
    mark = Mark()

    for _ in range(3):
        await one_pass(session, mark)

    assert telegram.titles == [
        "тревога: Изменён доступ учёток — 2. С 03:10 МСК.",
        "тревога: Изменён доступ учётки op2@team.example.test. В 03:10 МСК.",
    ]
    assert mark.cursor == await top(session)


async def test_without_redis_the_process_remembers_and_later_misses_nothing(
    session: AsyncSession, boss: UserModel, telegram: Telegram, redis: FakeRedis
) -> None:
    """Redis молчал при старте: о свежем сторож сказал по памяти процесса. Ответил — прежний курсор
    ниже, и правка, которую прежний процесс не успел сказать (она старше окна сверки), приходит;
    сказанное по памяти — нет."""
    old, op = await account(session, OLD), await account(session, OP)
    redis.hashes[access_mark.key()] = {"cursor": str(await top(session)), "told": "{}"}
    await changed(session, boss, old, at=LONG_AGO, permissions={"send": True})
    await changed(session, boss, op, permissions={"send": True})
    redis.down = True
    mark = Mark()

    await one_pass(session, mark)
    redis.down = False
    await one_pass(session, mark)
    await one_pass(session, mark)

    assert telegram.titles == [
        f"тревога: Изменён доступ учётки {OP}. В 03:10 МСК.",
        f"тревога: Изменён доступ учётки {OLD}. В 14.11 03:11 МСК.",
    ]
    assert redis.hashes[access_mark.key()]["cursor"] == str(await top(session))


async def test_a_broken_memory_in_redis_does_not_stop_the_pass(
    session: AsyncSession, boss: UserModel, telegram: Telegram, redis: FakeRedis
) -> None:
    redis.hashes[access_mark.key()] = {"cursor": "не номер", "told": "{битое"}
    op = await account(session, OP)
    await changed(session, boss, op, permissions={"send": True})
    mark = Mark()

    await one_pass(session, mark)
    await one_pass(session, mark)

    assert len(telegram.said) == 1
    assert redis.hashes[access_mark.key()]["cursor"] == str(await top(session))


async def test_what_the_cursor_passed_and_the_window_left_is_forgotten(
    session: AsyncSession, boss: UserModel, telegram: Telegram, redis: FakeRedis
) -> None:
    op = await account(session, OP)
    await changed(session, boss, op, permissions={"send": True})
    mark = Mark()
    await one_pass(session, mark)
    remembered = dict(mark.told)

    await mark.tell(Batch([], await top(session)), WATCH_MINUTE + timedelta(minutes=30))

    assert len(remembered) == 1
    assert mark.told == {}
    assert redis.hashes[access_mark.key()]["told"] == "{}"
