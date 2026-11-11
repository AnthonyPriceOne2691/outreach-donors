"""Кандидаты в рекламодатели: что уходит на экран."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from backend.features.core.domain import Verdict
from backend.features.core.models.advertiser import CandidateModel
from backend.features.crawl.accuracy import FALSE_LIMIT, Decisions, ScoringCheck, Share, Within
from backend.features.crawl.judge_advice import JudgeAdvice
from backend.features.crawl.judge_mark import batch_of
from backend.features.crawl.niche import NicheRow
from backend.features.crawl.reason_words import shown_reasons
from backend.features.crawl.review import MAX_PAGE_SIZE, PAGE_SIZE, QueueFacets, QueueFilter


class JudgeAdviceView(BaseModel):
    """Совет судьи рекламодателей — только у строки, по которой человек уже решил
    (`crawl/judge_advice.py`): до решения совет тянул бы решение за собой."""

    advice: str
    #: Правило таблицы словами.
    words: str
    link_quote: str
    target_quote: str
    disclosure: str
    prompt_version: str
    model: str
    judged_at: datetime

    @classmethod
    def of(cls, said: JudgeAdvice) -> JudgeAdviceView:
        return cls(
            advice=said.advice.value,
            words=said.words,
            link_quote=said.link_quote,
            target_quote=said.target_quote,
            disclosure=said.disclosure,
            prompt_version=said.prompt_version,
            model=said.model,
            judged_at=said.judged_at,
        )


class CandidateCard(BaseModel):
    """Один кандидат: балл, вердикт и всё, по чему человек решает.

    Причины приходят строками, а не кодами: человек читает их глазами,
    и превращать «ссылки с 20 страниц донора +3» в ключ ради
    аккуратности значит переводить их обратно на экране. Длинное пояснение
    причины — не в строке, а в `reason_notes` по её словам (`crawl/reason_words.py`).
    """

    id: int
    donor_host: str
    target_root: str
    points: int
    verdict: Verdict
    reasons: list[str] = Field(default_factory=list)
    #: Пояснение причины по её словам в `reasons`: «зона .org» — почему решает человек.
    reason_notes: dict[str, str] = Field(default_factory=dict)
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
    #: Решение поставлено по совету судьи, а не человеком (`crawl/judge_apply.py`): экран говорит
    #: «решил судья», а метку партии из `decided_by` не разбирает.
    by_judge: bool = False
    #: Ответ на решение: что сказать человеку рядом с ним — «рекламодатель уже ответил».
    warning: str | None = None
    #: Совет судьи — только после решения человека; до решения — пусто.
    judge: JudgeAdviceView | None = None

    @classmethod
    def of(
        cls,
        row: CandidateModel,
        *,
        published: date | None = None,
        judge: JudgeAdvice | None = None,
    ) -> CandidateCard:
        shown = shown_reasons(row.reasons)
        return cls(
            id=row.id,
            donor_host=row.donor_host,
            target_root=row.target_root,
            points=row.points,
            verdict=row.verdict,
            reasons=shown.words,
            reason_notes=shown.notes,
            links=row.links,
            pages=row.pages,
            best_page_url=row.best_page_url,
            best_anchor=row.best_anchor,
            best_published=published,
            confirmed=row.confirmed,
            decided_by=row.decided_by,
            decided_at=row.decided_at,
            by_judge=batch_of(row.decided_by) is not None,
            judge=JudgeAdviceView.of(judge)
            if judge is not None and row.confirmed is not None
            else None,
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
    пусто, когда вывод уже есть. `decided` — решения подряд в порядке выборки, только они в доле;
    `aside` — решённые дальше первой нерешённой, вне доли (`accuracy.in_turn`). `left` — строк
    выборки вне замера: больше них не решить."""

    total: int
    decided: int
    write: int
    skip: int
    left: int
    aside: int
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
            aside=share.aside,
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


class ReviewQuery(BaseModel):
    """Какой кусок очереди спрашивают. Страница — номером, размер называет сервер; лишний
    параметр — отказ словами: фильтр с опечаткой не должен молча показать всю очередь.

    Имена те же, что в адресе экрана (`frontend/src/advertisers/reviewFilters.ts`). FastAPI
    разворачивает модель в параметры адреса, только когда других параметров у маршрута нет.
    """

    model_config = ConfigDict(extra="forbid")

    include_decided: bool = Field(
        default=False, description="показывать и те, по которым решение уже принято"
    )
    verdict: Verdict = Field(
        default=Verdict.PENDING, description="спорные (pending) или «куплено» (bought)"
    )
    points: int | None = Field(default=None, description="балл — точным значением")
    feature: str | None = Field(
        default=None, max_length=64, description="признак скоринга: ключ из `crawl/strata.py`"
    )
    page: int = Field(default=1, ge=1, le=1_000_000, description="страница, с единицы")
    limit: int = Field(default=PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="строк на странице")

    def chosen(self) -> QueueFilter:
        return QueueFilter(points=self.points, feature=self.feature)


class FacetChoiceView(BaseModel):
    """Вариант фильтра: значение, слова экрана и сколько строк с ним."""

    key: str
    title: str
    count: int


class FacetView(BaseModel):
    """Варианты одного фильтра. `total` — сколько строк, когда он не сужает («все»)."""

    total: int
    choices: list[FacetChoiceView]


class QueueFacetsView(BaseModel):
    """Счётчики под фильтрами очереди: у каждого — со всеми условиями, кроме своего
    (`crawl/review._facets`). `listed` — строк списка без фильтров."""

    listed: int
    points: FacetView
    features: FacetView

    @classmethod
    def of(cls, facets: QueueFacets) -> QueueFacetsView:
        return cls(**asdict(facets))


class CandidatesView(BaseModel):
    """Страница очереди ручной проверки и что вокруг неё.

    `total` — строк под фильтром на всех страницах, `limit` — размер страницы: по ним экран
    считает страницы. **Всё остальное — по всем, а не по странице и не по фильтру**: числа в
    шапке экрана от них не меняются. Счётчики по всем вердиктам, а не только по спорным: по
    отсеянным видно, что список «кому не пишем» работает, а не молчит. Рядом — доля ложных
    среди «куплено» (сверка ТЗ 11.10.2026): решения человека, по которым она считается,
    принимаются в этой же очереди.
    """

    rows: list[CandidateCard]
    total: int
    page: int
    limit: int
    facets: QueueFacetsView
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
    #: Домены без DR: в `ready` не входят и ждут, пока обход его спросит.
    waiting_dr: int = 0


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
