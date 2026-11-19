"""Длина секретов на старте сервера (аудит 19.11.2026).

Секрет подписи пропусков короче 32 байт сверял только вход (`access/tokens.py`): сервис
стартовал и отказывал первому сотруднику. Теперь — отказ на старте, тем же числом
(`config/access.MIN_SECRET_BYTES`). Секрет приёма почты подписывает метки отписки и адресов
ответа; его длину на бою никто не сверял, а смена гасит ссылки в ушедших письмах, — поэтому
короткий даёт громкую строку журнала, а сервис стартует. Значения секретов в тексте нет.
"""

from __future__ import annotations

import logging

import pytest
from backend.api import app as app_module
from backend.config import access, startup_checks
from backend.config import outreach as outreach_cfg
from backend.config.startup_checks import ConfigError, check_access, say_inbound_secret
from backend.features.access import tokens
from fastapi import FastAPI

SHORT_SIGNING = "signing-secret-of-31-bytes-long"  # pragma: allowlist secret
SHORT_INBOUND = "inbound-secret-short"  # pragma: allowlist secret


def _said(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == startup_checks.__name__]


class TestSigningSecret:
    @pytest.mark.parametrize(
        "secret", [SHORT_SIGNING, "я" * 15], ids=["31 байт", "15 букв — 30 байт"]
    )
    def test_short_secret_stops_the_start(
        self, monkeypatch: pytest.MonkeyPatch, secret: str
    ) -> None:
        monkeypatch.setattr(access, "JWT_SECRET", secret)

        with pytest.raises(ConfigError, match="ACCESS_JWT_SECRET короче 32 байт") as refused:
            check_access()

        assert secret not in str(refused.value), "значения секрета в отказе нет"
        assert "openssl rand -hex 32" in str(refused.value)

    @pytest.mark.parametrize("secret", ["x" * 32, "я" * 16], ids=["32 байта", "16 букв — 32 байта"])
    def test_32_bytes_are_enough(self, monkeypatch: pytest.MonkeyPatch, secret: str) -> None:
        """Длина — в байтах, как её считает подпись, а не в буквах."""
        monkeypatch.setattr(access, "JWT_SECRET", secret)

        check_access()

    def test_the_server_does_not_start_with_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(access, "JWT_SECRET", SHORT_SIGNING)

        with pytest.raises(ConfigError, match="короче"):
            app_module.create_app()

    def test_one_number_for_the_start_and_the_login(self) -> None:
        """Старт и выдача пропуска судят одним числом: разойдутся — сервис стартует с секретом,
        на котором вход отказывает, или наоборот."""
        assert tokens.MIN_SECRET_LENGTH == access.MIN_SECRET_BYTES == 32


class TestInboundSecret:
    def test_short_secret_is_a_loud_line_not_a_refusal(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", SHORT_INBOUND)

        with caplog.at_level(logging.ERROR, logger=startup_checks.__name__):
            say_inbound_secret()

        [record] = _said(caplog)
        assert record.levelno == logging.ERROR
        assert record.getMessage().startswith("OUTREACH_INBOUND_SECRET короче 32 байт")
        assert SHORT_INBOUND not in record.getMessage()

    @pytest.mark.parametrize("secret", ["", "i" * 32], ids=["не задан", "32 байта"])
    def test_unset_or_long_enough_says_nothing(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, secret: str
    ) -> None:
        """Не задан — не про длину: без секрета приём и ссылки отписки отказывают сами, словами."""
        monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", secret)

        with caplog.at_level(logging.DEBUG, logger=startup_checks.__name__):
            say_inbound_secret()

        assert _said(caplog) == []

    @pytest.mark.usefixtures("jwt_secret")
    def test_the_server_starts_and_says_it(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", SHORT_INBOUND)
        # Журнал — в caplog, а не в свой обработчик сборки.
        monkeypatch.setattr(app_module, "setup_logging", lambda: None)

        with caplog.at_level(logging.ERROR, logger=startup_checks.__name__):
            app = app_module.create_app()

        assert isinstance(app, FastAPI)
        assert [record.levelno for record in _said(caplog)] == [logging.ERROR]
