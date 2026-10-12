"""Имена перед моделью — метками, как адреса, и обратно после.

Требование: «уходит текст письма и текст ответа; адреса и имена заменяются метками до
отправки и возвращаются после». До 12.10.2026 маскирование знало только адреса:
«Hi Anna, … Best, Elena» уходило модели как есть. Имена — те, что мы знаем: наше имя
отправителя и имя из «От кого» ответа; его приём теперь хранит у ответа.

Модель здесь — подставная, сеть — `httpx.MockTransport`: проверяется, что уходит
в запрос и что делают с её ответом, а не сама модель.
"""

from __future__ import annotations

import inspect
import json
import re
from typing import Any

import httpx
import pytest
from backend.config import outreach as outreach_cfg
from backend.features.agent import drafting
from backend.features.agent import writer as agent_writer
from backend.features.agent.stages import Conversation
from backend.features.agent.writer import Request, Turn
from backend.features.core.domain import ReplyKind, Stage
from backend.features.core.models.outreach import ReplyModel
from backend.features.letters import masking
from backend.features.letters.building import BuildRequest, QueueBuilder
from backend.features.letters.compose import Rendered
from backend.features.letters.rewrite import Personalization, RewriteClient, RewriteResult
from backend.features.letters.template import Zone, ZoneKind
from backend.features.replies.extract import ExtractClient, Extracted
from backend.features.replies.inbound import AttachedText, Incoming
from backend.features.replies.mime import from_form, sender_name
from backend.features.replies.parse_input import parse_input
from backend.features.replies.repository import ReplyRepository
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor
from tests.test_agent_drafting import FakeWriter, agent_on, reply
from tests.test_agent_writer import SETTINGS, Model, _write
from tests.test_replies_inbox import sent

__all__ = ["reply", "sent"]  # фикстуры ответа донора — отсюда их видит pytest

#: Ключ модели — выдуманный: сеть здесь подставная.
KEY = "test-key"  # pragma: allowlist secret
OURS = "Anna Smith"
THEIRS = "Elena Petrova"


class TestMask:
    def test_a_known_name_goes_whole_and_by_its_words(self) -> None:
        text = "Hi Anna, Anna Smith here. Best, Smith"

        hidden = masking.mask(text, names=[OURS])

        assert "Anna" not in hidden.text
        assert "Smith" not in hidden.text
        assert sorted(hidden.labels.values()) == ["Anna", "Anna Smith", "Smith"]
        assert masking.unmask(hidden.text, hidden.labels) == text

    def test_the_whole_name_in_any_case_its_words_as_written(self) -> None:
        """Слово имени — только с большой буквы, как пишут имена: «Will», «Mark» —
        и имена, и слова, и «we will publish» не теряет глагол."""
        hidden = masking.mask("ANNA SMITH wrote; anna asked", names=[OURS])

        assert hidden.text == "[name 1] wrote; anna asked"

    def test_an_address_stays_an_address(self) -> None:
        hidden = masking.mask("Write to anna.smith@site.test", names=[OURS])

        assert hidden.text == "Write to [address 1]"
        assert hidden.labels == {"[address 1]": "anna.smith@site.test"}

    def test_the_site_name_inside_a_domain_stays(self) -> None:
        """По имени сайта модель находит его строку в прайсе на несколько сайтов."""
        text = "A guest post on example.com is $100. Example Media"

        hidden = masking.mask(text, names=["Example Media"])

        assert hidden.text == "A guest post on example.com is $100. [name 1]"

    @pytest.mark.parametrize("name", ["Editorial Team", "Guest Posts", "Sales", "Example.com"])
    def test_a_role_or_a_domain_is_not_a_person(self, name: str) -> None:
        text = "Guest posts are $100, the editorial team and sales agree. Example.com"

        assert masking.mask(text, names=[name]).text == text

    def test_role_words_stay_next_to_a_person(self) -> None:
        hidden = masking.mask("Anna from Sales: a guest post is $100", names=["Anna | Sales"])

        assert hidden.text == "[name 1] from Sales: a guest post is $100"

    def test_without_names_only_addresses_go_as_before(self) -> None:
        """Продажи и агент зовут маскирование без имён — их поведение прежнее."""
        hidden = masking.mask("Hi Anna, write to anna@site.test")

        assert hidden.text == "Hi Anna, write to [address 1]"
        assert masking.leaked("Hi Anna") is None

    def test_a_leak_names_the_name(self) -> None:
        assert masking.leaked("Thanks, Anna", names=[OURS]) == "Anna"
        assert masking.leaked("Thanks, [name 1]", names=[OURS]) is None

    def test_one_masker_gives_one_label_across_texts(self) -> None:
        masker = masking.Masker([THEIRS])

        assert (masker("Elena"), masker("Best, Elena")) == ("[name 1]", "Best, [name 1]")
        assert masker.labels == {"[name 1]": "Elena"}

    def test_a_lost_name_label_is_a_failure(self) -> None:
        hidden = masking.mask("I'm Anna", names=[OURS])

        with pytest.raises(masking.UnmaskError, match="потеряла"):
            masking.unmask("I'm here", hidden.labels)

    def test_an_invented_name_label_is_a_failure(self) -> None:
        with pytest.raises(masking.UnmaskError, match=r"не выдавали: \[name 9\]"):
            masking.restore("Thanks, [name 9]", {})


#: Как маскирование работало до 12.10.2026 — только адреса, слово в слово: мерка
#: для «без имён — как было» у продаж и агента.
_OLD_ADDRESS = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+", re.UNICODE)


def _old_mask(text: str) -> tuple[str, dict[str, str]]:
    labels: dict[str, str] = {}
    known: dict[str, str] = {}

    def replace(found: re.Match[str]) -> str:
        address = found.group(0)
        if address not in known:
            known[address] = f"[address {len(known) + 1}]"
            labels[known[address]] = address
        return known[address]

    return _OLD_ADDRESS.sub(replace, text), labels


class TestSalesStayAsBefore:
    """masking.py общий с продажами (`sales/reply_kind.py`, `sales/agent/calling.py`)
    и агентом (`agent/writer.py`): они зовут его без имён, и без имён всё прежнее —
    вызов, метки, тексты и отказы."""

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "No address here, Anna.",
            "Write to editor@site.test or ads@site.test, editor@site.test again",
            "Елена <елена@пример.рф> пишет",
            "Already masked: [address 1] and [name 1]",
        ],
    )
    def test_without_names_the_labels_are_word_for_word_the_old_ones(self, text: str) -> None:
        hidden = masking.mask(text)

        assert (hidden.text, hidden.labels) == _old_mask(text)
        assert masking.leaked(hidden.text) is None

    def test_the_call_shapes_stay(self) -> None:
        """Тест продаж подменяет `mask` функцией с одним доводом (`lambda text: …`),
        а сами продажи зовут `mask(text)` и `leaked(text)`: имена — только по имени
        довода и с умолчанием."""
        for function in (masking.mask, masking.leaked):
            text, names = inspect.signature(function).parameters.values()
            assert (text.name, text.kind) == ("text", inspect.Parameter.POSITIONAL_OR_KEYWORD)
            assert (names.name, names.kind) == ("names", inspect.Parameter.KEYWORD_ONLY)
            assert names.default == ()

    def test_address_labels_come_back_and_refusals_keep_their_words(self) -> None:
        hidden = masking.mask("write to editor@site.test")

        assert masking.restore("Mail [address 1]", hidden.labels) == "Mail editor@site.test"
        with pytest.raises(masking.UnmaskError, match="потеряла"):
            masking.unmask("write to us", hidden.labels)
        with pytest.raises(masking.UnmaskError, match=r"не выдавали: \[address 9\]"):
            masking.unmask("write to [address 9]", {})


class TestNameShapes:
    @pytest.mark.parametrize(
        ("names", "text", "hidden"),
        [
            # Имя по-русски — те же правила.
            (
                ["Анна Петрова"],
                "Здравствуйте, Анна! С уважением, Петрова",
                "Здравствуйте, [name 1]! С уважением, [name 2]",
            ),
            # Апостроф и дефис внутри слова имени, инициал из одной буквы — нет.
            (["Liam O'Brien"], "Hi O'Brien, it's Liam.", "Hi [name 1], it's [name 2]."),
            (["Jean-Luc Picard"], "Dear Jean-Luc,", "Dear [name 1],"),
            (["J. Smith"], "J will answer. Smith", "J will answer. [name 1]"),
            # «Ann» — не часть «Annual»; притяжательное «Ann's» — имя.
            (["Ann Lee"], "Annual rates: Ann's reply", "Annual rates: [name 1]'s reply"),
            # Имя «Фамилия, Имя» — целиком одной меткой.
            (["Smith, Anna"], "Smith, Anna wrote", "[name 1] wrote"),
            # Пустое имя и повтор ничего не меняют.
            (["", OURS, OURS], "Anna", "[name 1]"),
        ],
    )
    def test_shapes_of_names(self, names: list[str], text: str, hidden: str) -> None:
        masked = masking.mask(text, names=names)

        assert masked.text == hidden
        assert masking.unmask(masked.text, masked.labels) == text

    def test_two_people_and_an_address_get_their_own_numbers(self) -> None:
        masked = masking.mask("Hi Anna, Elena here: elena@donor.example.test", names=[OURS, THEIRS])

        assert masked.text == "Hi [name 1], [name 2] here: [address 1]"
        assert masking.unmask(masked.text, masked.labels) == (
            "Hi Anna, Elena here: elena@donor.example.test"
        )

    @pytest.mark.parametrize("name", ["Name", "Address", "Info", "noreply"])
    def test_the_label_words_are_never_names(self, name: str) -> None:
        """Метка `[name 1]` сама состоит из слова «name»: спрятанное «Name» сломало бы
        возврат, а проверка утечки нашла бы его в собственной метке."""
        masked = masking.mask("Name: Info, address noreply", names=[name])

        assert masked.text == "Name: Info, address noreply"
        assert masking.leaked("[name 1] [address 1]", names=[name]) is None


class TestCaseAndDomains:
    def test_a_name_word_in_capitals_is_hidden_too(self) -> None:
        """Подпись капсом — то же имя: «ANNA», «Smith» и «SMITH» прячутся. Со строчной
        слово не прячется: «will», «mark» — слова языка, а имя пишут с заглавной."""
        masked = masking.mask("Thanks, ANNA. Best, SMITH; anna asked", names=[OURS])

        assert masked.text == "Thanks, [name 1]. Best, [name 2]; anna asked"

    def test_a_name_written_in_lowercase_is_hidden_as_written_and_capitalised(self) -> None:
        masked = masking.mask("anna, Anna, ANNA", names=["anna"])

        assert masked.text == "[name 1], [name 2], [name 3]"

    @pytest.mark.parametrize(
        ("name", "text", "hidden"),
        [
            ("J.Smith", "Best, J.Smith. Smith", "Best, [name 1]. [name 2]"),
            ("Dr.Smith", "Dr. Smith wrote; Smith", "[name 1] wrote; [name 2]"),
        ],
    )
    def test_an_initial_or_a_title_with_a_dot_is_a_name(
        self, name: str, text: str, hidden: str
    ) -> None:
        """«J.Smith» и «Dr.Smith» — не домены: зон «smith» в списке суффиксов нет."""
        assert masking.mask(text, names=[name]).text == hidden

    @pytest.mark.parametrize(
        "name", ["Example.com", "Example.co.uk", "donor.example.test", "Example.co.uk Team"]
    )
    def test_a_domain_is_not_a_name(self, name: str) -> None:
        text = "A post on Example.com, Example.co.uk and donor.example.test. Example"

        assert masking.mask(text, names=[name]).text == text


class Network:
    """Модель на месте сети: запоминает запрос, отвечает заданной формой."""

    def __init__(self, answer: dict[str, Any]) -> None:
        self.answer = answer
        self.sent: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(json.loads(request.content))
        body = {
            "choices": [{"message": {"content": json.dumps(self.answer)}}],
            "usage": {"total_tokens": 300},
        }
        return httpx.Response(200, json=body)

    @property
    def user(self) -> str:
        content: str = self.sent[-1]["messages"][1]["content"]
        return content


def _answer(**fields: Any) -> dict[str, Any]:
    return {
        "price_white": None,
        "price_grey": None,
        "currency": None,
        "offers": [],
        "payment_methods": [],
        "placement_days": None,
        "link_type": None,
        "placement": "unclear",
        "label_stated": False,
        "placement_quote": None,
        "confidence": 0.9,
        "note": None,
    } | fields


async def _extract(network: Network, text: str, subject: str = "Re: Guest article") -> Extracted:
    incoming = Incoming(
        message_id="<in-1@site.test>",
        to=(),
        from_email="elena@donor.example.test",
        from_name=THEIRS,
        subject=subject,
        text=text,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(network)) as http:
        return await ExtractClient(http, model="gpt-4o-mini", api_key=KEY).extract(incoming)


class TestReplyParse:
    async def test_our_name_and_theirs_do_not_reach_the_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(outreach_cfg, "SENDER_NAME", OURS)
        network = Network(_answer(price_white=150, currency="USD", placement="sells"))

        found = await _extract(
            network,
            "Hi Anna,\nA guest post is 150 USD.\nBest, Elena Petrova",
            subject="Re: Guest article for Anna",
        )

        for name in ("Anna", "Smith", "Elena", "Petrova"):
            assert name not in network.user
        assert "Subject: Re: Guest article for [name " in network.user
        assert "150 USD" in network.user
        assert found.confidence == 0.9

    async def test_a_quote_with_a_label_comes_back_as_written(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Цитата «не продаём» сверяется с письмом: с меткой внутри она не нашлась бы,
        и верный отказ ушёл бы человеку с нулевой уверенностью."""
        monkeypatch.setattr(outreach_cfg, "SENDER_NAME", OURS)
        network = Network(
            _answer(placement="declines", placement_quote="Sorry [name 1], we do not sell posts")
        )

        found = await _extract(network, "Sorry Anna, we do not sell posts.")

        assert found.placement_quote == "Sorry Anna, we do not sell posts"
        assert (found.declines, found.confidence) == (True, 0.9)

    async def test_payment_and_products_come_back_as_written(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(outreach_cfg, "SENDER_NAME", "")
        network = Network(
            _answer(
                price_white=150,
                currency="USD",
                placement="sells",
                payment_methods=["invoice to [name 1]"],
                offers=[{"product": "post by [name 1]", "price": 150, "currency": "USD"}],
            )
        )

        found = await _extract(network, "Elena writes posts: 150 USD, invoice to Elena.")

        assert found.payment_methods == ("invoice to Elena",)
        assert [offer.product for offer in found.offers] == ["post by Elena"]

    async def test_an_invented_label_sends_the_parse_to_a_person(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(outreach_cfg, "SENDER_NAME", OURS)
        network = Network(_answer(placement="declines", placement_quote="[name 7] says no"))

        found = await _extract(network, "No thanks.")

        assert found.confidence == 0.0
        assert any("не выдавали: [name 7]" in note for note in found.notes)


class TestReplyFiles:
    async def test_a_name_in_an_attached_price_list_is_hidden_too(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Прайс файлом — тот же ответ донора: имя в нём уходит той же меткой, что
        в письме, — словарь меток у письма, файлов и темы один."""
        monkeypatch.setattr(outreach_cfg, "SENDER_NAME", OURS)
        network = Network(_answer(price_white=150, currency="USD", placement="sells"))
        incoming = Incoming(
            message_id="<in-2@site.test>",
            to=(),
            from_email="elena@donor.example.test",
            from_name=THEIRS,
            subject="Re: Guest article",
            text="Hi Anna, the price list is attached. Elena",
            attached=(AttachedText(name="prices.txt", text="Elena Petrova\nGuest post 150 USD"),),
        )

        async with httpx.AsyncClient(transport=httpx.MockTransport(network)) as http:
            await ExtractClient(http, model="gpt-4o-mini", api_key=KEY).extract(incoming)

        attachments = network.user.split("<<<ATTACHMENTS", 1)[1]
        assert "Elena" not in network.user
        assert "Petrova" not in network.user
        assert "[name 3]\nGuest post 150 USD" in attachments
        assert network.user.count("[name 2]") == 1


class TestRewrite:
    """Имя отправителя попадает в переписываемую зону, когда шаблон правят:
    «Hi, I'm Anna Smith» в приветствии. Прячут его письма доноров и рекламодателей
    (`building.py` кладёт имя в `Personalization.names`); продажи зовут тот же клиент
    без имён — их запрос прежний."""

    LETTER = Rendered(
        subject="Rates",
        zones=(
            Zone("greeting", ZoneKind.REWRITE, "Hi, I'm Anna Smith,"),
            Zone("signature", ZoneKind.FIXED, "Best regards,\nAnna Smith"),
        ),
    )

    async def _rewrite(
        self, answer: str, *, names: tuple[str, ...], hint: str | None = None
    ) -> tuple[dict[str, str], str]:
        seen: list[dict[str, Any]] = []

        def model(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content))
            content = {"choices": [{"message": {"content": json.dumps({"greeting": answer})}}]}
            return httpx.Response(200, json=content | {"usage": {"total_tokens": 10}})

        about = Personalization(host="x.example.test", country="us", names=names)
        async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as http:
            result = await RewriteClient(http, model="gpt-5", api_key=KEY).rewrite(
                self.LETTER, about, hint=hint
            )
        user: str = seen[0]["messages"][1]["content"]
        return result.zones, user

    async def test_our_name_goes_as_a_label_and_comes_back(self) -> None:
        zones, user = await self._rewrite("Hello, this is [name 1],", names=(OURS,))

        assert "Anna" not in user
        assert zones == {"greeting": "Hello, this is Anna Smith,"}

    async def test_a_retry_with_a_hint_hides_the_name_too(self) -> None:
        """Повтор с подсказкой (подбор зон, 12.10.2026) идёт тем же путём маскирования:
        подсказка — числа и слова о коридоре, зоны — с метками."""
        hint = "A previous rewrite of these zones went too far. Change fewer words."
        zones, user = await self._rewrite("Hello, this is [name 1],", names=(OURS,), hint=hint)

        assert hint in user
        assert "Anna" not in user
        assert zones == {"greeting": "Hello, this is Anna Smith,"}

    async def test_a_lost_name_leaves_the_zone_as_the_template(self) -> None:
        zones, _ = await self._rewrite("Hello there,", names=(OURS,))

        assert zones == {}

    async def test_without_names_the_request_is_as_before(self) -> None:
        """Продажи: имён не передают — зона уходит как есть, как до 12.10.2026."""
        zones, user = await self._rewrite("Hello, this is Anna Smith,", names=())

        assert "Hi, I'm Anna Smith," in user
        assert zones == {"greeting": "Hello, this is Anna Smith,"}

    async def test_letters_of_donors_hide_our_sender_name(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        await make_donor(session, "one.example.test", email="info@one.example.test")
        rewriter = Remembering()

        builder = QueueBuilder(session, rewriter)  # type: ignore[arg-type]
        await builder.build(BuildRequest(campaign_name="Проверка", limit=5, niche=("hiking",)))

        assert rewriter.names == [(outreach_cfg.SENDER_NAME,)]


class Remembering:
    """Переписчик сборки писем: запоминает, какие имена ему велели спрятать."""

    def __init__(self) -> None:
        self.names: list[tuple[str, ...]] = []

    async def rewrite(self, rendered: object, about: Personalization) -> RewriteResult:
        self.names.append(about.names)
        return RewriteResult()


class TestAgentDraft:
    """Черновик агента переписки: у доноров и рекламодателей наше имя (им подписан
    черновик, `sign_as`) и имена собеседников из «От кого» уходят модели метками и
    возвращаются в черновик именами; у продаж — как было."""

    async def test_names_do_not_reach_the_model_and_come_back_in_the_draft(self) -> None:
        request = Request(
            stage=Stage.DONORS,
            settings=SETTINGS,
            turns=(
                Turn(ours=True, text="Hi! I'm Anna Smith from the agency."),
                Turn(ours=False, text="Hi Anna, a post is $90. Elena"),
            ),
            sign_as=OURS,
            parsed={},
            names=(OURS, THEIRS),
        )
        hidden = masking.mask(agent_writer.user_message(request), names=request.names)
        label = {said: label for label, said in hidden.labels.items()}
        body = f"Dear {label['Elena']},\nDeal at $90.\n{label[OURS]}"
        model = Model({"body": body, "needs_human": False, "reason": ""})

        written = await _write(model, request)

        content = model.sent[0]["messages"][1]["content"]
        for name in ("Anna", "Smith", "Elena"):
            assert name not in content
        assert '"sign_as": "[name 1]"' in content
        assert written.body == f"Dear Elena,\nDeal at $90.\n{OURS}"
        assert written.needs_human is False

    async def test_drafts_of_donors_pass_our_name_and_theirs(
        self, session: AsyncSession, reply: ReplyModel
    ) -> None:
        reply.from_name = THEIRS
        await session.commit()
        await agent_on(session)
        agent = FakeWriter()

        await drafting.draft_answer(session, agent, reply.id)

        [request] = agent.seen
        assert request.names == ("Anna", THEIRS)

    async def test_sales_drafts_pass_no_names(self, session: AsyncSession) -> None:
        """У продаж своё имя отправителя и свой контур проверок: запрос прежний."""
        conversation = Conversation(
            stage=Stage.SALES, thread_id=1, reply_id=1, turns=(), settings=SETTINGS
        )

        assert await drafting._hidden_names(session, conversation, OURS) == ()


class TestSenderName:
    @pytest.mark.parametrize(
        ("value", "name"),
        [
            ("Elena Petrova <elena@site.test>", "Elena Petrova"),
            ('"Petrova, Elena" <elena@site.test>', "Petrova, Elena"),
            ("=?utf-8?B?0JDQvdC90LAg0J/QtdGC0YDQvtCy0LA=?= <anna@site.test>", "Анна Петрова"),
            # «Петрова, Анна» закодировано: делим по адресам до раскодирования, иначе
            # запятая без кавычек отрезала бы «Петрова».
            ("=?utf-8?B?0J/QtdGC0YDQvtCy0LAsINCQ0L3QvdCw?= <anna@site.test>", "Петрова, Анна"),
            ("elena@site.test", ""),
            ('"elena@site.test" <elena@site.test>', ""),
            ("", ""),
        ],
    )
    def test_the_name_is_read_from_the_from_header(self, value: str, name: str) -> None:
        assert sender_name(value) == name

    def test_a_raw_letter_keeps_the_whole_name(self) -> None:
        """Письмо целиком (сырой режим платформы): разбор почты уже раскодировал имя
        и взял его в кавычки — запятая внутри имени не делит его."""
        raw = (
            "From: =?utf-8?B?0J/QtdGC0YDQvtCy0LAsINCQ0L3QvdCw?= <anna@site.test>\r\n"
            "To: anna@ours.test\r\nSubject: Re: price\r\n\r\nHi"
        )

        assert from_form({"email": raw}).from_name == "Петрова, Анна"

    def test_the_platform_form_carries_it(self) -> None:
        got = from_form({"from": "Elena Petrova <elena@site.test>", "to": "anna@ours.test"})

        assert (got.from_email, got.from_name) == ("elena@site.test", "Elena Petrova")

    async def test_the_reply_keeps_it_for_the_parse(self, session: AsyncSession) -> None:
        """Разбор идёт задачей очереди после приёма: имя, не сохранённое у ответа,
        прятать было бы нечего."""
        incoming = from_form({"from": "Elena Petrova <elena@site.test>", "text": "Hi"})
        stored = ReplyRepository(session).add(
            incoming, kind=ReplyKind.HUMAN, thread_id=None, message_id=None, found=None
        )
        await session.flush()

        assert stored.from_name == "Elena Petrova"
        assert (await parse_input(session, stored)).from_name == "Elena Petrova"
