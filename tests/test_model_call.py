"""Вызов модели: отказ, который повтор не лечит, называется отказом, а не сетью.

24.09.2026 судья на сервере без ключа модели записал 44 домена прогона №21
с причиной «модель, сеть (можно повторить): LocalProtocolError». Повторять
было нечего: заголовок `Bearer ` без ключа httpx не собирает вовсе.
"""

from __future__ import annotations

import httpx
import pytest
from backend.shared.llm import Refusal, RefusalKind, post_chat


class NoCalls:
    """Клиент, которому звонить нельзя: вызов — уже ошибка."""

    async def post(self, *args: object, **kwargs: object) -> httpx.Response:
        raise AssertionError("без ключа модель вызываться не должна")


@pytest.mark.parametrize("key", ["", "   ", "\n"])
async def test_no_key_is_a_permanent_refusal_without_a_call(key: str) -> None:
    answer = await post_chat(NoCalls(), api_key=key, payload={}, topic="проверка")  # type: ignore[arg-type]

    assert isinstance(answer, Refusal)
    assert answer.permanent
    assert answer.kind is RefusalKind.LOCAL
    assert "LLM_API_KEY" in str(answer)
    assert "можно повторить" not in str(answer)


async def test_a_request_httpx_cannot_build_is_permanent() -> None:
    """Пробел или перевод строки внутри ключа — тот же класс: запрос не
    собран у нас, и три попытки дали бы три одинаковых отказа."""
    calls: list[int] = []

    class Broken:
        async def post(self, *args: object, **kwargs: object) -> httpx.Response:
            calls.append(1)
            raise httpx.LocalProtocolError("Illegal header value b'Bearer sk x'")

    answer = await post_chat(Broken(), api_key="sk x", payload={}, topic="проверка")  # type: ignore[arg-type]

    assert isinstance(answer, Refusal)
    assert answer.permanent
    assert answer.kind is RefusalKind.LOCAL
    assert calls == [1]


async def test_a_dropped_connection_is_still_worth_repeating() -> None:
    """Обрыв связи остаётся тем, чем был: временным."""

    class Dropped:
        async def post(self, *args: object, **kwargs: object) -> httpx.Response:
            raise httpx.ConnectError("обрыв")

    answer = await post_chat(Dropped(), api_key="sk-test", payload={}, topic="проверка")  # type: ignore[arg-type]

    assert isinstance(answer, Refusal)
    assert answer.kind is RefusalKind.NETWORK
    assert not answer.permanent
