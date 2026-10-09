"""Kommo через шлюз агентства: одна операция — сделка с контактом и первым примечанием.

**Шлюз — способ передачи внутри `live`, а не другой провайдер.** Вместо доступа к API v4
агентство даёт свой адрес: `POST` с ключом в заголовке; в теле воронка и этап, название
сделки, источник, тег, примечание, сайт и контакт; в ответе — номера сделки и контакта.
Других методов у шлюза нет, и передача лида знает это по `KommoAbilities`, а не по имени
клиента: первое примечание едет в самой сделке (`NewLead.note`), контакт не ищется —
склеивает ли шлюз контакты, его дело, — а примечание к уже заведённой сделке не пишется:
новый ответ лида уходит телемаркетологу сообщением со ссылкой на ту же сделку
(`handoff_kommo.py`). Когда агентство даст метод примечаний, его вызов встанет в `add_note`.

**Тело — только то, что знаем.** Почта контакта — всегда, имя — если есть, телефона нет
вовсе: не выдумываем. Источник и тег сделки и контакта — из настроек
(`SALES_KOMMO_SOURCE`, `SALES_KOMMO_TAG`), воронка и этап — числами, сайт — адресом
`https://…`, как в образце шлюза.

**Запрос — один на вызов, в темпе прямого пути** (`kommo_wire.Pace`); повтор — проходом
передачи по расписанию, а не внутри запроса: поиска, которым прямой путь проверяет
дошедшую запись, у шлюза нет, и лишняя попытка — лишний шанс завести вторую сделку.
- `success: true` с номером сделки — сделка; `warnings` шлюза — полем журнала.
- 429, обрыв до отправки — `KommoUnavailableError` (пауза `Retry-After`, если шлюз её
  назвал): повторит проход.
- 5xx — запрос дошёл, и шлюз мог завести сделку до своего сбоя: повтора нет, решает человек
  (`unconfirmed`), пока агентство не подтвердит, что 5xx значит «ничего не создано», или не
  примет ключ от дублей. Дубль сделки в чужой CRM не отзывается, а лидов — единицы в день.
- Ушло, а ответ потерян, или ответ без номера сделки — сделка могла создаться: повтора
  нет, решает человек (`unconfirmed`).
- 400 и 422, `success: false` — отказ словами шлюза; 401 и 403 — отказ ключа
  (`KommoAuthError`); прочее — отказ с адресом шлюза к проверке. Повтор не поможет.

**Ключа нет ни в журнале, ни в словах, ни в `repr`.** Он едет заголовком; обрыв связи —
своими словами с типом ошибки httpx, без её текста и без адреса (`kommo_wire.unreached`);
что шлюз вернул в теле, проходит без ключа, даже если шлюз его повторил.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from backend.config import sales as cfg
from backend.features.sales.kommo_types import (
    LIVE,
    CreatedLead,
    KommoAbilities,
    KommoAuthError,
    KommoContact,
    KommoFormatError,
    KommoRefusedError,
    KommoUnconfirmedError,
    NewLead,
    lead_url,
    wanted_email,
)
from backend.features.sales.kommo_wire import (
    Pace,
    Peer,
    body_of,
    glimpse,
    number_of,
    temporary,
    unreached,
)

logger = logging.getLogger(__name__)

#: Что шлюз умеет: только создание сделки, первое примечание — в ней же.
GATEWAY = KommoAbilities(search=False, add_notes=False, note_in_lead=True)

#: Как шлюз называется в словах обрыва связи.
PEER = Peer(
    who="шлюз Kommo",
    whose="шлюза Kommo",
    to="шлюзу Kommo",
    settings="SALES_KOMMO_GATEWAY_KEY и SALES_KOMMO_GATEWAY_URL",
)

#: Чем заменяется ключ в том, что шлюз вернул в теле.
HIDDEN_KEY = "<ключ шлюза>"

#: Сколько предупреждений шлюза и знаков каждого уходит в журнал.
WARNINGS_KEPT = 10
TEXT_KEPT = 300

_KEY = (
    "ключ шлюза Kommo отклонён (HTTP {code}{said}) — ключ отозван или неверен: проверить "
    "SALES_KOMMO_GATEWAY_KEY; повтор не поможет"
)
_REFUSED = (
    "шлюз Kommo отверг сделку (HTTP {code}{said}) — повтор не поможет: проверить воронку, "
    "этап, источник, тег и поля лида"
)
_NOT_MADE = (
    "шлюз Kommo не завёл сделку (HTTP {code}, success: false{said}) — повтор не поможет: "
    "проверить воронку, этап, источник, тег и поля лида"
)
_SERVER = (
    "шлюз Kommo ответил HTTP {code}{said} уже после отправки — сделка могла создаться: "
    "проверить в Kommo руками; повтор вслепую завёл бы вторую"
)
_ELSE = (
    "шлюз Kommo ответил HTTP {code}{said} — повтор не поможет: проверить адрес "
    "SALES_KOMMO_GATEWAY_URL"
)


@dataclass(frozen=True, slots=True)
class GatewayAccount:
    """Учётка шлюза. Ни адрес, ни ключ не печатаются: `repr` уходит в журналы и трассировки.
    Поддомен — для ссылки на сделку телемаркетологу: её шлюз не возвращает."""

    url: str = field(repr=False)
    key: str = field(repr=False)
    subdomain: str
    pipeline_id: int
    status_id: int
    source: str
    tag: str


class KommoGateway:
    """Kommo через шлюз агентства (шапка модуля). Разбор ответа защитный, как у прямого
    пути: чужой формат однажды поменяется, и молчать об этом нельзя."""

    name = LIVE
    can = GATEWAY

    def __init__(
        self, http: httpx.AsyncClient, account: GatewayAccount, *, pace: Pace | None = None
    ) -> None:
        self._http = http
        self._account = account
        # Ключ — заголовком: в адресе он утекал бы в журналы прокси и в текст ошибок.
        self._headers = {"Authorization": f"Bearer {account.key}", "Accept": "application/json"}
        self._pace = pace or Pace(cfg.KOMMO_RATE_PER_SEC)

    def lead_url(self, lead_id: int) -> str:
        return lead_url(self._account.subdomain, lead_id)

    async def find_contact(self, email: str) -> KommoContact | None:
        """Поиска у шлюза нет — «не найден» без запроса (`GATEWAY.search`)."""
        wanted_email(email)
        return None

    async def create_complex_lead(self, lead: NewLead) -> CreatedLead:
        wanted_email(lead.email)
        await self._pace.wait()
        try:
            response = await self._http.post(
                self._account.url,
                json=self._body(lead),
                headers=self._headers,
                timeout=cfg.KOMMO_TIMEOUT_SEC,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise unreached(exc, "POST", PEER) from None
        self._judge(response)
        return self._created(response)

    async def add_note(self, lead_id: int, text: str) -> int:
        """Метода нет (`GATEWAY.add_notes`): передача сюда не зовёт, а позвавший — отказ."""
        raise KommoRefusedError(
            f"примечание к сделке №{lead_id} ({len(text)} знаков) шлюз Kommo не принимает — "
            "метода нет: новый ответ лида уходит телемаркетологу сообщением"
        )

    def _body(self, lead: NewLead) -> dict[str, Any]:
        """Тело запроса шлюза: только то, что о лиде известно (шапка модуля)."""
        contact: dict[str, Any] = {"email": lead.email.strip()}
        if name := lead.name.strip():
            contact["name"] = name
        contact["tag"] = self._account.tag
        body: dict[str, Any] = {
            "pipeline_id": self._account.pipeline_id,
            "status_id": self._account.status_id,
            "lead_name": lead.title,
            "source": self._account.source,
            "lead_tag": self._account.tag,
            "contact": contact,
        }
        if site := lead.site.strip():
            body["site"] = site if site.startswith(("https://", "http://")) else f"https://{site}"
        if note := lead.note.strip():
            body["note"] = note
        return body

    def _judge(self, response: httpx.Response) -> None:
        """Код ответа → исход. 2xx проходит к разбору тела; остальное — исключение словами."""
        code = response.status_code
        if httpx.codes.is_success(code):
            return
        if httpx.codes.is_server_error(code):
            raise KommoUnconfirmedError(_SERVER.format(code=code, said=self._said(response)))
        if (busy := temporary(response, PEER)) is not None:
            raise busy
        said = self._said(response)
        if code in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
            raise KommoAuthError(_KEY.format(code=code, said=said))
        refused = (httpx.codes.BAD_REQUEST, httpx.codes.UNPROCESSABLE_ENTITY)
        words = _REFUSED if code in refused else _ELSE
        raise KommoRefusedError(words.format(code=code, said=said))

    def _created(self, response: httpx.Response) -> CreatedLead:
        """Сделка из ответа 2xx. `success: false` — отказ; ответ без `success: true` и
        номера сделки — не «успех без ссылки», а сделка, которая могла создаться: громко
        и без повтора, повтор вслепую завёл бы вторую."""
        code, body = response.status_code, body_of(response)
        if isinstance(body, dict) and body.get("success") is False:
            raise KommoRefusedError(_NOT_MADE.format(code=code, said=self._said(response)))
        done = isinstance(body, dict) and body.get("success") is True
        number = number_of(body.get("lead_id")) if done else None
        if number is None:
            raise KommoFormatError(
                f"шлюз Kommo ответил HTTP {code} без «success: true» и номера сделки: "
                f"{glimpse(self._hide(response.text))} — сделка могла создаться, но ссылку "
                "не собрать; проверить в Kommo руками: повтор вслепую завёл бы вторую"
            )
        contact = number_of(body.get("contact_id"))
        warnings = self._warnings(body.get("warnings"))
        extra = {"lead_id": number, "contact_id": contact, "warnings": warnings}
        if warnings:
            logger.warning("kommo: шлюз завёл сделку с предупреждениями", extra=extra)
        else:
            logger.info("kommo: шлюз завёл сделку", extra=extra)
        return CreatedLead(
            id=number,
            url=self.lead_url(number),
            contact_id=contact,
            company_id=None,
            contact_found=False,
        )

    def _said(self, response: httpx.Response) -> str:
        """Что сказал шлюз об отказе — для человека, без ключа и не длиннее `TEXT_KEPT`."""
        body = body_of(response)
        if not isinstance(body, dict):
            return ""
        parts = [_text(body.get(key)) for key in ("error", "message", "detail", "errors")]
        text = self._hide("; ".join(part for part in parts if part))
        return f": {text[:TEXT_KEPT]}" if text else ""

    def _warnings(self, value: object) -> list[str]:
        """Предупреждения шлюза — строками для журнала, без ключа и не больше `WARNINGS_KEPT`."""
        items = value if isinstance(value, list) else [value] if value else []
        return [self._hide(_text(item))[:TEXT_KEPT] for item in items[:WARNINGS_KEPT]]

    def _hide(self, text: str) -> str:
        """Текст из тела ответа без ключа: шлюз чужой и мог повторить заголовок запроса."""
        return text.replace(self._account.key, HIDDEN_KEY) if self._account.key else text


def _text(value: object) -> str:
    """Значение из тела шлюза — строкой: строка как есть, остальное — JSON."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)
