"""Калибровка агента продаж — числа экрана «Агент: качество» и ворот автоответа (срез 6.4).

Журнал подложен на настоящей базе дерева: черновики продаж всех видов решений, две версии
промпта, черновик доноров рядом. Проверяется то, ради чего числа:

- доли — от решений оператора по готовым черновикам, а не от всех черновиков: ждущие,
  отданные человеку, молчание и автоотправка в основу не входят;
- когорты (версия промптов и базы знаний) не смешиваются;
- меньше N решений — «данных мало», без доли и вывода; ровно N — уже вывод;
- пороги ворот — «не ниже»: ровно 80% без правки и ровно 90% верного молчания проходят;
- ложное молчание — агент сказал «ответ не нужен», а человек ответил; молчание человека —
  исход только через срок после письма.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any

import pytest
from backend.config import sales as sales_cfg
from backend.features.agent.drafts import AUTOPILOT
from backend.features.agent.settings import AgentSettingsRepository
from backend.features.core.domain import DraftStatus, Stage
from backend.features.core.models.agent import AgentDraftModel
from backend.features.core.models.outreach import ReplyModel
from backend.features.sales import kb
from backend.features.sales.agent import brief, calibration, calibration_board, parts
from backend.features.sales.agent.calibration import (
    Agent,
    Bucket,
    Cohort,
    GateState,
    Limits,
    Row,
    Took,
    gate,
    percent,
    tally,
)
from backend.features.sales.agent.calibration_board import Piece, word_diff
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_replay import answered, lead_reply

NOW = datetime.now(UTC)
OLD = Cohort("sales-reply-old-test", "sit-1", "judge-1", "moves-1", "kb-0a1b2c3d4e5f")
NEW = Cohort("sales-reply-new-test", "sit-1", "judge-1", "moves-1", "kb-0a1b2c3d4e5f")

# --- ряды без базы -------------------------------------------------------------------------

_numbers = iter(range(1, 10_000))


def row(
    agent: Agent,
    took: Took,
    *,
    situation: str | None = "asks_price",
    cohort: Cohort = NEW,
    replied: bool | None = None,
    settled: bool = True,
    kind: str | None = None,
) -> Row:
    """Черновик журнала: что сделал агент и что — человек."""
    said = took in calibration.REPLIED if replied is None else replied
    return Row(
        draft_id=next(_numbers),
        thread_id=None,
        situation=situation,
        cohort=cohort,
        agent=agent,
        took=took,
        replied=said,
        settled=settled,
        written_at=NOW,
        reject_kind=kind,
    )


def rows_of(**counts: int) -> list[Row]:
    """Готовые черновики с решениями и молчания агента — числами по местам."""
    made = {
        "as_is": (Agent.READY, Took.AS_IS),
        "edited": (Agent.READY, Took.EDITED),
        "rejected": (Agent.READY, Took.REJECTED),
        "true_silence": (Agent.SILENT, Took.SILENT),
        "false_silence": (Agent.SILENT, Took.OWN),
    }
    return [row(*made[name]) for name, count in counts.items() for _ in range(count)]


EDGE = Limits(min_n=30)


def test_shares_are_of_operator_decisions_not_of_all_drafts() -> None:
    """Ждущие, отданные человеку, молчание и автоотправка — вне основы «без правки»."""
    found = tally(
        [
            *rows_of(as_is=24, edited=4, rejected=2),
            row(Agent.READY, Took.PENDING),
            row(Agent.HELD, Took.EDITED),
            row(Agent.HELD, Took.REJECTED),
            row(Agent.HELD, Took.PENDING),
            row(Agent.SILENT, Took.PENDING, settled=False),
            row(Agent.READY, Took.AUTO),
        ]
    )

    assert (found.decisions, found.drafts) == (30, 36)
    assert dict(found.buckets) == {
        Bucket.AS_IS: 24,
        Bucket.EDITED: 4,
        Bucket.REJECTED: 2,
        Bucket.WAITING: 2,
        Bucket.HELD_SENT: 1,
        Bucket.HELD_REJECTED: 1,
        Bucket.HELD_WAITING: 1,
        Bucket.AUTO: 1,
    }
    # 24 из 30 решений — ровно порог; от всех черновиков было бы 24 из 36 — ниже.
    assert gate(found, EDGE) == calibration.Gate(
        GateState.PASSED, ("без правки 80% (24 из 30) — не ниже 80%",)
    )


@pytest.mark.parametrize(
    ("counts", "state", "said"),
    [
        (
            {"as_is": 24, "edited": 6},
            GateState.PASSED,
            ("без правки 80% (24 из 30) — не ниже 80%",),
        ),
        (
            {"as_is": 23, "edited": 7},
            GateState.FAILED,
            ("без правки 76,7% (23 из 30) — ниже 80%",),
        ),
        (
            {"as_is": 29},
            GateState.FEW,
            ("данных мало: решений по готовым черновикам 29 из 30",),
        ),
        (
            {"true_silence": 27, "false_silence": 3},
            GateState.PASSED,
            ("верное молчание 90% (27 из 30) — не ниже 90%",),
        ),
        (
            {"true_silence": 26, "false_silence": 4},
            GateState.FAILED,
            ("верное молчание 86,7% (26 из 30) — ниже 90%",),
        ),
        (
            {"true_silence": 29},
            GateState.FEW,
            ("данных мало: молчаний агента с исходом 29 из 30",),
        ),
        ({}, GateState.FEW, ("данных мало: решений нет, нужно не меньше 30",)),
    ],
)
def test_gate_passes_at_the_threshold_and_fails_below_it(
    counts: dict[str, int], state: GateState, said: tuple[str, ...]
) -> None:
    assert gate(tally(rows_of(**counts)), EDGE) == calibration.Gate(state, said)


def test_too_few_decisions_give_no_share_and_no_verdict_until_n() -> None:
    """29 решений «как есть» из 29 — всё ещё «данных мало»: вывода по горсти нет."""
    few = gate(tally(rows_of(as_is=29)), EDGE)
    enough = gate(tally(rows_of(as_is=30)), EDGE)

    assert few.state is GateState.FEW
    assert not any("%" in said for said in few.said)
    assert enough == calibration.Gate(
        GateState.PASSED, ("без правки 100% (30 из 30) — не ниже 80%",)
    )


def test_a_failure_on_a_full_base_is_said_even_if_the_other_base_is_small() -> None:
    found = tally(rows_of(as_is=10, edited=20, true_silence=3))

    assert gate(found, EDGE) == calibration.Gate(
        GateState.FAILED,
        (
            "без правки 33,3% (10 из 30) — ниже 80%",
            "данных мало: молчаний агента с исходом 3 из 30",
        ),
    )


def test_both_numbers_pass_and_both_are_said() -> None:
    found = tally(rows_of(as_is=40, edited=2, true_silence=30))

    assert gate(found, EDGE).said == (
        "без правки 95,2% (40 из 42) — не ниже 80%",
        "верное молчание 100% (30 из 30) — не ниже 90%",
    )


@pytest.mark.parametrize(
    ("part", "base", "floor", "shown"),
    [
        (24, 30, 80, "80%"),
        (239, 300, 80, "79,7%"),
        (3999, 5000, 80, "79,98%"),
        (89_999, 100_000, 90, "89,999%"),
        (1, 3, 80, "33,3%"),
    ],
)
def test_share_near_the_threshold_stays_on_its_side(
    part: int, base: int, floor: int, shown: str
) -> None:
    assert percent(part, base, floor) == shown


def test_false_silence_is_the_agent_silent_and_the_human_answered() -> None:
    found = tally(
        [
            row(Agent.SILENT, Took.SILENT),
            row(Agent.SILENT, Took.OWN),
            row(Agent.SILENT, Took.AS_IS),  # «всё же написать» поверх молчания — и ушло
            row(Agent.SILENT, Took.REJECTED, replied=True),  # отклонил, а ответил сам
            row(Agent.SILENT, Took.REJECTED, replied=False),  # отклонил и промолчал
            row(Agent.SILENT, Took.REJECTED, replied=False, settled=False),  # ещё не исход
        ]
    )

    assert (found[Bucket.TRUE_SILENCE], found[Bucket.FALSE_SILENCE]) == (2, 3)
    assert found[Bucket.WAITING] == 1
    assert found.decisions == 0


def test_rejection_reasons_count_only_ready_drafts_by_kind() -> None:
    found = tally(
        [
            row(Agent.READY, Took.REJECTED, kind="длинно"),
            row(Agent.READY, Took.REJECTED, kind="длинно"),
            row(Agent.READY, Took.REJECTED, kind="другое"),
            row(Agent.READY, Took.REJECTED),
            row(Agent.HELD, Took.REJECTED, kind="длинно"),
        ]
    )

    assert dict(found.reasons) == {"длинно": 2, "другое": 1, calibration.NO_KIND: 1}
    assert calibration_board.reasons(found)[:2] == (
        ("неверная ситуация", 0),
        ("факт не из базы", 0),
    )
    assert [name for name, _ in calibration_board.reasons(found)] == [
        *parts.REJECT_REASONS,
        "другое",
        calibration.NO_KIND,
    ]


# --- что сделал агент и человек — по строке черновика ------------------------------------------


def draft(status: DraftStatus, **values: Any) -> AgentDraftModel:
    found = {"body": "Черновик агента.", "meta": {}, "reason": None, **values}
    return AgentDraftModel(status=status, **found)


@pytest.mark.parametrize(
    ("made", "agent", "took"),
    [
        (draft(DraftStatus.DRAFTED), Agent.READY, Took.PENDING),
        (draft(DraftStatus.SENT, edited=False), Agent.READY, Took.AS_IS),
        (draft(DraftStatus.SENT, edited=True), Agent.READY, Took.EDITED),
        (draft(DraftStatus.REJECTED), Agent.READY, Took.REJECTED),
        (draft(DraftStatus.ESCALATED, reason="судья: сумма"), Agent.HELD, Took.PENDING),
        (draft(DraftStatus.SENT, reason="судья: сумма", edited=True), Agent.HELD, Took.EDITED),
        (
            draft(DraftStatus.SENT, body="", meta={"skip": "human"}, edited=True),
            Agent.HELD,
            Took.OWN,
        ),
        (draft(DraftStatus.SKIPPED, body="", meta={"skip": "no_reply"}), Agent.SILENT, Took.SILENT),
        (
            draft(DraftStatus.SENT, body="", meta={"skip": "no_reply"}, edited=True),
            Agent.SILENT,
            Took.OWN,
        ),
        (
            draft(DraftStatus.SENT, edited=False, meta={"forced_over_skip": {"kind": "no_reply"}}),
            Agent.SILENT,
            Took.AS_IS,
        ),
        (
            draft(DraftStatus.SENT, edited=False, meta={"forced_over_skip": {"kind": "human"}}),
            Agent.HELD,
            Took.AS_IS,
        ),
        (draft(DraftStatus.SENT, edited=False, decided_by=AUTOPILOT), Agent.READY, Took.AUTO),
    ],
)
def test_what_the_agent_and_the_human_did_is_read_from_the_draft(
    made: AgentDraftModel, agent: Agent, took: Took
) -> None:
    assert calibration.agent_of(made) is agent
    assert calibration.took_of(made, answered=False, settled=True) is took


def test_human_silence_is_an_outcome_only_after_the_settle_time() -> None:
    silent = draft(DraftStatus.SKIPPED, body="", meta={"skip": "no_reply"})

    assert calibration.took_of(silent, answered=False, settled=False) is Took.PENDING
    assert calibration.took_of(silent, answered=True, settled=False) is Took.OWN


def test_cohort_number_follows_every_version() -> None:
    assert OLD.id != NEW.id
    assert len({Cohort(**{**vars_of(NEW), name: "x"}).id for name in vars_of(NEW)}) == 5
    assert NEW.id == Cohort(*vars_of(NEW).values()).id


def vars_of(cohort: Cohort) -> dict[str, str | None]:
    return {
        "reply": cohort.reply,
        "situation": cohort.situation,
        "judge": cohort.judge,
        "moves": cohort.moves,
        "kb": cohort.kb,
    }


# --- разница по словам ---------------------------------------------------------------------


def test_word_diff_marks_removed_and_added_words_and_rebuilds_both_texts() -> None:
    old = "Спасибо за письмо!\nНаш аудит стоит 500 долларов, свяжемся позже."
    new = "Спасибо за письмо!\nЦену назовём на созвоне: https://cal.example.test/a."

    found = word_diff(old, new)

    assert "".join(p.text for p in found if p.kind is not Piece.ADDED) == old
    assert "".join(p.text for p in found if p.kind is not Piece.REMOVED) == new
    assert found[0] == calibration_board.Part(Piece.SAME, "Спасибо за письмо!\n")
    assert any(p.kind is Piece.REMOVED and "500 долларов" in p.text for p in found)
    assert any(p.kind is Piece.ADDED and "созвоне" in p.text for p in found)
    # Подряд одинаковые части склеены: две «убранные» рядом не стоят.
    assert all(a.kind is not b.kind for a, b in pairwise(found))


def test_a_short_common_word_inside_an_edit_does_not_cut_it() -> None:
    """«на» в черновике и в ушедшем — случайное совпадение, а не общий текст: правка цельная."""
    old = "Аудит стоит 500 долларов, вернёмся на этой неделе."
    new = "Цену назовём на созвоне."

    found = word_diff(old, new)

    assert [p for p in found if p.kind is Piece.SAME] == []
    assert "".join(p.text for p in found if p.kind is Piece.REMOVED) == old
    assert "".join(p.text for p in found if p.kind is Piece.ADDED) == new
    # По краям текста короткое общее слово — общее: граница правки там настоящая.
    edge = word_diff("Да, аудит стоит 500 долларов.", "Да, цену назовём на созвоне.")
    assert edge[0] == calibration_board.Part(Piece.SAME, "Да, ")


def test_a_word_with_other_spaces_after_it_is_still_the_same_word() -> None:
    """Подпись в конце черновика и та же подпись перед дописанной строкой — одно слово."""
    found = word_diff("Спасибо.\n\nКоманда", "Спасибо.\n\nКоманда\n\nПоправлено.")

    assert found == (
        calibration_board.Part(Piece.SAME, "Спасибо.\n\nКоманда"),
        calibration_board.Part(Piece.ADDED, "\n\nПоправлено."),
    )


def test_word_diff_of_too_long_texts_is_empty() -> None:
    long = "слово " * (calibration_board.MAX_WORDS + 1)

    assert word_diff(long, "коротко") == ()


# --- журнал на базе ------------------------------------------------------------------------


async def stored(
    session: AsyncSession,
    reply: ReplyModel,
    settings_id: int,
    status: DraftStatus,
    *,
    cohort: Cohort,
    label: str | None = "asks_price",
    meta: dict[str, Any] | None = None,
    **values: Any,
) -> AgentDraftModel:
    """Черновик продаж с версией агента и ситуацией в `meta` — как пишет бриф."""
    found: dict[str, Any] = {
        "versions": {"situation": cohort.situation, "judge": cohort.judge, "moves": cohort.moves},
        "kb_version": cohort.kb,
        "turn": 1,
        **(meta or {}),
    }
    if label is not None:
        found["situation"] = label
    row_ = AgentDraftModel(
        reply_id=reply.id,
        settings_id=settings_id,
        status=status,
        body=values.pop("body", "Черновик агента."),
        meta=found,
        model="test-model",
        prompt_version=cohort.reply,
        tokens=0,
        **values,
    )
    session.add(row_)
    await session.flush()
    return row_


async def current_cohort(session: AsyncSession) -> Cohort:
    versions = brief.versions()
    return Cohort(
        parts.PROMPT_VERSION,
        versions["situation"],
        versions["judge"],
        versions["moves"],
        await kb.version(session),
    )


async def journal_world(session: AsyncSession) -> dict[str, AgentDraftModel]:
    """Две версии агента: прежняя — три решения; текущая — все виды решений; доноры рядом."""
    settings = await AgentSettingsRepository(session).save(
        Stage.SALES, parts.DEFAULTS, author="тест"
    )
    sid = settings.id
    now = await current_cohort(session)
    number = iter(range(100, 200))

    async def letter(days: float = 5, stage: Stage = Stage.SALES) -> ReplyModel:
        return await lead_reply(
            session, next(number), "Сколько стоит аудит?", days=days, stage=stage
        )

    made: dict[str, AgentDraftModel] = {}
    made["old_as_is"] = await stored(
        session, await letter(9), sid, DraftStatus.SENT, cohort=OLD, edited=False
    )
    for name, kind in (("old_long", "длинно"), ("old_other", "другое")):
        made[name] = await stored(
            session,
            await letter(9),
            sid,
            DraftStatus.REJECTED,
            cohort=OLD,
            reject_reason=kind if kind != "другое" else "другое: не о том",
            reject_kind=kind,
            decided_at=NOW - timedelta(days=8),
        )
    sent = await letter()
    await answered(session, sent, "Черновик агента.")
    made["as_is"] = await stored(session, sent, sid, DraftStatus.SENT, cohort=now, edited=False)
    made["as_is_2"] = await stored(
        session, await letter(), sid, DraftStatus.SENT, cohort=now, edited=False
    )
    made["edited"] = await stored(
        session,
        await letter(),
        sid,
        DraftStatus.SENT,
        cohort=now,
        edited=True,
        body="Спасибо за вопрос. Аудит стоит 500 долларов, свяжемся позже.",
        final_body="Спасибо за вопрос. Цену назовём на созвоне.",
        decided_at=NOW - timedelta(hours=1),
    )
    made["rejected"] = await stored(
        session,
        await letter(),
        sid,
        DraftStatus.REJECTED,
        cohort=now,
        label="asks_info",
        reject_reason="факт не из базы",
        reject_kind="факт не из базы",
        decided_at=NOW - timedelta(hours=2),
    )
    made["waiting"] = await stored(
        session, await letter(), sid, DraftStatus.DRAFTED, cohort=now, label="asks_info"
    )
    made["held_sent"] = await stored(
        session,
        await letter(),
        sid,
        DraftStatus.SENT,
        cohort=now,
        reason="судья: сумма не из базы",
        edited=True,
        final_body="Поправленный ответ.",
        decided_at=NOW - timedelta(hours=3),
    )
    made["held_waiting"] = await stored(
        session,
        await letter(),
        sid,
        DraftStatus.ESCALATED,
        cohort=now,
        label=None,
        body="",
        reason="язык письма не определён",
        meta={"skip": "human"},
    )
    made["true_silence"] = await stored(
        session,
        await letter(5),
        sid,
        DraftStatus.SKIPPED,
        cohort=now,
        label="ack",
        body="",
        meta={"skip": "no_reply"},
    )
    replied = await letter(5)
    await answered(session, replied, "Ответ человека сам.")
    made["false_silence"] = await stored(
        session,
        replied,
        sid,
        DraftStatus.SENT,
        cohort=now,
        label="ack",
        body="",
        edited=True,
        meta={"skip": "no_reply"},
    )
    made["fresh_silence"] = await stored(
        session,
        await letter(1),
        sid,
        DraftStatus.SKIPPED,
        cohort=now,
        label="autoresponder",
        body="",
        meta={"skip": "no_reply"},
    )
    made["auto"] = await stored(
        session,
        await letter(),
        sid,
        DraftStatus.SENT,
        cohort=now,
        edited=False,
        decided_by=AUTOPILOT,
    )
    made["donors"] = await stored(
        session, await letter(stage=Stage.DONORS), sid, DraftStatus.SENT, cohort=now, edited=False
    )
    return made


async def test_the_journal_reads_sales_drafts_with_decisions_and_versions(
    session: AsyncSession,
) -> None:
    made = await journal_world(session)
    now = await current_cohort(session)

    rows = {r.draft_id: r for r in await calibration.journal(session, now=NOW)}

    assert made["donors"].id not in rows
    assert len(rows) == 14
    places = {name: rows[made[name].id].bucket for name in made if name != "donors"}
    assert places == {
        "old_as_is": Bucket.AS_IS,
        "old_long": Bucket.REJECTED,
        "old_other": Bucket.REJECTED,
        "as_is": Bucket.AS_IS,
        "as_is_2": Bucket.AS_IS,
        "edited": Bucket.EDITED,
        "rejected": Bucket.REJECTED,
        "waiting": Bucket.WAITING,
        "held_sent": Bucket.HELD_SENT,
        "held_waiting": Bucket.HELD_WAITING,
        "true_silence": Bucket.TRUE_SILENCE,
        "false_silence": Bucket.FALSE_SILENCE,
        "fresh_silence": Bucket.WAITING,
        "auto": Bucket.AUTO,
    }
    assert rows[made["old_as_is"].id].cohort == OLD
    assert rows[made["as_is"].id].cohort == now
    assert rows[made["held_waiting"].id].situation is None
    assert rows[made["as_is"].id].replied  # ответ ушёл письмом


async def test_cohorts_do_not_mix_and_the_current_version_is_shown_first(
    session: AsyncSession,
) -> None:
    made = await journal_world(session)
    now = await current_cohort(session)

    board = await calibration_board.board(session, cohort=None, now=NOW)

    assert board.shown == board.current == now
    assert [line.cohort for line in board.cohorts] == [now, OLD]
    current, old = (line.found for line in board.cohorts)
    assert (current.decisions, current[Bucket.AS_IS], current[Bucket.REJECTED]) == (4, 2, 1)
    assert (old.decisions, old[Bucket.AS_IS], old[Bucket.REJECTED]) == (3, 1, 2)
    assert board.found.buckets == current.buckets
    # Причины прежней версии в текущей не видны.
    assert dict(board.found.reasons) == {"факт не из базы": 1}
    earlier = await calibration_board.board(session, cohort=OLD.id, now=NOW)
    assert earlier.shown == OLD
    assert dict(earlier.found.reasons) == {"длинно": 1, "другое": 1}
    assert [change.row.draft_id for change in earlier.changes] == [
        made["old_other"].id,
        made["old_long"].id,
    ]


async def test_situations_carry_their_own_numbers_and_gate(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await journal_world(session)
    monkeypatch.setattr(sales_cfg, "AUTOREPLY_MIN_N", 3)

    board = await calibration_board.board(session, cohort=None, now=NOW)

    lines = {line.situation: line for line in board.situations}
    assert list(lines)[:9] == [
        "wants_to_talk",
        "asks_price",
        "asks_info",
        "objection",
        "not_now",
        "refusal",
        "wrong_person",
        "ack",
        "autoresponder",
    ]
    assert None in lines  # отдано до разбора — строкой, раз такое было
    assert "parse_failed" not in lines
    price = lines["asks_price"]
    assert (price.found.decisions, price.found[Bucket.AS_IS]) == (3, 2)
    assert price.gate == calibration.Gate(
        GateState.FAILED, ("без правки 66,7% (2 из 3) — ниже 80%",)
    )
    assert lines["ack"].gate == calibration.Gate(
        GateState.FEW, ("данных мало: молчаний агента с исходом 2 из 3",)
    )
    assert lines["wants_to_talk"].gate.state is GateState.FEW


async def test_changes_are_the_latest_edits_and_rejections_with_a_word_diff(
    session: AsyncSession,
) -> None:
    made = await journal_world(session)

    board = await calibration_board.board(session, cohort=None, now=NOW)

    assert [change.row.draft_id for change in board.changes] == [
        made["edited"].id,
        made["rejected"].id,
        made["held_sent"].id,
    ]
    edited = board.changes[0]
    assert "".join(p.text for p in edited.parts if p.kind is not Piece.REMOVED) == (
        "Спасибо за вопрос. Цену назовём на созвоне."
    )
    assert [p.kind for p in edited.parts] == [Piece.SAME, Piece.REMOVED, Piece.ADDED]
    assert board.changes[1].parts == ()  # у отклонения ушедшего нет


async def test_without_drafts_of_the_current_version_the_latest_one_is_shown(
    session: AsyncSession,
) -> None:
    settings = await AgentSettingsRepository(session).save(
        Stage.SALES, parts.DEFAULTS, author="тест"
    )
    reply = await lead_reply(session, 300, "Сколько стоит?", days=4)
    await stored(session, reply, settings.id, DraftStatus.SENT, cohort=OLD, edited=False)

    board = await calibration_board.board(session, cohort=None, now=NOW)

    assert board.shown == OLD
    assert board.current == await current_cohort(session)
    with pytest.raises(calibration_board.UnknownCohortError, match="версии агента №0123456789"):
        await calibration_board.board(session, cohort="0123456789", now=NOW)
    # Номер текущей версии без черновиков — тоже версия: экран покажет её пустой.
    empty = await calibration_board.board(session, cohort=board.current.id, now=NOW)
    assert (empty.shown, empty.found.drafts) == (board.current, 0)


async def test_gate_of_a_situation_is_judged_on_the_current_version_only(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = await AgentSettingsRepository(session).save(
        Stage.SALES, parts.DEFAULTS, author="тест"
    )
    now = await current_cohort(session)
    for number in range(4):
        old = await lead_reply(session, 400 + number, "Сколько стоит?", days=4)
        await stored(session, old, settings.id, DraftStatus.SENT, cohort=OLD, edited=False)
    fresh = await lead_reply(session, 410, "Сколько стоит?", days=4)
    await stored(session, fresh, settings.id, DraftStatus.SENT, cohort=now, edited=True)
    monkeypatch.setattr(sales_cfg, "AUTOREPLY_MIN_N", 1)

    found = await calibration.verdict(session, "asks_price", now=NOW)

    # Четыре «как есть» прежней версии не в счёте: у текущей — одна правка из одной.
    assert found == calibration.Gate(GateState.FAILED, ("без правки 0% (0 из 1) — ниже 80%",))
