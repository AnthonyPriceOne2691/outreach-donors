"""Проход судьи по прогону: кэш, подсчёт и то, что в наблюдении он не режет."""

from __future__ import annotations

import pytest

from backend.features.donors.judging import (
    UNITS_DR,
    UNITS_METRICS,
    JudgeSummary,
    judge_candidates,
)
from backend.features.donors.publisher_judge import Intent, Judgement, Recommendation
from backend.features.runs.planning import SerpText

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
async def test_считает_каждого_и_складывает_токены(judge: FakeJudge) -> None:
    result = await judge_candidates(None, list(TEXTS), TEXTS)  # type: ignore[arg-type]

    assert result.summary.judged == 3
    assert result.summary.would_cut == 1
    assert result.summary.to_review == 1
    assert result.summary.tokens == 810
    assert result.summary.by_intent == {"sells_own": 1, "refers_out": 1, "unknown": 1}


@pytest.mark.asyncio
async def test_свежий_вердикт_не_пересуживается(judge: FakeJudge) -> None:
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
async def test_вердикт_несёт_адрес_судимой_страницы(judge: FakeJudge) -> None:
    """Без адреса цитата повисает без контекста, а тип страницы решает."""
    result = await judge_candidates(None, ["media.example"], TEXTS)  # type: ignore[arg-type]

    record = result.verdicts["media.example"]
    assert record.source_url == "https://media.example/a"
    assert record.quote == "Reviews"
    assert record.intent == "refers_out"


@pytest.mark.asyncio
async def test_падение_на_одном_домене_не_роняет_проход(
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


def test_экономия_считается_нижней_границей() -> None:
    """Завышать нельзя: на это число будут ссылаться, решая, включать ли отказ.

    Страны (55 юнитов) не считаются вовсе — их зовут не всем.
    """
    summary = JudgeSummary()
    summary.would_cut = 11
    assert summary.units_saved == 11 * (UNITS_DR + UNITS_METRICS)
    assert UNITS_DR + UNITS_METRICS < 55 + UNITS_DR + UNITS_METRICS
