"""CSP экрана (`deploy/nginx.conf`) — по директивам, а не одним `script-src` (аудит 19.11.2026).

`tests/test_deploy_headers.py` сторожит хеш встроенного скрипта и запрет 'unsafe-inline'
в `script-src`, а остальное держалось на внимании: правка, снявшая `object-src 'none'` или
`frame-ancestors`, прошла бы зелёной. Политика разбирается на директивы, и у каждой
обязательной — ровно нужное значение: лишний источник в ней — тоже снятая защита.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

NGINX = Path(__file__).resolve().parent.parent / "deploy" / "nginx.conf"

#: Директива → её значение, и чего без неё лишаемся.
REQUIRED = {
    # Всё, что не названо отдельно, — только со своего адреса.
    "default-src": ("'self'",),
    # Ни плагинов, ни <object>/<embed>: через них скрипт обходит script-src.
    "object-src": ("'none'",),
    # Подставленный <base> не уведёт относительные адреса скриптов на чужой сервер.
    "base-uri": ("'self'",),
    # Форма не отправит введённое (пароль, данные) на чужой адрес.
    "form-action": ("'self'",),
    # Страницу не встроить в чужую — защита от подставных кнопок поверх неё.
    "frame-ancestors": ("'none'",),
}


def _code(text: str) -> str:
    """Конфиг без комментариев: в них слова директив законны."""
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _policies() -> list[str]:
    return re.findall(r'Content-Security-Policy\s+"([^"]+)"', _code(NGINX.read_text("utf-8")))


def _directives(policy: str) -> dict[str, tuple[str, ...]]:
    """Политика → директива и её источники. Повтор директивы браузер не складывает, а берёт
    первую — повтор здесь отказ, а не молчаливое «второе значение»."""
    found: dict[str, tuple[str, ...]] = {}
    for part in policy.split(";"):
        words = part.split()
        if not words:
            continue
        name, *sources = words
        assert name not in found, f"директива {name} в CSP дважды — браузер возьмёт первую"
        found[name] = tuple(sources)
    return found


def test_the_screen_has_one_policy() -> None:
    assert len(_policies()) == 1, "CSP в deploy/nginx.conf должна быть одна — у экрана"


@pytest.mark.parametrize(("directive", "sources"), REQUIRED.items(), ids=list(REQUIRED))
def test_required_directive_has_exactly_its_value(directive: str, sources: tuple[str]) -> None:
    [policy] = _policies()

    found = _directives(policy)

    assert directive in found, f"в CSP нет {directive}"
    assert found[directive] == sources, f"{directive}: {found[directive]}, ждали {sources}"


def test_no_directive_lets_a_foreign_script_in() -> None:
    """Источники, при которых чужой код исполняется: любой адрес, `data:` и `blob:` в
    script-src, 'unsafe-eval' где угодно. Пропуск лежит в localStorage — скрипт унёс бы его."""
    [policy] = _policies()

    found = _directives(policy)

    script = found.get("script-src", found["default-src"])
    assert not {"*", "data:", "blob:", "http:", "https:"} & set(script), script
    assert all("'unsafe-eval'" not in sources for sources in found.values())
