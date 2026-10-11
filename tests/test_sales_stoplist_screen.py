"""Стоп-лист продаж экраном (сверка модуля продаж с ТЗ, п. 3): список, вставка списком и файлом,
снятие записи; пополнение снимает лидов правилом очистки, снятие — возвращает их на очистку.

Раньше список пополняла только консоль, а список текущих клиентов и партнёров вставляют разом.
Здесь — путь экрана на настоящей базе: записи, лиды, журнал. Домены и адреса выдуманы.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from backend.cli.main import build_parser
from backend.cli.sales import EXIT_OK, run_stoplist_add
from backend.features.access.permissions import no_right
from backend.features.core.domain import AuditAction, Permission, Stage, SuppressionReason
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import MessageModel
from backend.features.letters.sending import Sending
from backend.features.sales import hypotheses, queue, stoplist, stoplist_leads
from backend.features.sales.mail import LeadStoppedError
from backend.features.sales.models import (
    LeadStatus,
    SalesLeadModel,
    SalesStoplistModel,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.conftest import bearer
from tests.test_sales_send import _transports

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

SELLER = "seller@ours.example.test"
URL = "/api/sales/stoplist"
#: Три адреса и два домена — список клиентов и партнёров, вставленный разом.
PASTED = (
    "boss@acme.example.test\n"
    "  Anna@Beta.example.test \n"
    "ceo@gamma.example.test\n"
    "delta.example.test\n"
    "https://www.Omega.example.test/about\n"
)


async def _hypothesis(session: AsyncSession) -> int:
    return (await hypotheses.add(session, "Выдуманная гипотеза", None)).id


async def _lead(session: AsyncSession, hypothesis_id: int, email: str, **fields: Any) -> int:
    return (await w.lead(session, hypothesis_id, email, **fields)).id


async def _state(session: AsyncSession, lead_id: int) -> tuple[str, str | None, str | None]:
    found = await session.execute(
        select(
            SalesLeadModel.status, SalesLeadModel.rejection_reason, SalesLeadModel.cleaning_note
        ).where(SalesLeadModel.id == lead_id)
    )
    status, reason, note = found.one()
    return status.value, reason, note


async def _stored(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(SalesStoplistModel)) or 0)


async def _headers(make_user: MakeUser, sign_in: SignIn, **permissions: bool) -> dict[str, str]:
    await make_user(SELLER, permissions=permissions or {"send": True})
    return bearer(await sign_in(SELLER))


# --- вставка ---------------------------------------------------------------------------------


async def test_three_addresses_and_two_domains_are_written_and_their_leads_closed(
    client: httpx.AsyncClient, session: AsyncSession, make_user: MakeUser, sign_in: SignIn
) -> None:
    """Мутант «стоп-лист без очистки лидов»: лиды остались бы «новыми» и «готовыми»."""
    hypothesis = await _hypothesis(session)
    boss = await _lead(session, hypothesis, "boss@acme.example.test", status=LeadStatus.NEW)
    sales = await _lead(session, hypothesis, "sales@delta.example.test")
    mail = await _lead(session, hypothesis, "ivan@omega.example.test", host="o.example.test")
    other = await _lead(session, hypothesis, "kim@acme.example.test")
    await session.commit()
    headers = await _headers(make_user, sign_in)

    response = await client.post(URL, headers=headers, data={"text": PASTED, "note": "клиенты"})

    assert response.status_code == 200, response.text
    assert response.json() == {"added": 5, "known": 0, "closed": 3, "remarks": []}
    assert await _state(session, boss) == (
        "rejected",
        "stoplist",
        "стоп-лист продаж: адрес boss@acme.example.test",
    )
    assert await _state(session, sales) == (
        "rejected",
        "stoplist",
        "стоп-лист продаж: домен delta.example.test",
    )
    assert (await _state(session, mail))[2] == "стоп-лист продаж: домен omega.example.test"
    assert await _state(session, other) == ("ready", None, None)
    rows = await session.execute(
        select(SalesStoplistModel.host, SalesStoplistModel.email, SalesStoplistModel.note).order_by(
            SalesStoplistModel.id
        )
    )
    assert [tuple(row) for row in rows] == [
        ("delta.example.test", None, "клиенты"),
        ("omega.example.test", None, "клиенты"),
        (None, "boss@acme.example.test", "клиенты"),
        (None, "anna@beta.example.test", "клиенты"),
        (None, "ceo@gamma.example.test", "клиенты"),
    ]
    journal = await session.scalar(
        select(AuditLogModel).where(AuditLogModel.action == AuditAction.SUPPRESSION_ADDED)
    )
    assert journal is not None
    assert journal.user_id is not None
    assert journal.details is not None
    assert journal.details["лидов снято"] == 3


async def test_repeated_paste_adds_no_second_row(
    client: httpx.AsyncClient, session: AsyncSession, make_user: MakeUser, sign_in: SignIn
) -> None:
    headers = await _headers(make_user, sign_in)
    await client.post(URL, headers=headers, data={"text": PASTED})

    again = await client.post(URL, headers=headers, data={"text": PASTED})

    assert again.json() == {"added": 0, "known": 5, "closed": 0, "remarks": []}
    assert await _stored(session) == 5


async def test_crooked_lines_get_a_remark_each_and_the_rest_is_taken(
    client: httpx.AsyncClient, session: AsyncSession, make_user: MakeUser, sign_in: SignIn
) -> None:
    """Вставка заголовка не знает: непонятая первая строка — тоже замечание. Домен бесплатной
    почты не записывается: он закрыл бы всех лидов на нём."""
    headers = await _headers(make_user, sign_in)
    text = "не домен\nacme.example.test\ngmail.com\nкто-то @ где-то\nkim@gmail.com\n"

    response = await client.post(URL, headers=headers, data={"text": text})

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["added"], body["known"]) == (2, 0)
    assert body["remarks"] == [
        {"line": 1, "reason": stoplist.NOT_AN_ENTRY, "cell": "не домен"},
        {"line": 3, "reason": stoplist.FREE_MAIL, "cell": "gmail.com"},
        {"line": 4, "reason": stoplist.NOT_AN_ENTRY, "cell": "кто-то @ где-то"},
    ]
    assert await _stored(session) == 2


async def test_file_is_read_like_the_console_file(
    client: httpx.AsyncClient, session: AsyncSession, make_user: MakeUser, sign_in: SignIn
) -> None:
    headers = await _headers(make_user, sign_in)
    data = "Домен;Примечание\nacme.example.test;клиент\nboss@beta.example.test;партнёр\n"

    response = await client.post(
        URL, headers=headers, files={"file": ("партнёры.csv", data.encode("utf-8"), "text/csv")}
    )

    assert response.json() == {"added": 2, "known": 0, "closed": 0, "remarks": []}
    notes = await session.scalars(select(SalesStoplistModel.note))
    assert set(notes) == {"партнёры.csv"}  # без причины — имя файла, как у консоли


@pytest.mark.parametrize("data", [{}, {"text": "   "}], ids=["ничего", "пробелы"])
async def test_nothing_given_is_refused_in_words(
    client: httpx.AsyncClient, make_user: MakeUser, sign_in: SignIn, data: dict[str, str]
) -> None:
    headers = await _headers(make_user, sign_in)

    response = await client.post(URL, headers=headers, data=data)

    assert response.status_code == 400
    assert response.json()["detail"].startswith("Вставьте адреса и домены по строке")


async def test_reason_longer_than_its_column_is_refused(
    client: httpx.AsyncClient, session: AsyncSession, make_user: MakeUser, sign_in: SignIn
) -> None:
    headers = await _headers(make_user, sign_in)

    response = await client.post(URL, headers=headers, data={"text": PASTED, "note": "я" * 256})

    assert response.status_code == 400
    assert response.json()["detail"] == "причина длиннее 255 знаков (256) — сократите"
    assert await _stored(session) == 0


# --- снятие -----------------------------------------------------------------------------------


async def test_removal_returns_only_the_leads_no_entry_closes_any_more(
    client: httpx.AsyncClient, session: AsyncSession, make_user: MakeUser, sign_in: SignIn
) -> None:
    hypothesis = await _hypothesis(session)
    sales = await _lead(session, hypothesis, "sales@delta.example.test")
    boss = await _lead(session, hypothesis, "boss@acme.example.test")
    await session.commit()
    headers = await _headers(make_user, sign_in)
    await client.post(URL, headers=headers, data={"text": "delta.example.test\nacme.example.test"})
    delta = await session.scalar(
        select(SalesStoplistModel.id).where(SalesStoplistModel.host == "delta.example.test")
    )

    response = await client.post(f"{URL}/{delta}/remove", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json() == {"id": delta, "target": "delta.example.test", "released": 1}
    assert await _state(session, sales) == ("new", None, None)  # следующая очистка проверит
    assert (await _state(session, boss))[:2] == ("rejected", "stoplist")
    journal = await session.scalar(
        select(AuditLogModel).where(AuditLogModel.action == AuditAction.SUPPRESSION_REMOVED)
    )
    assert journal is not None
    assert (journal.target, journal.details) == (
        f"sales_stoplist:{delta}",
        {"кому снова пишем": "delta.example.test", "лидов вернулось": 1},
    )
    gone = await client.post(f"{URL}/{delta}/remove", headers=headers)
    assert gone.status_code == 404
    assert gone.json()["detail"].startswith(f"записи стоп-листа продаж №{delta} нет")


async def test_lead_closed_by_two_entries_stays_closed_after_one_is_removed(
    session: AsyncSession,
) -> None:
    hypothesis = await _hypothesis(session)
    boss = await _lead(session, hypothesis, "boss@acme.example.test")
    entries = stoplist.read_text("acme.example.test\nboss@acme.example.test")
    await stoplist.add(session, entries, author="тест", note=None)
    domain = await session.scalar(
        select(SalesStoplistModel.id).where(SalesStoplistModel.host == "acme.example.test")
    )
    assert domain is not None

    removed = await stoplist.remove(session, domain, author_id=None)

    assert removed.released == 0
    assert await _state(session, boss) == (
        "rejected",
        "stoplist",
        "стоп-лист продаж: адрес boss@acme.example.test",
    )
    assert await stoplist_leads.release(session) == 0


# --- снятый лид: письмо не уходит --------------------------------------------------------------


async def test_queued_letter_of_a_closed_lead_does_not_go(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = await w.world(session, monkeypatch)
    await w.lead(session, world.hypothesis_id, "jane@acme.example.test")
    await queue.build(session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=5)
    queued = await session.scalar(select(MessageModel.id))
    assert queued is not None

    await stoplist.add(session, stoplist.read_text("acme.example.test"), author="тест", note=None)

    with pytest.raises(LeadStoppedError, match="сняли"):
        await Sending(session, _transports(), now=w.NOW).send(queued)


# --- список и права ---------------------------------------------------------------------------


async def test_screen_lists_the_entries_and_the_general_rows_that_hold_sales(
    client: httpx.AsyncClient, session: AsyncSession, make_user: MakeUser, sign_in: SignIn
) -> None:
    held = [
        SuppressionModel(email="gone@x.example.test", reason=SuppressionReason.UNSUBSCRIBED,
                         stage=Stage.DONORS),
        SuppressionModel(email="all@y.example.test", reason=SuppressionReason.MANUAL, stage=None),
        SuppressionModel(email="donor@z.example.test", reason=SuppressionReason.MANUAL,
                         stage=Stage.DONORS),
    ]  # fmt: skip
    session.add_all(held)
    await stoplist.add(session, stoplist.read_text("acme.example.test"), author="тест", note="п")
    await session.commit()
    headers = await _headers(make_user, sign_in, send=False)

    response = await client.get(URL, headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert [(row["host"], row["note"], row["created_by"]) for row in body["rows"]] == [
        ("acme.example.test", "п", "тест")
    ]
    # Ручная запись доноров продажи не держит; отписка с любого этапа — держит.
    assert {(row["target"], row["reason"]) for row in body["general"]} == {
        ("gone@x.example.test", "unsubscribed"),
        ("all@y.example.test", "manual"),
    }
    assert (body["total"], body["general_total"], body["note_max"]) == (1, 2, 255)


@pytest.mark.parametrize(
    ("permissions", "method", "words"),
    [
        ({"sales": False}, "GET", no_right(Permission.SALES)),
        ({}, "POST", no_right(Permission.SEND)),
    ],
    ids=["смотреть без «Продаж»", "пополнять без отправки"],
)
async def test_without_the_rights_the_list_is_refused_and_nothing_is_written(
    client: httpx.AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    sign_in: SignIn,
    permissions: dict[str, bool],
    method: str,
    words: str,
) -> None:
    await make_user(SELLER, permissions=permissions or None)
    headers = bearer(await sign_in(SELLER))

    response = await client.request(
        method, URL, headers=headers, data={"text": PASTED} if method == "POST" else None
    )

    assert response.status_code == 403
    assert response.json()["detail"] == words
    assert await _stored(session) == 0


async def test_console_closes_the_leads_the_same_way_and_says_how_many(
    session: AsyncSession, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Консоль и экран — один путь (`stoplist.add`): лиды снимаются и из консоли."""
    hypothesis = await _hypothesis(session)
    sales = await _lead(session, hypothesis, "sales@delta.example.test")
    path = tmp_path / "стоп.csv"
    path.write_text("Домен\ndelta.example.test\ngmail.com\n", encoding="utf-8")
    args = build_parser().parse_args(["sales-stoplist-add", "--file", str(path)])

    assert await run_stoplist_add(session, args) == EXIT_OK

    assert capsys.readouterr().out == (
        "Стоп-лист продаж: добавлено 1, уже было 0 (доменов 1, адресов 0); лидов снято: 1.\n"
        f"  строка 3: {stoplist.FREE_MAIL} — «gmail.com»\n"
    )
    assert (await _state(session, sales))[:2] == ("rejected", "stoplist")
