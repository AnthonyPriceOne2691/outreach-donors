"""Отправитель продаж на настоящей базе — срез 3.1: готовность к отправке, правка, журнал, схема.

Адреса и подписи выдуманы, ссылки — на `*.example.test`. Тест готовности начинает
с пустых настроек: фикстура, заполняющая адрес заранее, спрятала бы ровно тот
случай, ради которого проверка написана (урок прогона писем 21.09).
"""

from __future__ import annotations

from typing import Any

import pytest
from backend.features.core.domain import AuditAction
from backend.features.core.models.access import AuditLogModel
from backend.features.sales import sender
from backend.features.sales.models import SalesSettingsModel
from backend.features.sales.sender import SenderNotReadyError, SenderSettingsError
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

ADDRESS = "Выдуманная ул., 7\nТестоград, 000000"
SIGNATURE = "Ива Тестова\nстудия примеров"
FILLED = {
    "sender_name": "Ива Тестова",
    "sender_position": "менеджер",
    "signature": SIGNATURE,
    "website": "https://studio.example.test",
    "telegram": "@studio_example",
    "physical_address": ADDRESS,
    "call_link": "https://call.example.test/iva",
}


async def _save(
    session: AsyncSession, values: dict[str, Any], *, author: str = "т"
) -> sender.Sender:
    return await sender.save(session, values, author=author, author_id=None)


async def _journal(session: AsyncSession) -> list[tuple[str | None, dict[str, Any] | None]]:
    rows = await session.scalars(
        select(AuditLogModel)
        .where(AuditLogModel.action == AuditAction.SALES_KB_CHANGED)
        .order_by(AuditLogModel.id)
    )
    return [(row.target, row.details) for row in rows]


# --- A3: готовность к отправке --------------------------------------------------------


async def test_a3_no_address_refuses_in_words_even_with_the_donor_address_in_the_environment(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A3 — пример спеки
    """Адрес доноров из окружения у продаж не подставляется: у них свой отправитель."""
    monkeypatch.setattr("backend.config.outreach.POSTAL_ADDRESS", "Донорская ул., 1")
    await _save(session, {"signature": SIGNATURE})

    with pytest.raises(SenderNotReadyError) as refused:
        await sender.check_ready(session)

    assert str(refused.value) == (
        "отправка продаж невозможна: не задан физический адрес — "
        "заполните на экране «Продажи» → «Отправитель»"
    )


async def test_a3_empty_settings_name_both_missing_fields(session: AsyncSession) -> None:
    # A3 — пример спеки
    with pytest.raises(SenderNotReadyError) as refused:
        await sender.check_ready(session)

    assert "не задан физический адрес; не задана подпись" in str(refused.value)
    assert (await sender.read(session)).missing == [
        "не задан физический адрес",
        "не задана подпись",
    ]


async def test_a3_address_of_blanks_is_not_an_address(session: AsyncSession) -> None:
    # A3 — пример спеки
    saved = await _save(session, {"signature": SIGNATURE, "physical_address": " \n\t "})

    assert saved.values["physical_address"] is None
    with pytest.raises(SenderNotReadyError, match="не задан физический адрес"):
        await sender.check_ready(session)


async def test_a3_address_and_signature_are_enough_to_send(session: AsyncSession) -> None:
    # A3 — пример спеки
    await _save(session, {"signature": SIGNATURE, "physical_address": ADDRESS})

    ready = await sender.check_ready(session)

    assert (ready.missing, ready.values["physical_address"]) == ([], ADDRESS)


# --- правка ---------------------------------------------------------------------------


async def test_settings_are_kept_in_one_row_with_the_author_and_read_back(
    session: AsyncSession,
) -> None:
    saved = await _save(session, FILLED, author="admin@ours.example.test")

    assert saved.values == FILLED
    assert (saved.updated_by, saved.updated_at is not None) == ("admin@ours.example.test", True)
    assert (await sender.read(session)).values == FILLED
    assert await session.scalar(select(func.count()).select_from(SalesSettingsModel)) == 1


async def test_values_are_trimmed_single_lines_squashed_and_blank_means_not_set(
    session: AsyncSession,
) -> None:
    saved = await _save(
        session,
        {
            "sender_name": "  Ива   Тестова ",
            "signature": "Ива Тестова  \r\nстудия примеров\r\n",
            "website": "",
            "telegram": "https://t.me/studio_example",
        },
    )

    assert saved.values == dict.fromkeys(sender.FIELDS) | {
        "sender_name": "Ива Тестова",
        "signature": SIGNATURE,
        "telegram": "https://t.me/studio_example",
    }


@pytest.mark.parametrize(
    ("values", "words"),
    [
        (
            {"website": "studio.example.test"},
            "сайт: «studio.example.test» — не ссылка, ждём https://…",
        ),
        (
            {"call_link": "ftp://call.example.test"},
            "ссылка на созвон: «ftp://call.example.test» — не ссылка",
        ),
        (
            {"website": "https://studio .example.test"},
            "сайт: «https://studio .example.test» — не ссылка",
        ),
        (
            {"telegram": "studio_example"},
            "Telegram для лидов: «studio_example» — ждём @имя или ссылку",
        ),
        ({"telegram": "@abc"}, "Telegram для лидов: «@abc» — ждём @имя"),
        ({"telegram": "https://t.me/"}, "Telegram для лидов: «https://t.me/» — ждём"),
        ({"telegram": "https://evil.example.test/studio"}, "Telegram для лидов: «https://evil"),
        ({"sender_name": "я" * 129}, "имя отправителя: длиннее 128 знаков (129)"),
        ({"physical_address": "я" * 501}, "физический адрес: длиннее 500 знаков (501)"),
        ({"api_token": "секрет"}, "полей api_token нет; есть: sender_name, sender_position, "),
    ],
)
async def test_bad_values_are_refused_in_words_and_nothing_is_written(
    session: AsyncSession, values: dict[str, Any], words: str
) -> None:
    with pytest.raises(SenderSettingsError) as refused:
        await _save(session, {"physical_address": ADDRESS} | values)

    assert str(refused.value).startswith(words)
    assert await session.get(SalesSettingsModel, 1) is None


async def test_each_change_is_journaled_once_with_the_old_values(session: AsyncSession) -> None:
    await _save(session, FILLED)
    await _save(session, FILLED)  # то же самое — журнал не пишется
    await _save(session, FILLED | {"physical_address": "Другая выдуманная ул., 9"})

    journal = await _journal(session)
    assert [target for target, _ in journal] == ["sales_settings", "sales_settings"]
    assert journal[-1][1] == {"поля": ["physical_address"], "было": {"physical_address": ADDRESS}}


async def test_saving_nothing_over_nothing_writes_no_row(session: AsyncSession) -> None:
    saved = await _save(session, {})

    assert saved.missing == ["не задан физический адрес", "не задана подпись"]
    assert (await session.get(SalesSettingsModel, 1), await _journal(session)) == (None, [])


# --- схема ------------------------------------------------------------------------------


async def test_a_second_settings_row_is_refused_by_the_database(session: AsyncSession) -> None:
    with pytest.raises(IntegrityError, match="ck_sales_settings_one_row"):
        async with session.begin_nested():
            session.add(SalesSettingsModel(id=2, sender_name="второй"))
            await session.flush()


def test_settings_table_has_no_place_for_secrets() -> None:
    """Ключи сервисов — в окружении через `config/`; эту строку целиком видит экран."""
    columns = {column.key for column in inspect(SalesSettingsModel).columns}
    secret_like = [
        c for c in columns if any(w in c for w in ("key", "secret", "token", "password", "dsn"))
    ]

    assert secret_like == []
    assert columns == {*sender.FIELDS, "id", "updated_by", "created_at", "updated_at"}
