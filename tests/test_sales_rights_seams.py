"""Права раздела «Продажи» — обходом всего приложения: без права «Продажи» и у телемаркетолога.

Ревью стыков, круг 2 (экраны и права). Тесты вкладок держат права своих маршрутов таблицами
(`test_sales_write_rights.py` и тесты каждой вкладки); здесь — обход схемы приложения целиком:
маршрут раздела, который новая вкладка завела без `needs(Permission.SALES)`, или запись, которую
телемаркетологу не положено делать, — красный тест, даже если тесты самой вкладки об этом не
спросили. Маршруты берутся из приложения, а не из списка: новый маршрут проверяется сам.

- **Без права «Продажи»** (админ, право снято поимённо, — все остальные права у него есть):
  каждый маршрут `/api/sales/…` — 403 словами о праве «Продажи» (`no_right`), с признаком отказа
  доступа (по нему экран перечитывает «кто я»), и в журнале от него — ничего, кроме входа.
- **Телемаркетолог** — оператор, у которого из прав только «Продажи» и «смотреть базу»: лида ему
  передают в Telegram и Kommo, а без сделки — ссылкой на переписку в сервисе
  (`sales/handoff_text.dialog_link`). Он читает каждую вкладку раздела; записи — по таблице
  `TELEMARKETER_WRITES`: что раздел пускает одним правом «Продажи», что требует «запускать
  прогоны» (платная проверка адресов, выдача и модель) и что — «отправлять письма» (всё, что
  уходит адресату). Переписку продаж открывает; ответить лиду и отправить черновик агента —
  нет: это «отправлять письма».

Номера строк в путях — `1`: строк нет, и маршрут с правом отвечает своим «не найдено» или
отказом разбора; без права до тела маршрута не доходит. Тела запросов нет: отказ в праве
стоит раньше разбора тела (зависимость маршрута), а с правом пустое тело — отказ разбора, не 403.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

from backend.api.errors import ACCESS_REFUSAL
from backend.features.access.permissions import no_right
from backend.features.core.domain import AuditAction, Permission, UserRole
from backend.features.core.models.access import AuditLogModel, UserModel
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_stage_access import World, world

__all__ = ["world"]  # фикстура мира — отсюда её видит pytest

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

SECTION = "/api/sales/"
OUTSIDER = "outsider@team.example.test"
TELEMARKETER = "telemarketer@team.example.test"

#: Телемаркетолог: оператор, у которого сняты «запускать прогоны», «править пороги» и
#: «подтверждать цены»; «отправлять письма» оператору не положено ролью. Остаются «Продажи»
#: и «смотреть базу».
TELEMARKETER_RIGHTS = {"run": False, "settings": False, "prices": False}

#: Записи раздела, которые телемаркетолог делает или нет, — каждая по своему праву. `None` —
#: одного права «Продажи» достаточно: загрузка базы, гипотеза (заведение, правка, удаление
#: пустой), описание сегмента, смета сбора, решение по сайту «на проверку» и его отмена и письмо
#: глазами адресата (предпросмотр ничего не пишет).
TELEMARKETER_WRITES: dict[tuple[str, str], Permission | None] = {
    ("POST", "/api/sales/import/preview"): None,
    ("POST", "/api/sales/import"): None,
    ("POST", "/api/sales/hypotheses"): None,
    ("PATCH", "/api/sales/hypotheses/{}"): None,
    ("DELETE", "/api/sales/hypotheses/{}"): None,
    ("POST", "/api/sales/segment/estimate"): None,
    # Помощник описания сегмента: описание правит и телемаркетолог — спросить помощника может тоже.
    ("POST", "/api/sales/segment/helper"): None,
    ("POST", "/api/sales/segment/candidates/{}/decision"): None,
    # Отмена решения по сайту — как само решение: одного права «Продажи».
    ("DELETE", "/api/sales/segment/candidates/{}/decision"): None,
    ("POST", "/api/sales/chain/preview"): None,
    ("POST", "/api/sales/clean"): Permission.RUN,
    ("POST", "/api/sales/segment/runs"): Permission.RUN,
    ("POST", "/api/sales/segment/runs/{}/resume"): Permission.RUN,
    ("POST", "/api/sales/segment/runs/{}/again"): Permission.RUN,
    ("POST", "/api/sales/segment/runs/{}/rejudge"): Permission.RUN,
    ("POST", "/api/sales/chain"): Permission.SEND,
    ("POST", "/api/sales/kb"): Permission.SEND,
    ("PATCH", "/api/sales/kb/{}"): Permission.SEND,
    ("POST", "/api/sales/sender"): Permission.SEND,
    ("POST", "/api/sales/queue"): Permission.SEND,
    ("PATCH", "/api/sales/leads/{}"): Permission.SEND,
    ("POST", "/api/sales/stoplist"): Permission.SEND,
    ("POST", "/api/sales/stoplist/{}/remove"): Permission.SEND,
}


def _shape(path: str) -> str:
    """Путь без имён подстановок: `/kb/{entry_id}` и `/kb/{id}` — один маршрут."""
    return re.sub(r"\{[^}]*\}", "{}", path)


def _section(app: FastAPI) -> list[tuple[str, str]]:
    """Маршруты раздела в схеме приложения: метод и путь с подстановками."""
    return sorted(
        (method.upper(), path)
        for path, methods in app.openapi()["paths"].items()
        if path.startswith(SECTION)
        for method in methods
    )


def _url(path: str) -> str:
    return re.sub(r"\{[^}]*\}", "1", path)


async def _signed(
    make_user: MakeUser, sign_in: SignIn, email: str, *, role: UserRole, rights: dict[str, bool]
) -> tuple[UserModel, dict[str, str]]:
    user = await make_user(email, role=role, permissions=rights)
    return user, bearer(await sign_in(email))


async def _journal_of(session: AsyncSession, user: UserModel) -> list[AuditAction]:
    found = await session.scalars(
        select(AuditLogModel.action).where(
            AuditLogModel.user_id == user.id, AuditLogModel.action != AuditAction.LOGIN
        )
    )
    return list(found)


# --- без права «Продажи» ----------------------------------------------------------------------


async def test_the_section_has_routes_to_walk(api_app: FastAPI) -> None:
    """Обход ниже не пуст: раздел смонтирован, и его маршрутов не меньше вкладок."""
    assert len(_section(api_app)) >= len(TELEMARKETER_WRITES)


async def test_without_the_right_every_route_of_the_section_is_refused_in_words(
    api_app: FastAPI,
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    sign_in: SignIn,
) -> None:
    user, headers = await _signed(
        make_user, sign_in, OUTSIDER, role=UserRole.ADMIN, rights={"sales": False}
    )

    let_through = {}
    for method, path in _section(api_app):
        answer = await client.request(method, _url(path), headers=headers)
        said = answer.json().get("detail") if answer.status_code == 403 else None
        refusal = answer.headers.get(ACCESS_REFUSAL)
        if (answer.status_code, said, refusal) != (403, no_right(Permission.SALES), "permission"):
            let_through[f"{method} {path}"] = (answer.status_code, answer.text[:120])

    assert let_through == {}
    assert await _journal_of(session, user) == []


# --- телемаркетолог: «Продажи» и «смотреть базу» ---------------------------------------------


async def test_the_telemarketer_reads_every_tab_of_the_section(
    api_app: FastAPI, client: AsyncClient, make_user: MakeUser, sign_in: SignIn
) -> None:
    """Чтение раздела — одним правом «Продажи»: ни одна вкладка не отказывает ему в праве."""
    _, headers = await _signed(
        make_user, sign_in, TELEMARKETER, role=UserRole.OPERATOR, rights=TELEMARKETER_RIGHTS
    )

    refused = {}
    for method, path in _section(api_app):
        if method != "GET":
            continue
        answer = await client.get(_url(path), headers=headers)
        if answer.status_code in (401, 403):
            refused[path] = answer.text[:160]

    assert refused == {}


async def test_the_table_of_the_telemarketer_covers_every_write_of_the_section(
    api_app: FastAPI,
) -> None:
    """Новая запись раздела не уйдёт мимо таблицы: решать, может ли её телемаркетолог, — тут."""
    writes = {(method, _shape(path)) for method, path in _section(api_app) if method != "GET"}

    assert writes == set(TELEMARKETER_WRITES)


async def test_the_telemarketer_writes_only_what_the_section_alone_allows(
    api_app: FastAPI,
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    sign_in: SignIn,
) -> None:
    """Запись под «запускать прогоны» или «отправлять письма» — 403 словами о своём праве;
    запись под одним «Продажи» — не отказ в праве (пустое тело — отказ разбора или «нет»)."""
    user, headers = await _signed(
        make_user, sign_in, TELEMARKETER, role=UserRole.OPERATOR, rights=TELEMARKETER_RIGHTS
    )

    got: dict[str, str] = {}
    want: dict[str, str] = {}
    for method, path in _section(api_app):
        if method == "GET":
            continue
        needed = TELEMARKETER_WRITES[(method, _shape(path))]
        answer = await client.request(method, _url(path), headers=headers)
        said = answer.json().get("detail") if answer.status_code == 403 else None
        got[f"{method} {path}"] = said if isinstance(said, str) else "не отказ в праве"
        want[f"{method} {path}"] = "не отказ в праве" if needed is None else no_right(needed)

    assert got == want
    assert await _journal_of(session, user) == []


async def test_the_telemarketer_opens_a_sales_dialog_but_does_not_write_to_the_lead(
    client: AsyncClient, world: World, make_user: MakeUser, sign_in: SignIn
) -> None:
    """Ссылка из сообщения о лиде ведёт в переписку продаж — она открывается, и диалог есть в
    списке продаж. Ответ лиду и отправка черновика агента — «отправлять письма», которого у
    телемаркетолога нет: отказ называет это право."""
    _, headers = await _signed(
        make_user, sign_in, TELEMARKETER, role=UserRole.OPERATOR, rights=TELEMARKETER_RIGHTS
    )

    dialog = await client.get(f"/api/threads/{world.sales_thread}", headers=headers)
    listed = await client.get("/api/threads", headers=headers)
    answer = await client.post(
        f"/api/threads/{world.sales_thread}/answer",
        json={"reply_id": world.sales_reply, "body": "Thanks"},
        headers=headers,
    )
    draft = await client.post(
        f"/api/agent/drafts/{world.sales_draft}/send", json={}, headers=headers
    )

    assert dialog.status_code == 200, dialog.text
    assert world.sales_thread in {row["id"] for row in listed.json()}
    assert (answer.status_code, answer.json()["detail"]) == (403, no_right(Permission.SEND))
    assert (draft.status_code, draft.json()["detail"]) == (403, no_right(Permission.SEND))
