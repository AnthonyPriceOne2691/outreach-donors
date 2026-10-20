"""Вердикт прогона по каждому его домену: записать, прочесть, вынести по сохранённому.

**Вердикт — у прогона** (аудит 20.10.2026). Домен, купленный сейчас, прогон судит
замером (`donors/collect.py`); свежий — сам, своей страной и своими порогами, по метрикам
и странам, которые лежат у домена (`donors/rejudge.py`), за ноль юнитов. Оба вердикта
ложатся строкой на пару «прогон + домен» (`run_verdicts`), и по ней прогон кладёт годных
в очередь (`review/queue.py`). До этого очередь брала вердикт домена — последнего замера,
чьей бы страны он ни был.

**Прогон без строки — судится по сохранённому, когда его спрашивают** (`judged`): так
очередь старого прогона кладёт консоль `outreach review-queue`, и так решение человека
узнаёт вердикт прогона, где его приняли. Пороги и правило региона — версии настроек
этого прогона (`runs.settings_id`), страна — его.

**Исключённые гейтом — тоже строкой** (`save_excluded`, резерв 20.10.2026): статус
`excluded` и причина гейта. Без неё резерв прогона не мог бы сказать, что платформу и
крупное СМИ он видел и почему не взял: отчёт хранит только их число. «Не проверен» из-за
нехватки данных — с кодом `gap` (`rejudge.DATA_GAPS`): по нему резерв показывает такие
домены своей группой.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import DonorStatus
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.run import RunModel, RunSettingsModel, RunVerdictModel
from backend.features.donors.collect import DomainResult
from backend.features.donors.geo import GeoRule
from backend.features.donors.rejudge import DATA_GAPS, Stored, rejudge
from backend.features.donors.verdict import Thresholds
from backend.features.runs.exclusions import ExclusionReason, exclusion_words
from backend.features.runs.refusals import kind_of
from backend.features.runs.thresholds import rules_of

#: Сколько знаков причины помещается в строку вердикта.
REASON_LENGTH = 128
#: Строк и значений `IN` в одном запросе: параметров у Postgres не больше 32 767 на запрос.
CHUNK = 1000
#: Статусы вердикта порогов — других строка вердикта прогона вердиктом не несёт.
_STATUSES = tuple(status.value for status in DonorStatus)
#: Статус строки домена, которого гейт снял до Ahrefs: вердикта порогов у него нет.
EXCLUDED = "excluded"
#: Код причины «не проверен» из-за нехватки данных (`rejudge.DATA_GAPS`) — группа резерва.
GAP = "gap"


@dataclass(frozen=True, slots=True)
class RunVerdict:
    """Вердикт домена в прогоне и его причина словами вердикта."""

    status: DonorStatus
    reason: str
    #: Данные куплены этим прогоном; `False` — вердикт по сохранённым, без Ahrefs.
    measured: bool = False

    @property
    def suitable(self) -> bool:
        return self.status is DonorStatus.SUITABLE


@dataclass(frozen=True, slots=True)
class Judging:
    """Чем прогон судит: страна, пороги и правило региона его версии настроек."""

    country: str
    thresholds: Thresholds
    rule: GeoRule


def measured(results: Sequence[DomainResult]) -> dict[str, RunVerdict]:
    """Вердикты пачки замера — как их вынес сбор."""
    return {
        result.host: RunVerdict(result.status, result.reason, measured=True) for result in results
    }


class RunVerdicts:
    """Строки `run_verdicts`: одна на пару «прогон + домен»."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, run: RunModel, verdicts: Mapping[str, RunVerdict]) -> int:
        """Записать вердикты прогона. Повтор той же пары переписывает строку: продолжение
        прогона судит тот же домен теми же порогами. Купленное этим прогоном пересуд не
        перетирает (`_upsert`). Домен без строки в `domains` пропускается: вердикт бывает
        только у домена, который прогон видел в базе."""
        if not verdicts:
            return 0
        ids = await self._domain_ids(list(verdicts))
        return await self._upsert(
            [
                {
                    "run_id": run.id,
                    "domain_id": ids[host],
                    "status": verdict.status.value,
                    "cause": _cause(verdict, run.country),
                    "reason": verdict.reason[:REASON_LENGTH] or None,
                    "measured": verdict.measured,
                }
                for host, verdict in verdicts.items()
                if host in ids
            ]
        )

    async def save_excluded(self, run: RunModel, excluded: Mapping[str, ExclusionReason]) -> int:
        """Исключённые гейтом — строкой прогона с причиной гейта. Домену без строки в
        `domains` она заводится: платформу и СМИ гейт снимает раньше, чем их кто-то видел."""
        if not excluded:
            return 0
        for chunk in _chunks(list(dict.fromkeys(excluded))):
            await self._session.execute(
                insert(DomainModel)
                .values([{"host": host} for host in chunk])
                .on_conflict_do_nothing(index_elements=["host"])
            )
        ids = await self._domain_ids(list(excluded))
        return await self._upsert(
            [
                {
                    "run_id": run.id,
                    "domain_id": ids[host],
                    "status": EXCLUDED,
                    "cause": reason.value,
                    "reason": exclusion_words(reason)[:REASON_LENGTH],
                    "measured": False,
                }
                for host, reason in excluded.items()
                if host in ids
            ]
        )

    async def _upsert(self, payload: list[dict[str, object]]) -> int:
        """Строки — пачками по `CHUNK`: у запроса не больше 32 767 параметров, а у строки их
        шесть — один запрос на прогон в 5 500 доменов падал (ревью 20.10.2026).

        Купленный вердикт пересуд не перетирает: продолжение прогона застаёт купленное
        первой попыткой свежим, и пересуд по сохранённому назвал бы крупный сайт с DR 80,4
        «не проверен» — в колонке DR он 80."""
        table = RunVerdictModel.__table__.c
        for chunk in _chunks(payload):
            statement = insert(RunVerdictModel).values(list(chunk))
            await self._session.execute(
                statement.on_conflict_do_update(
                    constraint="uq_run_verdicts_run_domain",
                    set_={
                        "status": statement.excluded.status,
                        "cause": statement.excluded.cause,
                        "reason": statement.excluded.reason,
                        "measured": statement.excluded.measured,
                        "updated_at": func.now(),
                    },
                    where=or_(statement.excluded.measured, ~table.measured),
                )
            )
        return len(payload)

    async def of(self, run_id: int, hosts: Sequence[str]) -> dict[str, RunVerdict]:
        """Записанные вердикты прогона по этим доменам. Только вердикты порогов: строка
        другого статуса (её пишет не замер и не пересуд) вердиктом не читается."""
        found: dict[str, RunVerdict] = {}
        for chunk in _chunks(list(dict.fromkeys(hosts))):
            rows = await self._session.execute(
                select(
                    DomainModel.host,
                    RunVerdictModel.status,
                    RunVerdictModel.reason,
                    RunVerdictModel.measured,
                )
                .join(RunVerdictModel, RunVerdictModel.domain_id == DomainModel.id)
                .where(RunVerdictModel.run_id == run_id)
                .where(RunVerdictModel.status.in_(_STATUSES))
                .where(DomainModel.host.in_(list(chunk)))
            )
            found.update(
                (host, RunVerdict(DonorStatus(status), reason or "", measured=bool(was)))
                for host, status, reason, was in rows.tuples().all()
            )
        return found

    async def fresh(
        self, run: RunModel, hosts: Sequence[str], judging: Judging
    ) -> dict[str, RunVerdict]:
        """Вердикты свежих доменов прогона. Купленный этим же прогоном — продолжение после
        сбоя застаёт его свежим — остаётся вердиктом замера: пересуд по сохранённому не
        знает точного DR. Остальные — пересудом по сохранённому, с записью."""
        known = {host: one for host, one in (await self.of(run.id, hosts)).items() if one.measured}
        rest = [host for host in dict.fromkeys(hosts) if host not in known]
        return {**known, **(await self.rejudged(run, rest, judging))}

    async def rejudged(
        self, run: RunModel, hosts: Sequence[str], judging: Judging, *, keep: bool = True
    ) -> dict[str, RunVerdict]:
        """Свежие домены — вердиктом этого прогона по сохранённым метрикам и странам.
        Возвращает и, если `keep`, записывает; домен без строки донора не судится —
        судить нечем."""
        found: dict[str, RunVerdict] = {}
        for chunk in _chunks(list(dict.fromkeys(hosts))):
            rows = await self._session.execute(
                select(DomainModel.host, DonorModel)
                .join(DonorModel, DonorModel.domain_id == DomainModel.id)
                .where(DomainModel.host.in_(list(chunk)))
            )
            for host, donor in rows.tuples().all():
                verdict = rejudge(
                    Stored.of(donor), judging.thresholds, judging.country, judging.rule
                )
                found[host] = RunVerdict(verdict.status, verdict.reason)
        if keep:
            await self.save(run, found)
        return found

    async def judged(
        self, run: RunModel, hosts: Sequence[str], *, keep: bool = True
    ) -> dict[str, RunVerdict]:
        """Вердикты прогона по доменам: записанные, а у кого строки нет — по сохранённому,
        порогами версии этого прогона. `keep` — записать вынесенные: очередь старого
        прогона судит его домены целиком, а решение человека только спрашивает."""
        known = await self.of(run.id, hosts)
        missing = [host for host in dict.fromkeys(hosts) if host not in known]
        if missing:
            judging = await judging_of(self._session, run)
            known.update(await self.rejudged(run, missing, judging, keep=keep))
        return known

    async def _domain_ids(self, hosts: Sequence[str]) -> dict[str, int]:
        ids: dict[str, int] = {}
        for chunk in _chunks(list(dict.fromkeys(hosts))):
            rows = await self._session.execute(
                select(DomainModel.host, DomainModel.id).where(DomainModel.host.in_(list(chunk)))
            )
            ids.update(rows.tuples().all())
        return ids


def _chunks[Item](items: Sequence[Item]) -> Iterator[Sequence[Item]]:
    for start in range(0, len(items), CHUNK):
        yield items[start : start + CHUNK]


def _cause(verdict: RunVerdict, country: str) -> str | None:
    """Почему не прошёл — кодом: порог у «не подходит», `gap` у «не проверен» из-за
    нехватки данных (`rejudge.DATA_GAPS`); у остальных — пусто."""
    if verdict.status is DonorStatus.UNSUITABLE:
        return kind_of(verdict.reason, country).value
    if verdict.status is DonorStatus.UNCHECKED and verdict.reason in DATA_GAPS:
        return GAP
    return None


async def judging_of(session: AsyncSession, run: RunModel) -> Judging:
    """Чем судит этот прогон: его страна и версия настроек, с которой он поставлен."""
    rules = rules_of(await session.get_one(RunSettingsModel, run.settings_id))
    return Judging(country=run.country, thresholds=rules.thresholds, rule=rules.geo)
