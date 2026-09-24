"""Заголовки безопасности внутреннего nginx: две ловушки, которые не видны глазом.

Обе ломают молча. Первая — правка встроенного скрипта в `index.html`: CSP
пускает его только по хешу содержимого, и новый текст без нового хеша
браузер просто не исполнит (тема перестанет вставать до React, в консоли —
ошибка, которую никто не читает). Вторая — наследование `add_header`: блок
`location` со своим `add_header` теряет ВСЕ заголовки уровня `server`,
и забытый `include` снимает защиту с целого адреса без единой ошибки.
"""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NGINX = ROOT / "deploy" / "nginx.conf"
SNIPPET = ROOT / "deploy" / "security-headers.conf"
INDEX = ROOT / "frontend" / "index.html"
DOCKERFILE = ROOT / "Dockerfile"
INCLUDE = "include /etc/nginx/snippets/security-headers.conf;"


def _inline_script_hashes(html: str) -> list[str]:
    """CSP-хеши встроенных скриптов — ровно так, как их считает браузер:
    sha256 от текста между тегами, байт в байт, вместе с отступами."""
    hashes = []
    for attrs, body in re.findall(r"<script([^>]*)>(.*?)</script>", html, re.S):
        if "src=" in attrs:
            continue
        digest = hashlib.sha256(body.encode("utf-8")).digest()
        hashes.append(f"'sha256-{base64.b64encode(digest).decode()}'")
    return hashes


def _locations(conf: str) -> dict[str, str]:
    """Тела блоков `location` — их у нас без вложенности."""
    return {
        match.group(1).strip(): match.group(2)
        for match in re.finditer(r"location\s+([^{]+)\{([^}]*)\}", conf)
    }


def _code(text: str) -> str:
    """Конфиг без комментариев: в них слова `add_header` и `include` законны."""
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def test_every_inline_script_is_allowed_by_csp() -> None:
    conf = _code(NGINX.read_text(encoding="utf-8"))
    csp = re.search(r'Content-Security-Policy\s+"([^"]+)"', conf)
    assert csp is not None, "CSP пропала из deploy/nginx.conf"
    for expected in _inline_script_hashes(INDEX.read_text(encoding="utf-8")):
        assert expected in csp.group(1), (
            f"встроенный скрипт в frontend/index.html изменился: браузер его не исполнит. "
            f"Заменить хеш в script-src deploy/nginx.conf на {expected}"
        )


def test_csp_allows_no_inline_scripts_wholesale() -> None:
    """'unsafe-inline' в script-src отменил бы весь смысл: пропуск лежит
    в localStorage, и любой подброшенный скрипт унёс бы его."""
    csp = re.search(r'Content-Security-Policy\s+"([^"]+)"', _code(NGINX.read_text("utf-8")))
    assert csp is not None
    script_src = next(part for part in csp.group(1).split(";") if "script-src" in part)
    assert "'unsafe-inline'" not in script_src
    assert "'unsafe-eval'" not in script_src


def test_location_with_own_headers_keeps_the_common_ones() -> None:
    conf = _code(NGINX.read_text(encoding="utf-8"))
    server_level = conf.split("location", 1)[0]
    assert INCLUDE in server_level, "общие заголовки должны стоять на уровне server"
    lost = [
        path
        for path, body in _locations(conf).items()
        if "add_header" in body and INCLUDE not in body
    ]
    assert lost == [], (
        f"у location {lost} свой add_header без include сниппета — nginx молча "
        "снимет с этих адресов все заголовки безопасности"
    )


def test_snippet_reaches_the_image() -> None:
    assert SNIPPET.exists()
    assert "/etc/nginx/snippets/security-headers.conf" in DOCKERFILE.read_text(encoding="utf-8")


def test_headers_survive_error_responses() -> None:
    """Без `always` заголовки стоят только на 2xx/3xx, а страница ошибки
    — такая же страница, и встроить её в чужой сайт так же можно."""
    for line in _code(SNIPPET.read_text(encoding="utf-8")).splitlines():
        if line.strip().startswith("add_header"):
            assert line.rstrip().endswith("always;"), line


def test_api_address_is_resolved_per_request() -> None:
    """`proxy_pass http://api:8000` запоминает адрес сервера при старте nginx:
    выкатка, пересоздавшая только api, даёт 502 на всё API до перезапуска web
    (воспроизведено 24.09.2026). Адрес — только через переменную и resolver."""
    conf = _code(NGINX.read_text(encoding="utf-8"))
    assert re.search(r"resolver\s+127\.0\.0\.11\b", conf), "нет resolver DNS докера"
    fixed = re.findall(r"proxy_pass\s+https?://[^;]+;", conf)
    assert fixed == [], f"адрес сервера зашит в proxy_pass: {fixed} — через $api_upstream"
