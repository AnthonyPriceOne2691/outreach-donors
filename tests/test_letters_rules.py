"""Правила писем без базы и без сети.

Три вещи здесь стоят дорого, если ошибиться, и проверяются придирчиво:
шаблон без условий или подписи (письмо не то, что утвердили), метрики Ahrefs
в письме (правила Ahrefs, платит ключ) и нулевой транспорт, дотянувшийся
до боевого адреса (письмо, которое выглядит отправленным и не ушло).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from backend.features.core.domain import SenderStatus, Stage
from backend.features.core.models.outreach import SenderModel
from backend.features.letters import compose, guards, masking, reply_to, uniqueness
from backend.features.letters.rewrite import (
    CHANGE_MAX,
    CHANGE_MIN,
    Personalization,
    RewriteResult,
    _accept,
    build_payload,
    change_share,
    parse_zones,
)
from backend.features.letters.template import (
    ADVERTISER,
    ADVERTISER_PATH,
    REQUIRED_ZONES,
    REWRITE_YIELD,
    Template,
    TemplateError,
    ZoneKind,
    advertiser,
    default,
    parse,
)
from backend.features.letters.transport import (
    NullTransport,
    Outgoing,
    TransportError,
)
from backend.features.letters.transport_factory import build_transport
from backend.features.outreach.senders import pick

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)

#: Наименьший шаблон, проходящий все проверки. Собирается из требуемых
#: зон, чтобы тест не разъехался с ТЗ при добавлении зоны.
_ZONE_TEXT = {
    "greeting": "Hello there,",
    "opening": (
        "I have been reading {{host}} for a while and it looks like a good match "
        "for the kind of material my clients publish every month."
    ),
    "ask": (
        "Could you tell me what a placement costs, and whether the number changes "
        "when the article carries a sponsored label on it?"
    ),
    "offer": "I work with an agency that places articles for clients.",
    "terms": "We pay per published article.",
    "signature": "Best regards,\n{{sender_name}}",
}


def _template_text(**overrides: str) -> str:
    zones = dict(_ZONE_TEXT)
    zones.update(overrides)
    lines = ["subject: Rates for {{host}}", ""]
    for name, kind in REQUIRED_ZONES.items():
        if name not in zones:
            continue
        lines += [f"[{name}] {kind.value}", zones[name], ""]
    return "\n".join(lines)


def _sender(sender_id: int, **extra: object) -> SenderModel:
    values: dict[str, object] = {
        "id": sender_id,
        "domain": "mail.example.test",
        "email": f"outreach{sender_id}@mail.example.test",
        "stage": Stage.DONORS,
        "daily_cap": 20,
        "status": SenderStatus.FREE,
        "enabled": True,
        "warmup_started_at": None,
    }
    values.update(extra)
    return SenderModel(**values)


class TestTemplate:
    def test_default_template_parses(self) -> None:
        """Шаблон, лежащий в репозитории, обязан разбираться: он уходит
        в каждое письмо, и сломанный виден только у адресата."""
        template = default()

        assert {z.name for z in template.zones} == set(REQUIRED_ZONES)
        assert template.subject

    def test_missing_terms_zone_is_refused(self) -> None:
        """Шаблон без условий собирается так же легко, как правильный,
        и разница видна только у адресата."""
        without_terms = _template_text().replace(
            "[terms] fixed\nWe pay per published article.\n", ""
        )

        with pytest.raises(TemplateError, match="terms"):
            parse(without_terms)

    def test_legal_zone_is_now_an_extra_zone(self) -> None:
        """Юридический блок снят 23.09.2026. Шаблон, где он остался, —
        старый, и молча принять его значило бы слать адрес и отписку,
        которых больше нет в настройках, громкими метками."""
        with_legal = _template_text() + "[legal] fixed\n{{postal_address}}\n"

        with pytest.raises(TemplateError, match="лишние зоны: legal"):
            parse(with_legal)

    def test_signature_without_sender_name_is_refused(self) -> None:
        with pytest.raises(TemplateError, match="sender_name"):
            parse(_template_text(signature="Best regards,\nThe team"))

    def test_fixed_zone_declared_as_rewritable_is_refused(self) -> None:
        """Условия сделки, объявленные переписываемыми, ушли бы в модель."""
        text = _template_text().replace("[terms] fixed", "[terms] rewrite")

        with pytest.raises(TemplateError, match="terms"):
            parse(text)

    def test_text_outside_zones_is_refused(self) -> None:
        """Молча пропущенная строка шаблона — это письмо без абзаца."""
        text = _template_text().replace("subject: Rates for {{host}}", "Hello!\nsubject: Rates")

        with pytest.raises(TemplateError, match="до первой зоны"):
            parse(text)

    def test_short_rewritable_part_is_refused(self) -> None:
        """Если переписывать почти нечего, нижний край коридора недостижим,
        и каждое письмо уходило бы с пометкой «слишком похоже»."""
        with pytest.raises(TemplateError, match="недостижим"):
            parse(_template_text(greeting="Hi,", opening="Nice site.", ask="Price?"))

    def test_unknown_zone_kind_names_what_exists(self) -> None:
        text = _template_text().replace("[greeting] rewrite", "[greeting] maybe")

        with pytest.raises(TemplateError, match="rewrite"):
            parse(text)


class TestCompose:
    def test_unset_sender_name_shouts(self) -> None:
        """Пустое имя в подписи выглядело бы готовым письмом."""
        letter = compose.assemble(
            compose.render(parse(_template_text()), compose.values_for(host="site.test")), {}
        )

        assert compose.unset_in(letter.body)

    def test_filled_values_leave_nothing_unset(self) -> None:
        values = {
            "host": "site.test",
            "sender_name": "Anna Ro",
            "postal_address": "1 Main St, Dublin",
            "unsubscribe_url": "https://ours.test/stop",
        }

        letter = compose.assemble(compose.render(parse(_template_text()), values), {})

        assert compose.unset_in(letter.body) == []
        assert "Anna Ro" in letter.body
        assert "site.test" in letter.subject

    def test_unknown_placeholder_is_refused(self) -> None:
        text = _template_text(greeting="Hello {{nickname}},")

        with pytest.raises(compose.ComposeError, match="nickname"):
            compose.render(parse(text), compose.values_for(host="site.test"))

    def test_rewrites_replace_only_their_zones(self) -> None:
        rendered = compose.render(parse(_template_text()), compose.values_for(host="site.test"))

        letter = compose.assemble(rendered, {"greeting": "Good afternoon,"})

        assert "Good afternoon," in letter.body
        assert "We pay per published article." in letter.body


class TestUniqueness:
    def test_identical_text_is_zero(self) -> None:
        assert uniqueness.difference("one two three", "one two three") == 0.0

    def test_measured_against_substituted_template(self) -> None:
        """Главное решение: подстановка сама по себе не считается отличием.

        Иначе одинаковые письма выглядели бы уникализированными просто
        потому, что в них разные имена сайтов.
        """
        rendered = compose.render(parse(_template_text()), compose.values_for(host="site.test"))
        letter = compose.assemble(rendered, {})

        assert uniqueness.difference(letter.plain_body, letter.body) == 0.0

    def test_corridor_verdict_names_both_bounds(self) -> None:
        assert uniqueness.corridor_verdict(0.02) is not None
        assert uniqueness.corridor_verdict(0.20) is None
        assert uniqueness.corridor_verdict(0.90) is not None

    def test_reordered_sentences_count_as_change(self) -> None:
        """Сравнение позиционное: для читателя переставленные предложения —
        другое письмо, для множества слов — то же самое."""
        before = "first sentence here. second sentence there."
        after = "second sentence there. first sentence here."

        assert uniqueness.difference(before, after) > 0


class TestForbiddenContent:
    @pytest.mark.parametrize(
        "text",
        [
            "your DR 46 site looks great",
            "we checked your Domain Rating",
            "impressive organic traffic on the blog",
            "we saw you in Ahrefs",
            "your referring domains count is high",
        ],
    )
    def test_metrics_are_caught(self, text: str) -> None:
        """Правила Ahrefs: метрики не попадают в письмо адресату.
        Платит за нарушение ключ, а не письмо."""
        assert guards.metrics_leak(text) is not None

    def test_ordinary_letter_passes(self) -> None:
        assert guards.metrics_leak(default().body) is None

    def test_traffic_alone_is_allowed(self) -> None:
        """Проверка по фразам, а не по словам: «traffic» в письме законно."""
        assert guards.metrics_leak("the traffic light outside my window") is None


class TestMasking:
    def test_addresses_do_not_leave(self) -> None:
        hidden = masking.mask("write to editor@site.test or to ads@site.test")

        assert masking.leaked(hidden.text) is None
        assert len(hidden.labels) == 2

    def test_same_address_gets_one_label(self) -> None:
        hidden = masking.mask("editor@site.test and editor@site.test")

        assert len(hidden.labels) == 1

    def test_addresses_come_back(self) -> None:
        hidden = masking.mask("write to editor@site.test")

        assert masking.unmask(hidden.text, hidden.labels) == "write to editor@site.test"

    def test_lost_label_is_a_failure(self) -> None:
        """Съеденная моделью метка оставила бы в письме дыру на месте адреса."""
        hidden = masking.mask("write to editor@site.test")

        with pytest.raises(masking.UnmaskError, match="потеряла"):
            masking.unmask("write to us", hidden.labels)

    def test_invented_label_is_a_failure(self) -> None:
        with pytest.raises(masking.UnmaskError, match="не выдавали"):
            masking.unmask("write to [address 9]", {})


class TestReplyAddress:
    SECRET = "s" * 32

    def test_address_carries_the_message(self) -> None:
        address = reply_to.address_for(
            417,
            sender_email="anna@mail.example.test",
            reply_domain="replies.ours.test",
            secret=self.SECRET,
        )

        assert address.startswith("anna+m417.")
        assert reply_to.message_id_from(address, secret=self.SECRET) == 417

    def test_forged_label_does_not_pass(self) -> None:
        """Без подписи чужой подсунул бы ответ в чужой диалог."""
        forged = "anna+m418.0000000000@replies.ours.test"

        assert reply_to.message_id_from(forged, secret=self.SECRET) is None

    def test_address_without_label_is_not_a_failure(self) -> None:
        """Письмо на общий ящик — законный случай: остаётся запасной путь
        через заголовки цепочки."""
        assert reply_to.message_id_from("info@ours.test", secret=self.SECRET) is None

    def test_missing_reply_domain_is_named(self) -> None:
        with pytest.raises(reply_to.ReplyAddressError, match="OUTREACH_REPLY_DOMAIN"):
            reply_to.address_for(1, sender_email="a@b.test", reply_domain="", secret=self.SECRET)


class TestTransport:
    def _outgoing(self, to: str) -> Outgoing:
        return Outgoing(
            message_id=1,
            to=to,
            from_email="anna@mail.example.test",
            from_name="Anna",
            reply_to=None,
            subject="Rates",
            body="Hello",
        )

    async def test_null_transport_refuses_real_addresses(self) -> None:
        """Тот же запрет, что после 670 сожжённых юнитов: заглушка
        не касается боевых данных. Письмо, выглядящее отправленным
        и не ушедшее, — худший исход из возможных."""
        with pytest.raises(TransportError, match="боевой адрес"):
            await NullTransport().send(self._outgoing("editor@real-site.com"))

    async def test_null_transport_works_on_demo_addresses(self) -> None:
        result = await NullTransport().send(self._outgoing("info@demo.example.test"))

        assert result.startswith("null-")

    def test_live_transport_without_a_key_is_refused(self) -> None:
        """Раньше здесь был отказ «транспорт ещё не написан». Теперь он
        написан, и единственное, чего ему не хватает, — ключ платформы:
        промолчать и собрать транспорт без ключа значит показать
        отправленными письма, которых платформа не приняла."""
        with pytest.raises(TransportError, match="OUTREACH_SENDGRID_API_KEY"):
            build_transport("sendgrid")

    def test_unknown_transport_lists_the_known(self) -> None:
        with pytest.raises(TransportError, match="null"):
            build_transport("carrier-pigeon")


class TestSenderChoice:
    def test_picks_the_one_with_most_room(self) -> None:
        senders = [_sender(1), _sender(2)]

        spot = pick(senders, sent_today={1: 15, 2: 3}, stage=Stage.DONORS, now=NOW)

        assert spot is not None
        assert spot.sender.id == 2

    def test_warmup_limits_the_room(self) -> None:
        """Ящик на первом дне разгона может пять писем, а не двадцать."""
        fresh = _sender(1, warmup_started_at=NOW)
        mature = _sender(2)

        spot = pick([fresh, mature], sent_today={1: 0, 2: 10}, stage=Stage.DONORS, now=NOW)

        assert spot is not None
        assert spot.sender.id == 2

    def test_exhausted_box_is_not_offered(self) -> None:
        spot = pick([_sender(1)], sent_today={1: 20}, stage=Stage.DONORS, now=NOW)

        assert spot is None

    def test_disabled_box_is_not_offered(self) -> None:
        spot = pick([_sender(1, enabled=False)], sent_today={}, stage=Stage.DONORS, now=NOW)

        assert spot is None

    def test_other_stage_is_not_offered(self) -> None:
        """Домены Этапа 2 конфликтнее, и репутацию доноров они задевать
        не должны."""
        spot = pick(
            [_sender(1, stage=Stage.ADVERTISERS)], sent_today={}, stage=Stage.DONORS, now=NOW
        )

        assert spot is None

    def test_yesterday_does_not_count(self) -> None:
        """Счёт дневного расхода идёт по письмам за сегодня: вчерашние
        в словаре не появляются, и ящик снова может писать."""
        spot = pick([_sender(1)], sent_today={}, stage=Stage.DONORS, now=NOW + timedelta(days=1))

        assert spot is not None
        assert spot.remaining == 20


class TestRewriteRequest:
    def test_reasoning_model_gets_its_own_limits(self) -> None:
        payload = build_payload(
            "gpt-5",
            zones={"greeting": "Hi"},
            about=Personalization(host="site.test", country="us"),
        )

        assert "max_completion_tokens" in payload
        assert "temperature" not in payload

    def test_niche_reaches_the_prompt(self) -> None:
        payload = build_payload(
            "gpt-4.1",
            zones={"greeting": "Hi"},
            about=Personalization(host="site.test", country="us", niche=("home repair",)),
        )

        assert "home repair" in payload["messages"][1]["content"]

    def test_fixed_zones_never_reach_the_model(self) -> None:
        """Проверка главного решения уникализации: условия сделки модель
        не видит, поэтому изменить их не может."""
        template: Template = parse(_template_text())
        rendered = compose.render(template, compose.values_for(host="site.test"))

        zones = {z.name: z.text for z in rendered.rewritable()}

        assert set(zones) == {
            name for name, kind in REQUIRED_ZONES.items() if kind is ZoneKind.REWRITE
        }
        payload = build_payload(
            "gpt-5", zones=zones, about=Personalization(host="site.test", country="us")
        )
        sent = payload["messages"][1]["content"]
        assert "We pay per published article" not in sent
        assert "Unsubscribe" not in sent

    def test_unknown_zones_are_dropped_not_taken(self) -> None:
        answered = parse_zones(
            '{"greeting": "Hi!", "terms": "we pay nothing"}', expected={"greeting"}
        )

        assert answered == {"greeting": "Hi!"}

    def test_broken_answer_is_an_empty_result(self) -> None:
        """Отказ модели — не отказ письма: зоны остаются шаблонными."""
        assert parse_zones("not json at all", expected={"greeting"}) == {}


class TestAdvertiserTemplate:
    """Оффер рекламодателю: набор зон тот же, границы другие.

    Шаблон без проверки — это текстовый файл: разбираться он перестанет
    молча, а заметит это первое письмо. Здесь проверяется не текст
    (он выдуман и будет утверждён), а то, что делает его шаблоном.
    """

    def test_it_parses_with_the_same_zones_as_the_donor_letter(self) -> None:
        letter = advertiser()

        assert {zone.name for zone in letter.zones} == set(REQUIRED_ZONES)

    def test_the_donor_price_is_not_in_the_letter(self) -> None:
        """Требование расходилось само с собой — «Вводная» допускала цену,
        чеклист запрещал, — и решено в пользу запрета: названная чужая
        цена это и претензия от площадки, и вопрос об источнике,
        на который нечем ответить."""
        body = advertiser().body

        assert not re.search(r"[$€£]\s?\d|\b\d+\s?(?:USD|EUR|GBP)\b", body)

    def test_provider_metrics_are_not_mentioned(self) -> None:
        """Правило самого провайдера: нарушение бьёт не по письму,
        а по ключу, на котором держится весь сбор базы."""
        body = advertiser().body

        assert not re.search(r"\b(?:DR|domain rating|traffic|backlinks?)\b", body, re.I)

    def test_it_personalises_by_the_link_we_actually_found(self) -> None:
        """Требование просит письмо «под конкретную найденную ссылку —
        страницу и анкор». Обе подстановки обязаны быть в шаблоне,
        иначе персонализации взяться неоткуда."""
        placeholders = advertiser().placeholders()

        assert {"donor_host", "page_url", "anchor"} <= placeholders

    def test_signed_and_without_the_legal_block(self) -> None:
        """Юридический блок снят для обоих этапов (23.09.2026): требования
        второго этапа отсылали к первому."""
        placeholders = advertiser().placeholders()

        assert "sender_name" in placeholders
        assert not {"postal_address", "unsubscribe_url"} & placeholders

    def test_the_corridor_is_reachable(self) -> None:
        """Если переписываемых зон мало, каждое письмо уходило бы
        с пометкой «ниже коридора», и человек искал бы поломку в модели,
        а не в шаблоне."""
        letter = advertiser()

        assert letter.rewritable_share * REWRITE_YIELD >= 0.15

    def test_the_found_link_never_reaches_the_model(self) -> None:
        """Промпт запрещает модели утверждать, что она читала статью, —
        фраза про конкретную страницу в переписываемой зоне вылетела бы
        первой, а с ней и персонализация, которую просит требование.
        Поэтому ссылка стоит только там, куда модель не смотрит."""
        link = compose.FoundLink(
            donor_host="donor.test", page_url="https://donor.test/best-tools/", anchor="best tools"
        )
        rendered = compose.render(advertiser(), compose.values_for(host="brand.test", link=link))

        to_model = " ".join(zone.text for zone in rendered.rewritable())
        assert not {"donor.test", "https://donor.test/best-tools/", "best tools"} & {
            value for value in (link.donor_host, link.page_url, link.anchor) if value in to_model
        }
        kept = compose.assemble(rendered, {"opening": "Totally different words here."}).body
        assert '"best tools"' in kept
        assert "https://donor.test/best-tools/" in kept

    def test_a_link_in_a_rewritten_zone_is_refused(self) -> None:
        """Шаблон правят на экране; анкор, перенесённый во вступление,
        собрался бы и ушёл пересказанным — видно это было бы только
        у адресата."""
        text = ADVERTISER_PATH.read_text(encoding="utf-8").replace(
            "Your company came up", 'Your "{{anchor}}" link came up'
        )

        with pytest.raises(TemplateError, match="переписываемой зоне «opening»"):
            parse(text, ADVERTISER)

    def test_a_letter_without_the_link_is_refused(self) -> None:
        text = ADVERTISER_PATH.read_text(encoding="utf-8").replace('anchored "{{anchor}}", ', "")

        with pytest.raises(TemplateError, match="нет подстановки"):
            parse(text, ADVERTISER)

    def test_the_donor_letter_does_not_know_the_link(self) -> None:
        """`{{anchor}}` в письме донору — опечатка, а не пустота: без ссылки
        подстановки нет вовсе, и сборка называет это громко."""
        text = _template_text(offer="We saw your link anchored {{anchor}}.")

        with pytest.raises(compose.ComposeError, match="anchor"):
            compose.render(parse(text), compose.values_for(host="site.test"))

    def test_an_empty_link_is_loud_and_named_as_such(self) -> None:
        """Пустой анкор при сборке — громкая метка в тексте, и отправка
        по ней откажет: письмо «под ссылку» без ссылки уходить не должно."""
        link = compose.FoundLink(
            donor_host="donor.test", page_url="https://donor.test/p", anchor=" "
        )
        letter = compose.assemble(
            compose.render(advertiser(), compose.values_for(host="brand.test", link=link)), {}
        )

        assert "АНКОР ССЫЛКИ НЕ ЗАДАН" in compose.unset_in(letter.body)
        assert compose.LINK_TITLES & set(compose.unset_in(letter.body))


class TestRewriteKeepsTheQuestions:
    """Боевой текст (23.09.2026) держит шесть вопросов в переписываемой
    зоне: на них донор отвечает и их разбирает разбор ответа."""

    ASK = "Could you let me know:\n1. Do you accept guest posts?\n2. What's the price?"

    def test_lost_item_leaves_the_zone_as_template(self) -> None:
        result = RewriteResult()

        _accept("ask", "Could you tell me your price?", {}, before=self.ASK, into=result)

        assert "ask" not in result.zones
        assert "1, 2" in result.notes[0]

    def test_renumbered_list_is_refused_too(self) -> None:
        result = RewriteResult()
        after = "Tell me:\n1. Do you take guest posts?\n3. How much is it?"

        _accept("ask", after, {}, before=self.ASK, into=result)

        assert "ask" not in result.zones

    def test_rephrased_items_are_accepted(self) -> None:
        result = RewriteResult()
        after = "Could you share:\n1. Are guest posts welcome?\n2. How much per article?"

        _accept("ask", after, {}, before=self.ASK, into=result)

        assert result.zones["ask"] == after


class TestChangeShare:
    """Сколько просить поменять — от доли переписываемых зон в письме."""

    def test_battle_template_asks_for_less_than_half(self) -> None:
        """Переписывается ~73% письма: «поменяй половину» дало бы ~37%
        отличия при верхнем крае коридора 25%."""
        rendered = compose.render(default(), compose.values_for(host="site.test"))

        share = change_share(rendered)

        assert CHANGE_MIN <= share < 0.35

    def test_small_rewritable_part_asks_for_the_most(self) -> None:
        """Переписываемая часть — треть письма: чтобы дойти до середины
        коридора, просить надо больше половины — упираемся в потолок."""
        text = _template_text(
            offer=" ".join(["We place sponsored articles for clients every week."] * 6),
            terms=" ".join(["We pay per published article, on time."] * 6),
        )
        rendered = compose.render(parse(text), compose.values_for(host="site.test"))

        assert change_share(rendered) == CHANGE_MAX

    def test_advertiser_offer_reaches_the_middle_below_the_ceiling(self) -> None:
        """У оффера рекламодателю переписывалось 38% письма, и даже просьба
        «поменяй половину» оставляла его у нижнего края коридора. Фраза
        со ссылкой ушла в неизменяемую зону, вступление и вопрос выросли —
        середина коридора достижима без упора в потолок, даже с длинным
        адресом страницы."""
        link = compose.FoundLink(
            donor_host="techradar-like.example",
            page_url="https://techradar-like.example/best/vpn-services-for-streaming-2026/",
            anchor="best VPN for streaming",
        )
        rendered = compose.render(advertiser(), compose.values_for(host="brand.test", link=link))

        assert CHANGE_MIN <= change_share(rendered) < CHANGE_MAX

    def test_share_reaches_the_model(self) -> None:
        payload = build_payload(
            "gpt-5",
            zones={"greeting": "Hi,"},
            about=Personalization(host="site.test", country="us"),
            change=0.27,
        )

        assert "Change roughly 27% of the words" in payload["messages"][1]["content"]
