"""Разбор ответа v2: проект, услуги, имя названного — и что из этого видит сделка в Kommo.

Ф5: в сводке — проект, услуги и ГЕО: это нужно телемаркетологу для MQL. Отдельного
вызова модели нет: необязательные поля того же разбора вида (`sales-reply-kind-v2`). Правила —
как у цитаты и адреса: проект и имя — дословно из письма, услуги — только названия записей
«услуга» базы знаний; выдуманное отбрасывается с пометкой. В примечании к сделке — строки
«Проект» и «Услуги» (пусто — «не описан», «не названы»); в Telegram — по-прежнему три строки.
Обязательные поля сделки — постоянными значениями из `SALES_KOMMO_LEAD_FIELDS`; форму разбирает
сборка клиента `live`, а не импорт настроек: негодная — отказ словами на передаче.

Модель — подставной транспорт, Kommo — `MockTransport`, база — настоящая. Тексты выдуманные.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from backend.config import sales as sales_cfg
from backend.config.startup_checks import ConfigError
from backend.features.core.domain import Stage
from backend.features.sales import handoff, handoff_card, handoff_jobs, kb, kommo, reply_kind
from backend.features.sales import handoff_text as wording
from backend.features.sales.handoff_text import Card
from backend.features.sales.kommo import KommoAccount, KommoGateway, KommoLive, KommoRefusedError
from backend.features.sales.models import HandoffKommo
from backend.features.sales.replies import SalesReplies
from backend.features.sales.reply_kind import KindFound, SalesKind, parse_form, snapshot, told
from scripts import eval_sales_reply as ev
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_kommo as live
from tests import test_sales_kommo_gateway as gate
from tests.test_sales_handoff_rows import sales_dialog
from tests.test_sales_reply_kind import Model, client, says
from tests.test_sales_reply_routing import FakeClassifier
from tests.test_sales_switch import sales_switched_on

__all__ = ["sales_switched_on"]  # продажи включены: разбор вида ответа

SERVICES = ("SEO-аудит", "Ссылки на сайт")
LETTER = "У нас интернет-магазин мебели. Сколько стоит аудит?"
REFERRAL = "Это не ко мне. Напишите нашему маркетологу, Анна Петрова: anna@acme.example.test"


def _found(**fields: Any) -> KindFound:
    return KindFound(SalesKind.QUESTION, 0.9, quote="Сколько стоит аудит?", **fields)


# --- поля из ответа модели: только дословные и сверенные -----------------------------------


def test_form_reads_the_v2_fields_and_ignores_garbage() -> None:
    found = parse_form(
        says(
            kind="question",
            confidence=0.9,
            quote="Сколько стоит аудит?",
            project="интернет-магазин мебели",
            services=["SEO-аудит", 7, "", None, "Ссылки на сайт"],
            contact_name=None,
        )
    )

    assert found is not None
    assert (found.project, found.services, found.contact_name) == (
        "интернет-магазин мебели",
        ("SEO-аудит", "Ссылки на сайт"),
        None,
    )


def test_verbatim_project_and_known_services_stay_in_the_base_spelling() -> None:
    found = told(
        _found(project="интернет-магазин  МЕБЕЛИ", services=("seo-аудит",)),
        text=LETTER,
        labels={},
        services=SERVICES,
    )

    assert found.project == "интернет-магазин  МЕБЕЛИ", "слова — из письма, сверка без регистра"
    assert found.services == ("SEO-аудит",), "название — как записано в базе"
    assert (found.notes, found.confidence) == ((), 0.9)


def test_made_up_project_and_service_are_dropped_with_a_note() -> None:
    found = told(
        _found(project="сеть ресторанов в Казани", services=("SEO-аудит", "Контекстная реклама")),
        text=LETTER,
        labels={},
        services=SERVICES,
    )

    assert (found.project, found.services) == (None, ("SEO-аудит",))
    assert found.notes == (
        "проект модели в письме не найден — отброшен",
        "услуга модели не из базы знаний — отброшена",
    )
    assert found.confidence == 0.9, "вид от полей не зависит"


def test_without_service_records_every_service_is_dropped() -> None:
    found = told(_found(services=("SEO-аудит",)), text=LETTER, labels={}, services=())

    assert found.services == ()


@pytest.mark.parametrize(
    ("kind", "name", "expected", "note"),
    [
        (SalesKind.REFERRAL, "Анна Петрова", "Анна Петрова", None),
        (
            SalesKind.REFERRAL,
            "Мария Иванова",
            None,
            "имя названного в письме не найдено — отброшено",
        ),
        (
            SalesKind.REFERRAL,
            "маркетологу, Анна",
            None,
            "имя названного не похоже на имя — отброшено",
        ),
        (SalesKind.QUESTION, "Анна Петрова", None, None),
    ],
    ids=["verbatim", "made-up", "phrase-not-in-shape", "not-a-referral"],
)
def test_contact_name_is_only_a_verbatim_name_of_the_referred(
    kind: SalesKind, name: str, expected: str | None, note: str | None
) -> None:
    found = told(
        KindFound(kind, 0.9, quote="Это не ко мне", contact_name=name),
        text=REFERRAL,
        labels={},
        services=(),
    )

    assert found.contact_name == expected
    if note is not None:
        assert note in found.notes


def test_phrase_in_place_of_a_name_is_dropped_with_a_note() -> None:
    found = told(
        KindFound(SalesKind.REFERRAL, 0.9, contact_name="anna@acme.example.test"),
        text=REFERRAL,
        labels={},
        services=(),
    )

    assert found.contact_name is None
    assert "имя названного не похоже на имя — отброшено" in found.notes


async def test_client_shows_the_services_and_keeps_only_what_checks_out() -> None:
    model = Model(
        says(
            kind="question",
            confidence=0.91,
            quote="Сколько стоит аудит?",
            contact=None,
            contact_name=None,
            project="интернет-магазин мебели",
            services=["SEO-аудит", "Реклама в соцсетях"],
        )
    )

    found = await client(model).classify(text=LETTER, subject="Re: вопрос", services=SERVICES)

    assert isinstance(found, KindFound)
    assert '"SEO-аудит", "Ссылки на сайт"' in model.user, "модель видит услуги базы"
    assert model.user.index("Known services") < model.user.index("<<<EMAIL")
    assert (found.project, found.services) == ("интернет-магазин мебели", ("SEO-аудит",))
    assert reply_kind.PROMPT_VERSION == "sales-reply-kind-v2"
    snap = snapshot(found, model="m")
    assert (snap["project"], snap["services"], snap["contact_name"]) == (
        "интернет-магазин мебели",
        ["SEO-аудит"],
        None,
    )
    assert snap["prompt_version"] == "sales-reply-kind-v2"


async def test_reply_job_gives_the_model_the_service_records_of_the_base(
    session: AsyncSession,
) -> None:
    for title, language in (("SEO-аудит", "ru"), ("SEO audit", "en"), ("SEO-аудит", "en")):
        entry = kb.entry(kind="service", language=language, title=title, text="Что входит.")
        await kb.add(session, entry, author="тест", author_id=None)
    off = kb.entry(kind="service", language="ru", title="Старая услуга", text="—", active=False)
    await kb.add(session, off, author="тест", author_id=None)
    dialog = await sales_dialog(session, text=LETTER)
    classifier = FakeClassifier(_found())

    await SalesReplies(session, classifier).handle(dialog.reply.id)

    assert classifier.services == [("SEO audit", "SEO-аудит")], "включённые, без повторов"


# --- сводка сделки: проект и услуги; сообщение — три строки ---------------------------------


def _card(**fields: Any) -> Card:
    base: dict[str, Any] = {
        "thread_id": 7,
        "lead_id": 3,
        "email": "ivan@acme.example.test",
        "name": "Иван",
        "company": "Акме",
        "site": "acme.example.test",
        "hypothesis": "гипотеза",
        "country": "de",
        "language": "ru",
        "reply_id": 11,
        "reply_at": datetime(2026, 10, 14, 9, 17, tzinfo=UTC),
        "reply_subject": "Re: вопрос",
        "reply_text": LETTER,
        "sent": 1,
        "replies": 1,
        "first_sent_at": None,
    }
    return Card(**{**base, **fields})


def test_note_has_project_and_services_lines() -> None:
    note = wording.note(_card(project="интернет-магазин мебели", services=SERVICES))

    lines = note.split("\n")
    assert "Проект: интернет-магазин мебели" in lines
    assert "Услуги: SEO-аудит, Ссылки на сайт" in lines
    assert lines.index("Проект: интернет-магазин мебели") > lines.index(
        "Компания: Акме · acme.example.test"
    )


def test_note_says_plainly_when_nothing_was_named() -> None:
    lines = wording.note(_card()).split("\n")

    assert (lines[8], lines[9]) == ("Проект: не описан", "Услуги: не названы")


def test_message_is_still_three_lines_without_project_or_services() -> None:
    text = wording.message("https://x.kommo.com/leads/detail/9301", 7, HandoffKommo.DONE)

    assert text.count("\n") == 2
    assert "Проект" not in text
    assert "Услуги" not in text


def test_card_takes_project_and_services_from_every_answer_of_the_dialog() -> None:
    snaps: list[dict[str, Any] | None] = [
        {"stage": Stage.SALES.value, "project": "магазин мебели", "services": ["SEO-аудит"]},
        None,
        {"stage": "donors", "project": "чужое", "services": ["Чужое"]},
        {"stage": Stage.SALES.value, "project": None, "services": ["Ссылки на сайт", "SEO-аудит"]},
        {"stage": Stage.SALES.value, "project": "интернет-магазин мебели", "services": []},
    ]

    assert handoff_card.told(snaps) == ("интернет-магазин мебели", SERVICES)


async def test_deal_note_of_a_handoff_shows_what_the_answers_said(session: AsyncSession) -> None:
    dialog = await sales_dialog(session, text=LETTER)
    dialog.reply.model_parse = {
        "stage": Stage.SALES.value,
        "kind": "wants_to_talk",
        "project": "интернет-магазин мебели",
        "services": ["SEO-аудит"],
    }
    row = await handoff.start(session, dialog.thread.id, enqueue=lambda _id: None)

    note = wording.note(await handoff_card.card_of(session, row))
    assert "Проект: интернет-магазин мебели" in note
    assert "Услуги: SEO-аудит" in note


# --- обязательные поля сделки --------------------------------------------------------------


async def test_lead_fields_from_the_setting_go_into_the_deal_body() -> None:
    account = KommoAccount(
        subdomain="acme-test",
        token=live.TOKEN,
        pipeline_id=4417,
        status_id=61953,
        responsible_user_id=88231,
        lead_fields=((712345, "Email"), (712349, 3), (712351, True)),
    )
    script = live.Script(live._nobody(), live._created(9341))

    async with httpx.AsyncClient(transport=httpx.MockTransport(script)) as http:
        await KommoLive(http, account).create_complex_lead(live.LEAD)

    deal = script.body(1)[0]
    assert deal["custom_fields_values"] == [
        {"field_id": 712345, "values": [{"value": "Email"}]},
        {"field_id": 712349, "values": [{"value": 3}]},
        {"field_id": 712351, "values": [{"value": True}]},
    ]


async def test_no_lead_fields_means_no_custom_fields_in_the_deal() -> None:
    script = live.Script(live._nobody(), live._created(9341))

    async with httpx.AsyncClient(transport=httpx.MockTransport(script)) as http:
        await KommoLive(http, live.ACCOUNT).create_complex_lead(live.LEAD)

    assert "custom_fields_values" not in script.body(1)[0]


async def test_live_client_takes_the_lead_fields_from_the_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live._configure(monkeypatch, "live")
    monkeypatch.setattr(sales_cfg, "KOMMO_LEAD_FIELDS", '{"712345": "Email"}')

    built = await live._build(live.Script())

    assert isinstance(built, KommoLive)
    assert built._account.lead_fields == ((712345, "Email"),)


async def test_gateway_refuses_lead_fields_in_words(monkeypatch: pytest.MonkeyPatch) -> None:
    """Шлюз полей сделки не принимает — молча потерять настройку хуже отказа."""
    gate._configure(monkeypatch, "live")
    monkeypatch.setattr(sales_cfg, "KOMMO_LEAD_FIELDS", '{"712345": "Email"}')

    with pytest.raises(ConfigError, match="SALES_KOMMO_LEAD_FIELDS задан, а шлюз агентства"):
        await gate._build(gate.Script())


async def test_gateway_without_lead_fields_is_built_as_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate._configure(monkeypatch, "live")
    monkeypatch.setattr(sales_cfg, "KOMMO_LEAD_FIELDS", "")

    assert type(await gate._build(gate.Script())) is KommoGateway


@pytest.mark.parametrize(
    ("raw", "words"),
    [
        ("{oops", "это не JSON"),
        ("[1, 2]", "нужен непустой объект"),
        ("{}", "нужен непустой объект"),
        ('{"abc": "x"}', "«abc» не номер поля"),
        ('{"0": "x"}', "«0» не номер поля"),
        ('{"712345": "  "}', "значение поля 712345"),
        ('{"712345": ["a"]}', "значение поля 712345"),
        ('{"712345": null}', "значение поля 712345"),
    ],
)
def test_crooked_lead_fields_setting_is_refused_in_words(raw: str, words: str) -> None:
    with pytest.raises(ConfigError, match="SALES_KOMMO_LEAD_FIELDS") as refused:
        kommo.lead_fields(raw)

    assert words in str(refused.value)


async def test_crooked_lead_fields_stop_the_live_client_not_the_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Чтение настроек кривую форму принимает как есть — их импортирует и общий код; отказ —
    при сборке клиента `live`, там же, где отказ `live` без ключа: задача передачи получает
    клиент-отказ, и каждая запись в Kommo отказывает словами настройки."""
    crooked = '{"712345": '
    monkeypatch.setenv("SALES_KOMMO_LEAD_FIELDS", crooked)
    assert sales_cfg._Sales(_env_file=None).kommo_lead_fields == crooked
    live._configure(monkeypatch, "live", KOMMO_LEAD_FIELDS=crooked)
    words = "SALES_KOMMO_LEAD_FIELDS: нужен JSON «номер поля → значение»"

    with pytest.raises(ConfigError, match=words):
        await live._build(live.Script())
    async with httpx.AsyncClient() as http:
        refusing = handoff_jobs.connected_kommo(http)
    assert refusing is not None
    with pytest.raises(KommoRefusedError, match=f"клиент Kommo не собран — {words}"):
        await refusing.create_complex_lead(live.LEAD)


def test_lead_fields_setting_reads_numbers_and_values() -> None:
    assert kommo.lead_fields("") == ()
    assert kommo.lead_fields('{"712345": " Email ", "9": 2.5, "10": false}') == (
        (712345, "Email"),
        (9, 2.5),
        (10, False),
    )


# --- eval: поля меряются рядом с видами, вне ворот -----------------------------------------


def test_eval_reports_the_v2_fields_outside_the_gates() -> None:
    case = {"id": "x", "expect": {"kind": "referral", "contact_name": "Анна Петрова"}}
    named = ev.judged(case, KindFound(SalesKind.REFERRAL, 0.9, contact_name="анна  петрова"), 0.8)
    service_case = {"id": "y", "expect": {"kind": "question", "services": ["SEO audit"]}}
    listed = ev.judged(
        service_case, KindFound(SalesKind.QUESTION, 0.9, services=("SEO audit",)), 0.8
    )
    extra_case = {"id": "z", "expect": {"kind": "question"}}
    extra = ev.judged(extra_case, KindFound(SalesKind.QUESTION, 0.9, project="магазин"), 0.8)

    lines = ev.fields([named, listed, extra])

    assert lines[0].startswith("Поля v2 (вне ворот): имя названного верно 1/1, лишнее 0")
    assert lines[1].startswith("  проект найден 0/0, лишний 1 ['z']")
    assert lines[2].startswith("  услуги верно 1/1, лишние 0")
    assert ev.known_services(None) == ev.SYNTHETIC_SERVICES
    assert ev.known_services(" SEO-аудит ; Ссылки ;") == ("SEO-аудит", "Ссылки")
