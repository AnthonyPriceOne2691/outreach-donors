"""Глубина донора в обходе 24/7: сколько прошла цепочка частей, откуда её продолжить и слабый ли сайт.

Обход 24/7 (`always.py`) идёт по донору частями: первая — до тысячи страниц, дальше — до 10 000 и
ещё по 10 000. Часть — обычная строка обхода, а цепочка живёт здесь, строкой донора
(`CrawlDepthModel`): страниц открыто, частей кончено, откуда продолжить, доля продающих страниц и
пометка «мало рекламодателей», поставленная руками.

**Откуда продолжить — чекпоинт цепочки, сжатый** (`pack`). Это тот же чекпоинт обхода
(`progress.Checkpoint`): открытые страницы — их следующая часть не откроет второй раз — и очередь
ссылок. Страницы копятся всю цепочку и не режутся: иначе открытое открылось бы снова. Очередь —
не длиннее `QUEUE_KEEP`: следующая часть откроет не больше 10 000 страниц, а ссылки за обрезом
найдутся снова на них. JSON сжимается zlib: адрес страницы — около 10–25 байт (замер на
синтетике — 9 байт), то есть цепочка в 10 000 страниц — 0,1–0,25 МБ, в 100 000 — 1–2,5 МБ, а
очередь — не больше 0,5 МБ. Читается чекпоинт дважды на часть: в её начале и в конце.

**Строка обхода части хранит только своё** (`stored`): в её чекпоинт, который переписывается
каждой пачкой, страницы прежних частей не едут — их держит строка донора. Иначе цепочка в сто
тысяч страниц писала бы мегабайты на каждую сотню страниц. Продолжение части после выкатки
собирает чекпоинт обратно (`merged`).

**Сайт кончился** (`exhausted_at`) — только когда обход исчерпал адреса и карта сайта дочитана или
её нет: оборванная карта говорит «адресов больше нет» о сайте, который мы не дочитали.

**Первые 10 000 пройдены** (`first_done_at`) — когда в цепочке 10 000 разных страниц, сайт кончился
или часть упёрлась в потолок первых 10 000 (`reached_first`). Последнее — потому что потолок части
считает открытое, а цепочка — разные адреса: адрес карты сайта, который уводит на уже открытую
страницу, открывается и считается, а цепочку не растит. 17–18.11.2026 у четырёх доноров цепочка
встала на 9 973–9 998 страниц, и каждая часть «до 10 000» кончалась за минуту, открыв те же адреса.

**Слабый** (`weak_of`, в запросе — `weak_clause`) — считается при чтении: пометка руками, если она
есть, иначе доля продающих страниц после первых 10 000 ниже порога (`always_switch`). Порог сменили —
пометки меняются сразу.

**Выдача и экран чекпоинт не читают** (`Facts`, `facts`): выдача живёт в процессе разбора с потолком
памяти в 256 МБ, а чекпоинт — мегабайты на донора. Его читает только начало части в обходчике
(`row_of`); колонка ещё и отложена в модели.
"""

from __future__ import annotations

import json
import logging
import zlib
from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import Boolean, ColumnElement, and_, case, false, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from backend.config import crawl as cfg
from backend.features.core.domain import StopReason
from backend.features.core.models.crawl import CrawlDepthModel, CrawlRunModel
from backend.features.crawl.progress import Checkpoint
from backend.features.crawl.report import CrawlReport

logger = logging.getLogger(__name__)

#: Сколько ссылок очереди хранить в чекпоинте цепочки и в чекпоинте строки части (шапка).
QUEUE_KEEP = 20_000

#: Кто поставил пометку «мало рекламодателей»: сервис по порогу или человек руками.
AUTO = "auto"
HUMAN = "human"


def first_step() -> int:
    """Первый проход цепочки — страниц: столько, сколько у глубокого обхода (`deep.py`)."""
    return cfg.MAX_PAGES_DEEP


def pack(point: Checkpoint) -> bytes:
    """Чекпоинт цепочки — в колонку: JSON, очередь не длиннее `QUEUE_KEEP`, сжатый zlib."""
    kept = {**point.as_dict(), "queue": point.queue[:QUEUE_KEEP]}
    return zlib.compress(json.dumps(kept, ensure_ascii=False).encode("utf-8"))


def unpack(data: bytes | None) -> Checkpoint | None:
    """Чекпоинт цепочки из колонки. Испорченный — `None` с предупреждением: цепочка продолжится
    с начала сайта, а не упадёт на каждой части."""
    if not data:
        return None
    try:
        raw = json.loads(zlib.decompress(data).decode("utf-8"))
    except (zlib.error, UnicodeDecodeError, ValueError) as exc:
        logger.warning("обход 24/7: чекпоинт цепочки не прочитан (%s) — начну сначала", exc)
        return None
    return Checkpoint.of(raw) if isinstance(raw, dict) else None


def seed(base: Checkpoint) -> Checkpoint:
    """С чего начать новую часть: открытое и очередь цепочки, а срок, время и замедление — свои.
    Срок обхода — на часть, а не на цепочку: иначе к третьей части он кончался бы с первой
    страницы. Счётчики (статьи, попытки, откуда страницы) идут дальше — они о цепочке."""
    return replace(base, elapsed_sec=0.0, time_budget_sec=0.0, slowed=False)


def stored(point: Checkpoint, skip: int) -> dict[str, Any]:
    """Что писать в чекпоинт строки части: без `skip` страниц прежних частей и с обрезанной
    очередью (шапка). Остальное — как у обычного обхода."""
    return {**point.as_dict(), "pages": point.pages[skip:], "queue": point.queue[:QUEUE_KEEP]}


def merged(base: Checkpoint | None, own: Mapping[str, Any] | None) -> Checkpoint | None:
    """Чекпоинт цепочки и записанное частью — обратно в один: открытое цепочкой, затем частью, без
    повторов; очередь, счётчики, время и срок — части. Части ещё нечего сказать — цепочка как
    была."""
    part = Checkpoint.of(own)
    if part is None:
        return base
    if base is None:
        return part
    return replace(part, pages=list(dict.fromkeys([*base.pages, *part.pages])))


def exhausted(report: CrawlReport) -> bool:
    """Сайт кончился: адреса исчерпаны, а карта дочитана или её нет (шапка)."""
    complete_map = report.sitemap_found is False or report.sitemap_complete
    return report.stop_reason is StopReason.EXHAUSTED and complete_map


def reached_first(run: CrawlRunModel, report: CrawlReport) -> bool:
    """Часть упёрлась в потолок первых 10 000 или выше — первые 10 000 пройдены, сколько бы разных
    страниц ни легло в цепочку (шапка). Первая часть идёт с обычным потолком и сюда не попадает."""
    ceiling = run.max_pages or 0
    return report.stop_reason is StopReason.MAX_PAGES and ceiling >= first_step()


def _passed_first(row: CrawlDepthModel, run: CrawlRunModel, report: CrawlReport) -> bool:
    """Первые 10 000 пройдены этой частью (шапка): разных страниц хватило, сайт кончился или часть
    упёрлась в их потолок."""
    return row.pages >= first_step() or row.exhausted_at is not None or reached_first(run, report)


def past_first(depth: Facts | None) -> bool:
    """Цепочка прошла первые 10 000 — следующая часть идёт «ещё 10 000» к пройденному."""
    if depth is None:
        return False
    return depth.first_done_at is not None or depth.pages >= first_step()


class Marked(Protocol):
    """Что нужно о доноре, чтобы сказать, слабый ли он: строка цепочки или её числа (`Facts`)."""

    @property
    def weak_override(self) -> bool | None: ...

    @property
    def first_done_at(self) -> datetime | None: ...

    @property
    def yield_pct(self) -> float | None: ...


@dataclass(frozen=True, slots=True)
class Facts:
    """Строка цепочки без чекпоинта — то, что читают выдача и экран (шапка)."""

    host: str
    pages: int
    parts: int
    selling_pages: int | None
    yield_pct: float | None
    first_done_at: datetime | None
    exhausted_at: datetime | None
    weak_override: bool | None
    frozen_parts: int


#: Колонки `Facts` — по порядку полей.
FACT_COLUMNS = (
    CrawlDepthModel.host,
    CrawlDepthModel.pages,
    CrawlDepthModel.parts,
    CrawlDepthModel.selling_pages,
    CrawlDepthModel.yield_pct,
    CrawlDepthModel.first_done_at,
    CrawlDepthModel.exhausted_at,
    CrawlDepthModel.weak_override,
    CrawlDepthModel.frozen_parts,
)


async def facts(session: AsyncSession, hosts: Collection[str] | None = None) -> dict[str, Facts]:
    """Числа цепочек доноров — колонками, без чекпоинта; `hosts` — только этих, пусто — всех."""
    query = select(*FACT_COLUMNS)
    if hosts is not None:
        query = query.where(CrawlDepthModel.host.in_(sorted(hosts)))
    rows = await session.execute(query)
    return {row[0]: Facts(*row) for row in rows.tuples().all()}


def weak_clause(threshold: float) -> ColumnElement[bool]:
    """«Мало рекламодателей» в запросе — то же правило, что `weak_of`, при соединении доноров со
    строками цепочек: пометка руками, иначе доля после первых 10 000 ниже порога; строки нет — нет."""
    by_threshold = and_(
        CrawlDepthModel.first_done_at.is_not(None), CrawlDepthModel.yield_pct < threshold
    )
    return func.coalesce(
        CrawlDepthModel.weak_override, func.coalesce(by_threshold, false()), type_=Boolean
    )


def weak_of(row: Marked | None, threshold: float) -> tuple[bool, str | None]:
    """Слабый ли сайт и кто так решил: пометка руками сильнее порога; без неё — доля продающих
    страниц после первых 10 000 ниже порога. Руками снятая пометка — «не слабый» от человека."""
    if row is None:
        return False, None
    if row.weak_override is not None:
        return row.weak_override, HUMAN
    known = row.first_done_at is not None and row.yield_pct is not None
    if known and row.yield_pct is not None and row.yield_pct < threshold:
        return True, AUTO
    return False, None


def by_threshold(row: Marked | None) -> bool:
    """Пометку решает порог: первые 10 000 пройдены, доля известна, руками не ставили."""
    if row is None or row.weak_override is not None:
        return False
    return row.first_done_at is not None and row.yield_pct is not None


async def row_of(session: AsyncSession, host: str) -> CrawlDepthModel | None:
    """Строка донора вместе с чекпоинтом цепочки: колонка отложена, и без `undefer` её чтение
    пошло бы отдельным запросом мимо цикла событий."""
    found: CrawlDepthModel | None = await session.scalar(
        select(CrawlDepthModel)
        .where(CrawlDepthModel.host == host)
        .options(undefer(CrawlDepthModel.checkpoint))
    )
    return found


async def ensure(session: AsyncSession, host: str) -> CrawlDepthModel:
    """Строка донора под замком строки: заводится, если её нет. Замок — потому что строку пишут
    конец части и пометка руками, и каждый — по прочитанному."""
    await session.execute(
        insert(CrawlDepthModel).values(host=host).on_conflict_do_nothing(index_elements=["host"])
    )
    row = await session.scalar(
        select(CrawlDepthModel)
        .where(CrawlDepthModel.host == host)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise ValueError(f"строка глубины донора {host} пропала сразу после вставки")
    return row


async def finish_part(
    session: AsyncSession,
    run: CrawlRunModel,
    report: CrawlReport,
    *,
    base: Checkpoint | None,
    own: Mapping[str, Any] | None,
    now: datetime,
) -> CrawlDepthModel:
    """Часть кончилась — цепочка дальше: страницы, откуда продолжить, частей на одну больше, сайт
    кончился ли и пройдены ли первые 10 000. Чекпоинт строки части к этому моменту ещё не стёрт
    (`own`). Без фиксации."""
    row = await ensure(session, run.host)
    final = merged(base, own)
    if final is not None:
        row.checkpoint = pack(final)
        row.pages = len(final.pages)
    row.parts += 1
    row.last_run_id = run.id
    if exhausted(report) and row.exhausted_at is None:
        row.exhausted_at = now
    if row.first_done_at is None and _passed_first(row, run, report):
        row.first_done_at = now
    await session.flush()
    logger.info(
        "обход 24/7 %s: часть №%s кончилась (%s) — у цепочки %s страниц, частей %s%s",
        run.host,
        run.id,
        report.stop_reason.value,
        row.pages,
        row.parts,
        ", сайт кончился" if row.exhausted_at is not None else "",
    )
    return row


async def refresh_yield(session: AsyncSession, host: str, selling: int, *, frozen: int = 0) -> None:
    """Продающие страницы цепочки после пересчёта кандидатов (`gate.judge_run`) — и их доля от
    открытых, когда первые 10 000 пройдены, — и сколько старых частей пересчёт не читал (`frozen`,
    потолок ссылок в памяти). Одним запросом: строку в это время может писать конец следующей части.
    Строки донора нет — цепочки нет, писать некуда."""
    share = case(
        (
            (CrawlDepthModel.first_done_at.is_not(None)) & (CrawlDepthModel.pages > 0),
            100.0 * selling / CrawlDepthModel.pages,
        ),
        else_=None,
    )
    await session.execute(
        update(CrawlDepthModel)
        .where(CrawlDepthModel.host == host)
        .values(selling_pages=selling, yield_pct=share, frozen_parts=frozen)
    )


async def set_weak(
    session: AsyncSession, host: str, weak: bool | None, *, by: int | None, now: datetime
) -> tuple[bool | None, CrawlDepthModel]:
    """Пометка «мало рекламодателей» руками: `True` — пометить, `False` — снять, `None` — вернуть
    оценку порогу. Ответ — как стояло и строка донора. Кто и когда — у строки, при каждой смене.
    Без фиксации: её делает вызывающий вместе с записью журнала."""
    row = await ensure(session, host)
    was = row.weak_override
    if was != weak:
        row.weak_override, row.weak_set_by, row.weak_set_at = weak, by, now
        await session.flush()
    return was, row
