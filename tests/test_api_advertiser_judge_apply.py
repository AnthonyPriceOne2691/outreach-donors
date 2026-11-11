"""«Применить советы судьи» на экране: права, план, порции, журнал, список партий и откат.

Проверяется то, ради чего маршруты так устроены: числа очереди видит каждый, кто видит раздел, а
план с советами нерешённым парам, применение и откат — только учётка с правом запуска и правом
решения разом; порция фиксируется со своей записью журнала; партия продолжается по имени, которое
дал сервер, и не продолжается после отката; список партий сходится с журналом.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.api.advertisers import judge_apply as api
from backend.features.access.journal import JournalQuery, read_journal
from backend.features.access.permissions import no_right
from backend.features.core.domain import AuditAction, Permission, UserRole
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.advertiser import AdvertiserJudgementModel
from backend.features.crawl import judge_apply
from backend.features.crawl.judge_mark import BATCH, marker_of
from backend.features.crawl.judge_rules import RULES_VERSION
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.migration_helpers import load_migration
from tests.test_advertiser_judge_apply import disputed, judged

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

URL = "/api/advertisers/judge-apply"
OPERATOR = "оператор@site.example.test"
MIGRATION = "fa5cd4ff98c6_advertiser_judge_audit_actions.py"
SOME_BATCH = "skip-20261101-090000-a1b2"

#: Маршруты применения советов: метод, путь и тело. Общая таблица рекламодателей
#: (`tests/test_api_advertisers.py`) их не держит: им нужны строки суда и два права разом.
ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", URL, None),
    ("GET", f"{URL}/plan?band=skip", None),
    ("POST", URL, {"band": "skip"}),
    ("GET", f"{URL}/batches", None),
    ("POST", f"{URL}/batches/{SOME_BATCH}/revert", None),
]


@pytest.fixture
async def operator(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user(OPERATOR, role=UserRole.OPERATOR)
    return bearer(await sign_in(OPERATOR))


async def _as(make_user: MakeUser, sign_in: SignIn, email: str, **user: Any) -> dict[str, str]:
    await make_user(email, **user)
    return bearer(await sign_in(email))


async def _journal(session: AsyncSession, action: AuditAction) -> list[AuditLogModel]:
    rows = await session.scalars(
        select(AuditLogModel).where(AuditLogModel.action == action).order_by(AuditLogModel.id)
    )
    return list(rows.all())


# --- права -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
async def test_without_a_pass_nobody_is_let_in(
    client: AsyncClient, method: str, path: str, body: dict[str, Any] | None
) -> None:
    answer = await client.request(method, path, json=body)

    assert answer.status_code == 403


def test_the_table_covers_every_route_of_applying(api_app: FastAPI) -> None:
    in_app = {
        (method.upper(), path)
        for path, methods in api_app.openapi()["paths"].items()
        if path.startswith(URL)
        for method in methods
    }
    in_table = {
        (method, path.partition("?")[0].replace(SOME_BATCH, "{batch}"))
        for method, path, _ in ROUTES
    }

    assert in_app == in_table


@pytest.mark.parametrize("missing", [Permission.RUN, Permission.PRICES])
async def test_applying_needs_both_the_run_and_the_decision_right(
    client: AsyncClient,
    make_user: MakeUser,
    sign_in: SignIn,
    session: AsyncSession,
    missing: Permission,
) -> None:
    """Оператор без одного из двух прав видит числа очереди, но не план с советами, не применение и
    не откат — и ни одна пара не решена."""
    await disputed(session, ("skip.example.test", "skip"))
    headers = await _as(
        make_user, sign_in, "без-права@site.example.test", permissions={missing.value: False}
    )

    numbers = await client.get(URL, headers=headers)
    refused = [
        await client.get(f"{URL}/plan", params={"band": "skip"}, headers=headers),
        await client.post(URL, json={"band": "skip"}, headers=headers),
        await client.post(f"{URL}/batches/skip-20261101-090000-a1b2/revert", headers=headers),
    ]

    assert numbers.status_code == 200
    assert numbers.json()["skip"] == 1
    assert [answer.status_code for answer in refused] == [403, 403, 403]
    assert all(answer.json()["detail"] == no_right(missing) for answer in refused)
    assert (await judge_apply.standing(session)).skip == 1


async def test_the_section_is_closed_without_the_stage_right(
    client: AsyncClient, make_user: MakeUser, sign_in: SignIn
) -> None:
    headers = await _as(make_user, sign_in, "доноры@site.example.test", role=UserRole.DONORS)

    answers = [
        await client.get(URL, headers=headers),
        await client.get(f"{URL}/batches", headers=headers),
        await client.post(URL, json={"band": "skip"}, headers=headers),
    ]

    assert [answer.status_code for answer in answers] == [403, 403, 403]


# --- итог очереди и план ---------------------------------------------------------------------------


async def test_the_standing_and_the_plan_say_what_the_judge_said(
    client: AsyncClient, operator: dict[str, str], session: AsyncSession
) -> None:
    await disputed(
        session,
        ("skip.example.test", "skip"),
        ("write.example.test", "write"),
        ("unclear.example.test", "unclear"),
        ("unjudged.example.test", None),
    )

    standing = await client.get(URL, headers=operator)
    plan = await client.get(f"{URL}/plan", params={"band": "write"}, headers=operator)

    assert standing.status_code == 200
    assert standing.json() == {
        "waiting": 4,
        "skip": 1,
        "write": 1,
        "waiting_dr": 0,
        "unclear": 1,
        "stale": 0,
        "without": 1,
    }
    assert plan.status_code == 200
    body = plan.json()
    assert (body["band"], body["pairs"], body["donors"]) == ("write", 1, 1)
    assert body["rules"] == [{"rule": "promoted", "words": body["rules"][0]["words"], "pairs": 1}]
    assert [one["target_root"] for one in body["examples"]] == ["write.example.test"]
    assert body["examples"][0]["words"]
    assert body["launch"] == {"domains": 1, "fresh": 1, "with_address": 0}
    assert body["portion"] == judge_apply.PORTION
    assert body["standing"] == standing.json()


async def test_reading_the_standing_keeps_the_relabelled_advice(
    client: AsyncClient, operator: dict[str, str], session: AsyncSession
) -> None:
    """Совет прежней таблицей пересчитан чтением — и записан: следующий запрос его не повторяет."""
    await disputed(session, ("relabelled.example.test", None))
    row = judged("relabelled.example.test", "skip", rules_version="advertiser-rules-v0")
    row.advice = "unclear"
    session.add(row)
    await session.commit()

    answer = await client.get(URL, headers=operator)
    await session.rollback()

    assert answer.json()["skip"] == 1
    kept = await session.scalar(
        select(AdvertiserJudgementModel.rules_version).where(
            AdvertiserJudgementModel.target_root == "relabelled.example.test"
        )
    )
    assert kept == RULES_VERSION


async def test_a_plan_of_no_band_is_refused(client: AsyncClient, operator: dict[str, str]) -> None:
    answer = await client.get(f"{URL}/plan", params={"band": "unclear"}, headers=operator)

    assert answer.status_code == 422


# --- применение, журнал, партии, откат -------------------------------------------------------------


async def test_portions_make_one_batch_and_the_journal_holds_each(
    client: AsyncClient, operator: dict[str, str], session: AsyncSession
) -> None:
    await disputed(
        session,
        ("one.example.test", "skip"),
        ("two.example.test", "skip"),
        ("three.example.test", "skip"),
        ("write.example.test", "write"),
    )

    first = await client.post(URL, json={"band": "skip", "limit": 2}, headers=operator)
    batch = first.json()["batch"]
    second = await client.post(
        URL, json={"band": "skip", "batch": batch, "limit": 2}, headers=operator
    )

    assert (first.status_code, second.status_code) == (200, 200)
    assert BATCH.fullmatch(batch) is not None
    assert (first.json()["applied"], first.json()["left"]) == (2, 1)
    assert second.json() == {
        "batch": batch,
        "band": "skip",
        "applied": 1,
        "decided": 0,
        "gone": 0,
        "left": 0,
        "removed": 0,
        "stopped": 0,
        "answered": [],
        "answered_total": 0,
    }
    written = await _journal(session, AuditAction.ADVERTISER_JUDGE_APPLIED)
    assert [entry.target for entry in written] == [f"judge_batch:{batch}"] * 2
    assert [entry.details for entry in written] == [
        {"полоса": "skip", "применено": 2, "осталось": 1},
        {"полоса": "skip", "применено": 1, "осталось": 0},
    ]
    assert (await judge_apply.standing(session)).skip == 0

    listed = (await client.get(f"{URL}/batches", headers=operator)).json()

    assert len(listed) == 1
    assert {key: listed[0][key] for key in ("id", "band", "by", "applied", "left", "standing")} == {
        "id": batch,
        "band": "skip",
        "by": OPERATOR,
        "applied": 3,
        "left": 0,
        "standing": 3,
    }
    assert (listed[0]["reverted_at"], listed[0]["returned"]) == (None, 0)


async def test_a_reverted_batch_is_in_the_journal_and_is_not_continued(
    client: AsyncClient, operator: dict[str, str], session: AsyncSession
) -> None:
    await disputed(session, ("one.example.test", "skip"), ("two.example.test", "skip"))
    batch = (await client.post(URL, json={"band": "skip", "limit": 1}, headers=operator)).json()[
        "batch"
    ]

    reverted = await client.post(f"{URL}/batches/{batch}/revert", headers=operator)

    assert reverted.status_code == 200
    assert reverted.json() == {
        "batch": batch,
        "returned": 1,
        "kept": 0,
        "forgotten": 0,
        "removed": 0,
        "stopped": 0,
    }
    [entry] = await _journal(session, AuditAction.ADVERTISER_JUDGE_REVERTED)
    assert (entry.target, entry.details) == (
        f"judge_batch:{batch}",
        {"полоса": "skip", "возвращено в очередь": 1},
    )
    assert (await judge_apply.standing(session)).skip == 2
    [listed] = (await client.get(f"{URL}/batches", headers=operator)).json()
    assert (listed["reverted_by"], listed["returned"], listed["standing"]) == (OPERATOR, 1, 0)
    assert listed["reverted_at"] is not None

    again = await client.post(f"{URL}/batches/{batch}/revert", headers=operator)
    more = await client.post(URL, json={"band": "skip", "batch": batch}, headers=operator)

    assert (again.status_code, more.status_code) == (409, 409)
    assert "Откатывать нечего" in again.json()["detail"]
    assert more.json()["detail"] == api.REVERTED
    assert len(await _journal(session, AuditAction.ADVERTISER_JUDGE_REVERTED)) == 1
    assert (await judge_apply.standing(session)).skip == 2


async def test_refusals_say_why_and_write_nothing(
    client: AsyncClient, operator: dict[str, str], session: AsyncSession
) -> None:
    await disputed(session, ("write.example.test", "write"))

    nothing = await client.post(URL, json={"band": "skip"}, headers=operator)
    other_band = await client.post(
        URL, json={"band": "write", "batch": "skip-20261101-090000-a1b2"}, headers=operator
    )
    bad_name = await client.post(URL, json={"band": "write", "batch": "вчера"}, headers=operator)
    # Имя по форме годное, а даты такой нет или она из будущего: партию по нему не найти.
    no_such_day = await client.post(
        URL, json={"band": "write", "batch": "write-20261341-090000-a1b2"}, headers=operator
    )
    from_future = await client.post(
        f"{URL}/batches/write-29991101-090000-a1b2/revert", headers=operator
    )
    extra = await client.post(URL, json={"band": "write", "force": True}, headers=operator)
    unknown = await client.post(f"{URL}/batches/skip-20261101-090000-a1b2/revert", headers=operator)
    not_a_batch = await client.post(f"{URL}/batches/everything/revert", headers=operator)

    assert (nothing.status_code, nothing.json()["detail"]) == (409, api.NOTHING_TO_APPLY)
    assert other_band.status_code == 422
    assert "другой полосой" in other_band.json()["detail"]
    assert (bad_name.status_code, extra.status_code) == (422, 422)
    assert (no_such_day.status_code, from_future.status_code) == (422, 422)
    assert "названа не так" in no_such_day.json()["detail"]
    assert (unknown.status_code, not_a_batch.status_code) == (409, 422)
    assert await _journal(session, AuditAction.ADVERTISER_JUDGE_APPLIED) == []
    assert (await judge_apply.standing(session)).write == 1


async def test_the_queue_says_the_judge_decided_and_a_human_may_overrule(
    client: AsyncClient, operator: dict[str, str], session: AsyncSession
) -> None:
    ids = await disputed(session, ("skip.example.test", "skip"))
    batch = (await client.post(URL, json={"band": "skip"}, headers=operator)).json()["batch"]

    queue = await client.get(
        "/api/advertisers", params={"include_decided": "true"}, headers=operator
    )
    overruled = await client.post(
        f"/api/advertisers/{ids['skip.example.test']}/decide",
        json={"confirmed": True, "force": True},
        headers=operator,
    )

    [card] = queue.json()["rows"]
    assert (card["confirmed"], card["by_judge"]) == (False, True)
    assert card["decided_by"] == marker_of(batch)
    assert (card["judge"]["advice"], bool(card["judge"]["words"])) == ("skip", True)
    assert overruled.status_code == 200
    assert (overruled.json()["confirmed"], overruled.json()["by_judge"]) == (True, False)
    assert overruled.json()["decided_by"] == OPERATOR


# --- журнал ----------------------------------------------------------------------------------------


async def test_the_batch_entries_are_stage_two_entries(
    client: AsyncClient, operator: dict[str, str], session: AsyncSession
) -> None:
    """Без права «Рекламодатели» записей о партиях в журнале нет — как остальных действий Этапа 2."""
    await disputed(session, ("skip.example.test", "skip"))
    batch = (await client.post(URL, json={"band": "skip"}, headers=operator)).json()["batch"]
    await client.post(f"{URL}/batches/{batch}/revert", headers=operator)

    def about_batches(page: Any) -> list[AuditAction]:
        return [entry.action for entry in page.entries if entry.target == f"judge_batch:{batch}"]

    seen = await read_journal(session, JournalQuery())
    hidden = await read_journal(session, JournalQuery(with_advertisers=False))

    assert about_batches(seen) == [
        AuditAction.ADVERTISER_JUDGE_REVERTED,
        AuditAction.ADVERTISER_JUDGE_APPLIED,
    ]
    assert about_batches(hidden) == []


def _journal_values(connection: Connection) -> list[str]:
    """Ревизия журнала ещё раз, в процессе: подъём сьюта идёт подпроцессом, и покрытие его не видит.
    `ADD VALUE IF NOT EXISTS` делает повтор безвредным."""
    migration = load_migration(MIGRATION)
    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
        migration.downgrade()
    values = connection.execute(text("SELECT unnest(enum_range(NULL::auditaction))::text"))
    return list(values.scalars())


async def test_the_journal_values_are_there_once_and_survive_a_rerun(
    session: AsyncSession,
) -> None:
    connection = await session.connection()

    values = await connection.run_sync(_journal_values)

    assert values.count("advertiser_judge_applied") == 1
    assert values.count("advertiser_judge_reverted") == 1
