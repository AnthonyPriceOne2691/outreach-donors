"""Судья черновика продаж: правила кодом, затем модель — срез 3.2a, шаг 6.

Модель — подставной HTTP. Проверяется то, чего не видно по зелёному прогону:
нарушение правила не тратит модель; самооценка судьи-модели только добавляет
нарушения (утверждение без опоры на базу или со ссылкой на чужую запись — не
пропуск); судья, который не смог проверить, не пропускает (3.3 A5); черновик,
написанный поверх пропуска брифа, решает человек.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from backend.config import llm as llm_cfg
from backend.features.agent.stages import GuardInput, VerdictKind
from backend.features.agent.writer import load_prompt
from backend.features.core.domain import Stage
from backend.features.sales.agent import judge
from tests.test_sales_agent_judge_rules import CALL, CLEAN, LETTER
from tests.test_sales_agent_situation import SETTINGS, TOKENS, Plug, llm

__all__ = ["llm"]  # фикстура подставной модели — отсюда её видит pytest

LINES = (
    "[move price] Ответить о цене по политике цен из базы.",
    f"[cta call] {CALL}",
    "[kb:3 price_policy] Цены: Цены в письме не называем, предлагаем короткий созвон.",
    "[kb:4 case] Магазин: Трафик вырос в 3,7 раза за 41 день.",
    f"[link call] {CALL}",
    "[language] ru",
)
FINE = {
    "claims": [{"quote": "трафик вырос в 3,7 раза", "kb": [4]}],
    "promises": [],
    "tone": {"ok": True, "problem": ""},
}


def check(
    draft: str = CLEAN, *, lines: tuple[str, ...] = LINES, letter: str = LETTER
) -> GuardInput:
    return GuardInput(
        stage=Stage.SALES, draft=draft, incoming=letter, facts=lines, settings=SETTINGS, attempt=0
    )


# --- разбор ответа судьи-модели -----------------------------------------------------------


def _opinion(answer: dict[str, Any] | str) -> list[str] | None:
    content = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
    return judge.parse(content, known={3: "Цены", 4: "Магазин"})


def test_supported_claims_and_good_tone_pass() -> None:
    assert _opinion(FINE) == []


@pytest.mark.parametrize(
    ("answer", "problem"),
    [
        (
            {**FINE, "claims": [{"quote": "Аудит за  день", "kb": []}]},
            "утверждение без опоры на базу: «Аудит за день» — уберите его или возьмите из фактов",
        ),
        (
            {**FINE, "claims": [{"quote": "Гарантия результата", "kb": [3, 99]}]},
            "утверждение ссылается на запись базы №99, которой нет в брифе: «Гарантия результата»",
        ),
        (
            {**FINE, "promises": ["скидка за быстрый ответ"]},
            "обещание вне базы: «скидка за быстрый ответ» — уберите его",
        ),
        ({**FINE, "tone": {"ok": False, "problem": "давит"}}, "тон: давит"),
        ({**FINE, "tone": {"ok": "yes"}}, "тон: судья не подтвердил, что тон в порядке"),
    ],
)
def test_model_opinion_only_adds_violations(answer: dict[str, Any], problem: str) -> None:
    assert _opinion(answer) == [problem]


@pytest.mark.parametrize(
    "answer",
    [
        "Черновик хороший.",
        '["claims"]',
        {**FINE, "claims": "нет"},
        {**FINE, "claims": ["трафик"]},
        {**FINE, "promises": [7]},
        {**FINE, "tone": "ok"},
        {"claims": [], "promises": []},
    ],
)
def test_unreadable_opinion_is_none_not_a_pass(answer: dict[str, Any] | str) -> None:
    assert _opinion(answer) is None


# --- вердикт ------------------------------------------------------------------------------


async def test_rule_violation_blocks_without_the_model(llm: Plug) -> None:
    model = llm(judge=[FINE])

    verdict = await judge.guard(check(CLEAN.replace("в 3,7 раза", "в 5 раз")))

    assert verdict.kind is VerdictKind.BLOCK
    assert verdict.reasons[0].startswith("число 5 не из базы")
    assert (verdict.tokens, model.sent["judge"]) == (0, [])


async def test_clean_draft_goes_to_the_model_with_records_and_passes(llm: Plug) -> None:
    model = llm(judge=[FINE])

    verdict = await judge.guard(check())

    [sent] = model.sent["judge"]
    assert (verdict.kind, verdict.tokens) == (VerdictKind.ALLOW, TOKENS)
    assert sent["model"] == llm_cfg.SALES_JUDGE_MODEL
    assert sent["messages"][0]["content"] == load_prompt(judge.PROMPT)
    user = sent["messages"][1]["content"]
    assert '"id": 4' in user
    assert "Трафик вырос в 3,7 раза" in user
    assert user.count("DRAFT>>>") == 1


async def test_unsupported_claim_from_the_model_blocks(llm: Plug) -> None:
    llm(judge=[{**FINE, "claims": [{"quote": "работаем 24 часа", "kb": []}]}])

    verdict = await judge.guard(check())

    assert verdict.kind is VerdictKind.BLOCK
    assert verdict.reasons == (
        "утверждение без опоры на базу: «работаем 24 часа» — уберите его или возьмите из фактов",
    )
    assert verdict.tokens == TOKENS


@pytest.mark.parametrize(("status", "answer"), [(503, "{}"), (200, "Всё хорошо.")])
async def test_judge_that_could_not_check_does_not_pass(
    llm: Plug, status: int, answer: str
) -> None:  # 3.3 A5
    llm(status, judge=[answer])

    verdict = await judge.guard(check())

    assert verdict.kind is VerdictKind.ESCALATE
    [why] = verdict.reasons
    assert why.startswith("судья-модель")


@pytest.mark.parametrize(
    ("lines", "letter", "words"),
    [
        (LINES[2:], LETTER, "бриф не дал хода"),
        (LINES, "この監査はいくらですか", "язык письма собеседника не определён"),
    ],
)
async def test_draft_the_judge_cannot_check_goes_to_a_human(
    llm: Plug, lines: tuple[str, ...], letter: str, words: str
) -> None:
    model = llm(judge=[FINE])

    verdict = await judge.guard(check(lines=lines, letter=letter))

    assert verdict.kind is VerdictKind.ESCALATE
    assert words in verdict.reasons[0]
    assert model.sent["judge"] == []
