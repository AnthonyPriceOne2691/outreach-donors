"""Сторож входов и прав — события входа с кодом (`ops/access_events.py`, `ops/code_event_words.py`),
23.11.2026.

Здесь держится:

- **выключение, сброс и вход кодом восстановления — тревога с поводом**: чья учётка, что
  случилось, кто, минута по Москве, где смотреть и что сделать, если это были не вы; сброс без
  автора — «кто — команда на сервере» со своей пометкой; события пишет тот же код, что экран;
- **кодов восстановления сколько осталось**: три и меньше — «мало», ноль — «кодов больше нет», и
  прямо, что без телефона дальше не войти и что сделать;
- **включение — строкой общего сообщения без повода**; рядом с правкой доступа — в её сообщении,
  а не «правкой доступа»;
- **проигрыш доступа не сломан**: запись о коде между правками учётки не меняет «было» следующей;
- **о каждом событии — один раз**: окно сверки, перезапуск (Redis), отказ Telegram — ещё раз и
  только то, что не принял;
- **паролей, кодов и набранного в сообщениях нет**; пометки — коротко и без технического мусора.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from backend.config import access as access_cfg
from backend.config import alerts as alerts_cfg
from backend.features.access import code_login, recovery_codes, second_step, totp
from backend.features.access.administration import reset_totp, update_access
from backend.features.access.challenge import Challenge, Step, account_stamp
from backend.features.access.repository import AccessRepository, actor_of
from backend.features.core.domain import AuditAction, UserRole
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.ops import access_events, access_mark, alarm_feed
from backend.features.ops import code_event_words as words
from backend.features.ops.access_event_words import CHANGED, notices
from backend.features.ops.access_events import Batch, CodeEvent
from backend.features.ops.access_mark import Mark
from backend.features.ops.alarms import Concern, marked
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import WATCH_MINUTE
from tests.test_access_totp import fresh_key
from tests.test_access_watch_events import (
    AT,
    BOSS,
    LONG_AGO,
    NEW,
    OLD,
    OP,
    PASSWORD,
    account,
    stamp,
)
from tests.test_alarm_concern import _plain
from tests.test_mail_watch_seams import FakeRedis

WHERE = "Где смотреть: экран «Журнал действий»."


@pytest.fixture(autouse=True)
def code_settings(monkeypatch: pytest.MonkeyPatch, jwt_secret: None) -> None:
    """Ключ шифрования секретов есть, обязательных ролей нет — как после выкатки с ключом; секрет
    подписи — для вызова второго шага и пропуска входа кодом восстановления."""
    monkeypatch.setattr(access_cfg, "TOTP_KEY", fresh_key())
    monkeypatch.setattr(access_cfg, "TOTP_REQUIRED_ROLES", frozenset())


@pytest.fixture
async def boss(session: AsyncSession) -> UserModel:
    """Админ, который сбрасывает вход с кодом и меняет доступ."""
    return await account(session, BOSS, UserRole.ADMIN)


@pytest.fixture
async def op(session: AsyncSession) -> UserModel:
    return await account(session, OP)


async def with_code(
    session: AsyncSession, user: UserModel, at: datetime = LONG_AGO
) -> tuple[str, list[str]]:
    """Включить учётке вход с кодом теми же шагами, что экран, — минутой `at`: давно — о включении
    сторож уже не говорит. Секрет и коды восстановления — для следующих шагов."""
    secret = await second_step.begin(session, user)
    await session.refresh(user)
    codes = await second_step.enable(session, user, totp.code_at(secret, at.timestamp()), at)
    await stamp(session, at)
    return secret, codes


async def recovered(session: AsyncSession, user: UserModel, code: str, at: datetime = AT) -> None:
    """Вход кодом восстановления — вторым шагом входа, как с экрана."""
    ticket = Challenge(user_id=user.id, step=Step.CODE, stamp=account_stamp(user))
    await code_login.finish_with_code(session, ticket, code, now=at)
    await stamp(session, at)


async def spent(session: AsyncSession, user: UserModel, codes: list[str]) -> None:
    """Коды восстановления, потраченные раньше: остаток меньше, записей о них в окне нет."""
    for code in codes:
        assert await recovery_codes.spend(session, user.id, code, LONG_AGO) is not None


async def read(session: AsyncSession) -> Batch:
    return await access_events.read(session, cursor=None, told=set(), now=WATCH_MINUTE)


def said(batch: Batch) -> list[str]:
    """Сообщения прохода — как они уйдут: с пометкой последней строкой."""
    return [marked(notice.text, notice.concern) for notice in notices(batch.events, WATCH_MINUTE)]


# --- выключение и сброс -----------------------------------------------------------------------


async def test_turning_the_code_off_is_an_alarm_with_what_to_do(
    session: AsyncSession, op: UserModel
) -> None:
    secret, _ = await with_code(session, op)
    await code_login.disable_own(session, op, totp.code_at(secret, AT.timestamp()), now=AT)
    await stamp(session, AT)

    assert said(await read(session)) == [
        f"тревога: Выключен вход с кодом — {OP}. В 03:10 МСК.\n"
        f"Вход с кодом выключен — учётку снова открывает один пароль; кто — {OP}.\n"
        f"{WHERE}\n{words.OFF.words}"
    ]


async def test_a_reset_by_an_admin_names_the_admin(
    session: AsyncSession, boss: UserModel, op: UserModel
) -> None:
    await with_code(session, op)
    await reset_totp(session, user_id=op.id, author=actor_of(boss))
    await stamp(session, AT)

    assert said(await read(session)) == [
        f"тревога: Сброшен вход с кодом — {OP}. В 03:10 МСК.\n"
        "Вход с кодом сброшен — ключ и коды восстановления удалены, сеансы учётки закрыты; "
        f"кто — {BOSS}.\n"
        f"{WHERE}\n{words.RESET.words}"
    ]


async def test_a_reset_on_the_server_names_the_command(
    session: AsyncSession, op: UserModel
) -> None:
    """Запись без автора — сброс командой на сервере (`outreach user-totp-reset`), как заведение
    учётки без автора; пометка своя: у сбросившего есть доступ к серверу."""
    await with_code(session, op)
    await reset_totp(session, user_id=op.id, author=None)
    await stamp(session, AT)

    [text] = said(await read(session))

    assert text.startswith(f"тревога: Сброшен вход с кодом — {OP}. В 03:10 МСК.\n")
    assert "сеансы учётки закрыты; кто — команда на сервере.\n" in text
    assert text.endswith(f"{WHERE}\n{words.RESET_ON_SERVER.words}")


async def test_resets_of_one_pass_are_one_message_per_mark(
    session: AsyncSession, boss: UserModel, op: UserModel
) -> None:
    """Два сброса админом — одно сообщение списком; сброс командой — своё: пометка другая."""
    new, old = await account(session, NEW), await account(session, OLD)
    for user in (op, new, old):
        await with_code(session, user)
    for user in (op, new):
        await reset_totp(session, user_id=user.id, author=actor_of(boss))
    await reset_totp(session, user_id=old.id, author=None)
    await stamp(session, AT)

    admin, server = said(await read(session))

    assert admin == (
        "тревога: Сброшен вход с кодом — 2. С 03:10 МСК.\n"
        f"• 03:10 {OP} — вход с кодом сброшен — ключ и коды восстановления удалены, сеансы учётки "
        f"закрыты; кто — {BOSS}\n"
        f"• 03:10 {NEW} — вход с кодом сброшен — ключ и коды восстановления удалены, сеансы "
        f"учётки закрыты; кто — {BOSS}\n"
        f"{WHERE}\n{words.RESET.words}"
    )
    assert server.startswith(f"тревога: Сброшен вход с кодом — {OLD}. В 03:10 МСК.")
    assert server.endswith(words.RESET_ON_SERVER.words)


# --- вход кодом восстановления ------------------------------------------------------------------


async def test_a_recovery_login_says_how_many_codes_are_left(
    session: AsyncSession, op: UserModel
) -> None:
    secret, codes = await with_code(session, op)
    await recovered(session, op, codes[0])

    [text] = said(await read(session))

    assert text == (
        f"тревога: Вход кодом восстановления — {OP}. В 03:10 МСК.\n"
        f"Вход кодом восстановления вместо кода из приложения, осталось 9 кодов; кто — {OP}.\n"
        f"{WHERE}\n{words.RECOVERY.words}"
    )
    for secret_word in (codes[0], recovery_codes.normalize(codes[0]), secret, PASSWORD):
        assert secret_word not in text


async def test_few_codes_left_says_plainly_that_without_the_phone_it_ends(
    session: AsyncSession, op: UserModel
) -> None:
    _, codes = await with_code(session, op)
    await spent(session, op, codes[:7])
    await recovered(session, op, codes[7])

    assert said(await read(session)) == [
        f"тревога: Вход кодом восстановления — {OP}. В 03:10 МСК.\n"
        "Вход кодом восстановления вместо кода из приложения, осталось 2 кода — это мало: без "
        f"телефона ещё 2 входа, а дальше не войти; кто — {OP}.\n"
        f"{words.RENEW}\n{WHERE}\n{words.RECOVERY.words}"
    ]


async def test_the_last_code_says_there_are_none(session: AsyncSession, op: UserModel) -> None:
    _, codes = await with_code(session, op)
    await spent(session, op, codes[:9])
    await recovered(session, op, codes[9])

    [text] = said(await read(session))

    assert (
        "Вход кодом восстановления вместо кода из приложения — кодов больше нет: без телефона "
        f"дальше не войти; кто — {OP}.\n{words.RENEW}\n" in text
    )


@pytest.mark.parametrize(
    ("left", "few"),
    [(None, False), (9, False), (4, False), (3, True), (1, True), (0, True)],
    ids=["счёта нет", "девять", "четыре", "три — мало", "один", "ноль"],
)
def test_few_is_three_and_below(left: int | None, few: bool) -> None:
    """Порог «мало» — три: без телефона это день работы с перерывами (`code_event_words.FEW`)."""
    event = CodeEvent(
        id=1, at=AT, action=AuditAction.RECOVERY_CODE_USED, whose=OP, author=OP, left=left
    )

    assert words.advice([event]) == ([words.RENEW] if few else [])
    assert ("дальше не войти" in words.what(event)) is few
    assert ("осталось" in words.what(event)) is (left not in (None, 0))


async def test_a_record_without_the_count_is_said_without_it(session: AsyncSession) -> None:
    """Счёт в записи не числом (запись чужого вида) — без счёта, а не «осталось True»."""
    session.add(
        AuditLogModel(
            action=AuditAction.RECOVERY_CODE_USED,
            target="user:900001",
            details={"email": "gone@team.example.test", recovery_codes.LEFT: True},
            created_at=AT,
        )
    )
    await session.flush()

    [text] = said(await read(session))

    assert "Вход кодом восстановления вместо кода из приложения; кто — команда на сервере." in text
    assert "осталось" not in text


# --- включение ------------------------------------------------------------------------------------


async def test_turning_the_code_on_is_said_without_a_cause(
    session: AsyncSession, op: UserModel
) -> None:
    secret, codes = await with_code(session, op, at=AT)

    [text] = said(await read(session))

    assert text == (
        f"тревога: Включён вход с кодом — {OP}. В 03:10 МСК.\n"
        f"Вход с кодом включён; кто — {OP}.\n"
        f"{WHERE}\n{words.ENABLED.words}"
    )
    assert secret not in text
    assert not any(code in text for code in codes)


async def test_turning_on_rides_the_access_message_and_is_not_called_an_access_change(
    session: AsyncSession, boss: UserModel, op: UserModel
) -> None:
    """Правка доступа и включение кода одного прохода — одно общее сообщение с поводом правки;
    включение в нём — своей строкой и своим числом в заголовке."""
    new = await account(session, NEW)
    await update_access(session, user_id=op.id, author=actor_of(boss), permissions={"send": True})
    await stamp(session, AT - timedelta(minutes=1))
    await with_code(session, new, at=AT)

    assert said(await read(session)) == [
        "тревога: Изменён доступ учёток — 1; включён вход с кодом — 1. С 03:09 МСК.\n"
        f"• 03:09 {OP} — добавлено: «отправлять письма»; кто — {BOSS}\n"
        f"• 03:10 {NEW} — вход с кодом включён; кто — {NEW}\n"
        f"{WHERE}\n{CHANGED.words}"
    ]


async def test_several_turned_on_are_one_message_without_a_cause(
    session: AsyncSession, op: UserModel
) -> None:
    new = await account(session, NEW)
    await with_code(session, op, at=AT - timedelta(minutes=1))
    await with_code(session, new, at=AT)

    assert said(await read(session)) == [
        "тревога: Включён вход с кодом — 2. С 03:09 МСК.\n"
        f"• 03:09 {OP} — вход с кодом включён; кто — {OP}\n"
        f"• 03:10 {NEW} — вход с кодом включён; кто — {NEW}\n"
        f"{WHERE}\n{words.ENABLED.words}"
    ]


# --- проход целиком и проигрыш доступа ----------------------------------------------------------


async def test_a_pass_with_everything_says_alarms_first_and_the_common_message_last(
    session: AsyncSession, boss: UserModel, op: UserModel
) -> None:
    new = await account(session, NEW)
    secret, codes = await with_code(session, op)
    await recovered(session, op, codes[0], at=AT - timedelta(minutes=1))
    await code_login.disable_own(session, op, totp.code_at(secret, AT.timestamp()), now=AT)
    await update_access(session, user_id=new.id, author=actor_of(boss), role=UserRole.ADMIN)
    await stamp(session, AT)

    assert [text.splitlines()[0] for text in said(await read(session))] == [
        f"тревога: Учётка стала админом — {NEW}. В 03:10 МСК.",
        f"тревога: Вход кодом восстановления — {OP}. В 03:09 МСК.",
        f"тревога: Выключен вход с кодом — {OP}. В 03:10 МСК.",
    ]


async def test_a_code_record_between_access_changes_does_not_change_what_was_before(
    session: AsyncSession, boss: UserModel, op: UserModel
) -> None:
    """Запись о коде в проигрыш доступа не идёт: правка после неё видит прежний доступ, а не
    «доступ изменён» записи без «было»."""
    await update_access(session, user_id=op.id, author=actor_of(boss), permissions={"send": True})
    await stamp(session, AT - timedelta(minutes=2))
    await with_code(session, op, at=AT - timedelta(minutes=1))
    await update_access(session, user_id=op.id, author=actor_of(boss), is_active=False)
    await stamp(session, AT)

    assert said(await read(session)) == [
        "тревога: Изменён доступ учёток — 2; включён вход с кодом — 1. С 03:08 МСК.\n"
        f"• 03:08 {OP} — добавлено: «отправлять письма»; кто — {BOSS}\n"
        f"• 03:09 {OP} — вход с кодом включён; кто — {OP}\n"
        f"• 03:10 {OP} — учётка отключена; кто — {BOSS}\n"
        f"{WHERE}\n{CHANGED.words}"
    ]


# --- о каждом событии — один раз ------------------------------------------------------------------


class Telegram:
    """Подставной Telegram сторожа: что ему сказали; ответы — по очереди из `answers`, дальше —
    «принял»."""

    def __init__(self) -> None:
        self.said: list[str] = []
        self.answers: list[bool] = []

    async def send(self, text: str) -> bool:
        self.said.append(text)
        return self.answers.pop(0) if self.answers else True

    @property
    def titles(self) -> list[str]:
        return [text.splitlines()[0] for text in self.said]


@pytest.fixture
def telegram(monkeypatch: pytest.MonkeyPatch) -> Telegram:
    """Бот тревог задан (отказ Telegram — не сказанное), а сам Telegram — подставной."""
    found = Telegram()
    monkeypatch.setattr(alerts_cfg, "TELEGRAM_BOT_TOKEN", "made-up-token")
    monkeypatch.setattr(alerts_cfg, "TELEGRAM_CHAT_ID", "made-up-chat")
    monkeypatch.setattr(access_mark, "send_alert", found.send)
    return found


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


async def test_a_code_event_is_said_once_in_the_window_and_across_a_restart(
    session: AsyncSession, op: UserModel, telegram: Telegram, redis: FakeRedis
) -> None:
    """Окно сверки читает запись снова каждым проходом, перезапуск начинает с памяти в Redis —
    а сообщение одно."""
    await with_code(session, op)
    await reset_totp(session, user_id=op.id, author=None)
    await stamp(session, AT)
    mark = Mark()

    for minutes in (0, 1, 2):
        await one_pass(session, mark, now=WATCH_MINUTE + timedelta(minutes=minutes))
    await one_pass(session, Mark(), now=WATCH_MINUTE + timedelta(minutes=3))  # перезапуск

    assert telegram.titles == [f"тревога: Сброшен вход с кодом — {OP}. В 03:10 МСК."]
    assert redis.hashes[access_mark.key()]["cursor"] == str(mark.cursor)


async def test_a_refused_code_alarm_is_said_again_and_only_it(
    session: AsyncSession, op: UserModel, telegram: Telegram
) -> None:
    """Вход кодом восстановления Telegram не принял, а включение кода у другой учётки — принял:
    следующий проход повторяет только вход кодом."""
    new = await account(session, NEW)
    _, codes = await with_code(session, op)
    await recovered(session, op, codes[0])
    await with_code(session, new, at=AT)
    telegram.answers = [False, True]
    mark = Mark()

    for _ in range(3):
        await one_pass(session, mark)

    assert telegram.titles == [
        f"тревога: Вход кодом восстановления — {OP}. В 03:10 МСК.",
        f"тревога: Включён вход с кодом — {NEW}. В 03:10 МСК.",
        f"тревога: Вход кодом восстановления — {OP}. В 03:10 МСК.",
    ]


async def test_journal_records_other_than_the_code_stay_quiet(
    session: AsyncSession, op: UserModel
) -> None:
    """Вход по коду из приложения и неудачный код — не события сторожа: о них молчит, как раньше."""
    secret, _ = await with_code(session, op)
    ticket = Challenge(user_id=op.id, step=Step.CODE, stamp=account_stamp(op))
    with pytest.raises(second_step.CodeRefusedError):
        await code_login.finish_with_code(session, ticket, "не код", now=AT)
    await code_login.finish_with_code(session, ticket, totp.code_at(secret, AT.timestamp()), now=AT)
    await stamp(session, AT)

    batch = await read(session)

    assert batch.events == []
    actions = await AccessRepository(session).journal(limit=10)
    assert {row.action for row in actions} >= {AuditAction.LOGIN, AuditAction.LOGIN_FAILED}


# --- пометки -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("concern", "caused"),
    [
        (words.OFF, True),
        (words.RESET, True),
        (words.RESET_ON_SERVER, True),
        (words.RECOVERY, True),
        (words.ENABLED, False),
    ],
    ids=["выключен", "сброшен админом", "сброшен на сервере", "код восстановления", "включён"],
)
def test_the_marks_are_plain_and_only_turning_on_has_no_cause(
    concern: Concern, caused: bool
) -> None:
    _plain(concern)
    assert concern.cause is caused


def test_every_code_action_has_its_words() -> None:
    """Новое действие журнала о коде без слов здесь краснит, а не роняет проход сторожа."""
    assert set(words.TITLES) == set(access_events.CODE_ACTIONS)
    assert set(words.CONCERNS) == set(access_events.CODE_ACTIONS)
    assert set(words.DONE) | {AuditAction.RECOVERY_CODE_USED} == set(access_events.CODE_ACTIONS)
