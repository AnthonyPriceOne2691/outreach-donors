"""Правка лида на экране «Лиды» (сверка модуля продаж с ТЗ, п. 2): имя, должность, компания,
язык, страна и пояс — под правами «Продажи» и отправки, с журналом; письмо в очереди после
правки собирается заново, а до того не уходит.

Мир — подключённые продажи (`test_sales_send_world.py`). Модель — подделка: переписывает только
зону `ask`, приветствие с `{{name}}` уходит как в шаблоне — так имя из правки видно в письме.
Все тексты и адреса выдуманы.

Время правки и время письма ставит база (`now()` транзакции), а тест идёт одной транзакцией:
«письмо собрано раньше правки» тест задаёт сам, сдвигая время письма назад.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable

import httpx
import pytest
from backend.features.access.permissions import no_right
from backend.features.core.domain import AuditAction, Permission
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.outreach import MessageModel
from backend.features.letters.sending import NotReadyError, Sending
from backend.features.sales import lead_edit, queue
from backend.features.sales.models import SalesLeadModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.conftest import bearer
from tests.test_sales_send import _seen, _transports

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

JANE = "jane@acme.example.test"
SELLER = "seller@ours.example.test"
#: Переписана только зона `ask` — около пятой части письма, в коридоре отличия.
ASK_ONLY = {"ask": 0.3}


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    return await w.world(session, monkeypatch)


async def _build(session: AsyncSession, world: w.World) -> queue.QueueReport:
    model = w.ZoneRewriter(ASK_ONLY)
    return await queue.build(session, model, hypothesis_id=world.hypothesis_id, limit=10)


async def _letter(session: AsyncSession) -> MessageModel:
    found = await session.scalar(select(MessageModel).order_by(MessageModel.id))
    assert found is not None
    return found


async def _written_earlier(session: AsyncSession, letter: MessageModel) -> None:
    """Письмо собрано минутой раньше правки — как в жизни, где у сборки своя транзакция."""
    await session.execute(
        text("UPDATE messages SET updated_at = updated_at - interval '1 minute' WHERE id = :id"),
        {"id": letter.id},
    )
    await session.refresh(letter)


async def _journal(session: AsyncSession) -> list[AuditLogModel]:
    rows = await session.scalars(
        select(AuditLogModel).where(AuditLogModel.target.like("sales_lead:%"))
    )
    return list(rows)


# --- поля и журнал ---------------------------------------------------------------------------


async def test_fields_are_read_by_the_rules_of_the_import(
    session: AsyncSession, world: w.World
) -> None:
    jane = await w.lead(session, world.hypothesis_id, JANE, language=None, timezone=None)

    done = await lead_edit.change(
        session,
        jane.id,
        {
            "name": "  Jane   Example ",
            "language": "EN-us",
            "country": "Германия",
            "timezone": "europe/berlin",
            "position": "",
        },
        author_id=None,
    )

    assert done.fields == ["language", "country", "timezone"]
    fresh = done.lead
    assert (fresh.name, fresh.language, fresh.country, fresh.timezone, fresh.position) == (
        "Jane Example",
        "en",
        "de",
        "Europe/Berlin",
        None,
    )
    assert done.rebuild is False  # письма у лида ещё нет


@pytest.mark.parametrize(
    ("changes", "words"),
    [
        ({"language": "english"}, "язык «english» — не код языка: ждём код — en, ru"),
        ({"country": "Нарния"}, "страна «Нарния» не распознана: ждём код или название"),
        ({"timezone": "Mars/Base"}, "пояс «Mars/Base» не распознан: ждём имя из базы поясов"),
        ({"company": "Я" * 256}, "компания длиннее 255 знаков (256) — сократите"),
        ({"email": "x@y.example.test"}, "поля «email» у лида не правят; правят: name, position"),
    ],
)
async def test_value_that_is_not_understood_is_refused_and_nothing_is_written(
    session: AsyncSession, world: w.World, changes: dict[str, str], words: str
) -> None:
    jane = await w.lead(session, world.hypothesis_id, JANE)

    with pytest.raises(lead_edit.LeadEditError) as refused:
        await lead_edit.change(session, jane.id, {"name": "Другое", **changes}, author_id=None)

    assert str(refused.value).startswith(words)
    await session.refresh(jane)
    assert jane.name == "Jane Example"
    assert await _journal(session) == []


async def test_change_is_journaled_with_what_was_and_what_became_once(
    session: AsyncSession, world: w.World, make_user: MakeUser
) -> None:
    author = await make_user(SELLER)
    jane = await w.lead(session, world.hypothesis_id, JANE, name=None)

    await lead_edit.change(session, jane.id, {"name": "Jane"}, author_id=author.id)
    again = await lead_edit.change(session, jane.id, {"name": "Jane"}, author_id=author.id)

    [journal] = await _journal(session)
    assert (journal.action, journal.user_id, journal.target) == (
        AuditAction.SALES_LEADS_IMPORTED,
        author.id,
        f"sales_lead:{jane.id}",
    )
    assert journal.details == {
        "действие": "правка лида",
        "поля": ["name"],
        "было": {"name": None},
        "стало": {"name": "Jane"},
    }
    assert (again.fields, again.rebuild) == ([], False)  # без изменений — без журнала


async def test_unknown_lead_is_refused_in_words(session: AsyncSession) -> None:
    with pytest.raises(lead_edit.UnknownLeadError, match="лида №987 нет — обновите список"):
        await lead_edit.change(session, 987, {"name": "Jane"}, author_id=None)


# --- письмо: имя из правки, пересборка, отправка ---------------------------------------------


async def test_name_from_the_edit_goes_into_the_letter(
    session: AsyncSession, world: w.World
) -> None:
    """Лид из «пишите другому» без имени ждёт; правка имени — и письмо с ним."""
    jane = await w.lead(session, world.hypothesis_id, JANE, name=None)
    waiting = await _build(session, world)
    assert waiting.waiting == Counter({"у лида нет значения для {{name}}": 1})

    await lead_edit.change(session, jane.id, {"name": "Jane Edited"}, author_id=None)
    report = await _build(session, world)

    assert report.prepared == 1
    letter = await _letter(session)
    assert letter.body is not None
    assert letter.body.startswith("Hello Jane Edited,")


async def test_queued_letter_of_an_edited_lead_is_built_again_in_place_once(
    session: AsyncSession, world: w.World
) -> None:
    jane = await w.lead(session, world.hypothesis_id, JANE)
    await _build(session, world)
    before = await _letter(session)
    await _written_earlier(session, before)

    done = await lead_edit.change(session, jane.id, {"name": "Janet"}, author_id=None)
    rebuilt = await _build(session, world)
    after = await _letter(session)
    again = await _build(session, world)

    assert done.rebuild is True
    assert (rebuilt.prepared, rebuilt.refreshed) == (0, 1)
    assert after.id == before.id
    assert after.body is not None
    assert after.body.startswith("Hello Janet,")
    assert (again.refreshed, again.waiting) == (0, Counter({queue.UP_TO_DATE: 1}))


async def test_letter_built_before_the_edit_does_not_go_until_it_is_built_again(
    session: AsyncSession, world: w.World
) -> None:
    jane = await w.lead(session, world.hypothesis_id, JANE)
    await _build(session, world)
    letter = await _letter(session)
    await _written_earlier(session, letter)
    await lead_edit.change(session, jane.id, {"company": "Renamed Co"}, author_id=None)
    source = _transports()

    with pytest.raises(NotReadyError, match=lead_edit.EDITED):
        await Sending(session, source, now=w.NOW).send(letter.id)
    await _build(session, world)
    await Sending(session, source, now=w.NOW).send(letter.id)

    [sent] = _seen(source)
    assert sent.subject == "A made-up question for Renamed Co"


# --- экран -----------------------------------------------------------------------------------


async def _headers(make_user: MakeUser, sign_in: SignIn, **permissions: bool) -> dict[str, str]:
    await make_user(SELLER, permissions=permissions or None)
    return bearer(await sign_in(SELLER))


async def test_screen_edits_a_lead_and_gets_the_row_back(
    client: httpx.AsyncClient,
    session: AsyncSession,
    world: w.World,
    make_user: MakeUser,
    sign_in: SignIn,
) -> None:
    jane = await w.lead(session, world.hypothesis_id, JANE, name=None)
    await session.commit()
    headers = await _headers(make_user, sign_in, send=True)

    response = await client.patch(
        f"/api/sales/leads/{jane.id}",
        headers=headers,
        json={"name": "Jane", "language": "ru-RU", "timezone": None},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["id"], body["name"], body["language"], body["timezone"]) == (
        jane.id,
        "Jane",
        "ru",
        None,
    )
    assert (body["host"], body["hypothesis"]) == ("acme.example.test", "Выдуманная гипотеза")
    assert (body["changed"], body["rebuild"]) == (["name", "language", "timezone"], False)
    stored = await session.scalar(select(SalesLeadModel.name).where(SalesLeadModel.id == jane.id))
    assert stored == "Jane"


@pytest.mark.parametrize(
    ("permissions", "words"),
    [({}, no_right(Permission.SEND)), ({"sales": False, "send": True}, no_right(Permission.SALES))],
    ids=["без права отправки", "без права «Продажи»"],
)
async def test_screen_without_the_rights_is_refused_and_nothing_changes(
    client: httpx.AsyncClient,
    session: AsyncSession,
    world: w.World,
    make_user: MakeUser,
    sign_in: SignIn,
    permissions: dict[str, bool],
    words: str,
) -> None:
    """Мутант «правка без права»: имя ушло бы в письмо без права отправки."""
    jane = await w.lead(session, world.hypothesis_id, JANE)
    await session.commit()
    headers = await _headers(make_user, sign_in, **permissions)

    response = await client.patch(
        f"/api/sales/leads/{jane.id}", headers=headers, json={"name": "Чужое"}
    )

    assert response.status_code == 403
    assert response.json()["detail"] == words
    await session.refresh(jane)
    assert jane.name == "Jane Example"


@pytest.mark.parametrize(
    ("lead", "body", "expected"),
    [
        (True, {"country": "Нарния"}, (400, "страна «Нарния» не распознана")),
        (False, {"name": "Jane"}, (404, "лида №")),
        (True, {"mail": "x"}, (422, None)),
    ],
    ids=["негодное поле", "лида нет", "лишнее поле"],
)
async def test_screen_refusals_are_in_words(
    client: httpx.AsyncClient,
    session: AsyncSession,
    world: w.World,
    make_user: MakeUser,
    sign_in: SignIn,
    lead: bool,
    body: dict[str, str],
    expected: tuple[int, str | None],
) -> None:
    jane = await w.lead(session, world.hypothesis_id, JANE)
    await session.commit()
    headers = await _headers(make_user, sign_in, send=True)
    number = jane.id if lead else jane.id + 1000

    response = await client.patch(f"/api/sales/leads/{number}", headers=headers, json=body)

    code, words = expected
    assert response.status_code == code, response.text
    if words is not None:
        assert response.json()["detail"].startswith(words)
