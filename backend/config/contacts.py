"""Поиск контакта: ступени лестницы, их лимиты и ключ платного сервиса.

Порядок ступеней и причины, по которым он именно такой, — в
`okf/contact-ladder.md`. Здесь только числа, которые можно менять
окружением, не трогая код.
"""

from __future__ import annotations

from pydantic import Field

from backend.config._base import DomainSettings


class _Contacts(DomainSettings):
    # --- Ступень 0: MX ---
    # Нет MX — домен письмо не примет, дальше по лестнице не идём.
    mx_timeout_sec: float = Field(default=5.0, validation_alias="CONTACTS_MX_TIMEOUT_SEC")
    # Запасные резолверы. Системный бывает настроен на адреса, до которых
    # с машины нет маршрута: тогда ступень молча возвращает «неизвестно»
    # по всем доменам и стоит таймаута на каждом. Поймано боевым прогоном.
    dns_fallback: str = Field(default="1.1.1.1,8.8.8.8", validation_alias="CONTACTS_DNS_FALLBACK")

    # --- Ступень 1: страницы сайта ---
    page_timeout_sec: float = Field(default=10.0, validation_alias="CONTACTS_PAGE_TIMEOUT_SEC")
    # Сколько страниц максимум качаем на домен. Без потолка домен с бесконечной
    # навигацией съедает прогон.
    max_pages_per_domain: int = Field(default=8, validation_alias="CONTACTS_MAX_PAGES")
    # Потолок запросов на домен. Считается отдельно от открытых страниц:
    # сайт отвечает 404 на большинство угадываемых слагов, и общий счётчик
    # заканчивался бы на несуществующих адресах, не дожив до существующих.
    max_attempts_per_domain: int = Field(default=24, validation_alias="CONTACTS_MAX_ATTEMPTS")
    # Страницы больше этого размера не разбираем: адрес в первых сотнях
    # килобайт, а дальше начинается выгрузка каталога.
    max_page_bytes: int = Field(default=2_000_000, validation_alias="CONTACTS_MAX_PAGE_BYTES")

    # --- Ступень 2: RDAP ---
    # Пять секунд, а не десять: отдача ступени близка к нулю (контакты
    # владельца скрыты в большинстве зон), и ждать её долго незачем.
    rdap_timeout_sec: float = Field(default=5.0, validation_alias="CONTACTS_RDAP_TIMEOUT_SEC")
    # Ступень выключена. Два замера дали ноль адресов из 58 доменов при
    # каждом пятом запросе в таймаут: контакты владельца скрыты почти во
    # всех зонах. Код остался — включается этой переменной, если появится
    # ниша со старыми зонами, где владелец ещё виден.
    rdap_enabled: bool = Field(default=False, validation_alias="CONTACTS_RDAP_ENABLED")

    # --- Ступень 3: платный сервис ---
    hunter_api_key: str = Field(default="", validation_alias="CONTACTS_HUNTER_API_KEY")
    hunter_timeout_sec: float = Field(default=20.0, validation_alias="CONTACTS_HUNTER_TIMEOUT_SEC")
    # Ниже этой уверенности адрес платного сервиса не берём: дешевле не писать,
    # чем писать по выдуманному адресу и ловить отказ доставки.
    hunter_min_confidence: int = Field(default=70, validation_alias="CONTACTS_HUNTER_MIN_CONF")

    # --- Ступень 4: ручная очередь ---
    # Форм в месяц, которые готовы заполнить руками. Сверх потолка домен
    # получает form_only и ждёт следующего месяца.
    manual_queue_monthly_cap: int = Field(default=100, validation_alias="CONTACTS_MANUAL_CAP")

    # Срок годности исхода поиска: раньше по домену не ходим заново.
    contact_ttl_days: int = Field(default=180, validation_alias="CONTACTS_TTL_DAYS")


_s = _Contacts()

MX_TIMEOUT_SEC: float = _s.mx_timeout_sec
DNS_FALLBACK: str = _s.dns_fallback
PAGE_TIMEOUT_SEC: float = _s.page_timeout_sec
MAX_PAGES_PER_DOMAIN: int = _s.max_pages_per_domain
MAX_ATTEMPTS_PER_DOMAIN: int = _s.max_attempts_per_domain
MAX_PAGE_BYTES: int = _s.max_page_bytes
RDAP_TIMEOUT_SEC: float = _s.rdap_timeout_sec
RDAP_ENABLED: bool = _s.rdap_enabled
HUNTER_API_KEY: str = _s.hunter_api_key
HUNTER_TIMEOUT_SEC: float = _s.hunter_timeout_sec
HUNTER_MIN_CONFIDENCE: int = _s.hunter_min_confidence
MANUAL_QUEUE_MONTHLY_CAP: int = _s.manual_queue_monthly_cap
CONTACT_TTL_DAYS: int = _s.contact_ttl_days
