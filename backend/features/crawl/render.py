"""Сервис рендеринга: страница, вернувшаяся пустой, — через управляемый браузер снаружи.

Решение ТЗ о краулере: браузерный рендеринг — только для страниц, которые вернулись
пустыми, и через управляемый сервис; включить его на всё — сжечь бюджет обхода за дни.
До 12.10.2026 был только свой Playwright за флагом (уровень 2) — и звался он не на
пустых, а на закрытых страницах. Он остаётся как был; сервис — отдельный путь, и только
для пустых.

**Пустая — это не «не статья».** Раздел со списком и карточка товара статьями не являются,
но текст у них есть, и рендеринг их не изменит — он только стоит денег. Пустая — это
страница, у которой без кода, меню, шапки и подвала (`article.strip_structural_noise`)
текста меньше, чем бывает у тела статьи (`article.MIN_BODY_CHARS`): скелет приложения,
которое рисует текст скриптом. Тела статьи у такой страницы нет заведомо — короче этого
выделение тела ничего статьёй не считает.

**Адаптер общий, а не под один сервис.** Сервис не выбран (решает Anthony), а у тех,
что отдают готовый HTML одним GET, интерфейс один и тот же: адрес API с адресом страницы
в параметре и ключом — параметром или заголовком (`okf/crawl-access.md`). Поэтому настройка —
шаблон адреса с `{url}` (`CRAWL_RENDER_API_URL`) и ключ (`CRAWL_RENDER_API_KEY`): есть
в шаблоне `{key}` — ключ встаёт туда, нет — уходит заголовком `Authorization: Bearer`.
Нет ключа — сервиса нет.

**Ключ — секрет, и адрес с ним — тоже.** Только https и с хостом — шаблон проверяется
на старте обходчика (`config/crawl.render_template_problem`): защита исходящих отказывает
адресу без хоста текстом с адресом целиком, вместе с ключом. В запись обхода, отказы
и журнал адрес API не попадает (у результата — адрес страницы), исключение вызова
наружу идёт только видом, а строку httpx о запросе фильтр журнала чистит от ключа
(`_HideKey`), как у тревог.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

import httpx
from selectolax.parser import HTMLParser

from backend.config import crawl as cfg
from backend.config.crawl import render_template_problem
from backend.features.crawl.article import MIN_BODY_CHARS, strip_structural_noise

logger = logging.getLogger(__name__)

URL_SLOT = "{url}"
KEY_SLOT = "{key}"
HIDDEN = "***"


def looks_empty(html: str) -> bool:
    """Страница вернулась пустой: без кода, меню, шапки и подвала текста меньше, чем бывает
    у тела статьи. Такой странице тела статьи не найти заведомо (`article.MIN_BODY_CHARS`)."""
    tree = HTMLParser(html)
    strip_structural_noise(tree)
    body = tree.body
    text = " ".join((body.text() if body is not None else "").split())
    return len(text) < MIN_BODY_CHARS


def _secret_forms(key: str) -> list[str]:
    """Ключ как он есть и как он стоит в адресе — закодированным."""
    return sorted({key, quote(key, safe="")} - {""}, key=len, reverse=True)


def hidden(text: str) -> str:
    """Текст без ключа сервиса. Всё, что идёт в журнал о вызове, проходит здесь."""
    for secret in _secret_forms(cfg.RENDER_API_KEY):
        text = text.replace(secret, HIDDEN)
    return text


class _HideKey(logging.Filter):
    """Прячет ключ сервиса в строке httpx о запросе — фильтр на логгере, а не на
    обработчике: так он стоит раньше любого обработчика (форма — `shared/alerts.py`)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not cfg.RENDER_API_KEY:
            return True
        if isinstance(record.msg, str):
            record.msg = hidden(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_hidden_arg(arg) for arg in record.args)
        return True


def _hidden_arg(arg: object) -> object:
    """Аргумент с ключом — строкой без ключа; без ключа — как был, своего типа
    (код ответа печатается как `%d`)."""
    text = str(arg)
    clean = hidden(text)
    return arg if clean == text else clean


logging.getLogger("httpx").addFilter(_HideKey())


class RenderService:
    """Вызов сервиса рендеринга: адрес страницы — в шаблон, ключ — в шаблон, ответ — HTML."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        template: str,
        key: str,
        timeout: float | None = None,
        max_bytes: int | None = None,
    ) -> None:
        self._client = client
        self._template = template.strip()
        self._key = key.strip()
        self._timeout = timeout if timeout is not None else cfg.RENDER_TIMEOUT_SEC
        self.max_bytes = max_bytes if max_bytes is not None else cfg.MAX_PAGE_BYTES

    @classmethod
    def from_settings(cls, client: httpx.AsyncClient) -> RenderService | None:
        """Сервис из настроек; ключа нет — `None`: сервиса нет, всё как до него."""
        if not cfg.RENDER_API_KEY:
            return None
        return cls(client, template=cfg.RENDER_API_URL, key=cfg.RENDER_API_KEY)

    @property
    def problem(self) -> str | None:
        """Почему сервис с таким шаблоном звать нельзя. `None` — можно."""
        return render_template_problem(self._template)

    def address(self, url: str) -> str:
        """Адрес вызова: страница и ключ — закодированными, иначе `&` в адресе страницы
        оборвал бы параметр, а ключ с `+` пришёл бы сервису пробелом."""
        return self._template.replace(URL_SLOT, quote(url, safe="")).replace(
            KEY_SLOT, quote(self._key, safe="")
        )

    def headers(self) -> dict[str, str]:
        """Ключ заголовком — когда в шаблоне ему нет места (`{key}`)."""
        return {} if KEY_SLOT in self._template else {"Authorization": f"Bearer {self._key}"}

    async def render(self, url: str) -> tuple[str | None, str | None]:
        """HTML страницы от сервиса — или `None` и причина словами, без адреса вызова."""
        try:
            response = await self._client.get(
                self.address(url), headers=self.headers(), timeout=self._timeout
            )
        except Exception as exc:  # noqa: BLE001 — текст любого может нести ключ
            # Любое: и сетевое, и отказ защиты исходящих (`UnsafeUrlError` — это
            # `ValueError`), и разбор адреса. Текст каждого может нести адрес вызова —
            # с ключом; наружу — только вид, иначе он ушёл бы в запись обхода и журнал.
            logger.info("обход: сервис рендеринга не ответил по %s (%s)", url, type(exc).__name__)
            return None, f"сервис рендеринга не ответил ({type(exc).__name__})"
        if response.status_code >= 400:
            return None, f"сервис рендеринга ответил {response.status_code}"
        return response.text[: self.max_bytes], None
