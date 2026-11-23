"""Без пропуска — отказ «входа нет» на каждом маршруте API, кроме открытых (аудит 19.11.2026).

Таблицы прав разделов берут маршруты по префиксу раздела, и общие разделы без таблицы —
агент, журнал, сторож, настройки, расход — держал только префикс. Здесь обход всей схемы
приложения: маршрут, заведённый завтра без зависимости входа, роняет тест сам, без строки
в списке. Отказ — 403 с признаком `signed-out` (`access/permissions.SignedOutError`): по нему
экран ведёт на вход, а 401 в сервисе отвечает только прокси (`test_api_auth.py`).

Открыты без пропуска девять операций — сверено по коду маршрутов: у них нет зависимости
`signed_in`, и у каждой своя защита или нечего защищать. Список — полный: новый открытый
маршрут без строки здесь роняет обход, строка без маршрута — сверку списка.
"""

from __future__ import annotations

import re

from backend.api.errors import ACCESS_REFUSAL
from backend.features.access.permissions import SignedOutError
from backend.features.access.tokens import TOKEN_HEADER
from fastapi import FastAPI
from httpx import AsyncClient

#: Операции без пропуска — и почему это безопасно.
OPEN = {
    ("GET", "/api/health"): "живость для проверки докера: ответ без данных",
    ("POST", "/api/auth/login"): "вход: пропуска ещё нет, частоту режут прокси и счётчик",
    # Второй шаг входа (23.11.2026): пропуска ещё нет — его и выдаёт шаг; вместо пропуска —
    # подписанный вызов своего шага, выданный верной паре (`access/challenge.py`).
    ("POST", "/api/auth/login/code"): "второй шаг: вызов входа, счётчик попыток и пауза учётки",
    ("POST", "/api/auth/login/enroll"): "обязательное включение кода: вызов входа своего шага",
    ("POST", "/api/auth/login/enroll/confirm"): "первый код по вызову: счётчик и пауза учётки",
    ("POST", "/api/inbound/replies"): "вебхук ответов — секрет вебхука",
    ("POST", "/api/events/delivery"): "события доставки — подпись платформы",
    ("GET", "/api/unsubscribe/{label}"): "страница отписки — подписанная метка в ссылке",
    ("POST", "/api/unsubscribe/{label}"): "отписка — подписанная метка в ссылке",
}

#: Сколько закрытых операций в схеме на сегодня: снизу — чтобы сломанный обход не прошёл молча.
AT_LEAST = 200


def _operations(app: FastAPI) -> list[tuple[str, str]]:
    """Метод и путь каждой операции схемы — с подстановками, как в схеме."""
    return sorted(
        (method.upper(), path)
        for path, methods in app.openapi()["paths"].items()
        for method in methods
    )


def _url(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "1", path)


def test_every_open_operation_is_a_real_route(api_app: FastAPI) -> None:
    """Строка списка без маршрута — пустое место, которое считается открытым: переименуют
    маршрут — список врёт, и сверка ниже судит не то."""
    missing = sorted(set(OPEN) - set(_operations(api_app)))

    assert missing == [], f"в схеме нет открытых операций {missing}"


async def test_without_a_pass_every_other_route_says_sign_in(
    api_app: FastAPI, client: AsyncClient
) -> None:
    """Запрос без пропуска — ни прав, ни разбора тела, ни строки базы: отказ входа раньше
    всего. Тело пустое: зависимость входа стоит раньше разбора, и до тела дело не доходит."""
    walked: list[str] = []
    answered: dict[str, str] = {}
    for method, path in _operations(api_app):
        if (method, path) in OPEN:
            continue
        body = None if method == "GET" else {}
        response = await client.request(method, _url(path), json=body)
        walked.append(f"{method} {path}")
        refusal = response.headers.get(ACCESS_REFUSAL)
        if response.status_code != 403 or refusal != SignedOutError.refusal:
            answered[f"{method} {path}"] = f"{response.status_code} {refusal or '-'}"

    assert len(walked) >= AT_LEAST, f"обход прошёл мимо маршрутов: {len(walked)}"
    assert answered == {}, f"без пропуска ответили не отказом входа: {answered}"


async def test_the_refusal_names_the_header_to_send(client: AsyncClient) -> None:
    """Отказ — словами, с именем заголовка: экрану и человеку с консолью понятно, чего нет."""
    response = await client.get("/api/journal")

    assert response.status_code == 403
    assert response.headers[ACCESS_REFUSAL] == SignedOutError.refusal
    assert TOKEN_HEADER in response.json()["detail"]
