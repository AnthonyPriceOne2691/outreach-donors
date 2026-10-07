"""Этап продаж в общей почте: экраны, главная, отбор — срез 1.1b, часть «в».

Сервер может отдать этап продаж там, где этап — свойство строки: диалог,
сводка писем по этапам, стоп-лист. Экран обязан знать это значение (`Stage`
в `api/stages.ts`), иначе он молча показывает продажи донорскими словами.
Экраны почты знают только этапы, которые почта ведёт (`LetterStage` —
`core.stages.MailStage`): подключат продажи к почте — сверка потребует их и
там. Числа главной не смешивают продажи с донорами: ответ лида — не
«Разобрать цены» и не «Лиды рекламодателей».
"""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path
from typing import get_args

import pytest
from backend.api.letters.routes import _STAGE_TITLES
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.stages import MailStage
from backend.features.ops.overview import overview
from backend.features.outreach.threads import ThreadState
from backend.features.replies.outcome import SALES_WAITING
from backend.features.runs.exclusions import ExclusionReason, Exclusions
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_donor
from tests.test_sales_stage_mail import NOW, admin_token, sales_world

__all__ = ["admin_token"]  # фикстура общая с частью «а»

STAGES = {stage.value for stage in Stage}
MAIL_STAGES = {stage.value for stage in get_args(MailStage)}
THREAD_STATES = {state.value for state in ThreadState}
API_TS = Path(__file__).resolve().parent.parent / "frontend" / "src" / "api"


def _union(name: str, file: str) -> set[str]:
    """Значения строкового объединения из файла типов фронта."""
    source = (API_TS / file).read_text(encoding="utf-8")
    found = re.search(rf"export type {name} =([^;]+);", source)
    assert found is not None, f"в {file} нет типа {name}"
    return set(re.findall(r"'([a-z_]+)'", found.group(1)))


def _keys(constant: str, file: str) -> set[str]:
    """Ключи объекта-словаря из файла подписей фронта."""
    source = (API_TS / file).read_text(encoding="utf-8")
    found = re.search(rf"export const {constant}\b[^=]*=\s*\{{(.*?)\n\}};", source, re.S)
    assert found is not None, f"в {file} нет {constant}"
    return set(re.findall(r"^\s*([a-z_]+):", found.group(1), re.M))


# --- слова экрана — коды сервера --------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "file", "values"),
    [
        ("Stage", "stages.ts", STAGES),
        ("LetterStage", "types.ts", MAIL_STAGES),
        ("ThreadState", "types.ts", THREAD_STATES),
    ],
)
def test_front_types_are_the_server_codes(name: str, file: str, values: set[str]) -> None:
    assert _union(name, file) == values


@pytest.mark.parametrize(
    ("constant", "file", "values"),
    [
        ("THREAD_STATES", "labels.ts", THREAD_STATES),
        ("THREAD_STAGE_NOTES", "stages.ts", STAGES),
    ],
)
def test_front_labels_name_every_stage_and_thread_state(
    constant: str, file: str, values: set[str]
) -> None:
    assert _keys(constant, file) == values


@pytest.mark.parametrize("stage", list(Stage))
def test_audit_names_every_stage_in_words(stage: Stage) -> None:
    """Словарь здесь падал `KeyError` уже после постановки сборки в очередь."""
    assert _STAGE_TITLES[stage]


def test_audit_calls_sales_by_name() -> None:
    assert _STAGE_TITLES[Stage.SALES] == "продажи"


# --- главная ---------------------------------------------------------------------------------


async def test_sales_answer_is_neither_a_price_nor_a_lead_on_the_main_page(
    session: AsyncSession,
) -> None:
    """Домен лида — заодно принятый донор: числа доноров его письмо и ответ не трогают."""
    await sales_world(session, status=MessageStatus.DELIVERED)

    view = await overview(session, now=NOW)

    assert (view.waiting.prices, view.waiting.leads) == (0, 0)
    assert (view.donors.written, view.donors.replied) == (0, 0)
    sales = view.letters[Stage.SALES]
    assert (sales.sent, sales.delivered) == (1, 1)
    assert view.letters[Stage.DONORS].sent == 0


async def test_main_page_letters_carry_every_stage(
    session: AsyncSession, client: AsyncClient, admin_token: str
) -> None:
    response = await client.get("/api/overview", headers=bearer(admin_token))

    assert response.status_code == 200
    assert set(response.json()["letters"]) == STAGES


# --- диалоги -----------------------------------------------------------------------------------


async def test_dialogs_show_a_sales_thread_with_its_own_state_and_reason(
    session: AsyncSession, client: AsyncClient, admin_token: str
) -> None:
    world = await sales_world(session, status=MessageStatus.DELIVERED)
    await session.commit()

    listed = await client.get("/api/threads", headers=bearer(admin_token))
    one = await client.get(f"/api/threads/{world.thread.id}", headers=bearer(admin_token))

    (card,) = [row for row in listed.json() if row["id"] == world.thread.id]
    assert (card["stage"], card["state"]) == ("sales", "sales_pending")
    (incoming,) = one.json()["incoming"]
    assert (incoming["needs_review"], incoming["review_reason"]) == (True, SALES_WAITING)
    assert incoming["lead"] is False


# --- отбор прогона ---------------------------------------------------------------------------


async def test_run_selection_skips_donor_answers_for_sales_like_for_advertisers(
    session: AsyncSession,
) -> None:
    """«Не продаём» и «отклонён» — ответы про донорство; продажам письмо о другом."""
    domain = await make_donor(session, "declined.example.test", review="rejected")
    domain.seller_answer = "declines"
    domain.seller_answer_at = NOW - timedelta(days=1)
    await session.flush()
    exclusions = Exclusions(session)
    hosts = ["declined.example.test"]

    for_donors = await exclusions.excluded_hosts(hosts, stage=Stage.DONORS, now=NOW)
    for_sales = await exclusions.excluded_hosts(hosts, stage=Stage.SALES, now=NOW)
    for_advertisers = await exclusions.excluded_hosts(hosts, stage=Stage.ADVERTISERS, now=NOW)

    assert for_donors == {"declined.example.test": ExclusionReason.REJECTED}
    assert for_sales == for_advertisers == {}
