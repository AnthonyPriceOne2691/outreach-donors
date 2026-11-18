"""Кнопки «Плана работ» (`sales/plan_actions.py`, `sales/plan_keys.py`) на настоящей базе.

Проверяется договор с конвейером: «Остановить» — `stopped` словами, новых прогонов нет, конвейер
идёт к следующей гипотезе; «Продолжить» — тот же прогон продолжается, выдача второй раз не
покупается, а очередь — «одна вперёд»; план с паузы снимает только кнопка; «Собрать ещё N» и
глубина — снова в работе или сразу «нужны ключи»; ключи текстом и книгой одним листом — той же
чисткой, что книга плана; отказы — словами, каждое действие — в журнале с автором. Две кнопки сразу —
на настоящих фиксациях: вторая ждёт замка и видит сделанное первой. Ключи — разных ниш и
письменностей, сайты выдуманы (`*.example.test`).
"""

from __future__ import annotations

import asyncio
import io
from datetime import datetime, timedelta
from typing import Any

import openpyxl
import pytest
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.sales import hypotheses, plan_actions, plan_keys
from backend.features.sales.models import SalesHypothesisModel
from backend.features.sales.plan_actions import (
    BUSY,
    NOTHING_TO_EDIT,
    PLAN_STOPPED,
    STOPPED_BY_HAND,
    PlanAskError,
    PlanStateError,
    Quality,
    UnknownPlanError,
)
from backend.features.sales.plan_budget import Problem, keys_words, problem
from backend.features.sales.plan_file import PlanFileError
from backend.features.sales.plan_models import (
    PlanStage,
    PlanStatus,
    SalesPlanHypothesisModel,
    SalesPlanKeywordModel,
)
from backend.features.sales.plan_moves import Did, Turn
from backend.features.sales.segment_estimate import NO_SEGMENT
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.test_sales_clean_api import _Jobs
from tests.test_sales_hypothesis_edit import _waits_on_a_lock
from tests.test_sales_plan_tick import (
    LATER,
    WORDS,
    codes,
    collecting,
    conveyor,
    jobs,
    runs_of,
    step,
    stopped_run,
)
from tests.test_sales_plan_waves import NOW, make_plan, sheet
from tests.test_sales_segment_estimate import _settings
from tests.test_send_race import MEETING, committed_sessions

__all__ = ["_settings", "conveyor", "jobs"]  # смета и конвейер — как у тестов прохода

Row = SalesPlanHypothesisModel
Key = SalesPlanKeywordModel
AUTHOR = "seller@ours.example.test"


@pytest.fixture
async def author(make_user: Any) -> UserModel:
    user: UserModel = await make_user(AUTHOR)
    return user


async def journal(session: AsyncSession) -> list[dict[str, Any]]:
    """«Действия» журнала по планам — с автором, по порядку."""
    found = await session.scalars(
        select(AuditLogModel)
        .where(AuditLogModel.target.like("sales_plan:%"))
        .order_by(AuditLogModel.id)
    )
    return [
        {
            "who": log.user_id,
            **{key: value for key, value in (log.details or {}).items() if key != "этап"},
        }
        for log in found.all()
    ]


async def name_of(session: AsyncSession, row: Row) -> str:
    found = await session.get(SalesHypothesisModel, row.hypothesis_id)
    assert found is not None
    return found.name


async def bought_out(session: AsyncSession, row: Row) -> None:
    """Каждый ключ гипотезы плана куплен до её предельной глубины: пачки нет ни в одной волне."""
    await session.execute(
        update(Key).where(Key.plan_hypothesis_id == row.id).values(pages=row.max_pages)
    )


def book_of(*sheets: list[list[object]]) -> bytes:
    """Книга в памяти: листы со строками по порядку."""
    made = openpyxl.Workbook()
    first = made.active
    assert first is not None
    made.remove(first)
    for number, lines in enumerate(sheets, 1):
        sheet = made.create_sheet(f"Лист {number}")
        for line in lines:
            sheet.append(line)
    out = io.BytesIO()
    made.save(out)
    return out.getvalue()


# --- остановить и продолжить гипотезу плана --------------------------------------------------


async def test_stop_stops_the_sheet_in_words_and_the_conveyor_goes_to_the_next(
    session: AsyncSession, jobs: _Jobs, author: UserModel
) -> None:
    plan = await make_plan(session)
    row = await collecting(session, plan)
    second = await sheet(session, plan, keys=WORDS)

    await plan_actions.stop_hypothesis(session, row.id, author_id=author.id, now=NOW)

    assert (row.stage, codes(row), row.problems[0]["words"]) == (
        "stopped",
        ["stopped"],
        STOPPED_BY_HAND,
    )
    turn = await step(session, jobs)

    [launched] = await runs_of(session, second)
    assert turn == Turn(Did.LAUNCHED, run_id=launched.id)
    assert await runs_of(session, row) == []
    assert await journal(session) == [
        {
            "who": author.id,
            "действие": plan_actions.ROW_STOPPED,
            "план": "План недели",
            "гипотеза": await name_of(session, row),
        }
    ]


@pytest.mark.parametrize(
    ("stage", "words"),
    [
        (PlanStage.STOPPED, "уже остановлена"),
        (PlanStage.SENDING, "уже набрала цель — новых прогонов у неё нет"),
        (PlanStage.DONE, "готова — первые письма ушли, останавливать нечего"),
    ],
)
async def test_a_stopped_sending_or_done_sheet_is_not_stopped_again(
    session: AsyncSession, stage: PlanStage, words: str
) -> None:
    row = await sheet(session)
    row.stage = stage.value
    await session.flush()

    with pytest.raises(PlanStateError, match=words):
        await plan_actions.stop_hypothesis(session, row.id, author_id=None, now=NOW)

    assert await journal(session) == []


async def test_resume_continues_the_same_stopped_run_and_buys_nothing_twice(
    session: AsyncSession, jobs: _Jobs
) -> None:
    """Прогон встал, гипотеза `stopped`. Человек поправил описание и нажал «Продолжить» — проход
    продолжает тот же прогон сразу, без паузы повтора: «Продолжить» — «попробовать снова»."""
    plan = await make_plan(session)
    row = await collecting(session, plan)
    run = await stopped_run(session, row, f"прогон остановлен: {NO_SEGMENT}", NOW)
    await step(session, jobs)
    assert (row.stage, codes(row)) == ("stopped", ["run_failed"])

    await plan_actions.resume_hypothesis(session, row.id, author_id=None, now=LATER)

    assert (row.stage, row.problems) == ("collecting", [])
    assert await step(session, jobs, LATER + timedelta(minutes=1)) == Turn(
        Did.RESUMED, run_id=run.id
    )
    assert [found.id for found in await runs_of(session, row)] == [run.id]


async def test_resume_waits_its_turn_while_another_sheet_collects(session: AsyncSession) -> None:
    """«Одна вперёд»: собирает одна гипотеза плана — продолженная ждёт очереди."""
    plan = await make_plan(session)
    first = await sheet(session, plan, keys=WORDS)
    second = await collecting(session, plan)
    first.stage, first.started_at = PlanStage.STOPPED.value, NOW
    first.problems = [problem(Problem.STOPPED, STOPPED_BY_HAND, NOW)]
    await session.flush()

    await plan_actions.resume_hypothesis(session, first.id, author_id=None, now=LATER)

    assert (first.stage, first.problems, second.stage) == ("waiting", [], "collecting")


async def test_resume_without_keys_and_depth_is_refused_in_need_keys_words(
    session: AsyncSession,
) -> None:
    row = await sheet(session, keys=WORDS, max_pages=2, target=300)
    row.stage, row.found = PlanStage.STOPPED.value, 120
    await bought_out(session, row)

    with pytest.raises(PlanStateError) as refused:
        await plan_actions.resume_hypothesis(session, row.id, author_id=None, now=NOW)

    assert str(refused.value).endswith(keys_words(2, 120, 300))
    assert row.stage == "stopped"


async def test_resuming_a_sheet_of_a_finished_plan_runs_the_plan_again(
    session: AsyncSession,
) -> None:
    plan = await make_plan(session)
    row = await sheet(session, plan, keys=WORDS)
    row.stage = PlanStage.STOPPED.value
    plan.status, plan.finished_at = PlanStatus.DONE.value, NOW
    await session.flush()

    await plan_actions.resume_hypothesis(session, row.id, author_id=None, now=LATER)

    assert (row.stage, plan.status) == ("collecting", "running")
    assert plan.finished_at is None


async def test_an_unknown_sheet_or_plan_is_said_in_words(session: AsyncSession) -> None:
    with pytest.raises(UnknownPlanError, match="гипотезы плана №404 нет"):
        await plan_actions.stop_hypothesis(session, 404, author_id=None, now=NOW)
    with pytest.raises(UnknownPlanError, match="плана №404 нет"):
        await plan_actions.stop_plan(session, 404, author_id=None)


# --- план ------------------------------------------------------------------------------------


async def test_the_plan_stops_and_only_the_button_resumes_it(
    session: AsyncSession, jobs: _Jobs, author: UserModel
) -> None:
    plan = await make_plan(session)
    await collecting(session, plan)

    await plan_actions.stop_plan(session, plan.id, author_id=author.id)

    assert (plan.status, plan.problem) == ("paused", PLAN_STOPPED)
    assert (await step(session, jobs)).did is Did.NOTHING
    with pytest.raises(PlanStateError, match="уже стоит: план остановлен кнопкой"):
        await plan_actions.stop_plan(session, plan.id, author_id=author.id)

    await plan_actions.resume_plan(session, plan.id, author_id=author.id)

    assert (plan.status, plan.problem) == ("running", None)
    assert (await step(session, jobs)).did is Did.LAUNCHED
    with pytest.raises(PlanStateError, match="уже идёт"):
        await plan_actions.resume_plan(session, plan.id, author_id=author.id)
    assert [(found["who"], found["действие"]) for found in await journal(session)] == [
        (author.id, plan_actions.PLAN_PAUSED),
        (author.id, plan_actions.PLAN_RESUMED),
    ]


async def test_a_finished_plan_is_neither_stopped_nor_resumed(session: AsyncSession) -> None:
    plan = await make_plan(session)
    plan.status = PlanStatus.DONE.value
    await session.flush()

    with pytest.raises(PlanStateError, match="окончен — останавливать нечего"):
        await plan_actions.stop_plan(session, plan.id, author_id=None)
    with pytest.raises(PlanStateError, match="продолжите гипотезу или соберите ещё"):
        await plan_actions.resume_plan(session, plan.id, author_id=None)


# --- собрать ещё и качество ------------------------------------------------------------------


async def test_more_raises_the_target_and_a_sending_sheet_collects_again(
    session: AsyncSession, author: UserModel
) -> None:
    row = await sheet(session, keys=WORDS, target=100)
    row.stage, row.found = PlanStage.SENDING.value, 100
    await session.flush()

    await plan_actions.collect_more(session, row.id, 50, author_id=author.id, now=NOW)

    assert (row.target, row.stage) == (150, "collecting")
    [said] = await journal(session)
    assert (said["действие"], said["было"], said["стало"]) == (plan_actions.MORE, 100, 150)


async def test_more_without_keys_and_depth_says_need_keys_at_once(session: AsyncSession) -> None:
    row = await sheet(session, keys=WORDS, max_pages=5, target=100)
    row.stage, row.found = PlanStage.DONE.value, 100
    await bought_out(session, row)

    await plan_actions.collect_more(session, row.id, 400, author_id=None, now=NOW)

    assert (row.target, row.stage, codes(row)) == (500, "stopped", ["keys"])
    assert row.problems[0]["words"] == keys_words(5, 100, 500)


async def test_more_beyond_the_largest_target_is_refused_in_words(session: AsyncSession) -> None:
    row = await sheet(session, target=999_990)

    with pytest.raises(PlanAskError, match="прибавить можно до 10"):
        await plan_actions.collect_more(session, row.id, 11, author_id=None, now=NOW)


async def test_quality_sets_depth_target_and_countries_with_their_journal(
    session: AsyncSession, author: UserModel
) -> None:
    row = await sheet(session, keys=WORDS, max_pages=1, target=100)

    await plan_actions.change_quality(
        session,
        row.id,
        Quality(max_pages=3, target=250, other_countries="accept", excluded_countries=["ru"]),
        author_id=author.id,
        now=NOW,
    )

    found = await session.get(SalesHypothesisModel, row.hypothesis_id)
    assert found is not None
    assert (row.max_pages, row.target) == (3, 250)
    assert (found.other_countries, found.excluded_countries) == ("accept", ["RU"])
    [said] = await journal(session)
    assert (said["было"], said["стало"]) == (
        "глубина до топ-10, цель 100",
        "глубина до топ-30, цель 250",
    )
    countries = await session.scalar(
        select(AuditLogModel.details["действие"].astext).where(
            AuditLogModel.target == f"sales_hypothesis:{row.hypothesis_id}"
        )
    )
    assert countries == hypotheses.COUNTRIES


async def test_deeper_search_puts_a_sheet_that_needed_keys_back_to_work(
    session: AsyncSession,
) -> None:
    row = await sheet(session, keys=WORDS, max_pages=2, target=300)
    row.stage = PlanStage.STOPPED.value
    row.problems = [problem(Problem.KEYS, keys_words(2, 0, 300), NOW)]
    await bought_out(session, row)

    await plan_actions.change_quality(
        session, row.id, Quality(max_pages=5), author_id=None, now=NOW
    )

    assert (row.stage, row.problems) == ("collecting", [])


async def test_quality_with_nothing_or_the_same_changes_nothing(session: AsyncSession) -> None:
    row = await sheet(session, max_pages=2, target=300)

    with pytest.raises(PlanAskError, match=NOTHING_TO_EDIT):
        await plan_actions.change_quality(session, row.id, Quality(), author_id=None, now=NOW)
    with pytest.raises(PlanAskError, match="глубина — одна из: топ-10, топ-20, топ-30, топ-50"):
        await plan_actions.change_quality(
            session, row.id, Quality(max_pages=4), author_id=None, now=NOW
        )
    await plan_actions.change_quality(
        session, row.id, Quality(max_pages=2, target=300), author_id=None, now=NOW
    )

    assert await journal(session) == []


# --- добавить ключи --------------------------------------------------------------------------


def test_keys_as_text_are_cleaned_like_the_keys_of_the_book() -> None:
    found = plan_keys.keys_of_text(
        "  Ремонт Кофемашин \n\nремонт кофемашин\n家具 批发\nsite:shop.example.test\n"
    )

    assert found.taken == ("ремонт кофемашин", "家具 批发")
    assert list(found.rejected) == ["site:shop.example.test"]


def test_a_book_of_one_sheet_gives_keys_below_the_keys_row() -> None:
    data = book_of([["Название", "Мебель"], ["Ключи"], ["家具 批发"], [None], ["dental implants"]])

    assert plan_keys.keys_of_book(data, "ключи.xlsx").taken == ("家具 批发", "dental implants")
    assert plan_keys.keys_of_book(book_of([["طباعة"]]), "k.xlsx").taken == ("طباعة",)


def test_a_book_of_two_sheets_is_refused_in_words() -> None:
    with pytest.raises(PlanFileError, match="листов: 2 — ключи берутся из книги одним листом"):
        plan_keys.keys_of_book(book_of([["a"]], [["b"]]), "ключи.xlsx")


async def test_added_keys_go_after_the_sheet_on_every_market_and_the_sheet_works_again(
    session: AsyncSession, author: UserModel
) -> None:
    row = await sheet(session, markets=("de", "at"), keys=WORDS, max_pages=1, target=300)
    row.stage = PlanStage.STOPPED.value
    row.problems = [problem(Problem.KEYS, keys_words(1, 0, 300), NOW)]
    await bought_out(session, row)

    added = await plan_keys.add_keys(
        session,
        row.id,
        plan_keys.keys_of_text("Fußpflege Praxis\nремонт кофемашин\nsite:shop.example.test"),
        author_id=author.id,
        now=NOW,
    )

    assert added == plan_keys.Added(added=1, known=1, rejected=1)
    found = await session.execute(
        select(Key.market, Key.keyword, Key.position, Key.pages).where(
            Key.plan_hypothesis_id == row.id, Key.pages == 0
        )
    )
    assert sorted(found.tuples().all()) == [
        ("at", "fußpflege praxis", 4, 0),
        ("de", "fußpflege praxis", 4, 0),
    ]
    assert (row.stage, row.problems) == ("collecting", [])
    [said] = await journal(session)
    assert (said["действие"], said["ключей"], said["уже были"]) == (plan_keys.KEYS_ADDED, 1, 1)


async def test_keys_the_sheet_already_has_are_refused_in_words(session: AsyncSession) -> None:
    row = await sheet(session, keys=WORDS)

    with pytest.raises(PlanAskError, match="новых ключей нет: все 3 уже есть у гипотезы"):
        await plan_keys.add_keys(
            session, row.id, plan_keys.keys_of_text("\n".join(WORDS)), author_id=None, now=NOW
        )
    with pytest.raises(PlanAskError, match="ключей нет"):
        await plan_keys.add_keys(
            session, row.id, plan_keys.keys_of_text(" \n"), author_id=None, now=NOW
        )


# --- две кнопки сразу: на настоящих фиксациях ------------------------------------------------


async def _committed_sheet(factory: async_sessionmaker[AsyncSession]) -> tuple[int, str]:
    """Лист плана — зафиксирован; номер строки и имя гипотезы."""
    async with factory() as session:
        row = await sheet(session, keys=WORDS, target=100)
        found = await session.get(SalesHypothesisModel, row.hypothesis_id)
        assert found is not None
        await session.commit()
        return row.id, found.name


async def _more(factory: async_sessionmaker[AsyncSession], row_id: int, now: datetime) -> None:
    async with factory() as session:
        await plan_actions.collect_more(session, row_id, 10, author_id=None, now=now)
        await session.commit()


async def test_a_second_press_waits_for_the_first_and_sees_what_it_did() -> None:
    """Две «Собрать ещё 10» сразу: вторая ждёт замка строки, читает цель после первой — обе
    прибавки на месте. Две «Остановить» сразу: одна остановила, вторая отказала словами."""
    async with committed_sessions() as factory:
        row_id, name = await _committed_sheet(factory)
        async with factory() as first:
            await plan_actions.collect_more(first, row_id, 10, author_id=None, now=NOW)
            second = asyncio.create_task(_more(factory, row_id, NOW))
            assert await _waits_on_a_lock(factory)
            await first.commit()
        await asyncio.wait_for(second, MEETING)

        async def stop() -> str:
            async with factory() as session:
                await plan_actions.stop_hypothesis(session, row_id, author_id=None, now=NOW)
                await session.commit()
                return "остановлена"

        results = await asyncio.gather(stop(), stop(), return_exceptions=True)

        async with factory() as session:
            row = await session.get(Row, row_id)
            assert row is not None
            assert (row.target, row.stage) == (120, "stopped")
            stops = await session.scalars(
                select(AuditLogModel.id).where(
                    AuditLogModel.details["действие"].astext == plan_actions.ROW_STOPPED
                )
            )
            assert len(stops.all()) == 1
        assert sorted(map(str, results)) == sorted(
            ["остановлена", f"гипотеза «{name}» уже остановлена"]
        )


async def test_a_row_held_longer_than_the_limit_is_refused_in_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Строку плана держит проход конвейера дольше предела — кнопка не висит, а отказывает
    словами, и её транзакция жива."""
    monkeypatch.setattr(hypotheses, "LOCK_WAIT", "SET LOCAL lock_timeout = '200ms'")
    async with committed_sessions() as factory:
        row_id, _ = await _committed_sheet(factory)
        async with factory() as tick:
            await tick.execute(
                text(
                    "SELECT id FROM sales_plans WHERE id = "
                    "(SELECT plan_id FROM sales_plan_hypotheses WHERE id = :id) FOR UPDATE"
                ),
                {"id": row_id},
            )
            async with factory() as pressing:
                with pytest.raises(PlanStateError) as refused:
                    await asyncio.wait_for(
                        plan_actions.stop_hypothesis(pressing, row_id, author_id=None, now=NOW),
                        MEETING,
                    )
                assert await pressing.scalar(text("SELECT 1")) == 1
            await tick.rollback()

        assert str(refused.value) == BUSY
