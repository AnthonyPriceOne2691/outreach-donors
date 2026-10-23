"""Что нашёл обход донора: домены по вердиктам и страницы со ссылками на рекламодателей.

Тест-группа 23.10.2026: «нет понимания, сколько с каждого сайта: сколько продажных ссылок,
статей, сколько спорных». Приёмка по донорам знала только рекламодателей, а счётчики
вердиктов экрана проверки — по всем донорам сразу.

**Строки — те же, что у «Рекламодателей» приёмки** (`acceptance.py`): кандидаты донора в базе —
его последнего пригодного обхода (`gate.latest_crawls`: новый обход заменяет кандидатов
прежнего), домен у донора — один раз. Ещё не посчитанный обход не меняет ни кандидатов, ни чисел.

- **Куплено** — вердикт скоринга, кроме тех, кому человек сказал «не пишем». Рекламодатели
  донора (`advertiser_rules.writes_to`) — это «куплено» и все, кому человек сказал «пишем» сверх
  него: разница двух чисел — решения человека. С отклонёнными внутри она была бы «пишем» минус
  «не пишем», и сверить её было бы не с чем.
- **Спорно** — все спорные, решённые человеком («пишем» или «не пишем») — своим числом среди них.
- **Мимо** и **кому не пишем** — вердиктами скоринга, как в шапке очереди проверки.
- **Страниц** — разных страниц обхода со ссылкой хоть на одного рекламодателя, которому пишем:
  витрина с пятью — одна страница. У кандидата лежит только число страниц (`pages`), и сумма по
  кандидатам посчитала бы витрину пять раз; адреса — у ссылок обхода (`outlinks`). Ссылка через
  сеть партнёрок лежит там с доменом сети, а кандидат — у рекламодателя, которого сеть назвала:
  соединение по домену ссылки её бы потеряло. Поэтому страницы собирает правило скоринга
  (`scoring.advertiser_pages`) — то же, что считает `pages`: сеть — к рекламодателю, документы
  не в счёт.

**Все доноры вместе — суммой по донорам, без поставщиков** (`together`): домен двух доноров —
две ссылки и два решения, а страницы у каждого донора свои.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Collection, Sequence
from dataclasses import astuple, dataclass, fields
from typing import Any

from sqlalchemy import Select, func, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import Verdict
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.crawl import OutLinkModel
from backend.features.crawl.advertiser_rules import writes_to
from backend.features.crawl.affiliate import NETWORK_ROOTS
from backend.features.crawl.links import OutLink
from backend.features.crawl.scoring import advertiser_pages

#: Ссылка обхода — колонками с именами полей `OutLink`: у таблицы `outlinks` они те же
#: (`gate._to_link` переводит строку так же, поле в поле).
_LINK_COLUMNS = tuple(OutLinkModel.__table__.c[field.name] for field in fields(OutLink))


@dataclass(frozen=True, slots=True)
class Findings:
    """Что нашёл последний обход донора — или сумма по донорам (`together`)."""

    #: «Куплено» скоринга без тех, кому человек сказал «не пишем».
    bought: int = 0
    #: «Спорно» — все: и решённые человеком, и ждущие его.
    pending: int = 0
    #: Из спорных — решённые человеком: «пишем» или «не пишем».
    decided: int = 0
    #: «Мимо»: на рекламу баллов не набралось.
    skipped: int = 0
    #: «Кому не пишем»: домен из списка — соцсеть, СМИ, гос. сайт, крупный сайт…
    blocked: int = 0
    #: Разных страниц обхода со ссылкой на рекламодателя, которому пишем.
    pages: int = 0

    def __add__(self, other: Findings) -> Findings:
        return Findings(
            *(mine + theirs for mine, theirs in zip(astuple(self), astuple(other), strict=True))
        )


def together(found: dict[str, Findings], suppliers: Collection[str]) -> Findings:
    """Сумма по донорам, кроме поставщиков (`suppliers` — в нижнем регистре), — строка
    «Все доноры вместе»: рекламодателей поставщика не трогаем, как и в остальной строке."""
    return sum(
        (value for host, value in found.items() if host.lower() not in suppliers), Findings()
    )


async def by_donor(session: AsyncSession) -> dict[str, Findings]:
    """Что нашёл обход у каждого донора с кандидатами. Донора без кандидатов здесь нет:
    у него всё по нулям — `Findings()`."""
    counted = (await session.execute(_verdicts())).all()
    pages = await _pages(session)
    return {
        str(donor): Findings(bought, pending, decided, skipped, blocked, pages.get(donor, 0))
        for donor, bought, pending, decided, skipped, blocked in counted
    }


def _verdicts() -> Select[Any]:
    """Домены донора по вердиктам — одним запросом, счётом с условием."""
    domains = func.count(func.distinct(CandidateModel.target_root))
    verdict = CandidateModel.verdict
    pending = verdict == Verdict.PENDING
    return select(
        CandidateModel.donor_host,
        domains.filter((verdict == Verdict.BOUGHT) & CandidateModel.confirmed.is_not(False)),
        domains.filter(pending),
        domains.filter(pending & CandidateModel.confirmed.is_not(None)),
        domains.filter(verdict == Verdict.SKIPPED),
        domains.filter(verdict == Verdict.BLOCKED),
    ).group_by(CandidateModel.donor_host)


async def _pages(session: AsyncSession) -> dict[str, int]:
    """Страниц со ссылкой на рекламодателя, которому пишем, — у каждого донора с такими.

    Из ссылок обхода берутся только нужные: на домены тех, кому пишем, и через сети
    партнёрок — сеть называет рекламодателя в адресе, а не в домене ссылки.
    """
    chosen = (
        await session.execute(
            select(
                CandidateModel.crawl_run_id, CandidateModel.donor_host, CandidateModel.target_root
            ).where(writes_to())
        )
    ).all()
    if not chosen:
        return {}
    direct = (
        select(OutLinkModel.crawl_run_id, *_LINK_COLUMNS)
        .join(
            CandidateModel,
            (CandidateModel.crawl_run_id == OutLinkModel.crawl_run_id)
            & (CandidateModel.target_root == OutLinkModel.target_root),
        )
        .where(writes_to())
    )
    through = select(OutLinkModel.crawl_run_id, *_LINK_COLUMNS).where(
        OutLinkModel.crawl_run_id.in_(sorted({run for run, _, _ in chosen})),
        OutLinkModel.target_root.in_(sorted(NETWORK_ROOTS)),
    )
    links: defaultdict[int, list[OutLink]] = defaultdict(list)
    for run, *values in await session.execute(union_all(direct, through)):
        links[run].append(OutLink(*values))
    # Разбор адресов — мимо цикла событий: у донора-витрины ссылок бывают тысячи.
    return await asyncio.to_thread(
        _carrying, [(run, donor, root) for run, donor, root in chosen], links
    )


def _carrying(
    chosen: Sequence[tuple[int, str, str]], links: dict[int, list[OutLink]]
) -> dict[str, int]:
    """Сколько разных страниц донора несут ссылку хоть на одного из `chosen` — его
    рекламодателей, которым пишем, по обходу, у которого они кандидаты."""
    advertisers: defaultdict[tuple[str, int], set[str]] = defaultdict(set)
    for run, donor, root in chosen:
        advertisers[(donor, run)].add(root)
    pages: defaultdict[str, set[str]] = defaultdict(set)
    for (donor, run), roots in advertisers.items():
        found = advertiser_pages(links.get(run, []))
        for root in roots:
            pages[donor] |= found.get(root, frozenset())
    return {donor: len(urls) for donor, urls in pages.items()}
