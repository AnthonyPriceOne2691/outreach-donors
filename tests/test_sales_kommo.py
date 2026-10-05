"""Клиент Kommo — срез 5.2 (A1–A5): fixture, live за `MockTransport`, фабрика по настройке.

Сеть — только `httpx.MockTransport`; ответы Kommo выдуманы по его документации (API v4),
номера некруглые. Ключ — заведомо выдуманный и узнаваемый: по нему видно, что он не утёк
ни в текст отказа, ни в журнал, ни в адрес запроса. Запись в Kommo — побочный эффект в
чужой CRM, поэтому весь набор идёт на `fixture` без ключа (`tests/conftest.py::_no_real_kommo`),
а живой режим тесты включают сами и только с подставным транспортом.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
from backend.config import sales as sales_cfg
from backend.config.startup_checks import ConfigError
from backend.features.runs.failures import is_permanent
from backend.features.sales import kommo, kommo_live
from backend.features.sales.kommo import (
    CreatedLead,
    KommoAccount,
    KommoAuthError,
    KommoClient,
    KommoContact,
    KommoError,
    KommoFixture,
    KommoFormatError,
    KommoLive,
    KommoRefusedError,
    KommoUnavailableError,
    NewLead,
    Pace,
    build_kommo,
)

TOKEN = "kommo-fake-token-3f9c"
ACCOUNT = KommoAccount(
    subdomain="acme-test",
    token=TOKEN,
    pipeline_id=4417,
    status_id=61953,
    responsible_user_id=88231,
)
API = "https://acme-test.kommo.com/api/v4"
EMAIL = "ivan@acme.example.test"
LEAD = NewLead(
    email=EMAIL,
    site="acme.example.test",
    hypothesis="Гипотеза пробная",
    company="Acme Test",
    name="Ivan Petrov",
)
EMAIL_FIELD = [{"field_code": "EMAIL", "values": [{"value": EMAIL, "enum_code": "WORK"}]}]


class Clock:
    """Часы без ожидания: сон двигает время, а не держит тест."""

    def __init__(self) -> None:
        self.now = 0.0

    def time(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


class Script:
    """Подставной Kommo: отвечает заготовками по очереди и запоминает запросы и их время."""

    def __init__(self, *replies: httpx.Response | Exception, clock: Clock | None = None) -> None:
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []
        self.times: list[float] = []
        self.clock = clock or Clock()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.times.append(self.clock.now)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def body(self, number: int) -> Any:
        return json.loads(self.requests[number].content)


async def _live[T](script: Script, act: Callable[[KommoLive], Awaitable[T]]) -> T:
    async with httpx.AsyncClient(transport=httpx.MockTransport(script)) as http:
        pace = Pace(7, clock=script.clock.time, sleep=script.clock.sleep)
        return await act(KommoLive(http, ACCOUNT, pace=pace))


@pytest.fixture(autouse=True)
def pauses(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Паузы между повторами проверяются числами, а не часами: сон подменён записью."""
    seen: list[float] = []

    async def record(seconds: float) -> None:
        seen.append(seconds)

    monkeypatch.setattr(kommo_live, "_sleep", record)
    return seen


def _nobody() -> httpx.Response:
    return httpx.Response(204)


def _contact(contact_id: int, *emails: str, name: str = "Ivan Petrov") -> dict[str, Any]:
    values = [{"value": email, "enum_id": 3313, "enum": "WORK"} for email in emails]
    field = {"field_id": 2203, "field_name": "Email", "field_code": "EMAIL", "values": values}
    return {"id": contact_id, "name": name, "custom_fields_values": [field]}


def _contacts(*contacts: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"_page": 1, "_embedded": {"contacts": list(contacts)}})


def _created(
    lead_id: int = 9341, contact_id: int | None = 77031, company_id: int | None = 50219
) -> httpx.Response:
    row = {"id": lead_id, "contact_id": contact_id, "company_id": company_id}
    return httpx.Response(200, json=[{**row, "request_id": ["0"], "merged": False}])


def _problem(status: int, detail: str, **extra: Any) -> httpx.Response:
    kind = f"https://httpstatus.es/{status}"
    body = {"title": "Problem", "type": kind, "status": status, "detail": detail, **extra}
    return httpx.Response(status, json=body)


# --- A1: контакт с этой почтой уже есть ---------------------------------------------------


async def test_a1_contact_with_this_email_gets_the_lead_and_no_second_contact() -> None:
    # A1 — пример спеки
    script = Script(
        _contacts(_contact(5823, "ivan@acme.example.test.other"), _contact(77031, "IVAN@Acme.Example.Test")),
        _created(9341, contact_id=77031),
    )  # fmt: skip

    created = await _live(script, lambda client: client.create_complex_lead(LEAD))

    assert created == CreatedLead(
        id=9341,
        url="https://acme-test.kommo.com/leads/detail/9341",
        contact_id=77031,
        company_id=50219,
        contact_found=True,
    )
    find, create = script.requests
    assert (find.method, str(find.url)) == ("GET", f"{API}/contacts?query=ivan%40acme.example.test")
    assert (create.method, str(create.url)) == ("POST", f"{API}/leads/complex")
    assert script.body(1)[0]["_embedded"]["contacts"] == [{"id": 77031}]


async def test_a1_without_such_contact_a_new_one_comes_with_name_and_work_email() -> None:
    # A1 — пример спеки
    script = Script(_nobody(), _created(9341, contact_id=77043))

    created = await _live(script, lambda client: client.create_complex_lead(LEAD))

    assert (created.contact_id, created.contact_found) == (77043, False)
    assert script.body(1)[0]["_embedded"]["contacts"] == [
        {"name": "Ivan Petrov", "custom_fields_values": EMAIL_FIELD}
    ]


async def test_a1_a_near_match_of_the_fuzzy_search_is_not_the_same_contact() -> None:
    # A1 — пример спеки: поиск Kommo нечёткий, «похожий» адрес — другой человек
    near = _contact(5823, "ivan@acme.example.test.other", "vanya.ivan@acme.example.test")
    # Контакт без полей: вместо пустого списка Kommo шлёт null.
    bare = {"id": 6107, "name": "Без полей", "custom_fields_values": None}
    script = Script(_contacts(near, bare), _created(contact_id=77043))

    created = await _live(script, lambda client: client.create_complex_lead(LEAD))

    assert created.contact_found is False
    assert script.body(1)[0]["_embedded"]["contacts"][0]["custom_fields_values"] == EMAIL_FIELD


async def test_a1_two_contacts_with_one_email_link_to_the_oldest_and_say_so(
    caplog: pytest.LogCaptureFixture,
) -> None:
    script = Script(_contacts(_contact(77031, EMAIL), _contact(6029, EMAIL)))

    with caplog.at_level(logging.WARNING, logger=kommo_live.__name__):
        found = await _live(script, lambda client: client.find_contact(EMAIL))

    assert found == KommoContact(6029, "Ivan Petrov")
    assert "несколько контактов с одной почтой" in caplog.text


async def test_a1_fixture_links_a_known_contact_and_keeps_one_contact_per_email() -> None:
    # A1 — пример спеки
    fixture = KommoFixture()
    known = fixture.seed_contact("Ivan@Acme.example.test", "Ivan Petrov")

    first = await fixture.create_complex_lead(LEAD)
    second = await fixture.create_complex_lead(LEAD)

    assert (first.contact_id, first.contact_found) == (known.id, True)
    assert (second.contact_id, second.contact_found) == (known.id, True)
    assert list(fixture.contacts.values()) == [known]
    assert second.id == first.id + 1


# --- сделка одним запросом и примечание -----------------------------------------------------


async def test_the_lead_goes_as_one_complex_request_with_pipeline_stage_owner_tags_and_company() -> (
    None
):
    script = Script(_nobody(), _created())

    await _live(script, lambda client: client.create_complex_lead(LEAD))

    website = [{"field_code": "WEB", "values": [{"value": "acme.example.test"}]}]
    assert script.body(1) == [
        {
            "name": "Email: Acme Test",
            "pipeline_id": 4417,
            "status_id": 61953,
            "responsible_user_id": 88231,
            "_embedded": {
                "tags": [{"name": "Email рассылка"}, {"name": "Гипотеза пробная"}],
                "contacts": [{"name": "Ivan Petrov", "custom_fields_values": EMAIL_FIELD}],
                "companies": [{"name": "Acme Test", "custom_fields_values": website}],
            },
        }
    ]


async def test_without_a_company_name_the_lead_and_company_are_named_by_the_site() -> None:
    bare = NewLead(email=EMAIL, site="acme.example.test", hypothesis="Гипотеза пробная")
    script = Script(_nobody(), _created())

    await _live(script, lambda client: client.create_complex_lead(bare))

    sent = script.body(1)[0]
    assert sent["name"] == "Email: acme.example.test"
    assert sent["_embedded"]["companies"][0]["name"] == "acme.example.test"
    # Имени человека нет — не выдумываем: контакт только с почтой.
    assert sent["_embedded"]["contacts"] == [{"custom_fields_values": EMAIL_FIELD}]


async def test_a_note_goes_to_the_lead_and_its_number_comes_back() -> None:
    notes = {"_embedded": {"notes": [{"id": 8263, "entity_id": 9341, "request_id": "0"}]}}
    script = Script(httpx.Response(200, json=notes))

    note = await _live(script, lambda client: client.add_note(9341, "Последнее письмо: созвонимся"))

    assert note == 8263
    (request,) = script.requests
    assert (request.method, str(request.url)) == ("POST", f"{API}/leads/9341/notes")
    assert script.body(0) == [
        {"note_type": "common", "params": {"text": "Последнее письмо: созвонимся"}}
    ]


@pytest.mark.parametrize("email", ["", "   ", "not-an-address"])
async def test_a_lead_without_an_address_is_a_caller_error_not_a_search(email: str) -> None:
    """Пустой поиск Kommo вернул бы всех, а пустой адрес совпал бы с контактом без почты."""
    script = Script()

    with pytest.raises(ValueError, match="нет почты"):
        await _live(script, lambda client: client.find_contact(email))
    with pytest.raises(ValueError, match="нет почты"):
        await KommoFixture().find_contact(email)
    assert script.requests == []


# --- A2 и повторы: временное повторяем, записанное — нет --------------------------------------


async def test_a2_429_with_retry_after_pauses_as_asked_and_repeats(pauses: list[float]) -> None:
    # A2 — пример спеки
    script = Script(
        httpx.Response(429, headers={"Retry-After": "2"}), _contacts(_contact(77031, EMAIL))
    )

    found = await _live(script, lambda client: client.find_contact(EMAIL))

    assert found == KommoContact(77031, "Ivan Petrov")
    assert len(script.requests) == 2
    assert pauses == [2.0]


async def test_a2_a_lead_refused_by_429_is_not_written_and_goes_again(pauses: list[float]) -> None:
    # A2 — пример спеки: 429 — Kommo отказал до записи, повтор не заводит вторую сделку
    script = Script(_nobody(), httpx.Response(429, headers={"Retry-After": "2"}), _created())

    created = await _live(script, lambda client: client.create_complex_lead(LEAD))

    assert created.id == 9341
    assert [request.method for request in script.requests] == ["GET", "POST", "POST"]
    assert pauses == [2.0]


async def test_kommo_puts_its_pause_into_the_body_of_429_and_it_is_kept(
    pauses: list[float],
) -> None:
    script = Script(_problem(429, "Too Many Requests", retry_after=3), _nobody())

    assert await _live(script, lambda client: client.find_contact(EMAIL)) is None

    assert pauses == [3.0]


async def test_a_pause_longer_than_the_ceiling_is_a_refusal_naming_it_not_a_sleep(
    pauses: list[float],
) -> None:
    """Повтор внутри окна запрета у Kommo — путь к блокировке IP (403): ждать
    за пределами потолка — дело расписания задачи, а не сна внутри запроса."""
    script = Script(_problem(429, "Too Many Requests", retry_after=317))

    with pytest.raises(KommoUnavailableError) as refused:
        await _live(script, lambda client: client.find_contact(EMAIL))

    assert str(refused.value) == (
        "Kommo не принял запрос (HTTP 429, просит подождать 317 с) — повторим позже"
    )
    assert refused.value.retry_after == 317
    assert (len(script.requests), pauses) == (1, [])
    assert not is_permanent(refused.value)


@pytest.mark.parametrize(
    "first",
    [
        httpx.Response(503),
        httpx.Response(502, text="<html>bad gateway</html>"),
        httpx.Response(500),
        httpx.Response(504),
        httpx.ConnectError("connection refused"),
        httpx.ConnectTimeout("timed out"),
        httpx.ReadTimeout("timed out"),
        httpx.RemoteProtocolError("Server disconnected without sending a response."),
    ],
)
async def test_a_temporary_failure_of_a_read_is_repeated(
    first: httpx.Response | Exception, pauses: list[float]
) -> None:
    script = Script(first, _nobody())

    assert await _live(script, lambda client: client.find_contact(EMAIL)) is None

    assert (len(script.requests), len(pauses)) == (2, 1)


@pytest.mark.parametrize(
    ("replies", "words", "asked"),
    [
        ([httpx.Response(503) for _ in range(3)], "Kommo не принял запрос (HTTP 503) — повторим позже", None),
        ([httpx.Response(429, headers={"Retry-After": "1"}) for _ in range(3)], "Kommo не принял запрос (HTTP 429, просит подождать 1 с) — повторим позже", 1.0),
    ],
)  # fmt: skip
async def test_temporary_failures_outlasting_three_attempts_become_unavailable(
    replies: list[httpx.Response], words: str, asked: float | None, pauses: list[float]
) -> None:
    script = Script(*replies)

    with pytest.raises(KommoUnavailableError) as refused:
        await _live(script, lambda client: client.find_contact(EMAIL))

    assert str(refused.value) == words
    assert refused.value.retry_after == asked
    assert (len(script.requests), len(pauses)) == (3, 2)


async def test_a_network_failure_outlasting_the_attempts_names_neither_address_nor_token(
    pauses: list[float], caplog: pytest.LogCaptureFixture
) -> None:
    script = Script(*[httpx.ConnectError(f"cannot connect to {API}/contacts") for _ in range(3)])

    with caplog.at_level(logging.DEBUG), pytest.raises(KommoUnavailableError) as refused:
        await _live(script, lambda client: client.find_contact(EMAIL))

    assert str(refused.value) == (
        "Kommo не ответил: связь оборвалась (ConnectError) — в CRM ничего не записано, "
        "повторим позже"
    )
    # Цепочки к httpx нет ни явной (`from exc`), ни неявной (подавлена `from None`).
    assert (refused.value.__cause__, refused.value.__suppress_context__) == (None, True)
    assert (len(script.requests), len(pauses)) == (3, 2)
    assert "kommo: повтор запроса" in caplog.text
    for text in (str(refused.value), caplog.text):
        assert API not in text
        assert TOKEN not in text


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ReadTimeout("timed out"),
        httpx.ReadError("connection reset by peer"),
        httpx.RemoteProtocolError("Server disconnected without sending a response."),
        httpx.WriteTimeout("timed out"),
    ],
)
async def test_a_lead_is_not_sent_twice_once_it_may_have_reached_kommo(
    failure: Exception, pauses: list[float]
) -> None:
    """У Kommo нет ключа идемпотентности: запрос ушёл, ответ потерян — сделка могла
    создаться, и повтор вслепую завёл бы вторую (урок отправки писем: повторяется
    только то, что точно не ушло)."""
    script = Script(_nobody(), failure)

    with pytest.raises(kommo.KommoUnconfirmedError) as refused:
        await _live(script, lambda client: client.create_complex_lead(LEAD))

    assert str(refused.value) == (
        f"ответ Kommo потерян после отправки ({type(failure).__name__}) — запись могла "
        "создаться; проверить в Kommo руками: повтор вслепую завёл бы вторую"
    )
    assert [request.method for request in script.requests] == ["GET", "POST"]
    assert (is_permanent(refused.value), pauses) == (True, [])


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectError("connection refused"),
        httpx.ConnectTimeout("timed out"),
        httpx.PoolTimeout("no free connection"),
    ],
)
async def test_a_lead_that_never_left_is_sent_again(
    failure: Exception, pauses: list[float]
) -> None:
    script = Script(_nobody(), failure, _created())

    created = await _live(script, lambda client: client.create_complex_lead(LEAD))

    assert created.id == 9341
    assert [request.method for request in script.requests] == ["GET", "POST", "POST"]
    assert len(pauses) == 1


async def test_a_note_lost_after_sending_is_not_repeated_either(pauses: list[float]) -> None:
    script = Script(httpx.ReadTimeout("timed out"))

    with pytest.raises(kommo.KommoUnconfirmedError):
        await _live(script, lambda client: client.add_note(9341, "сводка"))

    assert (len(script.requests), pauses) == (1, [])


async def test_every_attempt_keeps_the_pace(pauses: list[float]) -> None:
    script = Script(httpx.Response(503), httpx.Response(503), _nobody())

    assert await _live(script, lambda client: client.find_contact(EMAIL)) is None

    first, second, third = script.times
    assert min(second - first, third - second) >= 1 / 7 - 1e-9
    assert len(pauses) == 2


# --- A3 и прочие отказы без повторов --------------------------------------------------------

STALE_KEY = (
    "ключ закрытой интеграции отозван или истёк: выпустить новый и записать в "
    "SALES_KOMMO_TOKEN; повтор не поможет"
)


@pytest.mark.parametrize(
    ("reply", "words"),
    [
        (_problem(401, "Invalid user name or password"), f"ключ Kommo отклонён (HTTP 401: Invalid user name or password) — {STALE_KEY}"),
        (httpx.Response(401), f"ключ Kommo отклонён (HTTP 401) — {STALE_KEY}"),
    ],
)  # fmt: skip
async def test_a3_401_is_refused_at_once_in_words(reply: httpx.Response, words: str) -> None:
    # A3 — пример спеки
    script = Script(reply)

    with pytest.raises(KommoAuthError) as refused:
        await _live(script, lambda client: client.find_contact(EMAIL))

    assert str(refused.value) == words
    assert len(script.requests) == 1
    assert is_permanent(refused.value)


async def test_a3_401_on_the_lead_itself_is_the_same_refusal() -> None:
    # A3 — пример спеки
    script = Script(_nobody(), _problem(401, "Invalid user name or password"))

    with pytest.raises(KommoAuthError, match=r"^ключ Kommo отклонён"):
        await _live(script, lambda client: client.create_complex_lead(LEAD))

    assert [request.method for request in script.requests] == ["GET", "POST"]


INVALID = {
    "validation-errors": [
        {
            "request_id": "0",
            "errors": [
                {
                    "code": "NotSupportedChoice",
                    "path": "status_id",
                    "detail": "Not a valid choice.",
                },
                {"code": "InvalidType", "path": "pipeline_id", "detail": "Wrong type."},
            ],
        }
    ]
}
CHECK = "повтор не поможет: проверить поддомен, воронку, этап, ответственного и обязательные поля"


@pytest.mark.parametrize(
    ("reply", "words"),
    [
        (_problem(400, "Request validation failed", **INVALID), f"Kommo отверг запрос (HTTP 400: Request validation failed; status_id: Not a valid choice.; pipeline_id: Wrong type.) — {CHECK}"),
        (_problem(402, "Payment Required"), "подписка Kommo не оплачена (HTTP 402: Payment Required) — сделки не заводятся до оплаты; повтор не поможет"),
        (_problem(403, "Forbidden"), "Kommo закрыл доступ (HTTP 403: Forbidden) — у ключа нет прав или IP заблокирован за частые запросы; повтор не поможет"),
        (httpx.Response(404, text="<html>нет такой страницы</html>"), f"Kommo отверг запрос (HTTP 404) — {CHECK}"),
        (httpx.Response(301, headers={"Location": "https://www.kommo.com/"}), f"Kommo отверг запрос (HTTP 301) — {CHECK}"),
    ],
)  # fmt: skip
async def test_client_errors_are_refusals_in_words_without_repeats(
    reply: httpx.Response, words: str
) -> None:
    script = Script(reply)

    with pytest.raises(KommoRefusedError) as refused:
        await _live(script, lambda client: client.find_contact(EMAIL))

    assert type(refused.value) is KommoRefusedError
    assert str(refused.value) == words
    assert len(script.requests) == 1
    assert is_permanent(refused.value)


async def test_a_request_that_cannot_be_built_is_refused_not_repeated() -> None:
    """Перевод строки в ключе: httpx не собирает заголовок и кладёт его значение в текст
    `LocalProtocolError`. Это не сеть — повтор соберёт запрос так же."""
    script = Script(httpx.LocalProtocolError(f"Illegal header value b'Bearer {TOKEN}\\n'"))

    with pytest.raises(KommoRefusedError) as refused:
        await _live(script, lambda client: client.find_contact(EMAIL))

    assert str(refused.value) == (
        "запрос к Kommo не собран (LocalProtocolError) — проверить SALES_KOMMO_TOKEN "
        "и SALES_KOMMO_SUBDOMAIN; повтор не поможет"
    )
    assert len(script.requests) == 1


# --- A4: форма ответа ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(200, json=[{"request_id": ["0"], "merged": False}]),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"id": 9341}),
        httpx.Response(200, json=[{"id": "9341"}]),
        httpx.Response(200, json=[{"id": True}]),
        httpx.Response(200, json=[{"id": 0}]),
        httpx.Response(200, json=[{"id": 9341}, {"id": 9342}]),
        httpx.Response(200, text="ok"),
        httpx.Response(204),
    ],
)
async def test_a4_a_reply_without_the_lead_number_is_a_loud_format_refusal(
    reply: httpx.Response,
) -> None:
    # A4 — пример спеки
    script = Script(_nobody(), reply)

    with pytest.raises(KommoFormatError) as refused:
        await _live(script, lambda client: client.create_complex_lead(LEAD))

    words = str(refused.value)
    assert words.startswith(f"Kommo ответил HTTP {reply.status_code} без номера сделки")
    assert "сделка могла создаться" in words
    assert is_permanent(refused.value)
    assert len(script.requests) == 2  # ни одного повтора: вслепую завели бы вторую


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(200, json={"_page": 1}),
        httpx.Response(200, json={"_embedded": {"contacts": {"id": 5823}}}),
        httpx.Response(200, json=[]),
        httpx.Response(200, text="<html>обслуживание</html>"),
        _contacts({"name": "без номера", "custom_fields_values": EMAIL_FIELD}),
        _contacts(_contact(0, EMAIL)),
    ],
)
async def test_an_unknown_contacts_reply_is_not_taken_for_no_contact(reply: httpx.Response) -> None:
    """«Мы не поняли» ≠ «контакта нет»: тихий `None` здесь завёл бы дубль контакта."""
    script = Script(reply)

    with pytest.raises(KommoFormatError) as refused:
        await _live(script, lambda client: client.find_contact(EMAIL))

    assert "формат поменялся" in str(refused.value)
    assert is_permanent(refused.value)


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(200, json={"_embedded": {"notes": []}}),
        httpx.Response(200, json={"_embedded": {"notes": [{"entity_id": 9341}]}}),
        httpx.Response(204),
    ],
)
async def test_a_note_reply_without_its_number_is_a_format_refusal(reply: httpx.Response) -> None:
    script = Script(reply)

    with pytest.raises(KommoFormatError) as refused:
        await _live(script, lambda client: client.add_note(9341, "сводка"))

    assert str(refused.value).startswith(f"Kommo ответил HTTP {reply.status_code} без номера")


# --- ключ: только в заголовке ----------------------------------------------------------------


async def test_the_token_rides_in_a_header_never_in_the_address() -> None:
    script = Script(_nobody())

    assert await _live(script, lambda client: client.find_contact(EMAIL)) is None

    (request,) = script.requests
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in str(request.url)
    assert TOKEN not in repr(ACCOUNT)


@pytest.mark.parametrize(
    "reply",
    [
        _problem(401, "Invalid user name or password"),
        _problem(403, "Forbidden"),
        _problem(400, "Request validation failed", **INVALID),
        httpx.Response(200, json={}),
        httpx.LocalProtocolError(f"Illegal header value b'Bearer {TOKEN} '"),
    ],
)
async def test_the_token_never_reaches_an_error_text_or_the_log(
    reply: httpx.Response | Exception, caplog: pytest.LogCaptureFixture
) -> None:
    script = Script(reply)

    with caplog.at_level(logging.DEBUG), pytest.raises(KommoError) as refused:
        await _live(script, lambda client: client.find_contact(EMAIL))

    assert TOKEN not in str(refused.value)
    assert TOKEN not in caplog.text
    if isinstance(reply, Exception):
        # Цепочки к исключению httpx нет: в нём запрос с заголовком, а в тексте — ключ.
        assert refused.value.__suppress_context__ is True
        assert refused.value.__cause__ is None


# --- частота --------------------------------------------------------------------------------


async def test_no_more_than_seven_requests_start_in_any_second() -> None:
    clock = Clock()
    pace = Pace(7, clock=clock.time, sleep=clock.sleep)
    starts: list[float] = []

    for _ in range(15):
        await pace.wait()
        starts.append(clock.now)

    second = 1.0 - 1e-9  # окно в секунду без шума плавающей точки
    assert max(sum(t <= s < t + second for s in starts) for t in starts) == 7
    assert starts[7] == pytest.approx(1.0)


async def test_the_live_client_keeps_its_requests_apart() -> None:
    script = Script(_nobody(), _created())

    await _live(script, lambda client: client.create_complex_lead(LEAD))

    find, create = script.times
    assert create - find >= 1 / 7 - 1e-9


# --- fixture ---------------------------------------------------------------------------------


async def test_fixture_numbers_are_made_up_repeatable_and_remembered() -> None:
    first, second = KommoFixture(), KommoFixture()

    one = await first.create_complex_lead(LEAD)
    other = await second.create_complex_lead(LEAD)
    note = await first.add_note(one.id, "сводка переписки")

    assert one == other  # те же вызовы — те же номера
    assert one.url == f"https://fixture.kommo.com/leads/detail/{one.id}" == first.lead_url(one.id)
    assert (first.leads[one.id].draft, first.leads[one.id].notes) == (
        LEAD,
        {note: "сводка переписки"},
    )


async def test_fixture_note_for_an_unknown_lead_is_refused() -> None:
    with pytest.raises(KommoRefusedError) as refused:
        await KommoFixture().add_note(4243, "текст")

    assert str(refused.value) == "сделки №4243 нет в fixture — примечание некуда положить"


# --- фабрика и настройка ---------------------------------------------------------------------

FILLED = {
    "KOMMO_SUBDOMAIN": "acme-test",
    "KOMMO_TOKEN": TOKEN,
    "KOMMO_PIPELINE_ID": "4417",
    "KOMMO_STATUS_ID": "61953",
    "KOMMO_RESPONSIBLE_USER_ID": "88231",
}


def _configure(monkeypatch: pytest.MonkeyPatch, provider: str, **overrides: str) -> None:
    monkeypatch.setattr(sales_cfg, "KOMMO_PROVIDER", provider)
    for name, value in {**FILLED, **overrides}.items():
        monkeypatch.setattr(sales_cfg, name, value)


async def _build(script: Script) -> KommoClient:
    async with httpx.AsyncClient(transport=httpx.MockTransport(script)) as http:
        return build_kommo(http)


@pytest.mark.parametrize(("provider", "kind"), [("fixture", KommoFixture), (" Live ", KommoLive), ("live", KommoLive)])  # fmt: skip
async def test_the_client_is_chosen_by_the_setting(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    provider: str,
    kind: type[object],
) -> None:
    _configure(monkeypatch, provider)
    script = Script()

    with caplog.at_level(logging.INFO, logger=kommo.__name__):
        built = await _build(script)

    assert type(built) is kind
    assert isinstance(built, KommoClient)
    assert ("сделки выдуманные" in caplog.text) is (kind is KommoFixture)
    assert TOKEN not in caplog.text
    assert script.requests == []


async def test_fixture_from_the_factory_never_goes_to_the_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, "fixture")
    script = Script()  # любой запрос уронил бы тест: заготовок нет

    async with httpx.AsyncClient(transport=httpx.MockTransport(script)) as http:
        client = build_kommo(http)
        created = await client.create_complex_lead(LEAD)
        await client.add_note(created.id, "сводка")
        found = await client.find_contact(EMAIL)

    assert found is not None
    assert (found.id, found.name) == (created.contact_id, "Ivan Petrov")
    assert script.requests == []


MISSING = "Заполнить их или вернуть fixture — без них сделки в Kommo не заводятся"


@pytest.mark.parametrize(
    ("overrides", "words"),
    [
        ({"KOMMO_TOKEN": ""}, f"SALES_KOMMO_PROVIDER=live, а не заданы: SALES_KOMMO_TOKEN. {MISSING}"),
        ({"KOMMO_SUBDOMAIN": ""}, f"SALES_KOMMO_PROVIDER=live, а не заданы: SALES_KOMMO_SUBDOMAIN. {MISSING}"),
        (
            {"KOMMO_SUBDOMAIN": "", "KOMMO_TOKEN": "", "KOMMO_STATUS_ID": ""},
            "SALES_KOMMO_PROVIDER=live, а не заданы: SALES_KOMMO_SUBDOMAIN, SALES_KOMMO_TOKEN, "
            f"SALES_KOMMO_STATUS_ID. {MISSING}",
        ),
    ],
)  # fmt: skip
async def test_a5_live_without_a_token_or_subdomain_refuses_before_the_first_call(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, str], words: str
) -> None:
    # A5 — пример спеки
    _configure(monkeypatch, "live", **overrides)
    script = Script()

    with pytest.raises(ConfigError) as refused:
        await _build(script)

    assert str(refused.value) == words
    assert script.requests == []


BAD_TOKEN = (
    "SALES_KOMMO_TOKEN с пробелом, переводом строки или знаком вне латиницы — заголовок с ним "
    "не собрать; скопировать ключ закрытой интеграции заново"
)
NUMBER = "нужен номер из Kommo: целое больше нуля"


@pytest.mark.parametrize(
    ("overrides", "words"),
    [
        ({"KOMMO_SUBDOMAIN": "https://acme-test.kommo.com"}, "SALES_KOMMO_SUBDOMAIN=«https://acme-test.kommo.com» — нужен только поддомен: acme из acme.kommo.com"),
        ({"KOMMO_PIPELINE_ID": "воронка"}, f"SALES_KOMMO_PIPELINE_ID=«воронка» — {NUMBER}"),
        ({"KOMMO_RESPONSIBLE_USER_ID": "0"}, f"SALES_KOMMO_RESPONSIBLE_USER_ID=«0» — {NUMBER}"),
        ({"KOMMO_TOKEN": f"{TOKEN} 2"}, BAD_TOKEN),
        ({"KOMMO_TOKEN": f"{TOKEN}\n"}, BAD_TOKEN),
        ({"KOMMO_TOKEN": f"{TOKEN}ключ"}, BAD_TOKEN),
    ],
)  # fmt: skip
async def test_live_with_an_unusable_setting_refuses_and_never_shows_the_token(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, str], words: str
) -> None:
    _configure(monkeypatch, "live", **overrides)

    with pytest.raises(ConfigError) as refused:
        await _build(Script())

    assert str(refused.value) == words
    assert TOKEN not in str(refused.value)


async def test_an_unknown_provider_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, "sandbox")

    with pytest.raises(ConfigError) as refused:
        await _build(Script())

    assert str(refused.value) == (
        "SALES_KOMMO_PROVIDER=«sandbox» — такого клиента Kommo нет. Известные: fixture, live"
    )


SETTINGS = (
    ("SALES_KOMMO_PROVIDER", "kommo_provider", "fixture", "live"),
    ("SALES_KOMMO_SUBDOMAIN", "kommo_subdomain", "", "acme-test"),
    ("SALES_KOMMO_TOKEN", "kommo_token", "", TOKEN),
    ("SALES_KOMMO_PIPELINE_ID", "kommo_pipeline_id", "", "4417"),
    ("SALES_KOMMO_STATUS_ID", "kommo_status_id", "", "61953"),
    ("SALES_KOMMO_RESPONSIBLE_USER_ID", "kommo_responsible_user_id", "", "88231"),
)


def test_kommo_settings_default_to_the_fixture_and_are_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Урок L4 соседнего проекта: поле читается только по алиасу — оба значения."""
    for env, *_ in SETTINGS:
        monkeypatch.delenv(env, raising=False)
    empty = sales_cfg._Sales(_env_file=None)
    assert [getattr(empty, field) for _, field, _, _ in SETTINGS] == [d for *_, d, _ in SETTINGS]

    for env, _, _, value in SETTINGS:
        monkeypatch.setenv(env, value)
    filled = sales_cfg._Sales(_env_file=None)
    assert [getattr(filled, field) for _, field, _, _ in SETTINGS] == [v for *_, v in SETTINGS]


def test_the_suite_runs_on_the_fixture_without_a_token(request: pytest.FixtureRequest) -> None:
    """Ключ из `.env` разработчика не доезжает ни до одного теста: запись в Kommo — побочный
    эффект в чужой CRM. Держит автофикстура `tests/conftest.py::_no_real_kommo`."""
    assert "_no_real_kommo" in request.fixturenames
    assert (sales_cfg.KOMMO_PROVIDER, sales_cfg.KOMMO_TOKEN) == ("fixture", "")


def test_unavailable_is_temporary_and_the_rest_are_not() -> None:
    assert not is_permanent(KommoUnavailableError("Kommo не ответил (проверка)"))
    assert is_permanent(KommoFormatError("форма (проверка)"))
    assert is_permanent(KommoAuthError("ключ (проверка)"))
