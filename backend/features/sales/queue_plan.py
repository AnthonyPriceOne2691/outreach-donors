"""Кому сборка очереди продаж напишет первое письмо — одно решение у сборки и у счёта.

Сборка (`queue.build`) решает по лиду, писать ли ему (`plan_of`): передан телемаркетологу, в
стоп-листе, в «кому не пишем», сайт в работе у другого направления, адрес держит очистка, язык без
цепочки, цепочка задана не целиком, нет значения подстановки, ключ письма длиннее колонки — лид
ждёт с причиной; иначе — что писать. Решение здесь одно, а факты о лиде приходят двумя путями
(`Facts`): у сборки — запросом на лида, как всегда (`queue._PerLead`: между лидами идёт модель), у
счёта «сколько первых писем сборка ещё напишет» (`writable_counts`) — партией: прогноз рассылки
спрашивает его раз в минуту, и запрос на лида там стал бы тысячами запросов.

**Счёт — партией** (`writable_counts`): кандидаты — одним запросом (лиды «готов», которым сборка
напишет сейчас, `elsewhere.writable`, чей адрес письму не мешает, `unconfirmed.holds`; переданные
телемаркетологу — тем же запросом, правилом шва цепочки `handoff.handed_off_rule`); стоп-листы и
сайты в работе у другого направления — чтением на партию (`clean_known.load_known`, правило —
очистки: `cleaning._stoplisted`, `_unsubscribed`, как у снятия лидов стоп-листом); цепочка — одна на
пару «гипотеза, язык»; остальное — чистые функции.

Сколько напишет — не сколько уйдёт в очередь: модель может не уложить письмо в коридор отличия, а
потолок модели — встать посреди сборки; это знает только сама сборка.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import Stage
from backend.features.core.models.domain import DomainModel
from backend.features.letters.chain import FIRST_STEP
from backend.features.sales import chain, chain_text, elsewhere, letter
from backend.features.sales.clean_known import Known, load_known
from backend.features.sales.cleaning import _stoplisted, _unsubscribed
from backend.features.sales.handoff import handed_off_rule
from backend.features.sales.lead_values import language_base
from backend.features.sales.models import SalesLeadModel, SalesThreadModel
from backend.features.sales.not_writing import excluded
from backend.features.sales.queue_report import no_chain
from backend.features.sales.unconfirmed import first_letter, holds

#: Почему лид ждёт — словами отчёта сборки; одинаковые причины складываются.
HANDED_OFF = "передан телемаркетологу"
STOPLIST = "стоп-лист"
LONG_KEY = "адрес и домен длиннее ключа письма"


@dataclass(frozen=True, slots=True)
class Plan:
    """Что пишем лиду: цепочка его языка и значения подстановок."""

    chain: chain.Chain
    values: dict[str, str]
    key: str


class Facts(Protocol):
    """Что о лиде знает база — для решения сборки (`plan_of`)."""

    async def handed(self, lead: SalesLeadModel) -> bool:
        """Лид передан телемаркетологу (`handoff.handed_off_rule`)."""

    async def stopped(self, lead: SalesLeadModel, host: str) -> bool:
        """Лида держит стоп-лист продаж или общий (`mail.stopped_by`)."""

    async def busy(self, lead: SalesLeadModel) -> Stage | None:
        """Направление, у которого сайт лида в работе (`elsewhere.roles`); `None` — сайт свободен."""

    async def chain_of(self, hypothesis_id: int, language: str) -> chain.Chain:
        """Цепочка гипотезы на языке (`chain.resolve`)."""


async def plan_of(lead: SalesLeadModel, host: str, hypothesis_id: int, facts: Facts) -> Plan | str:
    """Что писать лиду — или почему он ждёт (строкой отчёта). Без вызова модели: лид, которому
    писать нельзя, её не стоит."""
    why = await _blocked(lead, host, facts) or first_letter(lead)
    if why is not None:
        return why
    # Язык без цепочки — причина в отчёте, не ошибка; язык — основой кода: `ru-RU` → `ru`.
    language = language_base(lead.language)
    if language not in chain_text.LANGUAGES:
        return no_chain(language)
    found = await facts.chain_of(hypothesis_id, language)
    if found.missing:
        # Язык словами («на русском»), а не кодом: причину читает человек на экране очереди.
        return f"цепочка {chain_text.in_language(language)} задана не целиком"
    values = letter.values_of(lead, host)
    absent = letter.lacking(found.steps.values(), values)
    if absent:
        return f"у лида нет значения для {', '.join(absent)}"
    key = letter.key(host, lead.email, FIRST_STEP)
    return LONG_KEY if len(key) > letter.KEY_LENGTH else Plan(found, values, key)


async def _blocked(lead: SalesLeadModel, host: str, facts: Facts) -> str | None:
    """Почему этому лиду писать нельзя: передан, в стоп-листе, в «кому не пишем» — или ждёт,
    пока сайт в работе у другого направления (`elsewhere.py`)."""
    if await facts.handed(lead):
        return HANDED_OFF
    if await facts.stopped(lead, host):
        return STOPLIST
    off = excluded(lead.email, host)
    if off is not None:
        return off.waiting
    busy = await facts.busy(lead)
    return elsewhere.waiting(busy) if busy is not None else None


@dataclass(slots=True)
class _Batch:
    """Факты партии кандидатов, прочитанные разом; цепочка — одна на пару «гипотеза, язык»."""

    session: AsyncSession
    known: Known
    handed_off: frozenset[int]
    chains: dict[tuple[int, str], chain.Chain] = field(default_factory=dict)

    async def handed(self, lead: SalesLeadModel) -> bool:
        return lead.id in self.handed_off

    async def stopped(self, lead: SalesLeadModel, _host: str) -> bool:
        return (
            _stoplisted(lead, self.known) is not None or _unsubscribed(lead, self.known) is not None
        )

    async def busy(self, lead: SalesLeadModel) -> Stage | None:
        return self.known.busy_domains.get(lead.domain_id)

    async def chain_of(self, hypothesis_id: int, language: str) -> chain.Chain:
        pair = (hypothesis_id, language)
        if pair not in self.chains:
            self.chains[pair] = await chain.resolve(
                self.session, hypothesis_id=hypothesis_id, language=language
            )
        return self.chains[pair]


async def writable_counts(session: AsyncSession, *, now: datetime | None = None) -> dict[int, int]:
    """Сколько первых писем сборка ещё напишет — по гипотезам: лиды, которым она напишет сейчас,
    решением сборки (`plan_of`) с фактами партией. Письма, уже стоящие в очереди, — не здесь: их
    считает очередь. Без записи."""
    rows = await _candidates(session)
    leads = [lead for lead, _, _ in rows]
    facts = _Batch(
        session,
        await load_known(session, leads, now or datetime.now(UTC)),
        frozenset(lead.id for lead, _, handed in rows if handed),
    )
    counts: Counter[int] = Counter()
    for lead, host, _ in rows:
        if isinstance(await plan_of(lead, host, lead.hypothesis_id, facts), Plan):
            counts[lead.hypothesis_id] += 1
    return dict(counts)


async def _candidates(session: AsyncSession) -> Sequence[tuple[SalesLeadModel, str, bool]]:
    """Лиды «готов», которым сборка напишет первое письмо сейчас, чей адрес письму не мешает, — с
    сайтом компании и тем, передан ли лид телемаркетологу. Одним запросом."""
    rows = await session.execute(
        select(
            SalesLeadModel,
            DomainModel.host,
            handed_off_rule(SalesLeadModel.id.expression).label("handed"),
        )
        .join(DomainModel, DomainModel.id == SalesLeadModel.domain_id)
        .outerjoin(SalesThreadModel, SalesThreadModel.lead_id == SalesLeadModel.id)
        .where(elsewhere.writable(), ~holds())
        .order_by(SalesLeadModel.id)
    )
    return rows.tuples().all()


async def supply(session: AsyncSession) -> int:
    """Сколько первых писем сборка ещё напишет, по всем гипотезам — ответ мосту прогноза рассылки
    (`core/stage_forecast.SalesForecast.supply`)."""
    return sum((await writable_counts(session)).values())
