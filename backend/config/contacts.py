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
    # Соединение — короче чтения: мёртвый сайт молчит именно на соединении, и до
    # 14.10.2026 четыре варианта адреса главной по десять секунд стоили домену 40 с.
    page_connect_timeout_sec: float = Field(
        default=5.0, validation_alias="CONTACTS_PAGE_CONNECT_TIMEOUT_SEC"
    )
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

    # --- Ступень 1б: браузер ---
    # Рендер настоящим браузером для сайтов, которые закрылись или рисуют
    # адрес скриптом. Выключен: секунды на страницу и сотни мегабайт
    # зависимости против каждого шестого домена. Включается на отдельном
    # проходе, когда обычный обход уже отработал (okf/contact-ladder.md).
    browser_enabled: bool = Field(default=False, validation_alias="CONTACTS_BROWSER_ENABLED")
    browser_timeout_sec: float = Field(default=20.0, validation_alias="CONTACTS_BROWSER_TIMEOUT")
    # Пауза после загрузки: футер с адресом дорисовывается скриптом.
    browser_settle_sec: float = Field(default=1.2, validation_alias="CONTACTS_BROWSER_SETTLE")

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
    # Платный поиск адресов доноров и рекламодателей — только явным флагом, не ключом
    # (решение Anthony 12.10.2026). Ключ общий с проверкой адресов продаж
    # (`SALES_VERIFIER_PROVIDER=live`), и одно его появление включало ступень и здесь:
    # автопоиск после каждого «принять» тратил бы квоту поисков (её делят доноры,
    # рекламодатели и сбор базы продаж; проверки у Hunter считаются отдельно) сам. Сбор базы продаж
    # платит по своему флагу (`SALES_SEGMENT_PAID_CONTACTS`), этот его не держит.
    paid_search_enabled: bool = Field(
        default=False, validation_alias="CONTACTS_PAID_SEARCH_ENABLED"
    )
    hunter_api_key: str = Field(default="", validation_alias="CONTACTS_HUNTER_API_KEY")
    hunter_timeout_sec: float = Field(default=20.0, validation_alias="CONTACTS_HUNTER_TIMEOUT_SEC")
    # Ниже этой уверенности адрес платного сервиса не берём: дешевле не писать,
    # чем писать по выдуманному адресу и ловить отказ доставки.
    hunter_min_confidence: int = Field(default=70, validation_alias="CONTACTS_HUNTER_MIN_CONF")

    # --- Ступень 4: ручная очередь ---
    # Ориентир темпа «Форм»: сколько адресов готовы вписать руками за последние
    # 30 дней (`forms.monthly_left`). Ничего не держит: форма сверх него — та же
    # form_only и та же строка очереди (решение Anthony 11.10.2026).
    manual_queue_monthly_cap: int = Field(default=100, validation_alias="CONTACTS_MANUAL_CAP")

    # Срок годности исхода поиска: раньше по домену не ходим заново.
    contact_ttl_days: int = Field(default=180, validation_alias="CONTACTS_TTL_DAYS")

    # --- «Искать глубже»: один донор по кнопке, после обычного поиска без адреса ---
    # Потолки обхода шире обычных (8 страниц и 24 запроса): человек просит один домен,
    # и время на него не делится с сотней соседей. Плюс карта сайта и браузер.
    deep_max_pages: int = Field(default=20, validation_alias="CONTACTS_DEEP_MAX_PAGES")
    deep_max_attempts: int = Field(default=60, validation_alias="CONTACTS_DEEP_MAX_ATTEMPTS")
    # Страниц браузером: каждая — секунды, обычному проходу их три.
    deep_browser_pages: int = Field(default=6, validation_alias="CONTACTS_DEEP_BROWSER_PAGES")
    # Потолок времени на домен: дольше — сайт тянет, и человек ждёт итога у кнопки.
    deep_timeout_sec: float = Field(default=240.0, validation_alias="CONTACTS_DEEP_TIMEOUT_SEC")

    # --- Прогон по файлу: проверка сети ---
    # Заведомо живые адреса через запятую. По ним прогон по файлу отличает
    # «сайт молчит» от «сети нет», чтобы попытки доменов не сгорали без сети.
    # За фильтром, который закрывает все три, — свои адреса; пустое значение
    # выключает проверку, и прогон без сети снова тратит попытки (ревью #128).
    network_probes: str = Field(
        default=(
            "https://www.google.com/generate_204,"
            "https://www.cloudflare.com/cdn-cgi/trace,"
            "https://github.com/"
        ),
        validation_alias="CONTACTS_NETWORK_PROBES",
    )


_s = _Contacts()

MX_TIMEOUT_SEC: float = _s.mx_timeout_sec
DNS_FALLBACK: str = _s.dns_fallback
PAGE_TIMEOUT_SEC: float = _s.page_timeout_sec
PAGE_CONNECT_TIMEOUT_SEC: float = _s.page_connect_timeout_sec
MAX_PAGES_PER_DOMAIN: int = _s.max_pages_per_domain
MAX_ATTEMPTS_PER_DOMAIN: int = _s.max_attempts_per_domain
MAX_PAGE_BYTES: int = _s.max_page_bytes
BROWSER_ENABLED: bool = _s.browser_enabled
BROWSER_TIMEOUT_SEC: float = _s.browser_timeout_sec
BROWSER_SETTLE_SEC: float = _s.browser_settle_sec
RDAP_TIMEOUT_SEC: float = _s.rdap_timeout_sec
RDAP_ENABLED: bool = _s.rdap_enabled
PAID_SEARCH_ENABLED: bool = _s.paid_search_enabled
HUNTER_API_KEY: str = _s.hunter_api_key.strip()  # хвостовой пробел из .env
HUNTER_TIMEOUT_SEC: float = _s.hunter_timeout_sec
HUNTER_MIN_CONFIDENCE: int = _s.hunter_min_confidence
MANUAL_QUEUE_MONTHLY_CAP: int = _s.manual_queue_monthly_cap
CONTACT_TTL_DAYS: int = _s.contact_ttl_days
DEEP_MAX_PAGES: int = _s.deep_max_pages
DEEP_MAX_ATTEMPTS: int = _s.deep_max_attempts
DEEP_BROWSER_PAGES: int = _s.deep_browser_pages
DEEP_TIMEOUT_SEC: float = _s.deep_timeout_sec
NETWORK_PROBES: tuple[str, ...] = tuple(
    url.strip() for url in _s.network_probes.split(",") if url.strip()
)
