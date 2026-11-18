"""Продажи: добивка, сохранённая до 18.11.2026, уходит с именем сайта IDN по-человечески.

Тексты добивок с зонами `rewrite` пишет сборка первого письма и хранит у переписки лида
(`sales_threads.followups`); уходят они потом этим текстом. До 18.11.2026 `{{site}}` вставал в них
в ASCII и в тексте — `xn--80ai1b.example.test` вместо «сад.example.test». На выходе — отправке
(`mail.followup`) и экрану очереди (`followup_texts.ahead`) — имя приводит правило подстановки
(`compose.shown_sites`, его проверки — `test_letters_idn_hosts.py`): в тексте — письменностью
сайта, в ссылке и в адресе почты — ASCII. Мир — `test_sales_send_world.py`; домены и люди выдуманы.
"""

from __future__ import annotations

from typing import Any

import pytest
from backend.features.sales import chain, chain_text, followup_texts, mail
from backend.features.sales.models import SalesThreadModel
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.test_sales_followup_texts import EVERY, Recorder, _build, _chain, _links
from tests.test_sales_seam_send_owner import NAMED_FOLLOW
from tests.test_sales_send import SIGNED

#: Сайт лида — домен IDN, как его хранит база: «сад.example.test».
SITE = "xn--80ai1b.example.test"
SHOWN = "сад.example.test"
ANNA = f"anna@{SITE}"
#: Вторая добивка не легла в коридор и сохранена текстом шаблона — так её называет экран очереди.
OFF = "не легла в коридор отличия после 3 попыток: 10%, 10%, 10% — уйдёт текстом шаблона"


def _note(step: int, site: str) -> str:
    """Текст добивки шага: имя сайта `site` в тексте; ссылка и адрес почты — всегда ASCII."""
    return (
        f"Made-up reminder {step} for the {site} team: two pages of {site} caught our eye.\n\n"
        f"See https://{SITE}/catalog or write to info@{SITE}."
    )


def _steps() -> dict[str, dict[str, Any]]:
    """Добивки, какими их сохранила сборка до 18.11.2026: имя сайта в ASCII и в тексте. Первая
    переписана (20%), вторая не легла в коридор — текст шаблона с подстановками."""
    return {
        "2": {
            "body": _note(2, SITE),
            "difference": 0.2,
            "zones": ["pa", "pb"],
            "differences": [0.2],
        },
        "3": {
            "body": _note(3, SITE),
            "difference": None,
            "zones": [],
            "differences": [0.1, 0.1, 0.1],
        },
    }


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    return await w.world(session, monkeypatch)


async def _kept(session: AsyncSession, world: w.World) -> SalesThreadModel:
    """Своя цепочка гипотезы; письмо Анны собрано и стоит в очереди, а у переписки — добивки,
    сохранённые до 18.11.2026 той же версией цепочки."""
    version = await _chain(session, world)
    await w.lead(session, world.hypothesis_id, ANNA)
    await _build(session, world, Recorder(EVERY))
    [link] = await _links(session)
    link.followups = {"version": version, "steps": _steps()}
    await session.flush()
    return link


async def test_kept_followup_goes_with_the_site_as_written_and_the_link_in_ascii(
    session: AsyncSession, world: w.World
) -> None:
    """Мутант «сохранённый текст уходит как есть»: имя сайта в тексте — «сад.example.test»,
    ссылка и адрес почты — ASCII, как в базе."""
    link = await _kept(session, world)

    second = await mail.followup(session, link.thread_id, 1)
    third = await mail.followup(session, link.thread_id, 2)

    assert second.body == f"{_note(2, SHOWN)}{SIGNED}"
    assert third.body == f"{_note(3, SHOWN)}{SIGNED}"
    assert f"See https://{SITE}/catalog or write to info@{SITE}." in second.body


async def test_queue_screen_shows_the_kept_followups_as_they_will_go(
    session: AsyncSession, world: w.World
) -> None:
    """Мутант «экран показывает сохранённый текст как есть»: и переписанная добивка, и
    сохранённая текстом шаблона — с именем сайта по-человечески, как у отправки."""
    link = await _kept(session, world)
    dialog = await mail.lead_dialog(session, link.lead_id)
    assert dialog is not None

    ahead = await followup_texts.ahead(session, dialog.link, dialog.lead, dialog.host)
    sent = await mail.followup(session, link.thread_id, 1)

    assert [(item.step, item.body, item.words) for item in ahead] == [
        (2, _note(2, SHOWN), "переписана при сборке — отличие 20%"),
        (3, _note(3, SHOWN), OFF),
    ]
    assert sent.body == f"{ahead[0].body}{SIGNED}"


async def test_kept_text_of_an_earlier_chain_goes_with_the_site_as_written_too(
    session: AsyncSession, world: w.World
) -> None:
    """Мутант «прежний текст — как есть»: цепочку поправили после сборки (голый `{{name}}`), у
    лида имени нет — добивка уходит текстом, сохранённым прежней версией цепочки (`choose`), и
    имя сайта в нём по-человечески; экран очереди говорит то же."""
    link = await _kept(session, world)
    dialog = await mail.lead_dialog(session, link.lead_id)
    assert dialog is not None
    dialog.lead.name = None
    named = chain_text.step_template(step=2, language="en", body=NAMED_FOLLOW)
    await chain.save(
        session, named, hypothesis_id=world.hypothesis_id, author="тест", author_id=None
    )
    await session.flush()

    sent = await mail.followup(session, link.thread_id, 1)
    [ahead, _] = await followup_texts.ahead(session, dialog.link, dialog.lead, dialog.host)

    assert sent.body == f"{_note(2, SHOWN)}{SIGNED}"
    assert (ahead.body, ahead.words.split(":")[0]) == (
        _note(2, SHOWN),
        "сохранённым прежним текстом — нынешней цепочкой не собрать",
    )


async def test_same_kept_text_goes_the_same_every_time_and_the_thread_keeps_it_as_it_was(
    session: AsyncSession, world: w.World
) -> None:
    """Повтор попытки (ящик встал, отказ почты) собирает текст добивки заново — он тот же. У
    переписки текст остаётся как был: имя сайта приводится на выходе, а не в базе."""
    link = await _kept(session, world)

    first = await mail.followup(session, link.thread_id, 1)
    again = await mail.followup(session, link.thread_id, 1)

    assert first.body == again.body == f"{_note(2, SHOWN)}{SIGNED}"
    [link] = await _links(session)
    assert link.followups is not None
    assert link.followups["steps"] == _steps()
