"""Судья площадки: денилист, цитата и fail-soft.

Проверяется не «модель угадала» — это не в нашей власти, — а то, что
судья **никогда не превращает свой сбой в отказ донору**. Ошибка модели
должна стоить домену одного взгляда человека, а не исключения навсегда.
"""

from __future__ import annotations

import json

import pytest

from backend.features.donors.publisher_judge import (
    Intent,
    Judgement,
    Recommendation,
    build_payload,
    is_platform,
    judge_host,
    parse,
    source_text,
)

TEXT = "Top Online Casino & Sports Betting at Interbet — deposit and play"


def test_денилист_матчит_целую_метку() -> None:
    assert is_platform("google.com")
    assert is_platform("www.reddit.com")
    # ⚠ Вхождение строки денило бы этот домен, а он к платформе отношения
    # не имеет. Именно этот случай оговорён в каноне соседней системы.
    assert not is_platform("google-maps-guide.co.ke")
    assert not is_platform("betting-review.co.za")


def test_похвала_себе_не_делает_обзорщиком() -> None:
    verdict = parse(
        json.dumps({"intent": "sells_own", "quote": "Top Online Casino", "why": "оператор"}),
        TEXT,
    )
    assert verdict.intent is Intent.SELLS_OWN
    assert verdict.recommendation is Recommendation.REJECT
    assert verdict.would_cut


def test_обзорщик_проходит() -> None:
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
def test_непонятный_ответ_даёт_посмотри_а_не_отказ(content: str, why: str) -> None:
    verdict = parse(content, TEXT)
    assert verdict.recommendation is Recommendation.REVIEW, "сбой судьи не хоронит домен"
    assert why in verdict.reason


def test_выдуманная_цитата_понижает_вердикт() -> None:
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


def test_цитата_переживает_иные_пробелы() -> None:
    """Модель переносит строки иначе, чем провайдер. Честная цитата от
    этого не должна проваливаться — иначе «посмотри» соберёт всех подряд."""
    verdict = parse(
        json.dumps({"intent": "sells_own", "quote": "Top   Online\nCasino", "why": "x"}),
        TEXT,
    )
    assert verdict.recommendation is Recommendation.REJECT


def test_текст_склеивается_и_режется_по_потолку() -> None:
    assert source_text(None, None) == ""
    assert source_text("  ", "") == ""
    assert source_text("Заголовок", "Описание") == "Заголовок\nОписание"


def test_запрос_рассуждающей_модели_без_температуры() -> None:
    """Перепутать нельзя: провайдер отвечает отказом, а не догадкой."""
    reasoning = build_payload("gpt-5-mini", "example.com", TEXT)
    assert "temperature" not in reasoning
    assert reasoning["max_completion_tokens"] > 0

    plain = build_payload("gpt-4o-mini", "example.com", TEXT)
    assert plain["temperature"] == 0
    assert plain["max_tokens"] > 0


@pytest.mark.asyncio
async def test_платформа_не_доходит_до_модели() -> None:
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
async def test_без_текста_выдачи_модель_не_зовётся() -> None:
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


def test_would_cut_считает_только_отказ() -> None:
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


def test_в_промпте_нет_ни_одного_слова_ниши() -> None:
    """⚠ Канарейка мультинишевости.

    Судья спрашивает СПОСОБ ЗАРАБОТКА: «продаёт своё» против «пишет про
    чужое». Первое зависит от ниши, второе — нет, и ровно поэтому механизм
    переносится с одной ниши на любую без списков брендов.

    Первая же подсказка вида «казино — это оператор» чинит один случай и
    ломает перенос: на соседней нише её нет, и судья теряет опору, которой
    привык пользоваться. Пример в промпте есть, но он про ФОРМУ вывода
    («похвала себе не делает обзорщиком»), и слов ниши в нём нет.
    """
    from backend.features.donors.publisher_judge import SYSTEM

    lowered = SYSTEM.lower()
    found = [word for word in NICHE_WORDS if word in lowered]
    assert found == [], (
        f"в промпте судьи появились слова ниши: {found}. "
        "Механизм обязан работать на любой нише — см. okf/selection-audit.md"
    )


def test_денилист_не_знает_ниш() -> None:
    """Денилист — про платформы, а не про рынок.

    Название площадки из конкретной ниши здесь появиться не может: сегодня
    это отрежет мусор, завтра — половину доноров соседнего рынка.
    """
    from backend.config.judge import PLATFORM_LABELS

    found = [
        label
        for label in PLATFORM_LABELS
        for word in NICHE_WORDS
        if word in label.lower()
    ]
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
async def test_отказ_доступа_не_судится_даже_когда_получается(text: str) -> None:
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
