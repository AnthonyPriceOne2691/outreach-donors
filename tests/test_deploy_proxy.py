"""Прокси хоста (`deploy/proxy/outreach.conf`): пароль прокси — на всём сервисе, кроме трёх адресов.

С 11.10.2026 за паролем прокси и API, а не только экран: код публичный, адреса API известны,
и вход в API был единственным замком перед данными. Пароль стоит на уровне server — новый
адрес закрыт сам. Открыты только адреса, куда ходят не люди команды, и у каждого своя защита:
приём ответов (секрет вебхука), события доставки (подпись платформы), отписка (метка в ссылке).

Ловушки, которые глазом не видны:
- забытый `auth_basic off` у вебхука молча отбивает ответы доноров и события доставки;
- лишний — открывает адрес, у которого своей защиты нет;
- свой `proxy_set_header` в блоке `location` снимает ВСЕ заголовки уровня server, в том числе
  перезапись адреса клиента, на которой держится счётчик попыток входа.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI

ROOT = Path(__file__).resolve().parent.parent
PROXY = ROOT / "deploy" / "proxy" / "outreach.conf"

#: Открыто без пароля прокси — и почему это безопасно.
PUBLIC = {
    "/api/inbound/": "вебхук ответов — секрет вебхука",
    "/api/events/": "события доставки — подпись платформы",
    "/api/unsubscribe/": "отписка — метка в ссылке из письма",
}


def _code(text: str) -> str:
    """Конфиг без комментариев: в них слова директив законны."""
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _outreach_server() -> str:
    """Блок server сервиса — первый в файле; блоки location в нём без вложенности."""
    conf = _code(PROXY.read_text(encoding="utf-8"))
    start = conf.index("server {")
    depth = 0
    for at in range(start, len(conf)):
        depth += {"{": 1, "}": -1}.get(conf[at], 0)
        if depth == 0 and conf[at] == "}":
            return conf[start : at + 1]
    raise AssertionError("в deploy/proxy/outreach.conf не закрыт блок server")


def _locations(server: str) -> dict[str, str]:
    return {
        match.group(1).strip(): match.group(2)
        for match in re.finditer(r"location\s+([^{]+)\{([^}]*)\}", server)
    }


def _server_level(server: str) -> str:
    """Директивы самого server — без тел location."""
    return re.sub(r"location\s+[^{]+\{[^}]*\}", "", server)


def test_the_whole_service_is_behind_the_proxy_password() -> None:
    level = _server_level(_outreach_server())

    assert re.search(r'^\s*auth_basic\s+"[^"]+";', level, re.M), (
        "нет пароля прокси на уровне server"
    )
    assert re.search(r"^\s*auth_basic_user_file\s+\S+;", level, re.M)


def test_only_the_three_public_addresses_are_open() -> None:
    opened = {
        place.split()[-1]
        for place, body in _locations(_outreach_server()).items()
        if re.search(r"^\s*auth_basic\s+off;", body, re.M)
    }

    assert opened == set(PUBLIC), f"без пароля прокси: {sorted(opened)}, ждали {sorted(PUBLIC)}"


def test_every_open_address_is_a_real_route(api_app: FastAPI) -> None:
    """Открытый адрес — тот, по которому сервер правда отвечает: переименуют маршрут —
    вебхук упрётся в пароль, а открытым останется пустое место."""
    # Схема API, а не `app.routes`: с FastAPI 0.141 включённые роутеры там ленивые и путей не несут.
    paths = list(api_app.openapi()["paths"])

    for prefix, why in PUBLIC.items():
        assert any(path.startswith(prefix) for path in paths), f"нет маршрута {prefix} ({why})"


def test_no_location_drops_the_server_proxy_headers() -> None:
    """Свой `proxy_set_header` в location снимает перезапись X-Forwarded-For уровня server —
    и счётчик попыток входа обходится сменой заголовка (проверено 24.09.2026)."""
    for place, body in _locations(_outreach_server()).items():
        assert "proxy_set_header" not in body, f"location {place} задаёт свои заголовки прокси"
