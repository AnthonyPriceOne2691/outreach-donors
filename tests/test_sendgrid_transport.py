"""Боевая отправка: что уходит платформе и чего она не прощает.

Транспорт, который шлёт живым людям, проверяется строже остальных:
ошибка здесь — это письмо не тому, письмо дважды или письмо, которое
считается отправленным, не уйдя никуда.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import httpx
import pytest
from backend.features.letters.sendgrid import MESSAGE_ID_HEADER, SendGridTransport
from backend.features.letters.transport import Outgoing, TransportError

SENT = Outgoing(
    message_id=417,
    to="editor@donor.test",
    from_email="anna@mail-a.example",
    from_name="Anna Ro",
    reply_to="anna+m417.abc@replies.mail-a.example",
    subject="Placement enquiry",
    body="Hello,\n\nWhat is your price?",
    unsubscribe_url="https://ours.test/api/unsubscribe/u5.abcdef0123",
)


def _transport(
    handler: Any, *, allowlist: tuple[str, ...] = ()
) -> tuple[SendGridTransport, list[dict[str, Any]]]:
    seen: list[dict[str, Any]] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return handler(request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(record))
    return SendGridTransport(api_key="sg-test-key", allowlist=allowlist, http=http), seen


def _accepted(_: httpx.Request) -> httpx.Response:
    return httpx.Response(202, headers={MESSAGE_ID_HEADER: "sg-42"})


class TestWhatGoesOut:
    async def test_message_number_travels_with_the_letter(self) -> None:
        """По нему событие доставки найдёт наше письмо. Искать по адресу
        нельзя: у донора адресов бывает несколько."""
        transport, seen = _transport(_accepted)

        await transport.send(SENT)

        assert seen[0]["custom_args"] == {"message_id": "417"}

    async def test_thread_headers_are_set_for_followups(self) -> None:
        transport, seen = _transport(_accepted)

        await transport.send(replace(SENT, in_reply_to="<first@mail-a.example>"))

        headers = seen[0]["headers"]
        assert headers["In-Reply-To"] == "<first@mail-a.example>"
        assert headers["References"] == "<first@mail-a.example>"

    async def test_unsubscribe_button_reaches_the_mail_client(self) -> None:
        """Её нажимают вместо «спам», и почты учитывают её в репутации."""
        transport, seen = _transport(_accepted)

        await transport.send(SENT)

        headers = seen[0]["headers"]
        assert headers["List-Unsubscribe"] == f"<{SENT.unsubscribe_url}>"
        assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"

    async def test_tracking_is_off(self) -> None:
        """Подмена ссылок ломает отписку и показывает донору чужой адрес
        вместо нашего — в холодном письме это и читается как спам."""
        transport, seen = _transport(_accepted)

        await transport.send(SENT)

        tracking = seen[0]["tracking_settings"]
        assert tracking["click_tracking"]["enable"] is False
        assert tracking["open_tracking"]["enable"] is False

    async def test_reply_to_carries_the_label(self) -> None:
        transport, seen = _transport(_accepted)

        await transport.send(SENT)

        assert seen[0]["reply_to"] == {"email": SENT.reply_to}

    async def test_provider_number_is_returned(self) -> None:
        transport, _ = _transport(_accepted)

        assert await transport.send(SENT) == "sg-42"


class TestWhenItGoesWrong:
    async def test_no_key_no_transport(self) -> None:
        with pytest.raises(TransportError, match="OUTREACH_SENDGRID_API_KEY"):
            SendGridTransport(api_key="", http=httpx.AsyncClient())

    async def test_refusal_is_loud(self) -> None:
        def refuse(_: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text='{"errors":[{"message":"unauthorized"}]}')

        transport, _ = _transport(refuse)

        with pytest.raises(TransportError, match="401"):
            await transport.send(SENT)

    async def test_missing_number_is_not_silent(self) -> None:
        """Платформа приняла письмо, но привязать к нему события
        доставки будет не к чему — это отказ, а не успех."""

        def without_number(_: httpx.Request) -> httpx.Response:
            return httpx.Response(202)

        transport, _ = _transport(without_number)

        with pytest.raises(TransportError, match="номер"):
            await transport.send(SENT)

    async def test_network_failure_is_a_refusal(self) -> None:
        def broken(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("сеть")

        transport, _ = _transport(broken)

        with pytest.raises(TransportError, match="недоступна"):
            await transport.send(SENT)


class TestTheSafetyCatch:
    """Предохранитель первых дней: пока список не пуст, письма уходят
    только на свои адреса."""

    async def test_stranger_is_refused(self) -> None:
        transport, seen = _transport(_accepted, allowlist=("anna@ours.test",))

        with pytest.raises(TransportError, match="не в списке разрешённых"):
            await transport.send(SENT)

        assert seen == []

    async def test_own_address_goes_through(self) -> None:
        transport, _ = _transport(_accepted, allowlist=("editor@donor.test",))

        assert await transport.send(SENT) == "sg-42"

    async def test_whole_domain_can_be_allowed(self) -> None:
        transport, _ = _transport(_accepted, allowlist=("donor.test",))

        assert await transport.send(SENT) == "sg-42"

    async def test_empty_list_means_everyone(self) -> None:
        transport, _ = _transport(_accepted)

        assert await transport.send(SENT) == "sg-42"
