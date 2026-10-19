"""Выдача прогона гипотезы → кандидаты сбора: один раз за прогон, со строкой расхода.

Выдача — общий путь прогонов доноров (`runs/planning.gather_candidates`): ключи уходят
источнику выдачи пачкой, адреса сводятся к корневым доменам (`domains.host` — ключ дедупа),
у каждого домена — текст его верхней позиции: по нему судит судья сегмента. Своей копии
разбора выдачи нет.

**Платная часть — один раз.** Цена выдачи пишется строкой расхода `sales_serp` в той же
транзакции, что кандидаты и итог выдачи `report.serp`, — он и есть отметка «куплена»
(`bought`): повтор задачи после сбоя и продолжение прогона выдачу заново не покупают
(вызывающий коммитит сразу). Отметка — итог в отчёте, а не стадия: стадия ставится при
входе в этап (`segment_progress.enter`), и «покупаем выдачу» ещё не значит «куплена».

**Пока выдача покупается — её след в строке прогона** (`SerpTrail`, `serp.protocol.SerpWatch`):
ход «готово X из Y ключей» (`report.progress`) и номера задач отложенного режима
(`report.serp.tasks`) — своими коммитами, номера — до опроса. Повтор задачи передаёт их
источнику, и он забирает оплаченное по номерам, а не покупает второй раз. У быстрого режима
(`live`) номеров нет: запрос не идемпотентен, и повтор после сбоя покупает выдачу заново —
прогон говорит это словами (`report.progress.note`) до повтора.

**Выдача упала, успев потратить** (задачи поставлены и списаны или быстрые запросы оплачены,
а забрать не вышло) — потраченное пишется строкой расхода и коммитится сразу, до повтора
задачи: откат унёс бы строку. Оно же копится в `report.serp.paid_usd`, а `report.serp.cost_usd`
— всегда сумма журнала по прогону: сразу после сбоя — оплаченное до него, после покупки —
оно и сама покупка (повтор по номерам задач стоит около нуля, и без этого отчёт назвал бы
бесплатной оплаченную выдачу). Его читает панель расхода — сумма в отчёте прогона и в
журнале одна и та же на любом шаге.

**Домен, уже собранный гипотезой, второй раз не заводится**: его не судят и не ищут ему
адрес снова — отчёт называет его «уже в сборе гипотезы».

**Перед покупкой — счёт** (`serp/funds.ensure_funds`, слово владельца 16.10.2026): денег на
счету выдачи меньше порога или меньше цены покупки — постоянный отказ словами, ничего не
куплено; счёт мог уйти между сметой и задачей. Цена — ключей, которые проход купит: ключ с
номером задачи прошлой попытки уже оплачен и забирается по номеру, и повтор после сбоя опроса
не встаёт на счету, опустевшем от той же покупки.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core import usage
from backend.features.donors.repository import DonorRepository
from backend.features.runs.planning import Candidates, gather_candidates
from backend.features.sales.segment_models import (
    Lane,
    SalesSegmentCandidateModel,
    SalesSegmentRunModel,
    Stage,
)
from backend.features.sales.segment_progress import KEY as PROGRESS
from backend.features.sales.segment_progress import advanced, noted
from backend.features.serp import funds
from backend.features.serp.modes import SerpMode
from backend.features.serp.protocol import SerpProgress, SerpProvider, SerpWatch

logger = logging.getLogger(__name__)

#: Операция журнала расхода: выдача сбора продаж, единица — доллар.
SERP_OPERATION = "sales_serp"
#: Строк в одной вставке: параметров у Postgres не больше 32 767 на запрос.
CHUNK = 1000
#: Итог выдачи в отчёте прогона; пока выдача покупается — номера задач и оплаченное.
KEY = "serp"
#: Поле итога, которое пишет только законченная покупка: по нему — «куплена». Есть и у
#: итогов прогонов до номеров задач, поэтому старый прогон выдачу второй раз не купит.
BOUGHT_FIELD = "hosts"
#: Ход покупки пишется не чаще раза в секунду: у быстрого режима ответы по ключам идут
#: десятками в секунду, а последний ответ пишется всегда.
PROGRESS_EVERY_S = 1.0


def purchased(report: Mapping[str, Any]) -> bool:
    """Итог выдачи в отчёте прогона: покупка закончена — её итог лёг в отчёт той же
    транзакцией, что кандидаты и строка расхода."""
    serp = report.get(KEY)
    return isinstance(serp, Mapping) and BOUGHT_FIELD in serp


def bought(run: SalesSegmentRunModel) -> bool:
    """Куплена ли выдача прогона (`purchased`). Нет итога (или в нём пока только номера задач)
    — выдачу покупает следующий проход, с какой бы стадии он ни начинался: прогон,
    остановленный до выдачи, продолжение покупает её, а не идёт к судье без сайтов."""
    return purchased(run.report)


def buying(report: Mapping[str, Any]) -> bool:
    """Покупка выдачи начата и не закончена — идёт или прервана, и следующий проход её доберёт:
    в отчёте её след (номера задач, оплаченное до сбоя) или ход этапа выдачи, а итога нет.
    Так панель расхода говорит «покупка не закончена», а не «цена не записана» все минуты
    очереди провайдера и не «ещё не покупали» посреди быстрой покупки."""
    if purchased(report):
        return False
    progress = report.get(PROGRESS)
    return isinstance(report.get(KEY), Mapping) or (
        isinstance(progress, Mapping) and progress.get("stage") == Stage.SERP
    )


def _partial(run: SalesSegmentRunModel) -> dict[str, Any]:
    """След покупки в отчёте: номера задач и оплаченное до сбоя; итога ещё нет."""
    serp = run.report.get(KEY)
    return dict(serp) if isinstance(serp, dict) and not bought(run) else {}


def _money(amount: float) -> str:
    return f"${amount:.4f}".replace(".", ",")


class SerpTrail:
    """След покупки выдачи в строке прогона: номера задач и ход «X из Y ключей».

    Пишет сессия прохода — она в это время ничем другим не занята (покупка ждёт
    провайдера), а у быстрого режима ответы приходят из параллельных запросов: запись —
    под замком. Сбой записи покупку не роняет (`SerpWatch` пишет его в журнал)."""

    def __init__(self, session: AsyncSession, run: SalesSegmentRunModel) -> None:
        self._session = session
        self._run = run
        self._lock = asyncio.Lock()
        self._said = 0.0
        #: Номера задач, сохранённые в строке прогона этим проходом.
        self.saved: set[str] = set()

    def watch(self) -> SerpWatch:
        """Слушатель покупки — с номерами задач, сохранёнными прошлой попыткой."""
        tasks = _partial(self._run).get("tasks")
        known = {str(k): str(v) for k, v in tasks.items()} if isinstance(tasks, dict) else {}
        return SerpWatch(tasks=known, on_posted=self.posted, on_progress=self.progress)

    async def posted(self, created: Mapping[str, str]) -> None:
        """Номера только что поставленных задач — в строку прогона до опроса."""
        async with self._lock:
            partial = _partial(self._run)
            tasks = partial.get("tasks")
            merged = {**(tasks if isinstance(tasks, dict) else {}), **created}
            self._run.report = {**self._run.report, KEY: {**partial, "tasks": merged}}
            await self._session.commit()
            self.saved.update(created)

    async def progress(self, event: SerpProgress) -> None:
        """Ход покупки: «покупка началась» и последний ответ — всегда, остальное — не чаще
        `PROGRESS_EVERY_S`."""
        last = event.keyword is None or event.done >= event.total
        if not last and time.monotonic() - self._said < PROGRESS_EVERY_S:
            return
        async with self._lock:
            self._run.report = advanced(self._run, done=event.done, total=event.total)
            await self._session.commit()
            self._said = time.monotonic()


def _rows(
    run: SalesSegmentRunModel, found: Candidates, ids: dict[str, int]
) -> list[dict[str, Any]]:
    rows = []
    for host in found.hosts:
        if host not in ids:
            continue
        page = found.texts.get(host)
        keys = found.found_by.get(host) or []
        rows.append(
            {
                "run_id": run.id,
                "hypothesis_id": run.hypothesis_id,
                "domain_id": ids[host],
                "keyword": keys[0] if keys else None,
                "page_url": page.url if page else None,
                "page_title": page.title if page else None,
                "page_description": page.description if page else None,
                "lane": Lane.PENDING.value,
            }
        )
    return rows


async def _insert_new(session: AsyncSession, rows: Sequence[dict[str, Any]]) -> int:
    """Новые кандидаты гипотезы; уже собранные домены остаются как были."""
    new = 0
    for start in range(0, len(rows), CHUNK):
        statement = (
            insert(SalesSegmentCandidateModel)
            .values(list(rows[start : start + CHUNK]))
            .on_conflict_do_nothing(constraint="uq_sales_segment_candidates_domain")
            .returning(SalesSegmentCandidateModel.id)
        )
        new += len((await session.scalars(statement)).all())
    return new


def provider_mode(provider: SerpProvider) -> str | None:
    """Режим покупки источника строкой (`SerpMode`); источник без режимов — пусто."""
    mode = getattr(provider, "mode", None)
    return mode.value if isinstance(mode, SerpMode) else None


async def _ensure_funds(run: SalesSegmentRunModel, provider: SerpProvider) -> None:
    """Хватит ли счёта на покупку (`funds.ensure_funds`) — по ключам без номера задачи: с номером
    оплачен прошлой попыткой. Покупать нечего — счёт не спрашивается."""
    tasks = _partial(run).get("tasks")
    paid = tasks if isinstance(tasks, dict) else {}
    to_buy = [keyword for keyword in run.keywords if keyword not in paid]
    if to_buy:
        await funds.ensure_funds(provider, len(to_buy), run.depth_pages, provider_mode(provider))


async def collect(
    session: AsyncSession, run: SalesSegmentRunModel, provider: SerpProvider
) -> dict[str, Any]:
    """Купить выдачу по ключам прогона и завести кандидатов. Коммит — за вызывающим,
    сразу: строка расхода, кандидаты и итог — одна транзакция. Номера задач и ход покупки
    коммитит `SerpTrail` по ходу. Денег на счету мало — отказ до покупки (`_ensure_funds`)."""
    await _ensure_funds(run, provider)
    trail = SerpTrail(session, run)
    before = getattr(provider, "spent", 0.0)
    try:
        found = await gather_candidates(
            provider,
            run.keywords,
            run.country,
            depth_pages=run.depth_pages,
            watch=trail.watch(),
        )
    except Exception:
        spent = max(0.0, getattr(provider, "spent", 0.0) - before)
        await _paid_anyway(
            session, run, spent, kept=bool(trail.saved), mode=provider_mode(provider)
        )
        raise
    if found.cost_usd:
        usage.record(session, operation=SERP_OPERATION, amount_usd=found.cost_usd)
    domains = DonorRepository(session)
    ids: dict[str, int] = {}
    for start in range(0, len(found.hosts), CHUNK):
        ids |= await domains.ensure_domains(found.hosts[start : start + CHUNK])
    new = await _insert_new(session, _rows(run, found, ids))
    partial = _partial(run)
    paid_before = float(partial.get("paid_usd") or 0.0)
    tally = {
        "keywords": found.keywords,
        "results": found.results,
        "hosts": len(found.hosts),
        "new": new,
        "known": len(found.hosts) - new,
        "dropped": found.dropped,
        "empty_keywords": list(found.empty_keywords),
        "lost_keywords": list(found.lost_keywords),
        # Расход выдачи прогона — за все попытки: оплаченное до сбоя и эта покупка.
        "cost_usd": round(paid_before + found.cost_usd, 4),
        "mode": provider_mode(provider),
        "tasks": partial.get("tasks") or {},
    }
    if paid_before:
        tally["paid_usd"] = round(paid_before, 4)
    run.report = {**run.report, KEY: tally}
    return tally


def _failure_words(spent: float, *, kept: bool, mode: str | None) -> str:
    """Что будет с оплаченной выдачей на следующем проходе — словами, до него."""
    if kept:
        return (
            f"выдача упала после оплаты ({_money(spent)}): номера задач сохранены — следующий "
            "проход заберёт оплаченное по ним и второй раз не купит"
        )
    why = (
        "у быстрого режима номеров задач нет"
        if mode == SerpMode.LIVE
        else "номера задач не сохранились"
    )
    return f"выдача упала после оплаты ({_money(spent)}): {why} — следующий проход купит её заново"


async def _paid_anyway(
    session: AsyncSession,
    run: SalesSegmentRunModel,
    spent: float,
    *,
    kept: bool,
    mode: str | None,
) -> None:
    """Выдача упала после оплаты: потраченное — строкой расхода и в след покупки, что будет
    со следующим проходом — словами в ходе прогона; всё — коммитом, до повтора задачи."""
    if not spent:
        return
    usage.record(session, operation=SERP_OPERATION, amount_usd=spent)
    partial = _partial(run)
    paid = round(float(partial.get("paid_usd") or 0.0) + spent, 4)
    words = _failure_words(spent, kept=kept, mode=mode)
    # Расход в отчёте — как в журнале уже сейчас, до повтора: прогон может так и остаться
    # остановленным, а панель расхода читает `cost_usd`.
    run.report = {**noted(run, words), KEY: {**partial, "paid_usd": paid, "cost_usd": paid}}
    await session.commit()
    logger.warning(
        "сбор продаж: выдача упала после оплаты — расход записан",
        extra={"run": run.id, "amount_usd": round(spent, 4), "rebuy": not kept, "mode": mode},
    )
