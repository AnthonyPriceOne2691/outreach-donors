"""Словарь клиента Kommo: что уходит в CRM, что приходит обратно и как Kommo отказывает.

Общий для `fixture` и `live` (`kommo.py`, `kommo_live.py`): обе реализации
отвечают одними и теми же значениями и отказывают одними и теми же классами —
передача лида не должна знать, с какой из них говорит.

Классы отказа разделены по тому, что делать человеку (`okf/provider-refusals.md`):
временный — повторить позже; ключ, права, оплата, неверный запрос — чинить, повтор
не поможет; «мы не поняли ответ» — читать лог, не гадать. Признак `permanent`
читает общее правило повторов задач (`runs/failures.is_permanent`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

FIXTURE = "fixture"
LIVE = "live"
KNOWN = (FIXTURE, LIVE)

#: Домен Kommo: адрес API и ссылка на сделку строятся от поддомена аккаунта.
DOMAIN = "kommo.com"


class KommoError(RuntimeError):
    """Kommo не принял запрос. Текст — словами, без адреса запроса и без ключа."""

    permanent = False


class KommoUnavailableError(KommoError):
    """Сеть, 429 или 5xx — повтор позже поможет. `retry_after` — сколько секунд
    просил подождать сам Kommo, если сказал: раньше повторять незачем."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class KommoRefusedError(KommoError):
    """Ключ, права, оплата или неверный запрос: повтор не поможет, чинит человек."""

    permanent = True


class KommoAuthError(KommoRefusedError):
    """Ключ отклонён (401): отозван или истёк."""


class KommoFormatError(KommoError):
    """Kommo ответил, а мы не поняли ответ — формат поменялся. Повтор вслепую
    опасен: запись могла состояться."""

    permanent = True


class KommoUnconfirmedError(KommoError):
    """Запись ушла, а ответ потерян: в CRM она могла появиться. Ключа
    идемпотентности у Kommo нет, и повтор вслепую завёл бы вторую сделку —
    сверить в Kommo руками; повтор по расписанию тут не помощник."""

    permanent = True


@dataclass(frozen=True, slots=True)
class KommoAccount:
    """Учётка `live`. Ключ не печатается: `repr` уходит в журналы и трассировки."""

    subdomain: str
    token: str = field(repr=False)
    pipeline_id: int
    status_id: int
    responsible_user_id: int


@dataclass(frozen=True, slots=True)
class KommoContact:
    """Контакт, который уже есть в Kommo."""

    id: int
    name: str


@dataclass(frozen=True, slots=True)
class NewLead:
    """Что уходит в Kommo о лиде: сделка, человек и его компания."""

    email: str
    site: str
    hypothesis: str
    company: str = ""
    #: Имя человека. Пустое — контакт заводится только с почтой: имя не выдумываем.
    name: str = ""

    @property
    def company_name(self) -> str:
        """Название компании, а без него — сайт: он тоже называет компанию."""
        return self.company.strip() or self.site.strip()

    @property
    def title(self) -> str:
        return f"Email: {self.company_name}"


@dataclass(frozen=True, slots=True)
class CreatedLead:
    """Сделка, которую завёл Kommo."""

    id: int
    url: str
    contact_id: int | None
    company_id: int | None
    #: Контакт с этой почтой уже был — сделка привязана к нему (A1).
    contact_found: bool


def lead_url(subdomain: str, lead_id: int) -> str:
    """Ссылка на сделку для человека — та, что уйдёт телемаркетологу."""
    return f"https://{subdomain}.{DOMAIN}/leads/detail/{lead_id}"


def wanted_email(email: str) -> str:
    """Почта для поиска контакта. Пустой поиск Kommo вернул бы всех, а пустой адрес
    совпал бы с контактом без почты: это ошибка вызывающего, а не «никого не нашли»."""
    address = email.strip().lower()
    if "@" not in address:
        raise ValueError(f"у лида нет почты ({email!r}) — сделку не к кому привязать")
    return address
