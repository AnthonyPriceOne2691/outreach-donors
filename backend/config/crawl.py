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

import logging
import re
from urllib.parse import SplitResult, urlsplit

from pydantic import Field, field_validator

from backend.config._base import DomainSettings

logger = logging.getLogger(__name__)


class _Crawl(DomainSettings):
    # --- Границы обхода одного донора ---
    # Три потолка двигаются вместе: поднятый один упирается в другой.
    # Потолок открытых страниц — 1 000, как в требовании. До 06.10.2026
    # умолчание было 200 «для замера», и боевой обход шёл по нему же:
    # на проде настройки не заданы, оба донора встали ровно на 200.
    max_pages_per_donor: int = Field(default=1_000, validation_alias="CRAWL_MAX_PAGES")
    # Потолок попыток. Считается отдельно от открытых страниц: сайт может
    # отвечать отказом на большинство адресов из sitemap, и общий счётчик
    # заканчивался бы на них, не дойдя до живых. Вдвое больше страниц.
    max_attempts_per_donor: int = Field(default=2_000, validation_alias="CRAWL_MAX_ATTEMPTS")
    # Потолок времени на донора. Чекпоинт по времени, а не по страницам:
    # чекпоинт «каждые 10 000 страниц» при лимите 1 000 не сработает ни
    # разу: чекпоинт в требовании больше, чем весь краул одного донора.
    # Час: боевой обход шёл ~1,4 с на страницу (200 за 259–297 с), то есть
    # 1 000 страниц — около 25 минут; прежние 10 минут обрывали бы на ~400.
    max_seconds_per_donor: float = Field(default=3_600.0, validation_alias="CRAWL_MAX_SECONDS")
    # Потолок срока сайту с долгой паузой (23.10.2026): срок донора — столько, чтобы при
    # его паузе успеть потолок страниц, но не меньше срока выше и не больше этого
    # (`crawl/pace.py`): с долгой паузой час не вмещал и половины потолка. Три часа —
    # тысяча страниц при самой долгой паузе, которой слушаемся (10 с), и обычном ответе.
    max_seconds_slow: float = Field(default=10_800.0, validation_alias="CRAWL_MAX_SECONDS_SLOW")
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
    # Представляться ли своим именем. **Выключено — решение Anthony
    # 22.09.2026 по итогам замера: «представляться тем, кого чаще
    # и больше пускают».** Замер на пяти донорах ниши: своим именем
    # 8,3% закрытых страниц и один донор из пяти недоступен целиком,
    # браузерными заголовками — ноль и ноль (okf/crawl-access.md).
    #
    # Цена решения записана там же: запрет, адресованный нашему имени
    # в robots.txt, перестаёт срабатывать — сайт вписал нас, а видит
    # браузер. Общие правила (`User-agent: *`) соблюдаются по-прежнему
    # и проверяются до первого запроса к страницам.
    identify: bool = Field(default=False, validation_alias="CRAWL_IDENTIFY")
    # Сколько файлов sitemap читаем максимум (индекс ссылается на индексы).
    max_sitemap_files: int = Field(default=20, validation_alias="CRAWL_MAX_SITEMAP_FILES")
    # Сколько адресов берём из sitemap максимум. Крупный сайт отдаёт
    # десятки тысяч, а потолок страниц всё равно меньше.
    max_sitemap_urls: int = Field(default=5_000, validation_alias="CRAWL_MAX_SITEMAP_URLS")

    # --- Каскад антибота ---
    # Уровень 2: настоящий браузер. Выключен — секунды на страницу.
    browser_enabled: bool = Field(default=False, validation_alias="CRAWL_BROWSER_ENABLED")
    # Уровень 3: резидентные прокси — списком, через запятую или с новой строки. Обход
    # меняет прокси на следующий при отказе через него и при просевшем здоровье
    # (требование: «отказов больше 10% — темп вдвое ниже и смена прокси»). Пусто —
    # уровня нет: не куплены. Прежнее имя `CRAWL_PROXY_URL` (один адрес) — то же.
    proxy_urls: str = Field(default="", validation_alias="CRAWL_PROXY_URLS")
    proxy_url: str = Field(default="", validation_alias="CRAWL_PROXY_URL")
    # Цена трафика прокси, $ за гигабайт (у резидентных — $3,5–7,4 по страницам цен
    # провайдеров на 12.10.2026, `okf/stage2-economy.md`). Пусто — цена неизвестна,
    # и прокси не зовётся: тратить вслепую нельзя (`crawl/spend.py`).
    proxy_usd_per_gb: float | None = Field(
        default=None, ge=0, validation_alias="CRAWL_PROXY_USD_PER_GB"
    )
    # Сервис рендеринга — только для страниц, вернувшихся пустыми, и только управляемый
    # (`crawl/render.py`). Адрес API — https-шаблон с хостом, `{url}` (адрес страницы,
    # закодированный) и `{key}` (ключ ниже); нет `{key}` — ключ уходит заголовком
    # `Authorization: Bearer`. Ключа нет — сервиса нет, свой браузер (уровень 2) как был.
    render_api_url: str = Field(default="", validation_alias="CRAWL_RENDER_API_URL")
    render_api_key: str = Field(default="", validation_alias="CRAWL_RENDER_API_KEY")
    # Сколько ждём сервис: он открывает страницу браузером, это секунды, а не доли.
    render_timeout_sec: float = Field(
        default=60.0, gt=0, validation_alias="CRAWL_RENDER_TIMEOUT_SEC"
    )
    # Цена вызова сервиса, $ за тысячу запросов (с JS — $0,98–4,90 по страницам цен
    # провайдеров на 12.10.2026). Пусто — как у прокси: сервис не зовётся.
    render_usd_per_1k: float | None = Field(
        default=None, ge=0, validation_alias="CRAWL_RENDER_USD_PER_1K"
    )

    # --- Свой потолок Этапа 2 ---
    # Потолок денег на платный обход — прокси и рендеринг — за календарный месяц, $,
    # отдельным счётчиком от Этапа 1: иначе один донор за защитой съест месяц. Пусто —
    # платные уровни не зовутся вовсе: без потолка их трата ничем не держится.
    month_cap_usd: float | None = Field(default=None, ge=0, validation_alias="CRAWL_MONTH_CAP_USD")

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

    # --- Скоринг ---
    # Размещение старше стольких лет — не «существующий рекламодатель донора»
    # (требование: «переманить существующих рекламодателей»). Обход 06.10
    # дал «куплено» бонусу банка 2007 года, которого давно нет.
    stale_years: int = Field(default=3, validation_alias="CRAWL_STALE_YEARS")

    # --- Сторож нагрузки обходчика (`workers/load_gate.py`) ---
    # Новый обход копия берёт, только если на машине сейчас свободно ядер не меньше
    # «на обход» + «в запас» и памяти не меньше порога; идущий обход сторож не трогает.
    # Правило Anthony 29.10.2026: сосед занял ядра — обходчики берут, сколько свободно;
    # свободны все — все 16. На обход — 0,75 ядра: потолок копии в
    # `docker-compose.prod.yml`, больше обход взять не может; в запас — ядро базе, API и
    # соседу на всплеск; памяти — 8 ГБ: копии нужно до полутора. 0 — проверка выключена.
    gate_cores_per_crawl: float = Field(
        default=0.75, ge=0, validation_alias="CRAWL_GATE_CORES_PER_CRAWL"
    )
    gate_reserve_cores: float = Field(
        default=1.0, ge=0, validation_alias="CRAWL_GATE_RESERVE_CORES"
    )
    gate_min_free_gb: float = Field(default=8.0, ge=0, validation_alias="CRAWL_GATE_MIN_FREE_GB")
    # Как часто ждущая копия смотрит снова, с, и как часто пишет в журнал, что ещё ждёт.
    gate_poll_sec: float = Field(default=15.0, gt=0, validation_alias="CRAWL_GATE_POLL_SEC")
    gate_log_every_sec: float = Field(
        default=300.0, gt=0, validation_alias="CRAWL_GATE_LOG_EVERY_SEC"
    )
    # После взятого обхода остальные копии столько секунд новых не берут: нагрузка начатого
    # должна попасть в их замер, иначе по одному замеру стартовали бы все разом. 0 — разом.
    gate_stagger_sec: float = Field(default=20.0, ge=0, validation_alias="CRAWL_GATE_STAGGER_SEC")

    @field_validator("proxy_usd_per_gb", "render_usd_per_1k", "month_cap_usd", mode="before")
    @classmethod
    def _unset_if_empty(cls, value: object) -> object:
        """Пустое значение в `.env` — «не задано», а не ошибка разбора числа."""
        return None if value == "" else value


def _proxy_list(*raw: str) -> tuple[str, ...]:
    """Адреса прокси из настроек: через запятую, пробел или перевод строки, без повторов,
    в заданном порядке — по нему обход и меняет прокси."""
    found = (part.strip() for text in raw for part in re.split(r"[\s,]+", text))
    return tuple(dict.fromkeys(part for part in found if part))


#: Схемы прокси, которые понимает транспорт обхода (`socks5` — с пакетом httpx[socks]).
PROXY_SCHEMES = frozenset({"http", "https", "socks5", "socks5h"})


def proxy_problem(url: str) -> str | None:
    """Что не так с адресом прокси — словами и без логина, пароля и хоста; `None` — годен.

    **Адрес несёт пароль, и битый адрес выдаёт его кусками.** Неэкранированный «#»,
    «?» или «/» в пароле режет адрес: разбор видит в остатке фрагмент, параметры или
    путь, а хостом — кусок пароля, и этот кусок уходил в имя прокси, в отказ транспорта
    и в журнал. Такой адрес не берём вовсе: отказ на старте (`check_crawl`), а не
    на первой закрытой странице. Сначала — то, что режет адрес, потом — его части.
    """
    if "://" not in url:
        return "нет схемы — нужно http://хост:порт"
    try:
        parts = urlsplit(url)
    except ValueError:
        # Текст исключения несёт адрес — с паролем; в журнал — только что случилось.
        logger.debug("настройки обхода: адрес прокси не разбирается")
        return "адрес не разбирается"
    return _cut(parts) or _named(parts)


def _cut(parts: SplitResult) -> str | None:
    """Что режет адрес: неэкранированные «/», «?», «#» или «@» в логине и пароле."""
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        return (
            "после хоста — путь, параметры или «#»: в логине или пароле неэкранированный "
            "«/», «?» или «#» — закодируйте их (%2F, %3F, %23)"
        )
    if parts.netloc.count("@") > 1:
        return "в логине или пароле неэкранированный «@» — закодируйте его (%40)"
    return None


def _named(parts: SplitResult) -> str | None:
    """Части целого адреса: схема, хост, порт."""
    try:
        port = parts.port
    except ValueError:
        logger.debug("настройки обхода: порт прокси не число или вне 1–65535")
        port = 0
    if port == 0:
        return "порт не число или вне 1–65535"
    if parts.scheme not in PROXY_SCHEMES:
        return "схема не http, https или socks5"
    return None if parts.hostname else "нет хоста"


def render_template_problem(template: str) -> str | None:
    """Что не так с шаблоном адреса сервиса рендеринга — словами; `None` — годен.

    Шаблон несёт ключ (`{key}`), поэтому всё, что ломает адрес, ломает его до запроса,
    а не в запросе: защита исходящих отказывает адресу без хоста текстом с адресом
    целиком — вместе с ключом (`shared/net/url_guard.py`).
    """
    if not template:
        return "ключ сервиса рендеринга задан, а адрес API — нет (CRAWL_RENDER_API_URL)"
    try:
        parts = urlsplit(template)
    except ValueError:
        logger.debug("настройки обхода: шаблон сервиса рендеринга не разбирается")
        return "адрес API сервиса рендеринга не разбирается"
    if parts.scheme != "https":
        return "адрес API сервиса рендеринга — не https: ключ ушёл бы открытым текстом"
    if not parts.hostname or "{" in parts.netloc:
        return "в адресе API сервиса рендеринга нет хоста — нужно https://хост/…"
    if "{url}" not in template:
        return "в адресе API сервиса рендеринга нет {url} — некуда подставить страницу"
    return None


_s = _Crawl()

MAX_PAGES_PER_DONOR: int = _s.max_pages_per_donor
MAX_ATTEMPTS_PER_DONOR: int = _s.max_attempts_per_donor
MAX_SECONDS_PER_DONOR: float = _s.max_seconds_per_donor
MAX_SECONDS_SLOW: float = _s.max_seconds_slow
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
PROXY_URLS: tuple[str, ...] = _proxy_list(_s.proxy_urls, _s.proxy_url)
PROXY_USD_PER_GB: float | None = _s.proxy_usd_per_gb
RENDER_API_URL: str = _s.render_api_url.strip()
RENDER_API_KEY: str = _s.render_api_key.strip()  # хвостовой пробел из .env ломает адрес
RENDER_TIMEOUT_SEC: float = _s.render_timeout_sec
RENDER_USD_PER_1K: float | None = _s.render_usd_per_1k
MONTH_CAP_USD: float | None = _s.month_cap_usd
HEALTH_WINDOW: int = _s.health_window
SLOW_DOWN_SHARE: float = _s.slow_down_share
STOP_SHARE: float = _s.stop_share
HEALTH_MIN_REQUESTS: int = _s.health_min_requests
STALE_YEARS: int = _s.stale_years
GATE_CORES_PER_CRAWL: float = _s.gate_cores_per_crawl
GATE_RESERVE_CORES: float = _s.gate_reserve_cores
GATE_MIN_FREE_GB: float = _s.gate_min_free_gb
GATE_POLL_SEC: float = _s.gate_poll_sec
GATE_LOG_EVERY_SEC: float = _s.gate_log_every_sec
GATE_STAGGER_SEC: float = _s.gate_stagger_sec
