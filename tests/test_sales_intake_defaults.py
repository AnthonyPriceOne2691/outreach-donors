"""Язык и пояс лида — не только колонкой файла: умолчания загрузки, код с регионом, слова до
загрузки (сверка модуля продаж с ТЗ, п. 1).

Без колонки языка весь файл ждал в очереди «язык не задан», а без страны и пояса лид не уходил
никогда: окно отправки не знает его часов. Здесь: умолчание ложится только туда, где колонка
пуста, и считается; без умолчания — предупреждение с числом; `ru-RU` — это `ru` и при загрузке,
и в очереди для уже загруженных; пояс по умолчанию — окно его часов. Люди и домены выдуманы.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
import pytest
from backend.cli.main import build_parser
from backend.cli.sales import EXIT_BAD_INPUT, run_import
from backend.config import sales as sales_cfg
from backend.features.core.domain import AuditAction
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.outreach import MessageModel
from backend.features.letters.sending import OutsideWindowError, Sending
from backend.features.sales import intake, lead_values, queue
from backend.features.sales.intake import Problem
from backend.features.sales.models import LeadStatus, SalesLeadModel, SalesThreadModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.conftest import bearer
from tests.test_sales_send import _seen, _transports
from tests.test_sales_send_zones import MONDAY_NOON, WEEKDAYS_9_17

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

#: Колонки языка и места нет вовсе — так приходят базы.
BARE = "Почта;Имя\nivan@acme.example.test;Иван\nlena@beta.example.test;Lena\n"
NO_LANGUAGE = (
    "у 2 лидов нет языка — им не уйдёт ни одно письмо: задайте язык по умолчанию или колонку языка"
)
NO_ZONE = (
    "у 2 лидов нет ни страны, ни пояса — окно отправки не знает их часов, и письмо не уйдёт: "
    "задайте страну или пояс по умолчанию"
)


def _table(tmp_path: Path, text: str) -> intake.Table:
    path = tmp_path / "база.csv"
    path.write_text(text, encoding="utf-8")
    return intake.read_file(path)


def _preview(tmp_path: Path, text: str, language: str | None, where: str | None) -> intake.Preview:
    return intake.preview(_table(tmp_path, text), given=intake.defaults(language, where))


# --- умолчания загрузки --------------------------------------------------------------------


def test_file_without_a_language_column_and_a_default_gives_everyone_en(tmp_path: Path) -> None:
    found = _preview(tmp_path, BARE, "en", None)

    assert [lead.language for lead in found.leads] == ["en", "en"]
    assert (found.defaulted_language, found.defaulted_place) == (2, 0)
    assert NO_LANGUAGE not in found.warnings
    assert found.warnings == [NO_ZONE]


def test_without_a_default_the_screen_is_told_how_many_get_no_letter(tmp_path: Path) -> None:
    found = _preview(tmp_path, BARE, None, None)

    assert [lead.language for lead in found.leads] == [None, None]
    assert found.warnings == [NO_LANGUAGE, NO_ZONE]
    assert (found.defaulted_language, found.defaulted_place) == (0, 0)


def test_default_goes_only_where_the_column_is_empty(tmp_path: Path) -> None:
    """Мутант «умолчание затирает колонку»: у Ивана в файле `ru`, у Лены пусто, у Олега —
    непонятое слово: оно не пусто, умолчание его не перекрывает, а замечание остаётся."""
    text = (
        "Почта;Язык;Страна;Пояс\n"
        "ivan@acme.example.test;ru;Россия;\n"
        "lena@beta.example.test;;;\n"
        "oleg@gamma.example.test;русский;;Asia/Tokyo\n"
    )

    found = _preview(tmp_path, text, "en", "Germany")

    placed = [(lead.language, lead.country, lead.timezone) for lead in found.leads]
    assert placed == [
        ("ru", "ru", "Europe/Moscow"),
        ("en", "de", "Europe/Berlin"),
        (None, None, "Asia/Tokyo"),
    ]
    assert (found.defaulted_language, found.defaulted_place) == (1, 1)
    assert Problem(4, intake.NO_LANGUAGE, "русский", loaded=True) in found.problems


@pytest.mark.parametrize(
    ("where", "placed"),
    [
        ("Europe/Berlin", (None, "Europe/Berlin")),
        ("europe/berlin", (None, "Europe/Berlin")),
        ("DE", ("de", "Europe/Berlin")),
        ("Германия", ("de", "Europe/Berlin")),
    ],
)
def test_place_is_a_zone_or_a_country_in_the_words_of_the_columns(
    tmp_path: Path, where: str, placed: tuple[str | None, str]
) -> None:
    found = _preview(tmp_path, BARE, None, where)

    assert {(lead.country, lead.timezone) for lead in found.leads} == {placed}
    assert found.warnings == [NO_LANGUAGE]


def test_country_with_many_zones_takes_the_capital_and_says_so(tmp_path: Path) -> None:
    found = _preview(tmp_path, BARE, "en", "USA")

    assert {lead.timezone for lead in found.leads} == {"America/New_York"}
    assert found.warnings == [
        "пояс по умолчанию — по столице: America/New_York; у страны us их несколько, точнее "
        "скажет колонка пояса"
    ]


def test_language_without_a_chain_is_said_with_its_code(tmp_path: Path) -> None:
    found = _preview(tmp_path, BARE, "de", "de")

    assert found.warnings == [
        "у 2 лидов язык без цепочки писем (de) — им не уйдёт ни одно письмо: цепочки есть на ru, en"
    ]


@pytest.mark.parametrize(
    ("language", "where", "words"),
    [
        ("english", None, "язык по умолчанию «english» — не код языка: ждём код — en, ru"),
        (None, "Атлантида", "страна или пояс по умолчанию «Атлантида» не распознаны: ждём код"),
    ],
)
def test_default_that_is_not_understood_is_refused_in_words(
    language: str | None, where: str | None, words: str
) -> None:
    with pytest.raises(intake.IntakeError) as refused:
        intake.defaults(language, where)
    assert str(refused.value).startswith(words)


def test_one_lead_in_the_warning_is_said_in_the_singular() -> None:
    lead = intake.Lead(2, "ivan@acme.example.test", "acme.example.test")
    assert lead_values.warnings([lead])[0].startswith("у 1 лида нет языка")
    assert lead_values.warnings([lead] * 11)[0].startswith("у 11 лидов нет языка")
    assert lead_values.warnings([lead] * 21)[0].startswith("у 21 лида нет языка")


# --- код с регионом -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cell", "language"),
    [("ru-RU", "ru"), ("RU", "ru"), ("en_US", "en"), ("zh-Hant-TW", "zh"), ("ru", "ru")],
)
def test_language_with_a_region_is_loaded_as_its_base(
    tmp_path: Path, cell: str, language: str
) -> None:
    found = intake.preview(_table(tmp_path, f"Почта;Язык\nivan@acme.example.test;{cell}\n"))

    assert [lead.language for lead in found.leads] == [language]
    assert found.problems == []


@pytest.mark.parametrize(("stored", "base"), [("ru-ru", "ru"), (" EN-gb ", "en"), (None, "")])
def test_queue_reads_the_base_of_a_language_loaded_before_the_rule(
    stored: str | None, base: str
) -> None:
    assert lead_values.language_base(stored) == base


async def test_old_lead_with_ru_ru_gets_the_ru_letter(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Мутант «регион не обрезан»: лид, загруженный с `ru-ru` до правила, ждал бы вечно."""
    world = await w.world(session, monkeypatch)
    await w.lead(session, world.hypothesis_id, "ivan@beta.example.test", language="ru-ru")

    report = await queue.build(
        session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=5
    )

    assert (report.prepared, dict(report.waiting)) == (1, {})
    link = await session.scalar(select(SalesThreadModel))
    assert link is not None
    assert link.language == "ru"


# --- пояс по умолчанию — окно его часов --------------------------------------------------


@pytest.mark.parametrize(("where", "goes"), [("Europe/Berlin", True), ("America/New_York", False)])
async def test_default_zone_puts_the_letter_into_the_window_of_its_hours(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    where: str,
    goes: bool,
) -> None:
    """Понедельник 14:00 в Берлине — окно открыто; в Нью-Йорке 08:00 — ещё нет: у лида без
    страны и пояса в файле окно считает пояс по умолчанию, а не пропадает."""
    world = await w.world(session, monkeypatch)
    monkeypatch.setattr(sales_cfg, "SEND_DAYS", WEEKDAYS_9_17.days)
    monkeypatch.setattr(sales_cfg, "SEND_OPENS", WEEKDAYS_9_17.start)
    monkeypatch.setattr(sales_cfg, "SEND_CLOSES", WEEKDAYS_9_17.end)
    text = "Email;Name;Company\njane@acme.example.test;Jane;Example Test Co\n"
    found = intake.preview(_table(tmp_path, text), given=intake.defaults("en", where))
    await intake.load(session, found, world.hypothesis_id, author_id=None)
    await session.execute(update(SalesLeadModel).values(status=LeadStatus.READY))
    await queue.build(session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=5)
    letter = await session.scalar(select(MessageModel))
    assert letter is not None
    source = _transports()
    sending = Sending(session, source, now=MONDAY_NOON)

    if goes:
        await sending.send(letter.id)
    else:
        with pytest.raises(OutsideWindowError, match=r"уйдёт не раньше пн 12\.10 09:"):
            await sending.send(letter.id)

    assert [outgoing.to for outgoing in _seen(source)] == (
        ["jane@acme.example.test"] if goes else []
    )


# --- журнал, экран и консоль ----------------------------------------------------------------


async def test_load_journal_names_the_defaults_that_went_into_leads(
    session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = await w.world(session, monkeypatch)
    found = _preview(tmp_path, BARE, "en", "de")

    assert await intake.load(session, found, world.hypothesis_id, author_id=None) == 2

    journal = await session.scalar(
        select(AuditLogModel).where(AuditLogModel.action == AuditAction.SALES_LEADS_IMPORTED)
    )
    assert journal is not None
    assert journal.details == {
        "источник": "база.csv",
        "загружено": 2,
        "отклонено": 0,
        "по умолчанию": {
            "язык": {"значение": "en", "лидов": 2},
            "страна или пояс": {"страна": "de", "пояс": "Europe/Berlin", "лидов": 2},
        },
    }
    stored = await session.execute(select(SalesLeadModel.language, SalesLeadModel.timezone))
    assert set(stored.tuples()) == {("en", "Europe/Berlin")}


async def test_screen_sends_the_defaults_and_reads_the_counts_and_the_warnings(
    client: httpx.AsyncClient, make_user: MakeUser, sign_in: SignIn
) -> None:
    await make_user("seller@ours.example.test")
    headers = bearer(await sign_in("seller@ours.example.test"))
    files = {"file": ("leads.csv", BARE.encode("utf-8"), "text/csv")}

    plain = await client.post("/api/sales/import/preview", headers=headers, files=files)
    given = await client.post(
        "/api/sales/import/preview",
        headers=headers,
        files=files,
        data={"default_language": "ru-RU", "default_place": "Germany"},
    )

    assert plain.status_code == given.status_code == 200, given.text
    assert plain.json()["warnings"] == [NO_LANGUAGE, NO_ZONE]
    body = given.json()
    assert body["defaults"] == {"language": "ru", "country": "de", "timezone": "Europe/Berlin"}
    assert (body["defaulted_language"], body["defaulted_place"], body["warnings"]) == (2, 2, [])
    assert {lead["language"] for lead in body["leads"]} == {"ru"}


async def test_screen_default_that_is_not_understood_is_a_400_in_words(
    client: httpx.AsyncClient, make_user: MakeUser, sign_in: SignIn
) -> None:
    await make_user("seller@ours.example.test")
    headers = bearer(await sign_in("seller@ours.example.test"))

    refused = await client.post(
        "/api/sales/import/preview",
        headers=headers,
        files={"file": ("leads.csv", BARE.encode("utf-8"), "text/csv")},
        data={"default_place": "Атлантида"},
    )

    assert refused.status_code == 400
    assert refused.json()["detail"].startswith("страна или пояс по умолчанию «Атлантида»")


async def test_console_takes_the_defaults_and_prints_how_many_got_them(
    session: AsyncSession,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await w.world(session, monkeypatch)
    source = tmp_path / "база.csv"
    source.write_text(BARE, encoding="utf-8")
    args = ["sales-import", "--hypothesis", "Выдуманная гипотеза", "--file", str(source)]

    code = await run_import(
        session, build_parser().parse_args([*args, "--language", "en", "--place", "Europe/Berlin"])
    )

    assert code == 0
    out = capsys.readouterr().out
    assert "Язык по умолчанию en — лидов: 2\n" in out
    assert "Страна или пояс по умолчанию Europe/Berlin — лидов: 2\n" in out
    assert "Внимание:" not in out
    refused = await run_import(session, build_parser().parse_args([*args, "--language", "xx yy"]))
    assert refused == EXIT_BAD_INPUT
    assert "База не прочитана: язык по умолчанию «xx yy» — не код языка" in capsys.readouterr().out
