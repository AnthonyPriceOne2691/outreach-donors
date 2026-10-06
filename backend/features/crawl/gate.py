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
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.ahrefs.client import AhrefsError
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.crawl import CrawlRunModel, OutLinkModel
from backend.features.crawl.big_sites import Ratings, apply_ratings, needs_rating
from backend.features.crawl.links import OutLink
from backend.features.crawl.scoring import Candidate, boost_across_donors, score_candidates

logger = logging.getLogger(__name__)


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
        .where(OutLinkModel.target_root.in_(roots))
        .group_by(OutLinkModel.target_root)
    )
    return dict((await session.execute(query)).all())  # type: ignore[arg-type]


@dataclass(slots=True)
class _Previous:
    """Что переносится через пересчёт: решения человека и спрошенный DR."""

    decisions: dict[str, tuple[bool | None, str | None, datetime | None]] = field(
        default_factory=dict
    )
    ratings: dict[str, int | None] = field(default_factory=dict)
    rated_at: dict[str, datetime] = field(default_factory=dict)


async def _previous(session: AsyncSession, run_id: int) -> _Previous:
    rows = (
        (await session.execute(select(CandidateModel).where(CandidateModel.crawl_run_id == run_id)))
        .scalars()
        .all()
    )
    found = _Previous()
    for row in rows:
        if row.confirmed is not None:
            found.decisions[row.target_root] = (row.confirmed, row.decided_by, row.decided_at)
        if row.dr_checked_at is not None:
            found.ratings[row.target_root] = row.dr
            found.rated_at[row.target_root] = row.dr_checked_at
    return found


async def _rate(candidates: list[Candidate], previous: _Previous, ratings: Ratings | None) -> None:
    """DR тем, кому он нужен: из прошлых пересчётов, а недостающий — у провайдера.

    Отказ провайдера не роняет пересчёт: вердикты остаются, а в причинах —
    «DR не проверен». Повтор — следующим пересчётом, юниты за уже
    спрошенное не тратятся.
    """
    asked = sorted({c.target_root for c in candidates if needs_rating(c)} - set(previous.ratings))
    if asked and ratings is not None:
        try:
            fresh = await ratings(asked)
        except AhrefsError as exc:
            logger.warning("DR кандидатов не получен (%s доменов): %s", len(asked), exc)
        else:
            now = datetime.now(UTC)
            previous.ratings.update(fresh)
            previous.rated_at.update(dict.fromkeys(fresh, now))
    blocked = apply_ratings(candidates, previous.ratings)
    if blocked:
        logger.info("кандидаты: крупных сайтов (DR > 80) отсеяно %s", blocked)


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

    rows = (
        (await session.execute(select(OutLinkModel).where(OutLinkModel.crawl_run_id == run_id)))
        .scalars()
        .all()
    )
    candidates = score_candidates([_to_link(row) for row in rows], donor_root=run.host)
    seen = await donors_per_root(session, [c.target_root for c in candidates])
    boost_across_donors(candidates, seen)

    previous = await _previous(session, run.id)
    await _rate(candidates, previous, ratings)
    await _replace_candidates(session, run, candidates, previous)
    logger.info(
        "обход %s (%s): кандидатов %s, из них куплено %s, спорных %s",
        run.host,
        run_id,
        len(candidates),
        sum(1 for c in candidates if c.verdict.value == "bought"),
        sum(1 for c in candidates if c.verdict.value == "pending"),
    )
    return candidates


async def _replace_candidates(
    session: AsyncSession, run: CrawlRunModel, candidates: list[Candidate], previous: _Previous
) -> None:
    """Переписать кандидатов обхода целиком.

    Пересчёт с новыми весами обязан заменить прежний вердикт, а не лечь
    рядом: две строки про один домен с разными баллами — это вопрос
    «какая из них правда», на который никто не ответит.

    Решение человека при этом переносится: его подтверждение сильнее
    любого веса и теряться при пересчёте не должно. Спрошенный DR —
    тоже: за него заплачено.
    """
    existing = (
        (await session.execute(select(CandidateModel).where(CandidateModel.crawl_run_id == run.id)))
        .scalars()
        .all()
    )
    for row in existing:
        await session.delete(row)
    await session.flush()

    for candidate in candidates:
        root = candidate.target_root
        confirmed, decided_by, decided_at = previous.decisions.get(root, (None, None, None))
        session.add(
            CandidateModel(
                crawl_run_id=run.id,
                donor_host=run.host,
                target_root=candidate.target_root,
                points=candidate.points,
                verdict=candidate.verdict,
                reasons=candidate.reasons,
                links=candidate.links,
                pages=candidate.pages,
                best_page_url=candidate.best_link.page_url if candidate.best_link else None,
                best_anchor=candidate.best_link.anchor if candidate.best_link else None,
                confirmed=confirmed,
                decided_by=decided_by,
                decided_at=decided_at,
                dr=previous.ratings.get(root),
                dr_checked_at=previous.rated_at.get(root),
            )
        )
    await session.flush()
