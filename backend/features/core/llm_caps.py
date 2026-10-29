"""Дневные потолки расхода на модель — с экрана «Расход» (слово Anthony 29.10.2026: «чтобы
администратор мог поднимать расходы сам»; устройство — как ключи в рабочем пространстве Ahrefs:
«на ключе 100 тысяч из 2 миллионов гарантированно, и на каждый ключ можно накидывать потом из
остатка общего пространства»).

**Общий потолок сервиса и лимиты частей.** Общий — токены за сутки UTC на весь сервис
(`GENERAL_PRESETS`: 10–200 млн) или «без потолка». У каждой части — свой лимит, процентом общего:
«Продажи» (вызовы модели продаж, `sales/usage_cap.py`), «Агент переписки» (черновики доноров и
рекламодателей, `agent/guarding.drafts_cap`) и «Аутрич» — всё остальное: разбор ответов, судья
прогона, письма, ключи, тематика (`usage.PART_OPERATIONS`). Лимит части — жёсткий потолок её
расхода за сутки: неистраченное другими не перетекает (`available` — единственное место формулы).
Сумма лимитов — не больше 100 %; что не роздано — свободный остаток: его никто не тратит, как
нераспределённые юниты пространства Ahrefs, а администратор добавляет из него части или убавляет
часть в него. Шаг — `SHARE_STEP`, у части не меньше шага: ноль — не лимит, а запрет её вызовов.
Лимиты хранятся долями: общий двигают — лимиты и остаток растут вместе с ним. Общий «без
потолка» — и части без потолка, а доли запомнены и встают обратно, когда общий снова число.

**Пока блок не сохраняли — как в `.env`**, как до 29.10: общий `LLM_DAILY_TOKEN_CAP` (на проде 0 —
без потолка), свои `SALES_DAILY_TOKEN_CAP` и `AGENT_DAILY_TOKEN_CAP` (пусто — доля общего).
Первое сохранение включает лимиты частей; экран до него предлагает `SUGGESTED` — общий 50 млн,
продажи 20 % (те же 10 млн, что на проде), агент 20 %, аутрич 30 %, свободно 30 %. Смена —
строка истории (кто, когда, «было → стало»), решает последняя; прежние строки не правятся, как у
капа Ahrefs.

**Модули частей лимит не знают — его подставляет ядро по имени настройки** (`usage.OwnCap`):
продажи и агент строят свой потолок из `.env`, а действует лимит их части. Поэтому файлы продаж
править не нужно, и смета сбора продаж считает остаток от того же лимита, что сторож.

**Читают все процессы** — API, воркеры, сторож, — и не всегда с базой под рукой: свой потолок
части (`usage.OwnCap`) считается без запроса. Поэтому блок процесс держит в памяти (`_block`) и
перечитывает: перед каждой проверкой потолка (`usage.ensure_llm_within_cap`), при старте API
(`load`), при открытии «Расхода» и сразу после смены. API — один процесс (`Dockerfile`): смета,
посчитанная после смены, видит новое число; воркер — со следующего вызова модели.

**Право — «менять потолки расхода»** (`Permission.SPEND`), а не «править пороги»: это деньги, и
по слову Anthony их поднимает администратор, а не оператор. Кап Ahrefs остаётся под «порогами»
(решение владельца 16.10.2026).

**Сброс — полночь UTC, 03:00 по Москве**: тот же день, что у счёта. Отказ называет часть, что
делать и когда сброс — «через 5 ч 12 мин», а не «завтра»: в полночь по Москве до сброса ещё три
часа.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import llm as llm_cfg
from backend.features.core.models.access import UserModel
from backend.features.core.models.ops import LlmCapModel
from backend.shared.database.base import SessionFactory
from backend.shared.numbers import spaced

logger = logging.getLogger(__name__)

#: Настройки `.env`, которые блок заменяет, когда его сохранили.
GENERAL = "LLM_DAILY_TOKEN_CAP"
SALES = "SALES_DAILY_TOKEN_CAP"
AGENT = "AGENT_DAILY_TOKEN_CAP"

#: Части общего: продажи, агент переписки и всё остальное — аутрич.
PART_SALES, PART_AGENT, PART_OUTREACH = "sales", "agent", "outreach"
PARTS = (PART_SALES, PART_AGENT, PART_OUTREACH)
#: Часть словами экрана и отказа: «лимит раздела «Продажи» на сегодня выбран».
TITLES = {PART_SALES: "Продажи", PART_AGENT: "Агент переписки", PART_OUTREACH: "Аутрич"}
#: Часть свою настройку `.env` подменяет блоком: продажи и агент; у аутрича своей нет.
PART_OF_SETTING = {SALES: PART_SALES, AGENT: PART_AGENT}

#: Общий — только эти числа токенов в сутки (слово Anthony 29.10.2026) или «без потолка».
GENERAL_PRESETS = (10_000_000, 20_000_000, 30_000_000, 50_000_000, 100_000_000, 200_000_000)
#: Лимиты частей — целыми процентами общего с этим шагом; у части — не меньше шага.
SHARE_STEP = 5

#: Ключ замка смены — «LLMCAP» байтами. Смены редки и коротки: замок один.
CAP_LOCK = 0x4C4C4D434150
#: Сколько последних смен отдаёт экран; вся история — в журнале действий.
RECENT = 5

MOSCOW = ZoneInfo("Europe/Moscow")


class LlmCapError(ValueError):
    """Блок не годится: общий не из списка, доля не шагом или аутричу не остаётся. Текст
    говорит, что не так и что допустимо."""


@dataclass(frozen=True, slots=True)
class Block:
    """Сохранённый блок: общий (`None` — без потолка) и лимиты частей, проценты общего."""

    general: int | None
    sales: int
    agent: int
    outreach: int

    def share(self, part: str) -> int:
        return {PART_SALES: self.sales, PART_AGENT: self.agent, PART_OUTREACH: self.outreach}[part]

    def limit(self, part: str) -> int:
        """Лимит части, токены за сутки; общий без потолка — 0: и часть без потолка."""
        return 0 if self.general is None else self.general * self.share(part) // 100

    @property
    def free_share(self) -> int:
        """Свободный остаток, проценты общего: не роздан частям, и его никто не тратит."""
        return 100 - self.sales - self.agent - self.outreach

    @property
    def free(self) -> int:
        """Свободный остаток, токены за сутки; общий без потолка — 0."""
        return 0 if self.general is None else self.general * self.free_share // 100


#: Потрачено частями за сутки UTC — токены по журналу (`usage.spent_by_part`).
Spent = dict[str, int]


def available(saved: Block, part: str, spent: Spent) -> int:
    """Сколько части можно потратить сейчас, токены; общий без потолка — не зовётся.

    **Единственное место формулы частей**: лимит части минус потраченное ею за сутки — жёсткий
    потолок, неистраченное другими и свободный остаток сами не перетекают (решение Anthony
    29.10.2026). Сторож и экран зовут только эту функцию."""
    return max(0, saved.limit(part) - spent.get(part, 0))


#: Что экран предлагает при первом открытии: общий 50 млн, продажи — те же 10 млн, что на
#: проде, агент 20 %, аутрич 30 %, свободно 30 %.
SUGGESTED = Block(50_000_000, 20, 20, 30)

#: Сохранённый блок — память процесса (`refresh`); `[None]` — не сохраняли: как в `.env`.
_block: list[Block | None] = [None]


def block() -> Block | None:
    """Блок в силе; `None` — не сохраняли, потолки из `.env`."""
    return _block[0]


def daily_cap() -> int:
    """Действующий общий потолок, токены за сутки; 0 — потолка нет."""
    saved = _block[0]
    if saved is None:
        return llm_cfg.DAILY_TOKEN_CAP
    return saved.general or 0


def share_tokens(share: float) -> int:
    """Доля действующего общего потолка — у своего потолка, пустого в `.env`; общего нет —
    нет и доли."""
    general = daily_cap()
    return max(1, int(general * share)) if general else 0


def own_tokens(setting: str, declared: int) -> int:
    """Действующий свой потолок части, токены за сутки (0 — без потолка): блок сохранён — лимит
    её части по имени настройки; нет — `declared`, то, что из `.env` посчитал вызывающий."""
    saved = _block[0]
    part = PART_OF_SETTING.get(setting)
    if saved is None or part is None:
        return declared
    return saved.limit(part)


async def refresh(session: AsyncSession) -> None:
    """Перечитать блок в силе — последнюю строку — в память процесса."""
    _block[0] = await _in_base(session)


async def load() -> None:
    """Блок — в память при старте процесса, своей сессией. База не ответила — потолки из
    `.env` до первой проверки потолка, которая перечитает их сама."""
    try:
        async with SessionFactory() as session:
            await refresh(session)
    except (OSError, SQLAlchemyError) as exc:
        logger.warning("потолки модели с экрана не прочитаны при старте: %s", exc)


def forget() -> None:
    """Забыть блок — для тестов: память процесса живёт дольше теста."""
    _block[0] = None


# ---------------- смена ----------------


@dataclass(frozen=True, slots=True)
class LlmCapChange:
    """Смена блока: что поставили, кто и когда. `set_by` пусто — учётку удалили."""

    id: int
    block: Block
    set_by: str | None
    set_at: datetime


@dataclass(frozen=True, slots=True)
class LlmCapSet:
    """Итог смены: что действовало до неё (`None` — `.env`) и сама смена."""

    was: Block | None
    change: LlmCapChange


def refusal(wanted: Block) -> str | None:
    """Почему блок не годится — словами; `None` — годится."""
    if wanted.general is not None and wanted.general not in GENERAL_PRESETS:
        listed = ", ".join(f"{preset // 1_000_000} млн" for preset in GENERAL_PRESETS)
        return f"Общий потолок — одно из чисел списка: {listed} токенов в сутки, или «без потолка»"
    for part in PARTS:
        share = wanted.share(part)
        if share < SHARE_STEP or share % SHARE_STEP:
            return (
                f"Лимит раздела «{TITLES[part]}» — целые проценты общего шагом {SHARE_STEP}, "
                f"не меньше {SHARE_STEP}: ноль — не лимит, а запрет его вызовов"
            )
    if wanted.free_share < 0:
        return (
            f"Лимиты разделов в сумме {100 - wanted.free_share} % — больше общего: раздать можно "
            "не больше 100 %"
        )
    return None


async def set_llm_caps(session: AsyncSession, wanted: Block, *, author: UserModel) -> LlmCapSet:
    """Новый блок — с этой минуты. Без коммита: смена и запись журнала ложатся одной
    транзакцией вызывающего; память процесса — `refresh` после коммита.

    Не годится — `LlmCapError` до записи. То же, что действует, — тоже смена: отказ «менять
    нечего» держит экран, как у капа Ahrefs. Прежнее «было» читается под рекомендательной
    блокировкой транзакции: две смены разом иначе записали бы одно «было».
    """
    why = refusal(wanted)
    if why is not None:
        raise LlmCapError(why)
    await session.execute(select(func.pg_advisory_xact_lock(CAP_LOCK)))
    was = await _in_base(session)
    row = LlmCapModel(
        general_tokens=wanted.general,
        sales_share=wanted.sales,
        agent_share=wanted.agent,
        outreach_share=wanted.outreach,
        set_by=author.id,
    )
    session.add(row)
    await session.flush()
    return LlmCapSet(was=was, change=LlmCapChange(row.id, wanted, author.email, row.set_at))


async def _in_base(session: AsyncSession) -> Block | None:
    """Блок в силе по базе — последняя строка; строк нет — `None`."""
    row = (
        await session.execute(
            select(
                LlmCapModel.general_tokens,
                LlmCapModel.sales_share,
                LlmCapModel.agent_share,
                LlmCapModel.outreach_share,
            )
            .order_by(LlmCapModel.id.desc())
            .limit(1)
        )
    ).first()
    return None if row is None else Block(*row)


async def cap_changes(session: AsyncSession, *, limit: int = RECENT) -> list[LlmCapChange]:
    """Последние смены блока, новые первыми."""
    rows = await session.execute(
        select(
            LlmCapModel.id,
            LlmCapModel.general_tokens,
            LlmCapModel.sales_share,
            LlmCapModel.agent_share,
            LlmCapModel.outreach_share,
            UserModel.email,
            LlmCapModel.set_at,
        )
        .outerjoin(UserModel, UserModel.id == LlmCapModel.set_by)
        .order_by(LlmCapModel.id.desc())
        .limit(limit)
    )
    return [
        LlmCapChange(row_id, Block(general, sales, agent, outreach), email, at)
        for row_id, general, sales, agent, outreach, email, at in rows.tuples().all()
    ]


def general_words(general: int | None) -> str:
    """Общий словами: «50 000 000 токенов в сутки» или «без потолка»."""
    return "без потолка" if general is None else f"{spaced(general)} токенов в сутки"


def block_words(saved: Block | None) -> str:
    """Блок словами журнала: «общий 50 000 000 токенов в сутки; продажи 20 %, агент переписки
    20 %, аутрич 30 %, свободно 30 %»; не сохраняли — «как в настройках сервера»."""
    if saved is None:
        return "как в настройках сервера"
    shares = ", ".join(f"{TITLES[part].lower()} {saved.share(part)} %" for part in PARTS)
    return f"общий {general_words(saved.general)}; {shares}, свободно {saved.free_share} %"


# ---------------- сброс ----------------


def reset_at(now: datetime) -> datetime:
    """Когда сбросится дневной счёт: следующая полночь UTC — тот же день, что у счёта."""
    day = now.astimezone(UTC).date()
    return datetime.combine(day + timedelta(days=1), time.min, tzinfo=UTC)


def until_reset(now: datetime) -> str:
    """«через 5 ч 12 мин» — до сброса, с округлением вверх до минуты."""
    minutes = max(1, math.ceil((reset_at(now) - now).total_seconds() / 60))
    hours, rest = divmod(minutes, 60)
    if hours and rest:
        return f"через {hours} ч {rest} мин"
    return f"через {hours} ч" if hours else f"через {rest} мин"


def reset_words(now: datetime) -> str:
    """«через 5 ч 12 мин (в 03:00 по Москве)» — когда сброс, словами экрана и отказа."""
    local = reset_at(now).astimezone(MOSCOW)
    return f"{until_reset(now)} (в {local:%H:%M} по Москве)"


def after_cap(now: datetime) -> str:
    """Что делать, когда дневной потолок из `.env` выбран (блок не сохраняли): «попросите
    администратора поднять потолок на экране «Расход» или дождитесь сброса — через …»."""
    return (
        "попросите администратора поднять потолок на экране «Расход» или дождитесь сброса — "
        + reset_words(now)
    )


def after_part(now: datetime) -> str:
    """Что делать, когда лимит части выбран: добавить ей из свободного остатка или поднять
    общий — на «Расходе», или ждать сброса."""
    return (
        "попросите администратора добавить разделу из свободного остатка или поднять общий "
        f"потолок на экране «Расход», или дождитесь сброса — {reset_words(now)}"
    )
