"""Отмена решения человека по сайту сбора — `DELETE /api/sales/segment/candidates/{N}/decision`.

Ответ решения и отмены — строкой списка сайтов прогона (`SiteView`): экран ставит её на место той,
по которой решали, — с полосой, словами судьи и тем, можно ли отменить. Отмена — в журнале рядом с
решением, тем же действием, со снятым лидом сайта, если он был; повтор — 200 без второй записи;
решение правила или судьи, письмо лиду ушло — 409 словами; нет сайта — 404; без права «Продажи» —
403, и ничего не меняется. Список сайтов называет у решения человека слова судьи. Сайты судит
настоящий проход судьи с подделкой модели (`test_sales_segment_undo.judged`); домены выдуманы.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from backend.api.sales.segment_sites import SiteView
from backend.config.judge import JudgeMode
from backend.features.access.permissions import no_right
from backend.features.core.domain import AuditAction, Permission, Stage
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.sales import segment_undo as su
from backend.features.sales.models import LeadSource, LeadStatus, SalesLeadModel
from backend.features.sales.segment_models import SalesSegmentCandidateModel
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_sales_segment_undo import (
    ANYWHERE,
    CLINIC,
    INTERMEDIARY,
    PORTAL,
    WORKSHOPS,
    _network,
    judged,
    landed,
    words,
)
from tests.test_sales_segment_undo_lead import sent_letter

__all__ = ["_network"]  # сеть прогона — отсюда фикстуру видит pytest

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

SELLER = "seller@ours.example.test"
CANDIDATES = "/api/sales/segment/candidates"
#: Время прохода судьи: отмена возвращает его под «модель».
JUDGED = datetime(2026, 10, 22, 9, 15, tzinfo=UTC)
SCREEN = Path(__file__).resolve().parent.parent / "frontend/src/sales/SegmentSitesApi.ts"
#: Слова судьи при отказе в наблюдении — как есть: что это отказ, говорит полоса «судья отказал бы».
CUT = words(INTERMEDIARY, WORKSHOPS)


@pytest.fixture
async def headers(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user(SELLER)
    return bearer(await sign_in(SELLER))


async def _journal(session: AsyncSession, candidate_id: int) -> list[dict[str, Any]]:
    found = await session.scalars(
        select(AuditLogModel)
        .where(
            AuditLogModel.action == AuditAction.SITE_REVIEWED,
            AuditLogModel.target == f"sales_segment_candidate:{candidate_id}",
        )
        .order_by(AuditLogModel.id)
    )
    return [entry.details for entry in found]


async def _lead(session: AsyncSession, site: SalesSegmentCandidateModel) -> SalesLeadModel:
    lead = SalesLeadModel(
        hypothesis_id=site.hypothesis_id,
        domain_id=site.domain_id,
        email=f"info@{site.domain_id}.example.test",
        source=LeadSource.COLLECTED,
        status=LeadStatus.READY,
    )
    session.add(lead)
    await session.flush()
    site.lead_id = lead.id
    await session.commit()
    return lead


async def test_a_decision_and_its_undo_answer_with_the_row_and_are_journaled_side_by_side(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    _, made = await judged(session, WORKSHOPS, now=JUDGED)
    site = made[WORKSHOPS.host]
    url = f"{CANDIDATES}/{site.id}/decision"

    decided = await client.post(url, json={"accept": False}, headers=headers)
    undone = await client.delete(url, headers=headers)
    again = await client.delete(url, headers=headers)

    assert decided.status_code == 200, decided.text
    row = decided.json()
    assert set(row) == set(SiteView.model_fields)
    assert (row["lane"], row["decided_by"], row["decided_by_user"], row["reason"]) == (
        "reject",
        "human",
        SELLER,
        "отказал человек",
    )
    assert (row["undoable"], row["judge_reason"], row["quote"]) == (
        True,
        CUT,
        "Alle Möbeltischler in Ihrer Stadt",
    )
    assert undone.status_code == 200, undone.text
    back = undone.json()
    assert (back["lane"], back["reason"], back["decided_by"], back["verdict"]) == (
        "would_reject",
        CUT,
        "model",
        "reject",
    )
    # Под «модель» — снова дата судьи: решения человека нет, а судья решал тогда.
    assert (
        back["decided_by_user"],
        back["decided_at"],
        back["undoable"],
        back["judge_reason"],
    ) == (
        None,
        "2026-10-22T09:15:00Z",
        False,
        None,
    )
    assert (again.status_code, again.json()) == (200, back)
    decision, undo = await _journal(session, site.id)  # повтор отмены второй записи не пишет
    assert (decision["действие"], decision["решение"], decision["было"]) == (
        "решение по сайту сбора базы",
        "rejected",
        "судья отказал бы",
    )
    assert undo == {
        "этап": Stage.SALES.value,
        "действие": "отмена решения по сайту сбора базы",
        "домен": WORKSHOPS.host,
        "отменено": "rejected",
        "стало": "судья отказал бы",
        "судья сегмента": "отсечён",
        "прогон": site.run_id,
    }


async def test_an_undo_takes_off_the_lead_without_a_letter_and_journals_it(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    """Тест продаж 21.10: лид заводится почти сразу после «Принять». Письма ему нет — кнопка есть,
    отмена снимает лида, а журнал говорит, какого и что с его письмом: по журналу ищут, куда он
    делся."""
    run, made = await judged(session, WORKSHOPS)
    site = made[WORKSHOPS.host]
    url = f"{CANDIDATES}/{site.id}/decision"
    assert (await client.post(url, json={"accept": True}, headers=headers)).status_code == 200
    lead = await _lead(session, site)
    listed = await client.get(f"/api/sales/segment/runs/{run.id}/sites", headers=headers)
    assert [row["undoable"] for row in listed.json()["rows"]] == [True]

    undone = await client.delete(url, headers=headers)

    assert undone.status_code == 200, undone.text
    row = undone.json()
    assert (row["lane"], row["outcome"], row["undoable"]) == ("would_reject", None, False)
    await session.refresh(lead)
    assert (lead.status, lead.rejection_reason) == (LeadStatus.REJECTED, "undone")
    _, undo = await _journal(session, site.id)
    assert undo == {
        "этап": Stage.SALES.value,
        "действие": "отмена решения по сайту сбора базы",
        "домен": WORKSHOPS.host,
        "отменено": "accepted",
        "стало": "судья отказал бы",
        "лид снят": f"№{lead.id} {lead.email}",
        "лид был": "готов",
        "письмо лиду": "в очереди не было",
        "судья сегмента": "отсечён",
        "прогон": site.run_id,
    }


async def test_refusals_are_409_in_words_and_an_unknown_site_is_404(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    _, made = await judged(session, WORKSHOPS, CLINIC)
    workshops, clinic = made[WORKSHOPS.host], made[CLINIC.host]
    url = f"{CANDIDATES}/{workshops.id}/decision"
    assert (await client.post(url, json={"accept": True}, headers=headers)).status_code == 200
    await sent_letter(session, await _lead(session, workshops))

    with_lead = await client.delete(url, headers=headers)
    by_judge = await client.delete(f"{CANDIDATES}/{clinic.id}/decision", headers=headers)
    unknown = await client.delete(f"{CANDIDATES}/987654/decision", headers=headers)

    assert (with_lead.status_code, with_lead.json()["detail"]) == (409, su.GONE)
    assert (by_judge.status_code, by_judge.json()["detail"]) == (409, su.NOT_HUMAN["model"])
    assert (unknown.status_code, unknown.json()["detail"]) == (
        404,
        "сайта сбора №987654 нет — обновите отчёт прогона",
    )
    assert [entry["действие"] for entry in await _journal(session, workshops.id)] == [
        "решение по сайту сбора базы"
    ]
    assert await _journal(session, clinic.id) == []


async def test_the_list_names_the_judge_next_to_a_human_decision_and_whether_it_is_undone(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    """Тест продаж 20.10: с чем человек не согласился, видно не было — причина у решения человека
    одна: «принял человек». Теперь рядом — слова судьи тем видом, каким их кладёт судья."""
    run, made = await judged(session, WORKSHOPS, PORTAL, CLINIC)
    workshops, portal = made[WORKSHOPS.host], made[PORTAL.host]
    for site, accept in ((workshops, True), (portal, False)):
        url = f"{CANDIDATES}/{site.id}/decision"
        assert (await client.post(url, json={"accept": accept}, headers=headers)).status_code == 200
    await sent_letter(session, await _lead(session, workshops))

    response = await client.get(f"/api/sales/segment/runs/{run.id}/sites", headers=headers)

    assert response.status_code == 200, response.text
    rows = {row["host"]: row for row in response.json()["rows"]}
    said = {
        host: (row["reason"], row["judge_reason"], row["undoable"]) for host, row in rows.items()
    }
    assert said == {
        # Письмо лиду ушло — отменой его не отозвать: кнопки нет.
        WORKSHOPS.host: ("принял человек", CUT, False),
        PORTAL.host: ("отказал человек", words(INTERMEDIARY, PORTAL), True),
        # Решила модель — её слова и так в причине.
        CLINIC.host: (words(ANYWHERE, CLINIC), None, False),
    }


async def test_without_the_sales_right_the_undo_is_refused_and_nothing_changes(
    session: AsyncSession, client: AsyncClient, make_user: MakeUser, sign_in: SignIn
) -> None:
    _, made = await judged(session, PORTAL, mode=JudgeMode.SHADOW)
    site = made[PORTAL.host]
    await make_user(SELLER)
    url = f"{CANDIDATES}/{site.id}/decision"
    decided = await client.post(url, json={"accept": False}, headers=bearer(await sign_in(SELLER)))
    assert decided.status_code == 200, decided.text
    await session.refresh(site)
    before = landed(site)
    outsider = "outsider@ours.example.test"
    await make_user(outsider, permissions={"sales": False})

    refused = await client.delete(url, headers=bearer(await sign_in(outsider)))

    assert (refused.status_code, refused.json()["detail"]) == (403, no_right(Permission.SALES))
    await session.refresh(site)
    assert landed(site) == before
    assert len(await _journal(session, site.id)) == 1


def _screen_fields(name: str) -> set[str]:
    """Поля интерфейса экрана `export interface <name> { … }` — файл читается как текст."""
    found = re.search(
        rf"export interface {name} \{{\n(.*?)\n\}}", SCREEN.read_text(encoding="utf-8"), re.DOTALL
    )
    assert found is not None, f"в SegmentSitesApi.ts нет интерфейса {name}"
    return set(re.findall(r"^  ([a-z_]+):", found.group(1), re.MULTILINE))


def test_the_screen_reads_the_row_by_the_server_names() -> None:
    """Поле, переименованное на сервере, экран показал бы пустым — без ошибки: кнопка «Отменить
    решение» молча не встала бы."""
    assert _screen_fields("SegmentSite") == set(SiteView.model_fields)
