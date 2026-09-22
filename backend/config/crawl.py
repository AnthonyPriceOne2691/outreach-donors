"""Обход донора: границы, каскад антибота и пороги здоровья.

Зачем каждая граница и откуда взяты числа — `docs/CRAWL.md`. Здесь
только значения, которые меняются окружением, не трогая код.

Умолчания подобраны под **замер**, а не под рабочий объём: на замере
важно быстро получить долю закрытых страниц по нескольким донорам,
а не обойти один донор целиком. Рабочие числа (тысяча страниц на донора,
как просит требование) ставятся окружением, когда путь Этапа 2 будет
выбран.
"""

from __future__ import annotations

from pydantic import Field

from backend.config._base import DomainSettings


class _Crawl(DomainSettings):
    # --- Границы обхода одного донора ---
    # Потолок открытых страниц. Требование называет 1 000 на рабочем
    # объёме; умолчание ниже — замер идёт по нескольким донорам подряд.
    max_pages_per_donor: int = Field(default=200, validation_alias="CRAWL_MAX_PAGES")
    # Потолок попыток. Считается отдельно от открытых страниц: сайт может
    # отвечать отказом на большинство адресов из sitemap, и общий счётчик
    # заканчивался бы на них, не дойдя до живых.
    max_attempts_per_donor: int = Field(default=400, validation_alias="CRAWL_MAX_ATTEMPTS")
    # Потолок времени на донора. Чекпоинт по времени, а не по страницам:
    # чекпоинт «каждые 10 000 страниц» при лимите 1 000 не сработает ни
    # разу: чекпоинт в требовании больше, чем весь краул одного донора.
    max_seconds_per_donor: float = Field(default=600.0, validation_alias="CRAWL_MAX_SECONDS")
    # Страницы больше этого размера не разбираем.
    max_page_bytes: int = Field(default=3_000_000, validation_alias="CRAWL_MAX_PAGE_BYTES")
    page_timeout_sec: float = Field(default=15.0, validation_alias="CRAWL_PAGE_TIMEOUT_SEC")

    # --- Ограничитель на домен ---
    # Пауза между запросами к одному хосту, меряется от конца предыдущего.
    # Crawl-delay из robots.txt сильнее этого значения, если он больше.
    delay_sec: float = Field(default=1.0, validation_alias="CRAWL_DELAY_SEC")
    # Потолок на Crawl-delay: встречаются значения в минуты, и слепо им
    # подчиняться значит потратить весь бюджет времени на один сайт.
    max_delay_sec: float = Field(default=10.0, validation_alias="CRAWL_MAX_DELAY_SEC")

    # --- robots.txt и sitemap ---
    # Имя, которым представляемся. Оно же ищется в robots.txt: сайт вправе
    # запретить именно нас, и такой запрет обязан работать.
    user_agent_token: str = Field(default="ParsingPricesBot", validation_alias="CRAWL_UA_TOKEN")
    robots_timeout_sec: float = Field(default=10.0, validation_alias="CRAWL_ROBOTS_TIMEOUT_SEC")
    # Представляться ли своим именем. Включено: соблюдать запрет,
    # адресованный нашему имени, и называться при этом браузером —
    # несовместимые вещи. Выключение даёт другую долю закрытых страниц,
    # и замер стоит провести в обоих режимах, а не выбирать вслепую.
    identify: bool = Field(default=True, validation_alias="CRAWL_IDENTIFY")
    # Сколько файлов sitemap читаем максимум (индекс ссылается на индексы).
    max_sitemap_files: int = Field(default=20, validation_alias="CRAWL_MAX_SITEMAP_FILES")
    # Сколько адресов берём из sitemap максимум. Крупный сайт отдаёт
    # десятки тысяч, а потолок страниц всё равно меньше.
    max_sitemap_urls: int = Field(default=5_000, validation_alias="CRAWL_MAX_SITEMAP_URLS")

    # --- Каскад антибота ---
    # Уровень 2: настоящий браузер. Выключен — секунды на страницу.
    browser_enabled: bool = Field(default=False, validation_alias="CRAWL_BROWSER_ENABLED")
    # Уровень 3: резидентный прокси. Выключен и не куплен; флаг существует,
    # чтобы включение было настройкой, а не правкой кода.
    proxy_url: str = Field(default="", validation_alias="CRAWL_PROXY_URL")

    # --- Здоровье обхода ---
    # Окно, по которому считаются доли отказов: требование говорит
    # «за последние 500 запросов». Среднее за всё время прячет защиту,
    # включившуюся на середине прогона.
    health_window: int = Field(default=500, validation_alias="CRAWL_HEALTH_WINDOW")
    # Доля отказов, после которой темп вдвое ниже.
    slow_down_share: float = Field(default=0.10, validation_alias="CRAWL_SLOW_DOWN_SHARE")
    # Доля отказов, после которой обход останавливается с тревогой.
    stop_share: float = Field(default=0.30, validation_alias="CRAWL_STOP_SHARE")
    # Доли считаются только начиная с этого числа запросов: три отказа
    # из трёх в начале прогона — это не 100% нездоровья, это три отказа.
    health_min_requests: int = Field(default=20, validation_alias="CRAWL_HEALTH_MIN_REQUESTS")


_s = _Crawl()

MAX_PAGES_PER_DONOR: int = _s.max_pages_per_donor
MAX_ATTEMPTS_PER_DONOR: int = _s.max_attempts_per_donor
MAX_SECONDS_PER_DONOR: float = _s.max_seconds_per_donor
MAX_PAGE_BYTES: int = _s.max_page_bytes
PAGE_TIMEOUT_SEC: float = _s.page_timeout_sec
DELAY_SEC: float = _s.delay_sec
MAX_DELAY_SEC: float = _s.max_delay_sec
USER_AGENT_TOKEN: str = _s.user_agent_token
ROBOTS_TIMEOUT_SEC: float = _s.robots_timeout_sec
IDENTIFY: bool = _s.identify
MAX_SITEMAP_FILES: int = _s.max_sitemap_files
MAX_SITEMAP_URLS: int = _s.max_sitemap_urls
BROWSER_ENABLED: bool = _s.browser_enabled
PROXY_URL: str = _s.proxy_url
HEALTH_WINDOW: int = _s.health_window
SLOW_DOWN_SHARE: float = _s.slow_down_share
STOP_SHARE: float = _s.stop_share
HEALTH_MIN_REQUESTS: int = _s.health_min_requests
