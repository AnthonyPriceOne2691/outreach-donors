"""Проверяльщик адресов лидов — срез 1.4: `fixture`, Hunter за `MockTransport`,
фабрика по настройке, расход (A5–A7).

Сеть — только `httpx.MockTransport`; формы ответов провайдера — выдуманные по
его документации, числа некруглые. Исходы различаются намеренно: вердикт
(в том числе «провайдер не знает») — значение, отказ сервиса — исключение,
и у квоты с закрытой учёткой свои классы: их очистка не повторяет.
"""

from __future__ import annotations

import importlib.util
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from backend.config import contacts as contacts_cfg
from backend.config import sales as sales_cfg
from backend.config.startup_checks import ConfigError
from backend.features.contacts.provider import (
    ProviderBlockedError,
    ProviderError,
    ProviderQuotaError,
    ProviderRateLimitError,
)
from backend.features.core import usage
from backend.features.core.domain import UsageProvider
from backend.features.core.models.ops import UsageRecordModel
from backend.features.sales import verifier
from backend.features.sales.verifier import (
    DELIVERABLE_BY_STATUS,
    UNDELIVERABLE_WORDS,
    FixtureVerifier,
    HunterVerifier,
    Verdict,
    build_verifier,
)
from sqlalchemy import Connection, select, text
from sqlalchemy.ext.asyncio import AsyncSession

ROOT = Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "backend/migrations/versions/2d9877260a6d_usage_provider_hunter.py"
EMAIL = "ivan@acme.example.test"
KEY = "k-test"

Handler = Callable[[httpx.Request], httpx.Response]


def _reply(status: int, payload: Any = None, *, text: str | None = None) -> Handler:
    def handler(_request: httpx.Request) -> httpx.Response:
        if text is not None:
            return httpx.Response(status, text=text)
        return httpx.Response(status) if payload is None else httpx.Response(status, json=payload)

    return handler


def _broken(_request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("сети нет")


def _unsendable(_request: httpx.Request) -> httpx.Response:
    """Ключ с переводом строки: httpx кладёт заголовок целиком в текст исключения."""
    raise httpx.LocalProtocolError(f"Illegal header value b'Bearer {KEY}\\n'")


async def _verify(handler: Handler, email: str = EMAIL) -> Verdict:
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        return await HunterVerifier(http, api_key=KEY).verify(email)


# --- Hunter: вердикты ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "verdict", "deliverable", "words"),
    [
        ({"status": "valid", "score": 97}, Verdict("valid", 97, units=1), True, ""),
        ({"status": "webmail", "score": 88}, Verdict("webmail", 88, units=1), True, ""),
        ({"status": "accept_all", "score": "n/a"}, Verdict("accept_all", None, units=1), True, ""),
        ({"status": "unknown", "score": 23}, Verdict("unknown", 23, units=1), None, ""),
        ({"status": "invalid", "score": 2}, Verdict("invalid", 2, units=1), False, "адрес не существует"),
        ({"status": "disposable"}, Verdict("disposable", None, units=1), False, "одноразовый ящик"),
    ],
)  # fmt: skip
async def test_a5_hunter_verdict_keeps_the_providers_word_its_score_and_one_paid_unit(
    data: dict[str, Any], verdict: Verdict, deliverable: bool | None, words: str
) -> None:  # A5
    # A5 — пример спеки
    found = await _verify(_reply(200, {"data": data}))

    assert found == verdict
    assert (found.deliverable, found.words) == (deliverable, words)


def test_every_known_status_has_a_deliverability_and_only_dead_ones_have_words() -> None:
    # A5 — пример спеки
    assert set(DELIVERABLE_BY_STATUS) == {
        "valid", "accept_all", "webmail", "unknown", "invalid", "disposable",
    }  # fmt: skip
    dead = {status for status, deliverable in DELIVERABLE_BY_STATUS.items() if deliverable is False}
    assert dead == set(UNDELIVERABLE_WORDS) == {"invalid", "disposable"}


# --- Hunter: отказы -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("handler", "kind", "words"),
    [
        (_reply(429), ProviderError, "провайдер не ответил (HTTP 429) — повторим следующей очисткой"),
        (_reply(503, text="<html>упал</html>"), ProviderError, "провайдер не ответил (HTTP 503) — повторим следующей очисткой"),
        (_reply(202, {"data": {"status": "pending"}}), ProviderError, "провайдер ещё проверяет адрес — повторим следующей очисткой"),
        (_reply(200, text="не json"), ProviderError, "ответ не разобран как JSON (HTTP 200): 'не json'"),
        (_reply(200, []), ProviderError, "ответ не разобран как JSON (HTTP 200): '[]'"),
        (_reply(200, {"meta": {}}), ProviderError, "в ответе нет объекта data (HTTP 200): \"{'meta': {}}\""),
        (_reply(200, {"data": {"status": "weird"}}), ProviderError, "формат ответа поменялся: status='weird' (HTTP 200) — читать лог, не гадать"),
        (_reply(200, {"data": {"score": 50}}), ProviderError, "формат ответа поменялся: status=None (HTTP 200) — читать лог, не гадать"),
        (_broken, ProviderError, "сеть до провайдера не дошла: ConnectError('сети нет')"),
        (_reply(429, {"errors": [{"id": "too_many_requests", "details": "slow down"}]}), ProviderRateLimitError, "частота превышена: slow down"),
        (_reply(429, {"errors": [{"id": "usage_exceeded", "details": "monthly"}]}), ProviderQuotaError, "квота исчерпана: monthly"),
        (_reply(403, {"errors": [{"details": "no id"}]}), ProviderQuotaError, "квота исчерпана: no id"),
        (_reply(429, {"errors": [{"id": "restricted_account", "details": "restricted"}]}), ProviderBlockedError, "учётка закрыта провайдером: restricted — квота тут ни при чём, зайти в кабинет и разобраться"),
        (_reply(401, {"errors": [{"id": "authentication_failed", "details": "No valid API key"}]}), ProviderBlockedError, "ключ не принят провайдером (HTTP 401, No valid API key) — проверить CONTACTS_HUNTER_API_KEY"),
        (_reply(401), ProviderBlockedError, "ключ не принят провайдером (HTTP 401) — проверить CONTACTS_HUNTER_API_KEY"),
    ],
)  # fmt: skip
async def test_a6_refusals_are_named_and_sorted_into_retry_and_stop(
    handler: Handler, kind: type[ProviderError], words: str
) -> None:  # A6
    # A6 — пример спеки
    """«Мы не поняли» — `ProviderError`, лид остаётся `new` и повторяется; квота
    и закрытая учётка — свои классы, их очистка не повторяет. Маркер читается
    раньше кода: закрытая учётка приезжает с 429."""
    with pytest.raises(ProviderError) as refused:
        await _verify(handler)
    assert type(refused.value) is kind
    assert str(refused.value) == words


async def test_hunter_asks_by_address_with_the_key_in_a_header_not_in_the_url() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": {"status": "valid", "score": 97}})

    await _verify(handler)

    (request,) = seen
    assert (
        str(request.url) == "https://api.hunter.io/v2/email-verifier?email=ivan%40acme.example.test"
    )
    assert request.headers["Authorization"] == f"Bearer {KEY}"
    assert KEY not in str(request.url)


# --- fixture ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("email", "verdict"),
    [
        (EMAIL, Verdict("valid", 93)),
        ("bounce@acme.example.test", Verdict("invalid", 7)),
        ("Dead.Letter@acme.example.test", Verdict("invalid", 7)),
        ("invalid-one@acme.example.test", Verdict("invalid", 7)),
        ("disposable@acme.example.test", Verdict("disposable", 11)),
        ("unknown.box@acme.example.test", Verdict("unknown", 48)),
        ("catchall@acme.example.test", Verdict("accept_all", 61)),
        ("accept-all@acme.example.test", Verdict("accept_all", 61)),
    ],
)
async def test_fixture_verdict_is_set_by_the_address_itself_and_costs_nothing(
    email: str, verdict: Verdict
) -> None:
    # A5 — пример спеки
    found = await FixtureVerifier().verify(email)
    assert (found, found.units, FixtureVerifier.name) == (verdict, 0, "fixture")


# --- фабрика и настройка ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("provider", "key", "kind"),
    [
        ("fixture", "", FixtureVerifier),
        (" Live ", KEY, HunterVerifier),
        ("live", KEY, HunterVerifier),
    ],
)
async def test_verifier_is_chosen_by_the_setting(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    provider: str,
    key: str,
    kind: type[object],
) -> None:
    monkeypatch.setattr(sales_cfg, "VERIFIER_PROVIDER", provider)
    monkeypatch.setattr(contacts_cfg, "HUNTER_API_KEY", key)

    with caplog.at_level(logging.WARNING, logger=verifier.__name__):
        async with httpx.AsyncClient(transport=httpx.MockTransport(_broken)) as http:
            built = build_verifier(http)

    assert type(built) is kind
    warned = "вердикты выдуманные" in caplog.text
    assert warned is (kind is FixtureVerifier)  # о выдуманных вердиктах — предупреждение


@pytest.mark.parametrize(
    ("provider", "key", "words"),
    [
        (
            "live",
            "",
            "SALES_VERIFIER_PROVIDER=live, а CONTACTS_HUNTER_API_KEY пуст. "
            "Заполнить ключ или вернуть fixture — без ключа проверка адресов не стартует",
        ),
        (
            "nothing",
            KEY,
            "SALES_VERIFIER_PROVIDER=«nothing» — такого проверяльщика нет. Известные: fixture, live",
        ),
    ],
)
async def test_a7_live_without_a_key_and_an_unknown_name_refuse_before_the_first_lead(
    monkeypatch: pytest.MonkeyPatch, provider: str, key: str, words: str
) -> None:  # A7
    # A7 — пример спеки
    monkeypatch.setattr(sales_cfg, "VERIFIER_PROVIDER", provider)
    monkeypatch.setattr(contacts_cfg, "HUNTER_API_KEY", key)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_broken)) as http:
        with pytest.raises(ConfigError) as refused:
            build_verifier(http)
    assert str(refused.value) == words


def test_verifier_setting_defaults_to_fixture_and_is_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Урок L4 соседнего проекта: поле читается только по алиасу — оба значения."""
    monkeypatch.delenv("SALES_VERIFIER_PROVIDER", raising=False)
    assert sales_cfg._Sales(_env_file=None).verifier_provider == "fixture"
    monkeypatch.setenv("SALES_VERIFIER_PROVIDER", "live")
    assert sales_cfg._Sales(_env_file=None).verifier_provider == "live"


# --- расход --------------------------------------------------------------------------------


async def test_sales_verify_is_booked_to_its_own_provider(session: AsyncSession) -> None:
    assert usage.OPERATION_PROVIDERS["sales_verify"] is UsageProvider.HUNTER

    usage.record(session, operation="sales_verify", units=3)
    await session.flush()

    row = (await session.scalars(select(UsageRecordModel))).one()
    assert (row.provider, row.operation, row.units, row.amount_usd, row.system, row.run_id) == (
        UsageProvider.HUNTER,
        "sales_verify",
        3,
        None,
        "outreach-donors",
        None,
    )


def _hunter_values(connection: Connection) -> list[str]:
    """Миграция значения ещё раз, в процессе: подъём сьюта идёт подпроцессом,
    и покрытие его не видит. `ADD VALUE IF NOT EXISTS` делает повтор безвредным."""
    spec = importlib.util.spec_from_file_location("usage_provider_hunter_migration", MIGRATION)
    assert spec is not None, MIGRATION
    assert spec.loader is not None, MIGRATION
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
        migration.downgrade()
    values = connection.execute(text("SELECT unnest(enum_range(NULL::usageprovider))::text"))
    return list(values.scalars())


async def test_usage_provider_value_is_there_once_and_survives_a_rerun(
    session: AsyncSession,
) -> None:
    connection = await session.connection()
    assert (await connection.run_sync(_hunter_values)).count("hunter") == 1


async def test_unsent_request_refusal_carries_no_key_or_cause(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Запрос не собран у нас — отказ словами, без ключа и без цепочки причин:
    иначе ключ всплыл бы в журнале очистки трассировкой (урок #174)."""
    with caplog.at_level(logging.DEBUG), pytest.raises(ProviderError) as refused:
        await _verify(_unsendable)

    assert "запрос не собран" in str(refused.value)
    assert KEY not in str(refused.value)
    assert refused.value.__cause__ is None
    assert KEY not in caplog.text
