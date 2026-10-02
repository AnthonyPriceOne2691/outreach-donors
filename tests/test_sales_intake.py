"""Загрузка базы лидов продаж — срез 1.3: чтение, сопоставление, отчёт, запись.

Люди и компании выдуманы, домены — `*.example.test`. Запись проверяется на
настоящей базе: лиды, домены компаний и журнал. Консоль — `test_sales_intake_cli.py`,
Google-таблица — `test_sales_intake_sheet.py`, путь экрана — `test_api_sales_intake.py`.

Утверждения — на точные значения и тексты: каждое правило приёма краснеет, если
его испортить (обратные прогоны — в verify-report).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from backend.features.core.domain import AuditAction
from backend.features.core.models.access import AuditLogModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.sales import intake
from backend.features.sales.columns import SYNONYMS, LeadField, Mapping, guess, key
from backend.features.sales.intake import Lead, Problem
from backend.features.sales.models import (
    LeadSource,
    LeadStatus,
    SalesHypothesisModel,
    SalesLeadModel,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

# A1: разделитель «;», русские заголовки; пробелы и регистр в ячейках.
A1 = (
    "Почта; Имя ;Компания;Сайт\n"
    " Ivan.Petrov @ACME.example.test ;  Иван Петров ;Acme;https://www.acme.example.test/about\n"
    "maria@mail.example.test;Мария Сидорова;Beta;beta.example.test\n"
)
A1_LEADS = [
    Lead(2, "ivan.petrov@acme.example.test", "acme.example.test", "Иван Петров", None, "Acme"),
    Lead(3, "maria@mail.example.test", "beta.example.test", "Мария Сидорова", None, "Beta"),
]
FREE = "нет сайта компании: gmail.com — бесплатная почта"


def _people(count: int, *, without_email: int | None = None) -> str:
    """Шапка и `count` лидов; строка файла `without_email` — без адреса."""
    rows = ["Почта;Имя;Компания"]
    for n in range(count):
        email = "" if n + 2 == without_email else f"lead{n}@firm{n}.example.test"
        rows.append(f"{email};Лид {n};Фирма {n}")
    return "\n".join(rows) + "\n"


def _table(tmp_path: Path, text: str, *, encoding: str = "utf-8") -> intake.Table:
    path = tmp_path / "база.csv"
    path.write_bytes(text.encode(encoding))
    return intake.read_file(path)


def _preview(tmp_path: Path, text: str, mapping: Mapping | None = None) -> intake.Preview:
    return intake.preview(_table(tmp_path, text), mapping)


async def _hypothesis(session: AsyncSession) -> SalesHypothesisModel:
    hypothesis = SalesHypothesisModel(name="сайты EN")
    session.add(hypothesis)
    await session.flush()
    return hypothesis


async def _count(session: AsyncSession, model: type, *where: object) -> int:
    query = select(func.count()).select_from(model).where(*where)
    return int(await session.scalar(query) or 0)


def test_a1_semicolon_and_russian_titles_are_mapped_by_themselves(tmp_path: Path) -> None:  # A1
    found = _preview(tmp_path, A1)

    assert (found.source, found.header, found.rows) == ("база.csv", True, 2)
    assert found.columns == ["Почта", "Имя", "Компания", "Сайт"]
    assert found.mapping == {
        LeadField.EMAIL: 0,
        LeadField.NAME: 1,
        LeadField.COMPANY: 2,
        LeadField.WEBSITE: 3,
    }
    assert (found.leads, found.problems) == (A1_LEADS, [])


def test_titles_match_without_case_and_separators_and_the_left_column_wins() -> None:
    one_per_field = ["E_MAIL", "Full Name", "first-name", "Фамилия", "Job.Title"]
    one_per_field += ["ORGANISATION", "Web Site", "Страна:", "  LANG  "]
    assert guess(one_per_field) == {field: index for index, field in enumerate(LeadField)}
    assert guess(["Почта", "Email", "Заметка"]) == {LeadField.EMAIL: 0}
    assert key("  First_Name: ") == "first name"


def test_synonyms_are_data_and_one_title_means_one_field() -> None:
    titles = [key(title) for names in SYNONYMS.values() for title in names]
    assert len(titles) == len(set(titles))
    assert set(SYNONYMS) == set(LeadField)


def test_a2_row_without_address_is_named_and_the_rest_go_on(tmp_path: Path) -> None:  # A2
    found = _preview(tmp_path, _people(9, without_email=7))

    assert found.problems == [Problem(7, intake.NO_ADDRESS, "")]
    assert (found.rows, len(found.leads), found.rejected, len(found.sample)) == (9, 8, 1, 5)
    assert [lead.line for lead in found.leads] == [2, 3, 4, 5, 6, 8, 9, 10]


@pytest.mark.parametrize(
    ("email", "taken"),
    [
        ("a" * 236 + "@acme.example.test", True),  # 254 знака — длиннее адресов не бывает
        ("a" * 237 + "@acme.example.test", False),
        ("ivan.acme.example.test", False),
        ("ivan@a..example.test", False),  # форма адреса есть, а имени сайта нет
    ],
)
def test_not_an_address_is_its_own_reason(tmp_path: Path, email: str, taken: bool) -> None:
    found = _preview(tmp_path, f"email\n{email}\n")
    assert [lead.email for lead in found.leads] == ([email] if taken else [])
    assert found.problems == ([] if taken else [Problem(2, "не адрес почты", email)])


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("email,name\nivan@gmail.com,Иван\n", Problem(2, FREE, "ivan@gmail.com")),  # A3
        (
            "email\nivan@mail.gmail.com\n",
            Problem(
                2, "нет сайта компании: mail.gmail.com — бесплатная почта", "ivan@mail.gmail.com"
            ),
        ),
        (
            "email,site\nivan@gmail.com,n/a\n",
            Problem(
                2,
                "нет сайта компании: «n/a» — не адрес сайта, а gmail.com — бесплатная почта",
                "n/a",
            ),
        ),
    ],
)
def test_a3_free_mailbox_without_a_website_is_refused(
    tmp_path: Path, text: str, problem: Problem
) -> None:  # A3
    found = _preview(tmp_path, text)
    assert (found.leads, found.problems) == ([], [problem])


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("email,name\nivan@acme.example.test,Иван\n", "Иван"),  # A4: домен из рабочей почты
        ("email,website\nivan@gmail.com,acme.example.test\n", None),  # A3: бесплатная почта и сайт
        ("email,website\nivan@mail.example.test,https://www.acme.example.test/x\n", None),
        ("email,name,website\nivan@acme.example.test,Иван\n", "Иван"),  # ячейки сайта нет
    ],
)
def test_a4_company_domain_from_a_website_or_a_work_address(
    tmp_path: Path, text: str, name: str | None
) -> None:  # A4
    found = _preview(tmp_path, text)
    assert ([(lead.domain, lead.name) for lead in found.leads], found.problems) == (
        [("acme.example.test", name)],
        [],
    )


def test_a4_unreadable_website_costs_no_more_than_an_empty_one(tmp_path: Path) -> None:  # A4
    """Урок L67 соседнего проекта: сайт разрешено не знать — значит, непонятый
    сайт не стоит строки, пока домен есть в рабочей почте. Замечание остаётся."""
    found = _preview(tmp_path, "email,site\nivan@acme.example.test,Acme Inc\n")

    assert [lead.domain for lead in found.leads] == ["acme.example.test"]
    reason = "сайт не разобран — домен компании acme.example.test взят из почты"
    assert found.problems == [Problem(2, reason, "Acme Inc", loaded=True)]


@pytest.mark.parametrize(("tail", "longest"), [(56, True), (57, False)])
def test_site_longer_than_any_domain_name_is_not_a_site(
    tmp_path: Path, tail: int, longest: bool
) -> None:
    site = ".".join(["a" * 63, "b" * 63, "c" * 63, "d" * tail]) + ".test"  # 253 и 254 знака
    found = _preview(tmp_path, f"email,site\nivan@acme.example.test,{site}\n")
    assert found.leads[0].domain == (site if longest else "acme.example.test")


def test_a6_file_without_a_header_asks_for_manual_mapping(tmp_path: Path) -> None:  # A6
    text = "ivan@acme.example.test;Иван\nmaria@beta.example.test;Мария\n"
    found = _preview(tmp_path, text)

    assert (found.header, found.needs_mapping, found.rows, found.mapping) == (False, True, 2, {})
    assert found.columns == ["колонка 1", "колонка 2"]
    assert found.sample == [
        ["ivan@acme.example.test", "Иван"],
        ["maria@beta.example.test", "Мария"],
    ]
    assert (found.leads, found.problems) == ([], [])

    mapped = _preview(tmp_path, text, {LeadField.EMAIL: 0, LeadField.NAME: 1})
    assert [(lead.line, lead.name) for lead in mapped.leads] == [(1, "Иван"), (2, "Мария")]


def test_header_cell_left_empty_is_named_by_its_number(tmp_path: Path) -> None:
    found = _preview(tmp_path, "Почта;;Компания\nivan@acme.example.test;x;Acme\n")
    assert found.columns == ["Почта", "колонка 2", "Компания"]


@pytest.mark.parametrize(
    ("titles", "cells", "name"),
    [
        ("First Name;Last Name", "Иван;Петров", "Иван Петров"),
        ("Name;Last Name", "Иван Петров;Петров", "Иван Петров"),  # полное имя не повторяется
        ("Name;First Name", "Иван Петров;Иван", "Иван Петров"),
        ("Last Name", "Петров", "Петров"),
        ("Company", "Acme", None),
    ],
)
def test_name_is_put_together_once(
    tmp_path: Path, titles: str, cells: str, name: str | None
) -> None:
    found = _preview(tmp_path, f"Email;{titles}\nivan@acme.example.test;{cells}\n")
    assert found.leads[0].name == name


def test_country_and_language_are_codes_or_a_note(tmp_path: Path) -> None:
    text = (
        "Email;Country;Language\n"
        "ivan@acme.example.test;DE;EN-us\n"
        "maria@acme.example.test;Germany;английский\n"
    )
    found = _preview(tmp_path, text)

    assert [(lead.country, lead.language) for lead in found.leads] == [
        ("de", "en-us"),
        (None, None),
    ]
    assert found.problems == [
        Problem(3, "страна не записана: ждём код — de, us", "Germany", loaded=True),
        Problem(3, "язык не записан: ждём код — en, ru", "английский", loaded=True),
    ]


@pytest.mark.parametrize("length", [255, 256])
def test_text_longer_than_its_column_is_cut_and_said(tmp_path: Path, length: int) -> None:
    title = "Д" * length
    found = _preview(tmp_path, f"Email;Должность\nivan@acme.example.test;{title}\n")

    assert found.leads[0].position == title[:255]
    cut = [Problem(2, "длиннее 255 знаков — обрезано", title, loaded=True)]
    assert found.problems == (cut if length > 255 else [])


@pytest.mark.parametrize(
    ("text", "encoding", "words"),
    [
        (
            "Почта;Имя\nivan@acme.example.test;Иван\n",
            "cp1251",
            "файл база.csv не в UTF-8 (строка 1)",
        ),
        ("Почта;Имя\n", "utf-8", "база.csv: только заголовок — строк с лидами нет"),
        ("\n\n", "utf-8", "в файле база.csv нет ни одной строки"),
        (
            "Почта\n" + "я" * 140_000 + "\n",
            "utf-8",
            "файл база.csv не читается как CSV: field larger",
        ),
    ],
)
def test_unusable_file_is_refused_in_words(
    tmp_path: Path, text: str, encoding: str, words: str
) -> None:
    with pytest.raises(intake.IntakeError) as refused:
        intake.preview(_table(tmp_path, text, encoding=encoding))
    assert str(refused.value).startswith(words)


def test_missing_file_is_refused_in_words(tmp_path: Path) -> None:
    with pytest.raises(intake.IntakeError) as refused:
        intake.read_file(tmp_path / "нет.csv")
    assert str(refused.value).startswith(f"файл {tmp_path / 'нет.csv'} не открылся: No such file")


def test_mapping_to_a_missing_column_is_refused(tmp_path: Path) -> None:
    with pytest.raises(intake.IntakeError) as refused:
        _preview(tmp_path, A1, {LeadField.EMAIL: 4})
    assert str(refused.value) == "колонки 5 нет, их в файле 4: email не сопоставить"


async def test_a8_load_writes_leads_domains_and_journal_but_no_contacts(
    session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # A8
    monkeypatch.setattr(intake, "CHUNK", 1)  # домены — пачками: здесь по одному
    hypothesis = await _hypothesis(session)

    assert await intake.load(session, _preview(tmp_path, A1), hypothesis.id, author_id=None) == 2

    hosts = dict((await session.execute(select(DomainModel.id, DomainModel.host))).tuples().all())
    rows = (await session.scalars(select(SalesLeadModel).order_by(SalesLeadModel.id))).all()
    assert [(r.email, hosts[r.domain_id], r.name, r.company) for r in rows] == [
        (lead.email, lead.domain, lead.name, lead.company) for lead in A1_LEADS
    ]
    assert {(r.contact_id, r.source, r.status, r.hypothesis_id) for r in rows} == {
        (None, LeadSource.IMPORT, LeadStatus.NEW, hypothesis.id)
    }
    assert await _count(session, ContactModel) == 0
    journal = await session.scalar(
        select(AuditLogModel).where(AuditLogModel.action == AuditAction.SALES_LEADS_IMPORTED)
    )
    assert journal is not None
    assert (journal.user_id, journal.target) == (None, f"sales_hypothesis:{hypothesis.id}")
    assert journal.details == {"источник": "база.csv", "загружено": 2, "отклонено": 0}


@pytest.mark.parametrize(
    ("text", "hypothesis_id", "words"),
    [
        (A1, 999999, "гипотезы №999999 нет — заведите её: outreach sales-hypothesis-add"),
        (
            "ivan@acme.example.test\n",
            None,
            "сопоставьте колонки: не найдена колонка почты — без неё лидов нет",
        ),
    ],
)
async def test_load_refusals_write_nothing(
    session: AsyncSession, tmp_path: Path, text: str, hypothesis_id: int | None, words: str
) -> None:
    hypothesis = hypothesis_id or (await _hypothesis(session)).id
    with pytest.raises((intake.UnknownHypothesisError, intake.IntakeError)) as refused:
        await intake.load(session, _preview(tmp_path, text), hypothesis, author_id=None)
    assert str(refused.value) == words
    assert await _count(session, SalesLeadModel) == 0


async def test_nothing_to_load_writes_nothing_not_even_the_journal(
    session: AsyncSession, tmp_path: Path
) -> None:
    hypothesis = await _hypothesis(session)
    found = _preview(tmp_path, "email\nivan@gmail.com\n")

    assert await intake.load(session, found, hypothesis.id, author_id=None) == 0
    assert await _count(session, DomainModel, DomainModel.host == "gmail.com") == 0
    assert (
        await _count(
            session, AuditLogModel, AuditLogModel.action == AuditAction.SALES_LEADS_IMPORTED
        )
        == 0
    )
