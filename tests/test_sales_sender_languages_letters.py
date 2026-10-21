"""Набор «Отправителя» на язык письма — во всех местах, где отправитель продаж попадает в письмо.

Находка стенда 21.10.2026: английское письмо уходило с русскими подписью, физическим адресом и
именем в From. Язык письма выбирает набор везде одним правилом (`Sender.in_language`): язык
цепочки диалога (`sales_threads.language`), а до диалога — язык лида или шаблона. Здесь —
первое письмо при сборке, From и добивки при отправке, ответ лиду, черновик агента,
«Показать письмо» на «Цепочке писем», сверка перед отправкой и автосборка, экран очереди
(добивки впереди) и страница отписки.

Мир — подключённые продажи (`tests/test_sales_send_world.py`): общий набор — по-русски, у писем на
английском — свой. Транспорт подставной, модели нет (подделки переписывания и ситуации письма).
Лиды — из разных ниш, адреса и имена выдуманы, домены — `*.example.test`.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from backend.api.unsubscribe import routes as unsubscribe_routes
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import ContactSource, MessageStatus, ReplyKind, Stage
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.letters import followups, unsubscribe
from backend.features.letters.answers import answer_reply
from backend.features.letters.sending import NotReadyError, Sending
from backend.features.letters.unsubscribe import Label
from backend.features.sales import autobuild, chain, chain_text, followup_texts, queue, sender
from backend.features.sales.agent import brief
from backend.features.sales.chain_text import ChainError
from backend.features.sales.models import (
    SETTINGS_ROW,
    LeadSource,
    LeadStatus,
    SalesHypothesisModel,
    SalesLeadModel,
    SalesSettingsModel,
    SalesThreadModel,
)
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.test_sales_agent_situation import Plug, llm, talk
from tests.test_sales_chain_api import FIRST, PREVIEW, connected, seller
from tests.test_sales_send import FIRST_DUE, SECOND_DUE, _seen, _transports
from tests.test_unsubscribe_page_voice import CLIENT, SALES_FOOTER, SECRET, _link, _sales_letter

__all__ = ["connected", "llm", "seller"]  # фикстуры — их видит pytest

#: Общий набор — по-русски.
RU_NAME = "Мира Тестова"
RU_SIGNATURE = "Мира Тестова\nвыдуманное агентство"
RU_ADDRESS = "Выдуманная ул., 7, Тестоград"
COMMON_BLOCK = f"\n\n{RU_SIGNATURE}\n\n{RU_ADDRESS}"
CALL = "https://call.example.test/slot"
#: Набор писем на английском — целиком.
EN_NAME = "Leo Sample"
EN_POSITION = "Account lead"
EN_SIGNATURE = "Leo Sample\nSample Studio Ltd"
EN_ADDRESS = "12 Sample Road, Testville 0000"
EN_SET = {
    "sender_name": EN_NAME,
    "sender_position": EN_POSITION,
    "signature": EN_SIGNATURE,
    "physical_address": EN_ADDRESS,
}
EN_BLOCK = f"\n\n{EN_SIGNATURE}\n\n{EN_ADDRESS}"
#: Свой набор писем на русском — иной, чем общий: так видно, какой из них подписал письмо.
RU_SET = {
    "sender_name": "Мира Примерова",
    "signature": "Мира Примерова\nвыдуманное агентство, отдел СНГ",
    "physical_address": "Примерная ул., 3, Тестоград",
}

#: Лиды из разных ниш: стоматология, велосипедная мастерская, мебель.
DENTAL = "ann@smile-clinic.example.test"
BIKES = "ivan@bike-repair.example.test"
FURNITURE = "tom@oak-tables.example.test"


async def _sender(session: AsyncSession, sets: dict[str, Any] | None = None) -> None:
    """Общий набор по-русски и ссылка на созвон; наборы языков — как переданы (`None` — не
    трогать)."""
    values: dict[str, Any] = {
        "sender_name": RU_NAME,
        "sender_position": "менеджер проектов",
        "signature": RU_SIGNATURE,
        "physical_address": RU_ADDRESS,
        "call_link": CALL,
    }
    if sets is not None:
        values["languages"] = sets
    await sender.save(session, values, author="тест", author_id=None)


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    """Подключённые продажи с общим набором по-русски и без наборов языков."""
    found = await w.world(session, monkeypatch)
    await _sender(session)
    return found


async def _build(session: AsyncSession, world: w.World) -> queue.QueueReport:
    return await queue.build(
        session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=10
    )


async def _first(session: AsyncSession, email: str) -> MessageModel:
    """Первое письмо лида — по явной связи диалога."""
    found = await session.scalar(
        select(MessageModel)
        .join(SalesThreadModel, SalesThreadModel.thread_id == MessageModel.thread_id)
        .join(SalesLeadModel, SalesLeadModel.id == SalesThreadModel.lead_id)
        .where(SalesLeadModel.email == email, MessageModel.step == 0)
    )
    assert found is not None
    return found


# --- первое письмо, From и добивки --------------------------------------------------------------


async def test_english_lead_gets_the_english_set_in_the_letter_followups_and_from(
    session: AsyncSession, world: w.World
) -> None:
    await _sender(session, {"en": EN_SET})
    await w.lead(session, world.hypothesis_id, DENTAL, name="Ann", company="Smile Clinic Example")

    await _build(session, world)
    letter = await _first(session, DENTAL)
    source = _transports()
    await Sending(session, source, now=w.NOW).send(letter.id)
    await followups.send_due(session, transport=source, limit=5, now=FIRST_DUE)
    await followups.send_due(session, transport=source, limit=5, now=SECOND_DUE)

    assert letter.body is not None
    assert letter.body.endswith(EN_BLOCK)
    sent = _seen(source)
    assert [outgoing.from_name for outgoing in sent] == [EN_NAME, EN_NAME, EN_NAME]
    assert [outgoing.body.endswith(EN_BLOCK) for outgoing in sent] == [True, True, True]
    assert not any(
        RU_SIGNATURE in outgoing.body or RU_ADDRESS in outgoing.body for outgoing in sent
    )


@pytest.mark.parametrize(
    ("sets", "name", "block"),
    [
        ({"en": EN_SET}, RU_NAME, COMMON_BLOCK),
        (
            {"en": EN_SET, "ru": RU_SET},
            RU_SET["sender_name"],
            f"\n\n{RU_SET['signature']}\n\n{RU_SET['physical_address']}",
        ),
    ],
    ids=["common", "own-russian-set"],
)
async def test_russian_lead_gets_the_common_set_or_its_own_when_given(
    session: AsyncSession, world: w.World, sets: dict[str, Any], name: str, block: str
) -> None:
    await _sender(session, sets)
    await w.lead(session, world.hypothesis_id, BIKES, language="ru", name="Иван", company="Вело")

    await _build(session, world)
    letter = await _first(session, BIKES)
    source = _transports()
    await Sending(session, source, now=w.NOW).send(letter.id)
    await followups.send_due(session, transport=source, limit=5, now=FIRST_DUE)

    first, followup = _seen(source)
    assert (first.from_name, followup.from_name) == (name, name)
    assert (first.body.endswith(block), followup.body.endswith(block)) == (True, True)
    assert EN_SIGNATURE not in first.body + followup.body


async def test_english_set_with_only_a_signature_takes_the_common_name_and_address(
    session: AsyncSession, world: w.World
) -> None:
    """Запас по полю: в наборе en задана только подпись — адрес и имя в From общие."""
    await _sender(session, {"en": {"signature": EN_SIGNATURE}})
    await w.lead(session, world.hypothesis_id, FURNITURE, name="Tom", company="Oak Tables Example")

    await _build(session, world)
    letter = await _first(session, FURNITURE)
    source = _transports()
    await Sending(session, source, now=w.NOW).send(letter.id)
    await followups.send_due(session, transport=source, limit=5, now=FIRST_DUE)

    block = f"\n\n{EN_SIGNATURE}\n\n{RU_ADDRESS}"
    first, followup = _seen(source)
    assert (first.from_name, followup.from_name) == (RU_NAME, RU_NAME)
    assert (first.body.endswith(block), followup.body.endswith(block)) == (True, True)


# --- сверка перед отправкой и автосборка -------------------------------------------------------


async def test_letter_signed_before_the_english_set_is_held_and_rebuilt_with_it(
    session: AsyncSession, world: w.World
) -> None:
    """Письмо собрали общим набором, потом задали набор en: сверка отправки его не пускает,
    автосборка видит его к пересборке, а сборка собирает его заново на том же месте — с набором
    en."""
    await w.lead(session, world.hypothesis_id, DENTAL, name="Ann", company="Smile Clinic Example")
    await _build(session, world)
    letter = await _first(session, DENTAL)
    assert letter.body is not None
    assert letter.body.endswith(COMMON_BLOCK)
    await _sender(session, {"en": EN_SET})

    with pytest.raises(NotReadyError, match="письмо собрано с прежними: соберите очередь заново"):
        await Sending(session, _transports(), now=w.NOW).send(letter.id)
    stale = await autobuild.queued(session)
    report = await _build(session, world)
    await session.refresh(letter)
    source = _transports()
    await Sending(session, source, now=w.NOW).send(letter.id)

    assert (stale.redo, stale.ready) == (frozenset({letter.id}), frozenset())
    assert (report.prepared, report.refreshed) == (0, 1)
    [outgoing] = _seen(source)
    assert (outgoing.from_name, outgoing.body.endswith(EN_BLOCK)) == (EN_NAME, True)


async def test_followup_repeating_the_english_signature_is_named_ahead_and_not_sent(
    session: AsyncSession, world: w.World
) -> None:
    """Шаблон добивки записан, когда набор en ещё не повторял его текст. Экран очереди и
    отправка судят добивку набором языка её цепочки: «не уйдёт» — и цепочка остановлена."""
    step = chain_text.step_template(
        step=2, language="en", body=f"[reminder] fixed\nA made-up reminder.\n{EN_SIGNATURE}"
    )
    await chain.save(session, step, hypothesis_id=None, author="тест", author_id=None)
    await _sender(session, {"en": EN_SET})
    lead = await w.lead(session, world.hypothesis_id, DENTAL, name="Ann", company="Smile Clinic")
    await _build(session, world)
    letter = await _first(session, DENTAL)
    link = await session.get(SalesThreadModel, letter.thread_id)
    assert link is not None

    [second, _] = await followup_texts.ahead(session, link, lead, "smile-clinic.example.test")
    source = _transports()
    await Sending(session, source, now=w.NOW).send(letter.id)
    report = await followups.send_due(session, transport=source, limit=5, now=FIRST_DUE)

    assert second.body is None
    assert "подпись из настроек отправителя стоит и в тексте письма" in second.words
    assert (report.sent, report.stopped) == (0, 1)
    assert len(_seen(source)) == 1


# --- ответ лиду и черновик агента ----------------------------------------------------------------


async def test_answer_to_an_english_lead_is_signed_with_the_english_set(
    session: AsyncSession, world: w.World
) -> None:
    await _sender(session, {"en": EN_SET})
    await w.lead(session, world.hypothesis_id, DENTAL, name="Ann", company="Smile Clinic Example")
    await _build(session, world)
    letter = await _first(session, DENTAL)
    source = _transports()
    await Sending(session, source, now=w.NOW).send(letter.id)
    reply = ReplyModel(
        thread_id=letter.thread_id,
        message_id=letter.id,
        kind=ReplyKind.HUMAN,
        raw_body="What does the audit include?",
        from_email=DENTAL,
        subject=f"Re: {letter.subject}",
        inbound_message_id="<in-1@smile-clinic.example.test>",
        created_at=w.NOW + timedelta(hours=1),
    )
    session.add(reply)
    await session.flush()

    await answer_reply(
        session,
        Sending(session, source, now=w.NOW),
        thread_id=letter.thread_id or 0,
        reply_id=reply.id,
        body="The audit covers the pages that bring you search traffic.",
        author_id=None,
    )

    _, answer = _seen(source)
    assert (answer.from_name, answer.body.endswith(EN_BLOCK)) == (EN_NAME, True)


@pytest.mark.parametrize(
    ("email", "language", "name", "persona"),
    [
        (DENTAL, "en", EN_NAME, f"[persona] {EN_NAME} — {EN_POSITION}"),
        (BIKES, "ru", RU_NAME, f"[persona] {RU_NAME} — менеджер проектов"),
    ],
    ids=["en", "ru"],
)
async def test_agent_draft_is_signed_with_the_set_of_the_thread_language(
    session: AsyncSession,
    world: w.World,
    llm: Plug,
    email: str,
    language: str,
    name: str,
    persona: str,
) -> None:
    """Черновик ответа лиду подписан персоной набора языка переписки — тем же набором отправка
    допишет подпись и поставит имя в From. Ссылки — общие."""
    await _sender(session, {"en": EN_SET})
    await w.lead(session, world.hypothesis_id, email, language=language, name="Ann")
    await _build(session, world)
    letter = await _first(session, email)
    llm(situation=[{"situation": "asks_price", "confidence": 0.92}])
    asked = talk((True, "A made-up first letter."), (False, "How much is the audit?"))

    found = await brief.brief(session, replace(asked, thread_id=letter.thread_id or 0))

    assert found.sign_as == name
    assert persona in found.facts
    assert f"[cta call] {CALL}" in found.facts


# --- «Показать письмо» на «Цепочке писем» -------------------------------------------------------


async def test_show_letter_is_signed_with_the_set_of_the_step_language(
    client: AsyncClient,
    session: AsyncSession,
    seller: tuple[UserModel, dict[str, str]],
    connected: None,
) -> None:
    _, headers = seller
    await _sender(session, {"en": EN_SET})
    await session.commit()

    english = (await client.post(PREVIEW, json=FIRST, headers=headers)).json()
    russian = (await client.post(PREVIEW, json=FIRST | {"language": "ru"}, headers=headers)).json()

    assert (english["sender_name"], english["signature"], english["address"]) == (
        EN_NAME,
        EN_SIGNATURE,
        EN_ADDRESS,
    )
    assert (russian["sender_name"], russian["signature"], russian["address"]) == (
        RU_NAME,
        RU_SIGNATURE,
        RU_ADDRESS,
    )
    assert english["missing"] == russian["missing"] == []


async def test_show_letter_names_what_sending_lacks_by_the_common_set(
    client: AsyncClient,
    session: AsyncSession,
    seller: tuple[UserModel, dict[str, str]],
    connected: None,
) -> None:
    """Набор en с адресом, а в общем адреса нет: письмо на английском показано с адресом набора,
    но «чего не хватает» — тем же правилом, что отказ отправки: по общему набору."""
    _, headers = seller
    common = {"sender_name": RU_NAME, "signature": RU_SIGNATURE}
    await sender.save(session, common | {"languages": {"en": EN_SET}}, author="т", author_id=None)
    await session.commit()

    shown = (await client.post(PREVIEW, json=FIRST, headers=headers)).json()

    assert (shown["address"], shown["missing"]) == (EN_ADDRESS, ["не задан физический адрес"])


async def test_english_template_repeating_the_english_signature_is_refused(
    session: AsyncSession,
) -> None:
    """Запрет повтора подписи в шаблоне — набором языка шаблона: письмо на английском допишет
    набор en, на русском — общий набор, и подпись набора en в нём не повторится."""
    await _sender(session, {"en": EN_SET})
    body = f"[reminder] fixed\nA made-up reminder.\n{EN_SIGNATURE}"
    english = chain_text.step_template(step=2, language="en", body=body)
    russian = chain_text.step_template(step=2, language="ru", body=body)

    with pytest.raises(ChainError, match="в тексте — подпись из настроек отправителя"):
        await chain.save(session, english, hypothesis_id=None, author="тест", author_id=None)
    saved = await chain.save(session, russian, hypothesis_id=None, author="тест", author_id=None)

    assert saved.language == "ru"


# --- страница отписки ----------------------------------------------------------------------------


@pytest.fixture
def page(monkeypatch: pytest.MonkeyPatch) -> None:
    """Страница отписки отвечает на метку, подписанную этим секретом; счётчик частоты пуст."""
    monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", SECRET)
    unsubscribe_routes._throttle.clear(CLIENT)


@pytest.mark.parametrize(
    ("language", "footer"),
    [
        (
            "en",
            "<footer>Maria Example · Made-up Agency Ltd, 9 Sample Road, Testville 4171</footer>",
        ),
        ("ru", SALES_FOOTER),
    ],
    ids=["en", "ru"],
)
async def test_unsubscribe_page_is_signed_with_the_set_of_the_letter_language(
    client: AsyncClient, session: AsyncSession, page: None, language: str, footer: str
) -> None:
    letter, _ = await _sales_letter(session, language=language)
    settings = await session.get(SalesSettingsModel, SETTINGS_ROW)
    assert settings is not None
    settings.languages = {
        "en": {
            "sender_name": "Maria Example",
            "physical_address": "Made-up Agency Ltd\n9 Sample Road\nTestville 4171",
        }
    }
    await session.commit()
    await session.refresh(settings)  # время правки ставит база — сессия у теста и страницы одна

    response = await client.get(_link(letter.domain_id, letter.id))

    assert response.status_code == 200
    assert footer in response.text


#: Метки отписки из уже ушедших писем — литералами, посчитанными кодом `origin/main` до этой правки
#: (сайт №917001, письмо продаж №917002, секрет — `SECRET` теста страницы), а не тем же кодом, что их
#: проверяет: правка — в том, чьим именем страница подписана (`api/unsubscribe/signer.py`), а подпись
#: метки (`letters/unsubscribe.py`) не тронута.
SENT_LETTER_LABEL = "u917001m917002.dcce09eb8c"
SENT_DONOR_LABEL = "u917001.89826b1e4d"


def test_labels_signed_by_the_previous_code_are_read_as_before() -> None:
    assert unsubscribe.parse(SENT_LETTER_LABEL, secret=SECRET) == Label(917001, 917002)
    assert unsubscribe.parse(SENT_DONOR_LABEL, secret=SECRET) == Label(917001, None)
    assert unsubscribe.label_for(917001, letter_id=917002, secret=SECRET) == SENT_LETTER_LABEL
    assert unsubscribe.label_for(917001, secret=SECRET) == SENT_DONOR_LABEL


async def _sent_letter(session: AsyncSession) -> str:
    """Письмо продаж лиду на английском, ушедшее до правки: номера сайта и письма — те, что в
    метке `SENT_LETTER_LABEL`; у «Отправителя» уже есть набор en. Адрес лида — в ответе."""
    domain = DomainModel(id=917001, host="lingua-school.example.test")
    hypothesis = SalesHypothesisModel(name="гипотеза языковых школ")
    campaign = CampaignModel(stage=Stage.SALES, name="продажи языковым школам")
    session.add_all([domain, hypothesis, campaign])
    await session.flush()
    # Адрес лида — и адрес сайта: по нему нажатие на странице узнаёт человека, как в
    # `tests/test_unsubscribe_page_voice.py` (продажи на этой странице не подключены).
    email = "kim@lingua-school.example.test"
    session.add(ContactModel(domain_id=domain.id, email=email, source=ContactSource.MANUAL))
    lead = SalesLeadModel(
        hypothesis_id=hypothesis.id,
        domain_id=domain.id,
        email=email,
        name="Kim",
        language="en",
        source=LeadSource.IMPORT,
        status=LeadStatus.READY,
    )
    thread = ThreadModel(domain_id=domain.id, campaign_id=campaign.id)
    session.add_all([lead, thread])
    await session.flush()
    session.add(
        MessageModel(
            id=917002,
            campaign_id=campaign.id,
            thread_id=thread.id,
            domain_id=domain.id,
            step=0,
            status=MessageStatus.SENT,
            subject="Who handles SEO for lingua-school.example.test?",
            idempotency_key="sales:lingua-school.example.test:kim@lingua-school.example.test:0",
        )
    )
    session.add_all(
        [
            SalesThreadModel(
                thread_id=thread.id,
                lead_id=lead.id,
                language="en",
                chain_version="chain-made-up-42",
            ),
            SalesSettingsModel(
                id=SETTINGS_ROW,
                sender_name=RU_NAME,
                physical_address=RU_ADDRESS,
                languages={"en": {"sender_name": EN_NAME, "physical_address": EN_ADDRESS}},
            ),
        ]
    )
    await session.commit()
    return lead.email


async def test_link_sent_before_the_change_opens_the_page_of_the_letter_language_and_unsubscribes(
    client: AsyncClient, session: AsyncSession, page: None
) -> None:
    """Ссылка из уже ушедшего письма: страница открывается по той же метке, подписана именем и
    адресом набора языка письма, а нажатие закрывает адрес лида — как раньше."""
    lead = await _sent_letter(session)

    shown = await client.get(f"/api/unsubscribe/{SENT_LETTER_LABEL}")
    pressed = await client.post(f"/api/unsubscribe/{SENT_LETTER_LABEL}")

    assert shown.status_code == 200
    assert f"<footer>{EN_NAME} · {EN_ADDRESS}</footer>" in shown.text
    assert '<html lang="en">' in shown.text
    assert RU_NAME not in shown.text
    assert pressed.status_code == 200
    rows = (await session.execute(select(SuppressionModel))).scalars().all()
    assert [(row.email, row.domain_id, row.stage) for row in rows] == [(lead, None, None)]
