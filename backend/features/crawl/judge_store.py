"""Строки суда судьи рекламодателей: когорта, кэш фактов и пересчёт совета без модели.

**Кэш отдаёт факты, а не совет.** Ключ строки — пара «донор + домен», отпечаток того, что видел
судья, и когорта фактов: версия и отпечаток подсказки, отпечаток примеров и модель
(`core/models/advertiser.AdvertiserJudgementModel`). Таблица правил в ключ не входит: совет ставит код
по фактам (`judge_rules.advise`), и правка таблицы ставит совет заново по фактам строк — без модели и
без токенов (`relabel`). Свежесть — `JUDGEMENT_TTL_DAYS`, как у судей площадки и продаж: чем сайт
живёт, он меняет раз в годы.

**Когорты не смешиваются** (`agent/calibration.py`): каждое число точности — по своей когорте, и
новая подсказка, новые примеры или другая модель — новые факты, а не поправка старых. Примеров у
первой версии нет: отпечаток пустой.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.models.advertiser import AdvertiserJudgementModel
from backend.features.crawl.judge_client import PROMPT_VERSION, prompt_print
from backend.features.crawl.judge_facts import LinkFacts
from backend.features.crawl.judge_rules import RULES, RULES_VERSION, Rule, advise

Judgement = AdvertiserJudgementModel

#: Срок фактов: чем сайт живёт, он меняет раз в годы — как у судей площадки и продаж.
JUDGEMENT_TTL_DAYS = 180
#: Отпечаток примеров первой версии: примеров нет. Они появятся, когда вне контрольной выборки
#: наберётся по 30 решений человека каждого вида, — и станут новой когортой.
EXAMPLES_PRINT = ""
#: Сколько знаков того, что видел судья, хранит строка: две стороны по 1500 знаков и подписи.
SEEN_CHARS = 4000
#: Что тот же ключ переписывает поверх: всё, кроме пары, контекста и когорты.
REFRESHED = (
    "rules_version",
    "facts",
    "advice",
    "rule",
    "seen",
    "language",
    "donor_niche",
    "tokens",
    "retried",
    "updated_at",
)


@dataclass(frozen=True, slots=True)
class Cohort:
    """Когорта фактов: подсказка с отпечатком, примеры и модель."""

    prompt_version: str
    prompt_print: str
    examples_print: str
    model: str


def cohort_of(model: str) -> Cohort:
    """Нынешняя когорта фактов для этой модели."""
    return Cohort(PROMPT_VERSION, prompt_print(), EXAMPLES_PRINT, model)


@dataclass(frozen=True, slots=True)
class Known:
    """Что строка суда знает: факты и какой таблицей поставлен её совет."""

    facts: LinkFacts
    rules_version: str
    id: int


async def known(
    session: AsyncSession,
    *,
    donor_host: str,
    target_root: str,
    context_print: str,
    cohort: Cohort,
    now: datetime,
) -> Known | None:
    """Свежие факты той же пары, того же контекста и той же когорты; строка не той формы — нет."""
    row = await session.scalar(
        select(Judgement).where(
            Judgement.donor_host == donor_host,
            Judgement.target_root == target_root,
            Judgement.context_print == context_print,
            Judgement.prompt_version == cohort.prompt_version,
            Judgement.prompt_print == cohort.prompt_print,
            Judgement.examples_print == cohort.examples_print,
            Judgement.model == cohort.model,
            Judgement.updated_at >= now - timedelta(days=JUDGEMENT_TTL_DAYS),
        )
    )
    facts = LinkFacts.from_json(row.facts) if row is not None else None
    return Known(facts, row.rules_version, row.id) if row is not None and facts else None


@dataclass(frozen=True, slots=True)
class Row:
    """Что пишется в строку суда, кроме пары, контекста и когорты."""

    facts: LinkFacts
    seen: str
    language: str | None
    donor_niche: str | None
    tokens: int
    retried: bool


async def remember(
    session: AsyncSession,
    *,
    donor_host: str,
    target_root: str,
    context_print: str,
    cohort: Cohort,
    row: Row,
    moment: datetime,
) -> None:
    """Суд — в строку: новый ключ — строка, тот же — поверх (два прохода разом сходятся на одной)."""
    ruled = advise(row.facts)
    values: dict[str, object] = {
        "donor_host": donor_host,
        "target_root": target_root,
        "context_print": context_print,
        "prompt_version": cohort.prompt_version,
        "prompt_print": cohort.prompt_print,
        "examples_print": cohort.examples_print,
        "model": cohort.model,
        "rules_version": RULES_VERSION,
        "facts": row.facts.as_json(),
        "advice": ruled.advice.value,
        "rule": ruled.rule,
        "seen": row.seen[:SEEN_CHARS],
        "language": row.language,
        "donor_niche": row.donor_niche,
        "tokens": row.tokens,
        "retried": row.retried,
        "updated_at": moment,
    }
    statement = insert(Judgement).values(values)
    fresh = statement.excluded
    await session.execute(
        statement.on_conflict_do_update(
            constraint="uq_advertiser_judgements_key",
            set_={name: getattr(fresh, name) for name in REFRESHED},
        )
    )


async def _by_other_table(
    session: AsyncSession, version: str, ids: list[int] | None = None
) -> list[tuple[AdvertiserJudgementModel, LinkFacts]]:
    """Строки, чей совет поставлен другой таблицей, с их фактами; факты не той формы — мимо."""
    query = select(Judgement).where(Judgement.rules_version != version)
    if ids is not None:
        query = query.where(Judgement.id.in_(ids))
    read = [(row, LinkFacts.from_json(row.facts)) for row in await session.scalars(query)]
    return [(row, facts) for row, facts in read if facts is not None]


async def relabel_moves(
    session: AsyncSession, *, rules: tuple[Rule, ...] = RULES, version: str = RULES_VERSION
) -> Counter[tuple[str, str]]:
    """Что сделает пересчёт, без записи: сколько строк с каким советом получат какой."""
    return Counter(
        (row.advice, advise(facts, rules).advice.value)
        for row, facts in await _by_other_table(session, version)
    )


async def relabel(
    session: AsyncSession,
    *,
    rules: tuple[Rule, ...] = RULES,
    version: str = RULES_VERSION,
    ids: list[int] | None = None,
) -> int:
    """Совет строк, поставленный другой таблицей, — заново по их фактам, без модели. Факты, их цена
    и срок — те, что заплачены: вызова модели у такого совета нет. Сколько строк переписано."""
    changed = 0
    for row, facts in await _by_other_table(session, version, ids):
        ruled = advise(facts, rules)
        await session.execute(
            update(Judgement)
            .where(Judgement.id == row.id)
            # Срок фактов — их, а не пересчёта: без явного значения `updated_at` сдвинулся бы.
            .values(
                advice=ruled.advice.value,
                rule=ruled.rule,
                rules_version=version,
                updated_at=Judgement.updated_at,
            )
            .execution_options(synchronize_session=False)
        )
        changed += 1
    return changed
