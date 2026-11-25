"""Служба обещает ответить позже — автоответчик и без номера заявки.

Проверка прода 25.11.2026: среди ответов рекламодателей, ждавших человека, — подтверждения
служб поддержки без номера заявки («Thank you for contacting us… a member of our Customer Service
Team will review your request»), подтверждения, чей номер правило не узнавало («Your
ticket ID is …», «We’ve created a new ticket (#…)»), оценка чата и письмо из одной точки.
Все они ждали человека на экране, хотя человека за ними нет. Повтор нового разбора на всех
ответах прода изменил только их: ни один живой ответ доноров, рекламодателей и продаж
автоответом не стал.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from backend.features.core.domain import ReplyKind, Stage
from backend.features.replies import classify, outcome, phrases, ticket_phrases
from backend.features.replies.inbound import Attachment, Incoming

OUR_SUBJECT = "Re: Paying less for placements like yours on donor.test"
PROMISE = "служба обещает ответить"
RECEIPT = "подтверждение заявки"
GOLDEN = Path(__file__).parent.parent / "scripts" / "data" / "reply_parse_golden.jsonl"


def reply(text: str, *, subject: str = OUR_SUBJECT) -> Incoming:
    return Incoming(
        message_id="<reply-1@brand.test>",
        to=("alex+m417.7d3a91c2e5@replies.ours.test",),
        from_email="help@brand.test",
        subject=subject,
        text=text,
        headers={},
    )


def verdict(text: str, *, subject: str = OUR_SUBJECT) -> classify.Verdict:
    return classify.classify(reply(text, subject=subject))


# --- подтверждения узнаются ---------------------------------------------------

#: Виды подтверждений с прода 25.11.2026 (имена брендов и номера заменены).
PROD_LIKE = {
    "команда сервиса, без номера": (
        "Thank you for contacting us. Your enquiry is important to us. We’ve received your email, "
        "and a member of our Customer Service Team will review your request and get back to you "
        "as soon as possible. In the meantime, you may find a quick answer to your question in our "
        "Help Centre. Kind regards, The Brand Customer Service Team",
        PROMISE,
    ),
    "бот поддержки одной строкой": (
        "Welcome to The Brand. Our support team will respond to you shortly. Regards, The Brand",
        PROMISE,
    ),
    "агент поддержки после проверки данных": (
        "Welcome to Brand, and thank you for opening a new request! For your security, please "
        "confirm your full name and date of birth. Once you share these details, one of our support "
        "agents will assist you shortly.",
        PROMISE,
    ),
    "приём и скорый ответ, без лица службы": (
        "Hi, Thank you for contacting the Brand Help Desk. We've received your request and will "
        "get back to you with a response as quickly as possible. Our average response time is "
        "within 24 hours. Cheers, Brand Support Team",
        PROMISE,
    ),
    "номер как ticket ID": (
        "Dear Player, Thanks for contacting Brand Support. We have received your message and will "
        "get back to you as soon as possible. Your ticket ID is 48213-E.",
        RECEIPT,
    ),
    "служба завела заявку сама": (
        "Dear Customer, We’ve created a new ticket (#48213) based on your email today. You will "
        "be notified via email once an agent responds.",
        RECEIPT,
    ),
    "оценка чата": ("Rate your conversation", "оценка разговора"),
    "письмо из одной точки": (".", "письмо из знаков препинания"),
}


@pytest.mark.parametrize(("text", "rule"), PROD_LIKE.values(), ids=PROD_LIKE.keys())
def test_support_receipts_without_a_number_are_auto_replies(text: str, rule: str) -> None:
    got = verdict(text)

    assert got.kind is ReplyKind.AUTO_REPLY
    assert got.rule == rule


#: Обещание лицом службы на девяти языках.
EACH_LANGUAGE = {
    "en": "Thanks for your email. Our customer service team will get back to you shortly.",
    "de": "Vielen Dank für Ihre Nachricht. Unser Kundenservice wird sich bald bei Ihnen melden.",
    "fr": "Merci pour votre message. Notre service client vous répondra rapidement.",
    "es": "Gracias por su mensaje. Nuestro equipo de soporte se pondrá en contacto con usted en breve.",
    "it": "Grazie per il messaggio. Il nostro servizio clienti ti risponderà al più presto.",
    "pt": "Obrigado pela mensagem. Nossa equipe de suporte entrará em contato em breve.",
    "nl": "Bedankt voor uw bericht. Ons klantenservice neemt spoedig contact met u op.",
    "pl": "Dziękujemy za wiadomość. Nasz zespół wsparcia odpowie wkrótce.",
    "ru": "Спасибо за письмо. Наша служба поддержки свяжется с вами в ближайшее время.",
}


@pytest.mark.parametrize("text", EACH_LANGUAGE.values(), ids=EACH_LANGUAGE.keys())
def test_service_promise_in_each_language(text: str) -> None:
    got = verdict(text)

    assert got.kind is ReplyKind.AUTO_REPLY, got.rule
    assert got.rule == PROMISE


#: «Ответим как можно скорее» вместе с фразой о приёме — на девяти языках.
ANSWER_LATER_EACH = {
    "en": "We have received your message and will reply as soon as possible.",
    "de": "Wir haben Ihre Nachricht erhalten und melden uns so schnell wie möglich.",
    "fr": "Nous avons bien reçu votre message et nous vous répondrons dans les plus brefs délais.",
    "es": "Hemos recibido su mensaje y le responderemos lo antes posible.",
    "it": "Abbiamo ricevuto la sua richiesta e le risponderemo al più presto.",
    "pt": "Recebemos a sua mensagem e responderemos o mais breve possível.",
    "nl": "Wij hebben uw bericht ontvangen en nemen zo snel mogelijk contact met u op.",
    "pl": "Otrzymaliśmy twoje zgłoszenie i odpowiemy najszybciej jak to możliwe.",
    "ru": "Ваше обращение получено, мы ответим вам в ближайшее время.",
}


@pytest.mark.parametrize("text", ANSWER_LATER_EACH.values(), ids=ANSWER_LATER_EACH.keys())
def test_answer_later_with_a_receipt_in_each_language(text: str) -> None:
    assert verdict(text).kind is ReplyKind.AUTO_REPLY


@pytest.mark.parametrize("stage", list(Stage))
def test_promise_keeps_the_chain_going(stage: Stage) -> None:
    """Как любой автоответ: человека не ждёт и добивки не обрывает — на каждом этапе."""
    got = verdict(EACH_LANGUAGE["en"])
    then = outcome.decide(got.kind, stage=stage)

    assert not classify.stops_chain(got.kind)
    assert not then.stop_chain
    assert not then.needs_review


# --- живые ответы остаются живыми ---------------------------------------------


def test_forwarded_to_a_colleague_is_a_human() -> None:
    """«Передал маркетологу, он ответит на днях» пишет человек: ни лица службы, ни приёма."""
    got = verdict(
        "Thanks, I've forwarded this to our marketing team, they will get back to you shortly."
    )

    assert got.kind is ReplyKind.HUMAN


def test_answer_later_without_a_receipt_is_a_human() -> None:
    got = verdict("Interesting offer. We will get back to you as soon as possible with our terms.")

    assert got.kind is ReplyKind.HUMAN


def test_long_business_reply_with_a_service_promise_is_a_human() -> None:
    """В длинном деловом ответе обещание службы — часть дела: потолок длины решает."""
    promise = "Our support team will get back to you shortly."
    business = (
        "We publish sponsored reviews on our site and can include your brand next month. " * 11
    )

    assert verdict(promise).kind is ReplyKind.AUTO_REPLY
    assert len(business) > classify.PROMISE_MAX_CHARS
    assert verdict(business + promise).kind is ReplyKind.HUMAN


def test_sum_outweighs_a_service_promise() -> None:
    got = verdict("Our support team will respond shortly. A sponsored post is 150 USD.")

    assert got.kind is ReplyKind.HUMAN
    assert got.rule == "сумма в тексте важнее фразы"


def test_price_file_outweighs_a_service_promise() -> None:
    incoming = replace(
        reply("Our support team will respond shortly. Rates attached."),
        attachments=(Attachment(name="rates.pdf", size=9000, content_type="application/pdf"),),
    )

    assert classify.classify(incoming).kind is ReplyKind.HUMAN


def test_rating_line_inside_a_real_answer_is_a_human() -> None:
    text = (
        "Hi Alex, we can place your article next week; the editor will review it first and we "
        "publish on Mondays. Please rate your experience with our editors after publication, it "
        "helps us improve. Send the draft when ready."
    )

    assert len(text) > classify.RATING_MAX_CHARS
    assert verdict(text).kind is ReplyKind.HUMAN


@pytest.mark.parametrize("text", ["Ok", "100", "Да", "👍"])
def test_a_word_a_number_or_an_emoji_is_an_answer(text: str) -> None:
    """«👍» — «да» человека, а не «.» чата поддержки (ревью продаж 25.11.2026)."""
    assert verdict(text).kind is ReplyKind.HUMAN


def test_answer_written_in_the_subject_is_a_human() -> None:
    """Пустое тело бывает у ответа, написанного в теме."""
    got = verdict("", subject="Re: Price monitoring - yes, call me tomorrow")

    assert got.kind is ReplyKind.HUMAN


#: Пробы продаж 25.11.2026: лицо службы, обещание или «opened a request» по отдельности пишет и
#: человек — «пишите другому», «посмотрим на следующей неделе», вежливый отказ.
LIVE_PROBES = {
    "пишите другому": (
        "Hi, I'm not the right person for this. The customer service team will be able to help "
        "you, please write to them directly."
    ),
    "посмотрим позже": (
        "Thanks for the offer. Our support team will review it next week and we will let you know."
    ),
    "вежливый отказ": (
        "Not interested right now, sorry. Our service team will keep your contact on file."
    ),
    "передал коллегам": "Спасибо, передал ваше письмо коллегам. Служба поддержки ответит вам.",
    "посмотрят предложение": "Danke, unser Support-Team wird sich Ihr Angebot ansehen.",
    "заявка в закупки": (
        "Thank you, we have opened a request with our procurement department and will get back to "
        "you as soon as possible."
    ),
}


@pytest.mark.parametrize("text", LIVE_PROBES.values(), ids=LIVE_PROBES.keys())
def test_live_answers_with_service_words_stay_human(text: str) -> None:
    assert verdict(text).kind is ReplyKind.HUMAN


def test_short_form_of_will_is_a_promise_too() -> None:
    got = verdict("We have received your email and we'll get back to you shortly.")

    assert (got.kind, got.rule) == (ReplyKind.AUTO_REPLY, PROMISE)


def test_golden_replies_stay_human() -> None:
    """Эталон разбора — живые ответы доноров: новые правила их не трогают."""
    cases = [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line]

    wrong = {
        case["id"]: got.rule
        for case in cases
        if (got := verdict(case["text"])).kind is not ReplyKind.HUMAN
    }

    assert not wrong


@pytest.mark.parametrize(
    "table",
    [
        ticket_phrases.SERVICE_TEAM,
        ticket_phrases.PROMISE_VERB,
        ticket_phrases.PROMISE_TIME,
        ticket_phrases.CHAT_RATING,
    ],
)
def test_every_promise_dictionary_knows_every_language(table: dict[str, tuple[str, ...]]) -> None:
    """Язык, забытый в словаре, — рынок, где подтверждение службы снова ждёт человека."""
    assert set(table) == set(phrases.LANGUAGES)
    assert all(table[lang] for lang in phrases.LANGUAGES)
