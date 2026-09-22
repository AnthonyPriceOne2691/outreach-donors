"""Каскад антибота: три уровня, и каждый следующий дороже предыдущего.

Уровень 1 — обычный запрос, доли секунды и копейки трафика. Уровень 2 —
настоящий браузер, секунды на страницу. Уровень 3 — резидентный прокси,
деньги за каждый мегабайт. Включать верхние уровни на всё значит сжечь
бюджет Этапа 2 за дни: рендеринг в 5–75 раз дороже сырого HTML. Поэтому
каждый уровень живёт за своим флагом и по умолчанию выключен.

**Отказ в теле — это отказ.** Страница с кодом 200 и словами «Just
a moment…» — не страница, а заставка защиты. Считать её успехом значит
получить прогон, где все доноры «обошлись», а ссылок ни у кого нет.
Это единственный признак, который нельзя увидеть по коду ответа, —
и единственная причина, по которой каскад смотрит в тело.

**Уровень, которого нет, помечает прогон.** Браузер не установлен,
прокси не настроен — это не тишина в логе, а пометка в записи обхода:
иначе прогон зелёный и врёт, что закрытые сайты проверены.

Ступени контактной лестницы этот каскад не заменяет: там обход ищет
адрес на четырёх страницах, здесь — ходит по сотням. Общее у них
только имя проблемы.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum

import httpx

from backend.config import crawl as cfg
from backend.features.contacts.browser import PageRenderer
from backend.features.contacts.pages import HEADERS
from backend.shared.net.url_guard import GuardedTransport

logger = logging.getLogger(__name__)

#: Имя, которым представляемся, когда представляемся. Форма `compatible`
#: — та, которую читают серверные правила и аналитика чужих сайтов.
BOT_USER_AGENT = f"Mozilla/5.0 (compatible; {cfg.USER_AGENT_TOKEN}/1.0)"


def headers_for(identify: bool) -> dict[str, str]:
    """Заголовки запроса. Представляемся или выглядим браузером.

    Выбор не косметический. Соблюдать запрет, адресованный нашему имени,
    и при этом называться Chrome — несовместимые вещи: сайт, вписавший
    нас в robots.txt, всё равно увидит браузер. С другой стороны, часть
    сайтов закрывается от любого названного бота, и доля закрытых страниц
    у двух режимов разная — а от неё считается смета Этапа 2. Поэтому
    режим переключается, и замер имеет смысл провести дважды.
    """
    if not identify:
        return HEADERS
    return {**HEADERS, "User-Agent": BOT_USER_AGENT}


#: Слова заставок антибота. Ищутся в начале страницы: настоящая статья
#: может цитировать что угодно, а заставка короткая и стоит первой.
ANTIBOT_HEAD_BYTES = 4096

ANTIBOT_MARKERS: tuple[str, ...] = (
    "just a moment",
    "checking your browser",
    "cf-browser-verification",
    "attention required! | cloudflare",
    "enable javascript and cookies to continue",
    "ddos-guard",
    "ddos protection by",
    "request unsuccessful. incapsula",
    "_incapsula_resource",
    "px-captcha",
    "are you a human",
    "access denied",
)


class FetchOutcome(StrEnum):
    """Чем кончилась попытка открыть страницу.

    `MISSING` и `BLOCKED` разделены намеренно: первое — обычная жизнь
    сайта (страница удалена), второе — повод к следующему уровню
    каскада и к тревоге по здоровью обхода. Сложив их в «не открылось»,
    мы купили бы прокси там, где просто битая ссылка.
    """

    OK = "ok"
    MISSING = "missing"  # 404 и подобное: страницы нет
    BLOCKED = "blocked"  # 401/403/429 или заставка антибота
    ERROR = "error"  # таймаут, обрыв, 5xx


class CascadeLevel(StrEnum):
    """Каким уровнем получена страница."""

    HTTP = "http"
    BROWSER = "browser"
    PROXY = "proxy"


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Ответ на одну попытку. `html` есть только при `OK`."""

    url: str
    outcome: FetchOutcome
    level: CascadeLevel | None = None
    status: int | None = None
    html: str | None = None
    reason: str | None = None


@dataclass(slots=True)
class Degradation:
    """Чего каскаду не хватило. Едет в запись обхода, а не только в лог.

    Пустой список здесь и «уровень не понадобился» — разные вещи, и
    различает их `asked`: если верхний уровень ни разу не спросили,
    заявлять о его отсутствии не о чем.
    """

    asked: set[CascadeLevel] = field(default_factory=set)
    unavailable: dict[CascadeLevel, str] = field(default_factory=dict)

    def note(self, level: CascadeLevel, reason: str) -> None:
        if level not in self.unavailable:
            logger.warning("обход: уровень %s недоступен — %s", level.value, reason)
        self.unavailable[level] = reason

    def as_dict(self) -> dict[str, str]:
        return {level.value: reason for level, reason in self.unavailable.items()}


def looks_like_antibot(html: str) -> bool:
    """Заставка защиты вместо страницы."""
    head = html[:ANTIBOT_HEAD_BYTES].lower()
    return any(marker in head for marker in ANTIBOT_MARKERS)


def classify(response: httpx.Response, html: str | None) -> tuple[FetchOutcome, str | None]:
    """Исход по ответу и телу. Тело смотрится только у успешного кода."""
    status = response.status_code
    if status in (401, 403, 429):
        return FetchOutcome.BLOCKED, f"код {status}"
    if status >= 500:
        return FetchOutcome.ERROR, f"код {status}"
    if status >= 400:
        return FetchOutcome.MISSING, f"код {status}"
    if "html" not in response.headers.get("content-type", "").lower():
        return FetchOutcome.MISSING, "не HTML"
    if html is not None and looks_like_antibot(html):
        return FetchOutcome.BLOCKED, "заставка антибота в теле"
    return FetchOutcome.OK, None


class PageCascade:
    """Три уровня доступа к странице, от дешёвого к дорогому.

    Уровни выше первого зовутся только после `BLOCKED` или `ERROR`:
    страница, отданная обычным запросом, браузеру не нужна, а прокси
    не нужен и подавно.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        renderer: PageRenderer | None = None,
        browser_enabled: bool | None = None,
        identify: bool | None = None,
        proxy_url: str | None = None,
        max_page_bytes: int | None = None,
    ) -> None:
        self._client = client
        self._renderer = renderer
        self._browser_enabled = (
            browser_enabled if browser_enabled is not None else cfg.BROWSER_ENABLED
        )
        self._headers = headers_for(identify if identify is not None else cfg.IDENTIFY)
        self._proxy_url = proxy_url if proxy_url is not None else cfg.PROXY_URL
        self._limit = max_page_bytes if max_page_bytes is not None else cfg.MAX_PAGE_BYTES
        self._proxy_client: httpx.AsyncClient | None = None
        self.degradation = Degradation()
        self.by_level: dict[CascadeLevel, int] = dict.fromkeys(CascadeLevel, 0)

    async def get(self, url: str) -> FetchResult:
        """Страница первым уровнем, который её отдал."""
        result = await self._http(url)
        if result.outcome is FetchOutcome.OK or result.outcome is FetchOutcome.MISSING:
            return self._counted(result)

        rendered = await self._browser(url)
        if rendered is not None and rendered.outcome is FetchOutcome.OK:
            return self._counted(rendered)

        proxied = await self._proxy(url)
        if proxied is not None and proxied.outcome is FetchOutcome.OK:
            return self._counted(proxied)

        # Возвращается исход первого уровня: он назвал причину, по которой
        # мы пошли выше, и она же объясняет, почему выше не помогло.
        return self._counted(result)

    def _counted(self, result: FetchResult) -> FetchResult:
        if result.outcome is FetchOutcome.OK and result.level is not None:
            self.by_level[result.level] += 1
        return result

    async def _http(self, url: str) -> FetchResult:
        try:
            response = await self._client.get(url, headers=self._headers, follow_redirects=True)
        except httpx.HTTPError as exc:
            # Причина едет в результате, а не только в лог: по одной
            # странице обрыв — норма, и поднимать его до предупреждения
            # значит залить лог обходом в тысячу страниц.
            logger.debug("обход: %s не открылся (%r)", url, exc)
            return FetchResult(
                url=url,
                outcome=FetchOutcome.ERROR,
                level=CascadeLevel.HTTP,
                reason=f"{type(exc).__name__}: {exc}",
            )
        return self._from_response(response, CascadeLevel.HTTP)

    def _from_response(self, response: httpx.Response, level: CascadeLevel) -> FetchResult:
        html = response.text[: self._limit] if response.status_code < 400 else None
        outcome, reason = classify(response, html)
        return FetchResult(
            url=str(response.url),
            outcome=outcome,
            level=level,
            status=response.status_code,
            html=html if outcome is FetchOutcome.OK else None,
            reason=reason,
        )

    async def _browser(self, url: str) -> FetchResult | None:
        """Уровень 2. `None` — уровень выключен или недоступен."""
        if not self._browser_enabled:
            return None
        self.degradation.asked.add(CascadeLevel.BROWSER)
        if self._renderer is None:
            self.degradation.note(
                CascadeLevel.BROWSER,
                "браузер включён настройкой, но не поднялся: "
                "pip install -e '.[browser]' и playwright install chromium",
            )
            return None

        html = await self._renderer.render(url)
        if html is None:
            return FetchResult(url=url, outcome=FetchOutcome.BLOCKED, level=CascadeLevel.BROWSER)
        if looks_like_antibot(html):
            return FetchResult(
                url=url,
                outcome=FetchOutcome.BLOCKED,
                level=CascadeLevel.BROWSER,
                reason="заставка антибота и в браузере",
            )
        return FetchResult(
            url=url,
            outcome=FetchOutcome.OK,
            level=CascadeLevel.BROWSER,
            html=html[: self._limit],
        )

    async def _proxy(self, url: str) -> FetchResult | None:
        """Уровень 3. Прокси платный: без адреса уровня просто нет."""
        if not self._proxy_url:
            return None
        self.degradation.asked.add(CascadeLevel.PROXY)
        client = self._proxy_or_note()
        if client is None:
            return None

        try:
            response = await client.get(url, headers=self._headers, follow_redirects=True)
        except httpx.HTTPError as exc:
            logger.debug("обход: %s не открылся и через прокси (%r)", url, exc)
            return FetchResult(
                url=url,
                outcome=FetchOutcome.ERROR,
                level=CascadeLevel.PROXY,
                reason=f"{type(exc).__name__}: {exc}",
            )
        return self._from_response(response, CascadeLevel.PROXY)

    def _proxy_or_note(self) -> httpx.AsyncClient | None:
        if self._proxy_client is not None:
            return self._proxy_client
        try:
            inner = httpx.AsyncHTTPTransport(proxy=self._proxy_url, verify=False)
        except (ValueError, httpx.ProxyError) as exc:
            logger.debug("обход: транспорт прокси не собрался (%r)", exc)
            self.degradation.note(CascadeLevel.PROXY, f"адрес прокси не принят: {exc}")
            return None
        self._proxy_client = httpx.AsyncClient(
            transport=GuardedTransport(inner), timeout=cfg.PAGE_TIMEOUT_SEC
        )
        return self._proxy_client

    async def aclose(self) -> None:
        if self._proxy_client is not None:
            await self._proxy_client.aclose()
            self._proxy_client = None
