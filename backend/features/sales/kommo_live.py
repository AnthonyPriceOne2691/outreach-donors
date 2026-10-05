"""Kommo API v4 по сети: запросы, частота и разбор ответов.

**Второго контакта не заводим (A1).** Перед сделкой контакт ищется по почте
(`GET /contacts?query=`). Поиск Kommo нечёткий — находит и «похожие» адреса, —
поэтому тем же человеком считается только точное совпадение адреса; найденный
привязывается к сделке номером, новый заводится с именем и рабочей почтой.

**Сделка — одним запросом** (`POST /leads/complex`): сделка, контакт и компания.
Ответ без номера сделки — не «успех без ссылки», а сделка, которая могла
создаться: громкий `KommoFormatError`, без повтора (A4).

**Частота — не больше `KOMMO_RATE_PER_SEC` запросов в секунду** (`Pace`): Kommo
считает их по IP, на превышение отвечает 429, а на частые 429 закрывает доступ (403).

**Ключа нет ни в адресе, ни в тексте ошибок, ни в журнале.** Он едет заголовком.
Текст ошибок httpx несёт адрес запроса, а `LocalProtocolError` — значение
заголовка целиком, то есть ключ; поэтому наружу идут свои исключения с одним
типом ошибки и `from None` (урок вебхука лидов, #160).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from backend.config import sales as cfg
from backend.features.sales.kommo_types import (
    DOMAIN,
    LIVE,
    CreatedLead,
    KommoAccount,
    KommoAuthError,
    KommoContact,
    KommoError,
    KommoFormatError,
    KommoRefusedError,
    KommoUnavailableError,
    NewLead,
    lead_url,
    wanted_email,
)

logger = logging.getLogger(__name__)

#: Тег канала у каждой сделки: по нему в CRM видно, что лид пришёл из рассылки.
CHANNEL_TAG = "Email рассылка"


class Pace:
    """Не чаще одного запроса в 1/N секунды — значит, не больше N в любую секунду.

    Ровный шаг, а не окно: у передачи лида запросов два-три, пачка разом не
    нужна, а шаг держит частоту и на повторах. Счёт — в памяти клиента: Kommo
    считает по IP, но лидов единицы в день, и общий счётчик на процесс не
    окупил бы своей сложности.
    """

    def __init__(
        self,
        per_second: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._gap = 1.0 / per_second
        self._clock = clock
        self._sleep = sleep
        self._next = float("-inf")

    async def wait(self) -> None:
        now = self._clock()
        start = max(now, self._next)
        # Место занимается до сна: запрос, пришедший, пока предыдущий ждёт,
        # встаёт за ним, а не рядом.
        self._next = start + self._gap
        if start > now:
            await self._sleep(start - now)


class KommoLive:
    """Kommo закрытой интеграцией. Разбор ответа защитный: чужой формат однажды
    поменяется, и молчать об этом нельзя."""

    name = LIVE

    def __init__(
        self, http: httpx.AsyncClient, account: KommoAccount, *, pace: Pace | None = None
    ) -> None:
        self._http = http
        self._account = account
        self._api = f"https://{account.subdomain}.{DOMAIN}/api/v4"
        # Ключ — заголовком: в адресе он утекал бы в журналы прокси и в текст ошибок.
        self._headers = {"Authorization": f"Bearer {account.token}", "Accept": "application/json"}
        self._pace = pace or Pace(cfg.KOMMO_RATE_PER_SEC)

    def lead_url(self, lead_id: int) -> str:
        return lead_url(self._account.subdomain, lead_id)

    async def find_contact(self, email: str) -> KommoContact | None:
        wanted = wanted_email(email)
        response = await self._send("GET", "/contacts", params={"query": wanted})
        if response.status_code == httpx.codes.NO_CONTENT:
            return None  # так Kommo отвечает на пустой поиск
        contacts = _embedded(_body(response), "contacts")
        if contacts is None:
            raise KommoFormatError(
                f"Kommo ответил HTTP {response.status_code} без списка контактов: "
                f"{_glimpse(response)} — формат поменялся, читать лог, не гадать"
            )
        return _same_email(contacts, wanted)

    async def create_complex_lead(self, lead: NewLead) -> CreatedLead:
        found = await self.find_contact(lead.email)
        response = await self._send("POST", "/leads/complex", json=[self._complex(lead, found)])
        number, contact_id, company_id = _created(response)
        return CreatedLead(
            id=number,
            url=self.lead_url(number),
            contact_id=contact_id,
            company_id=company_id,
            contact_found=found is not None,
        )

    async def add_note(self, lead_id: int, text: str) -> int:
        note = {"note_type": "common", "params": {"text": text}}
        response = await self._send("POST", f"/leads/{lead_id}/notes", json=[note])
        return _note_number(response)

    def _complex(self, lead: NewLead, found: KommoContact | None) -> dict[str, Any]:
        """Сделка одним запросом: контакт — найденный, по номеру, или новый."""
        tags = [{"name": tag} for tag in (CHANNEL_TAG, lead.hypothesis.strip()) if tag]
        return {
            "name": lead.title,
            "pipeline_id": self._account.pipeline_id,
            "status_id": self._account.status_id,
            "responsible_user_id": self._account.responsible_user_id,
            "_embedded": {
                "tags": tags,
                "contacts": [{"id": found.id} if found else _person(lead)],
                "companies": [_company(lead)],
            },
        }

    async def _send(self, method: str, path: str, **request: Any) -> httpx.Response:
        """Один запрос. Ответ 2xx — дальше, остальное — исключение словами."""
        await self._pace.wait()
        try:
            response = await self._http.request(
                method,
                f"{self._api}{path}",
                headers=self._headers,
                timeout=cfg.KOMMO_TIMEOUT_SEC,
                **request,
            )
        except httpx.HTTPError as exc:
            raise _unreached(exc) from None
        _judge(response)
        return response


def _person(lead: NewLead) -> dict[str, Any]:
    email = [{"value": lead.email.strip(), "enum_code": "WORK"}]
    person: dict[str, Any] = {"custom_fields_values": [{"field_code": "EMAIL", "values": email}]}
    if name := lead.name.strip():
        person["name"] = name
    return person


def _company(lead: NewLead) -> dict[str, Any]:
    company: dict[str, Any] = {"name": lead.company_name}
    if site := lead.site.strip():
        company["custom_fields_values"] = [{"field_code": "WEB", "values": [{"value": site}]}]
    return company


def _unreached(exc: httpx.HTTPError) -> KommoError:
    """Ответа нет. Наружу — только тип ошибки: в тексте httpx адрес запроса,
    а у `LocalProtocolError` — значение заголовка, то есть ключ."""
    kind = type(exc).__name__
    if isinstance(exc, httpx.LocalProtocolError):
        return KommoRefusedError(
            f"запрос к Kommo не собран ({kind}) — проверить SALES_KOMMO_TOKEN "
            "и SALES_KOMMO_SUBDOMAIN; повтор не поможет"
        )
    return KommoUnavailableError(
        f"Kommo не ответил: связь оборвалась ({kind}) — в CRM ничего не записано, повторим позже"
    )


#: Отказы со своими словами: что случилось и что делать человеку.
_REFUSALS: dict[int, tuple[type[KommoRefusedError], str]] = {
    401: (
        KommoAuthError,
        "ключ Kommo отклонён (HTTP 401{said}) — ключ закрытой интеграции отозван или истёк: "
        "выпустить новый и записать в SALES_KOMMO_TOKEN; повтор не поможет",
    ),
    402: (
        KommoRefusedError,
        "подписка Kommo не оплачена (HTTP 402{said}) — сделки не заводятся до оплаты; "
        "повтор не поможет",
    ),
    403: (
        KommoRefusedError,
        "Kommo закрыл доступ (HTTP 403{said}) — у ключа нет прав или IP заблокирован "
        "за частые запросы; повтор не поможет",
    ),
}
_REFUSED = (
    "Kommo отверг запрос (HTTP {code}{said}) — повтор не поможет: проверить поддомен, "
    "воронку, этап, ответственного и обязательные поля"
)


def _judge(response: httpx.Response) -> None:
    """Код ответа → исход. 2xx проходит; остальное — исключение со словами Kommo."""
    code = response.status_code
    if httpx.codes.is_success(code):
        return
    if code == httpx.codes.TOO_MANY_REQUESTS or httpx.codes.is_server_error(code):
        asked = _asked_wait(response)
        wait = f", просит подождать {asked:g} с" if asked is not None else ""
        raise KommoUnavailableError(
            f"Kommo не принял запрос (HTTP {code}{wait}) — повторим позже", retry_after=asked
        )
    kind, words = _REFUSALS.get(code, (KommoRefusedError, _REFUSED))
    raise kind(words.format(code=code, said=_said(response)))


def _asked_wait(response: httpx.Response) -> float | None:
    """Сколько просит подождать Kommo: заголовок `Retry-After` в секундах или поле
    `retry_after` тела — свою паузу на 429 Kommo кладёт в тело."""
    header = (response.headers.get("Retry-After") or "").strip()
    if header.isdecimal():
        return float(header)
    body = _body(response)
    value = body.get("retry_after") if isinstance(body, dict) else None
    if isinstance(value, int | float) and not isinstance(value, bool) and value >= 0:
        return float(value)
    return None


def _said(response: httpx.Response) -> str:
    """Что сказал Kommo об отказе: `detail` и ошибки проверки полей — для человека."""
    body = _body(response)
    if not isinstance(body, dict):
        return ""
    parts = [str(body.get("detail") or body.get("title") or ""), *_invalid_fields(body)]
    text = "; ".join(part for part in parts if part)
    return f": {text[:300]}" if text else ""


def _invalid_fields(body: dict[str, Any]) -> list[str]:
    """`validation-errors` → «поле: что не так» по каждому полю."""
    found: list[str] = []
    for group in body.get("validation-errors") or []:
        errors = group.get("errors") if isinstance(group, dict) else None
        for error in errors if isinstance(errors, list) else []:
            if isinstance(error, dict):
                found.append(f"{error.get('path')}: {error.get('detail')}")
    return found


def _body(response: httpx.Response) -> Any:
    """Тело как JSON; не JSON — `None`, судит вызывающий по коду и форме."""
    try:
        return response.json()
    except ValueError:
        # Не JSON — исход, а не потеря: вызывающий назовёт его словами.
        logger.debug("kommo: ответ не JSON", extra={"status": response.status_code})
        return None


def _glimpse(response: httpx.Response) -> str:
    return repr(response.text[:120])


def _number_of(value: object) -> int | None:
    """Номер сущности Kommo: целое больше нуля. `True` — тоже int, но не номер."""
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _embedded(body: Any, key: str) -> list[dict[str, Any]] | None:
    """`_embedded.<key>` — список объектов; другая форма — `None`."""
    embedded = body.get("_embedded") if isinstance(body, dict) else None
    items = embedded.get(key) if isinstance(embedded, dict) else None
    if isinstance(items, list) and all(isinstance(item, dict) for item in items):
        return items
    return None


def _emails(contact: dict[str, Any]) -> set[str]:
    """Адреса контакта из поля EMAIL. У контакта без полей Kommo шлёт `null`."""
    fields = contact.get("custom_fields_values")
    found: set[str] = set()
    for item in fields if isinstance(fields, list) else []:
        email = isinstance(item, dict) and item.get("field_code") == "EMAIL"
        values = item.get("values") if email else None
        for value in values if isinstance(values, list) else []:
            if isinstance(value, dict):
                found.add(str(value.get("value") or "").strip().lower())
    return found


def _same_email(contacts: list[dict[str, Any]], wanted: str) -> KommoContact | None:
    """Контакт с ровно этой почтой. Поиск Kommo нечёткий: «похожий» адрес — другой
    человек. Таких несколько — старший (меньший номер), и строка об этом в журнал."""
    same = [_contact_of(item) for item in contacts if wanted in _emails(item)]
    if len(same) > 1:
        logger.warning(
            "kommo: несколько контактов с одной почтой — сделка к старшему",
            extra={"contacts": [contact.id for contact in same]},
        )
    return min(same, key=lambda contact: contact.id) if same else None


def _contact_of(item: dict[str, Any]) -> KommoContact:
    number = _number_of(item.get("id"))
    if number is None:
        raise KommoFormatError(
            f"у контакта в ответе Kommo нет номера: {str(item)[:120]!r} — формат поменялся, "
            "читать лог, не гадать"
        )
    return KommoContact(number, str(item.get("name") or ""))


def _created(response: httpx.Response) -> tuple[int, int | None, int | None]:
    """Номера сделки, контакта и компании из ответа `/leads/complex`.

    Ответ без номера сделки — не «успех без ссылки»: сделка могла создаться,
    а ссылки для человека нет. Громко и без повтора: повтор вслепую завёл бы
    вторую (A4)."""
    body = _body(response)
    rows = body if isinstance(body, list) and len(body) == 1 else [None]
    row = rows[0] if isinstance(rows[0], dict) else {}
    number = _number_of(row.get("id"))
    if number is None:
        raise KommoFormatError(
            f"Kommo ответил HTTP {response.status_code} без номера сделки: {_glimpse(response)} — "
            "сделка могла создаться, но ссылку не собрать; проверить в Kommo руками: "
            "повтор вслепую завёл бы вторую"
        )
    return number, _number_of(row.get("contact_id")), _number_of(row.get("company_id"))


def _note_number(response: httpx.Response) -> int:
    notes = _embedded(_body(response), "notes") or []
    number = _number_of(notes[0].get("id")) if len(notes) == 1 else None
    if number is None:
        raise KommoFormatError(
            f"Kommo ответил HTTP {response.status_code} без номера примечания: "
            f"{_glimpse(response)} — примечание могло записаться; проверить в Kommo руками"
        )
    return number
