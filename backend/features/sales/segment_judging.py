"""Проход судьи сегмента по кандидатам прогона: кэш, правила, главная, модель, режим, учёт.

**Кого судим** (`waiting_judge`): кандидатов прогона, которых ещё не судили, и тех, за кого
модель не ответила, пока человек их не решил: отказ модели — не вердикт, и следующий
проход судит домен снова. Пересуд прогона после правки описания (`segment_rejudge.py`) идёт
тем же окном, мимо кэша и по тексту, который видел прошлый суд.

**Кэш — таблица вердиктов**: свежий вердикт того же домена, гипотезы, версии промпта и
описания сегмента не пересуживается и токенов не стоит. Вердикт правила и модели ложится
туда же; ответа модели нет — строки нет. «На проверку» без цитаты (модель её не дала,
сочинила, проверка её не нашла, ответ не разобран) — тоже не вердикт, а несработавшая
проверка: в кэш не ложится, а такие строки, записанные раньше, кэш не отдаёт
(`segment_judge.proven_again`) — так ложные «цитаты нет» прежней сверки не переживают её
правку, без миграции и без вызова модели: модель спросят снова.

**Скользящим окном** (`segment_window.py`, `SALES_SEGMENT_JUDGE_CONCURRENCY`): до N сайтов
разом, чекпоинт — каждые `BATCH` вердиктов, потолок модели — после каждого чекпоинта; выбран —
проход останавливается словами, неосуждённые ждут следующего прохода. Полоса ложится, пока
кандидат ещё ждёт судью (`waiting_judge`): человек, решивший сайт «на проверку» посреди
прохода, решил его — судья за ним не переписывает.

**Главная — только тем, кого судит модель**: «кому не пишем» по имени (`segment_judge.by_host`
— общее правило `runs/exclusions.by_name`) режется до неё, такой сайт не открывается вовсе; кэш
и выключенный судья главной не просят.

**Стоп-лист — до всего** (`stoplisted`): домен в стоп-листе продаж или под строкой общего
стоп-листа, которая держит продажи, отсекается правилом до кэша, главной и модели — при любом
режиме судьи. Иначе он оплатил бы модель и платную ступень поиска адреса и отсеялся бы только
очисткой лидов. Правило то же, что у очистки (`cleaning.py`): строка продаж на домен и
`suppressions.holds_sales`. В таблицу вердиктов такой отсев не пишется: строку стоп-листа
снимают, а вердикт сайта жил бы полгода.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config.judge import JudgeMode
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.sales.models import SalesStoplistModel
from backend.features.sales.segment_judge import SegmentClient
from backend.features.sales.segment_models import Decider, Lane, SalesSegmentCandidateModel
from backend.features.sales.segment_window import Ask, JudgeTally, Landing, Site, judge_sites, land
from backend.features.sales.suppressions import holds_sales

#: Что проход отдаёт наружу. Итог прохода (`JudgeTally`) живёт в окне (`segment_window.py`),
#: а прогон (`segment_run`) берёт его по имени прохода — список держит это имя явным.
__all__ = [
    "STOPLIST_OWN",
    "STOPLIST_SHARED",
    "JudgeTally",
    "Waiting",
    "cut",
    "judge_run",
    "stoplisted",
    "waiting_judge",
]

#: Отсечён стоп-листом до судьи — словами на экране прогона.
STOPLIST_OWN = "домен в стоп-листе продаж — отсечён до судьи, адрес не ищем"
STOPLIST_SHARED = "домен в общем стоп-листе — отсечён до судьи, адрес не ищем"

Candidate = SalesSegmentCandidateModel


def waiting_judge() -> ColumnElement[bool]:
    """Кого судить: не судили — или модель не ответила, а человек ещё не решил."""
    return or_(
        Candidate.lane == Lane.PENDING,
        and_(Candidate.unanswered.is_(True), Candidate.decided_by.is_distinct_from(Decider.HUMAN)),
    )


@dataclass(slots=True)
class Waiting:
    """Полосы прохода прогона: ложатся, пока кандидат ждёт судью (`waiting_judge`)."""

    taken: list[tuple[Site, Landing]] = field(default_factory=list)

    def take(self, site: Site, found: Landing) -> None:
        self.taken.append((site, found))

    async def flush(self, session: AsyncSession, moment: datetime) -> None:
        for site, found in self.taken:
            await land(session, site.candidate_id, found, moment, guard=waiting_judge())
        self.taken.clear()


async def _pending(session: AsyncSession, run_id: int) -> list[Site]:
    rows = await session.execute(
        select(Candidate, DomainModel.host)
        .join(DomainModel, DomainModel.id == Candidate.domain_id)
        .where(Candidate.run_id == run_id, waiting_judge())
        .order_by(Candidate.id)
    )
    return [Site.of(candidate, host) for candidate, host in rows.tuples().all()]


async def stoplisted(
    session: AsyncSession, domain_ids: Sequence[int], moment: datetime
) -> dict[int, str]:
    """Домены, которым продажи не пишут, — домен → почему словами. Строки на адрес ждут очистки
    лидов: адреса у кандидата ещё нет."""
    if not domain_ids:
        return {}
    ids = list(domain_ids)
    shared = await session.scalars(
        select(SuppressionModel.domain_id).where(
            SuppressionModel.domain_id.in_(ids), holds_sales(moment)
        )
    )
    own = await session.scalars(
        select(DomainModel.id)
        .join(SalesStoplistModel, SalesStoplistModel.host == DomainModel.host)
        .where(DomainModel.id.in_(ids))
    )
    found = {domain_id: STOPLIST_SHARED for domain_id in shared.all() if domain_id is not None}
    found.update(dict.fromkeys(own.all(), STOPLIST_OWN))
    return found


def cut(why: str) -> Landing:
    """Стоп-лист — «отсечён» правилом. Судья не говорил: вердикта нет."""
    return Landing(Lane.REJECT, why, decided_by=Decider.RULE)


async def _without_stoplisted(
    session: AsyncSession, waiting: Sequence[Site], tally: JudgeTally, moment: datetime
) -> list[Site]:
    """Отсечь стоп-лист до судьи и сохранить; вернуть тех, кого судить дальше."""
    closed = await stoplisted(session, [site.domain_id for site in waiting], moment)
    for site in waiting:
        if (why := closed.get(site.domain_id)) is not None:
            guard = waiting_judge()
            tally.by_rule += await land(session, site.candidate_id, cut(why), moment, guard=guard)
    await session.commit()
    return [site for site in waiting if site.domain_id not in closed]


async def judge_run(
    session: AsyncSession,
    *,
    run_id: int,
    segment: str,
    mode: JudgeMode,
    client: SegmentClient | None,
    home: httpx.AsyncClient | None,
    now: datetime | None = None,
) -> JudgeTally:
    """Судить кандидатов прогона, которые ждут судью. Коммит — по чекпоинту, здесь.

    `mode` — режим прогона; выключенный судья (`off`) модели не зовёт: стоп-лист и правило по
    домену режут, остальных решает человек. Потолок модели выбран — `stopped` словами, остальные
    ждут следующего прохода."""
    moment = now or datetime.now(UTC)
    tally = JudgeTally()
    waiting = await _without_stoplisted(session, await _pending(session, run_id), tally, moment)
    asked = None if mode is JudgeMode.OFF else client
    await judge_sites(session, waiting, Ask(segment, mode, asked, home, moment), Waiting(), tally)
    return tally
