"""Сторож входов и прав: всплеск неудачных входов, новый админ и смена доступа — в Telegram.

Аудит 20.11.2026 (тревоги одобрены владельцем в тот же день): подбор пароля, новая учётка админа и
выданные права были видны только на экране «Журнал действий» — тому, кто туда зашёл и
прокрутил. Счётчик попыток у входа (`access/attempts.py`) перебор тормозит, но никому о нём не
говорит. Сторож читает журнал действий сам — ту же таблицу, что экран, — и говорит в общий канал
тревог (`shared/alerts.py`, бот `ALERT_TELEGRAM_*`).

**Три тревоги, два устройства.**

- **Всплеск неудачных входов** — состояние, а не событие: за окно (`config/access_alarms.py`, 10
  минут) неудачных входов не меньше порога всего (10) или в одну набранную почту (5). Говорит
  лента тревог (`alarm_feed.Feed`), как у сторожа тишины: одна «тревога» на всплеск, пока он идёт,
  — тишина, «прошло» — когда целое окно прошло без него. Почта — как её записал журнал
  (`login.attempted_email`): набранное не почтой там уже «‹не почта›», и здесь та же проверка ещё
  раз — записи до 19.11.2026 несли набранное как есть, а пароль, набранный не в то поле, в
  Telegram не уходит. «‹не почта›» — не учётка, порог одной почты её не считает.
- **Новый админ и смена доступа** — события: заведена учётка, сменились роль, точечные права или
  активность (`access_events.py`). О каждом — один раз; новые админы своим сообщением, остальные
  события прохода — одним общим (`access_event_words.py`). Что уже сказано — курсор по номерам
  записей журнала в Redis (`access_mark.py`).

**Вызов — как у сторожа тишины в процессе разбора** (`workers/reaper.py`): `watch()` без доводов,
раз в `INTERVAL_SEC`, через общий цикл (`workers/ticker.every`). Своя сессия на проход и только
на чтение; Telegram и Redis — после её закрытия: при открытой транзакции соединение висело бы
«idle in transaction» на время чужих ответов. Часы прохода — часы ленты тревог
(`alarm_feed.wall_clock`): ими же лента называет начало тревоги, и щит тестов их закрепляет.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import access_alarms as cfg
from backend.config import storage
from backend.features.access.login import NOT_AN_EMAIL, attempted_email
from backend.features.core.domain import AuditAction
from backend.features.core.models.access import AuditLogModel
from backend.features.ops import access_events, alarm_feed
from backend.features.ops.access_event_words import SCREEN
from backend.features.ops.access_mark import Mark
from backend.features.ops.alarm_feed import Feed
from backend.features.ops.alarm_words import clock, counted
from backend.features.ops.alarms import Alarm, cause

#: Как часто — раз в минуту: о новом админе — не позже пары минут, а проход — четыре коротких
#: запроса к журналу по индексам номера и времени.
INTERVAL_SEC = 60.0

#: Код тревоги всплеска: лента помнит сказанное под ним, числа в словах её не будят.
BURST = "login-burst"

#: Заголовок тревоги всплеска — по нему её ищут в ленте.
TITLE = "Всплеск неудачных входов"

#: Сколько почт тревога называет: «чаще всего — …».
NAMED = 3

#: Сколько почт журнала читать для этого — самые частые: дальше единицы, они ни порога, ни слов
#: не меняют.
GROUPS = 20

#: Формы слов: «12 неудачных входов», «за 10 минут».
FAILED = ("неудачный вход", "неудачных входа", "неудачных входов")
MINUTES = ("минуту", "минуты", "минут")

#: Пометка всплеска: так подбирают пароль — повод есть всегда.
GUESSING = cause(
    "похоже на подбор пароля",
    "проверить в журнале, чью почту подбирают; если это учётка сервиса — сменить ей пароль",
)

#: Что сторож уже сказал о всплеске — своя лента со своим именем (`alarm_feed.told_key`). Срок
#: тишины — окно: всплеск кончился, когда целое окно прошло без него.
FEED = Feed("access", quiet_sec=cfg.WINDOW_MIN * 60.0, screen=SCREEN)

#: Докуда прочитаны события доступа — память этого процесса с зеркалом в Redis.
MARK = Mark()


async def watch() -> None:
    """Один проход сторожа входов и прав: всплеск — ленте тревог, события доступа — по курсору."""
    MARK.recall()
    at = alarm_feed.wall_clock()
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            found = await burst(session, at)
            batch = await access_events.read(
                session, cursor=MARK.cursor, told=MARK.told.keys(), now=at
            )
        await FEED.tell([] if found is None else [found])
        await MARK.tell(batch, at)
    finally:
        await engine.dispose()


async def burst(session: AsyncSession, at: datetime) -> Alarm | None:
    """Тревога «всплеск неудачных входов», пока за окно до `at` их не меньше порога всего или в
    одну набранную почту; иначе `None`."""
    window = (
        AuditLogModel.action == AuditAction.LOGIN_FAILED,
        AuditLogModel.created_at >= at - timedelta(minutes=cfg.WINDOW_MIN),
    )
    total, first = (
        await session.execute(
            select(func.count(), func.min(AuditLogModel.created_at)).where(*window)
        )
    ).one()
    if not total or first is None:
        return None
    email = AuditLogModel.details["email"].astext
    found = await session.execute(
        select(email, func.count())
        .where(*window)
        .group_by(email)
        .order_by(func.count().desc(), email)
        .limit(GROUPS)
    )
    typed = _typed(found.tuples())
    if total < cfg.FAILED_LOGINS and not _one_email_over(typed):
        return None
    named = ", ".join(f"{mail} ({count})" for mail, count in typed[:NAMED])
    return Alarm(
        code=BURST,
        title=TITLE,
        detail=(
            f"{counted(total, FAILED)} за {counted(cfg.WINDOW_MIN, MINUTES)} — "
            f"с {clock(first, at)} МСК; чаще всего — {named}"
        ),
        concern=GUESSING,
        screen=SCREEN,
    )


def _typed(groups: Iterable[tuple[str | None, int]]) -> list[tuple[str, int]]:
    """Набранные почты со счётом, самые частые первыми. Почта — проверкой журнала
    (`attempted_email`): похожая на почту — как есть, иначе «‹не почта›»."""
    counts: dict[str, int] = {}
    for raw, count in groups:
        mail = attempted_email(raw or "")
        counts[mail] = counts.get(mail, 0) + count
    return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))


def _one_email_over(typed: Iterable[tuple[str, int]]) -> bool:
    """В одну набранную почту — не меньше порога. «‹не почта›» — не учётка: её не считает."""
    return any(count >= cfg.PER_EMAIL for mail, count in typed if mail != NOT_AN_EMAIL)
