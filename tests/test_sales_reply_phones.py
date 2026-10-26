"""Телефон в ответе лида продаж — передача телемаркетологу (разбор `sales-reply-kind-v3`).

Человек дал номер для связи — значит, с ним говорить: лида передают телемаркетологу при любой
уверенности модели в виде ответа. Кроме отказа, «не сейчас» и «не пишите»: номер в их подписи —
не просьба позвонить; вид не разобран — решает человек, как раньше. Модель номер выписывает
дословно (`phones`), код проверяет («факты — кодом»): номер стоит в письме дословно и целиком, в
нём 7–15 цифр любой письменности. Телемаркетолог видит строку «Телефон: …» в сообщении Telegram и
в примечании к сделке Kommo.

Модель — подставной транспорт, Kommo — `KommoFixture`, Telegram — `httpx.MockTransport`, база —
настоящая. Номера, имена и адреса выдуманные (`*.example.test`), страны и письменности — разные.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import httpx
import pytest
from backend.features.core.domain import Stage, ThreadStatus
from backend.features.core.models.outreach import ReplyModel, ThreadModel
from backend.features.outreach.threads import review_of
from backend.features.sales import handoff, handoff_card, reply_kind
from backend.features.sales import handoff_text as wording
from backend.features.sales.handoff_text import Card
from backend.features.sales.kommo import KommoFixture
from backend.features.sales.models import HandoffKommo, SalesHandoffModel, SalesLeadModel
from backend.features.sales.replies import SalesReplies
from backend.features.sales.reply_handoff import HANDED_OVER
from backend.features.sales.reply_kind import (
    MAX_PHONES,
    KindFound,
    SalesKind,
    parse_form,
    phones_of,
    snapshot,
)
from backend.features.sales.reply_routes import GAVE_PHONE, NO_CALL, PHONE_WORDS, Route, decide
from backend.workers import sales_jobs
from scripts import eval_sales_reply as ev
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_handoff import (
    AGAIN,
    DEAL,
    HEAD,
    NOW,
    Alerts,
    alerts,
    api,
    deps,
    hand_off,
    http,
    sent,
)
from tests.test_sales_handoff_rows import answer, sales_dialog
from tests.test_sales_reply_handoff import _job_on_test_base
from tests.test_sales_reply_kind import Handover, Model, _sales_reply, client, says
from tests.test_sales_reply_routing import secret
from tests.test_sales_switch import sales_switched_on
from tests.test_sales_telegram import GROUP, PERSONAL, Recorder

__all__ = [  # оснастка передачи и приёма
    "alerts",
    "api",
    "http",
    "sales_switched_on",  # продажи включены: модель разбирает ответ, лида передают
    "secret",  # подпись адреса ответа: ответ проходит настоящий приём
]

#: Когда передача заведена — некруглое, заведомо выдуманное время.
AT = datetime(2026, 10, 26, 10, 41, tzinfo=UTC)
NOT_FOUND = "номер модели в письме не найден — отброшен"
NOT_A_PHONE = "номер модели — не телефон (цифры, «+» и разделители, 7–15 цифр) — отброшен"
A_PIECE = "номер модели — кусок длинного числа из письма — отброшен"


def _found(kind: SalesKind, confidence: float, *phones: str, **fields: Any) -> KindFound:
    return KindFound(kind, confidence, quote="x", phones=phones, **fields)


# --- форма: номера читаются списком или строкой --------------------------------------------


def test_form_reads_phones_as_a_list_or_a_lone_string_and_drops_garbage() -> None:
    listed = parse_form(
        says(kind="question", confidence=0.9, quote="x", phones=["+44 20 7946 0123", 7, "", None])
    )
    lone = parse_form(says(kind="question", confidence=0.9, quote="x", phones="+48 22 123 45 67"))
    absent = parse_form(says(kind="question", confidence=0.9, quote="x"))
    many = parse_form(
        says(
            kind="question",
            confidence=0.9,
            quote="x",
            phones=[f"+1 212 555 01{n:02}" for n in range(9)],
        )
    )

    assert listed is not None
    assert lone is not None
    assert absent is not None
    assert many is not None
    assert listed.phones == ("+44 20 7946 0123",), "мусор списка отброшен"
    assert lone.phones == ("+48 22 123 45 67",), "один номер строкой — не потерян"
    assert absent.phones == ()
    assert len(many.phones) == MAX_PHONES


# --- сверка кодом: дословно, целиком, 7–15 цифр любой письменности -------------------------


@pytest.mark.parametrize(
    ("text", "phone", "kept"),
    [
        ("Call me on +44 20 7946 0123 after 2 pm.", "+44 20 7946 0123", "+44 20 7946 0123"),
        ("Позвоните: +7 (495) 123-45-67, обсудим.", "+7 (495) 123-45-67", "+7 (495) 123-45-67"),
        ("тел. 8 (812) 312-34-56", "8 (812) 312-34-56", "8 (812) 312-34-56"),
        ("我的手机是+86 139 1234 5678，下午都方便。", "+86 139 1234 5678", "+86 139 1234 5678"),
        ("Call me on +971501234567.", "+971501234567", "+971501234567"),
        ("اتصل بي على ٠٥٠١٢٣٤٥٦٧", "٠٥٠١٢٣٤٥٦٧", "٠٥٠١٢٣٤٥٦٧"),
        ("تلفن: ۰۲۱ ۱۲۳۴ ۵۶۷۸", "۰۲۱ ۱۲۳۴ ۵۶۷۸", "۰۲۱ ۱۲۳۴ ۵۶۷۸"),
        ("โทร ๐๘๑ ๒๓๔ ๕๖๗๘ ครับ", "๐๘๑ ๒๓๔ ๕๖๗๘", "๐๘๑ ๒๓๔ ๕๖๗๘"),
        ("جوال: ‎+٩٧١ ٥٠ ١٢٣ ٤٥٦٧", "‎+٩٧١ ٥٠ ١٢٣ ٤٥٦٧", "+٩٧١ ٥٠ ١٢٣ ٤٥٦٧"),
        ("Tél. : 01 99 00 12 34", "01 99 00 12 34", "01 99 00 12 34"),
        ("Rufen Sie an: +49 30 1234 5678.", "+49 30 1234 5678.", "+49 30 1234 5678"),
        ("Tel 123 4567", "123 4567", "123 4567"),
        ("Phone +123 456 789 012 345 any time", "+123 456 789 012 345", "+123 456 789 012 345"),
    ],
    ids=[
        "uk",
        "ru-brackets",
        "trunk-8-brackets",
        "zh-glued",
        "ae-no-spaces",
        "ar-eastern-digits",
        "fa-persian-digits",
        "th-digits",
        "rtl-mark",
        "fr-nbsp",
        "sentence-period",
        "seven-digits",
        "fifteen-digits",
    ],
)
def test_verbatim_whole_number_of_seven_to_fifteen_digits_is_kept(
    text: str, phone: str, kept: str
) -> None:
    assert phones_of((phone,), text) == ((kept,), ())


@pytest.mark.parametrize(
    ("text", "phone", "why"),
    [
        ("Call me on +44 20 7946 0123.", "+44 20 7946 0999", NOT_FOUND),
        ("Zadzwoń: +48 22 123 45 67", "+48 22 1234567", NOT_FOUND),
        ("ИНН 7701234567, КПП 770101001", "ИНН 7701234567", NOT_A_PHONE),
        ("VAT Reg. No. GB 123 4567 89", "GB 123 4567 89", NOT_A_PHONE),
        ("р/с 40702810123450000001", "40702810123450000001", NOT_A_PHONE),
        ("Postcode 050000", "050000", NOT_A_PHONE),
        ("Tel.: +49 30 1234 5678", "30 1234 5678", A_PIECE),
        ("моб.: +7 916 765 43 21", "7 916 765 43 21", A_PIECE),
        ("Tel.: +49 30 1234 5678", "+49 30 1234", A_PIECE),
    ],
    ids=[
        "made-up",
        "respaced",
        "inn-with-label",
        "vat-with-letters",
        "account-20-digits",
        "postcode-6-digits",
        "head-cut-off",
        "plus-lost",
        "tail-cut-off",
    ],
)
def test_number_that_is_not_a_verbatim_whole_phone_is_dropped_with_a_note(
    text: str, phone: str, why: str
) -> None:
    assert phones_of((phone,), text) == ((), (why,))


def test_same_number_is_kept_once_and_a_note_said_once() -> None:
    text = "Cep: +90 532 765 43 21\nWhatsApp: +٩٠ ٥٣٢ ٧٦٥ ٤٣ ٢١\nTel: +90 212 312 34 56"

    kept, notes = phones_of(
        ("+90 532 765 43 21", "+٩٠ ٥٣٢ ٧٦٥ ٤٣ ٢١", "+90 212 312 34 56", "+1 1", "+2 2"), text
    )

    assert kept == ("+90 532 765 43 21", "+90 212 312 34 56"), "те же цифры — один номер"
    assert notes == (NOT_A_PHONE,)


async def test_dropped_number_is_noted_and_the_kind_keeps_its_confidence() -> None:
    model = Model(
        says(
            kind="question",
            confidence=0.91,
            quote="Сколько стоит аудит?",
            phones=["+7 916 123 45 67", "+7 999 000 00 00"],
        )
    )
    kinds = client(model)

    found = await kinds.classify(text="Сколько стоит аудит? Тел. +7 916 123 45 67", subject="Re:")
    await kinds.aclose()

    assert isinstance(found, KindFound)
    assert found.phones == ("+7 916 123 45 67",)
    assert NOT_FOUND in found.notes
    assert found.confidence == pytest.approx(0.91), "вид от номера не зависит"


# --- вход модели: подпись видна, цитата нашего письма — нет ---------------------------------


async def test_model_sees_the_signature_after_the_dashes_but_not_our_quoted_letter() -> None:
    text = (
        "Das klingt interessant, schicken Sie mir mehr Informationen.\n\n-- \nAnna Schmidt\n"
        "Tel.: +49 30 1234 5678\n\n"
        "On Mon, 5 Oct 2026 at 10:00, Sales <sales@mail.example.test> wrote:\n"
        "> Questions? Call us: +1 (212) 555-0199\n"
    )
    model = Model(
        says(
            kind="interested",
            confidence=0.9,
            quote="schicken Sie mir mehr Informationen",
            phones=["+49 30 1234 5678", "+1 (212) 555-0199"],
        )
    )
    kinds = client(model)

    found = await kinds.classify(text=text, subject="Re: kurze Frage")
    await kinds.aclose()

    assert "+49 30 1234 5678" in model.user, "подпись после «-- » модель видит"
    assert "555-0199" not in model.user, "цитата нашего письма снята"
    assert isinstance(found, KindFound)
    assert found.phones == ("+49 30 1234 5678",)
    assert NOT_FOUND in found.notes, "номер из цитаты — не из письма человека"
    assert reply_kind.own_words("  \n> только цитата\n") == "> только цитата"


async def test_reply_that_is_only_our_quoted_letter_gives_no_number_to_call() -> None:
    """Своего текста нет — модель видит цитату целиком (`own_words`), и номер оттуда наш: лида
    по нему не передают."""
    text = (
        "On Mon, 5 Oct 2026 at 10:00, Sales <sales@mail.example.test> wrote:\n"
        "> Questions? Call us: +1 (212) 555-0199\n"
    )
    model = Model(
        says(kind="question", confidence=0.9, quote="Questions?", phones=["+1 (212) 555-0199"])
    )
    kinds = client(model)

    found = await kinds.classify(text=text, subject="Re: kurze Frage")
    await kinds.aclose()

    assert isinstance(found, KindFound)
    assert found.phones == (), "номер из цитаты нашего письма — не номер для связи"
    assert reply_kind.QUOTED_ONLY in found.notes
    assert decide(found, 0.8).route is not Route.HANDOFF


# --- путь: телефон — передача при любой уверенности, кроме отказов -------------------------


CALLABLE = [
    kind for kind in SalesKind if kind not in NO_CALL and kind is not SalesKind.PARSE_FAILED
]


@pytest.mark.parametrize("kind", CALLABLE, ids=[kind.value for kind in CALLABLE])
def test_phone_with_any_kind_but_a_refusal_is_a_handoff_even_at_half_confidence(
    kind: SalesKind,
) -> None:
    decision = decide(_found(kind, 0.5, "+90 212 123 45 67"), 0.8)

    assert (decision.route, decision.waits) == (Route.HANDOFF, True)
    assert decision.reason.endswith(f"(уверенность 50% ниже порога 80%), {PHONE_WORDS}")


@pytest.mark.parametrize(
    ("kind", "confident"),
    [
        (SalesKind.NOT_INTERESTED, Route.CLOSED),
        (SalesKind.NOT_NOW, Route.CLOSED),
        (SalesKind.UNSUBSCRIBE, Route.UNSUBSCRIBE),
    ],
    ids=["not-interested", "not-now", "unsubscribe"],
)
def test_refusal_and_unsubscribe_with_a_phone_are_never_a_handoff(
    kind: SalesKind, confident: Route
) -> None:
    phone = "+1 (212) 555-0123"

    assert decide(_found(kind, 0.5, phone), 0.8).route is Route.MANUAL
    assert decide(_found(kind, 0.95, phone), 0.8).route is confident


def test_parse_failed_waits_for_a_human_whatever_it_holds() -> None:
    decision = decide(_found(SalesKind.PARSE_FAILED, 0.0, "+48 22 123 45 67"), 0.8)

    assert (decision.route, decision.waits) == (Route.MANUAL, True)


def test_phone_next_to_another_address_is_a_handoff_that_names_the_address() -> None:
    found = _found(SalesKind.REFERRAL, 0.92, "+380 67 123 45 67", contact="anna@shop.example.test")

    decision = decide(found, 0.8)

    assert decision.route is Route.HANDOFF
    assert decision.reason == (
        f"назвал другого человека, {PHONE_WORDS}; "
        "назван адрес anna@shop.example.test — новый лид не заводится"
    )


# --- задача ответа: передача после записи, нового лида нет ----------------------------------


async def test_question_with_a_phone_below_the_threshold_is_handed_over_by_the_job(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession
) -> None:
    dialog = await sales_dialog(session, text="Сколько стоит аудит?\n\n-- \nИван\n+7 916 123 45 67")
    found = _found(SalesKind.QUESTION, 0.55, "+7 916 123 45 67")
    queued: list[int] = []
    start = partial(handoff.start, enqueue=queued.append, now=lambda: AT)
    _job_on_test_base(monkeypatch, session, found, start, [])

    report = await sales_jobs.handle(dialog.reply.id)

    assert (report["kind"], report["route"]) == ("question", "handoff")
    row = await session.scalar(
        select(SalesHandoffModel).where(SalesHandoffModel.thread_id == dialog.thread.id)
    )
    assert row is not None, "передача заведена"
    assert queued == [row.id], "и её задача поставлена"
    reply = await session.get(ReplyModel, dialog.reply.id, populate_existing=True)
    assert reply is not None
    snap = reply.model_parse or {}
    assert (snap["phones"], snap["prompt_version"]) == (["+7 916 123 45 67"], "sales-reply-kind-v3")
    review = review_of(reply, Stage.SALES)
    assert review.waiting is False, "передан — человеку писать не надо"
    assert HANDED_OVER in review.reason
    assert review.reason.startswith(f"задал вопрос, {GAVE_PHONE}: "), "итог называет телефон"


async def test_referral_with_a_phone_is_handed_over_and_no_lead_is_made_for_the_address(
    session: AsyncSession,
) -> None:
    text = "Це не до мене. Пишіть маркетологу: anna@shop.example.test, тел. +380 67 123 45 67"
    reply = await _sales_reply(session, text)
    model = Model(
        says(
            kind="referral",
            confidence=0.93,
            quote="Пишіть маркетологу: [address 1]",
            contact="[address 1]",
            phones=["+380 67 123 45 67"],
        )
    )
    kinds = client(model)
    handover = Handover()
    sales = SalesReplies(session, kinds, hand_over=handover, threshold=0.8)

    handled = await sales.handle(reply.id)
    passed = await sales.pass_on(handled)
    await kinds.aclose()

    assert (handled.route, handled.handoff_thread) == ("handoff", reply.thread_id)
    assert "назван адрес anna@shop.example.test — новый лид не заводится" in (handled.reason or "")
    assert handover.threads == [reply.thread_id]
    assert passed.waits is False, "передан — ответ человека не ждёт"
    leads = await session.scalars(
        select(SalesLeadModel).where(SalesLeadModel.email == "anna@shop.example.test")
    )
    assert list(leads) == [], "нового лида на чужой адрес нет"
    thread = await session.get(ThreadModel, reply.thread_id)
    assert thread is not None
    assert thread.status is not ThreadStatus.CLOSED, "диалог не закрыт, как у «пишите другому»"


@pytest.mark.parametrize(
    ("kind", "route"),
    [("not_interested", "closed"), ("not_now", "closed"), ("unsubscribe", "unsubscribe")],
)
async def test_refusal_with_a_phone_in_the_signature_is_not_handed_over(
    session: AsyncSession, kind: str, route: str
) -> None:
    text = "Спасибо, не актуально.\n\n-- \nОльга\nтел. 8 (812) 312-34-56"
    model = Model(
        says(
            kind=kind, confidence=0.94, quote="Спасибо, не актуально.", phones=["8 (812) 312-34-56"]
        )
    )
    reply = await _sales_reply(session, text)
    kinds = client(model)

    handled = await SalesReplies(session, kinds, hand_over=Handover(), threshold=0.8).handle(
        reply.id
    )
    await kinds.aclose()

    assert (handled.route, handled.handoff_thread) == (route, None)
    assert (reply.model_parse or {})["phones"] == ["8 (812) 312-34-56"], "номер в снимке есть"


async def test_answer_the_model_did_not_parse_waits_for_a_human_with_a_number_in_it(
    session: AsyncSession,
) -> None:
    reply = await _sales_reply(session, "Hola, 612 345 678. Lucía")
    kinds = client(Model("это не JSON"))

    handled = await SalesReplies(session, kinds, hand_over=Handover(), threshold=0.8).handle(
        reply.id
    )
    await kinds.aclose()

    assert (handled.kind, handled.route, handled.waits) == ("parse_failed", "manual", True)
    assert handled.handoff_thread is None


def test_snapshot_keeps_the_phones_and_the_v3_version() -> None:
    snap = snapshot(_found(SalesKind.INTERESTED, 0.9, "+48 22 123 45 67"), model="m")

    assert (snap["phones"], snap["prompt_version"]) == (["+48 22 123 45 67"], "sales-reply-kind-v3")


# --- карточка телемаркетолога: строка «Телефон: …» ------------------------------------------


def _card(**fields: Any) -> Card:
    base: dict[str, Any] = {
        "thread_id": 7,
        "lead_id": 3,
        "email": "ivan@acme.example.test",
        "name": "Иван",
        "company": "Акме",
        "site": "acme.example.test",
        "hypothesis": "гипотеза",
        "country": "pl",
        "language": "ru",
        "reply_id": 11,
        "reply_at": datetime(2026, 10, 26, 9, 17, tzinfo=UTC),
        "reply_subject": "Re: вопрос",
        "reply_text": "Dzień dobry, +48 601 234 567, Piotr",
        "sent": 1,
        "replies": 1,
        "first_sent_at": None,
    }
    return Card(**{**base, **fields})


def test_message_ends_with_the_phone_line_and_without_phones_stays_three_lines() -> None:
    phones = ("+48 601 234 567", "+48 22 123 45 67")

    with_phone = wording.message(DEAL, 7, HandoffKommo.DONE, phones=phones).split("\n")
    without = wording.message(DEAL, 7, HandoffKommo.DONE)

    assert with_phone == [
        "Новый лид",
        "Эмейл Рассылка",
        f"Ссылка на сделку в коммо: {DEAL}",
        "Телефон: +48 601 234 567, +48 22 123 45 67",
    ]
    assert without.count("\n") == 2
    assert "Телефон" not in without


def test_note_has_the_phone_line_right_after_the_sender_and_none_without_phones() -> None:
    lines = wording.note(_card(phones=("+48 601 234 567",))).split("\n")

    assert lines[1:3] == ["От: ivan@acme.example.test", "Телефон: +48 601 234 567"]
    assert not any(line.startswith("Телефон") for line in wording.note(_card()).split("\n"))


def test_card_takes_every_number_of_the_dialog_once_by_digits() -> None:
    snaps: list[dict[str, Any] | None] = [
        {"stage": Stage.SALES.value, "phones": ["+971 50 123 4567"]},
        None,
        {"stage": "donors", "phones": ["+44 20 7946 0123"]},
        {"stage": Stage.SALES.value, "kind": None},
        {"stage": Stage.SALES.value, "phones": ["+٩٧١ ٥٠ ١٢٣ ٤٥٦٧", "054-123-4567", 7, ""]},
    ]

    assert handoff_card.phones(snaps) == ("+971 50 123 4567", "054-123-4567")


async def test_telemarketer_gets_the_phone_in_telegram_and_in_the_deal_note(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    dialog = await sales_dialog(session, text="Dzień dobry, +48 601 234 567, Piotr")
    dialog.reply.model_parse = {
        "stage": Stage.SALES.value,
        "kind": "wants_to_talk",
        "phones": ["+48 601 234 567"],
    }
    kommo = KommoFixture()

    await hand_off(session, dialog, deps(http, alerts, kommo))

    message = f"{HEAD}Ссылка на сделку в коммо: {DEAL}\nТелефон: +48 601 234 567"
    assert sent(api) == [(PERSONAL, message), (GROUP, message)]
    [note] = kommo.leads[9301].notes.values()
    assert "Телефон: +48 601 234 567" in note.split("\n")


async def test_new_number_from_a_lead_already_handed_over_reaches_the_telemarketer(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    dialog = await sales_dialog(session)
    work = deps(http, alerts, KommoFixture())
    await hand_off(session, dialog, work)
    later = await answer(
        session,
        dialog.thread,
        dialog.message,
        "Лучше звоните на мобильный: +7 916 765 43 21",
        at=NOW + timedelta(hours=2),
    )
    later.model_parse = {
        "stage": Stage.SALES.value,
        "kind": "wants_to_talk",
        "phones": ["+7 916 765 43 21"],
    }

    await hand_off(session, dialog, work)

    again = f"{AGAIN}Ссылка на сделку в коммо: {DEAL}\nТелефон: +7 916 765 43 21"
    assert sent(api)[2:] == [(PERSONAL, again), (GROUP, again)]


# --- eval: номера и путь меряются рядом с воротами видов ------------------------------------


def test_eval_counts_phones_false_numbers_and_handoffs_outside_the_gates() -> None:
    talk = {"id": "talk", "expect": {"kind": "wants_to_talk", "phones": ["+48 22 123 45 67"]}}
    ids = {"id": "ids", "expect": {"kind": "question"}}
    refusal = {"id": "no", "expect": {"kind": "not_interested", "phones": ["+1 212 555 0123"]}}
    results = [
        ev.judged(talk, _found(SalesKind.INTERESTED, 0.6, "+48 22 123 45 67"), 0.8),
        ev.judged(ids, _found(SalesKind.QUESTION, 0.9, "7701234567"), 0.8),
        ev.judged(refusal, _found(SalesKind.NOT_INTERESTED, 0.9, "+1 212 555 0123"), 0.8),
    ]

    lines = ev.phones(results)

    assert lines[0].startswith("Телефоны (вне ворот): номеров найдено 2/2, не найдены в 0")
    assert lines[1] == "  ложные номера в 1 [('ids', '7701234567')]"
    assert lines[2].startswith("  передача при телефоне 1/1")
    assert lines[3].startswith("  не передача на отказах 1/1")
    assert not any(result.dangerous for result in results), "передача — путь «хочет говорить»"


def test_guarded_kind_sent_another_way_by_a_phone_is_dangerous_and_rescued_by_it_is_not() -> None:
    talk = {"id": "t", "expect": {"kind": "wants_to_talk"}}
    stop = {"id": "s", "expect": {"kind": "unsubscribe"}}

    rescued = ev.judged(talk, _found(SalesKind.INTERESTED, 0.9, "+90 212 123 45 67"), 0.8)
    lost = ev.judged(talk, _found(SalesKind.INTERESTED, 0.9), 0.8)
    called = ev.judged(stop, _found(SalesKind.QUESTION, 0.4, "+90 212 123 45 67"), 0.8)

    assert (rescued.dangerous, lost.dangerous, called.dangerous) == (False, True, True)
