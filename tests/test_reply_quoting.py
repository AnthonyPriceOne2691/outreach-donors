"""Цитата нашего письма на языках рынков: отрезается, а ответ донора — нет.

Наше письмо — боевой текст из шаблона, зоны без разметки: шесть вопросов,
«5–10 articles per month», список ниш. Нераспознанная цитата отдаёт всё
это модели и правилам как слова донора; распознанная слишком жадно —
отрезает цену, которую донор написал над ней. Проверяется и то, и другое.
"""

from __future__ import annotations

import time

import pytest
from backend.features.letters import template
from backend.features.replies.quoting import quote_starts_at, written_by_hand

LETTER = template.default().body.replace("{{sender_name}}", "Anna Ro")
ME = "Anna Ro <anna+m417.7d3a91c2e5@replies.ours.test>"

#: Слова нашего письма, которых в ответе донора быть не должно.
OURS = ("What's the price per article", "5–10 articles per month", "iGaming, CBD, forex")

#: Ответ донора на каждом языке. Первые слова нарочно совпадают с началом
#: шапки цитаты того же языка: «On…», «Am…», «Le…», «El…», «Il…», «Em…»,
#: «Op…», «W dniu…», «От…».
ANSWERS = {
    "en": "On our site a guest post is $120, dofollow.\n\nBest,\nMark",
    "de": "Am besten per PayPal: ein Gastartikel kostet 150 €.\n\nViele Grüße\nJan",
    "fr": "Le prix est de 100 € par article, lien dofollow.\n\nCordialement,\nLuc",
    "es": "El precio es de 80 USD por artículo.\n\nSaludos,\nAna",
    "it": "Il prezzo è 90 € per articolo.\n\nCordiali saluti,\nMarco",
    "pt": "Em relação ao preço: 70 € por artigo.\n\nAtenciosamente,\nJoão",
    "nl": "Op onze site kost een artikel 120 €.\n\nMet vriendelijke groet,\nPieter",
    "pl": "W dniu publikacji płatność 300 zł.\n\nPozdrawiam,\nPiotr",
    "ru": "От нас: статья стоит 5000 рублей.\n\nС уважением,\nИван",
}

#: Шапка Outlook на языке почты донора.
OUTLOOK = {
    "en": "From: {me}\nSent: Monday, September 28, 2026 10:04 AM\nTo: mark@donor.test\n"
    "Subject: Guest article on donor.test",
    "de": "Von: {me}\nGesendet: Montag, 28. September 2026 10:04\nAn: jan@donor.test\n"
    "Betreff: Guest article on donor.test",
    "fr": "De : {me}\nEnvoyé : lundi 28 septembre 2026 10:04\nÀ : luc@donor.test\n"
    "Objet : Guest article on donor.test",
    "es": "De: {me}\nEnviado el: lunes, 28 de septiembre de 2026 10:04\nPara: ana@donor.test\n"
    "Asunto: Guest article on donor.test",
    "it": "Da: {me}\nInviato: lunedì 28 settembre 2026 10:04\nA: marco@donor.test\n"
    "Oggetto: Guest article on donor.test",
    "pt": "De: {me}\nEnviado: segunda-feira, 28 de setembro de 2026 10:04\nPara: joao@donor.test\n"
    "Assunto: Guest article on donor.test",
    "nl": "Van: {me}\nVerzonden: maandag 28 september 2026 10:04\nAan: pieter@donor.test\n"
    "Onderwerp: Guest article on donor.test",
    "pl": "Od: {me}\nWysłano: poniedziałek, 28 września 2026 10:04\nDo: piotr@donor.test\n"
    "Temat: Guest article on donor.test",
    "ru": "От: {me}\nОтправлено: 28 сентября 2026 г. 10:04\nКому: ivan@donor.test\n"
    "Тема: Guest article on donor.test",
}

#: Строка «… написал:» у Gmail на языке почты донора.
WROTE = {
    "en": "On Mon, Sep 28, 2026 at 10:04 AM {me} wrote:",
    "de": "Am Mo., 28. Sept. 2026 um 10:04 Uhr schrieb {me}:",
    "fr": "Le lun. 28 sept. 2026 à 10:04, {me} a écrit :",
    "es": "El lun, 28 sept 2026 a las 10:04, Anna Ro (<anna+m417.7d3a91c2e5@replies.ours.test>) escribió:",
    "it": "Il giorno lun 28 set 2026 alle ore 10:04 {me} ha scritto:",
    "pt": "Em seg., 28 de set. de 2026 às 10:04, {me} escreveu:",
    "nl": "Op ma 28 sep 2026 om 10:04 schreef {me}:",
    "pl": "pon., 28 wrz 2026 o 10:04 {me} napisał(a):",
    "ru": "пн, 28 сент. 2026 г. в 10:04, {me}:",
}

#: Другие почтовые программы и их обычаи.
OTHER_CLIENTS = {
    "gmail en в две строки": "On Mon, Sep 28, 2026 at 10:04 AM {me}\nwrote:",
    "gmail ru в две строки": "пн, 28 сент. 2026 г. в 10:04, Anna Ro\n<anna+m417.7d3a91c2e5@replies.ours.test>:",
    # Проверка прода 10.10.2026: Gmail перенёс строку внутри угловых скобок, и вторая
    # строка шапки начиналась с «>» — цитата резалась с неё, первая оставалась в ответе.
    "gmail ru перенос перед «>»": "пн, 28 сент. 2026 г. в 10:04, Anna Ro <anna+m417.7d3a91c2e5@replies.ours.test\n>:",
    "gmail ru перенос после «<»": "пн, 28 сент. 2026 г. в 10:04, Anna Ro <\nanna+m417.7d3a91c2e5@replies.ours.test>:",
    "es в две строки": "El lun, 28 sept 2026 a las 10:04, \nAnna Ro escribió:",
    "thunderbird de": "Am 28.09.26 um 10:04 schrieb Anna Ro:",
    "thunderbird fr": "Le 28/09/2026 à 10:04, Anna Ro a écrit :",
    "thunderbird pl": "W dniu 28.09.2026 o 10:04, Anna Ro pisze:",
    "thunderbird ru": "28.09.2026 10:04, Anna Ro пишет:",
    "apple ru": "28 сент. 2026 г., в 10:04, {me} написал(а):",
    "apple nl": "Op 28 sep. 2026 om 10:04 heeft {me} het volgende geschreven:",
    "yahoo de": "Am Montag, 28. September 2026, 10:04:00 MESZ hat {me} Folgendes geschrieben:",
    "yahoo pl": "W poniedziałek, 28 września 2026, 10:04:00 CEST, {me} napisał(-a):",
    "яндекс": '28.09.2026, 10:04, "Anna Ro" <anna+m417.7d3a91c2e5@replies.ours.test>:',
    "mail.ru": "Понедельник, 28 сентября 2026, 10:04 +03:00 от {me}:",
    "outlook жирным": "*From:* {me}\n*Sent:* Monday, September 28, 2026 10:04 AM\n*To:* mark@donor.test",
    "outlook мобильный": "________________________________\nFrom: {me}\nSent: Monday, September 28, 2026",
    "apple пересланное en": "Begin forwarded message:\n\nFrom: {me}",
    "apple пересланное de": "Anfang der weitergeleiteten Nachricht:\n\nVon: {me}",
    "apple пересланное fr": "Début du message réexpédié :\n\nDe : {me}",
    "outlook de старый": "-----Ursprüngliche Nachricht-----\nVon: {me}",
    "gmail es пересланное": "---------- Mensaje reenviado ---------\nDe: {me}",
}


def quoted(answer: str, header: str) -> str:
    return f"{answer}\n\n{header.format(me=ME)}\n\n{LETTER}\n"


def assert_only_the_answer(answer: str, header: str) -> None:
    got = written_by_hand(quoted(answer, header))

    assert got == answer.strip()
    assert not any(phrase in got for phrase in OURS)


@pytest.mark.parametrize("lang", ANSWERS)
def test_outlook_block_in_each_language(lang: str) -> None:
    """Стенд 28.09: блок «Von:/Gesendet:/An:/Betreff:» не узнавался, и наше
    письмо целиком уходило в разбор как слова донора."""
    assert_only_the_answer(ANSWERS[lang], OUTLOOK[lang])


@pytest.mark.parametrize("lang", ANSWERS)
def test_wrote_line_in_each_language(lang: str) -> None:
    assert_only_the_answer(ANSWERS[lang], WROTE[lang])


@pytest.mark.parametrize("header", OTHER_CLIENTS.values(), ids=OTHER_CLIENTS.keys())
def test_other_clients(header: str) -> None:
    assert_only_the_answer(ANSWERS["en"], header)


@pytest.mark.parametrize(
    "text",
    [
        "De: nuestro lado, el precio es 80 USD.\nGracias",
        "Our prices:\n- guest post $100\n- link insertion $50",
        "Contact our editor <editor@donor.test>:\nshe handles it",
        "El precio es 80 USD por artículo.\nEl lunes publicamos.",
        "Od 100 zł za artykuł.\nDo tego link dofollow.",
        "28.09 я написал редактору, цена 100$\n\nспасибо\nИван",
    ],
)
def test_ordinary_text_is_not_cut(text: str) -> None:
    """Одна строка «De: …» — ещё не шапка Outlook, фраза с датой и глаголом
    «написал» без двоеточия в конце — ещё не шапка цитаты."""
    assert written_by_hand(text) == text.strip()


def test_answer_line_starting_like_a_header_keeps_its_price() -> None:
    """«On our site…» начинается так же, как «On Mon, … wrote:». Раньше
    шапка узнавалась от первой строки на «On» до «wrote:» через весь
    ответ, и строка с ценой уезжала вместе с цитатой."""
    text = f"Hi Anna,\n\nOn our site a guest post is $120.\n\nOn Mon, 28 Sep 2026 at 10:04, {ME} wrote:\n{LETTER}"

    assert written_by_hand(text) == "Hi Anna,\n\nOn our site a guest post is $120."


def test_dated_answer_line_right_above_the_header_is_kept() -> None:
    """Строка ответа с датой прямо над шапкой — не первая строка шапки."""
    text = f"Hi Anna,\nPrice $100, live on 05.10.2026\nOn Mon, Sep 28, 2026 at 10:04 AM {ME} wrote:\n{LETTER}"

    assert written_by_hand(text) == "Hi Anna,\nPrice $100, live on 05.10.2026"


def test_windows_line_endings() -> None:
    text = quoted(ANSWERS["de"], OUTLOOK["de"]).replace("\n", "\r\n")

    assert written_by_hand(text) == ANSWERS["de"].replace("\n", "\r\n")


def test_reply_under_the_quote_is_not_lost() -> None:
    """Ответ снизу — законный обычай: письмо, из которого вырезали всё,
    хуже письма с лишней цитатой."""
    text = f"{WROTE['fr'].format(me=ME)}\n{LETTER}\n\nLe prix est de 100 €."

    assert "100 €" in written_by_hand(text)


@pytest.mark.parametrize(
    "unit",
    ["1 2 3 4 5 6 7 8 9 0 ", "On 28.09.2026 at 10:04 price 100 USD, ", "10:04 Anna <a@b.test> "],
)
def test_one_huge_line_is_cut_in_linear_time(unit: str) -> None:
    """Текст из HTML бывает одной строкой в двести тысяч знаков. Две
    неограниченные «сколько угодно символов» подряд разбирали такую строку
    минуту (замер 28.09: 16 и 67 секунд) — внутри вебхука, который
    платформа повторит по таймауту. Граница с запасом в десятки раз."""
    text = (unit * (200_000 // len(unit)))[:199_000]

    started = time.monotonic()
    written_by_hand(text)

    assert time.monotonic() - started < 2.0


def test_the_reason_names_the_header() -> None:
    """Признак идёт в отчёт: если цитаты перестанут узнаваться, это видно
    по числу, а не по жалобе."""
    _, reason = quote_starts_at(quoted(ANSWERS["nl"], OUTLOOK["nl"]))

    assert reason == "блок Van:"
