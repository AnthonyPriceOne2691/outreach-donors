"""Kommo API v4 по сети: запросы, частота и разбор ответов.

**Второго контакта не заводим (A1).** Перед сделкой контакт ищется по почте
(`GET /contacts?query=`). Поиск Kommo нечёткий — находит и «похожие» адреса, —
поэтому тем же человеком считается только точное совпадение адреса; найденный
привязывается к сделке номером, новый заводится с именем и рабочей почтой.

**Сделка — одним запросом** (`POST /leads/complex`): сделка, контакт и компания.
Ответ без номера сделки — не «успех без ссылки», а сделка, которая могла
создаться: громкий `KommoFormatError`, без повтора (A4).

**Частота — не больше `KOMMO_RATE_PER_SEC` запросов в секунду** (`Pace`, общий со
шлюзом — `kommo_wire.py`): Kommo считает их по IP, на превышение отвечает 429, а на
частые 429 закрывает доступ (403).

**Повторы — своим циклом поверх общих `delay_for` и `RETRY_STATUSES`**
(`shared/net/retry.py`), а не общим `with_retries`, по трём причинам Kommo:
паузу 429 он кладёт в тело (`retry_after`), а общий читает только заголовок;
повтор внутри окна запрета у Kommo кончается блокировкой IP — поэтому просьба
ждать дольше потолка `MAX_DELAY_SEC` даёт отказ с названной величиной, а не сон
внутри запроса; и запись не повторяется, если она могла дойти: ключа
идемпотентности у Kommo нет. Повторяем таймаут, обрыв, 429 и 5xx; запись —
только когда соединения не было или Kommo ответил 429/5xx; ушла и ответ
потерян — `KommoUnconfirmedError` без повтора (урок отправки писем).

**Ключа нет ни в адресе, ни в тексте ошибок, ни в журнале.** Он едет заголовком,
а обрыв связи называется своими словами с одним типом ошибки httpx (`kommo_wire.unreached`).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from backend.config import sales as cfg
from backend.features.sales.kommo_types import (
    DOMAIN,
    FULL,
    LIVE,
    CreatedLead,
    KommoAccount,
    KommoAuthError,
    KommoContact,
    KommoFormatError,
    KommoRefusedError,
    NewLead,
    lead_url,
    wanted_email,
)
from backend.features.sales.kommo_wire import (
    NOT_SENT,
    Pace,
    Peer,
    asked_wait,
    body_of,
    glimpse,
    number_of,
    temporary,
    unreached,
)
from backend.shared.net.retry import MAX_DELAY_SEC, RETRY_STATUSES, delay_for

logger = logging.getLogger(__name__)

#: Тег канала у каждой сделки: по нему в CRM видно, что лид пришёл из рассылки.
CHANNEL_TAG = "Email рассылка"

#: Сколько раз пробуем один запрос: перегрузка и обрыв проходят сами, а лид ждёт.
ATTEMPTS = 3

#: Пауза между попытками — отдельным именем, чтобы тест гасил её здесь,
#: а не `asyncio.sleep` всего процесса (как `shared/net/retry._sleep`).
_sleep = asyncio.sleep

#: Как прямой путь называется в словах обрыва связи.
KOMMO = Peer(
    who="Kommo", whose="Kommo", to="Kommo", settings="SALES_KOMMO_TOKEN и SALES_KOMMO_SUBDOMAIN"
)


class KommoLive:
    """Kommo закрытой интеграцией. Разбор ответа защитный: чужой формат однажды
    поменяется, и молчать об этом нельзя."""

    name = LIVE
    can = FULL

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
        contacts = _embedded(body_of(response), "contacts")
        if contacts is None:
            raise KommoFormatError(
                f"Kommo ответил HTTP {response.status_code} без списка контактов: "
                f"{glimpse(response.text)} — формат поменялся, читать лог, не гадать"
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
        """Запрос с повторами (шапка модуля). Ответ 2xx — дальше, остальное —
        исключение словами; частота держится на каждой попытке."""
        attempt = 1
        while True:
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
                if attempt == ATTEMPTS or not _resendable(exc, method):
                    raise unreached(exc, method, KOMMO) from None
                pause, why = delay_for(attempt - 1), type(exc).__name__
            else:
                wait = None if attempt == ATTEMPTS else _pause(response, attempt - 1)
                if wait is None:
                    _judge(response)
                    return response
                pause, why = wait, f"HTTP {response.status_code}"
            logger.warning(
                "kommo: повтор запроса",
                extra={"path": path, "why": why, "pause_sec": round(pause, 2), "attempt": attempt},
            )
            await _sleep(pause)
            attempt += 1


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


def _resendable(exc: httpx.HTTPError, method: str) -> bool:
    """Повторять ли обрыв. Несобранный запрос — никогда: повтор соберёт его так же.
    Чтение — всегда. Запись — только если она точно не ушла."""
    if isinstance(exc, httpx.LocalProtocolError):
        return False
    return method == "GET" or isinstance(exc, NOT_SENT)


def _pause(response: httpx.Response, attempt: int) -> float | None:
    """Пауза перед повтором; `None` — не повторяем: код не временный или Kommo
    просит ждать дольше потолка (тогда отказ называет величину)."""
    if response.status_code not in RETRY_STATUSES:
        return None
    asked = asked_wait(response)
    if asked is None:
        return delay_for(attempt)
    return asked if asked <= MAX_DELAY_SEC else None


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
    if (busy := temporary(response, KOMMO)) is not None:
        raise busy
    kind, words = _REFUSALS.get(code, (KommoRefusedError, _REFUSED))
    raise kind(words.format(code=code, said=_said(response)))


def _said(response: httpx.Response) -> str:
    """Что сказал Kommo об отказе: `detail` и ошибки проверки полей — для человека."""
    body = body_of(response)
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
    number = number_of(item.get("id"))
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
    body = body_of(response)
    rows = body if isinstance(body, list) and len(body) == 1 else [None]
    row = rows[0] if isinstance(rows[0], dict) else {}
    number = number_of(row.get("id"))
    if number is None:
        raise KommoFormatError(
            f"Kommo ответил HTTP {response.status_code} без номера сделки: {glimpse(response.text)} — "
            "сделка могла создаться, но ссылку не собрать; проверить в Kommo руками: "
            "повтор вслепую завёл бы вторую"
        )
    return number, number_of(row.get("contact_id")), number_of(row.get("company_id"))


def _note_number(response: httpx.Response) -> int:
    notes = _embedded(body_of(response), "notes") or []
    number = number_of(notes[0].get("id")) if len(notes) == 1 else None
    if number is None:
        raise KommoFormatError(
            f"Kommo ответил HTTP {response.status_code} без номера примечания: "
            f"{glimpse(response.text)} — примечание могло записаться; проверить в Kommo руками"
        )
    return number
