"""Имя присланного файла — одним правилом у загрузки базы и стоп-листа (`features/sales/upload.py`).

Имя приходит от клиента как есть: `..`, путь, знаки вне имён файлов, длина за пределом файловой
системы. Стоп-лист клал байты по имени почти как есть: `..` уводил запись в каталог выше
(`IsADirectoryError`), длинное имя давало `ENAMETOOLONG` — ответ 500 мимо слов отказа. Загрузка
базы резала имя по знакам, а не по байтам: сто иероглифов — 400 байт, за пределом имени у ext4.
Здесь — правило само и оба пути экрана. Имена и адреса выдуманы.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
import pytest
from backend.features.core.models.access import UserModel
from backend.features.sales import stoplist, upload
from backend.features.sales.models import SalesStoplistModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

SELLER = "seller@ours.example.test"
FALLBACK = "запасное.csv"
#: Буква за пределом основной плоскости: четыре байта в UTF-8, два знака в UTF-16.
WIDE = "\U00020000"
#: Имена, которые клиент присылает как есть: каталог выше, иероглифы за пределом имени файла
#: у любой файловой системы, кириллица за 255 байтами ext4 и длиннее колонки причины.
ODD = {
    "каталог выше": "..",
    "иероглифы": WIDE * 130 + ".csv",
    "кириллица": "партнёры-агентства-" * 15 + ".csv",
}


async def _headers(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user(SELLER, permissions={"send": True})
    return bearer(await sign_in(SELLER))


# --- правило ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("..", FALLBACK),
        (".", FALLBACK),
        ("", FALLBACK),
        ("../../etc/passwd", "passwd"),
        (".env", "env"),
        ("C:\\fakepath\\база клиентов (2).csv", "C__fakepath_база клиентов _2_.csv"),
        ("list\x00.csv", "list_.csv"),
    ],
    ids=["..", ".", "пусто", "путь", "точка в начале", "путь Windows", "нулевой знак"],
)
def test_the_name_is_the_last_part_without_leading_dots_and_odd_signs(
    name: str, expected: str
) -> None:
    """Мутант «без `.lstrip(".")`»: `..` осталось бы именем — каталогом выше."""
    assert upload.file_name(name, FALLBACK) == expected


@pytest.mark.parametrize(
    "name", [WIDE * 130 + ".csv", "я" * 300 + ".csv"], ids=["иероглифы", "кириллица"]
)
def test_a_long_name_is_cut_by_bytes_without_breaking_a_letter(name: str) -> None:
    """Мутант «обрезка по знакам, как была у загрузки базы»: сто иероглифов — 400 байт."""
    cut = upload.file_name(name, FALLBACK)

    assert len(cut.encode()) <= upload.NAME_BYTES
    assert name.startswith(cut)


# --- оба пути экрана --------------------------------------------------------------------------


@pytest.mark.parametrize("name", list(ODD.values()), ids=list(ODD))
async def test_stoplist_file_with_any_name_is_read_instead_of_failing(
    client: httpx.AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    sign_in: SignIn,
    name: str,
) -> None:
    """Мутант «стоп-лист со своим правилом имени, как было»: `..` — `IsADirectoryError`, длинное
    имя — `ENAMETOOLONG`, ответ 500. Без причины причиной ложится имя файла — не длиннее колонки
    причины: имя выбирал не человек у поля, отказывать ему за длину не за что."""
    headers = await _headers(make_user, sign_in)
    sent = {"file": (name, b"acme.example.test\n", "text/csv")}

    response = await client.post("/api/sales/stoplist", headers=headers, files=sent)

    assert response.status_code == 200, response.text
    assert response.json()["added"] == 1
    notes = await session.scalars(select(SalesStoplistModel.note))
    assert list(notes) == [name[: stoplist.NOTE_LENGTH]]


@pytest.mark.parametrize("name", list(ODD.values()), ids=list(ODD))
async def test_base_file_with_any_name_is_previewed_under_a_name_within_the_limit(
    client: httpx.AsyncClient, make_user: MakeUser, sign_in: SignIn, name: str
) -> None:
    """Имя, которое назовут отказы читателя, — по правилу. Мутант «обрезка по знакам, как была»:
    имя из ста иероглифов — 400 байт; на машине с APFS запрос прошёл бы, на сервере с ext4 — 500."""
    headers = await _headers(make_user, sign_in)
    sent = {"file": (name, "Почта\nivan@acme.example.test\n".encode(), "text/csv")}

    response = await client.post("/api/sales/import/preview", headers=headers, files=sent)

    assert response.status_code == 200, response.text
    source = response.json()["source"]
    assert source == upload.file_name(name, "база.csv")
    assert len(source.encode()) <= upload.NAME_BYTES
