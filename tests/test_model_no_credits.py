"""Модель без кредитов (слово владельца 20.10.2026): на пустом счету OpenAI прогон не начинается и
не судит вхолостую.

Общая часть (`shared/llm.py`): 429 «нет кредитов» отличается от 429 частоты по телу и не
повторяется; 402 — тоже «нет кредитов»; проверка модели (`probe`) — тем же `post_chat`, с его
повторами; любой отказ проверки — слова оператору (`words_of`). Места одного вызова на письмо
или действие — черновик агента, разбор ответа, ключи моделью, уникализация письма — проверки до
вызова не ждут: их отказ «нет кредитов» — постоянный, без повторов, на экране — словами
владельца. Провайдер — подставной транспорт
(`httpx.MockTransport`) с телом ответа, каким оно пришло от OpenAI 20.10.2026; к настоящей модели —
ни одного вызова. Помощники `answering` и `REFUSALS` берут тесты продаж и доноров.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from backend.features.agent.writer import AgentWriter, DraftUnavailableError
from backend.features.keywords.client import KeygenClient, LlmError
from backend.features.keywords.generator import PoolBuilder
from backend.features.letters.rewrite import RewriteClient
from backend.features.replies.extract import ExtractClient
from backend.features.replies.inbound import Incoming
from backend.features.runs.failures import is_permanent
from backend.shared import llm
from backend.shared.llm import (
    ATTEMPTS,
    KEY_WORDS,
    NO_CREDITS_WORDS,
    ModelUnreadyError,
    RefusalKind,
    ensure_model,
    post_chat,
    unready,
    words_of,
)
from tests.test_agent_writer import _request
from tests.test_letter_rewrite_client import ABOUT, _letter

#: Настоящая проверка — до подмены: `conftest._model_answers_the_probe` ставит «ответила» всем.
REAL_PROBE = llm.probe
KEY = "sk-test-not-a-real-key"  # pragma: allowlist secret
MODEL = "gpt-5-mini"

#: Тело отказа «нет кредитов» — как его вернул OpenAI 20.10.2026.
NO_CREDITS_BODY = {
    "error": {
        "message": "You have no credits remaining. Add credits to continue using the API at "
        "https://platform.openai.com/settings/organization/billing/.",
        "type": "insufficient_quota",
        "param": None,
        "code": "credit_balance_exhausted",
    }
}
#: 429 частоты: проходит сам, его повторяют. Текст выдуман, без чисел учётки.
RATE_BODY = {
    "error": {
        "message": "Rate limit reached for requests. Please try again in a moment.",
        "type": "requests",
        "param": None,
        "code": "rate_limit_exceeded",
    }
}
#: Ответ модели: слово и расход.
ANSWER = {
    "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 9, "completion_tokens": 7, "total_tokens": 16},
}

Reply = httpx.Response | Exception


def no_credits() -> httpx.Response:
    return httpx.Response(429, json=NO_CREDITS_BODY)


class Provider:
    """Подставной провайдер модели: ответы по очереди, последний — сколько ни спроси; тела
    запросов и ключи — для проверки."""

    def __init__(self, *replies: Callable[[], Reply]) -> None:
        self.replies = list(replies)
        self.bodies: list[dict[str, Any]] = []
        self.keys: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        self.keys.append(request.headers.get("Authorization", ""))
        reply = self.replies[min(len(self.bodies), len(self.replies)) - 1]()
        if isinstance(reply, Exception):
            raise reply
        return reply

    @property
    def calls(self) -> int:
        return len(self.bodies)


def answering(
    monkeypatch: pytest.MonkeyPatch, *replies: Callable[[], Reply], key: str = KEY
) -> Provider:
    """Настоящая проверка модели с подставным провайдером — для смет, запусков и задач: им
    проверка достаётся через `llm.unready`. Ключ `key` — только у проверки этого теста: настройки
    процесса (`LLM_API_KEY`) не трогаются, и прочие вызовы модели теста его не видят."""
    provider = Provider(*replies)

    async def checked(http: httpx.AsyncClient, *, api_key: str, model: str) -> Any:
        return await REAL_PROBE(http, api_key=key, model=model)

    monkeypatch.setattr(llm, "probe", checked)
    monkeypatch.setattr(
        llm, "probe_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(provider))
    )
    return provider


def answered() -> httpx.Response:
    return httpx.Response(200, json=ANSWER)


#: Каждый вид отказа проверки — и слова, которыми его называют оператору.
REFUSALS: dict[str, tuple[Callable[[], Reply], str]] = {
    "нет кредитов": (no_credits, NO_CREDITS_WORDS),
    "402": (
        lambda: httpx.Response(402, json={"error": {"message": "Payment required"}}),
        NO_CREDITS_WORDS,
    ),
    "ключ 401": (
        lambda: httpx.Response(
            401,
            json={"error": {"message": "Incorrect API key provided", "code": "invalid_api_key"}},
        ),
        KEY_WORDS,
    ),
    "ключ 403": (
        lambda: httpx.Response(403, json={"error": {"message": "Project has no access to model"}}),
        KEY_WORDS,
    ),
    "403 страны": (
        lambda: httpx.Response(
            403,
            json={
                "error": {
                    "message": "Country, region, or territory not supported",
                    "type": "request_forbidden",
                    "code": "unsupported_country_region_territory",
                }
            },
        ),
        "модель не ответила (HTTP 403: Country, region, or territory not supported) — проверить "
        "счёт нельзя, прогон не начат",
    ),
    "5xx": (
        lambda: httpx.Response(503, json={"error": {"message": "The server is overloaded"}}),
        "модель не ответила (HTTP 503: The server is overloaded) — проверить счёт нельзя, "
        "прогон не начат",
    ),
    "частота после повторов": (
        lambda: httpx.Response(429, json=RATE_BODY),
        f"модель не ответила (HTTP 429: {RATE_BODY['error']['message']}) — проверить счёт "
        "нельзя, прогон не начат",
    ),
    "сеть": (
        lambda: httpx.ConnectError("connection refused"),
        "модель не ответила (ConnectError('connection refused')) — проверить счёт нельзя, "
        "прогон не начат",
    ),
    "тайм-аут": (
        lambda: httpx.ReadTimeout("timed out"),
        "модель не ответила (ReadTimeout('timed out')) — проверить счёт нельзя, прогон не начат",
    ),
    "ответ не разобран": (
        lambda: httpx.Response(200, text="<html>шлюз</html>"),
        "модель не ответила (ответ не разобран как JSON) — проверить счёт нельзя, прогон не начат",
    ),
}


async def _chat(provider: Provider) -> Any:
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
        return await post_chat(http, api_key=KEY, payload={"model": MODEL}, topic="тест")


# --- 429 двух видов, 402 -----------------------------------------------------------------------


async def test_no_credits_is_its_own_kind_permanent_and_not_repeated() -> None:
    provider = Provider(no_credits)

    refusal = await _chat(provider)

    assert provider.calls == 1
    assert (refusal.kind, refusal.permanent, refusal.status) == (RefusalKind.NO_CREDITS, True, 429)
    assert refusal.no_credits
    assert "You have no credits remaining" in refusal.detail  # ответ провайдера — в журнал
    assert str(refusal) == NO_CREDITS_WORDS  # на экран — слова


async def test_a_rate_limit_is_still_repeated_and_worth_repeating() -> None:
    provider = Provider(lambda: httpx.Response(429, json=RATE_BODY))

    refusal = await _chat(provider)

    assert provider.calls == ATTEMPTS
    assert (refusal.kind, refusal.permanent, refusal.no_credits) == (
        RefusalKind.REFUSED,
        False,
        False,
    )


async def test_a_rate_limit_that_passes_gives_the_answer() -> None:
    provider = Provider(lambda: httpx.Response(429, json=RATE_BODY), answered)

    body = await _chat(provider)

    assert provider.calls == 2
    assert body == ANSWER


@pytest.mark.parametrize(
    "error",
    [
        {"type": "insufficient_quota", "code": "credit_balance_exhausted"},
        {"type": "insufficient_quota", "code": "insufficient_quota"},
        {"type": "requests", "code": "insufficient_quota"},
    ],
    ids=["type-2026", "both", "code-only"],
)
async def test_insufficient_quota_in_the_type_or_the_code_is_no_credits(
    error: dict[str, str],
) -> None:
    provider = Provider(lambda: httpx.Response(429, json={"error": {"message": "нет", **error}}))

    refusal = await _chat(provider)

    assert (provider.calls, refusal.kind) == (1, RefusalKind.NO_CREDITS)


async def test_402_is_no_credits_whatever_the_body() -> None:
    provider = Provider(lambda: httpx.Response(402, text="Payment Required"))

    refusal = await _chat(provider)

    assert (provider.calls, refusal.kind, refusal.permanent) == (1, RefusalKind.NO_CREDITS, True)


async def test_a_401_stays_a_permanent_refusal_of_the_key() -> None:
    refusal = await _chat(Provider(REFUSALS["ключ 401"][0]))

    assert (refusal.kind, refusal.permanent, refusal.status) == (RefusalKind.REFUSED, True, 401)
    assert words_of(refusal) == KEY_WORDS


# --- проверка модели --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "limits"),
    [
        (
            MODEL,
            # Литералы, а не константы модуля: правка усилия или потолка обязана краснеть здесь —
            # `minimal` пятого семейства закрыл бы запуск исправной модели другого (отказ 400).
            {"max_completion_tokens": 1024, "reasoning_effort": "low"},
        ),
        ("gpt-4o-mini", {"max_tokens": 8}),
    ],
    ids=["reasoning", "plain"],
)
async def test_the_probe_is_a_tiny_call_of_the_same_model_with_the_same_key(
    model: str, limits: dict[str, Any]
) -> None:
    provider = Provider(answered)

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
        assert await REAL_PROBE(http, api_key=KEY, model=model) is None

    [body] = provider.bodies
    assert body["model"] == model
    assert {key: body[key] for key in limits} == limits
    assert len(body) == 2 + len(limits)  # модель, сообщение и потолок — ничего лишнего
    assert provider.keys == [f"Bearer {KEY}"]


@pytest.mark.parametrize(
    "reply",
    [
        {"choices": [{"message": {"content": ""}, "finish_reason": "length"}], "usage": {}},
        {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]},
        {"choices": []},
    ],
    ids=["cut-off", "empty", "no-choices"],
)
async def test_any_answer_200_means_the_model_answers(reply: dict[str, Any]) -> None:
    provider = Provider(lambda: httpx.Response(200, json=reply))

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
        assert await REAL_PROBE(http, api_key=KEY, model=MODEL) is None


#: Отказ OpenAI, когда рассуждение съело потолок вывода (прод 21.10.2026, gpt-5-mini, потолок 64).
OUT_OF_ROOM = {
    "error": {
        "message": "Could not finish the message because max_tokens or model output limit was "
        "reached. Please try again with higher max_tokens.",
        "type": "invalid_request_error",
    }
}


async def test_a_model_that_ran_out_of_our_output_limit_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Модель отвечала, пока не кончился потолок вывода, — ключ принят и счёт жив: запуск не
    закрывается (смета прода 21.10.2026 закрывала его при пополненном счёте)."""
    provider = answering(monkeypatch, lambda: httpx.Response(400, json=OUT_OF_ROOM))

    assert await unready(MODEL) is None
    assert provider.calls == 1  # отказ 400 не повторяется


async def test_a_request_the_model_did_not_accept_still_closes_the_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Слова «max_tokens» сами по себе — не «модель ответила»: неверный запрос модель не принимала,
    и судья с тем же запросом не ответил бы тоже."""
    body = {
        "error": {
            "message": "Unsupported parameter: 'max_tokens' is not supported with this model. "
            "Use 'max_completion_tokens' instead.",
            "type": "invalid_request_error",
        }
    }
    answering(monkeypatch, lambda: httpx.Response(400, json=body))

    said = await unready(MODEL)

    assert said is not None
    assert said.startswith("модель не ответила (HTTP 400: Unsupported parameter: 'max_tokens'")


@pytest.mark.parametrize("kind", list(REFUSALS))
async def test_every_refusal_of_the_probe_is_named_in_words(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    reply, words = REFUSALS[kind]
    answering(monkeypatch, reply)

    assert await unready(MODEL) == words


async def test_a_region_refusal_named_in_the_type_is_not_the_key_either(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Страна запроса не обслуживается — признак бывает и в `error.type`: ключ годен, модель отсюда
    недоступна — «модель не ответила», а не «ключ не принят»."""
    body = {
        "error": {"message": "Region not supported", "type": "unsupported_country_region_territory"}
    }
    answering(monkeypatch, lambda: httpx.Response(403, json=body))

    assert await unready(MODEL) == (
        "модель не ответила (HTTP 403: Region not supported) — проверить счёт нельзя, прогон не начат"
    )


async def test_one_dropped_connection_does_not_close_the_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = answering(monkeypatch, lambda: httpx.ConnectError("reset"), answered)

    assert await unready(MODEL) is None
    assert provider.calls == 2


@pytest.mark.parametrize("key", ["", "  ", "ключ"], ids=["empty", "blank", "not-ascii"])
async def test_a_key_that_cannot_be_sent_is_not_accepted_without_a_call(
    monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    provider = answering(monkeypatch, answered, key=key)

    assert await unready(MODEL) == KEY_WORDS
    assert provider.calls == 0


async def test_each_model_is_asked_once_and_none_means_nothing_to_ask(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = answering(monkeypatch, answered)

    assert await unready() is None
    assert provider.calls == 0
    assert await unready(MODEL, MODEL, "gpt-5") is None
    assert [body["model"] for body in provider.bodies] == [MODEL, "gpt-5"]


async def test_the_first_model_that_does_not_answer_closes_the_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = answering(monkeypatch, no_credits)

    assert await unready(MODEL, "gpt-5") == NO_CREDITS_WORDS
    assert provider.calls == 1


async def test_a_task_gets_a_permanent_refusal_in_the_same_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answering(monkeypatch, no_credits)

    with pytest.raises(ModelUnreadyError) as refused:
        await ensure_model(MODEL)

    assert str(refused.value) == NO_CREDITS_WORDS
    assert is_permanent(refused.value)


async def test_the_key_is_not_written_to_the_log(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    answering(monkeypatch, REFUSALS["ключ 401"][0])
    caplog.set_level(logging.DEBUG)

    await unready(MODEL)

    assert caplog.records
    assert KEY not in caplog.text


# --- места одного вызова: черновик агента, разбор ответа, ключи моделью, уникализация ---------

#: «Нет кредитов» — телом 20.10.2026 и прежним, где признак и в типе, и в коде.
EMPTY = {
    "2026": NO_CREDITS_BODY,
    "quota": {
        "error": {
            "message": "You exceeded your current quota",
            "type": "insufficient_quota",
            "code": "insufficient_quota",
        }
    },
}


def _model(provider: Provider) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(provider))


@pytest.mark.parametrize("body", list(EMPTY.values()), ids=list(EMPTY))
async def test_the_agent_draft_names_the_empty_account_once_and_for_good(
    body: dict[str, Any],
) -> None:
    provider = Provider(lambda: httpx.Response(429, json=body))

    async with _model(provider) as http:
        with pytest.raises(DraftUnavailableError) as refused:
            await AgentWriter(http, model="gpt-5", api_key=KEY).write(_request())

    assert str(refused.value) == NO_CREDITS_WORDS
    assert refused.value.permanent is True  # задача агента не крутит его повторами
    assert provider.calls == 1


async def test_a_rate_limit_of_the_agent_draft_is_still_repeated_and_worth_repeating() -> None:
    provider = Provider(lambda: httpx.Response(429, json=RATE_BODY))

    async with _model(provider) as http:
        with pytest.raises(DraftUnavailableError) as refused:
            await AgentWriter(http, model="gpt-5", api_key=KEY).write(_request())

    assert refused.value.permanent is False  # задача агента повторит позже
    assert provider.calls == ATTEMPTS


async def test_the_reply_note_names_the_empty_account() -> None:
    provider = Provider(no_credits)
    incoming = Incoming(
        message_id="<in-1@site.example.test>",
        to=(),
        from_email="elena@donor.example.test",
        from_name="Elena",
        subject="Re: Guest article",
        text="Our price is $90 for a guest post, dofollow.",
    )

    async with _model(provider) as http:
        found = await ExtractClient(http, model="gpt-4o-mini", api_key=KEY).extract(incoming)

    assert found.notes == (NO_CREDITS_WORDS,)
    assert provider.calls == 1


async def test_keywords_by_the_model_name_the_empty_account_once() -> None:
    provider = Provider(no_credits)

    async with _model(provider) as http:
        client = KeygenClient(http, api_key=KEY)
        with pytest.raises(LlmError, match=NO_CREDITS_WORDS):
            await PoolBuilder(client).build(cap=10, country="US")

    assert (client.calls, provider.calls) == (1, 1)


async def test_the_letter_rewrite_names_the_empty_account() -> None:
    provider = Provider(no_credits)

    async with _model(provider) as http:
        result = await RewriteClient(http, model="gpt-5", api_key=KEY).rewrite(_letter(), ABOUT)

    assert result.notes == [NO_CREDITS_WORDS]
    assert provider.calls == 1
