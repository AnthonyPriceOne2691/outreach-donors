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
- токен CI получал права репозитория по умолчанию;
- контейнеры держали права ядра по умолчанию, база и очередь жили в сети с выходом наружу,
  а образы шли по тегу, который издатель передвигает (аудит 20.11.2026).
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


#: Внешний образ: тег для человека и дайджест индекса для докера.
PINNED = re.compile(r"[\w.\-/]+:[\w.\-]+@sha256:[0-9a-f]{64}")


def _backend() -> dict[str, dict[str, Any]]:
    """Сервисы из образа сервера: на сервере образ — из реестра, локально — из сборки."""
    return {
        name: service
        for name, service in _compose()["services"].items()
        if str(service.get("image", "")).startswith("${BACKEND_IMAGE")
    }


def test_no_container_gains_privileges() -> None:
    """Ни setuid-файл, ни capabilities файла не дают процессу прав больше, чем на старте."""
    services = _compose()["services"]

    lacking = [
        name
        for name, service in services.items()
        if "no-new-privileges:true" not in (service.get("security_opt") or [])
    ]
    assert lacking == [], f"у {lacking} нет no-new-privileges"
    assert [name for name, service in services.items() if service.get("privileged")] == []


def test_backend_containers_hold_no_kernel_capabilities() -> None:
    """Сервер и воркеры работают от `app`: прав ядра им не нужно, и докер их не даёт."""
    backend = _backend()
    text = DOCKERFILE.read_text(encoding="utf-8")
    stage = text.split("AS backend\n", 1)[1].split("\nFROM ", 1)[0]

    assert re.search(r"^USER app$", stage, re.M), "без USER app снятые права достаются root"
    assert {"migrate", "api", "worker", "crawler", "reaper", "followups"} <= set(backend)
    for name, service in backend.items():
        assert service.get("cap_drop") == ["ALL"], name
        assert "cap_add" not in service, name


#: Права образов, которые стартуют от root и сбрасывают их сами, — ровно те, без которых они
#: не встают: проверено живым запуском на новом и на прежнем томе (аудит 20.11.2026).
ROOT_STARTED = {
    "postgres": ["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"],
    "redis": ["CHOWN", "SETGID", "SETUID"],
    "web": ["CHOWN", "NET_BIND_SERVICE", "SETGID", "SETUID"],
}


def test_root_started_containers_keep_only_what_they_need() -> None:
    """Новое право — решение с проверкой, а не строка, дописанная, «чтобы завелось»."""
    services = _compose()["services"]

    assert set(services) == set(_backend()) | set(ROOT_STARTED)
    for name, capabilities in ROOT_STARTED.items():
        assert services[name]["cap_drop"] == ["ALL"], name
        assert services[name]["cap_add"] == capabilities, name


def test_the_database_and_the_queue_live_in_the_internal_network_only() -> None:
    """Базе и очереди нет пути наружу, а к ним — из сети фронта; серверу нужны обе сети."""
    compose = _compose()
    services = compose["services"]

    assert compose["networks"]["data"] == {"internal": True}
    for name in ("postgres", "redis"):
        assert services[name]["networks"] == ["data"], name
        assert "ports" not in services[name], name
    for name, service in _backend().items():
        assert sorted(service["networks"]) == ["data", "default"], name
    assert "networks" not in services["web"], "фронту хватает сети по умолчанию — он ходит к api"


def test_the_prod_overlay_does_not_loosen_the_containers() -> None:
    """Боевой файл накладывается сверху: сети и права в нём перекрыли бы основной."""
    prod = PROD.read_text(encoding="utf-8")

    keys = r"networks|network_mode|security_opt|cap_add|cap_drop|privileged|image"
    assert re.findall(rf"^\s*({keys})\s*:", prod, re.M) == []


def test_external_images_are_pinned_by_digest() -> None:
    """Тег издатель передвигает, дайджест — нет: проверенная сегодня база не меняется завтра.
    Свои образы (`BACKEND_IMAGE`, `WEB_IMAGE`) выкатываются меткой по коммиту — их не трогаем."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    stages = set(re.findall(r"^FROM\s+\S+\s+AS\s+(\S+)", text, re.M))
    bases = [image for image in re.findall(r"^FROM\s+(\S+)", text, re.M) if image not in stages]
    images = [
        str(service["image"])
        for service in _compose()["services"].values()
        if not str(service["image"]).startswith("${")
    ]

    assert len(bases) >= 2, bases
    assert sorted(image.split(":", 1)[0] for image in images) == ["postgres", "redis"]
    assert [image for image in bases + images if not PINNED.fullmatch(image)] == []


def test_ci_runs_the_backend_image_with_the_services_restrictions() -> None:
    """Миграции и проверка роли в CI — с правами сервисов компоуза: CI доказывает, что образ
    живёт без прав ядра и без новых привилегий, а не только компоуз их называет."""
    migrate = _compose()["services"]["migrate"]
    flags = [f"--cap-drop {cap}" for cap in migrate["cap_drop"]]
    flags += [f"--security-opt {option}" for option in migrate["security_opt"]]
    ci = yaml.safe_load((WORKFLOWS / "ci.yml").read_text(encoding="utf-8"))
    step = next(
        step
        for step in ci["jobs"]["docker"]["steps"]
        if str(step.get("name", "")).startswith("Миграции на чистой базе")
    )
    runs = re.sub(r"\\\n\s*", " ", step["run"]).split("docker run ")[1:]

    assert len(runs) == 2
    for command in runs:
        assert all(flag in command for flag in flags), command
