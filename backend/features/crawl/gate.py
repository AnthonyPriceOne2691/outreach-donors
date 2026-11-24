"""Гейт: ссылки обхода становятся кандидатами с вердиктом.

Порядок один на консоль, задачу очереди и будущий экран — иначе у кнопки
и у команды разойдутся правила, и разойдутся они молча.

**Кандидаты пишутся все, включая отсеянных.** Домен из списка «кому
не пишем» и домен с одним баллом — разные ответы, и оба нужны: по первому
видно, что список работает, по второму — где порог. Записав только тех,
кому пишем, мы получили бы базу, по которой скоринг всегда прав.

**Усилитель считается по базе, а не по обходу.** Признак «домен
встречается у двух наших доноров» внутри одного обхода не вычисляется
никак: донор там всегда один.

**«DR > 80 — не пишем» — здесь же** (`big_sites.py`): DR спрашивается
у провайдера только для «куплено» и «спорно» и только тот, которого
ещё не знаем. Без источника DR (`ratings=None`) вердикт не трогается,
а в причинах так и написано.

**Кандидаты донора — по его последнему пригодному обходу**, а не сумма всех:
обход стал кнопкой, и у донора их бывает несколько. Новый обход заменяет
кандидатов прежнего. Обход без открытых страниц (закрылся, не начался)
и незаконченный не заменяет ничего: он не знает о сайте ничего нового.
**Кроме цепочки обхода 24/7** (`always.py`, 17.11.2026): её части копятся — кандидаты считаются по
последнему обходу не из цепочки и частям цепочки, страница — у самого нового обхода, где она
есть (`gate_union.py`). Иначе часть стирала бы находки ночи, а ночь — находки десятков тысяч
страниц цепочки. Кандидат помнит обход своей лучшей ссылки, а не пересчитанный. Части, которые не
влезли под потолок ссылок в памяти, заморожены: их кандидаты остаются, пока тот же домен не найдут
обходы новее.

**Решения человека и спрошенный DR — ещё и в памяти донора по паре «донор +
домен»** (`CandidateMemoryModel`, решение владельца 28.10.2026). «Переобойти»
стоит у последнего обхода каждого донора, и повтор стал обычным делом: сайт
растёт, карта отдаёт адреса от новых к старым, потолок в 1 000 страниц срезает
старые. Пока решение и DR жили только на строке кандидата, повтор, не нашедший
домена, стирал их вместе с ней, а домен, найденный снова новой статьёй,
возвращался без решения: «куплено» заводило рекламодателя, которому сказали
«не пишем», и DR покупался второй раз. Теперь пересчёт пишет в память всё, что
переносит, до того как стереть строки (`_remember`), и читает её раньше строк
(`_previous`). Кандидатом память не становится: списки, перевод и проверки
офферов видят только то, что нашёл последний обход.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.ahrefs.client import AhrefsError
from backend.features.core.domain import Verdict
from backend.features.core.models.advertiser import CARRIED, CandidateMemoryModel, CandidateModel
from backend.features.core.models.crawl import CrawlRunModel, OutLinkModel
from backend.features.crawl import always_depth
from backend.features.crawl.big_sites import (
    PartialRatingsError,
    Ratings,
    RatingsRefusedError,
    apply_ratings,
    needs_rating,
)
from backend.features.crawl.gate_union import Union, chain_selling, gather, usable
from backend.features.crawl.links import OutLink
from backend.features.crawl.scoring import (
    Candidate,
    advertiser_links,
    advertiser_pages,
    boost_across_donors,
    score_candidates,
)
from backend.features.crawl.selection_settings import snapshot
from backend.features.crawl.topic import donor_topic

logger = logging.getLogger(__name__)

#: Строк памяти за один запрос: у запроса не больше 32 767 параметров, у строки их семь.
MEMORY_CHUNK = 1000

#: Ключи в `stats` обхода: страниц со ссылкой на домен «куплено» и самих таких ссылок
#: (`selling_stats`). У обхода, посчитанного раньше этих чисел, ключей нет: их дополнит
#: `outreach advertisers --pages-only` (`crawl/selling_backfill.py`) или следующий пересчёт.
SELLING_PAGES = "selling_pages"
SELLING_LINKS = "selling_links"


async def latest_crawls(session: AsyncSession) -> list[CrawlRunModel]:
    """Последний пригодный обход каждого донора — по ним и считаются кандидаты."""
    newest = select(func.max(CrawlRunModel.id)).where(usable()).group_by(CrawlRunModel.host)
    query = select(CrawlRunModel).where(CrawlRunModel.id.in_(newest)).order_by(CrawlRunModel.id)
    return list((await session.execute(query)).scalars().all())


async def _not_latest(session: AsyncSession, run: CrawlRunModel) -> str | None:
    """Почему по этому обходу кандидатов не считаем. `None` — он последний пригодный."""
    query = select(func.max(CrawlRunModel.id)).where(usable(), CrawlRunModel.host == run.host)
    latest = (await session.execute(query)).scalar_one_or_none()
    if latest == run.id:
        return None
    if latest is not None and latest > run.id:
        return f"у донора есть обход новее (№{latest})"
    return "обход не закончен или без открытых страниц"


def _to_link(row: OutLinkModel) -> OutLink:
    """Строка таблицы обратно в ссылку. Скоринг читает одно и то же,
    откуда бы ссылки ни пришли — из свежего обхода или из базы."""
    return OutLink(
        page_url=row.page_url,
        url=row.url,
        target_host=row.target_host,
        target_root=row.target_root,
        anchor=row.anchor,
        anchor_key=row.anchor_key,
        nofollow=row.nofollow,
        sponsored=row.sponsored,
        ugc=row.ugc,
        root_guessed=row.root_guessed,
        in_body=row.in_body,
        page_label=row.page_label,
        page_published=row.page_published,
    )


async def donors_per_root(session: AsyncSession, roots: list[str]) -> dict[str, int]:
    """Сколько РАЗНЫХ наших доноров ссылается на каждый домен.

    Считается по донору, а не по строкам: сто ссылок с одного донора —
    это один донор, и усилитель требования говорит именно про двух
    разных.
    """
    if not roots:
        return {}
    query = (
        select(OutLinkModel.target_root, func.count(func.distinct(CrawlRunModel.host)))
        .join(CrawlRunModel, CrawlRunModel.id == OutLinkModel.crawl_run_id)
        .where(OutLinkModel.target_root.in_(roots), usable())
        .group_by(OutLinkModel.target_root)
    )
    return dict((await session.execute(query)).all())  # type: ignore[arg-type]


@dataclass(slots=True)
class _Previous:
    """Что переносится через пересчёт и повтор обхода: решения человека и спрошенный DR."""

    decisions: dict[str, tuple[bool | None, str | None, datetime | None]] = field(
        default_factory=dict
    )
    ratings: dict[str, int | None] = field(default_factory=dict)
    rated_at: dict[str, datetime] = field(default_factory=dict)

    def take(self, row: CandidateModel | CandidateMemoryModel) -> None:
        """Решение и DR строки — поверх прочитанных раньше."""
        if row.confirmed is not None:
            self.decisions[row.target_root] = (row.confirmed, row.decided_by, row.decided_at)
        if row.dr_checked_at is not None:
            self.ratings[row.target_root] = row.dr
            self.rated_at[row.target_root] = row.dr_checked_at

    def roots(self) -> list[str]:
        """Домены, которым есть что переносить."""
        return sorted(set(self.decisions) | set(self.rated_at))

    def carried(self, root: str) -> dict[str, Any]:
        """Решение и DR домена — колонками кандидата и памяти (`CARRIED`)."""
        confirmed, decided_by, decided_at = self.decisions.get(root, (None, None, None))
        return {
            "confirmed": confirmed,
            "decided_by": decided_by,
            "decided_at": decided_at,
            "dr": self.ratings.get(root),
            "dr_checked_at": self.rated_at.get(root),
        }


async def _previous(session: AsyncSession, host: str) -> _Previous:
    """Решения и DR донора: из памяти, а поверх — со строк кандидатов всех его обходов.

    Строка сильнее памяти: решение могли переменить после пересчёта, который память
    писал (`review.decide`, `force`). В памяти — и домены, которых последний обход
    не нашёл: решение и оплаченный DR вернутся к ним, когда следующий найдёт.
    """
    found = _Previous()
    # Память пишется мимо сессии (`_remember`, ON CONFLICT): строка памяти, которую сессия
    # уже держит (два пересчёта в одной сессии), без `populate_existing` осталась бы прежней.
    remembered = await session.scalars(
        select(CandidateMemoryModel)
        .where(CandidateMemoryModel.donor_host == host)
        .execution_options(populate_existing=True)
    )
    for memory in remembered.all():
        found.take(memory)
    rows = await session.scalars(
        select(CandidateModel)
        .where(CandidateModel.donor_host == host)
        .order_by(CandidateModel.crawl_run_id, CandidateModel.id)
    )
    for row in rows.all():
        found.take(row)
    return found


async def _rate(candidates: list[Candidate], previous: _Previous, ratings: Ratings | None) -> None:
    """DR тем, кому он нужен: из прошлых пересчётов, а недостающий — у провайдера.

    Отказ провайдера не роняет пересчёт: вердикты остаются, а в причинах —
    «DR не проверен». Повтор — следующим пересчётом, юниты за уже
    спрошенное не тратятся. Отказ капа (`RatingsRefusedError`) — так же,
    но с причиной в словах кандидата: кап — не поломка провайдера.
    Пакет упал, когда прежние были уже оплачены (`PartialRatingsError`), —
    оплаченное сохраняется: выброшенный ответ следующий пересчёт купил бы снова.
    """
    asked = sorted({c.target_root for c in candidates if needs_rating(c)} - set(previous.ratings))
    fresh: dict[str, int | None] = {}
    refused: str | None = None
    if asked and ratings is not None:
        fresh, refused = await _ask(ratings, asked)
    if fresh:
        now = datetime.now(UTC)
        previous.ratings.update(fresh)
        previous.rated_at.update(dict.fromkeys(fresh, now))
    blocked = apply_ratings(candidates, previous.ratings, refused=refused)
    if blocked:
        logger.info("кандидаты: крупных сайтов (DR > 80) отсеяно %s", blocked)


async def _ask(ratings: Ratings, asked: list[str]) -> tuple[dict[str, int | None], str | None]:
    """DR у провайдера — и почему его не спросили (отказ капа словами), или `None`.

    Отказ не роняет пересчёт. Пакет упал, когда прежние были уже оплачены
    (`PartialRatingsError`), — оплаченное возвращается: выброшенный ответ
    следующий пересчёт купил бы снова (24.11.2026)."""
    try:
        return await ratings(asked), None
    except RatingsRefusedError as exc:
        logger.warning("DR кандидатов не спрошен (%s доменов): %s", len(asked), exc)
        return {}, str(exc)
    except PartialRatingsError as exc:
        found = len(exc.found)
        logger.warning("DR кандидатов получен не весь (%s из %s): %s", found, len(asked), exc)
        return exc.found, None
    except AhrefsError as exc:
        logger.warning("DR кандидатов не получен (%s доменов): %s", len(asked), exc)
        return {}, None


def bought_here(links: Sequence[OutLink], roots: set[str]) -> dict[str, int]:
    """Домены «куплено» и сколько их ссылок именно в `links` — тем же счётом, что `links`
    кандидата (`scoring.advertiser_links`): кандидаты считаются по объединению обходов, а мера
    обхода — по его ссылкам."""
    return {root: count for root, count in advertiser_links(links).items() if root in roots}


def selling_stats(links: Sequence[OutLink], bought: Mapping[str, int]) -> dict[str, int]:
    """Продающие ссылки обхода числами для `stats`: `bought` — домен с вердиктом «куплено» и
    сколько у него ссылок. Страницы — разные: витрина с пятью рекламодателями — одна; правилом
    скоринга (`advertiser_pages`): сеть партнёрок — к рекламодателю, документы не в счёт. Это мера
    обхода, как и вердикт: решение человека чисел не меняет. Оба числа — на обходе, а не на
    кандидатах: кандидатов заменит следующий обход, а итог этого должен остаться."""
    pages = advertiser_pages(links)
    found: set[str] = set()
    for root in bought:
        found |= pages.get(root, frozenset())
    return {SELLING_PAGES: len(found), SELLING_LINKS: sum(bought.values())}


async def judge_run(
    session: AsyncSession, run_id: int, *, ratings: Ratings | None = None
) -> list[Candidate]:
    """Посчитать кандидатов по сохранённому обходу и записать вердикты.

    Пересчёт по существующей записи, а не по свежему обходу: веса
    скоринга будут меняться, и прогонять ради этого чужие сайты заново
    незачем — ссылки уже лежат в базе.

    `ratings` — откуда брать DR для «DR > 80 — не пишем»; `None` — не
    спрашивать (тесты, пересчёт без ключа): вердикт не трогается.
    """
    run = await session.get(CrawlRunModel, run_id)
    if run is None:
        raise ValueError(f"обхода {run_id} нет в базе")
    skip = await _not_latest(session, run)
    if skip is not None:
        logger.info("обход %s (%s): кандидаты не считаются — %s", run.host, run_id, skip)
        return []

    found = await gather(session, run)
    candidates = score_candidates(
        found.links,
        donor_root=run.host,
        topic=await donor_topic(session, run.host),
    )
    seen = await donors_per_root(session, [c.target_root for c in candidates])
    boost_across_donors(candidates, seen)

    previous = await _previous(session, run.host)
    await _rate(candidates, previous, ratings)
    await _replace_candidates(
        session, run, candidates, previous, owner=found.owner, frozen=found.frozen
    )
    await _measure(session, run, candidates, found)
    await session.flush()
    logger.info(
        "обход %s (%s): кандидатов %s, из них куплено %s, спорных %s",
        run.host,
        run_id,
        len(candidates),
        sum(1 for c in candidates if c.verdict.value == "bought"),
        sum(1 for c in candidates if c.verdict.value == "pending"),
    )
    return candidates


async def _measure(
    session: AsyncSession, run: CrawlRunModel, candidates: list[Candidate], found: Union
) -> None:
    """Каким порогом судили и продающие ссылки — на строку обхода; доля продающих страниц — цепочке.

    Порог — потому что веса меняются окружением, а пересчёт переписывает вердикт. Продающие ссылки —
    здесь, после «DR > 80», который снимает «куплено», и там, где ссылки уже в памяти: панели читать
    их из `outlinks` на каждое открытие значило бы проходить сотни тысяч строк. Мера обычного обхода —
    по его ссылкам, вердикт — по объединению: «куплено» одно на донора. Цепочка меряется запросом по
    всем своим частям (`gate_union.chain_selling`), без ссылок в памяти; у строки части страницы — всей
    цепочки (`always_part.py`), и продающие у неё — те же числа цепочки, иначе доля «Качества загрузок»
    делила бы страницы части на страницы цепочки."""
    roots = {c.target_root for c in candidates if c.verdict is Verdict.BOUGHT}
    stats = selling_stats(found.own, bought_here(found.own, roots))
    if found.chained:
        pages, links = await chain_selling(session, run.host)
        await always_depth.refresh_yield(session, run.host, pages, frozen=len(found.frozen))
        if run.depth_part is not None:
            stats = {SELLING_PAGES: pages, SELLING_LINKS: links}
    run.stats = {**(run.stats or {}), "scored_with": snapshot(), **stats}


def _replaced(
    rows: Sequence[CandidateModel], candidates: list[Candidate], frozen: Collection[int]
) -> list[CandidateModel]:
    """Строки, которые пересчёт заменяет: все, кроме кандидатов замороженных частей, чей домен не
    найден заново (`_replace_candidates`)."""
    fresh = {candidate.target_root for candidate in candidates}
    return [row for row in rows if row.crawl_run_id not in frozen or row.target_root in fresh]


def _found_in(candidate: Candidate, owner: Mapping[str, int], judged: int) -> int:
    """Обход лучшей ссылки кандидата — по странице (`gate_union.Union.owner`); иначе судимый."""
    best = candidate.best_link
    return judged if best is None else owner.get(best.page_url, judged)


async def _replace_candidates(
    session: AsyncSession,
    run: CrawlRunModel,
    candidates: list[Candidate],
    previous: _Previous,
    *,
    owner: Mapping[str, int] | None = None,
    frozen: Collection[int] = (),
) -> None:
    """Переписать кандидатов донора целиком — прежних обходов тоже.

    Пересчёт с новыми весами обязан заменить прежний вердикт, а не лечь
    рядом: две строки про один домен с разными баллами — это вопрос
    «какая из них правда», на который никто не ответит.

    Решение человека при этом переносится: его подтверждение сильнее
    любого веса и теряться при пересчёте не должно. Спрошенный DR —
    тоже: за него заплачено. Домену, которого новый обход не нашёл, их
    хранит память донора (`_remember`) — он вернётся с ними.

    Строка кандидата помнит обход своей лучшей ссылки (`owner`: страница → обход, `gate_union`):
    по нему судья и приёмка находят её в `outlinks`. Без объединения — это судимый обход.

    Кандидат замороженной части (`frozen`: её ссылки не читались под потолком памяти) остаётся как
    был — решение, DR и вердикт на нём, — пока тот же домен не найден заново: тогда его заменяет
    новая строка.
    """
    await _remember(session, run.host, previous)
    existing = (
        (await session.execute(select(CandidateModel).where(CandidateModel.donor_host == run.host)))
        .scalars()
        .all()
    )
    for row in _replaced(existing, candidates, frozen):
        await session.delete(row)
    await session.flush()

    for candidate in candidates:
        session.add(
            CandidateModel(
                crawl_run_id=_found_in(candidate, owner or {}, run.id),
                donor_host=run.host,
                target_root=candidate.target_root,
                points=candidate.points,
                verdict=candidate.verdict,
                reasons=candidate.reasons,
                links=candidate.links,
                pages=candidate.pages,
                best_page_url=candidate.best_link.page_url if candidate.best_link else None,
                best_anchor=candidate.best_link.anchor if candidate.best_link else None,
                **previous.carried(candidate.target_root),
            )
        )
    await session.flush()


async def _remember(session: AsyncSession, host: str, previous: _Previous) -> None:
    """Решения и DR донора — в память, до того как строки кандидатов сотрут.

    Пишется всё, что пересчёт переносит, а не только домены, которых новый обход
    не нашёл: один путь записи, без учёта «ушёл — вернулся». Копия у нынешней строки
    ничего не стоит — память читает один `_previous`, и строку он ставит поверх неё.
    По той же причине запись не удаляется, когда домен вернулся. Решение, переменённое
    после пересчёта, доходит до памяти следующим: он читает строку, а пишет прочитанное.
    """
    rows = [
        {"donor_host": host, "target_root": root, **previous.carried(root)}
        for root in previous.roots()
    ]
    for start in range(0, len(rows), MEMORY_CHUNK):
        statement = insert(CandidateMemoryModel).values(rows[start : start + MEMORY_CHUNK])
        fresh = {name: statement.excluded[name] for name in CARRIED}
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=["donor_host", "target_root"],
                set_={**fresh, "updated_at": func.now()},
            )
        )
