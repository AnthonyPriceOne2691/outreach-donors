"""Стык продаж с ключом домена в ASCII (19.11.2026, `donors/host.normalize_host` → `idn.ascii_host`).

Сайт письменностью из книги или файла продаж ложится тем же ключом `xn--…`, что строка выдачи, — одна
компания для «одной гипотезы», «сбор не ищет адрес» и стоп-листа. Записанное раньше письменностью
переводит миграция; где ASCII-запись уже была, письменность осталась двойником. Сверка со стоп-листом
продаж обязана держать лида при любой из форм: иначе снятие любой записи (`stoplist_leads.release`)
вернуло бы его в работу, и письмо ушло бы компании из стоп-листа. Слова очистки называют домен
письменностью сайта (`shown_host`): ключ — для сверки, имя — для человека. Имена выдуманы.
"""

from __future__ import annotations

import pytest
from backend.features.sales import cleaning, intake, mail, stoplist_leads
from backend.features.sales.clean_known import load_known
from backend.features.sales.company_hold import Holder
from backend.features.sales.hold_words import HELD
from backend.features.sales.models import LeadStatus, RejectionReason, SalesStoplistModel
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w

SCRIPT = "пример.рф"
ACE = "xn--e1afmkfd.xn--p1ai"


@pytest.mark.parametrize("cell", [SCRIPT, "https://www.пример.рф/контакты", "ПРИМЕР.РФ", ACE])
def test_a_site_in_script_from_a_file_is_keyed_like_the_serp_row(cell: str) -> None:
    assert intake.host_key(cell) == ACE


async def test_an_ascii_stoplist_entry_holds_a_lead_on_a_twin_row_in_script(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Строка `domains` письменностью осталась двойником; стоп-лист записан ASCII."""
    world = await w.world(session, monkeypatch)
    lead = await w.lead(session, world.hypothesis_id, "ivan@mail.example.test", host=SCRIPT)
    session.add(SalesStoplistModel(host=ACE))
    await session.flush()

    known = await load_known(session, [lead], w.NOW)

    # Сверка — ключом ASCII, слова человеку — письменностью сайта (`shown_host`), как в Telegram.
    assert cleaning._stoplisted(lead, known) == f"стоп-лист продаж: домен {SCRIPT}"
    assert await mail.stopped_by(session, lead, SCRIPT, now=w.NOW) == "в ручном стоп-листе продаж"


def test_held_words_name_the_domain_in_script_and_still_give_the_holder() -> None:
    """Слова отказа «компания уже в работе…» называют домен письменностью, а номер держателя из них
    по-прежнему достаётся — по нему лида вернут, когда держателя отклонят (`company_hold.freed`)."""
    words = Holder(4021, 7, "Логистика — пример", ACE, True).words

    assert f"на домене {SCRIPT} — " in words
    assert ACE not in words
    assert HELD.lead(words) == 4021


async def test_a_stoplist_entry_left_in_script_holds_a_lead_on_the_ascii_row(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Запись стоп-листа письменностью осталась двойником; лид — на строке выдачи `xn--`."""
    world = await w.world(session, monkeypatch)
    lead = await w.lead(session, world.hypothesis_id, "ivan@mail.example.test", host=ACE)
    session.add(SalesStoplistModel(host=SCRIPT))
    await session.flush()

    known = await load_known(session, [lead], w.NOW)

    assert cleaning._stoplisted(lead, known) is not None
    assert await mail.stopped_by(session, lead, ACE, now=w.NOW) == "в ручном стоп-листе продаж"


async def test_releasing_another_entry_keeps_such_a_lead_rejected(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = await w.world(session, monkeypatch)
    held = await w.lead(
        session,
        world.hypothesis_id,
        "ivan@mail.example.test",
        host=SCRIPT,
        status=LeadStatus.REJECTED,
        rejection_reason=RejectionReason.STOPLIST.value,
    )
    session.add(SalesStoplistModel(host=ACE))
    await session.flush()

    released = await stoplist_leads.release(session, now=w.NOW)
    await session.refresh(held)

    assert released == 0
    assert held.status is LeadStatus.REJECTED
