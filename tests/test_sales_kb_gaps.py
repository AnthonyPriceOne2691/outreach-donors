"""Пустые разделы базы знаний видны (сверка модуля продаж с ТЗ, п. 5): строка у каждого пустого
раздела языка писем — что агент сделает без него; заполненный — без строки.

Правило — агента (`agent/facts.py`): нет записей вида на языке письма — берутся записи на любом
и переводятся. Поэтому пустой раздел называется двумя словами: «возьмёт на другом и переведёт»
и «не приведёт ни одного примера». Выключенную запись агент не видит — и раздел с ней пуст.
Тексты записей выдуманы.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
from backend.features.core.models.access import UserModel
from backend.features.sales import kb, kb_gaps
from backend.features.sales.models import KbKind
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]


def _fact(number: int, kind: KbKind, language: str) -> kb.Fact:
    return kb.Fact(number, kind, language, f"Выдуманная запись {number}", "Выдуманный текст.", ())


#: Все виды на русском и английском — кроме кейсов на английском и цен вовсе.
FULL = [
    _fact(n, kind, language)
    for n, (kind, language) in enumerate(
        (kind, language)
        for language in ("ru", "en")
        for kind in KbKind
        if (kind, language) != (KbKind.CASE, "en") and kind is not KbKind.PRICE_POLICY
    )
]


def test_empty_sections_of_the_letter_languages_are_said_and_filled_ones_are_not() -> None:
    found = kb_gaps.gaps(FULL)

    assert [(gap.kind, gap.language, gap.words) for gap in found] == [
        (
            KbKind.PRICE_POLICY,
            "ru",
            "Записей о ценах на русском нет — агент цен не назовёт и позовёт на созвон",
        ),
        (
            KbKind.CASE,
            "en",
            "Кейсов на английском нет — агент возьмёт записи на русском и переведёт",
        ),
        (
            KbKind.PRICE_POLICY,
            "en",
            "Записей о ценах на английском нет — агент цен не назовёт и позовёт на созвон",
        ),
    ]
    assert [gap.elsewhere for gap in found] == [(), ("ru",), ()]


def test_a_section_with_no_entry_in_any_language_says_what_the_agent_will_not_do() -> None:
    only_brief = [_fact(1, KbKind.BRIEF, "en-us")]

    found = {(gap.kind, gap.language): gap.words for gap in kb_gaps.gaps(only_brief)}

    assert (KbKind.BRIEF, "en") not in found  # `en-us` — тоже английский, как у агента
    assert (
        found[KbKind.CASE, "en"] == "Кейсов на английском нет — агент не приведёт ни одного примера"
    )
    assert found[KbKind.BRIEF, "ru"] == (
        "Записей «о компании» на русском нет — агент возьмёт записи на английском и переведёт"
    )
    assert len(found) == 2 * len(KbKind) - 1


async def test_screen_reads_the_gaps_and_a_switched_off_entry_does_not_fill_its_section(
    client: httpx.AsyncClient, session: AsyncSession, make_user: MakeUser, sign_in: SignIn
) -> None:
    for kind in KbKind:
        for language in ("ru", "en"):
            new = kb.entry(
                kind=kind.value,
                language=language,
                title=f"Выдуманная запись {kind.value}",
                text="Выдуманный текст.",
                active=(kind, language) != (KbKind.CASE, "en"),
            )
            await kb.add(session, new, author="тест", author_id=None)
    await session.commit()
    await make_user("seller@ours.example.test")
    headers = bearer(await sign_in("seller@ours.example.test"))

    body = (await client.get("/api/sales/kb", headers=headers)).json()

    assert body["gaps"] == [
        {
            "kind": "case",
            "language": "en",
            "elsewhere": ["ru"],
            "words": "Кейсов на английском нет — агент возьмёт записи на русском и переведёт",
        }
    ]
    assert (body["total"], body["active"]) == (2 * len(KbKind), 2 * len(KbKind) - 1)
