"""Волны глубины «Плана работ»: какие ключи гипотезы плана покупать следующим прогоном и что уже
куплено.

Конвейер (проход плана, следующий срез) собирает гипотезу плана с нарастающей глубиной выдачи и
берёт отсюда пачку прогона; прогон ставит, этапы и проблемы пишет он сам. Здесь — правила волн и
тонкие чтения базы; пишет модуль только отметку купленного, коммит — за вызывающим.

- **Волна** — страницы, которые покупает прогон: 1, 2, 3 и 4–5 одной покупкой — ступени «Глубины
  поиска до» книги (`plan_card.DEPTH_CHOICES`: топ-10, 20, 30, 50), не глубже предельной глубины
  листа (`max_pages`): последняя волна обрезается по ней.
- **Пачка** — до `keys_per_run` ключей одного рынка (прогон — один рынок), по порядку строк листа,
  у которых волна ещё не куплена: `pages` реестра меньше последней страницы волны. Рынки — по порядку
  «Где ищем». Волна — первая, где такие ключи есть: ключ, добавленный к гипотезе позже, сперва
  догоняет страницу 1, и только потом гипотеза идёт глубже.
- **Со второй волны** первыми идут ключи, чья страница 1 дала принятые сайты, — тем же счётом, что
  «Сайты по ключу» отчёта прогона (`segment_report.keyword_sites`); ключ с пустой выдачей
  (`report.serp.empty_keywords` прогонов гипотезы плана) глубже не идёт: за пустой страницей выдача
  тоже пуста.
- Ни в одной волне ключей нет — «нужны ключи» (`NeedKeys`): значением, а не исключением.
- **После прогона** — купленное у его ключей (`mark_bought`): последняя страница и сам прогон.
  Ключи, которых источник не отдал (`report.serp.lost_keywords`: заплачено, а не пришло), не
  отмечаются — их возьмёт следующая пачка той же волны.
- **«Найдено»** — уникальные сайты в полосе «принят» у прогонов гипотезы плана — своим модулем
  (`plan_found.found_sites`): его пересчитывают и решения человека, а им сюда не дотянуться без
  цикла импорта. В «тени» (`SALES_SEGMENT_JUDGE_MODE=shadow`) отказ модели — «судья отказал бы»: ни
  он, ни «на проверке» в счёт не идут, пока их не примет человек.

Ключи сверяются правилом выдачи (`serp.protocol.keyword_key`), а не строкой: источник называет ключ
в ответе по-своему. Списки ключей уходят в базу одним параметром-массивом: их тысячи, а параметров у
запроса Postgres — не больше 32 767.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from sqlalchemy import BindParameter, Text, all_, any_, case, literal, select, update
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.sales.plan_card import DEPTH_CHOICES
from backend.features.sales.plan_models import SalesPlanHypothesisModel, SalesPlanKeywordModel
from backend.features.sales.segment_models import (
    SalesSegmentCandidateModel,
    SalesSegmentRunModel,
)
from backend.features.sales.segment_report import keyword_sites
from backend.features.sales.segment_serp import KEY as SERP
from backend.features.sales.segment_serp import bought
from backend.features.serp.protocol import keyword_key

Key = SalesPlanKeywordModel
Run = SalesSegmentRunModel
Candidate = SalesSegmentCandidateModel
#: Поля итога выдачи прогона (`segment_serp.collect`): ключи без результатов и ключи, которых
#: источник не отдал.
EMPTY = "empty_keywords"
LOST = "lost_keywords"


@dataclass(frozen=True, slots=True)
class Wave:
    """Страницы выдачи, которые покупает прогон волны, с `first` по `last` включительно: это
    `from_page` и `depth_pages` прогона."""

    first: int
    last: int


#: Ступени «Глубины поиска до» книги страницами, по возрастанию: топ-10, 20, 30, 50.
STEPS = sorted(set(DEPTH_CHOICES.values()))
#: Волны по порядку — от ступени до ступени: страница 1, 2, 3 и страницы 4–5 одной покупкой.
WAVES = tuple(Wave(before + 1, pages) for before, pages in pairwise([0, *STEPS]))


def waves(max_pages: int) -> tuple[Wave, ...]:
    """Волны гипотезы плана — не глубже её предельной глубины: последняя обрезается по ней."""
    return tuple(
        Wave(wave.first, min(wave.last, max_pages)) for wave in WAVES if wave.first <= max_pages
    )


@dataclass(frozen=True, slots=True)
class Batch:
    """Следующий прогон гипотезы плана: волна, рынок и ключи — как их купит выдача, по порядку
    пачки."""

    wave: Wave
    market: str
    keywords: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NeedKeys:
    """Ключи и глубина кончились: каждый ключ куплен до предельной глубины `max_pages` или дальше
    пустой страницы не идёт. Слова проблемы — `plan_budget.keys_words`."""

    max_pages: int


@dataclass(frozen=True, slots=True)
class _Seen:
    """Что прогоны гипотезы плана на рынке сказали о ключах — правилом выдачи: чья страница 1 дала
    принятые сайты и у кого выдача пуста."""

    accepted: frozenset[str]
    empty: frozenset[str]


def listed_keys(answer: object) -> frozenset[str]:
    """Ключи из списка итога выдачи — правилом выдачи. Не список (покупка не закончена, поле правили
    руками) — ничего."""
    if not isinstance(answer, list):
        return frozenset()
    return frozenset(keyword_key(found) for found in answer if isinstance(found, str)) - {""}


def _texts(values: Iterable[str]) -> BindParameter[Any]:
    """Ключи — одним параметром-массивом (шапка модуля)."""
    return literal(sorted(values), ARRAY(Text))


async def _seen(session: AsyncSession, row_id: int, market: str) -> _Seen:
    """Что прогоны гипотезы плана на рынке сказали о ключах (`_Seen`)."""
    runs = (Run.plan_hypothesis_id == row_id, Run.country == market)
    first_page = select(Run.id).where(*runs, Run.from_page == 1)
    sites = await keyword_sites(session, Candidate.run_id.in_(first_page))
    answers = await session.scalars(select(Run.report[SERP][EMPTY]).where(*runs))
    return _Seen(
        frozenset(keyword_key(phrase) for phrase, found in sites.items() if found.accepted),
        frozenset[str]().union(*map(listed_keys, answers.all())),
    )


async def _history(
    session: AsyncSession, row_id: int, market: str, seen: dict[str, _Seen]
) -> _Seen:
    """Что сказали прогоны рынка — одним чтением на выбор пачки."""
    if market not in seen:
        seen[market] = await _seen(session, row_id, market)
    return seen[market]


async def _pick(
    session: AsyncSession, row_id: int, market: str, wave: Wave, seen: _Seen | None, limit: int
) -> tuple[str, ...]:
    """Ключи рынка, у которых волна не куплена, — до `limit`, по порядку пачки. `seen` — со второй
    волны: ключи с пустой выдачей отсеяны, давшие принятые на странице 1 — первыми."""
    query = select(Key.keyword).where(
        Key.plan_hypothesis_id == row_id, Key.market == market, Key.pages < wave.last
    )
    order: list[Any] = [Key.position, Key.id]
    if seen is not None:
        query = query.where(Key.keyword_key != all_(_texts(seen.empty)))
        order.insert(0, case((Key.keyword_key == any_(_texts(seen.accepted)), 0), else_=1))
    rows = await session.scalars(query.order_by(*order).limit(limit))
    return tuple(rows.all())


async def next_batch(
    session: AsyncSession, row: SalesPlanHypothesisModel, *, keys_per_run: int
) -> Batch | NeedKeys:
    """Следующая пачка гипотезы плана — волна, рынок и до `keys_per_run` ключей (шапка модуля), —
    или «нужны ключи». Ничего не пишет."""
    if keys_per_run < 1:
        raise ValueError(f"ключей на прогон — от одного, а не {keys_per_run}")
    seen: dict[str, _Seen] = {}
    for wave in waves(row.max_pages):
        for market in row.markets:
            history = await _history(session, row.id, market, seen) if wave.first > 1 else None
            picked = await _pick(session, row.id, market, wave, history, keys_per_run)
            if picked:
                return Batch(wave, market, picked)
    return NeedKeys(row.max_pages)


async def mark_bought(session: AsyncSession, run: SalesSegmentRunModel) -> int:
    """Купленное прогоном — его ключам в реестре гипотезы плана на его рынке: последняя страница
    (`depth_pages`) и сам прогон (`run_id`). Отметка — по итогу выдачи (`segment_serp.bought`): итога
    нет — выдача не куплена, отмечать нечего. Ключи, которых источник не отдал, не куплены. Отметка
    только вперёд: ключ, купленный глубже другим прогоном, остаётся как был, и повтор ничего не
    портит. Возвращает, скольким ключам отмечено; коммит — за вызывающим."""
    if run.plan_hypothesis_id is None or not bought(run):
        return 0
    lost = listed_keys(run.report[SERP].get(LOST))
    keys = {keyword_key(phrase) for phrase in run.keywords} - lost - {""}
    if not keys:
        return 0
    marked = await session.scalars(
        update(Key)
        .where(
            Key.plan_hypothesis_id == run.plan_hypothesis_id,
            Key.market == run.country,
            Key.keyword_key == any_(_texts(keys)),
            Key.pages <= run.depth_pages,
        )
        .values(pages=run.depth_pages, run_id=run.id)
        .returning(Key.id)
        .execution_options(synchronize_session="fetch")
    )
    return len(marked.all())
