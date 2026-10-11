"""Очередь прогона на рассмотрение и решения человека.

**Прогон кончается очередью, а не базой.** Пороги отвечают «годен ли
по цифрам», человек — «берём ли». Цифры у бренда отличные по построению:
прогон 23.09.2026 признал годными microsoft.com, x.com и reddit.com, и
первые письма пробной сборки ушли бы им на `copyright@` и `weee@`.
Поэтому годный по порогам домен становится кандидатом прогона со
статусом «предложен», а контакты и письма получает только принятый.

**Судья сортирует, а не решает.** Очередь делится на ярусы по
действующему совету о сайте (`selection.site_advice`: ответ донора
сильнее решения человека о типе, оно сильнее судьи): советует принять —
сверху, «посмотри» или совета нет — в середине, советует отказ — внизу
и скрыт под фильтром со счётчиком. Совсем не выкидывается: судья
ошибается (23.09 он отрезал airanklab.com, который прямо продаёт
платные публикации), и отказ модели без глаз человека стоил бы донора.

**Решение по домену одно** (решение владельца 11.10.2026). Человек решает
о домене, а не о прогоне: строка прогона, где решали, — само решение, строки
того же домена в остальных прогонах — его копия с пометкой «перенесено»,
и новый прогон кладёт решённый домен такой же копией: смотреть одно и то же
дважды — работа, которую человек бросит. До 11.10 решение ложилось только
на свою строку: домен ждал во втором прогоне и после решения в первом,
считался в его «Рассмотреть», и решение там переворачивало первое.
Отклонённый вообще не доходит до Ahrefs (`runs.exclusions`), принятый
проходит очередь сразу.

**Решение можно снять.** Отмена возвращает домен в «предложен» во всех
прогонах: решения о нём больше нет нигде. В соседней системе отказ вечный
и без отмены, и это её известная беда.

**Внутри яруса первыми — те, кто сам продаёт размещение.** Для
гест-постинга это главный признак донора, и смотреть его надо раньше
всего остального: сайт сам сказал «продаём», человек или судья поняли,
что он продаёт статьи у себя, или он зовёт авторов и рекламодателей
со страницы и из меню главной (`donors.doors`). Признак считается одной
формулой (`sells_placement`) — по ней и сортировка, и подпись в строке.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import ColumnElement, false, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import DonorStatus
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.run import RunCandidateModel, RunModel
from backend.features.review.accuracy import Agreement, JudgeAccuracy
from backend.features.review.ordering import (
    SellsBy,
    Tier,
    sells_placement,
    sells_source,
    shelf,
    tier,
    tier_order,
)
from backend.shared.database.ids import storable

logger = logging.getLogger(__name__)


class Decision(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class UnknownRunError(ValueError):
    """Прогона с таким номером нет."""


class NotInRunError(ValueError):
    """Кандидаты не из этого прогона. Сообщение называет номера."""


@dataclass(frozen=True, slots=True)
class QueueReport:
    """Что прогон положил на рассмотрение."""

    pending: int = 0
    #: Решено в прошлых прогонах и перенесено: смотреть заново не нужно.
    carried: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DecideReport:
    #: Строк этого прогона: копии в остальных прогонах сюда не входят.
    changed: int
    #: Домены, принятые этим решением, — им пора искать контакт.
    accepted_domains: list[int]


@dataclass(frozen=True, slots=True)
class CandidateRow:
    candidate: RunCandidateModel
    domain: DomainModel
    donor: DonorModel
    tier: Tier
    #: По каким ключам прогона нашёлся домен.
    found_by: list[str]
    #: Почему сайт стоит первым в ярусе: он продаёт размещение у себя.
    #: Пусто — такого признака нет.
    sells: str | None = None
    #: Чей голос это сказал: экран показывает признак в колонке источника.
    sells_by: SellsBy | None = None


@dataclass(frozen=True, slots=True)
class ReviewPage:
    run: RunModel
    rows: list[CandidateRow]
    #: Сколько в каждом статусе — для вкладок.
    counts: dict[str, int]
    #: Сколько «предложенных» скрыто под фильтром сомнительных.
    hidden: int


def _of_run(run_id: int) -> ColumnElement[bool]:
    """Строки одного прогона. Номер, которого не бывает в столбце, — пустой
    счёт, а не переполнение базы."""
    return RunCandidateModel.run_id == run_id if storable(run_id) else false()


def _voice(value: str | None) -> SellsBy | None:
    """Источник признака «продаёт» из строки выборки."""
    return None if value is None else SellsBy(value)


#: Решение в очереди → совет, сравнимый с советом судьи: принял — «площадка»,
#: отклонил — «не площадка». Одно правило у плиток рассмотрения и колонки
#: истории прогонов (`runs/browse.py`, проверка прода 10.10.2026).
DECISION_ADVICE: dict[str, str] = {
    Decision.ACCEPTED.value: "accept",
    Decision.REJECTED.value: "reject",
}


def _agreement_key(recommendation: str | None, status: str) -> tuple[str, bool] | None:
    if recommendation not in ("accept", "reject"):
        return None
    return recommendation, DECISION_ADVICE.get(status) == recommendation


def _bump(table: dict[str, Agreement], advice: str, agreed: bool) -> None:
    was = table.get(advice, Agreement())
    table[advice] = Agreement(was.advised + 1, was.agreed + int(agreed))


def _apply(
    candidate: RunCandidateModel,
    decision: Decision,
    *,
    by: str,
    note: str | None,
    moment: datetime,
) -> None:
    """Решение — в строку кандидата. Отмена стирает и автора: «предложен»
    ничьим решением не является."""
    candidate.status = decision.value
    candidate.carried = False
    if decision is Decision.PENDING:
        candidate.decided_by = candidate.decided_at = candidate.note = None
        return
    candidate.decided_by, candidate.decided_at, candidate.note = by, moment, note


def _copy_of(review: str | None) -> dict[str, Any]:
    """Строка домена в прогоне, где о нём не решали: копия решения с пометкой
    «перенесено» — или «предложен», если решения нет. Автора, времени и записки
    у копии нет: они у самого решения и у донора. Одна на очередь и на решение
    в другом прогоне — копии из двух мест не расходятся."""
    decided = review in (Decision.ACCEPTED, Decision.REJECTED)
    return {
        "status": review if decided else Decision.PENDING.value,
        "carried": decided,
        "decided_by": None,
        "decided_at": None,
        "note": None,
    }


class RunReview:
    """Очередь прогона: наполнить, показать, решить."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def queue_run(self, run_id: int, hosts: Sequence[str]) -> QueueReport:
        """Положить на рассмотрение годных по порогам доменов прогона.

        Повтор безопасен: пара «прогон + домен» кладётся один раз.
        Решённый домен приходит копией решения (`_copy_of`) — той же, что
        кладёт решение в другом прогоне (`decide`): решение по домену одно.
        """
        if not hosts:
            return QueueReport()
        rows = await self._session.execute(
            select(DonorModel.domain_id, DonorModel.review)
            .join(DomainModel, DomainModel.id == DonorModel.domain_id)
            .where(DomainModel.host.in_(hosts))
            .where(DonorModel.status == DonorStatus.SUITABLE)
        )
        pending = 0
        carried: dict[str, int] = {}
        for domain_id, review in rows.all():
            values = _copy_of(review)
            inserted = await self._session.execute(
                insert(RunCandidateModel)
                .values(run_id=run_id, domain_id=domain_id, **values)
                .on_conflict_do_nothing(constraint="uq_run_candidates_run_domain")
                .returning(RunCandidateModel.id)
            )
            if inserted.scalar_one_or_none() is None:
                continue
            if values["carried"]:
                carried[review] = carried.get(review, 0) + 1
            else:
                pending += 1
        await self._session.flush()
        return QueueReport(pending=pending, carried=carried)

    async def pending_hosts(self, run_id: int) -> list[str]:
        """Домены прогона, ждущие решения человека."""
        rows = await self._session.execute(
            select(DomainModel.host)
            .join(RunCandidateModel, RunCandidateModel.domain_id == DomainModel.id)
            .where(RunCandidateModel.run_id == run_id)
            .where(RunCandidateModel.status == Decision.PENDING.value)
            .order_by(DomainModel.host)
        )
        return list(rows.scalars().all())

    async def page(
        self, run_id: int, *, status: Decision, show_doubtful: bool = False
    ) -> ReviewPage:
        run = await self._run(run_id)
        statement = self._rows(run_id).where(RunCandidateModel.status == status.value)
        if status is Decision.PENDING and not show_doubtful:
            statement = statement.where(tier() != Tier.DOUBTFUL.value)
        result = await self._session.execute(
            statement.order_by(
                tier_order(),
                shelf(),
                # Внутри яруса и полки — сначала продающие размещение.
                sells_placement().is_(None),
                DonorModel.dr.desc().nullslast(),
                DomainModel.host,
            )
        )
        found_by = (run.candidates or {}).get("found_by") or {}
        rows = [
            CandidateRow(
                candidate=candidate,
                domain=domain,
                donor=donor,
                tier=Tier(row_tier),
                found_by=list(found_by.get(domain.host, [])),
                sells=sells,
                sells_by=_voice(sells_by),
            )
            for candidate, domain, donor, row_tier, sells, sells_by in result.all()
        ]
        return ReviewPage(
            run=run,
            rows=rows,
            counts=await self._counts(run_id),
            hidden=await self._hidden(run_id),
        )

    async def _run(self, run_id: int) -> RunModel:
        """Прогон по номеру — или отказ. Номер, которого не бывает в столбце,
        — тот же «нет такого», а не переполнение базы."""
        run = await self._session.get(RunModel, run_id) if storable(run_id) else None
        if run is None:
            raise UnknownRunError(f"Прогона №{run_id} нет")
        return run

    async def decide(
        self,
        run_id: int,
        candidate_ids: Sequence[int],
        decision: Decision,
        *,
        by: str,
        note: str | None = None,
        now: datetime | None = None,
    ) -> DecideReport:
        """Принять, отклонить или вернуть в «предложен» — домен целиком.

        Решение по домену одно: строка этого прогона — само решение (кто,
        когда, записка), строки домена в остальных прогонах — его копия,
        какими бы они ни были, донор — то же решение: контакты, письма и гейт
        следующего прогона смотрят на донора. «Предложен» — решения нет нигде.
        """
        if not candidate_ids:
            return DecideReport(changed=0, accepted_domains=[])
        moment = now or datetime.now(UTC)
        candidates = await self._of_run(run_id, candidate_ids)
        for candidate in candidates:
            _apply(candidate, decision, by=by, note=note, moment=moment)
        await self._session.flush()
        domains = sorted({c.domain_id for c in candidates})
        copies = await self._copy_elsewhere(run_id, domains, decision)
        for domain_id in domains:
            await self._settle_donor(domain_id)
        logger.info(
            "рассмотрение: прогон %s, %s кандидатов → %s (%s), строк в других прогонах: %s",
            run_id,
            len(candidates),
            decision.value,
            by,
            copies,
        )
        return DecideReport(
            changed=len(candidates),
            accepted_domains=domains if decision is Decision.ACCEPTED else [],
        )

    async def _of_run(self, run_id: int, candidate_ids: Sequence[int]) -> list[RunCandidateModel]:
        """Кандидаты — и только этого прогона: чужой номер значит, что экран
        показывает устаревший список."""
        # Номер за пределами столбца кандидатом быть не может — он «чужой»,
        # как и любой другой несуществующий, а не повод для пятисотки.
        found = await self._session.execute(
            select(RunCandidateModel).where(
                RunCandidateModel.id.in_([one for one in candidate_ids if storable(one)])
            )
        )
        candidates = list(found.scalars().all())
        ours = {c.id for c in candidates if c.run_id == run_id}
        foreign = sorted(set(candidate_ids) - ours)
        if foreign:
            raise NotInRunError(
                f"Кандидатов {', '.join(map(str, foreign))} в прогоне №{run_id} нет — "
                "обновить экран: список мог измениться"
            )
        return candidates

    async def _copy_elsewhere(self, run_id: int, domains: Sequence[int], decision: Decision) -> int:
        """Строки тех же доменов в остальных прогонах — копией решения, какими бы
        они ни были: ждущими, решёнными там раньше или перенесёнными. Отмена —
        «предложен» и там. Возвращает, сколько строк переписано."""
        copied = await self._session.execute(
            update(RunCandidateModel)
            .where(RunCandidateModel.domain_id.in_(domains))
            .where(RunCandidateModel.run_id != run_id)
            .values(**_copy_of(decision.value))
            .returning(RunCandidateModel.id)
        )
        return len(copied.all())

    async def accuracy(self, run_id: int | None = None) -> JudgeAccuracy:
        """Судья против человека — по всем прогонам или по одному.

        По всем — без перенесённых: одно решение считается один раз. В одном
        прогоне перенесённое — решение о его кандидате, оно стоит во вкладке:
        плитки сходятся с вкладками и с колонкой истории (проверка прода 10.10.2026).
        """
        statement = (
            select(
                RunCandidateModel.status,
                DomainModel.judge_recommendation,
                DomainModel.judge_decided_by,
                DomainModel.site_intent,
            )
            .join(DomainModel, DomainModel.id == RunCandidateModel.domain_id)
            .where(RunCandidateModel.status != Decision.PENDING.value)
        )
        statement = statement.where(
            RunCandidateModel.carried.is_(False) if run_id is None else _of_run(run_id)
        )
        by_advice: dict[str, Agreement] = {}
        by_layer: dict[str, dict[str, Agreement]] = {}
        by_intent: dict[str, dict[str, Agreement]] = {}
        decided = unjudged = asked = 0
        for status, recommendation, layer, intent in (await self._session.execute(statement)).all():
            decided += 1
            if recommendation is None:
                unjudged += 1
                continue
            key = _agreement_key(recommendation, status)
            if key is None:
                asked += 1
                continue
            advice, agreed = key
            _bump(by_advice, advice, agreed)
            _bump(by_layer.setdefault(layer or "—", {}), advice, agreed)
            _bump(by_intent.setdefault(intent or "—", {}), advice, agreed)
        return JudgeAccuracy(
            decided=decided,
            by_advice=by_advice,
            by_layer=by_layer,
            by_intent=by_intent,
            unjudged=unjudged,
            asked_to_review=asked,
        )

    async def _settle_donor(self, domain_id: int) -> None:
        """Решение человека по домену — донору, из всех его прогонов.

        Решение — только строка, где решали: перенесённая — копия, и после
        отмены решения копия не должна его воскрешать. После `decide` такая
        строка у домена одна; самая поздняя из нескольких берётся у доменов,
        решённых до 11.10.2026, когда каждый прогон решал о домене сам.
        """
        latest = await self._session.execute(
            select(
                RunCandidateModel.status,
                RunCandidateModel.decided_at,
                RunCandidateModel.decided_by,
            )
            .where(RunCandidateModel.domain_id == domain_id)
            .where(RunCandidateModel.status != Decision.PENDING.value)
            .where(RunCandidateModel.carried.is_(False))
            .order_by(RunCandidateModel.decided_at.desc().nullslast())
            .limit(1)
        )
        row = latest.first()
        await self._session.execute(
            update(DonorModel)
            .where(DonorModel.domain_id == domain_id)
            .values(
                review=row.status if row else None,
                review_at=row.decided_at if row else None,
                review_by=row.decided_by if row else None,
            )
        )

    def _rows(self, run_id: int) -> Any:
        return (
            select(
                RunCandidateModel,
                DomainModel,
                DonorModel,
                tier().label("tier"),
                sells_placement().label("sells"),
                sells_source().label("sells_by"),
            )
            .join(DomainModel, DomainModel.id == RunCandidateModel.domain_id)
            .join(DonorModel, DonorModel.domain_id == RunCandidateModel.domain_id)
            .where(RunCandidateModel.run_id == run_id)
        )

    async def _counts(self, run_id: int) -> dict[str, int]:
        rows = await self._session.execute(
            select(RunCandidateModel.status, func.count())
            .where(RunCandidateModel.run_id == run_id)
            .group_by(RunCandidateModel.status)
        )
        counts = {decision.value: 0 for decision in Decision}
        counts.update({status: int(count) for status, count in rows.all()})
        return counts

    async def _hidden(self, run_id: int) -> int:
        rows = await self._session.execute(
            select(func.count())
            .select_from(RunCandidateModel)
            .join(DomainModel, DomainModel.id == RunCandidateModel.domain_id)
            .join(DonorModel, DonorModel.domain_id == RunCandidateModel.domain_id)
            .where(RunCandidateModel.run_id == run_id)
            .where(RunCandidateModel.status == Decision.PENDING.value)
            .where(tier() == Tier.DOUBTFUL.value)
        )
        return int(rows.scalar_one())
