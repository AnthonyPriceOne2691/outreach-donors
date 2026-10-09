"""Kommo через шлюз агентства — способ передачи внутри `live`: клиент шлюза и выбор пути.

Сеть — только `httpx.MockTransport`; шлюз — `https://gateway.example.test/…`, ответы выдуманы
по описанию шлюза, номера некруглые. Ключ — заведомо выдуманный и узнаваемый: по нему видно,
что он не утёк ни в слова отказа, ни в журнал, ни в `repr`. Передача лида целиком через
шлюз — `tests/test_sales_handoff_gateway.py`.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
from backend.config import sales as sales_cfg
from backend.config.startup_checks import ConfigError
from backend.features.runs.failures import is_permanent
from backend.features.sales import kommo
from backend.features.sales.kommo import (
    FULL,
    CreatedLead,
    GatewayAccount,
    KommoAbilities,
    KommoAuthError,
    KommoClient,
    KommoError,
    KommoFixture,
    KommoFormatError,
    KommoGateway,
    KommoLive,
    KommoRefusedError,
    KommoUnavailableError,
    KommoUnconfirmedError,
    NewLead,
    Pace,
    build_kommo,
)
from tests.test_sales_kommo import ACCOUNT as DIRECT
from tests.test_sales_kommo import FILLED, Script

KEY = "gateway-fake-key-6b1d"  # pragma: allowlist secret
URL = "https://gateway.example.test/hooks/lead"
SOURCE = "Рассылка продаж (проверка)"
TAG = "email-sales-check"
ACCOUNT = GatewayAccount(
    url=URL,
    key=KEY,
    subdomain="acme-test",
    pipeline_id=5813,
    status_id=70429,
    source=SOURCE,
    tag=TAG,
)
EMAIL = "ivan@acme.example.test"
NOTE = "Ответ из рассылки — 14.10.2026 11:07 UTC\nОт: ivan@acme.example.test\n\nСозвонимся?"
LEAD = NewLead(
    email=EMAIL,
    site="acme.example.test",
    hypothesis="Гипотеза пробная",
    company="Acme Test",
    name="Ivan Petrov",
    note=NOTE,
)
DEAL = "https://acme-test.kommo.com/leads/detail/9341"


async def _gateway[T](script: Script, act: Callable[[KommoGateway], Awaitable[T]]) -> T:
    async with httpx.AsyncClient(transport=httpx.MockTransport(script)) as http:
        pace = Pace(7, clock=script.clock.time, sleep=script.clock.sleep)
        return await act(KommoGateway(http, ACCOUNT, pace=pace))


def _made(
    lead_id: object = 9341, contact_id: object = 77031, warnings: object = None
) -> httpx.Response:
    """Ответ шлюза «сделка заведена» — по его описанию."""
    body = {"success": True, "lead_id": lead_id, "contact_id": contact_id}
    body["warnings"] = [] if warnings is None else warnings
    return httpx.Response(200, json=body)


def _failed(status: int, error: str, **extra: Any) -> httpx.Response:
    return httpx.Response(status, json={"success": False, "error": error, **extra})


#: Журнал клиента шлюза; строки httpx о запросе — не его.
GATEWAY_LOG = "backend.features.sales.kommo_gateway"


def _logged(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """Строки журнала клиента шлюза: другие логгеры набора могли поднять свои уровни."""
    return [record for record in caplog.records if record.name == GATEWAY_LOG]


# --- тело запроса: только то, что знаем -------------------------------------------------------


async def test_the_lead_goes_as_one_post_with_the_exact_body() -> None:
    script = Script(_made())

    await _gateway(script, lambda client: client.create_complex_lead(LEAD))

    (request,) = script.requests
    assert (request.method, str(request.url)) == ("POST", URL)
    assert request.headers["Content-Type"] == "application/json"
    assert script.body(0) == {
        "pipeline_id": 5813,
        "status_id": 70429,
        "lead_name": "Email: Acme Test",
        "source": SOURCE,
        "lead_tag": TAG,
        "note": NOTE,
        "site": "https://acme.example.test",
        "contact": {"email": EMAIL, "name": "Ivan Petrov", "tag": TAG},
    }
    # Воронка и этап — числами, а не строками и не дробями.
    assert [type(script.body(0)[key]) for key in ("pipeline_id", "status_id")] == [int, int]


async def test_without_a_name_note_or_company_nothing_is_made_up() -> None:
    """Имени нет — контакт только с почтой и тегом; телефона нет никогда; без примечания
    поля `note` нет; без названия компании сделку называет сайт."""
    bare = NewLead(email=f"  {EMAIL} ", site="acme.example.test", hypothesis="Гипотеза пробная")
    script = Script(_made())

    await _gateway(script, lambda client: client.create_complex_lead(bare))

    sent = script.body(0)
    assert sent["contact"] == {"email": EMAIL, "tag": TAG}
    assert "note" not in sent
    assert sent["lead_name"] == "Email: acme.example.test"
    assert "phone" not in script.requests[0].content.decode()


async def test_a_site_that_already_is_an_address_is_kept() -> None:
    lead = NewLead(email=EMAIL, site="https://acme.example.test/ru", hypothesis="h")
    script = Script(_made())

    await _gateway(script, lambda client: client.create_complex_lead(lead))

    assert script.body(0)["site"] == "https://acme.example.test/ru"


# --- ключ: заголовком, и нигде больше ----------------------------------------------------------


async def test_the_key_rides_in_the_bearer_header_only() -> None:
    script = Script(_made())

    await _gateway(script, lambda client: client.create_complex_lead(LEAD))

    (request,) = script.requests
    assert request.headers["Authorization"] == f"Bearer {KEY}"
    assert KEY not in str(request.url)
    assert KEY not in request.content.decode()
    assert KEY not in repr(ACCOUNT)
    assert URL not in repr(ACCOUNT)


@pytest.mark.parametrize(
    "reply",
    [
        _failed(400, f"bad header Authorization: Bearer {KEY}"),
        _failed(401, f"key {KEY} revoked"),
        _failed(200, f"echo {KEY}"),
        httpx.Response(200, text=f"<html>Bearer {KEY}</html>"),
        httpx.LocalProtocolError(f"Illegal header value b'Bearer {KEY} '"),
        httpx.ConnectError(f"cannot connect to {URL} with Bearer {KEY}"),
        httpx.ReadTimeout(f"timed out reading {URL}"),
    ],
)
async def test_the_key_never_reaches_an_error_text_or_the_log(
    reply: httpx.Response | Exception, caplog: pytest.LogCaptureFixture
) -> None:
    script = Script(reply)

    with caplog.at_level(logging.DEBUG), pytest.raises(KommoError) as refused:
        await _gateway(script, lambda client: client.create_complex_lead(LEAD))

    ours = "\n".join(record.getMessage() for record in caplog.records if record.name != "httpx")
    assert KEY not in caplog.text, "ключ едет заголовком: строка httpx о запросе его не видит"
    for text in (str(refused.value), ours):
        assert KEY not in text
        assert URL not in text
    if isinstance(reply, Exception):
        # Цепочки к исключению httpx нет: в нём запрос с заголовком, а в тексте — ключ.
        assert (refused.value.__cause__, refused.value.__suppress_context__) == (None, True)


async def test_the_key_echoed_back_is_hidden_in_the_refusal_words() -> None:
    script = Script(_failed(422, f"Authorization: Bearer {KEY} is not allowed here"))

    with pytest.raises(KommoRefusedError) as refused:
        await _gateway(script, lambda client: client.create_complex_lead(LEAD))

    assert str(refused.value) == (
        "шлюз Kommo отверг сделку (HTTP 422: Authorization: Bearer <ключ шлюза> is not allowed "
        "here) — повтор не поможет: проверить воронку, этап, источник, тег и поля лида"
    )


# --- успех: номер сделки, ссылка, предупреждения — в журнал -----------------------------------


async def test_success_gives_the_deal_number_and_its_link(
    caplog: pytest.LogCaptureFixture,
) -> None:
    script = Script(_made(9341, contact_id=77031))

    with caplog.at_level(logging.INFO, logger=GATEWAY_LOG):
        created = await _gateway(script, lambda client: client.create_complex_lead(LEAD))

    assert created == CreatedLead(
        id=9341, url=DEAL, contact_id=77031, company_id=None, contact_found=False
    )
    [record] = _logged(caplog)
    assert (record.levelno, record.getMessage()) == (logging.INFO, "kommo: шлюз завёл сделку")
    assert (record.lead_id, record.warnings) == (9341, [])  # type: ignore[attr-defined]


async def test_gateway_warnings_go_to_the_log_as_a_field(
    caplog: pytest.LogCaptureFixture,
) -> None:
    told = ["контакт склеен с №5823", {"field": "site", "warning": f"трим {KEY}"}]
    script = Script(_made(9341, contact_id=None, warnings=told))

    with caplog.at_level(logging.INFO, logger=GATEWAY_LOG):
        created = await _gateway(script, lambda client: client.create_complex_lead(LEAD))

    assert (created.id, created.contact_id) == (9341, None)
    [record] = _logged(caplog)
    assert record.levelno == logging.WARNING
    assert record.getMessage() == "kommo: шлюз завёл сделку с предупреждениями"
    assert record.warnings == [  # type: ignore[attr-defined]
        "контакт склеен с №5823",
        '{"field": "site", "warning": "трим <ключ шлюза>"}',
    ]


async def test_a_single_warning_and_a_flood_of_them_are_kept_short(
    caplog: pytest.LogCaptureFixture,
) -> None:
    script = Script(_made(warnings="один"), _made(warnings=["x" * 400] * 17))

    with caplog.at_level(logging.INFO, logger=GATEWAY_LOG):
        await _gateway(script, lambda client: client.create_complex_lead(LEAD))
        await _gateway(script, lambda client: client.create_complex_lead(LEAD))

    one, flood = (record.warnings for record in _logged(caplog))  # type: ignore[attr-defined]
    assert one == ["один"]
    assert (len(flood), {len(item) for item in flood}) == (10, {300})


# --- отказы -----------------------------------------------------------------------------------

CHECK = "повтор не поможет: проверить воронку, этап, источник, тег и поля лида"
KEY_WORDS = "ключ отозван или неверен: проверить SALES_KOMMO_GATEWAY_KEY; повтор не поможет"
ADDRESS = "повтор не поможет: проверить адрес SALES_KOMMO_GATEWAY_URL"


@pytest.mark.parametrize(
    ("reply", "kind", "words"),
    [
        (_failed(400, "status_id: not in pipeline"), KommoRefusedError, f"шлюз Kommo отверг сделку (HTTP 400: status_id: not in pipeline) — {CHECK}"),
        (httpx.Response(422, json={"message": "invalid", "errors": ["email: required"]}), KommoRefusedError, f'шлюз Kommo отверг сделку (HTTP 422: invalid; ["email: required"]) — {CHECK}'),
        (_failed(200, "pipeline is archived"), KommoRefusedError, f"шлюз Kommo не завёл сделку (HTTP 200, success: false: pipeline is archived) — {CHECK}"),
        (httpx.Response(201, json={"success": False}), KommoRefusedError, f"шлюз Kommo не завёл сделку (HTTP 201, success: false) — {CHECK}"),
        (_failed(401, "unauthorized"), KommoAuthError, f"ключ шлюза Kommo отклонён (HTTP 401: unauthorized) — {KEY_WORDS}"),
        (httpx.Response(403, text="<html>forbidden</html>"), KommoAuthError, f"ключ шлюза Kommo отклонён (HTTP 403) — {KEY_WORDS}"),
        (httpx.Response(404, text="not found"), KommoRefusedError, f"шлюз Kommo ответил HTTP 404 — {ADDRESS}"),
        (httpx.Response(301, headers={"Location": "https://gateway.example.test/v2"}), KommoRefusedError, f"шлюз Kommo ответил HTTP 301 — {ADDRESS}"),
    ],
)  # fmt: skip
async def test_refusals_are_permanent_in_words_without_repeats(
    reply: httpx.Response, kind: type[KommoRefusedError], words: str
) -> None:
    script = Script(reply)

    with pytest.raises(KommoRefusedError) as refused:
        await _gateway(script, lambda client: client.create_complex_lead(LEAD))

    assert type(refused.value) is kind
    assert str(refused.value) == words
    assert is_permanent(refused.value)
    assert len(script.requests) == 1


@pytest.mark.parametrize(
    ("reply", "words", "asked"),
    [
        (httpx.Response(429, headers={"Retry-After": "7"}), "шлюз Kommo не принял запрос (HTTP 429, просит подождать 7 с) — повторим позже", 7.0),
        (httpx.Response(429), "шлюз Kommo не принял запрос (HTTP 429) — повторим позже", None),
    ],
)  # fmt: skip
async def test_429_is_temporary_and_left_to_the_pass(
    reply: httpx.Response, words: str, asked: float | None
) -> None:
    """Повтор — проходом передачи, а не внутри запроса: попытка одна."""
    script = Script(reply)

    with pytest.raises(KommoUnavailableError) as refused:
        await _gateway(script, lambda client: client.create_complex_lead(LEAD))

    assert str(refused.value) == words
    assert refused.value.retry_after == asked
    assert not is_permanent(refused.value)
    assert len(script.requests) == 1


@pytest.mark.parametrize(
    ("reply", "words"),
    [
        (httpx.Response(503, headers={"Retry-After": "41"}), "шлюз Kommo ответил HTTP 503 уже после отправки — сделка могла создаться: проверить в Kommo руками; повтор вслепую завёл бы вторую"),
        (httpx.Response(500, json={"success": False, "error": "kommo down"}), "шлюз Kommo ответил HTTP 500: kommo down уже после отправки — сделка могла создаться: проверить в Kommo руками; повтор вслепую завёл бы вторую"),
        (httpx.Response(502, text="<html>bad gateway</html>"), "шлюз Kommo ответил HTTP 502 уже после отправки — сделка могла создаться: проверить в Kommo руками; повтор вслепую завёл бы вторую"),
        (httpx.Response(504), "шлюз Kommo ответил HTTP 504 уже после отправки — сделка могла создаться: проверить в Kommo руками; повтор вслепую завёл бы вторую"),
    ],
)  # fmt: skip
async def test_5xx_is_unconfirmed_until_the_agency_says_nothing_was_made(
    reply: httpx.Response, words: str
) -> None:
    """Запрос дошёл, и шлюз мог завести сделку до сбоя: повтор вслепую завёл бы вторую,
    а дубль в чужой CRM не отзывается. Решает человек консолью."""
    script = Script(reply)

    with pytest.raises(KommoUnconfirmedError) as refused:
        await _gateway(script, lambda client: client.create_complex_lead(LEAD))

    assert str(refused.value) == words
    assert len(script.requests) == 1


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectError("connection refused"),
        httpx.ConnectTimeout("timed out"),
        httpx.PoolTimeout("no free connection"),
    ],
)
async def test_a_request_that_never_left_is_temporary(failure: Exception) -> None:
    script = Script(failure)

    with pytest.raises(KommoUnavailableError) as refused:
        await _gateway(script, lambda client: client.create_complex_lead(LEAD))

    assert str(refused.value) == (
        f"шлюз Kommo не ответил: связь оборвалась ({type(failure).__name__}) — в CRM ничего "
        "не записано, повторим позже"
    )
    assert len(script.requests) == 1


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ReadTimeout("timed out"),
        httpx.ReadError("connection reset by peer"),
        httpx.RemoteProtocolError("Server disconnected without sending a response."),
        httpx.WriteTimeout("timed out"),
    ],
)
async def test_a_lead_lost_after_sending_is_unconfirmed_and_not_sent_again(
    failure: Exception,
) -> None:
    """Запрос ушёл, ответа нет — сделка могла создаться. Повтора нет: решает человек."""
    script = Script(failure)

    with pytest.raises(KommoUnconfirmedError) as refused:
        await _gateway(script, lambda client: client.create_complex_lead(LEAD))

    assert str(refused.value) == (
        f"ответ шлюза Kommo потерян после отправки ({type(failure).__name__}) — запись могла "
        "создаться; проверить в Kommo руками: повтор вслепую завёл бы вторую"
    )
    assert is_permanent(refused.value)
    assert len(script.requests) == 1


async def test_a_request_that_cannot_be_built_is_refused_not_repeated() -> None:
    script = Script(httpx.LocalProtocolError(f"Illegal header value b'Bearer {KEY}\\n'"))

    with pytest.raises(KommoRefusedError) as refused:
        await _gateway(script, lambda client: client.create_complex_lead(LEAD))

    assert str(refused.value) == (
        "запрос к шлюзу Kommo не собран (LocalProtocolError) — проверить "
        "SALES_KOMMO_GATEWAY_KEY и SALES_KOMMO_GATEWAY_URL; повтор не поможет"
    )
    assert len(script.requests) == 1


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(200, json={"success": True}),
        httpx.Response(200, json={"lead_id": 9341, "contact_id": 77031}),
        httpx.Response(200, json={"success": "true", "lead_id": 9341}),
        httpx.Response(200, json={"success": True, "lead_id": "9341"}),
        httpx.Response(200, json={"success": True, "lead_id": 0}),
        httpx.Response(200, json={"success": True, "lead_id": True}),
        httpx.Response(200, json=[{"success": True, "lead_id": 9341}]),
        httpx.Response(200, text="ok"),
        httpx.Response(204),
    ],
)
async def test_a_reply_without_success_and_a_deal_number_is_a_loud_format_refusal(
    reply: httpx.Response,
) -> None:
    script = Script(reply)

    with pytest.raises(KommoFormatError) as refused:
        await _gateway(script, lambda client: client.create_complex_lead(LEAD))

    words = str(refused.value)
    assert words.startswith(
        f"шлюз Kommo ответил HTTP {reply.status_code} без «success: true» и номера сделки: "
    )
    assert "сделка могла создаться" in words
    assert is_permanent(refused.value)
    assert len(script.requests) == 1


# --- чего шлюз не умеет ------------------------------------------------------------------------


async def test_the_gateway_can_only_make_a_deal_with_its_first_note() -> None:
    assert KommoGateway.can == KommoAbilities(search=False, add_notes=False, note_in_lead=True)
    assert KommoLive.can == KommoFixture.can == FULL == KommoAbilities()
    assert (FULL.search, FULL.add_notes, FULL.note_in_lead) == (True, True, False)


async def test_search_is_not_found_without_a_request() -> None:
    script = Script()

    found = await _gateway(script, lambda client: client.find_contact(EMAIL))

    assert found is None
    assert script.requests == []


@pytest.mark.parametrize("email", ["", "   ", "not-an-address"])
async def test_a_lead_without_an_address_is_a_caller_error(email: str) -> None:
    script = Script()

    with pytest.raises(ValueError, match="нет почты"):
        await _gateway(script, lambda client: client.find_contact(email))
    with pytest.raises(ValueError, match="нет почты"):
        await _gateway(
            script,
            lambda client: client.create_complex_lead(NewLead(email, "acme.example.test", "h")),
        )
    assert script.requests == []


async def test_a_note_to_a_made_deal_is_refused_without_a_request() -> None:
    script = Script()

    with pytest.raises(KommoRefusedError) as refused:
        await _gateway(script, lambda client: client.add_note(9341, "сводка"))

    assert str(refused.value) == (
        "примечание к сделке №9341 (6 знаков) шлюз Kommo не принимает — метода нет: новый "
        "ответ лида уходит телемаркетологу сообщением"
    )
    assert script.requests == []


async def test_the_gateway_keeps_the_pace_of_the_direct_path() -> None:
    script = Script(_made(), _made(9342))

    async def twice(client: KommoGateway) -> None:
        await client.create_complex_lead(LEAD)
        await client.create_complex_lead(LEAD)

    await _gateway(script, twice)

    first, second = script.times
    assert second - first >= 1 / sales_cfg.KOMMO_RATE_PER_SEC - 1e-9


async def test_without_a_pace_of_its_own_the_gateway_takes_the_direct_one() -> None:
    """Темп шлюза — тот же, что у прямого пути: `KOMMO_RATE_PER_SEC` запросов в секунду."""
    async with httpx.AsyncClient() as http:
        gateway, direct = KommoGateway(http, ACCOUNT), KommoLive(http, DIRECT)

    assert gateway._pace._gap == direct._pace._gap == 1 / sales_cfg.KOMMO_RATE_PER_SEC


# --- фабрика: выбор пути и отказы на старте ----------------------------------------------------

GATEWAY_FILLED = {
    "KOMMO_GATEWAY_URL": URL,
    "KOMMO_GATEWAY_KEY": KEY,
    "KOMMO_SOURCE": SOURCE,
    "KOMMO_TAG": TAG,
    "KOMMO_SUBDOMAIN": "acme-test",
    "KOMMO_PIPELINE_ID": "5813",
    "KOMMO_STATUS_ID": "70429",
    # Шлюзу они не нужны — пустые, чтобы было видно, что их никто не просит.
    "KOMMO_TOKEN": "",
    "KOMMO_RESPONSIBLE_USER_ID": "",
}


def _configure(monkeypatch: pytest.MonkeyPatch, provider: str, **overrides: str) -> None:
    monkeypatch.setattr(sales_cfg, "KOMMO_PROVIDER", provider)
    for name, value in {**GATEWAY_FILLED, **overrides}.items():
        monkeypatch.setattr(sales_cfg, name, value)


async def _build(script: Script) -> KommoClient:
    async with httpx.AsyncClient(transport=httpx.MockTransport(script)) as http:
        return build_kommo(http)


async def test_live_with_a_gateway_address_goes_through_the_gateway(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _configure(monkeypatch, " Live ")
    script = Script()

    with caplog.at_level(logging.INFO, logger=kommo.__name__):
        built = await _build(script)

    assert type(built) is KommoGateway
    assert isinstance(built, KommoClient)
    assert built.name == "live"
    assert built.lead_url(9341) == DEAL
    assert "продажи: Kommo — live через шлюз агентства" in caplog.text
    for secret in (KEY, URL, "gateway.example.test"):
        assert secret not in caplog.text
    assert script.requests == []


async def test_live_without_a_gateway_address_goes_to_api_v4_as_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, "live", **FILLED, KOMMO_GATEWAY_URL="")

    assert type(await _build(Script())) is KommoLive


async def test_fixture_stays_the_fixture_whatever_the_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, "fixture")

    assert type(await _build(Script())) is KommoFixture


MISSING = (
    "Заполнить их, убрать адрес шлюза или вернуть fixture — без них сделки в Kommo не заводятся"
)
GATEWAY_LIVE = "SALES_KOMMO_PROVIDER=live через шлюз (задан SALES_KOMMO_GATEWAY_URL), а не заданы"


@pytest.mark.parametrize(
    ("setting", "env"),
    [
        ("KOMMO_GATEWAY_KEY", "SALES_KOMMO_GATEWAY_KEY"),
        ("KOMMO_SOURCE", "SALES_KOMMO_SOURCE"),
        ("KOMMO_TAG", "SALES_KOMMO_TAG"),
        ("KOMMO_PIPELINE_ID", "SALES_KOMMO_PIPELINE_ID"),
        ("KOMMO_STATUS_ID", "SALES_KOMMO_STATUS_ID"),
        ("KOMMO_SUBDOMAIN", "SALES_KOMMO_SUBDOMAIN"),
    ],
)
async def test_the_gateway_without_any_of_its_settings_refuses_before_the_first_call(
    monkeypatch: pytest.MonkeyPatch, setting: str, env: str
) -> None:
    _configure(monkeypatch, "live", **{setting: ""})
    script = Script()

    with pytest.raises(ConfigError) as refused:
        await _build(script)

    assert str(refused.value) == f"{GATEWAY_LIVE}: {env}. {MISSING}"
    assert script.requests == []


async def test_every_missing_gateway_setting_is_named_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(
        monkeypatch, "live", KOMMO_GATEWAY_KEY="", KOMMO_SOURCE="", KOMMO_TAG="", KOMMO_SUBDOMAIN=""
    )

    with pytest.raises(ConfigError) as refused:
        await _build(Script())

    assert str(refused.value) == (
        f"{GATEWAY_LIVE}: SALES_KOMMO_GATEWAY_KEY, SALES_KOMMO_SOURCE, SALES_KOMMO_TAG, "
        f"SALES_KOMMO_SUBDOMAIN. {MISSING}"
    )


BAD_ADDRESS = (
    "SALES_KOMMO_GATEWAY_URL — нужен полный адрес шлюза https://…: имя, почта и письмо лида "
    "и ключ уходят только шифрованным каналом"
)
BAD_KEY = (
    "SALES_KOMMO_GATEWAY_KEY с пробелом, переводом строки или знаком вне латиницы — заголовок "
    "с ним не собрать; скопировать ключ шлюза заново"
)
NUMBER = "нужен номер из Kommo: целое больше нуля"


@pytest.mark.parametrize(
    ("overrides", "words"),
    [
        ({"KOMMO_GATEWAY_URL": "http://gateway.example.test/hooks/lead"}, BAD_ADDRESS),
        ({"KOMMO_GATEWAY_URL": "gateway.example.test/hooks/lead"}, BAD_ADDRESS),
        ({"KOMMO_GATEWAY_URL": "https://"}, BAD_ADDRESS),
        ({"KOMMO_GATEWAY_URL": "https://[gateway.example.test/hooks"}, BAD_ADDRESS),
        ({"KOMMO_GATEWAY_URL": f"{URL}\n"}, BAD_ADDRESS),
        ({"KOMMO_GATEWAY_KEY": f"{KEY} 2"}, BAD_KEY),
        ({"KOMMO_GATEWAY_KEY": f"{KEY}\n"}, BAD_KEY),
        ({"KOMMO_GATEWAY_KEY": f"{KEY}ключ"}, BAD_KEY),
        ({"KOMMO_PIPELINE_ID": "воронка"}, f"SALES_KOMMO_PIPELINE_ID=«воронка» — {NUMBER}"),
        ({"KOMMO_STATUS_ID": "0"}, f"SALES_KOMMO_STATUS_ID=«0» — {NUMBER}"),
        ({"KOMMO_SUBDOMAIN": "acme-test.kommo.com"}, "SALES_KOMMO_SUBDOMAIN=«acme-test.kommo.com» — нужен только поддомен: acme из acme.kommo.com"),
    ],
)  # fmt: skip
async def test_an_unusable_gateway_setting_refuses_and_shows_neither_address_nor_key(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, str], words: str
) -> None:
    _configure(monkeypatch, "live", **overrides)

    with pytest.raises(ConfigError) as refused:
        await _build(Script())

    assert str(refused.value) == words
    for secret in (KEY, "gateway.example.test"):
        assert secret not in str(refused.value)


SETTINGS = (
    ("SALES_KOMMO_GATEWAY_URL", "kommo_gateway_url", URL),
    ("SALES_KOMMO_GATEWAY_KEY", "kommo_gateway_key", KEY),
    ("SALES_KOMMO_SOURCE", "kommo_source", SOURCE),
    ("SALES_KOMMO_TAG", "kommo_tag", TAG),
)


def test_gateway_settings_are_empty_by_default_and_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Урок L4 соседнего проекта: поле читается только по алиасу — оба значения."""
    for env, *_ in SETTINGS:
        monkeypatch.delenv(env, raising=False)
    empty = sales_cfg._Sales(_env_file=None)
    assert [getattr(empty, field) for _, field, _ in SETTINGS] == ["", "", "", ""]

    for env, _, value in SETTINGS:
        monkeypatch.setenv(env, value)
    filled = sales_cfg._Sales(_env_file=None)
    assert [getattr(filled, field) for _, field, _ in SETTINGS] == [v for *_, v in SETTINGS]


def test_the_suite_never_sees_a_real_gateway(request: pytest.FixtureRequest) -> None:
    """Адрес и ключ шлюза из `.env` разработчика не доезжают ни до одного теста: держит
    автофикстура `tests/conftest.py::_no_real_kommo`."""
    assert "_no_real_kommo" in request.fixturenames
    assert (sales_cfg.KOMMO_GATEWAY_URL, sales_cfg.KOMMO_GATEWAY_KEY) == ("", "")
