"""Семейство модели решает, какое тело уйдёт провайдеру.

11.12.2026 провайдер снимает `gpt-5` и `gpt-5-mini`; замена — шестое семейство (`gpt-6.1-sol`,
`gpt-6.1-terra`, `gpt-6-luna`). Две мины, ради которых этот файл: имя нового семейства в обычной
ветке — `max_tokens` и температура, отказ провайдера на каждом вызове; и `minimal`, которого у
шестого семейства нет, — тот же отказ у ключей, писем, разбора цены и переадресации. Тело пятого
семейства при этом — до байта прежнее: смена модели — одна строка `.env`, а не выкатка.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from backend.config import judge as judge_cfg
from backend.config import llm as llm_cfg
from backend.config import topics as topics_cfg
from backend.features.agent import writer
from backend.features.crawl import judge_client
from backend.features.donors import publisher_judge
from backend.features.keywords import client as keywords
from backend.features.letters import rewrite
from backend.features.replies import extract, redirect_ask
from backend.features.topics import classifier
from backend.shared.llm import (
    EFFORT_SCALE,
    GPT6_EFFORTS,
    GPT6_SOL_EFFORTS,
    ModelClient,
    is_reasoning,
    probe_payload,
    reasoning_effort,
)

ROOT = Path(__file__).resolve().parents[1]
NEW = ("gpt-6.1-sol", "gpt-6.1-terra", "gpt-6-luna")
#: Ступени, которые принимает модель, — по документации провайдера (сверено 17.11.2026).
ACCEPTS = {
    "gpt-6.1-sol": {"low", "medium", "high", "xhigh", "max"},
    "gpt-6.1-terra": {"none", "low", "medium", "high", "xhigh", "max"},
    "gpt-6-luna": {"none", "low", "medium", "high", "xhigh", "max"},
}


@pytest.mark.parametrize("model", NEW)
def test_the_new_family_reasons(model: str) -> None:
    assert is_reasoning(model)


def test_every_default_model_reasons() -> None:
    """Страховка от тихой обычной ветки при следующем переименовании: умолчание без префикса
    рассуждающего семейства ушло бы с `max_tokens` и температурой. Умолчания — из кода, а не из
    окружения: `.env` рабочей копии их не подменит."""
    defaults = {
        name: str(field.default)
        for name, field in llm_cfg._Llm.model_fields.items()
        if name.endswith("_model") and field.default
    }

    assert {"keygen_model", "judge_model", "letters_model", "agent_model", "topic_model"} <= set(
        defaults
    )
    assert [name for name, model in defaults.items() if not is_reasoning(model)] == []


# --- усилие рассуждения ----------------------------------------------------------------------------


@pytest.mark.parametrize("model", ["gpt-5", "gpt-5-mini", "gpt-5-nano", "o3", "o4-mini", "gpt-4.1"])
@pytest.mark.parametrize("wanted", [*EFFORT_SCALE, "lowest"])
def test_the_fifth_family_and_the_rest_get_what_was_asked(model: str, wanted: str) -> None:
    assert reasoning_effort(model, wanted) == wanted


@pytest.mark.parametrize(
    ("model", "wanted", "sent"),
    [
        ("gpt-6.1-terra", "minimal", "low"),
        ("gpt-6-luna", "minimal", "low"),
        ("gpt-6.1-sol", "minimal", "low"),
        ("gpt-6.1-sol", "none", "low"),
        ("gpt-6.1-terra", "none", "none"),
        ("gpt-6-luna", "none", "none"),
        ("gpt-6.1-terra", "medium", "medium"),
        ("gpt-6.1-sol", "max", "max"),
    ],
)
def test_a_step_the_family_lacks_becomes_the_nearest_above(
    model: str, wanted: str, sent: str
) -> None:
    """`minimal` → `low` — совет провайдера; у sol нет и `none`, а ниже `low` у неё ничего."""
    assert reasoning_effort(model, wanted) == sent


@pytest.mark.parametrize("model", NEW)
@pytest.mark.parametrize("wanted", EFFORT_SCALE)
def test_the_new_family_is_never_asked_a_step_it_refuses(model: str, wanted: str) -> None:
    sent = reasoning_effort(model, wanted)

    assert sent in ACCEPTS[model]
    assert EFFORT_SCALE.index(sent) >= EFFORT_SCALE.index(wanted)


def test_a_step_off_the_scale_goes_as_is() -> None:
    """Опечатку в `.env` назовёт отказ провайдера, а не тихая догадка здесь."""
    assert reasoning_effort("gpt-6.1-terra", "lo") == "lo"


def test_the_family_steps_are_the_documented_ones() -> None:
    """Таблица ступеней меняется только вместе с документацией провайдера, а не попутно."""
    assert ACCEPTS["gpt-6.1-terra"] == GPT6_EFFORTS
    assert ACCEPTS["gpt-6.1-sol"] == GPT6_SOL_EFFORTS


# --- общие сборщики тела ---------------------------------------------------------------------------

Build = Callable[[str], dict[str, Any]]
#: Каждый общий сборщик тела и усилие, которое он просит: пятое семейство получает именно его.
BUILDERS: dict[str, tuple[Build, str]] = {
    "ключи": (
        lambda m: keywords.build_payload(m, keywords.Ask(system="s", user="u", max_phrases=30)),
        "minimal",
    ),
    "письмо": (
        lambda m: rewrite.build_payload(
            m, zones={"greeting": "Hi"}, about=rewrite.Personalization(host="a.test", country="us")
        ),
        "minimal",
    ),
    "разбор цены": (lambda m: extract.build_payload(m, text="t", subject="s"), "minimal"),
    "переадресация": (lambda m: redirect_ask.build_payload(m, text="t", subject="s"), "minimal"),
    "агент": (lambda m: writer.build_payload(m, user="u"), "low"),
    "судья рекламодателей": (
        lambda m: judge_client.build_payload(m, user="u", system="s"),
        judge_client.REASONING_EFFORT,
    ),
    "второй круг": (
        lambda m: judge_client.build_payload(m, user="u", system="s", effort="medium", room=8000),
        "medium",
    ),
    "судья площадки": (
        lambda m: publisher_judge.build_payload(m, "a.test", "t"),
        judge_cfg.REASONING_EFFORT,
    ),
    "тематика": (lambda m: classifier.build_payload(m, "a.test", "t"), topics_cfg.REASONING_EFFORT),
    "проверка модели": (probe_payload, "low"),
}


@pytest.mark.parametrize("builder", sorted(BUILDERS))
@pytest.mark.parametrize("model", ["gpt-5", "gpt-5-mini"])
def test_the_fifth_family_body_keeps_its_effort(builder: str, model: str) -> None:
    build, wanted = BUILDERS[builder]

    payload = build(model)

    assert payload["reasoning_effort"] == wanted
    assert "max_completion_tokens" in payload
    assert not {"max_tokens", "temperature"} & set(payload)


@pytest.mark.parametrize("builder", sorted(BUILDERS))
@pytest.mark.parametrize("model", NEW)
def test_the_new_family_body_is_one_the_provider_accepts(builder: str, model: str) -> None:
    """Рассуждающая ветка без температуры и со ступенью, которую модель принимает."""
    build, _ = BUILDERS[builder]

    payload = build(model)

    assert payload["model"] == model
    assert payload["reasoning_effort"] in ACCEPTS[model]
    assert payload["max_completion_tokens"] > 0
    assert not {"max_tokens", "temperature", "top_p", "logprobs"} & set(payload)


# --- модели разбора цены и переадресации -----------------------------------------------------------


@pytest.mark.parametrize(
    ("client_type", "setting"),
    [(extract.ExtractClient, "REPLY_PARSE_MODEL"), (redirect_ask.RedirectClient, "REDIRECT_MODEL")],
)
async def test_reply_parse_and_redirect_follow_the_letters_model_until_set(
    monkeypatch: pytest.MonkeyPatch, client_type: type[ModelClient], setting: str
) -> None:
    """Своя строка `.env` — своя модель; пусто — модель писем в момент вызова, как до развязки."""
    monkeypatch.setattr(llm_cfg, "LETTERS_MODEL", "letters-model")
    monkeypatch.setattr(llm_cfg, setting, "")
    follows = client_type(api_key="k")
    monkeypatch.setattr(llm_cfg, setting, "own-model")
    own = client_type(api_key="k")
    named = client_type(api_key="k", model="named")

    assert (follows.model, own.model, named.model) == ("letters-model", "own-model", "named")
    for client in (follows, own, named):
        await client.aclose()


def test_a_trailing_space_in_env_does_not_reach_the_provider() -> None:
    """Строка `.env` с пробелом в конце при смене модели — «модели нет» на каждом вызове.

    Импорт — в подпроцессе: в этом процессе конфиг уже загружен с настройками теста."""
    names = (
        "LLM_KEYGEN_MODEL",
        "LLM_JUDGE_MODEL",
        "LLM_LETTERS_MODEL",
        "LLM_REPLY_PARSE_MODEL",
        "LLM_REDIRECT_MODEL",
        "LLM_AGENT_MODEL",
        "LLM_SALES_CLASSIFY_MODEL",
    )
    env = {**os.environ, **dict.fromkeys(names, " gpt-6.1-terra \t"), "PYTHONPATH": str(ROOT)}
    read = (
        "from backend.config import llm as c\n"
        "print([c.KEYGEN_MODEL, c.JUDGE_MODEL, c.LETTERS_MODEL, c.REPLY_PARSE_MODEL,\n"
        "       c.REDIRECT_MODEL, c.AGENT_MODEL, c.SALES_CLASSIFY_MODEL])"
    )

    done = subprocess.run(
        [sys.executable, "-c", read],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == str(["gpt-6.1-terra"] * len(names))
