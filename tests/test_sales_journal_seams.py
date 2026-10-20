"""Журнал действий и раздел «Продажи»: что пишется и прячется ли от учётки без права.

Ревью стыков, круг 2 (журнал действий, #341). Журнал читают «Учётки» (право «заводить
учётки»), и без права «Продажи» действий продаж в нём нет (П2, `access/journal.sales_entry`):
запись узнаётся по этапу в подробностях, по действию, которое бывает только у продаж, по цели
модуля продаж («sales_…») — и у прежних записей по словам «действия» («…продаж»). Слова правят:
запись, которая держится только на них, после правки слов показала бы действие продаж всем.

- **Каждая запись модуля продаж узнаётся без слов** — этапом (`stage=`), действием продаж или
  целью «sales_…»: проверка обходом исходников модуля, новая запись в журнал проверяется сама.
- **Решение человека по сайту сбора «на проверку»** — в журнале, как решение по сайту на
  «Отборе»: кто, какой сайт, что решил и что говорил судья сегмента; без права — не видно.
- **Передача лида телемаркетологу** — в журнале один раз на диалог, без автора (её начинает
  разбор ответа), с этапом продаж: по журналу видно, когда лид ушёл в Kommo и Telegram.
- **Заведение гипотезы и правка описания сегмента** (решение владельца 13.10) — в журнале: кто,
  гипотеза, а у правки — было и стало. Описание — вопрос судье сбора базы, по нему решается, каким
  сайтам продажи напишут. Своего действия журнала нет (новое значение перечисления — миграция):
  общая «правка» со словами «действия», цель — гипотеза, этап — в записи; без права не видно.
"""

from __future__ import annotations

import ast
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from backend.cli.sales import EXIT_OK, run_hypothesis_add
from backend.features.access.journal import SALES_ACTIONS, JournalQuery, read_journal
from backend.features.core.domain import AuditAction, Stage
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.sales import handoff, hypotheses
from backend.features.sales.segment_report import LANE_WORDS
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_sales_handoff_rows import SENT_AT, answer, sales_dialog
from tests.test_sales_segment_api import _review_site
from tests.test_sales_switch import sales_switched_on

__all__ = ["sales_switched_on"]  # фикстура «продажи включены» — отсюда её видит pytest

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

ROOT = Path(__file__).resolve().parent.parent
#: Модуль продаж: где он пишет в журнал.
SECTION = (
    "backend/features/sales",
    "backend/api/sales",
    "backend/cli",
    "backend/workers/sales_jobs.py",
)
SELLER = "seller@ours.example.test"


# --- каждая запись модуля узнаётся без слов -------------------------------------------------------


def _files() -> list[Path]:
    found: list[Path] = []
    for place in SECTION:
        path = ROOT / place
        if path.is_file():
            found.append(path)
        elif place == "backend/cli":
            found += sorted(path.glob("sales*.py"))
        else:
            found += sorted(path.rglob("*.py"))
    return found


def _writes(tree: ast.AST) -> list[ast.Call]:
    """Вызовы `AccessRepository(…).record(…)` — записи в журнал действий."""
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "record"
        and isinstance(node.func.value, ast.Call)
        and isinstance(node.func.value.func, ast.Name)
        and node.func.value.func.id == "AccessRepository"
    ]


def _sales_action(call: ast.Call) -> bool:
    first = call.args[0] if call.args else None
    return (
        isinstance(first, ast.Attribute)
        and isinstance(first.value, ast.Name)
        and first.value.id == "AuditAction"
        and AuditAction[first.attr] in SALES_ACTIONS
    )


def _sales_target(call: ast.Call) -> bool:
    """Цель модуля продаж — строкой «sales_…» или f-строкой, которая с неё начинается."""
    target = next((one.value for one in call.keywords if one.arg == "target"), None)
    if isinstance(target, ast.JoinedStr) and target.values:
        target = target.values[0]
    return (
        isinstance(target, ast.Constant)
        and isinstance(target.value, str)
        and target.value.startswith("sales_")
    )


def _knows_sales(call: ast.Call) -> bool:
    staged = any(one.arg == "stage" for one in call.keywords)
    return staged or _sales_action(call) or _sales_target(call)


def test_every_journal_entry_of_the_section_is_known_as_sales_without_its_words() -> None:
    """Запись, которую журнал узнаёт только по «действию» («…продаж»), — этапом в записи."""
    calls = {}
    for path in _files():
        for call in _writes(ast.parse(path.read_text(encoding="utf-8"))):
            calls[f"{path.relative_to(ROOT)}:{call.lineno}"] = _knows_sales(call)

    assert len(calls) >= 10, "обход не нашёл записей модуля — путь к модулю сменился?"
    assert [place for place, known in calls.items() if not known] == []


# --- решение человека по сайту сбора ------------------------------------------------------------


async def _decided(session: AsyncSession) -> list[AuditLogModel]:
    found = await session.scalars(
        select(AuditLogModel)
        .where(AuditLogModel.action == AuditAction.SITE_REVIEWED)
        .order_by(AuditLogModel.id)
    )
    return list(found)


async def test_a_decision_on_a_collected_site_is_journaled_with_the_sales_stage(
    session: AsyncSession, client: AsyncClient, make_user: MakeUser, sign_in: SignIn
) -> None:
    run, candidate = await _review_site(session)
    seller = await make_user(SELLER)
    headers = bearer(await sign_in(SELLER))
    url = f"/api/sales/segment/candidates/{candidate.id}/decision"

    taken = await client.post(url, json={"accept": False}, headers=headers)
    again = await client.post(url, json={"accept": True}, headers=headers)

    assert (taken.status_code, again.status_code) == (200, 409), taken.text
    [entry] = await _decided(session)  # отказ «уже решено» в журнал не пишет
    assert (entry.user_id, entry.target) == (seller.id, f"sales_segment_candidate:{candidate.id}")
    assert entry.details == {
        "этап": Stage.SALES.value,
        "действие": "решение по сайту сбора базы",
        "домен": "velo-maybe-sample.com",
        "решение": "rejected",
        # Из какой полосы решили: «на проверке» или «судья отказал бы» (вариант (b)).
        "было": LANE_WORDS["review"],
        "судья сегмента": LANE_WORDS["reject"],
        "прогон": run.id,
    }


async def test_without_the_right_the_decision_is_not_in_the_journal(
    session: AsyncSession, client: AsyncClient, make_user: MakeUser, sign_in: SignIn
) -> None:
    _, candidate = await _review_site(session)
    await make_user(SELLER)
    headers = bearer(await sign_in(SELLER))
    url = f"/api/sales/segment/candidates/{candidate.id}/decision"
    assert (await client.post(url, json={"accept": True}, headers=headers)).status_code == 200

    hidden = await read_journal(
        session, JournalQuery(action=AuditAction.SITE_REVIEWED, with_sales=False)
    )
    shown = await read_journal(session, JournalQuery(action=AuditAction.SITE_REVIEWED))

    assert (hidden.total, shown.total) == (0, 1)


# --- передача лида телемаркетологу --------------------------------------------------------------


async def _handed(session: AsyncSession, handoff_id: int) -> list[AuditLogModel]:
    found = await session.scalars(
        select(AuditLogModel)
        .where(
            AuditLogModel.action == AuditAction.LEAD_TAKEN,
            AuditLogModel.target == f"sales_handoff:{handoff_id}",
        )
        .order_by(AuditLogModel.id)
    )
    return list(found)


@pytest.mark.usefixtures("sales_switched_on")
async def test_a_handoff_is_journaled_once_per_dialog_with_the_sales_stage(
    session: AsyncSession,
) -> None:
    """Передачу начинают три пути (вид ответа, понимание агента, письма переданного лида) и
    повторяют на каждом новом ответе — в журнале она одна: лид ушёл телемаркетологу."""
    dialog = await sales_dialog(session)
    queued: list[int] = []

    row = await handoff.start(session, dialog.thread.id, enqueue=queued.append)
    await handoff.start(session, dialog.thread.id, enqueue=queued.append)
    await answer(
        session, dialog.thread, dialog.message, "И ещё вопрос.", at=SENT_AT.replace(day=15)
    )
    await handoff.start(session, dialog.thread.id, enqueue=queued.append)

    [entry] = await _handed(session, row.id)
    assert entry.user_id is None
    assert entry.details == {
        "этап": Stage.SALES.value,
        "действие": "лид передан телемаркетологу — сделка в Kommo и сообщение в Telegram",
        "лид": dialog.lead.id,
        "диалог": dialog.thread.id,
        "ответ": dialog.reply.id,
    }
    hidden = await read_journal(
        session, JournalQuery(action=AuditAction.LEAD_TAKEN, with_sales=False)
    )
    assert hidden.total == 0


# --- гипотеза и описание сегмента (решение владельца 13.10) -------------------------------------

HYPOTHESES = "/api/sales/hypotheses"
SEGMENT = "описание сегмента"


async def _of_hypotheses(session: AsyncSession) -> list[AuditLogModel]:
    """Записи о гипотезах, кроме загрузки базы в гипотезу (у неё своё действие)."""
    found = await session.scalars(
        select(AuditLogModel)
        .where(
            AuditLogModel.action == AuditAction.USER_UPDATED,
            AuditLogModel.target.like("sales\\_hypothesis:%", escape="\\"),
        )
        .order_by(AuditLogModel.id)
    )
    return list(found)


async def test_a_new_hypothesis_is_journaled_with_its_author_and_the_sales_stage(
    session: AsyncSession, client: AsyncClient, make_user: MakeUser, sign_in: SignIn
) -> None:
    seller = await make_user(SELLER)
    headers = bearer(await sign_in(SELLER))

    made = await client.post(
        HYPOTHESES,
        json={"name": " сайты  EN ", "description": " редакции и блоги "},
        headers=headers,
    )
    taken = await client.post(HYPOTHESES, json={"name": "сайты EN"}, headers=headers)
    nameless = await client.post(HYPOTHESES, json={"name": "   "}, headers=headers)

    assert (made.status_code, taken.status_code, nameless.status_code) == (201, 409, 422)
    [entry] = await _of_hypotheses(session)  # отказ заводить в журнал не пишет
    assert (entry.user_id, entry.target) == (seller.id, f"sales_hypothesis:{made.json()['id']}")
    assert entry.details == {
        "этап": Stage.SALES.value,
        "действие": hypotheses.MADE,
        "имя": "сайты EN",
        "описание сегмента": "редакции и блоги",
        # Поле новой гипотезы — «на проверку» и пустой «не пишем» (решение владельца 20.10).
        "страны компаний": "компании из других стран — на проверку; не пишем компаниям из: —",
    }


async def test_a_hypothesis_from_the_console_is_journaled_without_an_author(
    session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    """У заведения два пути — окно экрана и консоль (урок L63): журнал пишут оба."""
    assert await run_hypothesis_add(session, "сайты DE", None) == EXIT_OK
    capsys.readouterr()

    [entry] = await _of_hypotheses(session)
    assert entry.user_id is None
    assert entry.details == {
        "этап": Stage.SALES.value,
        "действие": hypotheses.MADE,
        "имя": "сайты DE",
        "описание сегмента": None,
        # Поле новой гипотезы — «на проверку» и пустой «не пишем» (решение владельца 20.10).
        "страны компаний": "компании из других стран — на проверку; не пишем компаниям из: —",
    }


async def test_a_segment_edit_is_journaled_with_who_the_hypothesis_what_was_and_what_became(
    session: AsyncSession, client: AsyncClient, make_user: MakeUser, sign_in: SignIn
) -> None:
    """«Было» и «стало» — своими строками; пустое экран журнала не выводит, поэтому «действие»
    говорит само: описание задано, поправлено или снято. То же описание (с пробелами по краям) —
    не правка: записи нет. Гипотезы нет — 404, записи тоже нет."""
    seller = await make_user(SELLER)
    headers = bearer(await sign_in(SELLER))
    made = (await client.post(HYPOTHESES, json={"name": "сайты EN"}, headers=headers)).json()
    url = f"{HYPOTHESES}/{made['id']}"

    said = [
        (await client.patch(url, json={"description": text}, headers=headers)).status_code
        for text in ("  Магазины самокатов. ", " Магазины самокатов.  ", "Прокат самокатов.", " ")
    ]
    unknown = await client.patch(
        f"{HYPOTHESES}/987654", json={"description": "Прокат."}, headers=headers
    )

    assert (said, unknown.status_code) == ([200] * 4, 404)
    edits = [
        entry
        for entry in await _of_hypotheses(session)
        if str((entry.details or {}).get("действие")).startswith(SEGMENT)
    ]
    assert [(entry.user_id, entry.target) for entry in edits] == [
        (seller.id, f"sales_hypothesis:{made['id']}")
    ] * 3
    assert [entry.details for entry in edits] == [
        {
            "этап": Stage.SALES.value,
            "действие": f"{SEGMENT} {what}",
            "гипотеза": "сайты EN",
            "было": was,
            "стало": became,
        }
        for what, was, became in (
            ("задано", None, "Магазины самокатов."),
            ("поправлено", "Магазины самокатов.", "Прокат самокатов."),
            ("снято", "Прокат самокатов.", None),
        )
    ]


async def test_without_the_right_the_hypothesis_and_its_segment_are_not_in_the_journal(
    session: AsyncSession, client: AsyncClient, make_user: MakeUser, sign_in: SignIn
) -> None:
    await make_user(SELLER)
    headers = bearer(await sign_in(SELLER))
    made = (await client.post(HYPOTHESES, json={"name": "сайты EN"}, headers=headers)).json()
    url = f"{HYPOTHESES}/{made['id']}"
    assert (await client.patch(url, json={"description": "Прокат."}, headers=headers)).is_success

    hidden = await read_journal(
        session, JournalQuery(action=AuditAction.USER_UPDATED, with_sales=False)
    )
    shown = await read_journal(session, JournalQuery(action=AuditAction.USER_UPDATED))

    target = f"sales_hypothesis:{made['id']}"
    assert [entry.target for entry in hidden.entries if entry.target == target] == []
    assert [entry.target for entry in shown.entries if entry.target == target] == [target] * 2
