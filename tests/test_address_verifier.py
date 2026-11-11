"""Клиент проверки адреса у Hunter (`contacts/verifier.py`) — за `httpx.MockTransport`, без сети.

Вердикт и отказ сервиса — разные вещи: слово Hunter возвращается как есть, а отказ — исключением
своего рода, по которому проверка перед письмом решает, встать ей или идти дальше. Формы ответов
— по документации Hunter и по живым отказам, разобранным у поиска адресов (`provider.py`).
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
from backend.features.contacts.provider import (
    ProviderBlockedError,
    ProviderDomainRefusedError,
    ProviderError,
    ProviderKeyRefusedError,
    ProviderQuotaError,
    ProviderRateLimitError,
)
from backend.features.contacts.verifier import (
    KEY_REFUSED,
    NO_KEY,
    STILL_CHECKING,
    STILL_CHECKING_ASKS,
    HunterVerifier,
    NoKeyError,
    StillCheckingError,
    Verdict,
)

Handler = Callable[[httpx.Request], httpx.Response]
EMAIL = "editor@site.example.test"
#: Ключ теста — выдуманный: сеть подменена, настоящего ключа тест не видит.
KEY = "k-test"


def _script(*answers: httpx.Response | Exception) -> tuple[Handler, list[httpx.Request]]:
    """Ответы по очереди; последний повторяется. Запросы — в список."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        answer = answers[min(len(seen), len(answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer

    return handler, seen


def _said(status: str, score: int | None = 91) -> httpx.Response:
    return httpx.Response(200, json={"data": {"status": status, "score": score}})


def _refusal(code: int, marker: str) -> httpx.Response:
    return httpx.Response(code, json={"errors": [{"id": marker, "details": "said so"}]})


async def _verify(handler: Handler, *, key: str = KEY) -> Verdict:
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        return await HunterVerifier(http, api_key=key).verify(EMAIL)


@pytest.mark.parametrize(
    "status", ["valid", "invalid", "accept_all", "webmail", "disposable", "unknown"]
)
async def test_the_word_of_the_service_comes_back_as_it_is(status: str) -> None:
    handler, seen = _script(_said(status))

    verdict = await _verify(handler)

    assert verdict == Verdict(status, 91, units=1)
    assert len(seen) == 1


async def test_the_key_goes_in_the_header_not_in_the_address() -> None:
    handler, seen = _script(_said("valid"))

    await _verify(handler)

    [request] = seen
    assert request.url.path == "/v2/email-verifier"
    assert request.url.params["email"] == EMAIL
    assert KEY not in str(request.url)
    assert request.headers["Authorization"] == f"Bearer {KEY}"


async def test_without_a_key_nobody_is_asked() -> None:
    handler, seen = _script(_said("valid"))

    with pytest.raises(NoKeyError, match="ключ проверки адресов не задан") as caught:
        await _verify(handler, key="")

    assert str(caught.value) == NO_KEY
    assert seen == []


async def test_a_missing_score_is_not_a_refusal() -> None:
    handler, _ = _script(_said("accept_all", None))

    assert await _verify(handler) == Verdict("accept_all", None)


@pytest.mark.parametrize(
    ("answer", "kind"),
    [
        (
            httpx.Response(401, json={"errors": [{"id": "authentication_failed"}]}),
            ProviderKeyRefusedError,
        ),
        (_refusal(429, "restricted_account"), ProviderBlockedError),
        (_refusal(429, "usage_exceeded"), ProviderQuotaError),
        (_refusal(400, "blacklisted_domain"), ProviderDomainRefusedError),
        (httpx.Response(429), ProviderRateLimitError),
        (httpx.Response(503), ProviderError),
        (httpx.Response(200, text="<html>proxy</html>"), ProviderError),
        (httpx.Response(200, json={"data": {"status": "great"}}), ProviderError),
        (httpx.Response(200, json={"meta": {}}), ProviderError),
        (httpx.ConnectError("no route"), ProviderError),
    ],
)
async def test_a_refusal_is_an_exception_of_its_kind_not_a_verdict(
    answer: httpx.Response | Exception, kind: type[ProviderError]
) -> None:
    handler, _ = _script(answer)

    with pytest.raises(ProviderError) as caught:
        await _verify(handler)

    assert type(caught.value) is kind


async def test_a_refused_key_is_said_in_words_without_the_setting_name() -> None:
    handler, seen = _script(httpx.Response(401))

    with pytest.raises(ProviderKeyRefusedError) as caught:
        await _verify(handler)

    assert str(caught.value) == KEY_REFUSED
    assert len(seen) == 1, "непринятый ключ повтором не лечится"


async def test_a_quota_refusal_is_not_repeated() -> None:
    handler, seen = _script(_refusal(429, "usage_exceeded"))

    with pytest.raises(ProviderQuotaError):
        await _verify(handler)

    assert len(seen) == 1


async def test_a_busy_service_is_asked_again_and_answers() -> None:
    handler, seen = _script(httpx.Response(503), httpx.Response(429), _said("valid"))

    assert (await _verify(handler)).status == "valid"
    assert len(seen) == 3


async def test_still_checking_is_asked_again_at_once() -> None:
    handler, seen = _script(httpx.Response(202, json={"data": {}}), _said("valid", 88))

    assert await _verify(handler) == Verdict("valid", 88)
    assert len(seen) == 2


async def test_still_checking_after_the_asks_is_no_verdict() -> None:
    handler, seen = _script(httpx.Response(202, json={"data": {"status": None}}))

    with pytest.raises(StillCheckingError) as caught:
        await _verify(handler)

    assert str(caught.value) == STILL_CHECKING
    assert len(seen) == 1 + STILL_CHECKING_ASKS
