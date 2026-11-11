"""Строки суда судьи рекламодателей: когорта, кэш фактов, признак донора-каталога и пересчёт совета
без модели.

**Кэш отдаёт факты, а не совет.** Ключ строки — пара «донор + домен», отпечаток того, что видел
судья, и когорта фактов: версия и отпечаток подсказки, отпечаток примеров и модель
(`core/models/advertiser.AdvertiserJudgementModel`). Таблица правил в ключ не входит: совет ставит код
по фактам (`judge_rules.advise`), и правка таблицы ставит совет заново по фактам строк — без модели и
без токенов (`relabel`). Свежесть — `JUDGEMENT_TTL_DAYS`, как у судей площадки и продаж: чем сайт
живёт, он меняет раз в годы.

**Когорты не смешиваются** (`agent/calibration.py`): каждое число точности — по своей когорте, и
новая подсказка, новые примеры или другая модель — новые факты, а не поправка старых. Примеров у
первой версии нет: отпечаток пустой.

**Донор-каталог — по его же судам** (решение владельца 11.11.2026, `judge_rules.is_catalog`). Совету
пары нужен признак её донора, а знает его только база: сколько пар донора рассужено в когорте и в
скольких из них последний суд назвал ссылку карточкой каталога. Считается одним запросом на всех
доноров когорты (`catalog_donors`), а не запросом на пару, и по когорте самой строки: факты другой
подсказки или модели — другие факты. Признак живой — донор набирает пары и становится каталогом,
— а у каталога от него зависит совет почти любой строки (`judge_rules.advise`). Поэтому пересчёт
смотрит не на одну версию таблицы: он ставит совет заново каждой строке, которой нынешние таблица
и признак дают другой.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.models.advertiser import AdvertiserJudgementModel
from backend.features.crawl.judge_client import PROMPT_VERSION, prompt_print
from backend.features.crawl.judge_facts import LinkFacts
from backend.features.crawl.judge_rules import (
    CARD_FACT,
    RULES,
    RULES_VERSION,
    Rule,
    Ruled,
    advise,
    is_catalog,
)

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


def _in_cohort(cohort: Cohort) -> tuple[ColumnElement[bool], ...]:
    """Строки суда этой когорты."""
    return (
        Judgement.prompt_version == cohort.prompt_version,
        Judgement.prompt_print == cohort.prompt_print,
        Judgement.examples_print == cohort.examples_print,
        Judgement.model == cohort.model,
    )


async def catalog_donors(session: AsyncSession, cohort: Cohort) -> frozenset[str]:
    """Доноры-каталоги когорты (`judge_rules.is_catalog`) — одним запросом на всех доноров: по
    последнему суду каждой пары — сколько у донора пар и в скольких ссылка названа карточкой каталога."""
    latest = (
        select(Judgement.donor_host, Judgement.facts[CARD_FACT].astext.label("card"))
        .where(*_in_cohort(cohort))
        .distinct(Judgement.donor_host, Judgement.target_root)
        .order_by(
            Judgement.donor_host,
            Judgement.target_root,
            Judgement.updated_at.desc(),
            Judgement.id.desc(),
        )
        .subquery()
    )
    # Факт — булево значение JSON: текстом это «true»; строки не той формы карточкой не считаются.
    cards = func.count().filter(latest.c.card == "true")
    counted = await session.execute(
        select(latest.c.donor_host, func.count(), cards).group_by(latest.c.donor_host)
    )
    return frozenset(
        donor for donor, pairs, with_card in counted.tuples() if is_catalog(pairs, with_card)
    )


class Catalogs:
    """Чей донор — каталог, для строк суда разных когорт: признак — по когорте самой строки, запрос —
    один на когорту, а не на строку."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._found: dict[Cohort, frozenset[str]] = {}

    async def at(self, cohort: Cohort, donor_host: str) -> bool:
        if cohort not in self._found:
            self._found[cohort] = await catalog_donors(self._session, cohort)
        return donor_host in self._found[cohort]

    async def has(self, row: AdvertiserJudgementModel) -> bool:
        cohort = Cohort(row.prompt_version, row.prompt_print, row.examples_print, row.model)
        return await self.at(cohort, row.donor_host)


@dataclass(frozen=True, slots=True)
class Known:
    """Что строка суда знает: факты и чем поставлен её совет — какой таблицей, какой и каким
    правилом."""

    facts: LinkFacts
    rules_version: str
    id: int
    advice: str = ""
    rule: str = ""

    def stale(self, ruled: Ruled) -> bool:
        """Совет строки — не тот, что нынешняя таблица и признак донора дают её фактам сейчас."""
        stored = (self.rules_version, self.advice, self.rule)
        return stored != (RULES_VERSION, ruled.advice.value, ruled.rule)


async def known(
    session: AsyncSession,
    *,
    donor_host: str,
    target_root: str,
    context_print: str,
    cohort: Cohort,
    now: datetime,
) -> Known | None:
    """Свежие факты той же пары, того же контекста и той же когорты; строка не той формы — нет.
    Колонками, а не строкой модели: совет строки переписывается мимо сессии (`restamp`), и объект,
    прочитанный раньше, помнил бы прежний."""
    found = await session.execute(
        select(
            Judgement.facts, Judgement.rules_version, Judgement.id, Judgement.advice, Judgement.rule
        ).where(
            Judgement.donor_host == donor_host,
            Judgement.target_root == target_root,
            Judgement.context_print == context_print,
            *_in_cohort(cohort),
            Judgement.updated_at >= now - timedelta(days=JUDGEMENT_TTL_DAYS),
        )
    )
    row = found.tuples().first()
    if row is None:
        return None
    body, table, number, advice, rule = row
    facts = LinkFacts.from_json(body)
    return Known(facts, table, number, advice, rule) if facts is not None else None


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
    catalog_donor: bool = False,
) -> None:
    """Суд — в строку: новый ключ — строка, тот же — поверх (два прохода разом сходятся на одной).
    `catalog_donor` — донор пары — каталог (`catalog_donors`): совет строки ставится с ним."""
    ruled = advise(row.facts, catalog_donor=catalog_donor)
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


async def _outdated(
    session: AsyncSession, rules: tuple[Rule, ...], version: str, ids: list[int] | None = None
) -> list[tuple[int, str, Ruled]]:
    """Строки, чей совет надо поставить заново, — номер, прежний совет и новый: поставленный другой
    таблицей — и нынешней, если у донора с тех пор сменился признак каталога. По ключу правила
    такие строки не выбрать: у каталога от признака зависит и «пишем», и «не понять» любого
    правила — поэтому читаются все строки (или названные) и сверяются с тем, что таблица и признак
    дают сейчас. Факты не той формы — мимо. Колонками и без текста, который видел судья: строк
    тысячи, а объект сессии помнил бы совет, уже переписанный мимо неё (`restamp`)."""
    query = select(
        Judgement.id,
        Judgement.donor_host,
        Judgement.prompt_version,
        Judgement.prompt_print,
        Judgement.examples_print,
        Judgement.model,
        Judgement.rules_version,
        Judgement.advice,
        Judgement.rule,
        Judgement.facts,
    )
    if ids is not None:
        query = query.where(Judgement.id.in_(ids))
    rows = (await session.execute(query.order_by(Judgement.id))).tuples().all()
    catalogs = Catalogs(session)
    found: list[tuple[int, str, Ruled]] = []
    for number, donor, *print_, table, advice, rule, body in rows:
        facts = LinkFacts.from_json(body)
        if facts is None:
            continue
        ruled = advise(facts, rules, catalog_donor=await catalogs.at(Cohort(*print_), donor))
        if table != version or (advice, rule) != (ruled.advice.value, ruled.rule):
            found.append((number, advice, ruled))
    return found


async def relabel_moves(
    session: AsyncSession, *, rules: tuple[Rule, ...] = RULES, version: str = RULES_VERSION
) -> Counter[tuple[str, str]]:
    """Что сделает пересчёт, без записи: сколько строк с каким советом получат какой."""
    outdated = await _outdated(session, rules, version)
    return Counter((advice, ruled.advice.value) for _, advice, ruled in outdated)


async def restamp(
    session: AsyncSession, row_id: int, ruled: Ruled, version: str = RULES_VERSION
) -> None:
    """Новый совет — в строку суда, без модели: факты и их срок остаются как были."""
    await session.execute(
        update(Judgement)
        .where(Judgement.id == row_id)
        # Срок фактов — их, а не пересчёта: без явного значения `updated_at` сдвинулся бы.
        .values(
            advice=ruled.advice.value,
            rule=ruled.rule,
            rules_version=version,
            updated_at=Judgement.updated_at,
        )
        .execution_options(synchronize_session=False)
    )


async def relabel(
    session: AsyncSession,
    *,
    rules: tuple[Rule, ...] = RULES,
    version: str = RULES_VERSION,
    ids: list[int] | None = None,
) -> int:
    """Совет строк, поставленный другой таблицей или до смены признака донора (`_outdated`), — заново
    по их фактам, без модели. Факты, их цена и срок — те, что заплачены: вызова модели у такого
    совета нет. Сколько строк переписано."""
    outdated = await _outdated(session, rules, version, ids)
    for number, _, ruled in outdated:
        await restamp(session, number, ruled, version)
    return len(outdated)
