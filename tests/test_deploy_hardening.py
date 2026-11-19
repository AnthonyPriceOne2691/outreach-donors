"""Выкатка без лишнего наружу (аудит 19.11.2026): журналы, порт, кэш, токен CI.

Каждая строка здесь снимается одной неосторожной правкой конфига, и ни одну не видно в работе —
сервис отвечает как прежде:
- журналы запросов сервера и nginx контейнера писали путь с параметрами — метку отписки и
  поиск лидов по почте и имени; теперь запросы пишет один журнал — прокси хоста
  (`tests/test_deploy_proxy.py`);
- выкатка убивала сервер через 10 с, посреди запроса;
- базовый компоуз публиковал экран на всех адресах машины: `up` без боевого файла открывал
  сервис по http мимо TLS и пароля прокси, а порт докера фаервол машины не закрывает;
- ответы API ложились в кэш браузера, и сервис не просил поисковики его не индексировать;
- токен CI получал права репозитория по умолчанию.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml
from tests.test_deploy_headers import INCLUDE, NGINX, SNIPPET, _code, _locations

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"
COMPOSE = ROOT / "docker-compose.yml"
PROD = ROOT / "docker-compose.prod.yml"
WORKFLOWS = ROOT / ".github" / "workflows"
LOOPBACK = "127.0.0.1:${WEB_PORT:-8090}:80"


def _compose() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    return loaded


def _server_command() -> list[str]:
    """CMD образа сервера — JSON-форма из Dockerfile, строки с `\\` склеены."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    stage = text.split("AS backend\n", 1)[1].split("\nFROM ", 1)[0]
    found = re.search(r"^CMD\s+(\[.*\])\s*$", re.sub(r"\\\n\s*", " ", stage), re.M)
    assert found is not None, "у образа сервера нет CMD в JSON-форме"
    command: list[str] = json.loads(found.group(1))
    return command


def test_the_server_keeps_no_request_log_and_finishes_requests_on_stop() -> None:
    command = _server_command()
    api = _compose()["services"]["api"]

    assert command[:2] == ["uvicorn", "backend.api.main:app"]
    assert "--no-access-log" in command
    assert "command" not in api, "команда сервера — из образа: иначе флаги выше не действуют"
    finish = int(command[command.index("--timeout-graceful-shutdown") + 1])
    wait = re.fullmatch(r"(\d+)s", str(api["stop_grace_period"]))
    assert wait is not None, api["stop_grace_period"]
    assert finish < int(wait.group(1)), "докер обязан ждать дольше, чем сервер дописывает"


def test_the_web_container_keeps_no_request_log() -> None:
    conf = _code(NGINX.read_text(encoding="utf-8"))

    assert re.search(r"^\s*access_log\s+off;", conf.split("location", 1)[0], re.M)
    assert re.findall(r"^\s*access_log\s+(\S+)", conf, re.M) == ["off;"], "журнал включён снова"


def test_the_screen_is_published_on_loopback_only() -> None:
    """И в базовом файле, и в боевом: забытый `-f docker-compose.prod.yml` — не сервис наружу."""
    published = [
        (name, str(port))
        for name, service in _compose()["services"].items()
        for port in service.get("ports") or []
    ]

    assert published == [("web", LOOPBACK)]
    # Боевой файл несёт тег компоуза `!override` — YAML без него не читается.
    assert f'- "{LOOPBACK}"' in PROD.read_text(encoding="utf-8")


def test_api_answers_are_never_cached() -> None:
    api = {
        place: body
        for place, body in _locations(_code(NGINX.read_text(encoding="utf-8"))).items()
        if place.startswith("/api/")
    }

    assert set(api) == {"/api/", "/api/inbound/"}
    for place, body in api.items():
        assert re.search(r'^\s*add_header\s+Cache-Control\s+"no-store"\s+always;', body, re.M), (
            place
        )
        assert INCLUDE in body, f"{place}: свой add_header без сниппета снял бы общие заголовки"


def test_the_service_asks_not_to_be_indexed() -> None:
    snippet = _code(SNIPPET.read_text(encoding="utf-8"))

    assert re.search(r'^add_header\s+X-Robots-Tag\s+"noindex, nofollow"\s+always;', snippet, re.M)


def test_the_ci_token_only_reads() -> None:
    """Верхним уровнем — только чтение кода. Публикации образов нужно больше: права джобы
    `docker` свои и заменяют верхние целиком; другим джобам своих прав не положено."""
    for name in ("ci.yml", "quality.yml"):
        workflow = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
        assert workflow["permissions"] == {"contents": "read"}, name
        own = {
            job: spec["permissions"]
            for job, spec in workflow["jobs"].items()
            if "permissions" in spec
        }
        if name == "ci.yml":
            assert own == {"docker": {"contents": "read", "packages": "write"}}
        else:
            assert own == {}, own
