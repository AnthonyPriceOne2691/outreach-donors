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

**Решение принимается один раз.** Домен, уже решённый в прошлом прогоне,
приходит в новый с тем же решением и пометкой «перенесено»: смотреть
одно и то же дважды — это работа, которую человек бросит. Отклонённый
вообще не доходит до Ahrefs (`runs.exclusions`), принятый проходит
очередь сразу.

**Решение можно снять.** Отмена возвращает кандидата в «предложен»,
а донору — предыдущее решение по другим прогонам, если оно было.
В соседней системе отказ вечный и без отмены, и это её известная беда.

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

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import DonorStatus
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.run import RunCandidateModel, RunModel
from backend.features.review.ordering import Tier, sells_placement, shelf, tier, tier_order

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


@dataclass(frozen=True, slots=True)
class ReviewPage:
    run: RunModel
    rows: list[CandidateRow]
    #: Сколько в каждом статусе — для вкладок.
    counts: dict[str, int]
    #: Сколько «предложенных» скрыто под фильтром сомнительных.
    hidden: int


#: Автоприём по совету судьи — только когда совет «принять» верен так часто
#: на такой выборке. Требование Anthony 23.09.2026: «если судья в 95% случаев
#: будет прав — открыть на автоматическую приёмку». Выборка меньше — число
#: случайно, и по нему ничего не включают.
AUTO_ACCEPT_PRECISION = 0.95
AUTO_ACCEPT_MIN_DECISIONS = 200


@dataclass(frozen=True, slots=True)
class Agreement:
    """Как совет судьи сходится с решением человека.

    Считается только там, где человек решал сам: перенесённые решения —
    копия, а не второе мнение. «Посмотри» судьи — просьба, а не совет,
    и в точность не входит, но считается отдельно: доля «посмотри» —
    это сколько работы судья оставляет человеку.
    """

    advised: int = 0
    agreed: int = 0

    @property
    def precision(self) -> float | None:
        return self.agreed / self.advised if self.advised else None


@dataclass(frozen=True, slots=True)
class JudgeAccuracy:
    """Судья против человека: по совету, по слою, по типу сайта."""

    decided: int
    #: Совет «принять» / «отказ» → сколько раз человек согласился.
    by_advice: dict[str, Agreement]
    #: Слой (`rule` / `model` / `arbiter`) → совет → согласие.
    by_layer: dict[str, dict[str, Agreement]]
    #: Тип сайта по судье → совет → согласие. Здесь видны систематические
    #: ошибки: «модель режет продавцов размещения» — строка этой таблицы.
    by_intent: dict[str, dict[str, Agreement]]
    #: Решённых человеком без вердикта судьи — пропуск, а не мелочь.
    unjudged: int
    #: Решённых, где судья сказал «посмотри».
    asked_to_review: int

    @property
    def auto_accept_ready(self) -> bool:
        accept = self.by_advice.get("accept", Agreement())
        return (
            accept.advised >= AUTO_ACCEPT_MIN_DECISIONS
            and (accept.precision or 0.0) >= AUTO_ACCEPT_PRECISION
        )


def _agreement_key(recommendation: str | None, status: str) -> tuple[str, bool] | None:
    if recommendation == "accept":
        return "accept", status == Decision.ACCEPTED.value
    if recommendation == "reject":
        return "reject", status == Decision.REJECTED.value
    return None


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


class RunReview:
    """Очередь прогона: наполнить, показать, решить."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def queue_run(self, run_id: int, hosts: Sequence[str]) -> QueueReport:
        """Положить на рассмотрение годных по порогам доменов прогона.

        Повтор безопасен: пара «прогон + домен» кладётся один раз.
        Решённый раньше домен приходит с тем же решением.
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
            decided = review in (Decision.ACCEPTED, Decision.REJECTED)
            inserted = await self._session.execute(
                insert(RunCandidateModel)
                .values(
                    run_id=run_id,
                    domain_id=domain_id,
                    status=review if decided else Decision.PENDING.value,
                    carried=decided,
                )
                .on_conflict_do_nothing(constraint="uq_run_candidates_run_domain")
                .returning(RunCandidateModel.id)
            )
            if inserted.scalar_one_or_none() is None:
                continue
            if decided:
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
        run = await self._session.get(RunModel, run_id)
        if run is None:
            raise UnknownRunError(f"Прогона №{run_id} нет")
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
            )
            for candidate, domain, donor, row_tier, sells in result.all()
        ]
        return ReviewPage(
            run=run,
            rows=rows,
            counts=await self._counts(run_id),
            hidden=await self._hidden(run_id),
        )

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
        """Принять, отклонить или вернуть в «предложен».

        Решение ложится и на кандидата (история прогона), и на донора
        (последнее решение по домену): контакты, письма и гейт следующего
        прогона смотрят на донора.
        """
        if not candidate_ids:
            return DecideReport(changed=0, accepted_domains=[])
        moment = now or datetime.now(UTC)
        candidates = await self._of_run(run_id, candidate_ids)
        for candidate in candidates:
            _apply(candidate, decision, by=by, note=note, moment=moment)
        await self._session.flush()
        domains = sorted({c.domain_id for c in candidates})
        for domain_id in domains:
            await self._settle_donor(domain_id)
        logger.info(
            "рассмотрение: прогон %s, %s кандидатов → %s (%s)",
            run_id,
            len(candidates),
            decision.value,
            by,
        )
        return DecideReport(
            changed=len(candidates),
            accepted_domains=domains if decision is Decision.ACCEPTED else [],
        )

    async def _of_run(self, run_id: int, candidate_ids: Sequence[int]) -> list[RunCandidateModel]:
        """Кандидаты — и только этого прогона: чужой номер значит, что экран
        показывает устаревший список."""
        found = await self._session.execute(
            select(RunCandidateModel).where(RunCandidateModel.id.in_(candidate_ids))
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

    async def accuracy(self, run_id: int | None = None) -> JudgeAccuracy:
        """Судья против человека — по всем прогонам или по одному."""
        statement = (
            select(
                RunCandidateModel.status,
                DomainModel.judge_recommendation,
                DomainModel.judge_decided_by,
                DomainModel.site_intent,
            )
            .join(DomainModel, DomainModel.id == RunCandidateModel.domain_id)
            .where(RunCandidateModel.status != Decision.PENDING.value)
            .where(RunCandidateModel.carried.is_(False))
        )
        if run_id is not None:
            statement = statement.where(RunCandidateModel.run_id == run_id)
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
        """Последнее решение человека по домену — из всех его прогонов.

        Перенесённые строки решением не считаются: они копия, и после
        отмены оригинала копия не должна его воскрешать.
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
