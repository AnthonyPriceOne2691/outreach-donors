"""Ручная проверка пограничных кандидатов: очередь и решение.

Порядок работы живёт здесь, а не в маршруте и не в команде. Экран
и консоль делают одно и то же, и когда правила разойдутся — а они
разойдутся, — разойдутся они молча.

**Экран нужен не для удобства.** Требование ограничивает долю ложных
рекламодателей десятью процентами, а скоринг выносит вердикт по пяти
признакам, два из которых выведены из замера на одной нише. Без
человека, который смотрит пограничные, эта доля не измерена и не
удержана.

**Решение человека сильнее вердикта скоринга и не переписывает его.**
Балл остаётся как был: по расхождению между ним и решением и считается,
как часто скоринг ошибается. Перезаписав балл, мы получили бы базу,
по которой он всегда прав.

**«Не пишем» доходит до рекламодателя** (`promote.withdraw`, сверка ТЗ 11.10.2026): сборка
и отправка смотрят на строку рекламодателя, а не на кандидата, и решение,
записанное одному кандидату, оффер ложному рекламодателю не останавливало.

**Очередь — страницами и с фильтрами под колонками** (слово Anthony 05.11.2026): сервер отдавал
только первые полсотни, и остальных человек не видел вовсе. Порядок сквозной и держит замер
доли ложных (`crawl/sample.py`): сначала — лёгкие строки всей очереди, номер, балл и причины, по
ним порядок, фильтр и счётчики; карточки читаются только для строк страницы. Признак скоринга
разбирается из причин питоном (`strata.features_of`), а не условием базы: правило одно на очередь
и на разбивку выборки.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Select, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import ReplyKind, Stage, Verdict
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.crawl import OutLinkModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import CampaignModel, ReplyModel, ThreadModel
from backend.features.crawl.promote import Withdrawn, withdraw
from backend.features.crawl.sample import in_sample, mixed
from backend.features.crawl.strata import FEATURES, OTHER, features_of

logger = logging.getLogger(__name__)

#: Сколько кандидатов на странице очереди: её читают глазами, причины балла — строками, и десять
#: строк уже занимают экран (слово Anthony 05.11.2026). Размер называет сервер.
PAGE_SIZE = 10
#: Больше за раз не отдаётся: без границы ответ рос бы вместе с базой.
MAX_PAGE_SIZE = 200


class UnknownCandidateError(ValueError):
    """Кандидата нет. Отдельный тип: маршрут отвечает 404, а не 500."""


class AlreadyDecidedError(ValueError):
    """Решение по кандидату уже принято.

    Отказ, а не тихая перезапись: два человека, открывшие одну очередь,
    иначе затрут решения друг друга и не узнают об этом. Передумать
    можно — но явно, а не случайным вторым нажатием.
    """


#: Какие вердикты человек смотрит списком: спорные — решить, «куплено» —
#: проверить перед письмом и снять ложного («Не пишем»).
REVIEWED = (Verdict.PENDING, Verdict.BOUGHT)


@dataclass(frozen=True, slots=True)
class QueueLine:
    """Строка очереди до карточки: номер, балл и признаки скоринга."""

    id: int
    points: int
    features: frozenset[str]


@dataclass(frozen=True, slots=True)
class QueueFilter:
    """Чем сужают очередь: колонки «Балл» и «За что». Пусто — не сужать."""

    #: Балл — точным значением.
    points: int | None = None
    #: Ключ признака скоринга (`strata.FEATURES`). Незнакомого нет ни у кого: список пуст.
    feature: str | None = None

    def keeps(self, line: QueueLine) -> bool:
        return (self.points is None or line.points == self.points) and (
            self.feature is None or self.feature in line.features
        )


#: Фильтры не выбраны.
NOT_NARROWED = QueueFilter()


@dataclass(frozen=True, slots=True)
class FacetChoice:
    """Вариант фильтра: значение, слова экрана и сколько строк с ним."""

    key: str
    title: str
    count: int


@dataclass(frozen=True, slots=True)
class Facet:
    """Варианты одного фильтра. `total` — сколько строк, когда он не сужает («все»)."""

    total: int
    choices: list[FacetChoice]


@dataclass(frozen=True, slots=True)
class QueueFacets:
    """Счётчики под фильтрами. `listed` — строк списка без фильтров: «найдено 3 из 40»."""

    listed: int
    points: Facet
    features: Facet


@dataclass(frozen=True, slots=True)
class QueuePage:
    rows: list[CandidateModel]
    #: Сколько строк под фильтром на всех страницах.
    total: int
    facets: QueueFacets


def _lines(rows: Iterable[Any]) -> list[QueueLine]:
    return [
        QueueLine(row.id, row.points, frozenset(one.key for one in features_of(row.reasons)))
        for row in rows
    ]


async def _queue_lines(
    session: AsyncSession, base: Select[Any]
) -> tuple[list[QueueLine], list[QueueLine]]:
    """Вся очередь лёгкими строками: сильные — по баллу вниз, выборка — в порядке хэша, тем же,
    что у доли (`accuracy.sample_turns`). Порядок полный, до номера строки."""
    strong = await session.execute(
        base.where(~in_sample()).order_by(CandidateModel.points.desc(), CandidateModel.id)
    )
    sample = await session.execute(
        base.where(in_sample()).order_by(func.md5(CandidateModel.target_root), CandidateModel.id)
    )
    return _lines(strong.all()), _lines(sample.all())


def _points_facet(lines: list[QueueLine], chosen: QueueFilter) -> Facet:
    under = [line for line in lines if replace(chosen, points=None).keeps(line)]
    counted = Counter(line.points for line in under)
    # По числу, а не по тексту: балл «12» — после «3».
    known = sorted({line.points for line in lines})
    return Facet(
        len(under), [FacetChoice(str(points), str(points), counted[points]) for points in known]
    )


def _features_facet(lines: list[QueueLine], chosen: QueueFilter) -> Facet:
    under = [line for line in lines if replace(chosen, feature=None).keeps(line)]
    counted = Counter(key for line in under for key in line.features)
    known = {key for line in lines for key in line.features}
    return Facet(
        len(under),
        [
            FacetChoice(one.key, one.title, counted[one.key])
            for one in (*FEATURES, OTHER)
            if one.key in known
        ],
    )


def _facets(lines: list[QueueLine], chosen: QueueFilter) -> QueueFacets:
    """Числа вариантов — со всеми условиями, кроме своего, как у счётчиков обхода доноров: выбранный
    балл не обнуляет соседние баллы. Варианты — что есть в списке без фильтров: вариант с нулём под
    соседним фильтром не пропадает, а чего в списке нет вовсе, фильтр не предлагает."""
    return QueueFacets(
        listed=len(lines),
        points=_points_facet(lines, chosen),
        features=_features_facet(lines, chosen),
    )


async def _cards(session: AsyncSession, ids: list[int]) -> list[CandidateModel]:
    """Кандидаты по номерам — в их порядке. Обход мог заменить строку между запросами: её нет."""
    found = await session.scalars(select(CandidateModel).where(CandidateModel.id.in_(ids)))
    by_id = {row.id: row for row in found.all()}
    return [by_id[one] for one in ids if one in by_id]


async def queue(
    session: AsyncSession,
    *,
    page: int = 1,
    limit: int = PAGE_SIZE,
    include_decided: bool = False,
    verdict: Verdict = Verdict.PENDING,
    chosen: QueueFilter = NOT_NARROWED,
) -> QueuePage:
    """Страница очереди ручной проверки (по умолчанию — спорных, которых ещё никто не смотрел).

    Сильные — по баллу вниз: ошибка на них дороже; каждая пятая строка — случайная выборка
    (`sample.mixed`). Фильтр сужает обе части, и замеру это не мешает: решённое вне порядка
    выборки в долю не идёт (`accuracy.in_turn`). Страница за концом — пустая, а не отказ: число
    страниц экран узнаёт по `total`.
    """
    base = select(CandidateModel.id, CandidateModel.points, CandidateModel.reasons).where(
        CandidateModel.verdict == verdict
    )
    if not include_decided:
        base = base.where(CandidateModel.confirmed.is_(None))
    strong, sample = await _queue_lines(session, base)
    order = mixed(
        [line for line in strong if chosen.keeps(line)],
        [line for line in sample if chosen.keeps(line)],
    )
    shown = order[(page - 1) * limit : page * limit]
    return QueuePage(
        rows=await _cards(session, [line.id for line in shown]),
        total=len(order),
        facets=_facets([*strong, *sample], chosen),
    )


async def article_dates(session: AsyncSession, rows: list[CandidateModel]) -> dict[int, date]:
    """Когда вышла статья, под которую будет письмо: у кандидата — страница,
    дата — у ссылок этой страницы в обходе. Свежая статья — живое размещение,
    статья 2012 года — давно забытое."""
    pairs = [(row.crawl_run_id, row.best_page_url) for row in rows if row.best_page_url]
    if not pairs:
        return {}
    found = (
        await session.execute(
            select(
                OutLinkModel.crawl_run_id,
                OutLinkModel.page_url,
                func.max(OutLinkModel.page_published),
            )
            .where(tuple_(OutLinkModel.crawl_run_id, OutLinkModel.page_url).in_(pairs))
            .group_by(OutLinkModel.crawl_run_id, OutLinkModel.page_url)
        )
    ).all()
    dated = {(run_id, page): day for run_id, page, day in found if day is not None}
    return {
        row.id: dated[(row.crawl_run_id, row.best_page_url)]
        for row in rows
        if (row.crawl_run_id, row.best_page_url) in dated
    }


async def waiting(session: AsyncSession) -> int:
    """Сколько пограничных ждёт человека. Число с главной."""
    query = (
        select(func.count())
        .select_from(CandidateModel)
        .where(CandidateModel.verdict == Verdict.PENDING, CandidateModel.confirmed.is_(None))
    )
    return int((await session.execute(query)).scalar_one())


async def counts(session: AsyncSession) -> dict[str, int]:
    """Сколько кандидатов с каждым вердиктом. Для шапки экрана.

    Отсеянные показываются наравне с принятыми намеренно: по ним видно,
    что список «кому не пишем» работает, а не молчит.
    """
    rows = (
        await session.execute(
            select(CandidateModel.verdict, func.count()).group_by(CandidateModel.verdict)
        )
    ).all()
    return {verdict.value: number for verdict, number in rows}


@dataclass(frozen=True, slots=True)
class Decided:
    """Решение по кандидату и что оно сделало с рекламодателем его домена."""

    candidate: CandidateModel
    withdrawn: Withdrawn = field(default_factory=Withdrawn)
    #: «Не пишем», а рекламодатель уже ответил: переписку решение не останавливает —
    #: снимает оффер и добивки, а разговор ведут в «Диалогах». Экран говорит это вслух.
    answered: bool = False

    @property
    def warning(self) -> str | None:
        """Что сказать человеку рядом с решением. Пусто — нечего."""
        if not self.answered:
            return None
        return (
            f"{self.candidate.target_root} уже ответил — переписка идёт в «Диалогах», "
            "и «не пишем» её не останавливает: сняты только оффер и добивки"
        )


async def decide(
    session: AsyncSession,
    candidate_id: int,
    *,
    confirmed: bool,
    by: str,
    force: bool = False,
) -> Decided:
    """Записать решение человека по кандидату. Экран и консоль — этим путём.

    `force` нужен, чтобы передумать: без него повторное решение —
    отказ, потому что два человека в одной очереди затрут друг друга
    и не заметят. «Не пишем» снимает рекламодателя домена, если других
    подходящих кандидатов у него нет, и его письма (`promote.withdraw`).
    """
    row = await session.get(CandidateModel, candidate_id)
    if row is None:
        raise UnknownCandidateError(f"кандидата {candidate_id} нет")
    if row.confirmed is not None and not force:
        raise AlreadyDecidedError(
            f"{row.target_root}: решение уже принято ({row.decided_by}) — "
            "чтобы изменить его, нужно сказать об этом явно"
        )

    row.confirmed = confirmed
    row.decided_by = by
    row.decided_at = datetime.now(UTC)
    withdrawn = Withdrawn() if confirmed else await withdraw(session, row)
    answered = not confirmed and await _answered(session, row.target_root)
    logger.info(
        "кандидат %s (%s): %s, решил %s; балл скоринга %s остался прежним; %s",
        row.target_root,
        row.donor_host,
        "подтверждён" if confirmed else "отклонён",
        by,
        row.points,
        withdrawn.as_details() or "рекламодателя не тронуло",
    )
    return Decided(row, withdrawn, answered)


async def _answered(session: AsyncSession, root: str) -> bool:
    """Рекламодатель ответил человеком в переписке Этапа 2 — разговор идёт."""
    found = await session.scalar(
        select(ReplyModel.id)
        .join(ThreadModel, ThreadModel.id == ReplyModel.thread_id)
        .join(CampaignModel, CampaignModel.id == ThreadModel.campaign_id)
        .join(DomainModel, DomainModel.id == ThreadModel.domain_id)
        .where(
            DomainModel.host == root,
            CampaignModel.stage == Stage.ADVERTISERS,
            ReplyModel.kind == ReplyKind.HUMAN,
        )
        .limit(1)
    )
    return found is not None
