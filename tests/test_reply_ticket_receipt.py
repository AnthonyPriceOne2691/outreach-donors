"""Подтверждение заявки системой поддержки — автоответчик, а не ответ человека.

Проверка прода 06–09.11.2026: три подтверждения служб поддержки пришли с
обычного `support@`, без заголовков автомата и без слов автоответчика — и
стояли ответом человека: ждали его на экране, а добивки по ним оборвались.
Правило узнаёт их по двум признакам сразу — фразе о приёме обращения и номеру
заявки, — потому что каждый по отдельности пишет и живой человек.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from backend.features.core.domain import ReplyKind, Stage
from backend.features.replies import classify, outcome, phrases, ticket_phrases
from backend.features.replies.inbound import Attachment, Incoming

OUR_SUBJECT = "Re: Guest article on donor.test"
RULE = "подтверждение заявки"
GOLDEN = Path(__file__).parent.parent / "scripts" / "data" / "reply_parse_golden.jsonl"


def reply(text: str, *, subject: str = OUR_SUBJECT, sender: str = "support@donor.test") -> Incoming:
    return Incoming(
        message_id="<reply-1@donor.test>",
        to=("anna+m417.7d3a91c2e5@replies.ours.test",),
        from_email=sender,
        subject=subject,
        text=text,
        headers={},
    )


def verdict(text: str, **extra: str) -> classify.Verdict:
    return classify.classify(reply(text, **extra))


# --- подтверждения узнаются ---------------------------------------------------

#: Три вида подтверждений с прода (адреса, имена и номера заменены).
PROD_LIKE = {
    "заявка закрыта, оцените": (
        "#300000417 Заявка закрыта: ${Тема}",
        "\u200b\u200bЗдравствуйте.\n\nБлагодарим за интерес проявленный к нашей компании, ваше обращение было\n"
        "передано в ответственный отдел. В случае заинтересованности с вами свяжутся.\n\n"
        "Пожалуйста, оцените качество обслуживания\n1\n<https://rate.test/1>",
    ),
    "номер в квадратных скобках": (
        "[#48213] Re: Кто отвечает за продвижение donor.test?",
        "Ваше обращение в службу поддержки #48213 получено. Мы уже занимаемся вашим вопросом, "
        "ожидайте ответа в ближайшее время!",
    ),
    "case assigned": (
        "Support :: Case Creation Confirmation - Case #48213",
        "Thank you for contacting us. Your case has been assigned to an agent who will respond "
        "shortly. Your case number is 48213.",
    ),
}


@pytest.mark.parametrize(("subject", "text"), PROD_LIKE.values(), ids=PROD_LIKE.keys())
def test_support_desk_receipts_from_prod_are_auto_replies(subject: str, text: str) -> None:
    got = verdict(text, subject=subject)

    assert got.kind is ReplyKind.AUTO_REPLY
    assert got.rule == RULE


#: Синтетика на девяти языках: номер в теме, фраза о приёме в тексте.
EACH_LANGUAGE = {
    "en": (
        "[#48213] Re: Guest article",
        "Hello, your request has been received and a ticket was opened.",
    ),
    "de": (
        "Re: Gastartikel [Ticket#2024110912]",
        "Vielen Dank, Ihre Anfrage ist bei uns eingegangen.",
    ),
    "fr": (
        "Re: Article invité - Ticket n° 48213",
        "Bonjour, votre demande a bien été enregistrée.",
    ),
    "es": (
        "[#48213] Re: Artículo de invitado",
        "Hola, su solicitud ha sido recibida y será atendida.",
    ),
    "it": (
        "Re: Articolo ospite (TK-48213)",
        "Gentile cliente, la sua richiesta è stata presa in carico.",
    ),
    "pt": (
        "Re: Artigo convidado - Chamado #48213",
        "Olá, recebemos a sua solicitação e retornaremos.",
    ),
    "nl": ("Re: Gastartikel [#48213]", "Beste, wij hebben uw aanvraag in goede orde ontvangen."),
    "pl": (
        "Re: Artykuł gościnny - zgłoszenie nr 48213",
        "Dzień dobry, Twoje zgłoszenie zostało przyjęte.",
    ),
    "ru": ("Re: Гостевая статья [#48213]", "Здравствуйте! Ваше обращение зарегистрировано."),
}


@pytest.mark.parametrize(("subject", "text"), EACH_LANGUAGE.values(), ids=EACH_LANGUAGE.keys())
def test_receipt_in_each_language(subject: str, text: str) -> None:
    got = verdict(text, subject=subject)

    assert got.kind is ReplyKind.AUTO_REPLY, got.rule
    assert got.rule == RULE


@pytest.mark.parametrize(
    "text",
    [
        "Ваше обращение принято и зарегистрировано под номером 4512.",
        "Your ticket has been created. Ticket number: 48213.",
    ],
)
def test_number_in_the_text_is_enough(text: str) -> None:
    """Номер бывает только в тексте: тема у подтверждения — наша."""
    assert verdict(text).rule == RULE


@pytest.mark.parametrize("stage", list(Stage))
def test_receipt_keeps_the_chain_going(stage: Stage) -> None:
    """«Соответствующая реакция логики»: подтверждение не ждёт человека и
    добивки не обрывает — как любой автоответ, на каждом этапе."""
    subject, text = PROD_LIKE["номер в квадратных скобках"]
    got = verdict(text, subject=subject)
    then = outcome.decide(got.kind, stage=stage)

    assert not classify.stops_chain(got.kind)
    assert not then.stop_chain
    assert not then.needs_review


# --- живые ответы остаются живыми ---------------------------------------------


def test_thanks_without_a_number_is_a_human() -> None:
    """«Письмо получили, передал маркетологу» пишет человек: номера заявки нет."""
    assert (
        verdict("Ваше письмо получили, передал маркетологу, он вам напишет.").kind
        is ReplyKind.HUMAN
    )


def test_ticket_number_without_a_receipt_is_a_human() -> None:
    """Контакт-центр отвечает по существу в заявку с номером — это ответ, а не подтверждение."""
    got = verdict(
        "Данный вопрос не находится в компетенции специалистов Контакт-центра. "
        "Вы можете обратиться в Управление.",
        subject="[Контакт-центр] (CC-500017) Кто отвечает за продвижение donor.test",
    )

    assert got.kind is ReplyKind.HUMAN


def test_a_year_after_a_ticket_word_is_not_a_number() -> None:
    """«In case 2025 plans change» — год, а не номер заявки."""
    got = verdict("Thanks, your message was received. In case 2025 plans change, we will write.")

    assert got.kind is ReplyKind.HUMAN


def test_sum_outweighs_a_receipt() -> None:
    """Живой сотрудник поддержки называет цену в ту же заявку: её разбирает модель."""
    got = verdict(
        "Your request has been received. A sponsored post is 150 USD.",
        subject="Re: [#48213] Guest article",
    )

    assert got.kind is ReplyKind.HUMAN
    assert got.rule == "сумма в тексте важнее фразы"


def test_price_file_outweighs_a_receipt() -> None:
    incoming = replace(
        reply(
            "Ваше обращение получено. Прайс во вложении.", subject="[#48213] Re: Гостевая статья"
        ),
        attachments=(Attachment(name="rates.pdf", size=9000, content_type="application/pdf"),),
    )

    assert classify.classify(incoming).kind is ReplyKind.HUMAN


def test_golden_replies_in_a_ticket_thread_stay_human() -> None:
    """Эталон разбора — живые ответы доноров: номер заявки в теме их не меняет."""
    cases = [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line]

    wrong = {
        case["id"]: got.rule
        for case in cases
        if (got := verdict(case["text"], subject="Re: [#48213] Guest article")).kind
        is not ReplyKind.HUMAN
    }

    assert not wrong


@pytest.mark.parametrize("table", [ticket_phrases.TICKET_RECEIPT, ticket_phrases.TICKET_WORD])
def test_every_ticket_dictionary_knows_every_language(table: dict[str, tuple[str, ...]]) -> None:
    """Язык, забытый в словаре, — рынок, где подтверждение заявки снова ждёт человека."""
    assert set(table) == set(phrases.LANGUAGES)
    assert all(table[lang] for lang in phrases.LANGUAGES)
