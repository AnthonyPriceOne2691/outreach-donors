"""Прогон по файлу: домен без почты — вердикт MX в строке и честный статус.

Прогон по файлу не останавливается на MX: ему нужны и каналы связи, а
телеграм в подвале от почты не зависит. Ревью #118, находка 7: при этом
вердикт `MailRoute.NONE` выбрасывался. Адрес на домене, который почту
не принимает, уходил в итог со статусом found, выигрывал у доставляемых
(вес «на домене сайта» выше, чем у личного ящика), а флага MX в строке
не было — письмо по такому файлу отбилось бы.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from backend.features.contacts import file_sweep, mx
from backend.features.contacts.ladder import ContactLadder
from backend.features.core.domain import ContactStatus
from tests.contacts_sweep_fakes import Web, install, page

BOTH = page('<a href="mailto:info@shop.de">редакция</a> <a href="mailto:owner@gmail.com">я</a>')
OWN_ONLY = page('<a href="mailto:info@shop.de">редакция</a> <a href="https://t.me/shopdesk">tg</a>')


async def _row(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str, route: mx.MailRoute
) -> dict[str, str]:
    install(monkeypatch, Web({"shop.de": {"/": body}}), routes={"shop.de": route})
    checkpoint = tmp_path / "c.jsonl"
    await file_sweep.sweep(["shop.de"], checkpoint=checkpoint)
    (row,) = file_sweep.rows_from_checkpoint(checkpoint)
    return row


class TestDomainWithoutMail:
    async def test_deliverable_address_wins_over_the_dead_domain(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        row = await _row(monkeypatch, tmp_path, BOTH, mx.MailRoute.NONE)
        assert row["email"] == "owner@gmail.com"
        assert row["mail_route"] == "none"
        assert "info@shop.de" in row["rejected"]
        assert "info@shop.de" not in row["emails"]

    async def test_own_address_alone_is_not_found(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Писать некуда — значит адреса нет, а каналы связи остаются в строке."""
        row = await _row(monkeypatch, tmp_path, OWN_ONLY, mx.MailRoute.NONE)
        assert (row["status"], row["email"]) == ("not_found", "")
        assert "почт" in row["rejected"]
        assert row["telegram"] == "shopdesk"


class TestVerdictInTheRow:
    @pytest.mark.parametrize(
        "route", [mx.MailRoute.MX, mx.MailRoute.IMPLICIT, mx.MailRoute.UNKNOWN]
    )
    async def test_deliverable_or_unchecked_keeps_the_address(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, route: mx.MailRoute
    ) -> None:
        """«DNS молчал» — не отказ: адрес остаётся, а в строке видно, что не проверен."""
        row = await _row(monkeypatch, tmp_path, OWN_ONLY, route)
        assert (row["status"], row["email"]) == ("found", "info@shop.de")
        assert row["mail_route"] == route.value


def _routes(monkeypatch: pytest.MonkeyPatch, table: dict[str, mx.MailRoute]) -> None:
    async def route(host: str, **_kwargs: object) -> mx.MailRoute:
        return table.get(host, mx.MailRoute.MX)

    monkeypatch.setattr("backend.features.contacts.ladder.mail_route", route)


class TestLadder:
    async def test_database_path_still_stops_without_a_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _routes(monkeypatch, {"shop.de": mx.MailRoute.NONE})
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            return httpx.Response(200, html=BOTH)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await ContactLadder(http).find("shop.de")

        assert result.status is ContactStatus.NOT_FOUND
        assert result.mail_route is mx.MailRoute.NONE
        assert requested == []

    async def test_verdict_is_about_this_name_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Поддомен без почты ничего не говорит о почте родительского домена."""
        _routes(monkeypatch, {"blog.shop.de": mx.MailRoute.NONE})

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, html=OWN_ONLY)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            ladder = ContactLadder(http, stop_without_mail=False)
            result = await ladder.find("blog.shop.de")

        assert result.contact is not None
        assert result.contact.email == "info@shop.de"
        assert result.mail_route is mx.MailRoute.NONE
