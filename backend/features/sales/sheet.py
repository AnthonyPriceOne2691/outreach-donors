"""Google-таблица по ссылке: CSV-экспорт листа — без ключей и без новой зависимости.

**Сервер ходит только на `docs.google.com`**, по адресу, собранному здесь из номера
таблицы и листа. Сама ссылка из поля ввода не открывается: иначе поле стало бы
дверью во внутреннюю сеть.

**Отказ — словами, а не пустым списком.** Замер 02.10.2026 (запрос без входа):
закрытая таблица — 401 и HTML, таблицы нет — 404, листа из ссылки нет — 400,
открытая — 307 на googleusercontent и 200 `text/csv`. У соседнего проекта 10.09
закрытая отвечала страницей входа со статусом 200 (урок L10), поэтому HTML
вместо CSV значит «закрыта» при любом коде. «Google не ответил» — другой исход:
его лечит повтор, а не доступ.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import httpx

from backend.shared.net.url_guard import guarded_client

TIMEOUT_S = 30.0

CLOSED = "таблица не открыта по ссылке — откройте доступ или загрузите CSV"

_EXPORT = "https://docs.google.com/spreadsheets/d/{key}/export?format=csv&gid={gid}"
#: «Опубликовать в интернете» даёт другую ссылку и другой экспорт.
_PUBLISHED = "https://docs.google.com/spreadsheets/d/e/{key}/pub?output=csv&gid={gid}"
_PATH = re.compile(r"/spreadsheets/d/(e/)?([\w-]{20,})")
_GID = re.compile(r"[#&?]gid=(\d+)")
_HTML = (b"<!doctype html", b"<html")


class SheetError(ValueError):
    """Ссылка не ведёт к таблице, которую можно прочитать. Текст говорит, что делать."""


class SheetUnavailableError(RuntimeError):
    """Google не ответил или ответил непонятно — повторить позже или загрузить файл."""


def export_url(link: str) -> str:
    """Ссылка на таблицу в любой форме → адрес CSV-экспорта её листа.

    Номер листа — из ссылки (`#gid=`, `?gid=`), нет его — первый лист, как у Google.
    """
    text = link.strip()
    refusal = SheetError(
        f"«{link}» — не ссылка на Google-таблицу: ждём https://docs.google.com/spreadsheets/d/…"
    )
    try:
        parts = urlsplit(text if "://" in text else f"https://{text}")
    except ValueError as exc:  # `[` в адресе: urlsplit принимает его за IPv6 и бросает
        raise refusal from exc
    found = _PATH.match(parts.path)
    if parts.hostname != "docs.google.com" or found is None:
        raise refusal
    gid = _GID.search(text)
    template = _PUBLISHED if found.group(1) else _EXPORT
    return template.format(key=found.group(2), gid=gid.group(1) if gid else "0")


async def fetch(link: str, http: httpx.AsyncClient, *, limit: int) -> bytes:
    """CSV листа по ссылке. Больше `limit` байт не читается."""
    url = export_url(link)
    try:
        async with http.stream("GET", url, follow_redirects=True) as response:
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > limit:
                    raise SheetError(
                        f"таблица больше {limit / 2**20:g} МБ — выгрузите её в CSV частями"
                    )
    except httpx.HTTPError as exc:
        raise SheetUnavailableError(
            f"Google не ответил ({type(exc).__name__}) — повторите позже или загрузите CSV"
        ) from exc
    _judge(response.status_code, bytes(body), url)
    return bytes(body)


def _judge(status: int, body: bytes, url: str) -> None:
    """Ответ Google → отказ словами. Тело с HTML — не таблица, при любом коде."""
    if status == 404:
        raise SheetError(
            f"таблица по ссылке не найдена — проверьте ссылку; если она верна, {CLOSED}"
        )
    if status == 400:
        gid = url.rpartition("gid=")[2]
        raise SheetError(
            f"в таблице нет листа из ссылки (gid={gid}) — откройте нужный лист "
            "и скопируйте ссылку заново"
        )
    if status in (401, 403) or body.lstrip()[:16].lower().startswith(_HTML):
        raise SheetError(CLOSED)
    if status != 200:
        raise SheetUnavailableError(
            f"Google ответил кодом {status} — повторите позже или загрузите CSV"
        )


def client() -> httpx.AsyncClient:
    """Клиент для похода по ссылке, которую дал человек: страж проекта — приватные
    адреса, адреса редиректов и потолок тела (`shared.net.url_guard`), таймаут таблицы."""
    return guarded_client(timeout=TIMEOUT_S)
