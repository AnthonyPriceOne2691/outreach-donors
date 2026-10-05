"""Ключ провайдера не попадает ни в отказ, ни в журнал, ни в цепочку причин.

Находка «Продаж» 05.10.2026: ключ с пробелом или переводом строки (частая
ошибка вставки в `.env`) ломает сборку заголовка, и текст исключения httpx
несёт заголовок целиком — «Illegal header value b'Bearer …'». Он уходил в
отказ прогона на экране и трассировкой в журнал. Здесь сборщик заголовка
подставлен: его отказ — ровно тот текст, который пишет h11.
"""

from __future__ import annotations

import logging

import httpx
import pytest
from backend.features.ahrefs.client import AhrefsClient, AhrefsError
from backend.features.contacts.provider import HunterProvider, ProviderError
from backend.features.serp.dataforseo import DataForSeoProvider, SerpError
from backend.shared.llm import Refusal, post_chat

#: Выдуманное значение ключа: его не должно быть нигде, кроме заголовка.
VALUE = "leaky-value-123"
#: Что передаётся клиенту: сам сборщик подставлен, значение ему неважно.
SENT = "value"


class _Unsendable:
    """Запрос не собирается — как с переводом строки в ключе. Считает попытки."""

    def __init__(self) -> None:
        self.tries = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.tries += 1
        raise httpx.LocalProtocolError(f"Illegal header value b'Bearer {VALUE}\\n'")


async def test_model_refusal_and_log_carry_no_key(caplog: pytest.LogCaptureFixture) -> None:
    unsendable = _Unsendable()
    http = httpx.AsyncClient(transport=httpx.MockTransport(unsendable))

    with caplog.at_level(logging.DEBUG):
        refusal = await post_chat(http, api_key=SENT, payload={}, topic="проба")
    await http.aclose()

    assert isinstance(refusal, Refusal)
    assert refusal.permanent
    assert "запрос не собран" in refusal.detail
    assert VALUE not in str(refusal)
    assert VALUE not in caplog.text
    assert unsendable.tries == 1  # повтор собрал бы запрос так же


def _ahrefs(unsendable: _Unsendable) -> AhrefsClient:
    http = httpx.AsyncClient(
        base_url="https://ahrefs.example.test", transport=httpx.MockTransport(unsendable)
    )
    return AhrefsClient(api_key=SENT, http=http)


async def test_ahrefs_request_refuses_at_once_without_key_or_cause(
    caplog: pytest.LogCaptureFixture,
) -> None:
    unsendable = _Unsendable()
    client = _ahrefs(unsendable)

    with caplog.at_level(logging.DEBUG), pytest.raises(AhrefsError) as caught:
        await client.batch_metrics(["site.example.test"], ["domain_rating"])
    await client.aclose()

    assert caught.value.permanent
    assert caught.value.__cause__ is None  # иначе ключ всплыл бы в трассировке
    assert VALUE not in str(caught.value)
    assert VALUE not in caplog.text
    assert unsendable.tries == 1


async def test_ahrefs_quota_check_carries_no_key() -> None:
    client = _ahrefs(_Unsendable())

    with pytest.raises(AhrefsError) as caught:
        await client.limits_and_usage()
    await client.aclose()

    assert caught.value.__cause__ is None
    assert VALUE not in str(caught.value)


async def test_hunter_refusal_carries_no_key_or_cause() -> None:
    http = httpx.AsyncClient(transport=httpx.MockTransport(_Unsendable()))
    provider = HunterProvider(http, api_key=SENT)

    with pytest.raises(ProviderError) as caught:
        await provider.find_emails("site.example.test")
    await http.aclose()

    assert caught.value.__cause__ is None
    assert VALUE not in str(caught.value)


async def test_serp_refusal_carries_no_login_or_cause() -> None:
    """У выдачи в заголовке Basic — логин и пароль в base64, то есть обратимо."""
    http = httpx.AsyncClient(
        base_url="https://serp.example.test", transport=httpx.MockTransport(_Unsendable())
    )
    provider = DataForSeoProvider(http, sandbox=True, login=SENT, password=SENT)

    with pytest.raises(SerpError) as caught:
        await provider.balance()
    await provider.aclose()
    await http.aclose()

    assert caught.value.permanent
    assert caught.value.__cause__ is None
    assert VALUE not in str(caught.value)
