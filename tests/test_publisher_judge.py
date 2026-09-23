"""Судья площадки: денилист, цитата и fail-soft.

Проверяется не «модель угадала» — это не в нашей власти, — а то, что
судья **никогда не превращает свой сбой в отказ донору**. Ошибка модели
должна стоить домену одного взгляда человека, а не исключения навсегда.
"""

from __future__ import annotations

import json

import pytest
from backend.config.judge import PLATFORM_LABELS
from backend.features.donors.home_signals import HomeSignals
from backend.features.donors.publisher_judge import (
    SYSTEM,
    Decider,
    Intent,
    Judgement,
    Recommendation,
    arbiter_text,
    build_payload,
    is_platform,
    is_public_zone,
    judge_host,
    parse,
    source_text,
)

TEXT = "Top Online Casino & Sports Betting at Interbet — deposit and play"


def test_denylist_matches_whole_label() -> None:
    assert is_platform("google.com")
    assert is_platform("www.reddit.com")
    # ⚠ Вхождение строки денило бы этот домен, а он к платформе отношения
    # не имеет. Именно этот случай оговорён в каноне соседней системы.
    assert not is_platform("google-maps-guide.co.ke")
    assert not is_platform("betting-review.co.za")


def test_self_praise_is_not_a_review() -> None:
    verdict = parse(
        json.dumps({"intent": "sells_own", "quote": "Top Online Casino", "why": "оператор"}),
        TEXT,
    )
    assert verdict.intent is Intent.SELLS_OWN
    assert verdict.recommendation is Recommendation.REJECT
    assert verdict.would_cut


def test_reviewer_passes() -> None:
    verdict = parse(
        json.dumps({"intent": "refers_out", "quote": "Sports Betting", "why": "сравнивает"}),
        TEXT,
    )
    assert verdict.recommendation is Recommendation.ACCEPT
    assert not verdict.would_cut


@pytest.mark.parametrize(
    ("content", "why"),
    [
        ("не json вовсе", "ответ модели не разобрать"),
        (json.dumps({"intent": "казино", "quote": "Top Online Casino"}), "намерение не из списка"),
        (json.dumps({"intent": "refers_out"}), "модель не дала цитаты"),
    ],
)
def test_unreadable_answer_gives_review_not_reject(content: str, why: str) -> None:
    verdict = parse(content, TEXT)
    assert verdict.recommendation is Recommendation.REVIEW, "сбой судьи не хоронит домен"
    assert why in verdict.reason


def test_invented_quote_downgrades_verdict() -> None:
    """Цитата, которой нет в тексте, — признак того, что модель сочинила.

    Вердикт при этом СОХРАНЯЕТСЯ: он уедет человеку вместе с цитатой,
    и по ней видно, что именно модель придумала.
    """
    verdict = parse(
        json.dumps({"intent": "sells_own", "quote": "этого в тексте нет", "why": "..."}),
        TEXT,
    )
    assert verdict.intent is Intent.SELLS_OWN
    assert verdict.recommendation is Recommendation.REVIEW
    assert verdict.quote == "этого в тексте нет"
    assert "цитата не найдена" in verdict.reason


def test_quote_survives_different_whitespace() -> None:
    """Модель переносит строки иначе, чем провайдер. Честная цитата от
    этого не должна проваливаться — иначе «посмотри» соберёт всех подряд."""
    verdict = parse(
        json.dumps({"intent": "sells_own", "quote": "Top   Online\nCasino", "why": "x"}),
        TEXT,
    )
    assert verdict.recommendation is Recommendation.REJECT


def test_text_is_joined_and_capped() -> None:
    assert source_text(None, None) == ""
    assert source_text("  ", "") == ""
    assert source_text("Заголовок", "Описание") == "Заголовок\nОписание"


def test_reasoning_model_request_has_no_temperature() -> None:
    """Перепутать нельзя: провайдер отвечает отказом, а не догадкой."""
    reasoning = build_payload("gpt-5-mini", "example.com", TEXT)
    assert "temperature" not in reasoning
    assert reasoning["max_completion_tokens"] > 0

    plain = build_payload("gpt-4o-mini", "example.com", TEXT)
    assert plain["temperature"] == 0
    assert plain["max_tokens"] > 0


@pytest.mark.asyncio
async def test_platform_never_reaches_model() -> None:
    """Денилист стоит ДО судьи и стоит ноль: вызова быть не должно."""

    class Boom:
        async def post(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("модель не должна вызываться для платформы")

    verdict = await judge_host(
        Boom(),  # type: ignore[arg-type]
        host="reddit.com",
        title="Anything",
        description=None,
        api_key="ключ",
    )
    assert verdict.recommendation is Recommendation.REJECT
    assert verdict.reason == "платформа из денилиста"


@pytest.mark.asyncio
async def test_no_serp_text_means_no_model_call() -> None:
    """Судить не по чему — это «посмотри», и это бесплатно."""

    class Boom:
        async def post(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("модель не должна вызываться без текста")

    verdict = await judge_host(
        Boom(),  # type: ignore[arg-type]
        host="example.com",
        title=None,
        description="   ",
        api_key="ключ",
    )
    assert verdict.recommendation is Recommendation.REVIEW
    assert "ни заголовка" in verdict.reason


def test_would_cut_counts_only_reject() -> None:
    for rec, expected in [
        (Recommendation.REJECT, True),
        (Recommendation.REVIEW, False),
        (Recommendation.ACCEPT, False),
    ]:
        assert Judgement(Intent.UNKNOWN, rec, None, "").would_cut is expected


# --- мультинишевость: не правило на словах, а проверка -----------------------

#: Слова, которых в промпте быть не должно. Любое из них означает, что судья
#: знает нишу и, значит, перестал работать на соседней.
NICHE_WORDS = (
    "букмекер",
    "ставк",
    "казино",
    "betting",
    "casino",
    "bookmaker",
    "sportsbook",
    "крипт",
    "crypto",
    "travel",
    "путешеств",
    "фитнес",
    "fitness",
    "beauty",
    "красот",
    "авто",
    "automotive",
)


def test_prompt_has_no_niche_words() -> None:
    """⚠ Канарейка мультинишевости.

    Судья спрашивает СПОСОБ ЗАРАБОТКА: «продаёт своё» против «пишет про
    чужое». Первое зависит от ниши, второе — нет, и ровно поэтому механизм
    переносится с одной ниши на любую без списков брендов.

    Первая же подсказка вида «казино — это оператор» чинит один случай и
    ломает перенос: на соседней нише её нет, и судья теряет опору, которой
    привык пользоваться. Пример в промпте есть, но он про ФОРМУ вывода
    («похвала себе не делает обзорщиком»), и слов ниши в нём нет.
    """

    lowered = SYSTEM.lower()
    found = [word for word in NICHE_WORDS if word in lowered]
    assert found == [], (
        f"в промпте судьи появились слова ниши: {found}. "
        "Механизм обязан работать на любой нише — см. okf/selection-audit.md"
    )


def test_denylist_knows_no_niches() -> None:
    """Денилист — про платформы, а не про рынок.

    Название площадки из конкретной ниши здесь появиться не может: сегодня
    это отрежет мусор, завтра — половину доноров соседнего рынка.
    """

    found = [label for label in PLATFORM_LABELS for word in NICHE_WORDS if word in label.lower()]
    assert found == [], f"в денилисте платформ появились слова ниши: {found}"


# --- страница-отказ не судится ----------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "403 - Access Denied | Edmunds",
        "Access Denied",
        "Attention Required! | Cloudflare",
        "Just a moment...",
        "Access to this page has been denied",
    ],
)
@pytest.mark.asyncio
async def test_access_denial_is_never_judged(text: str) -> None:
    """⚠ Худший случай — не промах, а УГАДАННОЕ попадание.

    На живом прогоне 23.09 `edmunds.com` отдал страницу 403, судья назвал
    его площадкой и попал; `trivago.com` отдал такую же и получил «не
    площадка». Оба вердикта — про защиту сайта, а не про сайт, и оба
    одинаково вредны: первый тихо завышает точность замера.
    """

    class Boom:
        async def post(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("модель не должна судить страницу-отказ")

    verdict = await judge_host(
        Boom(),  # type: ignore[arg-type]
        host="example.com",
        title=text,
        description=None,
        api_key="ключ",
    )
    assert verdict.recommendation is Recommendation.REVIEW
    assert "отказ доступа" in verdict.reason
    assert verdict.tokens == 0, "вызова модели не было — значит и токенов нет"


# --- некоммерческие и арбитр -----------------------------------------------


def test_non_commercial_goes_to_human_not_reject() -> None:
    """Госорган по тексту неотличим от издания; различает только знание
    модели о владельце. Резать по одному знанию нельзя."""
    verdict = parse(
        json.dumps({"intent": "non_commercial", "quote": "Sports Betting", "why": "госорган"}),
        TEXT,
    )
    assert verdict.recommendation is Recommendation.REVIEW
    assert not verdict.would_cut


def test_arbiter_quote_is_searched_in_both_sides() -> None:

    home = HomeSignals(reached=True, shop=("cart:/warenkorb",), title="Kaffee kaufen bei Rösterei")
    text = arbiter_text("Wie entkalke ich meine Maschine", home)
    verdict = parse(
        json.dumps(
            {"intent": "sells_own", "quote": "Kaffee kaufen bei Rösterei", "why": "магазин"}
        ),
        text,
    )
    assert verdict.recommendation is Recommendation.REJECT


def test_reasoning_effort_comes_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """На `minimal` модель не вспоминает, чей домен (замер 23.09)."""
    monkeypatch.setattr("backend.config.judge.REASONING_EFFORT", "low")
    payload = build_payload("gpt-5-mini", "x.test", "текст")
    assert payload["reasoning_effort"] == "low"


# --- государственные и учебные зоны -----------------------------------------


@pytest.mark.parametrize(
    "host",
    ["rajasthan.gov.in", "interior.gob.es", "ox.ac.uk", "mhlw.go.jp", "nih.gov", "mit.edu",
     "www.gov.br", "army.mil", "canada.gc.ca", "wien.gv.at"],
)  # fmt: skip
def test_public_zone_is_recognised(host: str) -> None:
    """Размещений не продают, а коммерческая страница на них — взлом:
    23.09 модель приняла rajasthan.gov.in со статьёй о букмекерах."""
    assert is_public_zone(host)


@pytest.mark.parametrize(
    "host", ["go.com", "google.com", "education.com", "mygov.com", "gov.tech", "edu.example"]
)
def test_lookalikes_are_not_public_zones(host: str) -> None:
    """`go` — зона только перед страной: `go.jp` да, `go.com` нет."""
    assert not is_public_zone(host)


@pytest.mark.asyncio
async def test_public_zone_is_cut_without_the_model() -> None:
    verdict = await judge_host(
        None,  # type: ignore[arg-type]
        host="rajasthan.gov.in",
        title="How to compare betting platforms in South Africa",
        description=None,
    )
    assert verdict.recommendation is Recommendation.REJECT
    assert verdict.decided_by is Decider.RULE
    assert verdict.tokens == 0


# --- судья v2: продажа размещения у себя и у чужих ---------------------------


def test_placement_seller_is_accepted_and_vendor_cut() -> None:
    text = "We offer paid guest post publishing on our blog. Buy backlinks on 5000 sites."
    seller = parse(
        json.dumps({"intent": "sells_placement", "quote": "paid guest post publishing"}), text
    )
    vendor = parse(json.dumps({"intent": "link_vendor", "quote": "Buy backlinks on 5000"}), text)
    assert seller.recommendation is Recommendation.ACCEPT
    assert vendor.recommendation is Recommendation.REJECT


def test_both_prompts_know_placement() -> None:
    """Арбитр без новых видов вернул бы продавца размещения в «продаёт своё»."""
    from backend.features.donors.publisher_judge import ARBITER_SYSTEM

    for prompt in (SYSTEM, ARBITER_SYSTEM):
        assert "sells_placement" in prompt
        assert "link_vendor" in prompt


def test_every_intent_has_advice() -> None:
    from backend.features.donors.publisher_judge import ADVICE

    assert set(ADVICE) == set(Intent)
