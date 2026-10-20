"""Резерв: домены прогона, которые не прошли, — с причиной, метриками и местом страны.

Вопрос оператора 20.10.2026: прогон на 122 домена предложил 28, 14 снял гейт, а 80 не
прошли пороги — «куда деваются остальные, можно ли их вынести в отдельную базу, чтобы при
нехватке выбирать из них». Они лежали только на «Донорах» вердиктом «не подходит», очередь
брала одних годных, а ручной ввод донора таким отказывал. Резерв — вердикты прогона
(`run_verdicts`): «не подходит» с причиной, «не проверен» из-за нехватки данных и «кому не
пишем» гейта.

**Взять на рассмотрение — в очередь того прогона, где домен в резерве** («по контексту
прогона»): ключи, по которым он нашёлся, страна и пороги — того прогона, и решение человека
о нём — обычное решение очереди (`candidates.decide`). Строка очереди помнит, кто её взяла
(`run_candidates.taken_by`): у такого кандидата вердикт прогона «не подходит», и очередь
показывает почему. Принятый из резерва — годен словом человека (`accepted.py`). «Взять» —
под замком решения по домену (`domain_lock.lock_domains`): решение и второе «взять» того же
домена ждут его, а не ложатся поверх.

**«Кому не пишем» видно, но не берётся** (решение владельца 11.10.2026, ТЗ): крупный сайт
(DR выше порога версии), платформа, соцсеть, крупное СМИ, справочник, гос. зона — причина
словами вместо кнопки. Тем же правилом — по действующей версии (`exclusions.not_writing`):
домен, перемеренный крупным после прогона, взять тоже нельзя.

**Не резерв** — стоп-лист, поставщик, «писали, не ответил», «не продаёт», отклонённый
человеком: это отношения, а не пороги, и в отчёте прогона они названы своей строкой.
Появилось такое отношение после прогона — домен в резерве стоит, но не берётся: «взять»
перепроверяет гейт (`Exclusions`, ревью 20.10.2026). **Решение по домену одно**: принятый,
отклонённый и ждущий в другой очереди — в резерве стоят, но не берутся, и сказано, где
о нём решают.

**Одна строка на домен — и одно правило у выдачи и счётчиков** (ревью 20.10.2026). Без
фильтра по прогону домен показан строкой последнего прогона, где он в резерве: решение о
нём одно, и десять строк одного домена были бы шумом. Фильтры выбирают из этих строк, а
счётчики пунктов считают их же — число у пункта равно тому, что он покажет.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import Select, false, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from backend.features.core.domain import Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.run import RunCandidateModel, RunModel, RunVerdictModel
from backend.features.donors.table_filters import in_top_countries, top_country_counts
from backend.features.review.candidates import UnknownRunError
from backend.features.review.domain_lock import lock_domains
from backend.features.review.queue import Decision
from backend.features.review.reserve_rules import (
    RELATIONS,
    CountryFit,
    Group,
    Standing,
    fit_of,
    group_named,
    group_of,
    in_reserve,
    refusal,
    said_of,
)
from backend.features.runs.exclusions import Exclusions, not_writing, not_writing_of
from backend.shared.database.ids import storable

#: Сколько строк резерва отдаётся за раз и сколько доменов берут одним нажатием.
PAGE_LIMIT = 100
TAKE_LIMIT = 200

#: Фильтр, чьи счётчики считаются: его самого к выборке не прикладывают.
Facet = Literal["group", "country", "geo"]


@dataclass(frozen=True, slots=True)
class ReserveFilters:
    """Что показать: прогон, страна прогона, группа причины, страна в топ-5 домена."""

    run_id: int | None = None
    country: str | None = None
    group: Group | None = None
    geo: str | None = None
    limit: int = 20
    offset: int = 0


@dataclass(frozen=True, slots=True)
class ReserveRow:
    """Строка резерва: прогон, домен, почему не прошёл, данные замера и можно ли взять."""

    run_id: int
    country: str
    domain_id: int
    host: str
    status: str
    cause: str | None
    reason: str | None
    donor: DonorModel | None
    fit: CountryFit
    #: Строка этого домена в очереди этого прогона: предложен, принят, отклонён.
    queued: str | None
    #: Почему взять нельзя — словами; `None` — можно.
    refusal: str | None

    @property
    def group(self) -> Group:
        return group_named(self.status, self.cause)

    @property
    def takeable(self) -> bool:
        return self.refusal is None

    @property
    def said(self) -> str | None:
        """Почему не прошёл — словами экрана (`said_of`)."""
        return said_of(self.cause, self.reason)


@dataclass(frozen=True, slots=True)
class ReservePage:
    rows: list[ReserveRow]
    total: int
    #: Сколько в каждой группе — под остальными фильтрами: счётчики пунктов фильтра.
    groups: dict[str, int]
    #: Страны прогонов резерва и сколько доменов у каждой — для фильтра страны.
    countries: dict[str, int]
    #: Страна в топ-5 доменов резерва → у скольких — для фильтра «Гео».
    geos: dict[str, int] = field(default_factory=dict)
    #: Прогон фильтра; `None` — резерв всех прогонов.
    run: RunModel | None = None


@dataclass(slots=True)
class TakeReport:
    """Что вышло из «Взять на рассмотрение»: взяты, уже были в очереди, отказано — почему."""

    #: Прогон → взятые в его очередь домены.
    taken: dict[int, list[str]] = field(default_factory=dict)
    already: list[str] = field(default_factory=list)
    refused: dict[str, str] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return sum(len(hosts) for hosts in self.taken.values())


def _pending_elsewhere() -> Any:
    """Прогон, где домен ждёт решения, кроме этого, — самый новый."""
    other = aliased(RunCandidateModel)
    return (
        select(func.max(other.run_id))
        .where(other.domain_id == RunVerdictModel.domain_id)
        .where(other.run_id != RunVerdictModel.run_id)
        .where(other.status == Decision.PENDING.value)
        .scalar_subquery()
    )


def _rows() -> Select[Any]:
    """Строки резерва со всем, что нужно строке экрана и правилу «можно ли взять»."""
    return (
        select(
            RunVerdictModel,
            RunModel.country,
            RunModel.stage,
            DomainModel.host,
            DonorModel,
            RunCandidateModel.status,
            _pending_elsewhere().label("pending_in"),
            func.coalesce(not_writing(), false()).label("off"),
        )
        .join(RunModel, RunModel.id == RunVerdictModel.run_id)
        .join(DomainModel, DomainModel.id == RunVerdictModel.domain_id)
        .outerjoin(DonorModel, DonorModel.domain_id == RunVerdictModel.domain_id)
        .outerjoin(
            RunCandidateModel,
            (RunCandidateModel.run_id == RunVerdictModel.run_id)
            & (RunCandidateModel.domain_id == RunVerdictModel.domain_id),
        )
    )


def _chosen(run_id: int | None) -> Select[Any]:
    """Строки резерва — по одной на домен: последнего прогона, где он в резерве; у резерва
    прогона — все строки этого прогона. Фильтры экрана выбирают из них (`_narrowed`)."""
    base = select(RunVerdictModel.id).where(in_reserve())
    if run_id is not None:
        return base.where(RunVerdictModel.run_id == (run_id if storable(run_id) else 0))
    return base.distinct(RunVerdictModel.domain_id).order_by(
        RunVerdictModel.domain_id, RunVerdictModel.run_id.desc()
    )


def _narrowed(statement: Select[Any], filters: ReserveFilters, skip: Facet | None = None) -> Any:
    """Фильтры экрана поверх выбранных строк (`_chosen`); `skip` — фильтр, чьи пункты
    считаются. У выборки — строка вердикта и её прогон."""
    statement = statement.where(RunVerdictModel.id.in_(_chosen(filters.run_id)))
    if filters.country and skip != "country":
        statement = statement.where(RunModel.country == filters.country.lower())
    if filters.geo and skip != "geo":
        # Страна в топ-5 — тем же правилом, что фильтр «Гео» доноров: верхняя или в разбивке.
        statement = statement.where(
            RunVerdictModel.domain_id.in_(
                select(DonorModel.domain_id).where(in_top_countries(filters.geo))
            )
        )
    if filters.group is not None and skip != "group":
        statement = statement.where(group_of() == filters.group.value)
    return statement


def _verdicts() -> Select[Any]:
    """Строки вердикта с их прогоном — основа счёта."""
    return select(RunVerdictModel.id).join(RunModel, RunModel.id == RunVerdictModel.run_id)


class Reserve:
    """Резерв прогонов: показать и взять на рассмотрение."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def page(self, filters: ReserveFilters) -> ReservePage:
        run = await self._run(filters.run_id)
        statement = (
            _narrowed(_rows(), filters)
            .order_by(
                RunVerdictModel.run_id.desc(),
                DonorModel.dr.desc().nullslast(),
                DomainModel.host,
            )
            .limit(min(filters.limit, PAGE_LIMIT))
            .offset(max(filters.offset, 0))
        )
        rows = await self._rows_of((await self._session.execute(statement)).all())
        listed = _narrowed(_verdicts(), filters).subquery()
        total = await self._session.scalar(select(func.count()).select_from(listed))
        return ReservePage(
            rows=rows,
            total=int(total or 0),
            groups=await self._groups(filters),
            countries=await self._countries(filters),
            geos=await self._geos(filters),
            run=run,
        )

    async def take(
        self, items: Sequence[tuple[int, int]], *, by: str, now: datetime | None = None
    ) -> TakeReport:
        """Положить домены в очереди их прогонов — пара «прогон + домен». Уже лежащий в
        очереди этого прогона — «уже», а не ошибка: повтор нажатия ничего не задваивает.
        Отказ — словами, по тому же правилу, что строка экрана. Замок решения по доменам —
        до всех проверок: читается то, что решение или второе «взять» уже записали."""
        report = TakeReport()
        pairs = [(run_id, domain_id) for run_id, domain_id in items if storable(run_id)]
        pairs = pairs[:TAKE_LIMIT]
        if not pairs:
            return report
        await lock_domains(self._session, [domain_id for _, domain_id in pairs])
        found = await self._session.execute(
            _rows()
            .where(in_reserve())
            .where(tuple_(RunVerdictModel.run_id, RunVerdictModel.domain_id).in_(pairs))
            .execution_options(populate_existing=True)
        )
        rows = {(row.run_id, row.domain_id): row for row in await self._rows_of(found.all())}
        moment = now or datetime.now(UTC)
        for run_id, domain_id in pairs:
            row = rows.get((run_id, domain_id))
            if row is None:
                report.refused[f"№{domain_id}"] = (
                    f"в резерве прогона №{run_id} такого домена нет — обновите экран"
                )
            elif row.queued is not None:
                report.already.append(row.host)
            elif row.refusal is not None:
                report.refused[row.host] = row.refusal
            elif await self._lay(row, by=by, moment=moment):
                report.taken.setdefault(run_id, []).append(row.host)
            else:
                report.already.append(row.host)
        await self._session.flush()
        return report

    async def _lay(self, row: ReserveRow, *, by: str, moment: datetime) -> bool:
        """Строка очереди — «предложен», взятый человеком. Пара занята — не взята."""
        inserted = await self._session.execute(
            insert(RunCandidateModel)
            .values(
                run_id=row.run_id,
                domain_id=row.domain_id,
                status=Decision.PENDING.value,
                carried=False,
                taken_by=by,
                taken_at=moment,
            )
            .on_conflict_do_nothing(constraint="uq_run_candidates_run_domain")
            .returning(RunCandidateModel.id)
        )
        return inserted.scalar_one_or_none() is not None

    async def _run(self, run_id: int | None) -> RunModel | None:
        if run_id is None:
            return None
        run = await self._session.get(RunModel, run_id) if storable(run_id) else None
        if run is None:
            raise UnknownRunError(f"Прогона №{run_id} нет")
        return run

    async def _rows_of(self, found: Iterable[Any]) -> list[ReserveRow]:
        """Строки выборки — строками резерва; почему не взять — словами (`refusal`)."""
        raw = list(found)
        off = [row[0].domain_id for row in raw if row.off]
        words = await not_writing_of(self._session, off) if off else {}
        relations = await self._relations(raw)
        return [
            ReserveRow(
                run_id=verdict.run_id,
                country=country,
                domain_id=verdict.domain_id,
                host=host,
                status=verdict.status,
                cause=verdict.cause,
                reason=verdict.reason,
                donor=donor,
                fit=fit_of(donor, country),
                queued=queued,
                refusal=refusal(
                    verdict,
                    donor,
                    Standing(queued, pending_in, words.get(host), relations.get((stage, host))),
                ),
            )
            for verdict, country, stage, host, donor, queued, pending_in, _ in raw
        ]

    async def _relations(self, raw: Sequence[Any]) -> dict[tuple[Stage, str], str]:
        """Отношения, появившиеся после прогона (`RELATIONS`): стоп-лист, поставщик,
        молчание, «не продаёт» — по этапу прогона, как их судит гейт."""
        by_stage: dict[Stage, list[str]] = {}
        for row in raw:
            by_stage.setdefault(row.stage, []).append(row.host)
        found: dict[tuple[Stage, str], str] = {}
        gate = Exclusions(self._session)
        for stage, hosts in by_stage.items():
            for host, reason in (await gate.excluded_hosts(hosts, stage=stage)).items():
                if reason in RELATIONS:
                    found[(stage, host)] = reason.caption
        return found

    async def _groups(self, filters: ReserveFilters) -> dict[str, int]:
        # Группа — столбцом подзапроса: выражение с параметрами в GROUP BY база не узнаёт
        # тем же, что в списке столбцов.
        labeled = _narrowed(
            select(group_of().label("grp")).join(RunModel, RunModel.id == RunVerdictModel.run_id),
            filters,
            "group",
        ).subquery()
        rows = await self._session.execute(
            select(labeled.c.grp, func.count()).group_by(labeled.c.grp)
        )
        counted = dict.fromkeys((group.value for group in Group), 0)
        counted.update({str(group): int(count) for group, count in rows.tuples().all()})
        return counted

    async def _geos(self, filters: ReserveFilters) -> dict[str, int]:
        """Страна → у скольких доменов резерва она в топ-5 (верхняя или в разбивке), под
        остальными фильтрами — тем же правилом, что фильтр «Гео»."""
        base = _narrowed(
            select(DonorModel.domain_id)
            .join(RunVerdictModel, RunVerdictModel.domain_id == DonorModel.domain_id)
            .join(RunModel, RunModel.id == RunVerdictModel.run_id),
            filters,
            "geo",
        )
        rows = await self._session.execute(top_country_counts(base))
        return {str(code).lower(): int(count) for code, count in rows.tuples().all()}

    async def _countries(self, filters: ReserveFilters) -> dict[str, int]:
        rows = await self._session.execute(
            _narrowed(
                select(RunModel.country, func.count()).join(
                    RunVerdictModel, RunVerdictModel.run_id == RunModel.id
                ),
                filters,
                "country",
            )
            .group_by(RunModel.country)
            .order_by(func.count().desc(), RunModel.country)
        )
        return {str(country): int(count) for country, count in rows.tuples().all()}
