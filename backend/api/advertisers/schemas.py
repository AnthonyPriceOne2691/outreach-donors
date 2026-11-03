"""Кандидаты в рекламодатели: что уходит на экран."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from backend.features.core.domain import Verdict
from backend.features.core.models.advertiser import CandidateModel
from backend.features.crawl.accuracy import FALSE_LIMIT, Decisions, ScoringCheck, Share, Within
from backend.features.crawl.niche import NicheRow


class CandidateCard(BaseModel):
    """Один кандидат: балл, вердикт и всё, по чему человек решает.

    Причины приходят строками, а не кодами: человек читает их глазами,
    и превращать «ссылки с 20 страниц донора +3» в ключ ради
    аккуратности значит переводить их обратно на экране.
    """

    id: int
    donor_host: str
    target_root: str
    points: int
    verdict: Verdict
    reasons: list[str] = Field(default_factory=list)
    links: int
    pages: int
    #: Страница и анкор, под которые будет написано письмо.
    best_page_url: str | None = None
    best_anchor: str | None = None
    #: Когда вышла эта статья: свежее размещение — живое, давнее — забытое.
    best_published: date | None = None
    confirmed: bool | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    #: Ответ на решение: что сказать человеку рядом с ним — «рекламодатель уже ответил».
    warning: str | None = None

    @classmethod
    def of(cls, row: CandidateModel, *, published: date | None = None) -> CandidateCard:
        return cls(
            id=row.id,
            donor_host=row.donor_host,
            target_root=row.target_root,
            points=row.points,
            verdict=row.verdict,
            reasons=[str(reason) for reason in row.reasons or []],
            links=row.links,
            pages=row.pages,
            best_page_url=row.best_page_url,
            best_anchor=row.best_anchor,
            best_published=published,
            confirmed=row.confirmed,
            decided_by=row.decided_by,
            decided_at=row.decided_at,
        )


class VerdictDecisions(BaseModel):
    """Кандидаты одного вердикта и решения человека по ним: «пишем» и «не пишем»."""

    total: int
    decided: int
    write: int
    skip: int

    @classmethod
    def of(cls, decisions: Decisions) -> VerdictDecisions:
        return cls(
            total=decisions.total,
            decided=decisions.decided,
            write=decisions.write,
            skip=decisions.skip,
        )


class ShareView(BaseModel):
    """Доля «не пишем» среди решённых строк случайной выборки (`crawl/accuracy.Share`): границы
    и вывод против допуска — от сервера, экран их не пересчитывает. `share`, `low` и `high`
    пусты — решений нет. `to_go` — сколько решений без «не пишем» ещё нужно для «в допуске»;
    пусто, когда вывод уже есть. `left` — нерешённых строк выборки: больше них не решить."""

    total: int
    decided: int
    write: int
    skip: int
    left: int
    share: float | None
    low: float | None
    high: float | None
    within: Within
    to_go: int | None

    @classmethod
    def of(cls, share: Share) -> ShareView:
        decisions = share.decisions
        return cls(
            total=decisions.total,
            decided=decisions.decided,
            write=decisions.write,
            skip=decisions.skip,
            left=decisions.left,
            share=share.share,
            low=share.low,
            high=share.high,
            within=share.within,
            to_go=share.to_go,
        )


class ScoringCheckView(BaseModel):
    """Скоринг против человека (`crawl/accuracy.py`): доля ложных среди решённых «куплено»
    случайной выборки, ошибка базы «писать всем спорным» по выборке спорных и допуск
    требования рядом — экран сравнивает их, а не держит свою копию десяти процентов. Какие
    кандидаты в выборке, ответ не говорит: человек их не отличает."""

    bought: VerdictDecisions
    pending: VerdictDecisions
    #: «Куплено» случайной выборки — доля ложных рекламодателей.
    bought_sample: ShareView
    #: Спорные случайной выборки — доля «не пишем», ошибка базы «писать всем спорным».
    pending_sample: ShareView
    false_limit: float

    @classmethod
    def of(cls, check: ScoringCheck) -> ScoringCheckView:
        return cls(
            bought=VerdictDecisions.of(check.of(Verdict.BOUGHT)),
            pending=VerdictDecisions.of(check.of(Verdict.PENDING)),
            bought_sample=ShareView.of(check.bought),
            pending_sample=ShareView.of(check.disputed),
            false_limit=FALSE_LIMIT,
        )


class CandidatesView(BaseModel):
    """Очередь ручной проверки и что вокруг неё.

    Счётчики по всем вердиктам, а не только по спорным: по отсеянным
    видно, что список «кому не пишем» работает, а не молчит. Рядом — доля
    ложных среди «куплено» (сверка ТЗ 11.10.2026): решения человека, по которым
    она считается, принимаются в этой же очереди.
    """

    rows: list[CandidateCard]
    waiting: int
    counts: dict[str, int] = Field(default_factory=dict)
    scoring: ScoringCheckView | None = None


class DecisionBody(BaseModel):
    """Решение человека по кандидату."""

    #: Это рекламодатель — пишем ему.
    confirmed: bool
    #: Передумать можно, но явно: без этого повторное решение — отказ,
    #: иначе два человека в одной очереди затрут друг друга.
    force: bool = False


class PromotionView(BaseModel):
    """Над кнопкой «Перевести»: сколько переводить и сколько уже заведено."""

    #: Домены среди «куплено» и «пишем» — их и переводит кнопка.
    ready: int
    #: Из них среди рекламодателей ещё нет.
    fresh: int
    advertisers: int
    with_address: int


class PromoteResult(BaseModel):
    """Чем кончился перевод — числами по причинам, как в консоли."""

    report: dict[str, int]
    #: Заведённые впервые — по ним человек узнаёт, что кнопка сработала.
    fresh: list[str] = Field(default_factory=list)
    #: Сколько рекламодателей ждёт адреса после перевода.
    pending: int
    #: Поиск адресов, поставленный самим переводом; `null` — искать некому.
    contacts_job_id: str | None = None


class NicheCard(BaseModel):
    """Бизнес ниши из выдачи прогона: по чему человек решает «пишем / нет».

    `intent_by` — кто сказал «продаёт своё»: `human` или `judge`. `confirmed`
    пусто — ещё не решали.
    """

    id: int
    host: str
    run_id: int | None
    keywords: list[str]
    country: str | None
    quote: str | None
    intent_by: str
    confirmed: bool | None
    decided_by: str | None
    decided_at: datetime | None

    @classmethod
    def of(cls, row: NicheRow) -> NicheCard:
        advertiser = row.advertiser
        decided = advertiser.decided_at is not None
        return cls(
            id=advertiser.id,
            host=row.host,
            run_id=row.run_id,
            keywords=list(row.keywords),
            country=row.country,
            quote=row.quote,
            intent_by=row.decided_by_intent,
            confirmed=advertiser.confirmed_by_human if decided else None,
            decided_by=advertiser.decided_by,
            decided_at=advertiser.decided_at,
        )


class NicheView(BaseModel):
    """Страница бизнесов ниши и сколько из них ждут решения.

    `total` — строк списка по всем страницам, `limit` — размер страницы: его знает
    только сервер, экран по ним считает число страниц (как у очереди форм).
    """

    rows: list[NicheCard]
    waiting: int
    total: int
    page: int
    limit: int


class NicheDecision(BaseModel):
    """«Пишем» (`write: true`) или «не пишем»."""

    write: bool


class NicheCollected(BaseModel):
    """Итог сбора бизнесов ниши из выдачи прогона."""

    run_id: int
    found: int
    added: int
