"""Что нового дала ночь: рекламодатели, которых до неё не было.

Ночной круг (`night.py`) каждую ночь проходит тех же доноров, и число доменов в его строке качества
(`load_quality.py`) от ночи к ночи одно. Вопрос к ночи другой: что она принесла нового (11.11.2026).

**Что хранится — и почему считается так.** Кандидатов донора пересчёт заменяет целиком
(`gate._replace_candidates`): время создания строки кандидата — время последнего пересчёта, а не
первой находки, и «созданных за ночь» кандидатов столько же, сколько всех. Память пар
(`candidate_memory`) заводится, только когда паре есть что переносить, — решение человека или
спрошенный DR. Числа обхода (`stats`) знают итог ночи, но не что в нём новое. История находок лежит
только в ссылках обходов (`outlinks`).

**Новый — правилом панели** (`panel.seen_before`): домена не было ни в ссылках обходов, заказанных
раньше этой ночи, ни среди рекламодателей. Правило одно с шагом «новых / повторных» над таблицей:
два «новых» на одном экране не должны значить разное.

**И память пары — сверх него.** Рекламодателя, найденного через сеть партнёрок, ссылки называют
доменом сети (`scoring._advertiser_of`), и по ссылкам его прошлого не видно: пока его не перевели,
он был бы «новым» каждую ночь — та же строка с тем же числом, от которой блок и уходил. Память пары
заводится первым же пересчётом, спросившим DR: со следующей ночи пара уже встречалась.

**Проверка — по индексу на каждый домен «куплено» ночи**, а не проход ссылок: так же шаг панели
считает «новых» на каждое открытие. Новые ссылки и новые пары «донор + домен» так не посчитать:
сравнивать пришлось бы ссылки двух обходов целиком.

**Чьи кандидаты** (`_found_then`). Ночного обхода донора — все. Донора обошли после ночи заново —
его кандидаты уже у нового обхода: из них находка ночи — домен, который стоял в ссылках ночного
обхода. Без этого число ночи убывало бы днём с каждым «Переобойти», а с одной выборкой «обходы
ночи» — и вовсе пропадало. Домен, который нашёл только поздний обход, ночи не приписывается.
Пересчёт до ночных обходов ещё не дошёл — считать не по чему: число пусто, а не ноль (ноль —
«искали и не нашли»).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ColumnElement, Select, Subquery, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.models.advertiser import CandidateMemoryModel, CandidateModel
from backend.features.core.models.crawl import OutLinkModel
from backend.features.crawl.acceptance import distinct_domains, found_rows, not_supplier
from backend.features.crawl.panel import seen_before
from backend.features.crawl.suppliers import supplier_hosts


def _remembered(rows: Subquery, since: datetime) -> ColumnElement[bool]:
    """Пара «донор + домен» строки была в памяти донора раньше `since`: её уже находили."""
    return (
        select(CandidateMemoryModel.id)
        .where(
            CandidateMemoryModel.donor_host == rows.c.donor,
            CandidateMemoryModel.target_root == rows.c.root,
            CandidateMemoryModel.created_at < since,
        )
        .exists()
    )


def _judged_since(runs: Subquery) -> ColumnElement[bool]:
    """Кандидат посчитан по обходу `runs` своего донора или по более позднему: пересчёт дошёл."""
    return (
        select(runs.c.id)
        .where(
            runs.c.host == CandidateModel.donor_host,
            CandidateModel.crawl_run_id >= runs.c.id,
        )
        .correlate(CandidateModel)
        .exists()
    )


def _found_then(runs: Subquery) -> ColumnElement[bool]:
    """Кандидат — находка обходов `runs`: посчитан по обходу своего донора из них или по более
    позднему, а домен стоял в ссылках того обхода (шапка, «Чьи кандидаты»).

    Связь с кандидатом названа явно (`correlate`): сама она ставится только с ближайшим внешним
    запросом, и вложенная проверка ссылок взяла бы кандидатов заново — «домен хоть какого-то
    кандидата стоял в ссылках ночи», то есть всегда «да» (поймано тестом позднего обхода)."""
    linked_then = (
        select(OutLinkModel.id)
        .where(
            OutLinkModel.crawl_run_id == runs.c.id,
            OutLinkModel.target_root == CandidateModel.target_root,
        )
        .correlate(runs, CandidateModel)
        .exists()
    )
    return (
        select(runs.c.id)
        .where(
            runs.c.host == CandidateModel.donor_host,
            or_(
                CandidateModel.crawl_run_id == runs.c.id,
                and_(CandidateModel.crawl_run_id > runs.c.id, linked_then),
            ),
        )
        .correlate(CandidateModel)
        .exists()
    )


async def fresh(
    session: AsyncSession, runs: Select[tuple[int, str]], *, since: datetime
) -> int | None:
    """Сколько рекламодателей обходы `runs` (номер и донор) нашли впервые: раньше `since` они не
    встречались. `None` — пересчёт до этих обходов не дошёл: кандидаты доноров ещё прежние."""
    night = runs.subquery("night")
    judged = select(CandidateModel.id).where(_judged_since(night)).exists()
    if not await session.scalar(select(judged)):
        return None
    rows = found_rows(_found_then(night)).subquery()
    seen = or_(seen_before(rows, since), _remembered(rows, since))
    found = await session.scalar(
        select(distinct_domains(rows).filter(~seen))
        .select_from(rows)
        .where(not_supplier(rows, await supplier_hosts(session)))
    )
    return int(found or 0)
