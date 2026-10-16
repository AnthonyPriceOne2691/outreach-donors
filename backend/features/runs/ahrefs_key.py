"""Ключ Ahrefs: снимок его лимитов и цикл, в котором считается кап (решение владельца 16.10.2026).

**Сервис владеет текущей ситуацией по ключу, а не зашитыми числами.** До 16.10.2026 кап и его
граница жили в окружении, а месяц капа был календарным; у ключа Ahrefs свой лимит и своё
обнуление, и лимит ключа команда двигает в Ahrefs посреди месяца. Замер на проде 16.10: кап
показывал почти выбранным, а у самого ключа оставалась большая часть лимита — смета говорила
«недостаточно лимитов».

**Снимок — каждый удачный ответ** бесплатного `subscription-info/limits-and-usage`: лимит
и расход ключа и пространства, следующее обнуление, когда спрошено; строка одна
(`remember`). Свежим его держит сторож — опрос раз в десять минут (`ops/key_watch`), а смета,
«Расход», старт прогона, DR Этапа 2, консоль и докупка стран кладут туда же свои ответы
(`keeper`). Запись не ждёт чужих транзакций: замок снимка берётся без ожидания, и кто его
не взял — пропускает свой ответ: транзакция, взявшая замок, кладёт свой сама.

**Цикл — от обнуления ключа** (`cycle_of`): начало — последнее обнуление не позже «сейчас».
«Сейчас» раньше даты из снимка — дата минус месяц (тот же день, а в коротком месяце — его
последний: 31-е → 30-е, 28-е или 29-е); снимок устарел и дата прошла — вперёд по месяцу,
пока следующее обнуление не окажется позже «сейчас». Шаг — от дня самой даты, а не от
прошлого шага: обнуление 31-го в феврале — 28-го, а в марте — снова 31-го. Снимка или даты
нет — календарный месяц, и экраны говорят это словами (`Cycle.by_key`).

Только кап Ahrefs: месяц Hunter у продаж, статьи «Расхода» и выдача на «Обзоре» считают
от первого числа, как и прежде (`runs/spending.since_month_start`).
"""

from __future__ import annotations

import calendar
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.ahrefs.units import Quota
from backend.features.core.models.ops import AhrefsKeySnapshotModel

logger = logging.getLogger(__name__)

#: Номер единственной строки снимка.
SNAPSHOT_ID = 1

#: Ключ замка записи снимка — «AHRKEY» байтами. Берётся без ожидания: снимок пишут и долгие
#: транзакции (пересчёт кандидатов Этапа 2 держит свою, пока Ahrefs отвечает о DR), и сторож,
#: ждущий их строку, молчал бы вместе с ними.
KEY_LOCK = 0x4148524B4559

#: Снимка нет: Ahrefs не отвечал ни разу — кап считать не от чего.
NOT_ASKED = "Ahrefs ещё не ответил — размер ключа неизвестен"

#: Ahrefs ответил, но не назвал ни лимита ключа, ни лимита пространства.
NOT_NAMED = "Ahrefs не назвал ни лимита ключа, ни лимита пространства — размер ключа неизвестен"

#: Куда ответ Ahrefs ложится снимком — в транзакцию того, у кого есть сессия (`keeper`).
Keep = Callable[[Quota], Awaitable[object]]

#: Месяцы в родительном падеже — день цикла словами: «21 ноября».
_MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def day_words(moment: datetime) -> str:
    """День цикла словами — «21 ноября»: по UTC, как дату обнуления называет Ahrefs."""
    day = moment.astimezone(UTC)
    return f"{day.day} {_MONTHS[day.month - 1]}"


@dataclass(frozen=True, slots=True)
class KeySnapshot:
    """Что Ahrefs ответил о ключе в последний раз и когда."""

    quota: Quota
    asked_at: datetime

    @property
    def unknown(self) -> str | None:
        """Почему размер ключа неизвестен — словами; `None` — известен."""
        return NOT_NAMED if self.quota.size is None else None


@dataclass(frozen=True, slots=True)
class Cycle:
    """Цикл капа: с какого момента считается наш расход и когда обнуление."""

    start: datetime
    next_reset: datetime
    #: От обнуления ключа; `False` — календарный месяц: снимка или даты обнуления нет.
    by_key: bool


def _shifted(anchor: datetime, months: int) -> datetime:
    """`anchor` через `months` месяцев (назад — отрицательным): тем же днём, а в коротком
    месяце — его последним."""
    year, month = divmod(anchor.month - 1 + months, 12)
    year, month = anchor.year + year, month + 1
    return anchor.replace(year=year, month=month, day=min(anchor.day, _days_in(year, month)))


def _days_in(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def cycle_of(reset_at: datetime | None, now: datetime) -> Cycle:
    """Цикл, в котором лежит `now`: от последнего обнуления не позже `now` до следующего.

    `reset_at` — следующее обнуление по снимку; снимок мог устареть, и тогда дата уже
    прошла. Даты нет — календарный месяц: так считал кап до 16.10.2026."""
    if reset_at is None:
        start = now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return Cycle(start=start, next_reset=_shifted(start, 1), by_key=False)
    # С первой прикидки — на месяц в нужную сторону, если день ещё не наступил или уже прошёл.
    step = (now.year - reset_at.year) * 12 + now.month - reset_at.month
    while _shifted(reset_at, step) > now:
        step -= 1
    while _shifted(reset_at, step + 1) <= now:
        step += 1
    return Cycle(_shifted(reset_at, step), _shifted(reset_at, step + 1), by_key=True)


async def snapshot(session: AsyncSession) -> KeySnapshot | None:
    """Последний снимок ключа; `None` — Ahrefs ещё не ответил ни разу.

    Столбцами, а не объектом: снимок переписывают запросом мимо карты сессии, и объект,
    прочитанный раньше в той же сессии, вернулся бы прежним."""
    row = (
        await session.execute(
            select(
                AhrefsKeySnapshotModel.workspace_limit,
                AhrefsKeySnapshotModel.workspace_used,
                AhrefsKeySnapshotModel.key_limit,
                AhrefsKeySnapshotModel.key_used,
                AhrefsKeySnapshotModel.reset_at,
                AhrefsKeySnapshotModel.asked_at,
            ).where(AhrefsKeySnapshotModel.id == SNAPSHOT_ID)
        )
    ).one_or_none()
    if row is None:
        return None
    workspace_limit, workspace_used, key_limit, key_used, reset_at, asked_at = row
    quota = Quota(
        workspace_limit=workspace_limit,
        workspace_used=workspace_used,
        key_limit=key_limit,
        key_used=key_used,
        reset_at=reset_at,
    )
    return KeySnapshot(quota=quota, asked_at=asked_at)


async def remember(session: AsyncSession, quota: Quota, *, at: datetime | None = None) -> bool:
    """Ответ Ahrefs — снимком ключа. Без фиксации: снимок ложится транзакцией вызывающего.

    `False` — не записан: замок снимка держит другая транзакция, и её ответ ляжет сам
    (замок — без ожидания, `KEY_LOCK`). Ответ старше записанного строку не трогает: два
    опроса разом пишут каждый в своём порядке, и прошлое не возвращается."""
    if not await session.scalar(select(func.pg_try_advisory_xact_lock(KEY_LOCK))):
        logger.info("снимок ключа Ahrefs: его пишет другая транзакция — этот ответ пропущен")
        return False
    values = {
        "key_limit": quota.key_limit,
        "key_used": quota.key_used,
        "workspace_limit": quota.workspace_limit,
        "workspace_used": quota.workspace_used,
        "reset_at": quota.reset_at,
        "asked_at": at or datetime.now(UTC),
    }
    statement = insert(AhrefsKeySnapshotModel).values(id=SNAPSHOT_ID, **values)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[AhrefsKeySnapshotModel.id],
            set_={name: statement.excluded[name] for name in values},
            where=AhrefsKeySnapshotModel.asked_at <= statement.excluded.asked_at,
        )
    )
    return True


def keeper(session: AsyncSession) -> Keep:
    """Ответ Ahrefs — снимком ключа в транзакцию этой сессии (`runs/budget.units_left`)."""
    return partial(remember, session)
