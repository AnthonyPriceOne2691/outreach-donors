"""План работ — в базу и из базы: гипотезы продаж, план, гипотезы плана и реестр ключей.

Запуск плана (маршрут — следующий срез) разбирает книгу заново (`plan_file.read_plan`), считает смету
(`plan_estimate.plan_estimate`), сверяет отпечаток с тем, что человек видел в превью (`plan_digest`), и
зовёт `save_plan`. Черновик на сервере не хранится: разбор — чистая функция книги.

**Правила сохранения** — отказ словами (`PlanRefusedError`, ответ 400), все нарушения сразу:

- все листы без ошибок: план без листа с ошибкой — не тот план, что загрузил человек;
- у каждого листа цель «Найти сайтов» — своя или общая из окна загрузки; нет ни той, ни другой —
  отказ, а не сбор до предельной глубины без цели;
- потолки плана и гипотезы обязательны и больше нуля (решение владельца 16.11);
- лист — новая гипотеза: имя существующей гипотезы — отказ словами «измените поле «Название»»
  (решение владельца 16.11; слова — 25.11: имя даёт поле карточки, ярлык листа на него не влияет),
  той же проверкой, что у заведения гипотезы (`hypotheses._free`: гипотеза и рассылка
  «Продажи: <имя>»).

**Запись — в транзакции вызывающего, коммит — за ним**: гипотеза — правилом заведения
(`hypotheses.add`: журнал и прежние правила), поле «страны компаний» — правкой (`hypotheses.edit`, со
своей строкой журнала), описание сегмента — «Кому пишем». Реестр ключей — ключ листа на каждом рынке
(прогон — на каждый рынок, как в смете), пачками по `CHUNK` строк. План — своей строкой журнала.

**Чтение** — `plans` (список для вкладки) и `plan` (один): строки плана и гипотез, имя гипотезы и
сколько ключей в реестре и сколько из них уже купили хотя бы страницу, — тремя запросами на список.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, Stage
from backend.features.sales import chain, chain_text, hypotheses
from backend.features.sales.hypothesis_countries import OtherCountries
from backend.features.sales.intake import IntakeError
from backend.features.sales.models import SalesHypothesisModel
from backend.features.sales.plan_card import LABELS, Field
from backend.features.sales.plan_estimate import PlanEstimate
from backend.features.sales.plan_file import PlanDraft, PlanHypothesis
from backend.features.sales.plan_models import (
    DIGEST_LENGTH,
    PlanMode,
    PlanStatus,
    SalesPlanHypothesisModel,
    SalesPlanKeywordModel,
    SalesPlanModel,
)
from backend.features.sales.segment_estimate_keys import fingerprint
from backend.features.sales.segment_tier import JudgeTier, tier_entry
from backend.features.serp.modes import SerpMode
from backend.features.serp.protocol import keyword_key

logger = logging.getLogger(__name__)

#: Строк реестра ключей на вставку — одним запросом со многими наборами значений (executemany).
#: Замер 16.11.2026 на машине разработки: 30 тысяч ключей — 1,3 с, 300 тысяч (10 листов по 30
#: тысяч, один рынок) — 11 с; вставка одним VALUES на пачку была вдвое дольше (22 с).
CHUNK = 1_000
#: Планов в списке вкладки — последние: план — работа на неделю или месяц.
PLANS_SHOWN = 50


@dataclass(frozen=True, slots=True)
class Pace:
    """Чем режим плана платит за сбор: режим покупки выдачи (`serp/modes.py`) и тариф судьи
    (`segment_tier`)."""

    serp_mode: SerpMode
    judge_tier: JudgeTier


#: Режим плана → выдача и тариф судьи — одно место соответствия: смета превью и сохранения
#: (`api/sales/plans.py`), отпечаток превью (`plan_digest`) и конвейер берут его отсюда. Экономный —
#: отложенная очередь выдачи и судья на Flex (вдвое дешевле, медленнее); быстрый — синхронная
#: выдача и обычный тариф.
PLAN_MODES: dict[PlanMode, Pace] = {
    PlanMode.ECONOMY: Pace(SerpMode.STANDARD, JudgeTier.FLEX),
    PlanMode.FAST: Pace(SerpMode.LIVE, JudgeTier.STANDARD),
}
#: Режим плана → режим покупки выдачи — из того же соответствия (`PLAN_MODES`).
SERP_MODES: dict[PlanMode, SerpMode] = {mode: pace.serp_mode for mode, pace in PLAN_MODES.items()}
#: «Действие» записи журнала плана: под общей «Правкой», как у гипотезы.
MADE = "план работ заведён"


class PlanRefusedError(IntakeError):
    """План не заведён: текст называет все нарушения и что сделать."""


@dataclass(frozen=True, slots=True)
class PlanAsk:
    """Что выбрал человек в окне загрузки: имя плана, режим, общая цель и потолки."""

    name: str
    mode: PlanMode
    #: Общая цель «Найти сайтов» — для листов без своей.
    goal: int | None
    #: Потолок плана и потолок каждой его гипотезы — доллары «выдача + модель».
    cap_usd: Decimal
    hypothesis_cap_usd: Decimal


@dataclass(frozen=True, slots=True)
class PlanRow:
    """Гипотеза плана для экрана: строка, имя гипотезы, ключей в реестре (ключ × рынок), из них
    купленных хотя бы до первой страницы и купленных до страницы её волны (`wave_page`: у волны
    4–5 — четвёртой и глубже) — «ключи 1 200 из 30 000» этапа сбора."""

    row: SalesPlanHypothesisModel
    name: str
    keywords: int
    bought: int
    wave_bought: int = 0


@dataclass(frozen=True, slots=True)
class PlanView:
    """План с гипотезами по порядку листов."""

    plan: SalesPlanModel
    hypotheses: tuple[PlanRow, ...]


def plan_digest(draft: PlanDraft, ask: PlanAsk) -> str:
    """Отпечаток того, что человек видел в превью и по чему запускает: листы (поля карточки и
    ключи), режим, общая цель и потолки, тариф судьи — только экономный (`segment_tier.tier_entry`:
    у быстрого отпечаток прежний до байта). Имя плана в него не входит — его правят и после превью.
    Ключи — своим отпечатком: их до 300 тысяч."""
    seen = {
        "sheets": [
            {
                "sheet": found.sheet,
                "card": asdict(found.card),
                "keys": hashlib.sha256("\n".join(found.keys.taken).encode()).hexdigest(),
            }
            for found in draft.hypotheses
        ],
        "mode": ask.mode.value,
        "goal": ask.goal,
        "caps": [str(ask.cap_usd), str(ask.hypothesis_cap_usd)],
        **tier_entry(PLAN_MODES[ask.mode].judge_tier),
    }
    return fingerprint(seen, DIGEST_LENGTH)


def _quoted(names: Sequence[str]) -> str:
    return ", ".join(f"«{name}»" for name in names)


def _rules(draft: PlanDraft, ask: PlanAsk) -> list[str]:
    """Нарушения правил сохранения, которые видны без базы, — словами."""
    broken = [found.sheet for found in draft.hypotheses if not found.ready]
    aimless = [found.sheet for found in draft.hypotheses if (found.card.target or ask.goal) is None]
    checks = (
        (bool(broken), f"листы с ошибками: {_quoted(broken)} — поправьте книгу и загрузите заново"),
        (
            bool(aimless),
            f"у листов {_quoted(aimless)} нет цели «Найти сайтов», и общая цель не задана — "
            "впишите цель в лист или задайте общую",
        ),
        (ask.cap_usd <= 0, "потолок плана обязателен — задайте его в долларах, больше нуля"),
        (
            ask.hypothesis_cap_usd <= 0,
            "потолок гипотезы обязателен — задайте его в долларах, больше нуля",
        ),
    )
    return [words for failed, words in checks if failed]


async def taken_names(session: AsyncSession, draft: PlanDraft) -> list[str]:
    """Листы, чьё имя уже занято гипотезой или рассылкой продаж, — словами: превью называет их
    до запуска, запуск отказывает (`save_plan`)."""
    found: list[str] = []
    for sheet in draft.hypotheses:
        try:
            await hypotheses._free(session, sheet.card.name)
        except hypotheses.NameTakenError as exc:
            # Занятое имя — ответ человеку, а не сбой: слова — в отказе, журнал — лист.
            logger.info("план работ: имя листа занято", extra={"sheet": sheet.sheet})
            found.append(
                f"лист «{sheet.sheet}»: {exc} — измените поле «{LABELS[Field.NAME]}» на листе "
                "(ярлык листа на имя не влияет)"
            )
    return found


async def problems(session: AsyncSession, draft: PlanDraft, ask: PlanAsk) -> list[str]:
    """Почему план не завести — словами, все сразу; пусто — заводится. Превью показывает их до
    кнопки, запуск ими отказывает (`save_plan`)."""
    return [*_rules(draft, ask), *await taken_names(session, draft)]


async def chain_gaps(session: AsyncSession, draft: PlanDraft) -> dict[str, str]:
    """Листы, чьи письма не соберутся: у новой гипотезы своей цепочки нет, и общая на языке писем
    неполна, — лист → слова для превью. Это предупреждение, а не отказ: цепочку задают на экране
    «Цепочка», пока гипотеза собирает базу."""
    gaps: dict[str, str] = {}
    missing: dict[str, list[str]] = {}
    for found in draft.ready:
        code = found.card.language
        if code not in missing:
            general = await chain.resolve(session, hypothesis_id=None, language=code)
            missing[code] = general.missing
        if missing[code]:
            gaps[found.sheet] = (
                f"письма не соберутся: в общей цепочке {chain_text.in_language(code)} нет "
                f"{', '.join(missing[code])} — задайте цепочку до первых писем гипотезы"
            )
    return gaps


def _keyword_rows(row_id: int, found: PlanHypothesis) -> list[dict[str, Any]]:
    """Реестр ключей листа: ключ на каждом рынке, по порядку листа."""
    keys = [
        (position, phrase, keyword_key(phrase))
        for position, phrase in enumerate(found.keys.taken, 1)
    ]
    return [
        {
            "plan_hypothesis_id": row_id,
            "market": market,
            "keyword": phrase,
            "keyword_key": key,
            "position": position,
        }
        for market in found.card.markets
        for position, phrase, key in keys
    ]


async def _sheet(
    session: AsyncSession,
    plan: SalesPlanModel,
    position: int,
    found: PlanHypothesis,
    ask: PlanAsk,
    author_id: int | None,
) -> SalesPlanHypothesisModel:
    """Лист — в гипотезу продаж, строку плана и реестр ключей."""
    card = found.card
    made = await hypotheses.add(session, card.name, card.segment, author_id=author_id)
    if card.other_countries != OtherCountries.REVIEW or card.excluded_countries:
        await hypotheses.edit(
            session,
            made.id,
            other_countries=card.other_countries.value,
            excluded_countries=list(card.excluded_countries),
            author_id=author_id,
        )
    row = SalesPlanHypothesisModel(
        plan_id=plan.id,
        hypothesis_id=made.id,
        position=position,
        sheet=found.sheet,
        markets=list(card.markets),
        language=card.language,
        target=card.target or ask.goal,
        max_pages=card.depth_pages,
        cap_usd=ask.hypothesis_cap_usd,
    )
    session.add(row)
    await session.flush()
    rows = _keyword_rows(row.id, found)
    for start in range(0, len(rows), CHUNK):
        await session.execute(insert(SalesPlanKeywordModel), rows[start : start + CHUNK])
    return row


async def save_plan(
    session: AsyncSession,
    draft: PlanDraft,
    ask: PlanAsk,
    estimate: PlanEstimate,
    *,
    author: str | None = None,
    author_id: int | None = None,
) -> SalesPlanModel:
    """Завести план из черновика книги. Нарушения правил — `PlanRefusedError` словами, все сразу,
    до первой записи. Записи и журнал — в транзакции вызывающего, коммит — за ним."""
    refused = await problems(session, draft, ask)
    if refused:
        raise PlanRefusedError("план не заведён: " + "; ".join(refused))
    plan = SalesPlanModel(
        name=" ".join(ask.name.split()),
        mode=ask.mode.value,
        target_default=ask.goal,
        cap_usd=ask.cap_usd,
        status=PlanStatus.RUNNING.value,
        estimate=asdict(estimate),
        digest=plan_digest(draft, ask),
        created_by=author,
    )
    session.add(plan)
    await session.flush()
    # Время заведения ставит база: без чтения назад поле не загружено, и экран в async читал бы
    # его ленивым запросом и падал (как у гипотезы, `hypotheses.add`).
    await session.refresh(plan)
    for position, found in enumerate(draft.hypotheses, 1):
        await _sheet(session, plan, position, found, ask, author_id)
    await AccessRepository(session).record(
        AuditAction.USER_UPDATED,
        author_id=author_id,
        target=f"sales_plan:{plan.id}",
        details={
            "действие": MADE,
            "план": plan.name,
            "листов": len(draft.hypotheses),
            "режим": plan.mode,
            "потолок плана, $": str(ask.cap_usd),
            "потолок гипотезы, $": str(ask.hypothesis_cap_usd),
        },
        stage=Stage.SALES,
    )
    logger.info("продажи: заведён план работ", extra={"plan_id": plan.id})
    return plan


async def _views(session: AsyncSession, plans: Sequence[SalesPlanModel]) -> list[PlanView]:
    """Планы с гипотезами по порядку листов и числами реестра ключей."""
    if not plans:
        return []
    ids = [plan.id for plan in plans]
    rows = (
        await session.execute(
            select(SalesPlanHypothesisModel, SalesHypothesisModel.name)
            .join(
                SalesHypothesisModel,
                SalesHypothesisModel.id == SalesPlanHypothesisModel.hypothesis_id,
            )
            .where(SalesPlanHypothesisModel.plan_id.in_(ids))
            .order_by(SalesPlanHypothesisModel.plan_id, SalesPlanHypothesisModel.position)
        )
    ).tuples()
    pairs = list(rows)
    keys, rows_of = SalesPlanKeywordModel, SalesPlanHypothesisModel
    counted = await session.execute(
        select(
            keys.plan_hypothesis_id,
            func.count(),
            func.count().filter(keys.pages > 0),
            func.count().filter(keys.pages >= rows_of.wave_page),
        )
        .join(rows_of, rows_of.id == keys.plan_hypothesis_id)
        .where(keys.plan_hypothesis_id.in_([row.id for row, _ in pairs]))
        .group_by(keys.plan_hypothesis_id)
    )
    counts = {
        row_id: (int(total), int(bought), int(wave))
        for row_id, total, bought, wave in counted.tuples()
    }
    return [
        PlanView(
            plan,
            tuple(
                PlanRow(row, name, *counts.get(row.id, (0, 0, 0)))
                for row, name in pairs
                if row.plan_id == plan.id
            ),
        )
        for plan in plans
    ]


async def plans(session: AsyncSession, *, limit: int = PLANS_SHOWN) -> list[PlanView]:
    """Последние планы — новые сверху, не больше `limit`."""
    found = await session.scalars(
        select(SalesPlanModel).order_by(SalesPlanModel.id.desc()).limit(limit)
    )
    return await _views(session, list(found))


async def plan(session: AsyncSession, plan_id: int) -> PlanView | None:
    """План по номеру; нет такого — `None`."""
    found = await session.get(SalesPlanModel, plan_id)
    if found is None:
        return None
    [view] = await _views(session, [found])
    return view
