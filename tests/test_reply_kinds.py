"""Вид ответа на языках рынков: живой ответ важнее.

Живой ответ, принятый за служебное письмо, теряет цену молча: отказ
доставки хоронит адрес, отписка кладёт его в стоп-лист, автоответ
продолжает добивки тому, кто уже ответил, — и никто из них не ждёт
человека. Поэтому первый класс здесь — живые ответы, на которых правила
ошибались на стенде 28.09, и их ближайшие соседи. Остальные классы —
настоящие служебные письма на девяти языках: они узнаются.
"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path

import pytest
from backend.features.core.domain import ReplyKind
from backend.features.replies import classify, outcome, phrases
from backend.features.replies.inbound import Attachment, Incoming

OUR_SUBJECT = "Re: Guest article on donor.test"
AUTO_SUBMITTED = {"Auto-Submitted": "auto-replied"}
GOLDEN = Path(__file__).parent.parent / "scripts" / "data" / "reply_parse_golden.jsonl"


def reply(
    text: str,
    *,
    subject: str = OUR_SUBJECT,
    headers: dict[str, str] | None = None,
    sender: str = "editor@donor.test",
) -> Incoming:
    return Incoming(
        message_id="<reply-1@donor.test>",
        to=("anna+m417.7d3a91c2e5@replies.ours.test",),
        from_email=sender,
        subject=subject,
        text=text,
        headers=headers or {},
    )


def verdict(text: str, **extra: object) -> classify.Verdict:
    return classify.classify(reply(text, **extra))  # type: ignore[arg-type]


# --- живые ответы остаются живыми ---------------------------------------------

#: Живые ответы, которые правила принимали за служебные письма, и соседи
#: каждого: та же фраза без цены, в прошедшем времени, в другом языке.
LIVE_REPLIES = {
    # Стенд 28.09: цена 550–559 совпадала с кодом отказа SMTP.
    "цена 550": "Hi Anna,\n\nYes, we accept sponsored posts. The price is 550 USD per article, dofollow.",
    "цена 553 и телефон": "Our rate is 553 EUR, call me at +1 555 1234.",
    # Стенд: «does not exist» — про страницу, а не про ящик.
    "does not exist": "The page you mentioned does not exist anymore, but a new guest post is $120.",
    "страница не активна": "The link you mentioned is no longer active.",
    "редактор ушёл, цена есть": "Our editor is no longer active on this blog, but a post costs $90.",
    # Стенд: тревел-блог называет себя словом «vacation».
    "vacation в тексте": "Hi! We are a vacation rentals blog. Sponsored posts cost $150, dofollow.",
    "тема статьи": "We have a great article about out of office messages, would it fit?",
    "тема статьи auto-reply": "We have a post about Gmail auto-reply settings, would that fit?",
    "нет мест": "I'm out of guest post slots until November.",
    "вернулся en": "I was out of the office last week, sorry. What topic do you have in mind?",
    "вернулся de": "Ich war im Urlaub bis gestern. Welches Thema haben Sie?",
    "вернулся es": "Estuve fuera de la oficina, ¿qué tema tienen en mente?",
    "вернулся ru": "Я был в отпуске до вчера, какая тема статьи?",
    # Стенд: условие публикации начиналось так же, как отписка.
    "не пишите про казино, цена": "Не пишите статьи про казино. Остальные темы — 100 долларов.",
    "не пишите про казино": "Не пишите статьи про казино, остальные темы можно.",
    "не пишите нам про казино": "Не пишите нам про казино, остальное — пожалуйста.",
    "больше не пишем про казино": "Больше не пишем статьи про казино, но гостевой пост возможен.",
    "de условие": "Schicken Sie uns keine Artikel über Casino, sonst gerne.",
    "de новости": "Wir veröffentlichen keine Nachrichten mehr über Glücksspiel.",
    "fr условие": "Merci de ne pas nous envoyer d'articles sur le casino.",
    "es условие": "No nos escriba sobre casinos, otros temas sí.",
    "it условие": "Non scriveteci articoli sul casinò, il resto va bene.",
    "pt условие": "Não nos envie artigos sobre cassino, o resto pode.",
    "nl условие": "We publiceren geen berichten meer over casino.",
    "pl условие": "Nie piszcie do nas o kasynach, reszta tematów OK.",
    "pl счёт": "Czy mam wypisać fakturę VAT?",
    "en черновик": "Don't email me the draft, just share a Google Doc link.",
    "en тема": "Do not contact us about casino topics, everything else is fine.",
    "en копия": "Please remove me from CC, my colleague Jan handles this.",
    "en формат": "Please stop sending drafts as PDF, use Google Docs.",
}


@pytest.mark.parametrize("text", LIVE_REPLIES.values(), ids=LIVE_REPLIES.keys())
def test_live_reply_stays_human(text: str) -> None:
    got = verdict(text)

    assert got.kind is ReplyKind.HUMAN, got.rule


def test_human_reply_neither_buries_nor_stoplists() -> None:
    """Ради чего всё: у живого ответа адрес жив и не в стоп-листе."""
    got = outcome.decide(verdict(LIVE_REPLIES["цена 550"]).kind)

    assert not got.mark_contact_dead
    assert not got.suppress_email


@pytest.mark.parametrize(
    "host",
    [
        "vacationguide.com",
        "out-of-office.de",
        "autoreply.io",
        "urlaub-abwesend.de",
        "undelivered-parcels.com",
        "urlop-w-gorach.pl",
        "отпуск.рф",
    ],
)
def test_donor_host_in_our_subject_is_not_a_sign(host: str) -> None:
    """Стенд: тема ответа — наша «Guest article on {host}», и домен
    тревел-блога делал автоответом каждый ответ с него."""
    got = verdict("Sponsored posts cost $150.", subject=f"Re: Guest article on {host}")

    assert got.kind is ReplyKind.HUMAN, got.rule


@pytest.mark.parametrize(
    "subject", ["Re: Advertising rates", "Re: Guest article on vacationguide.com"]
)
def test_golden_replies_stay_human(subject: str) -> None:
    """Эталон разбора — сплошь живые ответы доноров: ни один не должен
    стать служебным письмом, иначе цену в нём модель не увидит."""
    cases = [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line]

    wrong = {
        case["id"]: verdict(case["text"], subject=subject).rule
        for case in cases
        if verdict(case["text"], subject=subject).kind is not ReplyKind.HUMAN
    }

    assert not wrong


# --- автоответы ---------------------------------------------------------------

ABSENCE_TEXTS = {
    "en": "Thank you for your email. I am currently out of the office with limited access to email.",
    "en отпуск": "Hello, I'm on vacation until 10/5 and will respond when I return.",
    "en самоназвание": "This is an automatic reply. Your message has been received.",
    "de": "Vielen Dank für Ihre Nachricht. Ich bin vom 28.09. bis 05.10. im Urlaub.",
    "de офис": "Ich bin bis zum 5.10. nicht im Büro. In dringenden Fällen: office@donor.test.",
    "fr": "Je suis absente du bureau jusqu’au lundi 5 octobre.",
    "es": "Estoy fuera de la oficina hasta el 5 de octubre.",
    "it": "Sono fuori ufficio fino al 5 ottobre.",
    "pt": "Estou de férias até 5 de outubro.",
    "nl": "Ik ben afwezig tot 5 oktober.",
    "pl": "Jestem na urlopie do 5 października.",
    "ru": "Я в отпуске до 5 октября.",
}


@pytest.mark.parametrize("text", ABSENCE_TEXTS.values(), ids=ABSENCE_TEXTS.keys())
def test_absence_in_text_is_an_auto_reply(text: str) -> None:
    """Без служебных заголовков — по фразе о себе со сроком."""
    got = verdict(text)

    assert got.kind is ReplyKind.AUTO_REPLY
    assert got.rule == "автоответчик по тексту"


@pytest.mark.parametrize(
    "prefix",
    [
        "Automatic reply",
        "Out of Office",
        "Abwesenheitsnotiz",
        "Réponse automatique ",
        "Respuesta automática",
        "Risposta automatica",
        "Resposta automática",
        "Automatisch antwoord",
        "Odpowiedź automatyczna",
        "Автоматический ответ",
    ],
)
def test_auto_reply_subject_in_each_language(prefix: str) -> None:
    """Outlook пишет в теме автоответа своё имя на языке почты."""
    got = verdict("Thanks.", subject=f"{prefix}: Guest article on donor.test")

    assert got.kind is ReplyKind.AUTO_REPLY
    assert got.rule == "автоответчик по теме"


def test_empty_return_path_alone_is_not_a_bounce() -> None:
    """С пустым обратным путём уходят и отпускные ответы; отпуск,
    принятый за отказ, хоронил бы живой адрес."""
    got = verdict("Ich bin bis 5.1.27 nicht im Büro.", headers={"Return-Path": "<>"})

    assert got.kind is ReplyKind.AUTO_REPLY


# --- сумма против фразы -------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "I'm out of the office until Monday, but the price is $100.",
        "Ich bin bis 5.10. nicht im Büro, der Preis ist 150 €.",
        "Please remove us from your list. Guest posts are $200 if you need.",
        "Отпишите нас. Статья — 5000 рублей.",
    ],
)
def test_sum_outweighs_a_phrase_without_machine_signs(text: str) -> None:
    """Фраза автоответа или отписки рядом с суммой в валюте, а служебных
    заголовков нет: цену назвал человек, и её разбирает модель."""
    got = verdict(text)

    assert got.kind is ReplyKind.HUMAN
    assert got.rule == "сумма в тексте важнее фразы"
    assert not got.guessed


def test_sum_outweighs_an_auto_reply_subject() -> None:
    got = verdict("Back on Monday. The price is 90 EUR.", subject="Out of Office: Guest article")

    assert got.kind is ReplyKind.HUMAN


def test_machine_signature_outweighs_a_sum() -> None:
    """Заголовки автомата — факт, а не слово: автоответ с ценой остаётся
    автоответом."""
    got = verdict("I'm on vacation until Monday. The price is $100.", headers=AUTO_SUBMITTED)

    assert got.kind is ReplyKind.AUTO_REPLY


def test_machine_sent_requirement_stays_a_requirement() -> None:
    got = verdict(
        "Please remove us from your mailing list. Posts were $100.", headers=AUTO_SUBMITTED
    )

    assert got.kind is ReplyKind.UNSUBSCRIBE


def test_plain_price_reply_is_still_a_guess() -> None:
    """Сумма перевешивает фразу, но сама по себе правилом не становится:
    доля «не сработало ни одно» остаётся честным числом."""
    got = verdict("We charge 250 EUR per post.")

    assert got.kind is ReplyKind.HUMAN
    assert got.guessed


# --- мёртвый ящик -------------------------------------------------------------

DEAD_MAILBOXES = {
    "en": "This mailbox is no longer monitored. Please write to editor@donor.test.",
    "en ушёл": "John has left the company. Please contact editor@donor.test.",
    "en не читается": "This inbox is not being monitored.",
    "de": "Diese E-Mail-Adresse wird nicht mehr gelesen. Bitte schreiben Sie an redaktion@donor.test.",
    "fr": "Cette adresse n'est plus utilisée.",
    "es": "Esta dirección ya no se utiliza.",
    "it": "Questo indirizzo non è più attivo.",
    "pt": "Este endereço não é mais utilizado.",
    "nl": "Dit adres is niet meer in gebruik.",
    "pl": "Ten adres nie jest już używany.",
    "ru": "Этот ящик больше не используется.",
}


@pytest.mark.parametrize("text", DEAD_MAILBOXES.values(), ids=DEAD_MAILBOXES.keys())
def test_dead_mailbox_is_a_bounce(text: str) -> None:
    """Стенд: автоответ «ящик больше не читается» оставлял добивки мёртвому
    ящику, а другие адреса донора не пробовались никогда."""
    got = verdict(text, headers=AUTO_SUBMITTED)

    assert got.kind is ReplyKind.BOUNCE
    assert got.rule == "мёртвый ящик"


def test_dead_mailbox_opens_the_next_address() -> None:
    got = outcome.decide(verdict(DEAD_MAILBOXES["en"], headers=AUTO_SUBMITTED).kind)

    assert got.mark_contact_dead
    assert got.stop_chain
    assert not got.suppress_email


@pytest.mark.parametrize(
    "signs",
    [
        {"sender": "noreply@donor.test"},
        {"subject": "Automatic reply: Guest article on donor.test"},
        {"headers": {"X-Auto-Response-Suppress": "All"}},
    ],
)
def test_dead_mailbox_by_other_automatic_signs(signs: dict[str, object]) -> None:
    got = verdict("This address is no longer in use.", **signs)

    assert got.rule == "мёртвый ящик"


def test_noreply_speaking_of_itself_does_not_bury_the_address() -> None:
    """Подтверждение заявки от noreply: «ящик не читается» — это о нём,
    а адрес, которому мы писали, жив."""
    got = verdict(
        "Your request #123 was received. This mailbox is not monitored, please do not reply.",
        sender="noreply@donor.test",
    )

    assert got.kind is not ReplyKind.BOUNCE


# --- письмо робота ------------------------------------------------------------


@pytest.mark.parametrize(
    "sender",
    ["accounts-noreply@service.example.test", "noreply@donor.test", "postmaster@donor.test"],
)
def test_service_notice_from_a_robot_is_not_a_human_answer(sender: str) -> None:
    """Проверка прода 10.10.2026: уведомление службы о настройках с адреса
    `…-noreply@` стояло на «Не привязаны» ответом человека."""
    got = verdict(
        "Hello, we're updating our settings to give you more control over saved history.",
        subject="New privacy settings for your account",
        sender=sender,
    )

    assert got.kind is ReplyKind.AUTO_REPLY
    assert got.rule == "письмо робота без суммы и файла"


def test_robot_naming_a_sum_is_still_a_reply_for_the_model() -> None:
    """Прайс от робота тикет-системы — цена донора: сумма перевешивает адрес, как фразу."""
    got = verdict("Request #77: a sponsored post is 150 GBP.", sender="sc-noreply@donor.test")

    assert got.kind is ReplyKind.HUMAN
    assert got.rule == "сумма в тексте важнее фразы"


def test_robot_sending_a_file_is_still_a_reply_for_the_model() -> None:
    """Прайс приходит файлом чаще, чем текстом: файл перевешивает адрес робота."""
    incoming = replace(
        reply("Our rates are attached.", sender="noreply@donor.test"),
        attachments=(Attachment(name="rates.pdf", size=9000, content_type="application/pdf"),),
    )

    assert classify.classify(incoming).kind is ReplyKind.HUMAN


# --- отказы доставки ----------------------------------------------------------


def test_real_delivery_report_is_a_bounce() -> None:
    got = verdict(
        "This is the mail system at host mx.donor.test.\n\n"
        "<editor@donor.test>: host mx.donor.test said: 550 5.1.1 <editor@donor.test>: "
        "Recipient address rejected: User unknown in virtual mailbox table",
        subject="Undelivered Mail Returned to Sender",
        sender="mailer-daemon@mx.donor.test",
        headers={
            "Content-Type": 'multipart/report; report-type="delivery-status"; boundary="b1"',
            "Auto-Submitted": "auto-replied",
        },
    )

    assert got.kind is ReplyKind.BOUNCE
    assert got.rule == "отказ доставки по заголовкам"


@pytest.mark.parametrize(
    ("sender", "headers", "text"),
    [
        ("mailer-daemon@mx.donor.test", {}, "550 5.1.1 user unknown"),
        ("postmaster@donor.test", {}, "Delivery has failed to these recipients or groups."),
        ("editor@donor.test", {"Return-Path": "<>"}, "Delivery to the following recipient failed."),
        ("MAILER-DAEMON@mx.donor.test", {}, "Unzustellbar: Empfänger unbekannt."),
    ],
)
def test_bounce_by_text_of_a_mail_system_letter(
    sender: str, headers: dict[str, str], text: str
) -> None:
    got = verdict(text, subject="Mail", sender=sender, headers=headers)

    assert got.kind is ReplyKind.BOUNCE
    assert got.rule == "отказ доставки по тексту письма почты"


@pytest.mark.parametrize(
    "prefix",
    [
        "Undeliverable",
        "Unzustellbar",
        "Non remis ",
        "No se puede entregar",
        "Non recapitabile",
        "Não é possível entregar",
        "Onbestelbaar",
        "Niedostarczalne",
        "Недоставленное сообщение",
    ],
)
def test_bounce_subject_in_each_language(prefix: str) -> None:
    got = verdict("x", subject=f"{prefix}: Guest article on donor.test")

    assert got.kind is ReplyKind.BOUNCE


def test_delay_notice_is_not_a_bounce() -> None:
    """«(Delay)» — письмо ещё доставляется, хоронить адрес рано."""
    got = verdict("Will retry for 3 days.", subject="Delivery Status Notification (Delay)")

    assert got.kind is not ReplyKind.BOUNCE


# --- отписки ------------------------------------------------------------------

UNSUBSCRIBES = {
    "en": "Please remove me from your mailing list.",
    "en слово": "Unsubscribe",
    "en стоп": "Stop emailing me.",
    "en снова": "Do not contact us again.",
    "en список": "Take me off your list please",
    "de": "Bitte melden Sie mich ab.",
    "de слово": "Abmelden",
    "de список": "Bitte entfernen Sie uns aus Ihrem Verteiler.",
    "de письма": "Wir möchten keine weiteren E-Mails erhalten.",
    "fr": "Merci de ne plus nous contacter.",
    "fr слово": "Désinscription svp",
    "es": "Por favor, denme de baja.",
    "es больше": "No nos escriba más.",
    "it": "Cancellami dalla lista.",
    "it больше": "Non scriveteci più.",
    "pt": "Quero me descadastrar.",
    "pt больше": "Não nos envie mais e-mails.",
    "nl": "Graag afmelden.",
    "nl больше": "Stuur ons niet meer.",
    "pl": "Proszę mnie wypisać z listy.",
    "pl больше": "Nie piszcie do nas więcej.",
    "ru": "Больше не пишите.",
    "ru нам": "Не пишите нам.",
    "ru рассылка": "Удалите нас из рассылки.",
    "ru отпишите": "Отпишите нас, пожалуйста.",
}


@pytest.mark.parametrize("text", UNSUBSCRIBES.values(), ids=UNSUBSCRIBES.keys())
def test_unsubscribe_in_each_language(text: str) -> None:
    got = verdict(text)

    assert got.kind is ReplyKind.UNSUBSCRIBE
    assert outcome.decide(got.kind).suppress_email


def test_footer_link_is_not_a_request() -> None:
    """Слово «unsubscribe» в строке со ссылкой — подвал рассылки отправителя."""
    got = verdict("What topic do you have in mind?\n\nTo unsubscribe click here: https://x.test/u")

    assert got.kind is ReplyKind.HUMAN


def test_list_letter_word_is_its_footer() -> None:
    """У письма с заголовками рассылки одно слово «Unsubscribe» — её подвал;
    просьба фразой при этом остаётся просьбой."""
    headers = {"List-Unsubscribe": "<mailto:u@x.test>"}

    assert verdict("What topic?\nUnsubscribe", headers=headers).kind is ReplyKind.HUMAN
    assert (
        verdict("Please remove me from your list.\nUnsubscribe", headers=headers).kind
        is ReplyKind.UNSUBSCRIBE
    )


# --- устройство правил --------------------------------------------------------


@pytest.mark.parametrize(
    "table",
    [
        phrases.ABSENCE,
        phrases.AUTO_SUBJECT,
        phrases.UNSUBSCRIBE,
        phrases.UNSUBSCRIBE_WORD,
        phrases.FOOTER_LINE,
        phrases.NO_LONGER,
        phrases.NOT_READ,
        phrases.BOUNCE_SUBJECT,
        phrases.BOUNCE_TEXT,
    ],
)
def test_every_dictionary_knows_every_language(table: dict[str, tuple[str, ...]]) -> None:
    """Язык, забытый в одном словаре, — рынок, где этот вид ответа
    не узнаётся вовсе."""
    assert set(table) == set(phrases.LANGUAGES)
    assert all(table[lang] for lang in phrases.LANGUAGES)


def test_longest_letter_is_classified_fast() -> None:
    """Вид решается внутри вебхука: письмо предельной длины одной строкой
    не должно стоить секунд, иначе платформа повторит вебхук по таймауту."""
    text = ("On 28.09.2026 at 10:04 price 100 USD, " * 6_000)[:199_000]

    started = time.monotonic()
    verdict(text)

    assert time.monotonic() - started < 2.0


def test_every_rule_names_itself_once() -> None:
    names = [name for name, _, _ in classify.RULES]

    assert all(names)
    assert len(set(names)) == len(names)
