"""Правила приёма ответов без базы и без сети.

Четыре вещи здесь стоят дорого, если ошибиться, и проверяются придирчиво:
цитата нашего собственного письма (в ней слово «unsubscribe»), автоответчик,
принятый за ответ, цена, которой в письме нет, и указание для разборщика,
спрятанное в тексте донора.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from backend.features.core.domain import ReplyKind
from backend.features.letters import reply_to
from backend.features.replies import binding, classify, outcome
from backend.features.replies.extract import (
    LABEL_UNSAID,
    Extracted,
    parse_form,
    temper,
)
from backend.features.replies.inbound import Attachment, Incoming, addresses_in
from backend.features.replies.mime import from_form, message_ids_in, parse_headers
from backend.features.replies.money import amounts_in, appears_in, as_price, normalize_currency
from backend.features.replies.quoting import written_by_hand

SECRET = "s" * 32

#: Наше письмо, каким оно уезжает адресату. В юридическом блоке стоит
#: слово «unsubscribe», и оно вернётся к нам в цитате каждого ответа.
OUR_LETTER = """Good afternoon,

I have been reading through repair-guide.example.test over the past few days.

We pay per published article and send the draft over for your approval.

Best regards,
Anna Ro

12 Baggot Street, Dublin, Ireland
You are receiving this because your site is listed as accepting paid content.
To stop hearing from us, unsubscribe here: https://ours.test/stop"""


def incoming(text: str, *, subject: str = "Re: Advertising rates", **extra: object) -> Incoming:
    values: dict[str, object] = {
        "message_id": "<reply-1@site.test>",
        "to": ("anna+m417.7d3a91c2e5@replies.ours.test",),
        "from_email": "editor@site.test",
        "subject": subject,
        "text": text,
    }
    values.update(extra)
    return Incoming(**values)  # type: ignore[arg-type]


class TestQuoting:
    def test_quote_after_on_wrote_is_cut(self) -> None:
        text = (
            f"Hi Anna,\n\nPlacement is 250 EUR.\n\nOn Mon, Anna Ro <a@b.test> wrote:\n{OUR_LETTER}"
        )

        assert written_by_hand(text) == "Hi Anna,\n\nPlacement is 250 EUR."

    def test_quote_by_angle_bracket_is_cut(self) -> None:
        text = "Sure, 300 USD.\n\n> " + OUR_LETTER.replace("\n", "\n> ")

        assert written_by_hand(text) == "Sure, 300 USD."

    def test_signature_is_cut(self) -> None:
        text = "Placement is 250 EUR.\n\n-- \nElena, editor\nsite.test"

        assert written_by_hand(text) == "Placement is 250 EUR."

    def test_reply_under_the_quote_is_not_lost(self) -> None:
        """Ответ снизу — законный обычай. Письмо, из которого мы вырезали
        всё, хуже письма с лишней цитатой."""
        text = f"On Mon, Anna wrote:\n{OUR_LETTER}\n\nWe charge 250 EUR."

        assert "250 EUR" in written_by_hand(text)


class TestClassifying:
    def test_quoted_unsubscribe_is_not_an_unsubscribe(self) -> None:
        """Самая дорогая ловушка приёма: в цитате наше собственное письмо
        со словом «unsubscribe», и поиск по всему тексту отправил бы
        в стоп-лист каждого ответившего донора."""
        text = f"Hi Anna,\n\n250 EUR.\n\nOn Mon, Anna wrote:\n{OUR_LETTER}"

        assert classify.classify(incoming(text)).kind is ReplyKind.HUMAN

    def test_real_unsubscribe_is_caught(self) -> None:
        verdict = classify.classify(incoming("Please unsubscribe me, we do not sell posts."))

        assert verdict.kind is ReplyKind.UNSUBSCRIBE
        assert verdict.rule is not None

    def test_out_of_office_is_not_a_reply(self) -> None:
        """«Я в отпуске до понедельника» не значит «мне неинтересно»:
        оборвав на нём цепочку, мы потеряем донора ни на чём."""
        verdict = classify.classify(incoming("I am out of the office until Monday."))

        assert verdict.kind is ReplyKind.AUTO_REPLY
        assert not classify.stops_chain(verdict.kind)

    def test_auto_reply_by_header(self) -> None:
        verdict = classify.classify(
            incoming("Спасибо за письмо.", headers={"Auto-Submitted": "auto-replied"})
        )

        assert verdict.kind is ReplyKind.AUTO_REPLY

    def test_bounce_by_empty_return_path(self) -> None:
        verdict = classify.classify(
            incoming("Delivery to the following recipient failed.", headers={"Return-Path": "<>"})
        )

        assert verdict.kind is ReplyKind.BOUNCE

    def test_bounce_by_smtp_code(self) -> None:
        verdict = classify.classify(incoming("550 5.1.1 mailbox unavailable"))

        assert verdict.kind is ReplyKind.BOUNCE

    def test_bounce_wins_over_auto_reply(self) -> None:
        """Уведомление почты несёт и признаки автоответчика: служебные
        заголовки у них общие. Порядок правил и есть договорённость."""
        verdict = classify.classify(
            incoming(
                "user unknown",
                subject="Undelivered Mail Returned to Sender",
                headers={"Auto-Submitted": "auto-replied"},
            )
        )

        assert verdict.kind is ReplyKind.BOUNCE

    def test_plain_reply_is_a_guess_and_says_so(self) -> None:
        """Доля «решено по умолчанию» — отдельное число: у ступени,
        которая ничего не отсеивает, поломка выглядит как работа."""
        verdict = classify.classify(incoming("We charge 250 EUR per post."))

        assert verdict.kind is ReplyKind.HUMAN
        assert verdict.guessed

    def test_auto_reply_does_not_stop_the_chain(self) -> None:
        assert not classify.stops_chain(ReplyKind.AUTO_REPLY)
        assert classify.stops_chain(ReplyKind.HUMAN)
        assert classify.stops_chain(ReplyKind.BOUNCE)
        assert classify.stops_chain(ReplyKind.UNSUBSCRIBE)


class TestBinding:
    def test_label_binds_a_reply_from_another_address(self) -> None:
        """Ответ приходит не с того адреса, которому писали: на общий ящик
        смотрит секретарь. Метка едет в поле «кому» и доезжает всегда."""
        address = reply_to.address_for(
            417, sender_email="anna@mail.test", reply_domain="replies.ours.test", secret=SECRET
        )

        bound = binding.bind(incoming("250 EUR", to=(address,)), secret=SECRET)

        assert bound.message_id == 417
        assert bound.way is binding.BindingWay.LABEL

    def test_headers_are_the_fallback(self) -> None:
        bound = binding.bind(
            incoming("250 EUR", to=("info@ours.test",), in_reply_to="<ours-9@mail.test>"),
            by_provider_id=[("<ours-9@mail.test>", 9)],
            secret=SECRET,
        )

        assert bound.message_id == 9
        assert bound.way is binding.BindingWay.HEADERS

    def test_label_wins_over_headers(self) -> None:
        """Метка надёжнее: заголовки теряются пересылками регулярно."""
        address = reply_to.address_for(
            417, sender_email="anna@mail.test", reply_domain="replies.ours.test", secret=SECRET
        )

        bound = binding.bind(
            incoming("250 EUR", to=(address,), in_reply_to="<ours-9@mail.test>"),
            by_provider_id=[("<ours-9@mail.test>", 9)],
            secret=SECRET,
        )

        assert bound.message_id == 417

    def test_forged_label_does_not_bind(self) -> None:
        bound = binding.bind(
            incoming("250 EUR", to=("anna+m418.0000000000@replies.ours.test",)), secret=SECRET
        )

        assert not bound.bound

    def test_unbound_is_a_state_not_a_failure(self) -> None:
        bound = binding.bind(incoming("Hello", to=("info@ours.test",)), secret=SECRET)

        assert bound.way is binding.BindingWay.NONE
        assert bound.message_id is None

    def test_in_reply_to_comes_before_references(self) -> None:
        found = binding.thread_ids(
            incoming("x", in_reply_to="<last@x>", references=("<first@x>", "<middle@x>"))
        )

        assert found[0] == "<last@x>"


class TestExtractionGuards:
    def test_price_absent_from_the_letter_is_a_fabrication(self) -> None:
        """Главная проверка поверх самооценки модели: названное число
        обязано встречаться в письме."""
        text = "Placement is 250 EUR."
        found = Extracted(price_white=Decimal("999"), currency="EUR", confidence=0.95)

        tempered = temper(found, text=text)

        assert tempered.confidence == 0.0
        assert tempered.notes

    def test_price_without_currency_is_not_a_price(self) -> None:
        tempered = temper(
            Extracted(price_white=Decimal("250"), confidence=0.9), text="Placement is 250."
        )

        assert tempered.confidence < 0.8

    def test_honest_price_keeps_its_confidence(self) -> None:
        tempered = temper(
            Extracted(price_white=Decimal("250"), currency="EUR", confidence=0.9),
            text="Placement is 250 EUR.",
        )

        assert tempered.confidence == 0.9

    def test_separators_do_not_hide_the_number(self) -> None:
        assert appears_in(Decimal("1200"), "Placement is 1,200 EUR")
        assert appears_in(Decimal("1200"), "Placement is 1 200 EUR")
        assert not appears_in(Decimal("1250"), "Placement is 1,200 EUR")

    def test_missing_confidence_is_not_certainty(self) -> None:
        """Модель не поставила себе оценку — это «неизвестно», а не
        «уверена»."""
        found = parse_form('{"price_white": 250, "currency": "EUR"}')

        assert found is not None
        assert found.confidence == 0.0

    def test_zero_price_is_not_a_price(self) -> None:
        """Ноль означал бы «размещают бесплатно»."""
        assert as_price(0) is None
        assert as_price("-5") is None
        assert as_price("не знаю") is None

    def test_currency_comes_to_one_code(self) -> None:
        assert normalize_currency("€") == "EUR"
        assert normalize_currency("euros") == "EUR"
        assert normalize_currency(" usd ") == "USD"

    def test_unknown_currency_is_kept_not_dropped(self) -> None:
        """Выбросить незнакомую валюту значит потерять цену вместе с ней."""
        assert normalize_currency("CZK") == "CZK"

    def test_broken_answer_is_not_a_price(self) -> None:
        assert parse_form("не json") is None
        assert parse_form("[1, 2]") is None

    def test_amounts_are_numbers_next_to_a_currency(self) -> None:
        """Сеть, срок и число ссылок — не цены."""
        text = "Guest post 80 USDT (TRC20), live in 3 days, 2 links. Homepage — €1.200."

        assert amounts_in(text) == {Decimal("80"), Decimal("1200")}
        assert amounts_in("Размещение — 1500 рублей, 30 дней") == {Decimal("1500")}
        assert amounts_in("We have 5 tons of ethical content") == set()

    def test_several_prices_and_not_the_smallest_goes_to_a_human(self) -> None:
        """«Главная 1.200 €, блог 350 €»: модель брала 1200 с уверенностью
        0,90 и сама писала «неясно» (эталон 23.09)."""
        text = "Ein Artikel auf der Startseite kostet 1.200 €, im Blog 350 €."
        chosen = Extracted(price_white=Decimal("1200"), currency="EUR", confidence=0.9)

        tempered = temper(chosen, text=text)

        assert tempered.confidence < 0.8
        assert "не наименьшая" in tempered.notes[-1]

    def test_smallest_of_several_prices_keeps_its_confidence(self) -> None:
        text = "Ein Artikel auf der Startseite kostet 1.200 €, im Blog 350 €."
        chosen = Extracted(price_white=Decimal("350"), currency="EUR", confidence=0.9)

        assert temper(chosen, text=text).confidence == 0.9

    def test_white_and_grey_pair_is_not_a_choice(self) -> None:
        """Две цены за один пост с пометкой и без — ответ, а не выбор."""
        text = "$120 with a sponsored label, $180 without any label."
        pair = Extracted(
            price_white=Decimal("120"), price_grey=Decimal("180"), currency="USD", confidence=0.9
        )

        assert temper(pair, text=text).confidence == 0.9

    def test_silence_about_the_label_is_a_note_not_a_doubt(self) -> None:
        """Самый частый живой ответ — цена без слова о пометке. Он ложится
        в базу, а молчание остаётся заметкой и полем снимка."""
        found = parse_form(
            '{"price_white": 90, "currency": "EUR", "label_stated": false, "confidence": 0.95}'
        )

        assert found is not None
        assert found.confidence == 0.95
        assert LABEL_UNSAID in found.notes
        assert found.snapshot()["label_stated"] is False

    def test_stated_label_leaves_no_note(self) -> None:
        found = parse_form(
            '{"price_white": 90, "currency": "EUR", "label_stated": true, "confidence": 0.95}'
        )

        assert found is not None
        assert LABEL_UNSAID not in found.notes


class TestConsequences:
    def test_reply_stops_the_chain(self) -> None:
        got = outcome.decide(ReplyKind.HUMAN, Extracted(confidence=0.9))

        assert got.stop_chain
        assert got.remember_answering_address

    def test_auto_reply_changes_nothing(self) -> None:
        got = outcome.decide(ReplyKind.AUTO_REPLY)

        assert not got.stop_chain
        assert not got.suppress_email
        assert not got.needs_review

    def test_unsubscribe_blocks_the_address(self) -> None:
        got = outcome.decide(ReplyKind.UNSUBSCRIBE)

        assert got.suppress_email
        assert got.stop_chain

    def test_bounce_marks_the_contact(self) -> None:
        got = outcome.decide(ReplyKind.BOUNCE)

        assert got.mark_contact_dead
        assert not got.remember_answering_address

    def test_confident_price_goes_to_the_base(self) -> None:
        found = Extracted(price_white=Decimal("250"), currency="EUR", confidence=0.9)

        got = outcome.decide(ReplyKind.HUMAN, found, threshold=0.8)

        assert got.store_price
        assert not got.needs_review

    def test_unsure_price_waits_for_a_person(self) -> None:
        """Ниже порога — в ручную очередь, а не в базу. Без этой
        ветки приёмка в 5% ошибок не держится."""
        found = Extracted(price_white=Decimal("250"), currency="EUR", confidence=0.4)

        got = outcome.decide(ReplyKind.HUMAN, found, threshold=0.8)

        assert not got.store_price
        assert got.needs_review
        assert got.review_reason is not None
        assert "40%" in got.review_reason

    def test_reply_without_a_price_also_waits(self) -> None:
        """«Ответили, цена не распознана» — прямой фильтр в WEB_LAYER:
        цена могла быть во вложении или сказана непонятно."""
        got = outcome.decide(ReplyKind.HUMAN, Extracted(confidence=0.99))

        assert got.needs_review
        assert got.review_reason == "цена в ответе не распознана"

    def test_only_human_replies_wait_for_review(self) -> None:
        assert outcome.waiting_for_review(ReplyKind.HUMAN, 0.1, reviewed=False)
        assert not outcome.waiting_for_review(ReplyKind.HUMAN, 0.1, reviewed=True)
        assert not outcome.waiting_for_review(ReplyKind.AUTO_REPLY, 0.0, reviewed=False)
        assert not outcome.waiting_for_review(ReplyKind.BOUNCE, 0.0, reviewed=False)


class TestPlatformPayload:
    HEADERS = (
        "Message-ID: <CA+abc@mail.test>\n"
        "In-Reply-To: <m417.7d3a@replies.ours.test>\n"
        "References: <first@ours.test>\n"
        " <m417.7d3a@replies.ours.test>\n"
        "Return-Path: <editor@site.test>\n"
        "X-Whatever: ignored"
    )

    def test_folded_header_is_glued_back(self) -> None:
        """`References` переносится почти всегда, и разорванный пополам
        он не совпадёт ни с чем."""
        headers = parse_headers(self.HEADERS)

        assert message_ids_in(headers["references"]) == (
            "<first@ours.test>",
            "<m417.7d3a@replies.ours.test>",
        )

    def test_unused_headers_are_not_kept(self) -> None:
        assert "x-whatever" not in parse_headers(self.HEADERS)

    def test_encoded_subject_is_readable(self) -> None:
        got = from_form({"from": "a@b.test", "subject": "=?utf-8?B?0KbQtdC90LA=?="})

        assert got.subject == "Цена"

    def test_address_is_taken_out_of_the_name(self) -> None:
        got = from_form({"from": "Elena Petrova <elena@site.test>", "to": "anna@ours.test"})

        assert got.from_email == "elena@site.test"

    def test_attachments_are_listed(self) -> None:
        got = from_form(
            {
                "from": "a@b.test",
                "attachment-info": '{"a1": {"filename": "price.pdf", "type": "application/pdf", "size": 900}}',
            }
        )

        assert got.has_price_file
        assert got.attachments[0].name == "price.pdf"

    def test_dangerous_attachment_is_not_accepted(self) -> None:
        """Исполняемые файлы в прайсе не нужны никому."""
        assert Attachment(name="prices.exe", size=10).dangerous
        assert not Attachment(name="prices.pdf", size=10).dangerous

    def test_broken_attachment_info_is_no_attachments(self) -> None:
        """Письмо важнее списка его файлов."""
        got = from_form({"from": "a@b.test", "attachment-info": "не json"})

        assert got.attachments == ()

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("a@b.test", ("a@b.test",)),
            ("A@B.TEST", ("a@b.test",)),
            ("One <a@b.test>, Two <c@d.test>", ("a@b.test", "c@d.test")),
            ("", ()),
        ],
    )
    def test_addresses_are_found_in_a_header(self, value: str, expected: tuple[str, ...]) -> None:
        assert addresses_in(value) == expected

    def test_long_letter_is_cut_before_parsing(self) -> None:
        got = from_form({"from": "a@b.test", "text": "x" * 500_000})

        assert len(got.text) <= 200_000
        assert len(got.for_model) <= 20_000


# --- продаёт ли донор размещение --------------------------------------------


def test_placement_is_read_from_the_form() -> None:
    found = parse_form(
        '{"price_white": null, "placement": "declines", '
        '"placement_quote": "we do not sell links", "confidence": 0.9}'
    )
    assert found is not None
    assert found.placement == "declines"
    assert found.declines


def test_unknown_placement_is_unclear() -> None:
    found = parse_form('{"placement": "maybe", "confidence": 0.9}')
    assert found is not None
    assert found.placement == "unclear"


def test_decline_without_a_verbatim_quote_is_not_trusted() -> None:
    """Отказ без дословной опоры — догадка, а по нему домен уходит на год."""
    found = Extracted(placement="declines", placement_quote="no paid posts", confidence=0.9)
    tempered = temper(found, text="Hi, we will get back to you next week.")
    assert tempered.confidence == 0.0


def test_decline_with_a_price_is_a_contradiction() -> None:
    found = Extracted(
        price_white=Decimal("100"), currency="USD", placement="declines",
        placement_quote="we don't sell", confidence=0.9,
    )  # fmt: skip
    tempered = temper(found, text="We don't sell links, but a post is 100 USD.")
    assert tempered.confidence <= 0.3
    assert not tempered.declines, "цена в ответе — это уже не отказ"


def test_free_guest_post_is_consent_not_refusal() -> None:
    """«Платных не берём, гостевой — бесплатно» — согласие. 23.09 модель
    прочла его как отказ, и домен ушёл бы из отбора на год."""
    found = Extracted(placement="free", placement_quote="x", confidence=0.93)
    consequences = outcome.decide(ReplyKind.HUMAN, found, threshold=0.8)
    assert consequences.store_free
    assert not consequences.store_declines
    assert not consequences.needs_review


def test_confident_decline_needs_no_review() -> None:
    found = Extracted(placement="declines", placement_quote="x", confidence=0.95)
    consequences = outcome.decide(ReplyKind.HUMAN, found, threshold=0.8)
    assert consequences.store_declines
    assert not consequences.needs_review
    assert consequences.stop_chain


# --- евро, крипта и числа целиком -------------------------------------------


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("120,50", "120.50"), ("1.200", "1200"), ("1,200", "1200"), ("1.200,50", "1200.50"),
        ("1,200.50", "1200.50"), ("0.005", "0.005"), ("0,05", "0.05"), ("12 000", "12000"),
        ("1 200,00", "1200.00"), (0.05, "0.05"),
    ],
)  # fmt: skip
def test_european_and_english_numbers(raw: object, want: str) -> None:
    """Раньше запятая выбрасывалась: «120,50 €» становилось 12050, «1.200 €» — 1,2."""
    assert as_price(raw) == Decimal(want)


@pytest.mark.parametrize(
    ("value", "text", "want"),
    [
        ("120.5", "Preis 120,50 €", True),
        ("120", "Preis 120,50 €", False),
        ("1200", "Preis 1.200 €", True),
        ("0.05", "rate 0.05 BTC", True),
        ("0.05", "we have 10 pages", False),
        ("1250", "price 1200", False),
    ],
)
def test_number_must_appear_whole(value: str, text: str, want: bool) -> None:
    """Подстрокой «120» находилось внутри «120,50», а у «0,05» бралось «0» —
    и неверная цена ложилась в базу сама (эталонный прогон 23.09)."""
    assert appears_in(Decimal(value), text) is want


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("USDT", "USDT"), ("USDT TRC20", "USDT"), ("Tether (TRC-20)", "USDT"), ("₮", "USDT"),
        ("₿", "BTC"), ("bitcoin", "BTC"), ("ETH", "ETH"), ("USDC", "USDC"), ("TON", "TON"),
        ("рублей", "RUB"), ("долларов", "USD"), ("Euro", "EUR"), ("€", "EUR"), ("usd", "USD"),
    ],
)  # fmt: skip
def test_currency_words_not_substrings(raw: str, code: str) -> None:
    """«usd» внутри «usdt» делал из USDT доллар: для гест-постинга это другой
    способ оплаты, и потерять его значит не знать, чем платить."""
    assert normalize_currency(raw) == code


def test_golden_set_is_well_formed() -> None:
    """Эталон — ворота для правки промпта; испорченный файл молча их открыл бы."""
    path = Path(__file__).parent.parent / "scripts" / "data" / "reply_parse_golden.jsonl"
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    assert len(cases) >= 40
    assert len({c["id"] for c in cases}) == len(cases), "id повторяются"
    for case in cases:
        assert case["expect"]["placement"] in {"sells", "free", "declines", "unclear"}
        assert set(case["expect"]) == {"placement", "price_white", "price_grey", "currency"}
