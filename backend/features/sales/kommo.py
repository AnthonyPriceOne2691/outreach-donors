"""Kommo — CRM, куда уходит лид продаж: сделка, контакт и компания.

**Две реализации, выбирает `SALES_KOMMO_PROVIDER`.** `fixture` — без сети:
сделки с выдуманными номерами, всё созданное запоминается — на нём живут
разработка и тесты передачи лида. `live` — закрытая интеграция аккаунта
с долгоживущим ключом (`kommo_live.py`). Умолчание — `fixture`; на `live`
переключает человек: сделка в чужой CRM не отзывается. `live` без поддомена,
ключа, воронки, этапа или ответственного — `ConfigError` при сборке клиента,
до первого запроса (A5).

Отсюда же берут всё остальное: словарь (`kommo_types.py`) и живой клиент
переэкспортированы, чтобы передача лида знала один модуль.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import httpx

from backend.config import sales as cfg
from backend.config.startup_checks import ConfigError
from backend.features.sales.kommo_live import CHANNEL_TAG, KommoLive, Pace
from backend.features.sales.kommo_types import (
    FIXTURE,
    KNOWN,
    LIVE,
    CreatedLead,
    KommoAccount,
    KommoAuthError,
    KommoContact,
    KommoError,
    KommoFormatError,
    KommoRefusedError,
    KommoUnavailableError,
    KommoUnconfirmedError,
    NewLead,
    lead_url,
    wanted_email,
)

__all__ = [
    "CHANNEL_TAG",
    "FIXTURE",
    "KNOWN",
    "LIVE",
    "CreatedLead",
    "FixtureLead",
    "KommoAccount",
    "KommoAuthError",
    "KommoClient",
    "KommoContact",
    "KommoError",
    "KommoFixture",
    "KommoFormatError",
    "KommoLive",
    "KommoRefusedError",
    "KommoUnavailableError",
    "KommoUnconfirmedError",
    "NewLead",
    "Pace",
    "build_kommo",
    "lead_url",
]

logger = logging.getLogger(__name__)


@runtime_checkable
class KommoClient(Protocol):
    """CRM для лидов. За интерфейсом, чтобы передача лида не знала, живая ли она."""

    name: str

    async def find_contact(self, email: str) -> KommoContact | None:
        """Контакт с ровно этой почтой; `None` — такого нет."""
        ...

    async def create_complex_lead(self, lead: NewLead) -> CreatedLead:
        """Сделка с контактом и компанией; контакт с той же почтой не дублируется."""
        ...

    async def add_note(self, lead_id: int, text: str) -> int:
        """Примечание к сделке; возвращает его номер."""
        ...

    def lead_url(self, lead_id: int) -> str:
        """Ссылка на сделку для человека."""
        ...


@dataclass(slots=True)
class FixtureLead:
    """Сделка, «заведённая» fixture: что просили и какие примечания легли."""

    lead: CreatedLead
    draft: NewLead
    notes: dict[int, str] = field(default_factory=dict)


class KommoFixture:
    """Kommo без сети. Номера выдуманные, некруглые и повторяются: те же вызовы —
    те же номера. Созданное лежит в `leads` и `contacts` — по ним тесты передачи
    лида видят, что ушло бы в CRM."""

    name = FIXTURE
    SUBDOMAIN = "fixture"
    FIRST_LEAD = 9301
    FIRST_CONTACT = 5711
    FIRST_COMPANY = 7419
    FIRST_NOTE = 8263

    def __init__(self) -> None:
        self.leads: dict[int, FixtureLead] = {}
        self.contacts: dict[str, KommoContact] = {}
        self._notes = 0

    def seed_contact(self, email: str, name: str = "") -> KommoContact:
        """Контакт, который «уже есть в CRM» (A1); повтор отдаёт тот же."""
        address = wanted_email(email)
        if (known := self.contacts.get(address)) is None:
            known = KommoContact(self.FIRST_CONTACT + len(self.contacts), name)
            self.contacts[address] = known
        return known

    async def find_contact(self, email: str) -> KommoContact | None:
        return self.contacts.get(wanted_email(email))

    async def create_complex_lead(self, lead: NewLead) -> CreatedLead:
        found = await self.find_contact(lead.email)
        contact = found or self.seed_contact(lead.email, lead.name.strip())
        number = self.FIRST_LEAD + len(self.leads)
        created = CreatedLead(
            id=number,
            url=self.lead_url(number),
            contact_id=contact.id,
            company_id=self.FIRST_COMPANY + len(self.leads),
            contact_found=found is not None,
        )
        self.leads[number] = FixtureLead(created, lead)
        return created

    async def add_note(self, lead_id: int, text: str) -> int:
        record = self.leads.get(lead_id)
        if record is None:
            raise KommoRefusedError(f"сделки №{lead_id} нет в fixture — примечание некуда положить")
        number = self.FIRST_NOTE + self._notes
        self._notes += 1
        record.notes[number] = text
        return number

    def lead_url(self, lead_id: int) -> str:
        return lead_url(self.SUBDOMAIN, lead_id)


#: Поддомен аккаунта: буквы, цифры и дефис — без схемы и без `.kommo.com`.
_SUBDOMAIN = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")


def build_kommo(http: httpx.AsyncClient) -> KommoClient:
    """Клиент по настройке. Отказ — при сборке, до первого запроса (A5)."""
    name = (cfg.KOMMO_PROVIDER or "").strip().lower()
    if name == FIXTURE:
        logger.warning("продажи: Kommo — fixture, сделки выдуманные: в CRM ничего не уходит")
        return KommoFixture()
    if name == LIVE:
        account = _live_account()
        logger.info(
            "продажи: Kommo — live",
            extra={"subdomain": account.subdomain, "pipeline_id": account.pipeline_id},
        )
        return KommoLive(http, account)
    raise ConfigError(
        f"SALES_KOMMO_PROVIDER=«{cfg.KOMMO_PROVIDER}» — такого клиента Kommo нет. "
        f"Известные: {', '.join(KNOWN)}"
    )


def _live_account() -> KommoAccount:
    """Учётка `live` из настроек. Чего-то нет или оно негодно — `ConfigError`
    словами; значение ключа не печатается никогда."""
    given = {
        "SALES_KOMMO_SUBDOMAIN": cfg.KOMMO_SUBDOMAIN,
        "SALES_KOMMO_TOKEN": cfg.KOMMO_TOKEN,
        "SALES_KOMMO_PIPELINE_ID": cfg.KOMMO_PIPELINE_ID,
        "SALES_KOMMO_STATUS_ID": cfg.KOMMO_STATUS_ID,
        "SALES_KOMMO_RESPONSIBLE_USER_ID": cfg.KOMMO_RESPONSIBLE_USER_ID,
    }
    if missing := [env for env, value in given.items() if not value]:
        raise ConfigError(
            f"SALES_KOMMO_PROVIDER=live, а не заданы: {', '.join(missing)}. "
            "Заполнить их или вернуть fixture — без них сделки в Kommo не заводятся"
        )
    if not _SUBDOMAIN.fullmatch(cfg.KOMMO_SUBDOMAIN):
        raise ConfigError(
            f"SALES_KOMMO_SUBDOMAIN=«{cfg.KOMMO_SUBDOMAIN}» — нужен только поддомен: "
            "acme из acme.kommo.com"
        )
    token = cfg.KOMMO_TOKEN
    if not (token.isascii() and token.isprintable() and " " not in token):
        # Заголовок — только ASCII без пробелов и переводов строки. Иначе httpx
        # роняет LocalProtocolError или UnicodeEncodeError с ключом в тексте.
        raise ConfigError(
            "SALES_KOMMO_TOKEN с пробелом, переводом строки или знаком вне латиницы — "
            "заголовок с ним не собрать; скопировать ключ закрытой интеграции заново"
        )
    return KommoAccount(
        subdomain=cfg.KOMMO_SUBDOMAIN,
        token=token,
        pipeline_id=_setting_number("SALES_KOMMO_PIPELINE_ID", cfg.KOMMO_PIPELINE_ID),
        status_id=_setting_number("SALES_KOMMO_STATUS_ID", cfg.KOMMO_STATUS_ID),
        responsible_user_id=_setting_number(
            "SALES_KOMMO_RESPONSIBLE_USER_ID", cfg.KOMMO_RESPONSIBLE_USER_ID
        ),
    )


def _setting_number(env: str, raw: str) -> int:
    number = int(raw) if raw.isdecimal() else 0
    if number <= 0:
        raise ConfigError(f"{env}=«{raw}» — нужен номер из Kommo: целое больше нуля")
    return number
