"""Строки панели лимитов о модели: лимиты разделов, общий потолок и потолок прогона.

**Одна функция на часть** (`part_row`): строка продаж, агента переписки и аутрича — один код, разница
только в части блока (`core/llm_caps.py`). «Выбрано» — тем же счётом, что у сторожа
(`usage.spent_by_part`): число панели не расходится с отказом модели.

**Вписывают токены, хранится доля.** Лимит раздела — процент общего шагом `SHARE_STEP`, а панель
говорит токенами: `quote` переводит вписанное в долю с округлением вниз и называет, что встанет на
деле (`Quote.accepted`). Вниз, потому что это потолок: округление не должно поднимать его выше
вписанного. Ноль после округления — отказ: ноль не лимит, а запрет вызовов раздела.

**Менять можно, только когда блок сохранён.** Несохранённый блок — потолки `.env`, и лимит части от
общего не считается: первое сохранение делает вкладка «Лимиты», где блок виден целиком. Общий «без
потолка» — лимиты разделов не от чего считать; сам общий на панели меняется только числом из списка:
снять потолок, как и раньше, можно только на вкладке «Лимиты» — поле панели числовое.

**Смена — под замком блока.** Панель меняет одно число, а сеттер пишет блок целиком: без замка две
смены разом затёрли бы друг друга. Замок берётся до чтения, и смена считает доли заново: просчёт мог
устареть, пока человек читал окно.

**Стоимость — оценка.** Журнал считает токены, а цены сильной модели в коде нет; настройку цен
моделей не заводим. Смешанная цена дешёвой модели (`segment_spend.usd_per_million`) — нижняя граница,
и `cost_note` говорит это словами.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from backend.config import llm as llm_cfg
from backend.features.agent.guarding import drafts_cap
from backend.features.core import llm_caps, usage
from backend.features.core.limit_rows import LimitRefusedError, LimitRow, Quote
from backend.features.core.llm_caps import PART_AGENT, PART_OUTREACH, PART_SALES, Block
from backend.features.ops import cap_journal
from backend.features.ops.limit_fields import (
    BLANK,
    MONTH_DAYS,
    OUTREACH,
    PER_DAY,
    SALES,
    SAME,
    day_start,
    refused,
    usd,
)
from backend.features.runs.spending import month_start
from backend.features.sales.segment_spend import tokens_usd, usd_per_million
from backend.features.sales.usage_cap import sales_cap
from backend.features.serp.funds import dollars
from backend.shared.numbers import spaced

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.features.core.models.access import UserModel

SERVICE, UNIT = "Модель", "токенов"

#: Ключи строк: части блока, общий потолок сервиса и потолок прогона.
PART_KEYS = {
    PART_SALES: "sales.llm",
    PART_AGENT: "outreach.agent.llm",
    PART_OUTREACH: "outreach.llm",
}
GENERAL_KEY, RUN_KEY = "outreach.llm.general", "outreach.llm.run"
_PART_OF_KEY = {key: part for part, key in PART_KEYS.items()}

_TASKS = {PART_SALES: SALES, PART_AGENT: OUTREACH, PART_OUTREACH: OUTREACH}
_ACTIONS = {
    PART_SALES: "Все вызовы продаж: письма, ответы лидов, агент, судья сегмента",
    PART_AGENT: "Агент переписки: черновики доноров и рекламодателей",
    PART_OUTREACH: "Аутрич: разбор ответов, судья прогона, письма, ключи, тематика",
}
_SCOPES = {
    PART_SALES: "все вызовы продаж",
    PART_AGENT: "все черновики",
    PART_OUTREACH: "все вызовы аутрича",
}
#: Какая настройка держит часть, пока блок не сохраняли; у аутрича своей нет — держит общий.
_SETTINGS = {
    PART_SALES: llm_caps.SALES,
    PART_AGENT: llm_caps.AGENT,
    PART_OUTREACH: llm_caps.GENERAL,
}

FROM_SCREEN = "поле: вкладка «Лимиты»"
#: Почему строка модели не меняется, пока блок не сохраняли.
NOT_SAVED = "Менять можно, когда потолки сохранены на вкладке «Лимиты»"
NO_GENERAL = "Общий без потолка — лимита раздела нет: сначала поставьте общий на вкладке «Лимиты»"


def env_limit(part: str) -> int | None:
    """Потолок части из `.env`, пока блок не сохраняли: у продаж и агента свой, у аутрича своего
    нет — держит общий. Без потолка — `None`."""
    own = {PART_SALES: sales_cap, PART_AGENT: drafts_cap}.get(part)
    return None if own is None else own().tokens or None


def _month_usd(tokens_a_day: int | None) -> Decimal | None:
    """Оценка месяца при выборке потолка целиком; потолка нет — и оценить нечего."""
    if tokens_a_day is None:
        return None
    return usd(tokens_usd(tokens_a_day * MONTH_DAYS, usd_per_million()))


def _cost_note(tokens_a_day: int | None) -> str:
    if tokens_a_day is None:
        return "Потолка нет: расход ничем не ограничен, и оценивать нечего"
    return (
        f"Оценка снизу: {MONTH_DAYS} дней по {dollars(usd_per_million())} за миллион токенов — "
        "смешанная цена дешёвой модели; письма и ответы идут на сильной, она дороже"
    )


def _part_now(part: str, saved: Block | None, moment: datetime) -> str:
    if saved is None:
        if _SETTINGS[part] in llm_cfg.UNREAD_CAPS:
            return f"Настройка потолка не прочитана: вызовов раздела нет. {NOT_SAVED}"
        held = "Своего лимита нет, держит общий" if part == PART_OUTREACH else "Настройка сервера"
        return f"{held}. {NOT_SAVED}"
    if saved.general is None:
        return NO_GENERAL
    return (
        f"{saved.share(part)} % общего, свободно {saved.free_share} %. "
        f"Сброс {llm_caps.reset_words(moment)}"
    )


def part_row(
    part: str, saved: Block | None, today: llm_caps.Spent, month: llm_caps.Spent, moment: datetime
) -> LimitRow:
    """Строка части блока: у всех частей одна. Лимит — блока, не сохраняли — из `.env`."""
    limit = env_limit(part) if saved is None else saved.limit(part) or None
    open_to_change = saved is not None and saved.general is not None
    return replace(
        BLANK,
        key=PART_KEYS[part],
        task=_TASKS[part],
        service=SERVICE,
        action=_ACTIONS[part],
        scope=_SCOPES[part],
        unit=UNIT,
        per_day=limit,
        used_day=today[part],
        used_month=month[part],
        cost_usd=_month_usd(limit),
        cost_note=_cost_note(limit),
        now=_part_now(part, saved, moment),
        source=f".env {_SETTINGS[part]}" if saved is None else FROM_SCREEN,
        editable=frozenset({PER_DAY}) if open_to_change else frozenset(),
    )


def _general_now(saved: Block | None, general: int | None, moment: datetime) -> str:
    if saved is None:
        return f"Настройка сервера. {NOT_SAVED}"
    if general is None:
        return (
            "Без потолка: расход модели ничем не ограничен. Снять и вернуть — на вкладке «Лимиты»"
        )
    return f"Роздано разделам {100 - saved.free_share} %. Сброс {llm_caps.reset_words(moment)}"


def _general_row(
    saved: Block | None, today: llm_caps.Spent, month: llm_caps.Spent, moment: datetime
) -> LimitRow:
    """Общий потолок сервиса: выбранное — всех частей, поэтому строка закрыта без права «Продажи»
    (`api/settings/limit_panel.stage_of`)."""
    general = llm_caps.daily_cap() or None
    return replace(
        BLANK,
        key=GENERAL_KEY,
        service=SERVICE,
        action="Общий потолок сервиса",
        scope="весь сервис: продажи и аутрич",
        unit=UNIT,
        per_day=general,
        used_day=sum(today.values()),
        used_month=sum(month.values()),
        cost_usd=_month_usd(general),
        cost_note=_cost_note(general),
        now=_general_now(saved, general, moment),
        source=f".env {llm_caps.GENERAL}" if saved is None else FROM_SCREEN,
        editable=frozenset() if saved is None else frozenset({PER_DAY}),
    )


def _run_row() -> LimitRow:
    """Потолок токенов одного прогона: только настройка сервера, 0 — потолка нет."""
    cap = llm_cfg.RUN_TOKEN_CAP or None
    held = "Прогон останавливается на этом числе" if cap else "Потолка прогона нет, держит раздел"
    return replace(
        BLANK,
        key=RUN_KEY,
        service=SERVICE,
        action="Судья и тематика одного прогона",
        scope="один прогон",
        unit=UNIT,
        per_session=cap,
        cost_note=None if cap is None else _run_cost_note(cap),
        now=f"{held}. Меняется настройкой сервера",
        source=".env LLM_RUN_TOKEN_CAP",
    )


def _run_cost_note(cap: int) -> str:
    return f"Прогон целиком — оценка снизу {dollars(tokens_usd(cap, usd_per_million()))}"


async def rows(session: AsyncSession) -> list[LimitRow]:
    """Строки модели на эту минуту — по свежему чтению блока и журналу расхода."""
    moment = datetime.now(UTC)
    await llm_caps.refresh(session)
    saved = llm_caps.block()
    today = await usage.spent_by_part(session, since=day_start(moment))
    month = await usage.spent_by_part(session, since=month_start(moment))
    parts = [part_row(part, saved, today, month, moment) for part in llm_caps.PARTS]
    return [*parts, _general_row(saved, today, month, moment), _run_row()]


@dataclass(frozen=True, slots=True)
class _Plan:
    """Что значит вписанное: ответ просчёта и блок, который поставит смена (отказ — `None`)."""

    quote: Quote
    block: Block | None = None


def _rounded_share(tokens: int, general: int) -> int:
    """Вписанные токены — доля общего шагом `SHARE_STEP`, вниз: потолок округление не поднимает."""
    percent = max(0, tokens) * 100 // general
    return percent - percent % llm_caps.SHARE_STEP


def _part_plan(saved: Block, part: str, tokens: int) -> _Plan:
    """Новый лимит части: доля шагом, из свободного остатка, тем же правилом блока."""
    general, step = saved.general, llm_caps.SHARE_STEP
    if general is None:
        return _Plan(refused(NO_GENERAL))
    title, was, share = llm_caps.TITLES[part], saved.share(part), _rounded_share(tokens, general)
    most = was + saved.free_share
    if share < step:
        least = spaced(general * step // 100)
        return _Plan(refused(f"Лимит раздела — не меньше {step} % общего: {least} токенов в сутки"))
    if share > most:
        ceiling = spaced(general * most // 100)
        return _Plan(
            refused(f"Свободно {saved.free_share} %: лимит не выше {ceiling} токенов в сутки")
        )
    if share == was:
        return _Plan(refused(SAME))
    # Имя части — имя поля блока (`Block.share`).
    wanted = replace(saved, **{part: share})
    accepted = wanted.limit(part)
    rounded = "" if accepted == tokens else f" Округлено вниз: шаг — {step} % общего."
    words = (
        f"«{title}»: {share} % общего — {spaced(accepted)} токенов в сутки, было {was} % — "
        f"{spaced(saved.limit(part))}. Свободно останется {wanted.free_share} %.{rounded} "
        f"{_cost_note(accepted)}."
    )
    return _Plan(Quote(_month_usd(accepted), words, None, accepted), wanted)


def _general_plan(saved: Block, tokens: int) -> _Plan:
    """Новый общий: только число из списка; лимиты разделов двигаются вместе с ним."""
    wanted = replace(saved, general=tokens)
    why = llm_caps.refusal(wanted)
    if why is not None:
        return _Plan(refused(why))
    if tokens == saved.general:
        return _Plan(refused(SAME))
    shares = ", ".join(
        f"{llm_caps.TITLES[part].lower()} {spaced(wanted.limit(part))}" for part in llm_caps.PARTS
    )
    words = (
        f"Общий {llm_caps.general_words(tokens)}, было {llm_caps.general_words(saved.general)}. "
        f"Разделы теми же долями: {shares}. {_cost_note(tokens)}."
    )
    return _Plan(Quote(_month_usd(tokens), words, None, tokens), wanted)


def _plan(saved: Block | None, key: str, tokens: int) -> _Plan:
    if saved is None:
        return _Plan(refused(NOT_SAVED))
    if key == GENERAL_KEY:
        return _general_plan(saved, tokens)
    return _part_plan(saved, _PART_OF_KEY[key], tokens)


async def quote(session: AsyncSession, key: str, _field: str, value: int) -> Quote:
    """Просчёт нового лимита по свежему чтению блока. Ничего не пишет."""
    await llm_caps.refresh(session)
    return _plan(llm_caps.block(), key, value).quote


async def change(
    session: AsyncSession, key: str, _field: str, value: int, author: UserModel
) -> None:
    """Смена: заново считает всё от блока под замком — и встаёт строкой истории и журнала. Без
    фиксации; память процесса перечитывает вызывающий после неё (`llm_caps.refresh`)."""
    await session.execute(select(func.pg_advisory_xact_lock(llm_caps.CAP_LOCK)))
    await llm_caps.refresh(session)
    plan = _plan(llm_caps.block(), key, value)
    if plan.block is None:
        raise LimitRefusedError(plan.quote.refusal)
    done = await llm_caps.set_llm_caps(session, plan.block, author=author)
    await cap_journal.llm_block(session, done, author)
