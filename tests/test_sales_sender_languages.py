"""«Отправитель» продаж — свой набор на язык письма: запись, чтение, запас по полю, готовность,
пределы и отказы, журнал, экран.

Находка стенда 21.10.2026: английское письмо уходило с русскими подписью, физическим адресом и
именем в From — у «Отправителя» был один набор полей на все языки. Набор — имя, должность, подпись
и физический адрес на каждом языке цепочек (`chain_text.LANGUAGES`); пустое поле набора — общее.
Письма с наборами (сборка, добивки, From, ответы, агент, «Показать письмо», отписка) —
`tests/test_sales_sender_languages_letters.py`. Имена и адреса выдуманы, ссылки — `*.example.test`.
"""

from __future__ import annotations

from typing import Any

import pytest
from backend.features.core.domain import AuditAction
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.sales import chain_text, connection, sender
from backend.features.sales.chain_text import LANGUAGES
from backend.features.sales.models import SETTINGS_ROW, SalesSettingsModel
from backend.features.sales.sender import LANGUAGE_FIELDS, SenderNotReadyError, SenderSettingsError
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_kb_api import SENDER, connected, seller
from tests.test_sales_send_world import connect

__all__ = ["connected", "seller"]  # фикстуры — их видит pytest

#: Общий набор — по-русски: так его заполняют, пока писем на других языках нет.
COMMON = {
    "sender_name": "Мира Тестова",
    "sender_position": "менеджер проектов",
    "signature": "Мира Тестова\nвыдуманное агентство",
    "website": "https://agency.example.test",
    "telegram": "@agency_example",
    "physical_address": "Выдуманная ул., 7\nТестоград, 000000",
    "call_link": "https://call.example.test/mira",
}
#: Набор писем на английском — целиком.
EN = {
    "sender_name": "Mira Testova",
    "sender_position": "Project manager",
    "signature": "Mira Testova\nMade-up Test Agency",
    "physical_address": "7 Made-up Street\nTestville 0000",
}
EMPTY = dict.fromkeys(LANGUAGE_FIELDS)


async def _save(session: AsyncSession, values: dict[str, Any]) -> sender.Sender:
    return await sender.save(session, values, author="т", author_id=None)


async def _journal(session: AsyncSession) -> list[dict[str, Any] | None]:
    rows = await session.scalars(
        select(AuditLogModel)
        .where(AuditLogModel.action == AuditAction.SALES_KB_CHANGED)
        .order_by(AuditLogModel.id)
    )
    return [row.details for row in rows]


# --- набор и запас по полю ---------------------------------------------------------------------


async def test_english_set_is_kept_only_as_given_and_read_back(session: AsyncSession) -> None:
    saved = await _save(session, COMMON | {"languages": {"en": EN}})

    assert saved.languages == {"ru": EMPTY, "en": EN}
    assert (await sender.read(session)).languages == saved.languages
    row = await session.get(SalesSettingsModel, SETTINGS_ROW)
    assert row is not None
    # В колонке — только заданное: пустой русский набор не хранится.
    assert row.languages == {"en": EN}
    # Общий набор цел: экран показывает его как прежде.
    assert saved.values == COMMON


async def test_letter_in_english_is_signed_by_the_english_set_and_links_stay_common(
    session: AsyncSession,
) -> None:
    await _save(session, COMMON | {"languages": {"en": EN}})

    found = (await sender.read(session)).in_language("en")

    assert found.values == COMMON | EN
    assert (found.values["website"], found.values["telegram"]) == (
        COMMON["website"],
        COMMON["telegram"],
    )


async def test_empty_field_of_the_set_falls_back_to_the_common_one(session: AsyncSession) -> None:
    """В наборе en задана только подпись: имя (From), должность и адрес — общие."""
    await _save(session, COMMON | {"languages": {"en": {"signature": EN["signature"]}}})

    found = (await sender.read(session)).in_language("en")

    assert found.values == COMMON | {"signature": EN["signature"]}


@pytest.mark.parametrize("language", ["ru", None, "", "uk", "de-AT"])
async def test_letter_without_its_own_set_is_signed_by_the_common_set(
    session: AsyncSession, language: str | None
) -> None:
    await _save(session, COMMON | {"languages": {"en": EN}})

    assert (await sender.read(session)).in_language(language).values == COMMON


@pytest.mark.parametrize("language", ["en", "en-US", "EN-gb", " en "])
async def test_language_of_the_letter_is_taken_by_its_base_code(
    session: AsyncSession, language: str
) -> None:
    await _save(session, COMMON | {"languages": {"en": EN}})

    found = (await sender.read(session)).in_language(language)

    assert found.values["sender_name"] == EN["sender_name"]


async def test_russian_set_signs_russian_letters_when_it_is_given(session: AsyncSession) -> None:
    """Общий набор — по-английски (так бывает у агентства, что пишет в основном за рубеж), а у
    писем на русском — свой; язык без набора — общий."""
    english_common = COMMON | EN
    russian = {"sender_name": "Мира Тестова", "signature": "Мира Тестова\nвыдуманное агентство"}
    await _save(session, english_common | {"languages": {"ru": russian}})
    found = await sender.read(session)

    assert found.in_language("ru").values == english_common | russian
    assert found.in_language("en").values == english_common


async def test_choosing_a_set_twice_starts_from_the_common_set(session: AsyncSession) -> None:
    """Выбор набора — поверх общего, а не поверх уже выбранного: письмо не берёт поле чужого
    языка."""
    russian = {"signature": "Мира Тестова\nвыдуманное агентство, Москва"}
    await _save(session, COMMON | {"languages": {"en": EN, "ru": russian}})

    found = (await sender.read(session)).in_language("en").in_language("ru")

    assert found.values == COMMON | russian


# --- готовность — по общему набору -------------------------------------------------------------


async def test_set_of_a_language_does_not_make_sales_ready(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Общий набор без адреса, а набор en полный: продажи не готовы — как без набора, и у
    отправителя письма на английском те же слова."""
    connect(monkeypatch)
    without_address = COMMON | {"physical_address": None}
    await _save(session, without_address | {"languages": {"en": EN}})

    found = await sender.read(session)

    assert found.missing == ["не задан физический адрес"]
    assert found.in_language("en").missing == ["не задан физический адрес"]
    assert await connection.missing(session) == [
        "не задан физический адрес — заполните на экране «Продажи» → «Отправитель»"
    ]
    with pytest.raises(SenderNotReadyError, match="не задан физический адрес"):
        await sender.check_ready(session)


async def test_empty_sets_do_not_hold_sending(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    connect(monkeypatch)
    await _save(session, COMMON | {"languages": {"en": {}, "ru": None}})

    assert await connection.missing(session) == []
    assert (await sender.check_ready(session)).languages == {"ru": EMPTY, "en": EMPTY}


# --- пределы, вид и отказы — как у общих полей ---------------------------------------------------


async def test_set_fields_are_shaped_as_the_common_ones(session: AsyncSession) -> None:
    saved = await _save(
        session,
        {
            "languages": {
                "en": {
                    "sender_name": "  Mira   Testova ",
                    "sender_position": "",
                    "signature": "Mira Testova  \r\nMade-up Test Agency\r\n",
                    "physical_address": " \n\t ",
                }
            }
        },
    )

    assert saved.languages["en"] == {
        "sender_name": "Mira Testova",
        "sender_position": None,
        "signature": "Mira Testova\nMade-up Test Agency",
        "physical_address": None,
    }


@pytest.mark.parametrize(
    ("sets", "words"),
    [
        (
            {"en": {"signature": "s" * 1001}},
            "Письма на английском — подпись: длиннее 1000 знаков (1001)",
        ),
        (
            {"en": {"sender_name": "n" * 129}},
            "Письма на английском — имя отправителя: длиннее 128 знаков (129)",
        ),
        (
            {"ru": {"physical_address": "а" * 501}},
            "Письма на русском — физический адрес: длиннее 500 знаков (501)",
        ),
        (
            {"en": {"website": "https://agency.example.test"}},
            "Письма на английском: полей website нет; есть: sender_name, sender_position, "
            "signature, physical_address",
        ),
        ({"uk": {"signature": "Підпис"}}, "наборов на языках uk нет: письма пишутся на ru, en"),
    ],
    ids=["signature", "name", "address", "common-field", "language-without-chain"],
)
async def test_bad_set_is_refused_in_words_and_nothing_is_written(
    session: AsyncSession, sets: dict[str, Any], words: str
) -> None:
    with pytest.raises(SenderSettingsError) as refused:
        await _save(session, COMMON | {"languages": sets})

    assert str(refused.value) == words
    assert await session.get(SalesSettingsModel, SETTINGS_ROW) is None


# --- журнал и запись целиком ----------------------------------------------------------------------


async def test_each_change_of_a_set_is_journaled_once_with_the_old_values(
    session: AsyncSession,
) -> None:
    await _save(session, COMMON | {"languages": {"en": EN}})
    await _save(session, COMMON | {"languages": {"en": EN}})  # то же самое — журнал не пишется
    changed = EN | {"signature": "Mira Testova\nAnother Made-up Agency"}
    await _save(session, COMMON | {"languages": {"en": changed}})

    first, last = await _journal(session)

    assert first is not None
    assert first["поля"] == [*COMMON, *(f"en.{name}" for name in LANGUAGE_FIELDS)]
    assert last == {"поля": ["en.signature"], "было": {"en.signature": EN["signature"]}}


async def test_saving_without_sets_keeps_them(session: AsyncSession) -> None:
    """Консоль и прежний экран пишут только общие поля: наборы остаются как были."""
    await _save(session, COMMON | {"languages": {"en": EN}})

    saved = await _save(session, COMMON | {"physical_address": "Другая выдуманная ул., 9"})

    assert saved.languages["en"] == EN
    assert (await _journal(session))[-1] == {
        "поля": ["physical_address"],
        "было": {"physical_address": COMMON["physical_address"]},
    }


async def test_emptied_sets_leave_the_column_empty(session: AsyncSession) -> None:
    await _save(session, COMMON | {"languages": {"en": EN}})

    saved = await _save(session, COMMON | {"languages": {"en": {}}})

    assert saved.languages == dict.fromkeys(LANGUAGES, EMPTY)
    row = await session.get(SalesSettingsModel, SETTINGS_ROW)
    assert row is not None
    assert row.languages is None


async def test_set_of_a_language_without_chains_is_not_wiped_by_another_change(
    session: AsyncSession,
) -> None:
    """Язык убрали из цепочек: его набор писем не подписывает и на экране не виден, но правка
    других полей не стирает его молча."""
    await _save(session, COMMON)
    row = await session.get(SalesSettingsModel, SETTINGS_ROW)
    assert row is not None
    row.languages = {"pt": {"signature": "Mira Testova\nAgência de teste"}}
    await session.flush()
    await session.refresh(row)  # время правки ставит база — читаем назад сразу

    saved = await _save(session, COMMON | {"languages": {"en": EN}})

    await session.refresh(row)
    assert row.languages == {"pt": {"signature": "Mira Testova\nAgência de teste"}, "en": EN}
    assert set(saved.languages) == set(LANGUAGES)


# --- экран ------------------------------------------------------------------------------------------


async def test_screen_reads_a_block_per_language_titled_in_words(
    client: AsyncClient, seller: tuple[UserModel, dict[str, str]], connected: None
) -> None:
    _, headers = seller

    body = (await client.get(SENDER, headers=headers)).json()

    assert [(card["language"], card["title"]) for card in body["languages"]] == [
        ("ru", "Письма на русском"),
        ("en", "Письма на английском"),
    ]
    assert all(card[name] is None for card in body["languages"] for name in LANGUAGE_FIELDS)
    # Пределы полей набора — общих полей.
    assert {name: body["limits"][name] for name in LANGUAGE_FIELDS} == {
        "sender_name": 128,
        "sender_position": 128,
        "signature": 1000,
        "physical_address": 500,
    }


async def test_screen_saves_the_sets_whole_and_readiness_stays_common(
    client: AsyncClient, seller: tuple[UserModel, dict[str, str]], connected: None
) -> None:
    _, headers = seller
    body = COMMON | {"languages": {"en": {"signature": EN["signature"]}, "ru": {}}}

    saved = await client.post(SENDER, json=body, headers=headers)
    read = (await client.get(SENDER, headers=headers)).json()

    assert saved.status_code == 200, saved.text
    assert read["languages"][1] == {
        "language": "en",
        "title": "Письма на английском",
        **EMPTY,
        "signature": EN["signature"],
    }
    assert (read["sender_name"], read["missing"]) == (COMMON["sender_name"], [])


async def test_screen_refusals_of_a_set_are_words_and_typos_are_refused_by_the_schema(
    client: AsyncClient, seller: tuple[UserModel, dict[str, str]]
) -> None:
    _, headers = seller

    long = await client.post(
        SENDER, json={"languages": {"en": {"signature": "s" * 1001}}}, headers=headers
    )
    alien = await client.post(
        SENDER, json={"languages": {"uk": {"signature": "Підпис"}}}, headers=headers
    )
    typo = await client.post(
        SENDER, json={"languages": {"en": {"signatur": "Mira"}}}, headers=headers
    )

    assert (long.status_code, long.json()["detail"]) == (
        400,
        "Письма на английском — подпись: длиннее 1000 знаков (1001)",
    )
    assert (alien.status_code, alien.json()["detail"]) == (
        400,
        "наборов на языках uk нет: письма пишутся на ru, en",
    )
    assert typo.status_code == 422


# --- подстановки в «Отправителе» — отказ при записи ---------------------------------------------
#
# Поле «Отправителя» уходит в письмо как есть: подстановка `{{…}}` или громкая метка сборки
# («… НЕ ЗАДАНО») в нём не заменится, и сверка письма (`letter.problem`: «подстановка без
# значения») не пропустит ни одного письма — а человек искал бы причину в шаблоне цепочки.

UNFILLED = "подстановки здесь не работают: поле уходит в письмо как есть, напишите текст целиком"


@pytest.mark.parametrize(
    ("values", "words"),
    [
        (
            {"signature": "Мира Тестова\n{{name}}"},
            f"подпись: «{{{{name}}}}» — {UNFILLED}",
        ),
        (
            {"sender_name": "«ИМЯ ОТПРАВИТЕЛЯ НЕ ЗАДАНО»"},
            f"имя отправителя: «ИМЯ ОТПРАВИТЕЛЯ НЕ ЗАДАНО» — {UNFILLED}",
        ),
        (
            {"physical_address": "Выдуманная ул., 7\n«ФИЗИЧЕСКИЙ АДРЕС НЕ ЗАДАН»"},
            f"физический адрес: «ФИЗИЧЕСКИЙ АДРЕС НЕ ЗАДАН» — {UNFILLED}",
        ),
        (
            {"website": "https://agency.example.test/{{site}}"},
            f"сайт: «{{{{site}}}}» — {UNFILLED}",
        ),
        (
            {"languages": {"en": {"signature": "Mira Testova\n{{company}}"}}},
            f"Письма на английском — подпись: «{{{{company}}}}» — {UNFILLED}",
        ),
        (
            {"languages": {"ru": {"sender_position": "{{position}}"}}},
            f"Письма на русском — должность: «{{{{position}}}}» — {UNFILLED}",
        ),
    ],
    ids=["signature", "loud-name", "loud-address", "link", "en-set", "ru-set"],
)
async def test_placeholder_in_any_field_is_refused_in_words_and_nothing_is_written(
    session: AsyncSession, values: dict[str, Any], words: str
) -> None:
    with pytest.raises(SenderSettingsError) as refused:
        await _save(session, COMMON | values)

    assert str(refused.value) == words
    assert await session.get(SalesSettingsModel, SETTINGS_ROW) is None


async def test_placeholder_already_stored_is_read_and_refused_only_when_written(
    session: AsyncSession,
) -> None:
    """Строка с подстановкой, записанная раньше правила, читается как есть — экран её покажет, —
    а запись, которая её несёт, отказывает словами: поправить её — в том же поле."""
    await _save(session, COMMON)
    row = await session.get(SalesSettingsModel, SETTINGS_ROW)
    assert row is not None
    row.signature = "Мира Тестова\n{{name}}"
    row.languages = {"en": {"physical_address": "«ФИЗИЧЕСКИЙ АДРЕС НЕ ЗАДАН»"}}
    await session.flush()
    await session.refresh(row)  # время правки ставит база — читаем назад сразу

    found = await sender.read(session)
    with pytest.raises(SenderSettingsError) as shared:
        await _save(session, found.values | {"website": "https://other.example.test"})
    with pytest.raises(SenderSettingsError) as english:
        await _save(session, COMMON | {"languages": found.languages})

    assert found.values["signature"] == "Мира Тестова\n{{name}}"
    assert found.languages["en"]["physical_address"] == "«ФИЗИЧЕСКИЙ АДРЕС НЕ ЗАДАН»"
    assert str(shared.value) == f"подпись: «{{{{name}}}}» — {UNFILLED}"
    assert str(english.value) == (
        f"Письма на английском — физический адрес: «ФИЗИЧЕСКИЙ АДРЕС НЕ ЗАДАН» — {UNFILLED}"
    )


async def test_screen_reads_the_placeholder_pattern_and_refuses_in_words(
    client: AsyncClient, seller: tuple[UserModel, dict[str, str]]
) -> None:
    """Образец подстановки — сервера: экран проверяет им поле до нажатия, второго правила нет."""
    _, headers = seller

    read = (await client.get(SENDER, headers=headers)).json()
    refused = await client.post(
        SENDER, json={"languages": {"en": {"signature": "{{name}}"}}}, headers=headers
    )

    assert read["unfilled"] == chain_text.UNFILLED.pattern
    assert (refused.status_code, refused.json()["detail"]) == (
        400,
        f"Письма на английском — подпись: «{{{{name}}}}» — {UNFILLED}",
    )
