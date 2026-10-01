"""Сеть-заглушка для прогона по файлу: сайты, отказы, обрывы.

Прогон по файлу ходит наружу тремя путями — HTTP-клиент, DNS (ступень MX)
и браузер. Здесь все три подменяются одним вызовом `install`, а сайт
описывается словарём «путь → ответ»: строка — страница, число — код
ответа, `Moved` — редирект, `DOWN` и `SLOW` — обрыв и таймаут. Хост,
которого нет в словаре, не отвечает вовсе: так выглядит и выключенная
связь, и домен, которого нет.
"""

from __future__ import annotations

import argparse
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
from backend.cli.main import build_parser
from backend.features.contacts import file_sweep, mx

#: Соединение не установилось: связи нет, хост выключен, имя не разрешилось.
DOWN = "<обрыв>"
#: Соединение есть, ответа не дождались.
SLOW = "<таймаут>"


@dataclass(frozen=True, slots=True)
class Moved:
    """Редирект на другой адрес."""

    to: str


Reply = str | int | Moved


class Web:
    """Сайты-заглушки. Значение по хосту — словарь «путь → ответ» или один ответ
    на все пути сразу: `DOWN`, `SLOW` или код (403 — сайт закрылся целиком)."""

    def __init__(self, sites: Mapping[str, Mapping[str, Reply] | str | int]) -> None:
        self.sites = dict(sites)
        #: Запросы к сайтам. Пробы сети сюда не попадают: «сайт не тронут»
        #: проверяется по этому списку, и пробы сделали бы его непустым всегда.
        self.requested: list[str] = []
        #: Сеть целиком: выключенная, она не отвечает ни сайтам, ни пробам.
        self.network = True
        self.probes: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if not self.network:
            raise httpx.ConnectError("сети нет", request=request)
        if str(request.url) in file_sweep.NETWORK_PROBES:
            self.probes.append(str(request.url))
            return httpx.Response(204, request=request)
        self.requested.append(str(request.url))
        site = self.sites.get(request.url.host or "", DOWN)
        reply = site if isinstance(site, str | int) else site.get(request.url.path, 404)
        if reply == DOWN:
            raise httpx.ConnectError("соединение не установилось", request=request)
        if reply == SLOW:
            raise httpx.ReadTimeout("ответа не дождались", request=request)
        if isinstance(reply, Moved):
            return httpx.Response(301, headers={"location": reply.to}, request=request)
        if isinstance(reply, int):
            return httpx.Response(reply, text="нет", request=request)
        return httpx.Response(200, html=reply, request=request)


class FakeRenderer:
    """Браузер-заглушка: заранее заданный HTML по адресу, открытия считаются."""

    def __init__(self, pages: Mapping[str, str] | None = None) -> None:
        self.pages = dict(pages or {})
        self.opened: list[str] = []

    async def render(self, url: str) -> str | None:
        self.opened.append(url)
        return self.pages.get(url)


@dataclass(slots=True)
class Browser:
    """Что происходило с браузером: поднимали ли его вообще."""

    renderer: FakeRenderer | None = None
    started: list[bool] = field(default_factory=list)


def install(
    monkeypatch: pytest.MonkeyPatch,
    web: Web,
    *,
    routes: Mapping[str, mx.MailRoute] | None = None,
    renderer: FakeRenderer | None = None,
) -> Browser:
    """Подменить сеть прогона по файлу. MX по умолчанию — «почта принимается»."""
    transport = httpx.MockTransport(web)
    browser = Browser(renderer=renderer)

    @asynccontextmanager
    async def client(**_kwargs: object) -> AsyncIterator[httpx.AsyncClient]:
        async with httpx.AsyncClient(transport=transport) as http:
            yield http

    @asynccontextmanager
    async def playwright() -> AsyncIterator[FakeRenderer | None]:
        browser.started.append(True)
        yield browser.renderer

    async def route(host: str, **_kwargs: object) -> mx.MailRoute:
        return (routes or {}).get(host, mx.MailRoute.MX)

    monkeypatch.setattr(file_sweep, "guarded_client", client)
    monkeypatch.setattr(file_sweep, "PlaywrightRenderer", playwright)
    monkeypatch.setattr("backend.features.contacts.ladder.mail_route", route)
    return browser


def page(body: str) -> str:
    return f"<html><body>{body}</body></html>"


def write(path: Path, text: str, *, encoding: str = "utf-8") -> Path:
    path.write_text(text, encoding=encoding)
    return path


def cli_args(source: Path, *extra: str) -> argparse.Namespace:
    """Доводы команды так, как их разберёт настоящая командная строка."""
    return build_parser().parse_args(["contacts-file", str(source), *extra])
