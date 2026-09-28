"""Транспорт живёт один проход или один запрос — и закрывается.

Боевой транспорт держит свой HTTP-клиент с пулом соединений. Процесс
добивок собирал его на каждый проход, сервер — на каждое нажатие
«отправить», и ни один не закрывал: соединения копились до конца
процесса. Здесь — что закрытие стоит на всех трёх путях и переживает
отказ посреди работы, а консоль без ключа отвечает словами.
"""

from __future__ import annotations

import argparse
from collections.abc import Awaitable, Callable

import pytest
from backend.cli import letters_queue
from backend.config import outreach as outreach_cfg
from backend.config import storage
from backend.features.core.domain import UserRole
from backend.features.core.models.access import UserModel
from backend.features.letters.followups import PassReport
from backend.features.letters.transport import Outgoing
from backend.features.letters.transport_factory import in_use
from backend.workers import followups as followups_worker
from httpx import AsyncClient
from tests.conftest import TEST_DSN, bearer


class ClosingTransport:
    """Транспорт, который помнит, закрыли ли его."""

    name = "closing"
    real = False

    def __init__(self) -> None:
        self.closed = False

    async def send(self, outgoing: Outgoing) -> str:
        return f"closing-{outgoing.message_id}"

    async def aclose(self) -> None:
        self.closed = True


class BareTransport:
    """Транспорт без `aclose` — как нулевой и подставные в тестах."""

    name = "bare"
    real = False

    async def send(self, outgoing: Outgoing) -> str:
        return f"bare-{outgoing.message_id}"


class TestInUse:
    async def test_closes_after_the_block(self) -> None:
        transport = ClosingTransport()
        async with in_use(transport) as used:
            assert used is transport
            assert not transport.closed
        assert transport.closed

    async def test_closes_when_the_block_fails(self) -> None:
        transport = ClosingTransport()
        with pytest.raises(RuntimeError, match="посреди прохода"):
            async with in_use(transport):
                raise RuntimeError("посреди прохода")
        assert transport.closed

    async def test_transport_without_aclose_is_fine(self) -> None:
        async with in_use(BareTransport()) as used:
            assert used.name == "bare"


class TestFollowupSweep:
    async def test_each_pass_closes_its_transport(self, monkeypatch: pytest.MonkeyPatch) -> None:
        built: list[ClosingTransport] = []

        def build() -> ClosingTransport:
            built.append(ClosingTransport())
            return built[-1]

        seen_open: list[bool] = []

        async def fake_send_due(_session: object, *, transport: object, limit: int) -> PassReport:
            assert transport is built[-1]
            seen_open.append(not built[-1].closed)
            return PassReport()

        monkeypatch.setattr(storage, "DSN", TEST_DSN)
        monkeypatch.setattr(followups_worker, "build_transport", build)
        monkeypatch.setattr(followups_worker, "send_due", fake_send_due)

        await followups_worker.sweep()
        await followups_worker.sweep()

        # Два прохода — два транспорта, оба закрыты; внутри прохода открыт.
        assert len(built) == 2
        assert seen_open == [True, True]
        assert all(transport.closed for transport in built)

    async def test_pass_that_fails_still_closes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = ClosingTransport()

        async def broken(_session: object, *, transport: object, limit: int) -> PassReport:
            raise RuntimeError("база отвалилась")

        monkeypatch.setattr(storage, "DSN", TEST_DSN)
        monkeypatch.setattr(followups_worker, "build_transport", lambda: transport)
        monkeypatch.setattr(followups_worker, "send_due", broken)

        with pytest.raises(RuntimeError, match="база отвалилась"):
            await followups_worker.sweep()
        assert transport.closed


class TestSendButton:
    async def test_refused_send_still_closes_the_transport(
        self,
        client: AsyncClient,
        make_user: Callable[..., Awaitable[UserModel]],
        sign_in: Callable[..., Awaitable[str]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Письма нет — отказ, но транспорт, собранный под запрос, закрыт."""
        transport = ClosingTransport()
        monkeypatch.setattr("backend.api.letters.routes.build_transport", lambda: transport)
        await make_user("отправка@site.com", role=UserRole.ADMIN)
        token = await sign_in("отправка@site.com")

        answer = await client.post("/api/letters/987654/send", headers=bearer(token))

        assert answer.status_code in (404, 409), answer.text
        assert transport.closed


class TestLettersSendCommand:
    async def test_missing_key_is_words_not_a_traceback(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(outreach_cfg, "TRANSPORT", "sendgrid")
        monkeypatch.setattr(outreach_cfg, "SENDGRID_API_KEY", "")

        code = await letters_queue.cmd_letters_send(argparse.Namespace(id=1))

        assert code == letters_queue.EXIT_NOT_SENT
        out = capsys.readouterr().out
        assert "Письмо не отправлено" in out
        assert "OUTREACH_SENDGRID_API_KEY" in out
