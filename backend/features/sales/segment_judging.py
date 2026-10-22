"""Проход судьи сегмента по кандидатам прогона: кэш, правила, главная, модель, режим, учёт.

**Кого судим** (`waiting_judge`): кандидатов прогона, которых ещё не судили, и тех, за кого
модель не ответила, пока человек их не решил: отказ модели — не вердикт, и следующий
проход судит домен снова. Пересуд прогона после правки описания (`segment_rejudge.py`) идёт
тем же окном, мимо кэша и по тексту, который видел прошлый суд.

**Кэш — таблица вердиктов**: свежий вердикт того же домена, гипотезы, версии промпта и
описания сегмента не пересуживается и токенов не стоит. Вердикт правила и модели ложится
туда же; ответа модели нет — строки нет. «На проверку» без цитаты (модель её не дала,
сочинила, проверка её не нашла, ответ не разобран) — тоже не вердикт, а несработавшая
проверка: в кэш не ложится (`SegmentVerdict.proven`), а такие строки, записанные раньше, кэш не
отдаёт (`segment_cache`: только правило и факты) — так ложные «цитаты нет» прежней сверки не
переживают её правку, без миграции и без вызова модели: модель спросят снова. Слова такого
вердикта у сайта не теряются — они в колонке судьи (`judge_reason`, `segment_window.land`).

**Скользящим окном** (`segment_window.py`, `SALES_SEGMENT_JUDGE_CONCURRENCY`): до N сайтов
разом, чекпоинт — каждые `BATCH` вердиктов, потолок модели — после каждого чекпоинта; выбран —
проход останавливается словами, неосуждённые ждут следующего прохода. Полоса ложится, пока
кандидат ещё ждёт судью (`waiting_judge`): человек, решивший сайт «на проверку» посреди
прохода, решил его — судья за ним не переписывает.

**Главная — только тем, кого судит модель**: «кому не пишем» по имени (`segment_judge.by_host`
— общее правило `runs/exclusions.by_name`) режется до неё, такой сайт не открывается вовсе; кэш
и выключенный судья главной не просят.

**Разбор описания — один раз до окна** (`read_scope`, `segment_scope`): страны и виды сайтов
сегмента; тот же разбор того же описания берётся из отчёта прогона, без модели. Сбой разбора — не
«мест нет»: окно кладёт сайты «на проверку» словами и следующий проход спросит снова. Поле гипотезы
«страны компаний» (`hypothesis_countries`) — тоже одно на проход.

**Модель — до первой траты прохода** (`unready`, слово владельца 20.10.2026): судья включён, а
модель не ответила проверке (`shared/llm.unready`: нет кредитов, ключ не принят, не ответила) —
смета, запуск и «Продолжить» отказывают её словами, задача встаёт ими же. Факты сайта и разбор
описания — одна модель (`SegmentClient`), проверка спрашивает её один раз. Нет кредитов на разборе
описания — проход к сайтам не идёт и встаёт словами (`read_scope`), а не «сбоем разбора» у каждого.

**Второй шанс цитате — в итоге прохода** (`PassTally`, рядом с `JudgeTally` окна): сколько раз
модель спросили второй раз, потому что цитата первого ответа не нашлась в тексте, и сколько раз
цитата нового ответа нашлась (`segment_client.SegmentClient.judge`). Токены обоих вызовов — в
токенах вердикта, то есть в счёте прохода и в расходе, как у одного вызова. Тот же итог — у
пересуда (`segment_rejudge`); в отчёте прогона повторы — суммой по проходам (`segment_run`).

**Стоп-лист — до всего** (`stoplisted`): домен в стоп-листе продаж или под строкой общего
стоп-листа, которая держит продажи, отсекается правилом до кэша, главной и модели — при любом
режиме судьи. Иначе он оплатил бы модель и платную ступень поиска адреса и отсеялся бы только
очисткой лидов. Правило то же, что у очистки (`cleaning.py`): строка продаж на домен и
`suppressions.holds_sales`. В таблицу вердиктов такой отсев не пишется: строку стоп-листа
снимают, а вердикт сайта жил бы полгода.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import llm as llm_cfg
from backend.config.judge import JudgeMode
from backend.features.core import usage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.sales import segment_scope, usage_cap
from backend.features.sales.hypothesis_countries import CountryPolicy, policy_of
from backend.features.sales.models import SalesHypothesisModel, SalesStoplistModel
from backend.features.sales.segment_judge import (
    OPERATION,
    SegmentClient,
    SegmentVerdict,
    segment_digest,
)
from backend.features.sales.segment_models import (
    Decider,
    Lane,
    SalesSegmentCandidateModel,
    SalesSegmentRunModel,
)
from backend.features.sales.segment_scope import Scope
from backend.features.sales.segment_window import (
    NO_CREDITS,
    Ask,
    JudgeTally,
    Landing,
    Site,
    judge_sites,
    land,
)
from backend.features.sales.suppressions import holds_sales
from backend.shared import llm

logger = logging.getLogger(__name__)

#: Что проход отдаёт наружу. Итог прохода (`JudgeTally`) живёт в окне (`segment_window.py`),
#: а прогон (`segment_run`) берёт его по имени прохода — список держит это имя явным.
__all__ = [
    "STOPLIST_OWN",
    "STOPLIST_SHARED",
    "JudgeTally",
    "PassTally",
    "Waiting",
    "cut",
    "judge_run",
    "policy_of_run",
    "read_scope",
    "stoplisted",
    "unready",
    "waiting_judge",
]

#: Отсечён стоп-листом до судьи — словами на экране прогона.
STOPLIST_OWN = "домен в стоп-листе продаж — отсечён до судьи, адрес не ищем"
STOPLIST_SHARED = "домен в общем стоп-листе — отсечён до судьи, адрес не ищем"

Candidate = SalesSegmentCandidateModel


@dataclass(slots=True)
class PassTally(JudgeTally):
    """Итог прохода прогона и пересуда и рядом — второй шанс цитате (шапка модуля). Считается по
    вердикту там же, где его токены (`spend`): второй вызов — часть того же вердикта."""

    #: Цитата первого ответа не нашлась — модель спросили второй раз.
    quote_retried: int = 0
    #: Из них цитата нового ответа нашлась: факты — его.
    quote_retry_held: int = 0

    def spend(self, verdict: SegmentVerdict) -> None:
        JudgeTally.spend(self, verdict)
        self.quote_retried += int(verdict.retried)
        self.quote_retry_held += int(verdict.retry_held)

    def as_dict(self) -> dict[str, Any]:
        found = JudgeTally.as_dict(self)
        return {
            **found,
            "quote_retried": self.quote_retried,
            "quote_retry_held": self.quote_retry_held,
        }


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


async def unready(mode: JudgeMode) -> str | None:
    """Почему проходу с судьёй не начинать — словами владельца (`shared/llm.words_of`); `None` —
    модель судьи ответила проверке или судья выключен: модель не зовётся, проверять нечего."""
    if mode is JudgeMode.OFF:
        return None
    return await llm.unready(llm_cfg.SALES_SEGMENT_MODEL)


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


async def policy_of_run(session: AsyncSession, run: SalesSegmentRunModel) -> CountryPolicy:
    """Поле гипотезы прогона «страны компаний» — нынешнее: по нему проход и пересуд ставят полосы."""
    hypothesis = await session.get(SalesHypothesisModel, run.hypothesis_id)
    if hypothesis is None:
        return CountryPolicy()
    return policy_of(hypothesis.other_countries, hypothesis.excluded_countries)


async def read_scope(
    session: AsyncSession,
    run: SalesSegmentRunModel,
    client: SegmentClient,
    segment: str,
    tally: JudgeTally,
    moment: datetime,
) -> Scope | None:
    """Разбор описания прогона (`segment_scope`): того же описания — из отчёта прогона, без модели;
    иначе вызов модели под потолком продаж — итог или сбой словами ложится в отчёт, токены — в счёт
    прохода и в журнал расхода. Сбой — `None`: сайты — «на проверку» словами, следующий проход
    спросит снова. Потолок выбран или у ключа нет кредитов — `None` и остановка прохода словами
    (`tally.stopped`): к сайтам проход не идёт."""
    digest = segment_digest(segment)
    if (known := segment_scope.remembered(run.report, digest)) is not None:
        return known
    try:
        await usage.ensure_llm_within_cap(session, now=moment, own=usage_cap.sales_cap())
    except usage.LlmCapExceededError as exc:
        logger.warning(
            "разбор описания не спрошен: потолок модели продаж", extra={"error": str(exc)}
        )
        tally.stopped = str(exc)
        return None
    found, asked = await client.scope(segment)
    tally.paid(asked.tokens, asked.spent)
    if asked.no_credits:
        # Не «сбой разбора»: к сайтам проход не идёт — они ждут «Продолжить» после пополнения.
        logger.warning("разбор описания: у модели нет кредитов", extra={"run": run.id})
        tally.stopped = NO_CREDITS
        return None
    entry = segment_scope.entry(digest, found, asked.tokens)
    await segment_scope.write(session, run, entry, operation=OPERATION)
    return found if isinstance(found, Scope) else None


async def judge_run(
    session: AsyncSession,
    *,
    run_id: int,
    segment: str,
    mode: JudgeMode,
    client: SegmentClient | None,
    home: httpx.AsyncClient | None,
    now: datetime | None = None,
    letters: str | None = None,
) -> PassTally:
    """Судить кандидатов прогона, которые ждут судью. Коммит — по чекпоинту, здесь.

    `mode` — режим прогона; выключенный судья (`off`) модели не зовёт: стоп-лист и правило по
    домену режут, остальных решает человек. Потолок модели выбран — `stopped` словами, остальные
    ждут следующего прохода. `letters` — язык писем прогона: сайт уверенно на другом — «отказал бы»
    словами (`segment_judge.by_language`). Поле гипотезы «страны компаний» — нынешнее
    (`policy_of_run`)."""
    moment = now or datetime.now(UTC)
    tally = PassTally()
    waiting = await _without_stoplisted(session, await _pending(session, run_id), tally, moment)
    asked = None if mode is JudgeMode.OFF else client
    scope, policy = None, CountryPolicy()
    run = await session.get(SalesSegmentRunModel, run_id)
    if waiting and asked is not None and run is not None:
        scope = await read_scope(session, run, asked, segment, tally, moment)
        policy = await policy_of_run(session, run)
    ask = Ask(segment, mode, asked, home, moment, letters=letters, scope=scope, policy=policy)
    await judge_sites(session, waiting, ask, Waiting(), tally)
    return tally
