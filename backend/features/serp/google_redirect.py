"""Редирект Google вместо адреса сайта: куда на самом деле ведёт позиция выдачи.

Google отдаёт свой редирект `google.com/goto?url=<жетон>`, и `domain` у такой позиции — тоже
`google.com`. С конца октября 2026 он ограничил, сколько таких ссылок одной страницы выдачи
раскрывается сразу, и провайдер в ответе их не раскрывает. Проверка на проде 06.11.2026: вся выдача
ключа свелась к одному домену `google.com`, гейт снял его как платформу, и прогон закончился как
обычный: ноль кандидатов, ни юнита, ни тревоги.

Видимый адрес позиции называет сайт почти всегда, а метрики и письма — про домен. Страницу он не
называет: у провайдера есть отдельный конвертер жетонов, но он отвечает десятками секунд на страницу
выдачи, а чинить надо было сейчас.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs

from backend.shared.net.url_parts import split_url

_GOOGLE_HOST = re.compile(r"(www\.)?google(\.[a-z]{2,3}){1,2}")
_REDIRECT_PATHS = ("/goto", "/url")
_CRUMB_STEP = re.compile(r"\s*[›>]\s*")


def _is_redirect(url: str) -> bool:
    parts = split_url(url)
    return bool(
        parts
        and parts.hostname
        and _GOOGLE_HOST.fullmatch(parts.hostname)
        and parts.path.rstrip("/") in _REDIRECT_PATHS
    )


def _named_target(url: str) -> str | None:
    """Адрес, который редирект называет сам: старая обёртка `google.com/url?q=<адрес>`.
    Жетон `goto` адреса не называет."""
    parts = split_url(url)
    query = parse_qs(parts.query) if parts else {}
    for name in ("q", "url"):
        for value in query.get(name, ()):
            if value.startswith(("http://", "https://")):
                return value
    return None


def _shown_site(breadcrumb: object) -> str | None:
    """Корень сайта из видимого адреса позиции: `https://www.site.com › blog` → `https://www.site.com/`.

    Видимый адрес называет сайт, но не страницу: хвост у него приукрашен, собирать из него ссылку
    нельзя. Этого хватает: метрики и письма — про домен, а заголовок и описание позиция принесла
    настоящие. Обрезанное имя (`https://www.site-with-long-na…`) и подпись вместо адреса (у соцсетей
    там счётчики) — не адрес: позиция пропускается, а не становится чужим доменом.
    """
    if not isinstance(breadcrumb, str):
        return None
    shown = _CRUMB_STEP.split(breadcrumb.strip(), maxsplit=1)[0]
    if "…" in shown or "..." in shown or " " in shown or "." not in shown:
        return None
    parts = split_url(shown if "//" in shown else f"https://{shown}")
    if parts is None or not parts.hostname:
        return None
    return f"{parts.scheme or 'https'}://{parts.hostname}/"


def destination(url: str, breadcrumb: object) -> str | None:
    """Куда ведёт позиция: обычный адрес — как есть, редирект Google — тем, что удалось узнать."""
    if not _is_redirect(url):
        return url
    return _named_target(url) or _shown_site(breadcrumb)
