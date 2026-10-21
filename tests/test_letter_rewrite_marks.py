"""Ответ переписчика зон: обычные знаки до проверок и замера, лицо говорящего — как в зоне.

Стенд продаж 21.10.2026: в «e-mail» модель ставила неразрывный дефис U+2011 — часть почтовых
программ рисует его квадратиком; бывают и мягкий перенос U+00AD, и знаки нулевой ширины. Их не
видно, а слово они режут надвое: замер отличия (`uniqueness.words`) считал такое слово двумя.
Знаки заменяются в одном месте — где разбирается ответ модели (`rewrite.parse_zones`), — и это
место одно у всех направлений: доноров, рекламодателей, форм контактов и продаж. Здесь каждый путь
проходит на настоящей базе настоящим клиентом поверх подставной модели.

Там же зона от «мы» вернулась от «я» у двух лидов из двух: правило промпта держит лицо и число
говорящего, как в зоне, — словами, без примеров одного языка.

Тексты выдуманы; языки и письменности — разные, с письмом справа налево и без пробелов.
"""

from __future__ import annotations

import itertools
import json
import logging
import random
import re
from collections.abc import Callable

import httpx
import pytest
from backend.features.core.domain import Stage
from backend.features.core.models.outreach import CampaignModel, MessageModel
from backend.features.letters import form_letter, rewrite, selection
from backend.features.letters.building import BuildRequest, QueueBuilder
from backend.features.letters.compose import Rendered, assemble
from backend.features.letters.rewrite import (
    SYSTEM,
    Personalization,
    RewriteClient,
    RewriteResult,
    parse_zones,
)
from backend.features.letters.template import Zone, ZoneKind
from backend.features.letters.uniqueness import difference
from backend.features.sales import queue
from backend.features.sales.models import SalesThreadModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.conftest import make_donor
from tests.test_form_letter import _accepted_in_run, _form_donor
from tests.test_letters_advertisers import make_advertiser, priced_donor
from tests.test_sales_followup_texts import PAIRS, ZONES, _chain

#: Дефисы, которые становятся обычным «-».
HYPHENS = "\u2010\u2011"
#: Невидимые знаки, которые убираются: мягкий перенос, нулевой ширины пробел, разделитель
#: и соединитель, «склейка слов», метка порядка байтов.
INVISIBLE = "\u00ad\u200b\u200c\u200d\u2060\ufeff"
MARKS = HYPHENS + INVISIBLE
#: Что уходит клиенту ключом: модель подставная, значение ей неважно.
SENT = "value"
#: Сайт адресата — для запроса модели; ниша выдумана.
ABOUT = Personalization(host="garden.example.test", country="de", niche=("raised garden beds",))


# --- знаки: каждый, смесь, текст без них ---------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "plain"),
    [
        ("Пришлите имя или e\u2010mail.", "Пришлите имя или e-mail."),
        ("Пришлите имя или e\u2011mail.", "Пришлите имя или e-mail."),
        ("Ein kurzer Vor\u00adschlag für Ihre Seite.", "Ein kurzer Vorschlag für Ihre Seite."),
        ("ご連絡\u200bください。", "ご連絡ください。"),
        ("Krótka pro\u200cpozycja dla Państwa.", "Krótka propozycja dla Państwa."),
        ("Yanıtınız için teş\u200dekkürler.", "Yanıtınız için teşekkürler."),
        ("Una propu\u2060esta breve.", "Una propuesta breve."),
        ("\ufeffمرحبا، لدينا اقتراح قصير.", "مرحبا، لدينا اقتراح قصير."),
    ],
    ids=["U+2010", "U+2011", "U+00AD", "U+200B", "U+200C", "U+200D", "U+2060", "U+FEFF"],
)
def test_each_mark_of_the_model_becomes_plain(text: str, plain: str) -> None:
    assert rewrite.plain_marks(text) == plain


def test_a_mix_of_marks_in_one_text() -> None:
    text = (
        "\ufeffWe\u2011d like to share a short pro\u00adposal\u200b with you \u2010 by e\u2011mail."
    )

    assert rewrite.plain_marks(text) == "We-d like to share a short proposal with you - by e-mail."


@pytest.mark.parametrize(
    "text",
    [
        # Неразрывный пробел, узкий неразрывный, кавычки, тире, минус — типографика, а не след.
        "Ответим за 2\u00a0дня — «как есть», без „лишнего“.",
        "We’ll reply in 2\u202fdays – “as is”; 5\u221210 isn’t a range.",
        "Wir antworten in 2\u00a0Tagen – „wie besprochen“.",
        "שלום, נשמח לשמוע ממך בקרוב.",
        "สวัสดีครับ ยินดีที่ได้รู้จัก",
        "ご連絡ありがとうございます。",
        "Prosimy o odpowiedź — e-mail wystarczy.",
    ],
    ids=["ru", "en", "de", "he", "th", "ja", "pl"],
)
def test_text_without_these_marks_is_unchanged(text: str) -> None:
    assert rewrite.plain_marks(text) == text


#: Куски текста на разных письменностях — для свойства ниже; дефис только обычный.
PIECES = (
    "Пишем снова",
    "We are writing",
    "Ich schreibe",
    "ご連絡ください",
    "مرحبا بكم",
    "שלום רב",
    "สวัสดีครับ",
    "e-mail",
    "Państwa",
    "teşekkürler",
    "2\u00a0дня",
    "„так“",
)


def test_marks_put_anywhere_leave_the_text_as_it_was() -> None:
    """Свойство, а не пример: знаки модели, вставленные куда угодно в текст на любой письменности,
    после чистки не оставляют следа — текст тот же, и вторая чистка его не меняет."""
    rng = random.Random(2110)
    for _ in range(300):
        text = " ".join(rng.sample(PIECES, 4))
        marked = list(text.replace("-", rng.choice(HYPHENS)))
        for _ in range(rng.randint(1, 6)):
            marked.insert(rng.randint(0, len(marked)), rng.choice(INVISIBLE))

        plain = rewrite.plain_marks("".join(marked))

        assert plain == text
        assert rewrite.plain_marks(plain) == plain


def test_an_invisible_mark_the_template_writes_itself_stays() -> None:
    """Нулевой ширины разделитель в персидском — орфография: шаблон пишет его сам, и
    убрать его только у модели значило бы разойтись с шаблоном и в написании, и в счёте слов
    коридора. Знак, которого в шаблоне нет, — след модели и уходит; дефис — обычным всегда."""
    template = "ما می\u200cخواهیم درباره\u200cی همکاری صحبت کنیم."
    answer = "ما می\u200cخواهیم\u200b درباره\u200cی هم\u2011کاری گفت\u200cوگو کنیم."

    assert rewrite.plain_marks(answer, template=template) == (
        "ما می\u200cخواهیم درباره\u200cی هم-کاری گفت\u200cوگو کنیم."
    )
    assert rewrite.plain_marks("e\u2011mail", template="e\u2011mail") == "e-mail"


# --- разбор ответа: одно место у всех направлений ------------------------------------------------


def test_the_answer_is_parsed_into_plain_zones_and_an_invisible_only_zone_stays_template() -> None:
    answered = parse_zones(
        json.dumps({"greeting": "\ufeffHallo\u200b,", "ask": "\u200b\u00ad ", "offer": "x"}),
        expected={"greeting", "ask"},
    )

    assert answered == {"greeting": "Hallo,"}


async def test_the_client_takes_plain_zones_before_its_checks(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Знаки уходят до возврата меток и проверок зоны: мягкий перенос внутри метки адреса и
    метка порядка байтов перед номером пункта больше не стоят зоне отказа. Замена оставляет
    свой след в журнале — зона и коды знаков: в письме их больше не видно."""
    caplog.set_level(logging.INFO, logger=rewrite.__name__)
    letter = Rendered(
        subject="Price",
        zones=(
            Zone("greeting", ZoneKind.REWRITE, "Hi there,"),
            Zone(
                "ask",
                ZoneKind.REWRITE,
                "Please reply to info@garden.example.test:\n1. What is the price?\n2. Dofollow?",
            ),
            Zone("signature", ZoneKind.FIXED, "Best regards,\nMira"),
        ),
    )
    answer = {
        "greeting": "Hello\u200b there,",
        "ask": "Kindly reply to [addr\u00adess 1]:\n\ufeff1. What would it cost?\n2. Do\u2011follow?",
    }

    result = await _client_answering(letter, answer)

    assert result.zones == {
        "greeting": "Hello there,",
        "ask": "Kindly reply to info@garden.example.test:\n1. What would it cost?\n2. Do-follow?",
    }
    assert result.notes == []
    traces = {
        getattr(record, "zone", None): getattr(record, "marks", None)
        for record in caplog.records
        if record.getMessage() == "письма: знаки модели в зоне заменены обычными"
    }
    assert traces == {"greeting": ["U+200B"], "ask": ["U+00AD", "U+2011", "U+FEFF"]}


async def test_the_client_keeps_an_invisible_mark_its_template_writes() -> None:
    """Шаблон зоны на персидском пишет разделитель сам: клиент отдаёт разбору текст шаблона, и
    разделитель в ответе модели остаётся, а её пробел нулевой ширины — нет."""
    template = "ما می\u200cخواهیم درباره\u200cی همکاری صحبت کنیم."
    letter = Rendered(
        subject="همکاری",
        zones=(
            Zone("intro", ZoneKind.REWRITE, template),
            Zone("offer", ZoneKind.FIXED, "یک پیشنهاد کوتاه، بدون هزینه."),
        ),
    )
    answer = {"intro": "ما می\u200cخواهیم\u200b درباره\u200cی همکاری گفت\u200cوگو کنیم."}

    result = await _client_answering(letter, answer)

    assert result.zones == {"intro": "ما می\u200cخواهیم درباره\u200cی همکاری گفت\u200cوگو کنیم."}


def test_the_corridor_is_measured_on_the_plain_text() -> None:
    """Модель вернула слова шаблона, только с невидимыми знаками внутри. По сырому тексту такое
    письмо — без единого нового слова — легло бы в коридор отличия: знаки режут слова надвое, у
    письма без пробелов тоже. По обычному тексту отличие ноль, и в коридоре письма нет."""
    letter = Rendered(
        subject="Frage",
        zones=(
            Zone("intro", ZoneKind.REWRITE, "先日はお問い合わせいただきありがとうございました。"),
            Zone("ask", ZoneKind.REWRITE, "Подскажите, пожалуйста, кто отвечает за продвижение?"),
            Zone(
                "offer",
                ZoneKind.FIXED,
                "Ein kurzer Vorschlag für Ihre Seite, ohne Verpflichtung und ohne Kosten für Sie.",
            ),
        ),
    )
    raw = {
        "intro": "先日は\u200bお問い合わせ\u2060いただきありがとうございました。",
        "ask": "Под\u00adскажите, пожа\u200dлуйста, кто отвечает за продвижение?",
    }
    plain = parse_zones(json.dumps(raw), expected=set(raw))

    assert 0.15 <= difference(letter.body, assemble(letter, raw).body) <= 0.25
    assert selection.select(letter, raw, seed=1) is not None
    assert plain == {zone.name: zone.text for zone in letter.rewritable()}
    assert difference(letter.body, assemble(letter, plain).body) == 0.0
    assert selection.select(letter, plain, seed=1) is None


# --- лицо говорящего ------------------------------------------------------------------------------


def test_the_prompt_keeps_the_speaker_as_the_zone_has_it() -> None:
    """Правило держит смысл: лицо и число говорящего — как в зоне, для всех трёх случаев, без
    примеров одного языка — ни слов-образцов в кавычках, ни местоимений."""
    [rule] = [line for line in SYSTEM.splitlines() if "speaker" in line.lower()]
    lowered = rule.lower()

    assert all(word in lowered for word in ("person", "number", "plural", "singular"))
    assert "impersonal" in lowered
    assert "any language" in lowered
    assert not re.search(r"[\"'«»“”„]", rule)
    assert not re.search(r"\b(?:we|us|our|i|me|my)\b", lowered)


# --- каждый путь сборки — через тот же разбор ----------------------------------------------------

#: Слово, в которое модель вставляет знак, — от четырёх букв.
_WORD = re.compile(r"[^\W\d_]{4,}")


def _marked(text: str) -> str:
    """Тот же текст в знаках модели: внутри слов — невидимые знаки по кругу, дефисы —
    неразрывные, в начале — метка порядка байтов. Буквы и слова — те же."""
    marks = itertools.cycle(INVISIBLE)
    inside = _WORD.sub(lambda found: found[0][:3] + next(marks) + found[0][3:], text)
    return "\ufeff" + inside.replace("-", "\u2011")


def _model(
    answer: Callable[[dict[str, str]], dict[str, str]], seen: list[dict[str, str]]
) -> httpx.AsyncClient:
    """HTTP-клиент с подставной моделью: зоны запроса — в `seen`, ответ — `answer(зоны)`."""

    def reply(request: httpx.Request) -> httpx.Response:
        asked = json.loads(request.content)["messages"][1]["content"]
        zones = json.loads(asked.split("Zones to rewrite:\n", 1)[1])
        seen.append(zones)
        content = json.dumps(answer(zones), ensure_ascii=False)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}], "usage": {"total_tokens": 41}},
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(reply))


def _client(http: httpx.AsyncClient) -> RewriteClient:
    return RewriteClient(http, model="gpt-5", api_key=SENT)


async def _client_answering(letter: Rendered, answer: dict[str, str]) -> RewriteResult:
    seen: list[dict[str, str]] = []
    async with _model(lambda _zones: answer, seen) as http:
        return await _client(http).rewrite(letter, ABOUT)


def _same_words(zones: dict[str, str]) -> dict[str, str]:
    """Модель «переписала» зоны теми же словами, но в своих знаках."""
    return {name: _marked(text) for name, text in zones.items()}


def _plain_and_whole(body: str | None, seen: list[dict[str, str]]) -> None:
    """В тексте письма знаков модели нет, и каждая зона, ушедшая модели, стоит в нём слово в
    слово: отличие ноль — по обычному тексту; по сырому оно вышло бы ненулевым."""
    assert body is not None
    assert not set(MARKS) & set(body)
    for zones in seen:
        for text in zones.values():
            assert text in body
    assert any(difference(text, _marked(text)) > 0 for zones in seen for text in zones.values())


async def _letter_of(session: AsyncSession, stage: Stage) -> MessageModel:
    rows = await session.scalars(
        select(MessageModel)
        .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
        .where(CampaignModel.stage == stage)
    )
    [letter] = rows.all()
    return letter


async def test_donor_letter_is_plain_and_measured_plain(
    session: AsyncSession, filled_legal: None
) -> None:
    await make_donor(session, "garden.example.test", email="info@garden.example.test")
    seen: list[dict[str, str]] = []

    async with _model(_same_words, seen) as http:
        builder = QueueBuilder(session, _client(http))
        await builder.build(BuildRequest(campaign_name="Знаки", niche=("raised garden beds",)))

    letter = await _letter_of(session, Stage.DONORS)
    _plain_and_whole(letter.body, seen)
    assert letter.uniqueness_pct == 0.0


async def test_advertiser_offer_is_plain_and_measured_plain(
    session: AsyncSession, filled_legal: None
) -> None:
    await priced_donor(session)
    await make_advertiser(session, "brand.example.test")
    seen: list[dict[str, str]] = []

    async with _model(_same_words, seen) as http:
        builder = QueueBuilder(session, _client(http))
        await builder.build(BuildRequest(campaign_name="Знаки", stage=Stage.ADVERTISERS))

    letter = await _letter_of(session, Stage.ADVERTISERS)
    _plain_and_whole(letter.body, seen)
    assert letter.uniqueness_pct == 0.0


async def test_contact_form_letter_is_plain_and_measured_plain(
    session: AsyncSession, filled_legal: None
) -> None:
    donor_id = await _form_donor(session)
    await _accepted_in_run(session, donor_id, "DE")
    seen: list[dict[str, str]] = []

    async with _model(_same_words, seen) as http:
        client = _client(http)
        letter = await form_letter.build(session, donor_id, rewriter=client)

    _plain_and_whole(letter.body, seen)
    assert letter.uniqueness_pct == 0.0


def _whole_zones(plan: set[str]) -> Callable[[dict[str, str]], dict[str, str]]:
    """Модель переписала зоны плана целиком — новые слова со своими метками, первые два через
    дефис, — и в своих знаках. Обычными знаками это ответ `w.ZoneRewriter` с долей 1."""

    def answer(zones: dict[str, str]) -> dict[str, str]:
        written: dict[str, str] = {}
        for name in zones.keys() & plan:
            fresh = [f"{name}new{index}" for index in range(len(zones[name].split(" ")))]
            written[name] = _marked(f"{fresh[0]}-{' '.join(fresh[1:])}")
        return written

    return answer


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    return await w.world(session, monkeypatch)


async def test_sales_letter_and_followups_are_plain_and_land_in_the_corridor_plain(
    session: AsyncSession, world: w.World
) -> None:
    """Продажи: первое письмо и добивки (общий вызов) — тот же разбор. Обычными знаками
    вступление целиком — 20%, пары зон добивок — по 20%; по сырому тексту новые слова резались
    бы надвое, и ни вступление, ни пары в коридор не легли бы."""
    await _chain(session, world)
    await w.lead(session, world.hypothesis_id, "jane@acme.example.test")
    plan = {"intro"} | {f"step{step}_{zone}" for step in (2, 3) for zone in ZONES}
    seen: list[dict[str, str]] = []

    async with _model(_whole_zones(plan), seen) as http:
        client = _client(http)
        await queue.build(session, client, hypothesis_id=world.hypothesis_id, limit=10)

    assert len(seen) == 2  # первое письмо и общий вызов добивок — без повторов
    sent = await session.scalar(select(MessageModel))
    assert sent is not None
    assert sent.body is not None
    assert not set(MARKS) & set(sent.body)
    assert "intronew0-intronew1 intronew2" in sent.body
    assert sent.uniqueness_pct == pytest.approx(0.20)
    session.expire_all()
    link = await session.scalar(select(SalesThreadModel))
    assert link is not None
    assert link.followups is not None
    for step in ("2", "3"):
        kept = link.followups["steps"][step]
        assert not set(MARKS) & set(kept["body"])
        assert kept["difference"] == pytest.approx(0.2)
        assert frozenset(kept["zones"]) in PAIRS
