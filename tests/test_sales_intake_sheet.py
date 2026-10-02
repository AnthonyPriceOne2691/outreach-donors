"""Google-таблица по ссылке — срез 1.3, примеры A5 и A9. Сети нет: ответы Google
подменяет транспорт httpx. Формы ответов — по замеру 02.10.2026 (закрытая таблица —
401 и HTML, таблицы нет — 404, листа нет — 400, открытая — 307 и 200 `text/csv`) и
по уроку L10 соседнего проекта: закрытая отвечала страницей входа со статусом 200.
Здесь же байты с экрана: они идут тем же путём, и имя файла — только имя.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
from backend.features.sales import intake, sheet

#: Выдуманный номер таблицы — длиной как настоящий, но без случайных знаков:
#: хук секретов принял бы случайную строку за ключ.
KEY = "1" + "sheet" * 8 + "abc"
LINK = f"https://docs.google.com/spreadsheets/d/{KEY}/edit#gid=42"
EXPORT = f"https://docs.google.com/spreadsheets/d/{KEY}/export?format=csv&gid=42"
AGAIN = "повторите позже или загрузите CSV"

Handler = Callable[[httpx.Request], httpx.Response]


def _http(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _refusal(response: httpx.Response, link: str = LINK) -> str:
    async with _http(lambda _: response) as http:
        with pytest.raises((sheet.SheetError, sheet.SheetUnavailableError)) as refused:
            await intake.read_link(link, http)
    return f"{type(refused.value).__name__}: {refused.value}"


async def test_open_sheet_is_read_like_a_file() -> None:
    asked: list[tuple[str, str]] = []

    def google(request: httpx.Request) -> httpx.Response:
        asked.append((request.method, str(request.url)))
        if request.url.host == "docs.google.com":
            location = "https://doc-0s.sheets.googleusercontent.com/export/1"
            return httpx.Response(307, headers={"location": location})
        csv = "Почта;Имя\nivan@acme.example.test;Иван\n"
        return httpx.Response(200, text=csv, headers={"content-type": "text/csv; charset=utf-8"})

    async with _http(google) as http:
        found = intake.preview(await intake.read_link(LINK, http))

    assert asked[0] == ("GET", EXPORT)
    assert (found.source, [(lead.email, lead.name) for lead in found.leads]) == (
        intake.SHEET_NAME,
        [("ivan@acme.example.test", "Иван")],
    )


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (401, b""),  # замер 02.10.2026: 401, тело — HTML; решает код
        (403, b""),
        (200, b"\n  <!DOCTYPE html><html><body>Sign in"),  # урок L10: страница входа, 200
        (200, b"<HTML><body>Sign in"),
    ],
)
async def test_a5_closed_sheet_is_a_refusal_in_words_not_an_empty_list(
    status: int, body: bytes
) -> None:  # A5
    assert await _refusal(httpx.Response(status, content=body)) == f"SheetError: {sheet.CLOSED}"


@pytest.mark.parametrize(
    ("status", "words"),
    [
        (404, f"таблица по ссылке не найдена — проверьте ссылку; если она верна, {sheet.CLOSED}"),
        (
            400,
            "в таблице нет листа из ссылки (gid=42) — откройте нужный лист и скопируйте ссылку заново",
        ),
    ],
)
async def test_missing_sheet_or_tab_has_its_own_words(status: int, words: str) -> None:
    assert (
        await _refusal(httpx.Response(status, content=b"<!DOCTYPE html>")) == f"SheetError: {words}"
    )


@pytest.mark.parametrize(
    ("failure", "words"),
    [
        (503, f"Google ответил кодом 503 — {AGAIN}"),
        (429, f"Google ответил кодом 429 — {AGAIN}"),
        ("сеть", f"Google не ответил (ConnectError) — {AGAIN}"),
    ],
)
async def test_a9_google_not_answering_is_not_a_closed_sheet(
    failure: int | str, words: str
) -> None:  # A9
    """Провайдер не ответил — другое действие, чем «откройте доступ»: повторить."""

    def google(request: httpx.Request) -> httpx.Response:
        if isinstance(failure, str):
            raise httpx.ConnectError("нет сети", request=request)
        return httpx.Response(failure)

    async with _http(google) as http:
        with pytest.raises(sheet.SheetUnavailableError) as refused:
            await intake.read_link(LINK, http)
    assert str(refused.value) == words


@pytest.mark.parametrize(
    "link",
    [
        f"https://sheets.example.test/spreadsheets/d/{KEY}/edit",
        "http://169.254.169.254/latest/meta-data",
        f"https://docs.google.com/document/d/{KEY}/edit",
        f"https://[docs.google.com/spreadsheets/d/{KEY}/edit",
        "таблица у менеджера",
    ],
)
async def test_not_a_sheet_link_is_refused_before_any_request(link: str) -> None:
    asked: list[httpx.Request] = []

    def google(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return httpx.Response(200)

    async with _http(google) as http:
        with pytest.raises(sheet.SheetError) as refused:
            await intake.read_link(link, http)
    words = f"«{link}» — не ссылка на Google-таблицу: ждём https://docs.google.com/spreadsheets/d/…"
    assert (str(refused.value), asked) == (words, [])


@pytest.mark.parametrize(
    ("link", "export"),
    [
        (f"docs.google.com/spreadsheets/d/{KEY}/edit?usp=sharing", EXPORT.replace("42", "0")),
        (
            f" https://docs.google.com/spreadsheets/d/{KEY}/edit?gid=7#gid=7 ",
            EXPORT.replace("42", "7"),
        ),
        (
            f"https://docs.google.com/spreadsheets/d/e/2PACX-{KEY}/pubhtml?gid=7",
            f"https://docs.google.com/spreadsheets/d/e/2PACX-{KEY}/pub?output=csv&gid=7",
        ),
    ],
)
def test_every_link_form_leads_to_the_export_of_its_sheet(link: str, export: str) -> None:
    assert sheet.export_url(link) == export


@pytest.mark.parametrize("extra", [0, 1])
async def test_sheet_bigger_than_the_ceiling_is_refused(
    monkeypatch: pytest.MonkeyPatch, extra: int
) -> None:
    monkeypatch.setattr(intake, "MAX_BYTES", 2**20)
    body = (b"x" * 1023 + b"\n") * 1024 + b"x" * extra  # ровно 1 МБ и на байт больше
    async with _http(lambda _: httpx.Response(200, content=body)) as http:
        if not extra:
            assert len((await intake.read_link(LINK, http)).records) == 1024
            return
        with pytest.raises(sheet.SheetError) as refused:
            await intake.read_link(LINK, http)
    assert str(refused.value) == "таблица больше 1 МБ — выгрузите её в CSV частями"


@pytest.mark.parametrize(
    ("name", "source"),
    [
        ("../../tmp/..список.csv", "список.csv"),  # путь отрезан, точки в начале сняты
        ("отчёт?*.csv", "отчёт__.csv"),
        ("", "база.csv"),
        ("я" * 150 + ".csv", "я" * 100),
    ],
)
def test_file_name_from_the_screen_is_only_a_name(name: str, source: str) -> None:
    assert intake.read_bytes(b"email\n", name).source == source
