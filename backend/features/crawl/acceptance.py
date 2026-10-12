"""Приёмка Этапа 2 по донору: сколько рекламодателей найдено, сколько из переведённых
с адресом, какой доле ушло письмо.

ТЗ, Этап 2, «Приёмка»: «по донору: 100 рекламодателей с контактами, дедуплицировано,
письма ушли 70% из них». До 12.10.2026 этих чисел не было нигде (сверка ТЗ
11.10.2026): перевод знал «рекламодателей N, с адресом M» на всех донорах сразу,
а письма — на всех рекламодателях.

**Рекламодатель донора — кандидат его последнего обхода, которому пишем**
(`advertiser_rules.writes_to`): «куплено» скоринга или «пишем» человека, без «не
пишем» — условие перевода. Кандидаты донора уже по одному на домен (`crawl/gate.py`) —
это и есть «дедуплицировано». Домен у двух доноров считается у обоих: он рекламодатель
каждого из них, а письмо ему одно.

**С адресом и «ушло» — только среди переведённых** (проверка ветки 12.10.2026): адрес
ищут и письмо пишут рекламодателю из `advertisers`, которому пишем
(`advertiser_rules.in_work`). Домен, снятый стоп-листом при переводе, с адресом от
Этапа 1 приёмку не украшает: письма ему не будет. Адрес — в базе (`has_address`),
«ушло» — платформа приняла хоть одно письмо Этапа 2 (`OfferFlags.gone`); доля — от
тех, что с адресом.

**Донор-поставщик — помечен и в сумму не идёт.** Его рекламодателей не трогаем
(`SupplierDonorModel`): перевод их отсеивает, и порог ему не задача.

**Все доноры вместе — справочно, по разным доменам.** Критерий ТЗ — по донору; сумма
отвечает на оговорку самого ТЗ («Арифметика», п. 6: сто рекламодателей с одного
донора — на грани), и домен у двух доноров в ней один.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, Select, Subquery, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from backend.features.contacts.repository import has_address
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.advertisers import AdvertiserModel, SupplierDonorModel
from backend.features.core.models.crawl import CrawlRunModel
from backend.features.core.models.domain import DomainModel
from backend.features.crawl.advertiser_rules import in_work, writes_to
from backend.features.crawl.offer_state import OfferFlags

#: Порог ТЗ: рекламодателей с контактами у донора.
TARGET_WITH_ADDRESS = 100
#: Порог ТЗ: доля рекламодателей с контактами, которым ушло письмо.
TARGET_SENT_SHARE = 0.70


@dataclass(frozen=True, slots=True)
class Tally:
    """Числа приёмки: найдено, переведено, с адресом, письмо ушло."""

    found: int = 0
    #: Из найденных — рекламодатели, которым пишем: перевод их завёл, стоп-листы не сняли.
    promoted: int = 0
    #: Из переведённых — с адресом в базе.
    with_address: int = 0
    #: Из тех, что с адресом, — кому ушло письмо.
    sent: int = 0

    @property
    def sent_share(self) -> float | None:
        """Доля ушедших от тех, что с адресом; адресов нет — `None`, а не ноль."""
        return self.sent / self.with_address if self.with_address else None

    @property
    def accepted(self) -> bool:
        """Порог ТЗ пройден: сто с адресом, и письмо ушло семидесяти процентам из них."""
        share = self.sent_share
        return (
            self.with_address >= TARGET_WITH_ADDRESS
            and share is not None
            and share >= TARGET_SENT_SHARE
        )


@dataclass(frozen=True, slots=True)
class DonorTally:
    """Приёмка одного обойдённого донора."""

    host: str
    tally: Tally
    #: Донор-поставщик: его рекламодателей не трогаем, порог ему не задача.
    supplier: bool = False


@dataclass(frozen=True, slots=True)
class Acceptance:
    donors: list[DonorTally]
    #: Сколько обойдённых доноров всего — строк может быть меньше (`limit`).
    total: int
    #: Все доноры вместе, кроме поставщиков, — по разным доменам. Справочно.
    overall: Tally


def _found() -> Select[tuple[str, str, int]]:
    """Рекламодатели доноров: донор, домен, номер домена в базе (если завели)."""
    domain = aliased(DomainModel)
    return (
        select(
            CandidateModel.donor_host.label("donor"),
            CandidateModel.target_root.label("root"),
            domain.id.label("domain_id"),
        )
        .outerjoin(domain, domain.host == CandidateModel.target_root)
        .where(writes_to())
        .distinct()
    )


def _counts(rows: Subquery, flags: OfferFlags) -> tuple[ColumnElement[int], ...]:
    """Четыре числа приёмки над строками `rows` — счётом с условием, одним запросом.
    Считаются домены, а не строки: у суммы по донорам домен двух доноров — один."""
    domain_id = rows.c.domain_id
    promoted = (
        select(AdvertiserModel.id).where(AdvertiserModel.domain_id == domain_id, in_work()).exists()
    )
    addressed = and_(promoted, has_address(domain_id))
    domains = func.count(func.distinct(rows.c.root))
    return (
        domains,
        domains.filter(promoted),
        domains.filter(addressed),
        domains.filter(and_(addressed, flags.gone())),
    )


def _tally(numbers: Sequence[Any]) -> Tally:
    found, promoted, with_address, sent = (int(number or 0) for number in numbers)
    return Tally(found=found, promoted=promoted, with_address=with_address, sent=sent)


async def _suppliers(session: AsyncSession) -> set[str]:
    """Доноры-поставщики, чья запись ещё держит, — именами в нижнем регистре."""
    hosts = await session.scalars(
        select(func.lower(SupplierDonorModel.host)).where(
            SupplierDonorModel.in_force(datetime.now(UTC))
        )
    )
    return set(hosts.all())


async def acceptance(session: AsyncSession, *, limit: int) -> Acceptance:
    """Приёмка по каждому обойдённому донору (не больше `limit`) и всех вместе.

    Донор — каждый, у кого есть обход, в том числе без рекламодателей: «обошли
    и не нашли никого» — тоже ответ приёмке. Сверху — у кого найдено больше.
    """
    rows = _found().subquery()
    flags = OfferFlags.build()
    per_donor = await session.execute(
        flags.joined(
            select(rows.c.donor, *_counts(rows, flags)).select_from(rows), rows.c.domain_id
        ).group_by(rows.c.donor)
    )
    by_host = {str(line[0]): _tally(line[1:]) for line in per_donor.all()}
    suppliers = await _suppliers(session)
    crawled = (await session.scalars(select(CrawlRunModel.host).distinct())).all()
    hosts = sorted(
        set(crawled) | set(by_host),
        key=lambda host: (-by_host.get(host, Tally()).found, host),
    )
    overall = await session.execute(
        flags.joined(select(*_counts(rows, flags)).select_from(rows), rows.c.domain_id).where(
            func.lower(rows.c.donor).not_in(suppliers) if suppliers else rows.c.donor.is_not(None)
        )
    )
    return Acceptance(
        donors=[
            DonorTally(
                host=host,
                tally=by_host.get(host, Tally()),
                supplier=host.lower() in suppliers,
            )
            for host in hosts[:limit]
        ],
        total=len(hosts),
        overall=_tally(overall.one()),
    )
