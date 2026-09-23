"""Проход судьи по прогону: кэш, подсчёт и то, что в наблюдении он не режет."""

from __future__ import annotations

import pytest
from backend.features.donors.home_signals import HomeSignals
from backend.features.donors.judging import (
    UNITS_DR,
    UNITS_METRICS,
    JudgeSummary,
    judge_candidates,
)
from backend.features.donors.publisher_judge import (
    PROMPT_VERSION,
    Decider,
    Intent,
    Judgement,
    Recommendation,
)
from backend.features.runs.planning import SerpText
from backend.features.serp.protocol import SerpResult

TEXTS = {
    "brand.example": SerpText(url="https://brand.example/a", title="Buy direct at Brand"),
    "media.example": SerpText(url="https://media.example/a", title="Reviews of gear"),
    "blocked.example": SerpText(url="https://blocked.example/a", title="Access Denied"),
}


class FakeJudge:
    """Подменяет вызов модели: вердикт задаётся тестом, вызовы считаются."""

    def __init__(self, answers: dict[str, Judgement]) -> None:
        self.answers = answers
        self.asked: list[str] = []

    async def __call__(self, http: object, **kwargs: object) -> Judgement:
        host = str(kwargs["host"])
        self.asked.append(host)
        return self.answers[host]


@pytest.fixture
def judge(monkeypatch: pytest.MonkeyPatch) -> FakeJudge:
    fake = FakeJudge(
        {
            "brand.example": Judgement(
                Intent.SELLS_OWN, Recommendation.REJECT, "Buy direct", "продаёт своё", "m", 400
            ),
            "media.example": Judgement(
                Intent.REFERS_OUT, Recommendation.ACCEPT, "Reviews", "обзоры", "m", 410
            ),
            "blocked.example": Judgement(
                Intent.UNKNOWN, Recommendation.REVIEW, None, "отказ доступа", "m", 0
            ),
        }
    )
    monkeypatch.setattr("backend.features.donors.judging.judge_host", fake)
    return fake


@pytest.mark.asyncio
async def test_counts_each_and_sums_tokens(judge: FakeJudge) -> None:
    result = await judge_candidates(None, list(TEXTS), TEXTS)  # type: ignore[arg-type]

    assert result.summary.judged == 3
    assert result.summary.would_cut == 1
    assert result.summary.to_review == 1
    assert result.summary.tokens == 810
    assert result.summary.by_intent == {"sells_own": 1, "refers_out": 1, "unknown": 1}


@pytest.mark.asyncio
async def test_fresh_verdict_is_not_rejudged(judge: FakeJudge) -> None:
    """Кэш судьи — это отметка времени на домене, отдельного хранилища нет."""
    result = await judge_candidates(
        None,  # type: ignore[arg-type]
        list(TEXTS),
        TEXTS,
        already_judged={"brand.example": "reject"},
    )

    assert "brand.example" not in judge.asked, "за свежий вердикт платить токенами нельзя"
    assert result.summary.from_cache == 1
    assert result.summary.judged == 2
    # ⚠ Отрезанный из кэша обязан остаться отрезанным: иначе домен,
    # осуждённый в прошлом прогоне, тихо проходит в этом.
    assert "brand.example" in result.rejected


@pytest.mark.asyncio
async def test_verdict_carries_judged_page_url(judge: FakeJudge) -> None:
    """Без адреса цитата повисает без контекста, а тип страницы решает."""
    result = await judge_candidates(None, ["media.example"], TEXTS)  # type: ignore[arg-type]

    record = result.verdicts["media.example"]
    assert record.source_url == "https://media.example/a"
    assert record.quote == "Reviews"
    assert record.intent == "refers_out"


@pytest.mark.asyncio
async def test_one_domain_failure_does_not_break_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Судья — не та ступень, ради которой теряют оплаченную выдачу."""

    async def boom(http: object, **kwargs: object) -> Judgement:
        if kwargs["host"] == "brand.example":
            raise RuntimeError("провайдер лёг")
        return Judgement(Intent.REFERS_OUT, Recommendation.ACCEPT, "Reviews", "ок", "m", 10)

    monkeypatch.setattr("backend.features.donors.judging.judge_host", boom)
    result = await judge_candidates(None, list(TEXTS), TEXTS)  # type: ignore[arg-type]

    assert "brand.example" not in result.verdicts
    assert result.summary.judged == 2, "остальные домены досужены"


def test_savings_are_a_lower_bound() -> None:
    """Завышать нельзя: на это число будут ссылаться, решая, включать ли отказ.

    Страны (55 юнитов) не считаются вовсе — их зовут не всем.
    """
    summary = JudgeSummary()
    summary.would_cut = 15
    summary.would_cut_paid = 11
    # Досуженные свежие домены экономии не дают: их метрики уже куплены.
    assert summary.units_saved == 11 * (UNITS_DR + UNITS_METRICS)
    assert UNITS_DR + UNITS_METRICS < 55 + UNITS_DR + UNITS_METRICS


# --- вторая сторона: главная, правило и арбитр ------------------------------

SHOP = HomeSignals(reached=True, shop=("cart:/warenkorb",), title="Shop")
QUIET = HomeSignals(reached=True, title="Magazin")
CLOSED = HomeSignals(reached=False, error="закрылась")


class FakeHome:
    def __init__(self, answers: dict[str, HomeSignals]) -> None:
        self.answers = answers
        self.asked: list[str] = []

    async def __call__(self, client: object, host: str) -> HomeSignals:
        self.asked.append(host)
        return self.answers[host]


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch) -> FakeHome:
    fake = FakeHome({"brand.example": SHOP, "media.example": SHOP, "blocked.example": CLOSED})
    monkeypatch.setattr("backend.features.donors.judging.check_home", fake)
    return fake


@pytest.fixture
def arbiter(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    asked: list[str] = []

    async def fake(http: object, **kwargs: object) -> Judgement:
        asked.append(str(kwargs["host"]))
        return Judgement(
            Intent.SELLS_OWN,
            Recommendation.REJECT,
            "Shop",
            "магазин с журналом",
            "m",
            700,
            Decider.ARBITER,
        )

    monkeypatch.setattr("backend.features.donors.judging.arbitrate", fake)
    return asked


@pytest.mark.asyncio
async def test_sells_own_plus_cart_is_decided_by_rule(
    judge: FakeJudge, home: FakeHome, arbiter: list[str]
) -> None:
    """Две независимые стороны сказали одно — человеку смотреть нечего."""
    result = await judge_candidates(
        None,
        ["brand.example"],
        TEXTS,
        home_client=object(),  # type: ignore[arg-type]
    )

    record = result.verdicts["brand.example"]
    assert record.decided_by == "rule"
    assert record.recommendation == "reject"
    assert record.home is not None
    assert record.home["shop"] == ["cart:/warenkorb"]
    assert arbiter == [], "спора нет — арбитр не нужен"


@pytest.mark.asyncio
async def test_publisher_with_cart_goes_to_arbiter(
    judge: FakeJudge, home: FakeHome, arbiter: list[str]
) -> None:
    """Сразу в отказ нельзя: корзина бывает и у изданий, продающих свои тесты."""
    result = await judge_candidates(
        None,
        ["media.example"],
        TEXTS,
        home_client=object(),  # type: ignore[arg-type]
    )

    assert arbiter == ["media.example"]
    record = result.verdicts["media.example"]
    assert record.decided_by == "arbiter"
    assert result.summary.by_decider == {"arbiter": 1}
    # Токены обоих вызовов: арбитр — не бесплатное уточнение.
    assert result.summary.tokens == 410 + 700


@pytest.mark.asyncio
async def test_arbiter_reject_without_sales_goes_to_human(
    judge: FakeJudge, monkeypatch: pytest.MonkeyPatch, arbiter: list[str]
) -> None:
    """Правило доказательства: отрезать может только структура. Замер 23.09 —
    арбитр без него отрезал 5 из 77 изданий с магазином или курсом сбоку."""
    monkeypatch.setattr(
        "backend.features.donors.judging.check_home", FakeHome({"media.example": QUIET})
    )
    result = await judge_candidates(
        None,
        ["media.example"],
        TEXTS,
        home_client=object(),  # type: ignore[arg-type]
    )

    assert arbiter == ["media.example"], "блог компании по выдаче неотличим от издания"
    record = result.verdicts["media.example"]
    assert record.decided_by == "arbiter"
    assert record.recommendation == "review"


@pytest.mark.asyncio
async def test_service_marks_let_arbiter_cut(
    judge: FakeJudge, monkeypatch: pytest.MonkeyPatch, arbiter: list[str]
) -> None:
    service = HomeSignals(reached=True, service=("path:/pricing",), title="Product")
    monkeypatch.setattr(
        "backend.features.donors.judging.check_home", FakeHome({"media.example": service})
    )
    result = await judge_candidates(
        None,
        ["media.example"],
        TEXTS,
        home_client=object(),  # type: ignore[arg-type]
    )

    assert result.verdicts["media.example"].recommendation == "reject"


@pytest.mark.asyncio
async def test_no_verdict_means_no_home_request(judge: FakeJudge, home: FakeHome) -> None:
    """У «посмотри» спорить не с чем — сайт не дёргаем зря."""
    await judge_candidates(
        None,
        ["blocked.example"],
        TEXTS,
        home_client=object(),  # type: ignore[arg-type]
    )
    assert home.asked == []


@pytest.mark.asyncio
async def test_closed_home_is_counted_separately(
    judge: FakeJudge, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Иначе закрывшийся сайт выглядел бы как сайт без признаков магазина."""
    monkeypatch.setattr(
        "backend.features.donors.judging.check_home", FakeHome({"media.example": CLOSED})
    )
    result = await judge_candidates(
        None,
        ["media.example"],
        TEXTS,
        home_client=object(),  # type: ignore[arg-type]
    )

    assert result.summary.home_unreached == 1
    assert result.verdicts["media.example"].decided_by == "model"


@pytest.mark.asyncio
async def test_only_unpaid_domains_give_savings(judge: FakeJudge) -> None:
    """Свежий домен досуживается ради знания, но за его метрики уже заплачено."""
    result = await judge_candidates(
        None,
        ["brand.example"],
        TEXTS,
        paid=[],  # type: ignore[arg-type]
    )

    assert result.summary.would_cut == 1
    assert result.summary.units_saved == 0


# --- закрытая главная: образ из индекса поиска ------------------------------


class Index:
    """Источник выдачи для `site:`: запоминает, о ком спросили."""

    spent = 0.0

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def search(
        self, keywords: list[str], country: str, **_: object
    ) -> dict[str, list[SerpResult]]:
        self.asked.extend(keywords)
        self.spent += 0.006 * len(keywords)
        return {
            key: [
                SerpResult(1, f"https://{key[5:]}/", "P2P exchange: buy bitcoin", "Buy and sell"),
                SerpResult(2, f"https://{key[5:]}/sell", "Sell crypto without fees"),
            ]
            for key in keywords
        }


@pytest.mark.asyncio
async def test_closed_home_is_judged_through_the_index(
    judge: FakeJudge, monkeypatch: pytest.MonkeyPatch, arbiter: list[str]
) -> None:
    """23.09 все четыре ошибки пяти рынков — продавцы за закрытой главной,
    принятые по статье. Индекс видит их всё равно; структуры в нём нет,
    поэтому отказ по индексу идёт человеку, а не в отказ."""
    monkeypatch.setattr(
        "backend.features.donors.judging.check_home",
        FakeHome({"media.example": CLOSED, "brand.example": CLOSED, "blocked.example": CLOSED}),
    )
    index = Index()
    result = await judge_candidates(
        None,
        list(TEXTS),
        TEXTS,
        home_client=object(),
        index=index,  # type: ignore[arg-type]
    )

    # Спрашиваем только про принятых моделью: отрезанным и «посмотри» спорить не о чем.
    assert index.asked == ["site:media.example"]
    record = result.verdicts["media.example"]
    assert record.decided_by == "arbiter"
    assert record.recommendation == "review"
    assert record.home is not None
    assert record.home["via"] == "index"
    assert result.summary.from_index == 1
    assert result.summary.index_usd == pytest.approx(0.006)


@pytest.mark.asyncio
async def test_index_failure_keeps_model_verdicts(
    judge: FakeJudge, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Broken:
        spent = 0.0

        async def search(self, *_: object, **__: object) -> dict[str, list[SerpResult]]:
            raise RuntimeError("источник лёг")

    monkeypatch.setattr(
        "backend.features.donors.judging.check_home", FakeHome({"media.example": CLOSED})
    )
    result = await judge_candidates(
        None,
        ["media.example"],
        TEXTS,
        home_client=object(),
        index=Broken(),  # type: ignore[arg-type]
    )

    assert result.verdicts["media.example"].recommendation == "accept"


# --- продажа размещения и дверь для авторов (судья v2) -----------------------


@pytest.mark.asyncio
async def test_brand_with_author_page_goes_to_human(monkeypatch: pytest.MonkeyPatch) -> None:
    """Выдача пришла со страницы приёма авторов — отказ становится «посмотри».

    Прогон №18: 31 из 58 таких сайтов отрезан как «продаёт своё».
    """

    async def brand(http: object, **kwargs: object) -> Judgement:
        return Judgement(Intent.SELLS_OWN, Recommendation.REJECT, "q", "свой сервис", "m", 5)

    monkeypatch.setattr("backend.features.donors.judging.judge_host", brand)
    texts = {"tool.example": SerpText(url="https://tool.example/blog/write-for-us/", title="Blog")}
    result = await judge_candidates(None, ["tool.example"], texts)  # type: ignore[arg-type]

    record = result.verdicts["tool.example"]
    assert record.recommendation == "review"
    assert "write-for-us" in record.reason
    assert record.intent == "sells_own", "вид модели не переписывается — по нему считают точность"
    assert "tool.example" not in result.rejected
    assert result.summary.would_cut == 0


@pytest.mark.asyncio
async def test_article_about_guest_posts_is_not_a_door(monkeypatch: pytest.MonkeyPatch) -> None:
    """Статья ПРО гостевые посты у продавца инструмента — не приглашение."""

    async def brand(http: object, **kwargs: object) -> Judgement:
        return Judgement(Intent.SELLS_OWN, Recommendation.REJECT, "q", "свой сервис", "m", 5)

    monkeypatch.setattr("backend.features.donors.judging.judge_host", brand)
    url = "https://tool.example/blog/guest-posting-opportunities"
    texts = {"tool.example": SerpText(url=url, title="Guest posting opportunities in 2026")}
    result = await judge_candidates(None, ["tool.example"], texts)  # type: ignore[arg-type]

    assert result.verdicts["tool.example"].recommendation == "reject"


@pytest.mark.asyncio
async def test_placement_seller_skips_the_storefront(monkeypatch: pytest.MonkeyPatch) -> None:
    """`/pricing` на главной не отменяет платных гостевых статей у себя."""

    async def seller(http: object, **kwargs: object) -> Judgement:
        return Judgement(
            Intent.SELLS_PLACEMENT, Recommendation.ACCEPT, "paid guest post", "продаёт", "m", 5
        )

    async def storefront(client: object, host: str) -> HomeSignals:
        raise AssertionError("за продавцом размещения главную не спрашивают")

    monkeypatch.setattr("backend.features.donors.judging.judge_host", seller)
    monkeypatch.setattr("backend.features.donors.judging.check_home", storefront)
    texts = {"lab.example": SerpText(url="https://lab.example/write-for-us", title="Write for us")}
    result = await judge_candidates(
        None,  # type: ignore[arg-type]
        ["lab.example"],
        texts,
        home_client=object(),  # type: ignore[arg-type]
    )

    record = result.verdicts["lab.example"]
    assert record.recommendation == "accept"
    assert record.intent == "sells_placement"


@pytest.mark.asyncio
async def test_verdict_carries_prompt_version(judge: FakeJudge) -> None:
    result = await judge_candidates(None, ["media.example"], TEXTS)  # type: ignore[arg-type]
    assert result.verdicts["media.example"].version == PROMPT_VERSION
