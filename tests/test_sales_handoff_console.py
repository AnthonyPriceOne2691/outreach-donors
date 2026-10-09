"""Консоль передач, где Kommo решает человек (`unconfirmed`, `failed`) — решение владельца
по ревью стыков 5.3: список, «повторить», «закрыть руками».

Каждое решение — строка журнала действий; чужое состояние, занятая задачей передача,
пустая записка и чужой номер сделки — отказ словами и код 2; ключ Kommo и токен бота
продаж — ни в выводе, ни в журнале. База настоящая; Kommo — подставной; Telegram —
`httpx.MockTransport`; очередь — список. Номера, адреса и ключи — выдуманные.
"""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from backend.cli import sales_handoffs as cli
from backend.cli.main import _COMMANDS, _KEPT_ON_INTERRUPT, build_parser, main
from backend.config import sales as cfg
from backend.config import storage
from backend.features.core.domain import AuditAction
from backend.features.core.models.access import AuditLogModel
from backend.features.sales import handoff
from backend.features.sales.kommo import KommoFixture
from backend.features.sales.models import HandoffKommo, HandoffTelegram, SalesHandoffModel
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import TEST_DSN
from tests.test_sales_handoff import (
    DEAL,
    NOW,
    Alerts,
    LostAnswerKommo,
    RefusingKommo,
    alerts,
    api,
    deps,
    hand_off,
    http,
    reread,
    sent,
)
from tests.test_sales_handoff_rows import Dialog, answer, sales_dialog
from tests.test_sales_switch import sales_switched_on
from tests.test_sales_telegram import TOKEN, Recorder

__all__ = [  # оснастка передачи: тревоги, бот продаж, клиент httpx
    "alerts",
    "api",
    "http",
    "sales_switched_on",  # продажи включены (SALES_ENABLED): «повторить» ставит задачу
]

#: Ключ Kommo — выдуманный, некруглый; в выводе и журнале его быть не должно.
KOMMO_KEY = "kommo-test-key-5f3e91c2"  # pragma: allowlist secret


async def _unconfirmed(
    session: AsyncSession, http: httpx.AsyncClient, alerts: Alerts, *, lands: bool = False
) -> tuple[Dialog, SalesHandoffModel, LostAnswerKommo]:
    """Ответ Kommo потерян, поиск не решил: `unconfirmed`. `lands` — сделка на деле есть."""
    dialog = await sales_dialog(session)
    kommo = LostAnswerKommo(lands=lands)
    kommo.seed_contact(dialog.lead.email)
    row = await hand_off(session, dialog, deps(http, alerts, kommo))
    assert row.kommo is HandoffKommo.UNCONFIRMED
    return dialog, row, kommo


async def _in_state(session: AsyncSession, name: str, kommo: HandoffKommo) -> SalesHandoffModel:
    dialog = await sales_dialog(
        session, host=f"{name}.example.test", email=f"a@{name}.example.test"
    )
    row = await handoff.start(session, dialog.thread.id, enqueue=lambda _id: None, now=lambda: NOW)
    row.kommo, row.telegram, row.due_at = kommo, HandoffTelegram.SENT, None
    await session.commit()
    return row


async def _journal(session: AsyncSession, handoff_id: int) -> list[AuditLogModel]:
    rows = await session.scalars(
        select(AuditLogModel)
        .where(AuditLogModel.target == f"sales_handoff:{handoff_id}")
        .order_by(AuditLogModel.id)
    )
    return list(rows)


# --- список --------------------------------------------------------------------------------


async def test_list_shows_only_handoffs_that_wait_for_a_human(
    session: AsyncSession,
    http: httpx.AsyncClient,
    alerts: Alerts,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, unconfirmed, _ = await _unconfirmed(session, http, alerts)
    failed = await _in_state(session, "failed", HandoffKommo.FAILED)
    failed.kommo_lead_id, failed.attempts = 9417, 2
    failed.last_error = "Kommo: ключ Kommo отклонён (HTTP 401) — выпустить новый"
    await session.commit()
    others = [
        await _in_state(session, kommo.value, kommo)
        for kommo in (HandoffKommo.DONE, HandoffKommo.RETRY, HandoffKommo.OFF, HandoffKommo.PENDING)
    ]
    capsys.readouterr()

    code = await cli.run_list(session)

    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert "Передачи, где Kommo решает человек: 2" in out
    assert (
        f"№{unconfirmed.id} · лид №{unconfirmed.lead_id} ivan@acme.example.test (Акме Тест)" in out
    )
    assert "запись в Kommo не подтверждена · сделки нет · попыток записи: 1" in out
    assert "контакт с этой почтой есть — сделка могла создаться" in out, "причина словами"
    assert f"№{failed.id} · лид №{failed.lead_id} a@failed.example.test" in out
    assert "Kommo отказал · сделка №9417 · попыток записи: 2" in out
    assert "ключ Kommo отклонён (HTTP 401)" in out
    for row in others:
        assert f"№{row.id} ·" not in out, f"в списке передача в состоянии {row.kommo}"
    assert "sales-handoff-retry" in out
    assert "sales-handoff-close" in out


async def test_empty_list_says_so(
    session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    await _in_state(session, "done", HandoffKommo.DONE)

    assert await cli.run_list(session) == cli.EXIT_OK

    assert "Передач, где Kommo решает человек, нет." in capsys.readouterr().out


# --- «повторить» ---------------------------------------------------------------------------


async def test_retry_puts_the_write_back_queues_the_job_and_journals(
    session: AsyncSession,
    http: httpx.AsyncClient,
    api: Recorder,
    alerts: Alerts,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Человек проверил в Kommo: сделки нет. Запись снова ждёт, задача в очереди; её задача
    заводит сделку, и телемаркетолог получает ссылку на неё."""
    _, row, _ = await _unconfirmed(session, http, alerts)
    queued: list[int] = []

    code = await cli.run_retry(session, row.id, enqueue=queued.append, now=NOW)

    assert code == cli.EXIT_OK
    assert "запись в Kommo снова ждёт задачи — задача поставлена" in capsys.readouterr().out
    assert queued == [row.id]
    row = await reread(session, row.id)
    assert row.kommo is HandoffKommo.PENDING
    assert row.due_at == NOW + timedelta(seconds=cfg.HANDOFF_RETRY_SEC)
    [entry] = await _journal(session, row.id)
    assert entry.action is AuditAction.USER_UPDATED
    assert entry.details is not None
    assert entry.details["действие"] == "передача лида — повторить запись в Kommo"
    assert (entry.details["было"], entry.details["стало"]) == ("unconfirmed", "pending")
    assert str(entry.details["кто"]).startswith("консоль: ")
    assert "сделка могла создаться" in str(entry.details["причина"])

    await handoff.process(session, row.id, deps(http, alerts, KommoFixture()))

    row = await reread(session, row.id)
    assert (row.kommo, row.kommo_lead_id) == (HandoffKommo.DONE, 9301)
    assert sent(api)[-2][1].endswith(f"Ссылка на сделку в коммо: {DEAL}")


async def test_retry_whose_queue_is_down_is_left_to_the_pass(
    session: AsyncSession,
    http: httpx.AsyncClient,
    alerts: Alerts,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, row, _ = await _unconfirmed(session, http, alerts)

    def refuse(_handoff_id: int) -> None:
        raise RedisConnectionError("очередь не отвечает")

    code = await cli.run_retry(session, row.id, enqueue=refuse, now=NOW)

    assert code == cli.EXIT_OK
    assert "задачу возьмёт проход по расписанию" in capsys.readouterr().out
    later = NOW + timedelta(seconds=cfg.HANDOFF_RETRY_SEC + 1)
    assert await handoff.due(session, now=later) == [row.id]


# --- «закрыть руками» -----------------------------------------------------------------------


async def test_close_with_the_deal_found_in_kommo_notes_the_next_answer_to_it(
    session: AsyncSession,
    http: httpx.AsyncClient,
    alerts: Alerts,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Ответ потерян, а сделка на деле есть: человек нашёл её в Kommo и закрыл передачу с её
    номером. Следующий ответ лида ложится к ней примечанием — второй сделки нет."""
    dialog, row, kommo = await _unconfirmed(session, http, alerts, lands=True)
    assert list(kommo.leads) == [9301]

    code = await cli.run_close(session, row.id, "  сделка в Kommo есть, нашёл по почте  ", 9301)

    assert code == cli.EXIT_OK
    assert "сделка №9301 записана" in capsys.readouterr().out
    row = await reread(session, row.id)
    assert (row.kommo, row.kommo_lead_id, row.noted_reply_id) == (
        HandoffKommo.DONE,
        9301,
        dialog.reply.id,
    )
    [entry] = await _journal(session, row.id)
    assert entry.details is not None
    assert entry.details["действие"] == "передача лида — закрыта руками"
    assert entry.details["записка"] == "сделка в Kommo есть, нашёл по почте"
    assert (entry.details["было"], entry.details["стало"], entry.details["сделка"]) == (
        "unconfirmed",
        "done",
        9301,
    )

    await answer(session, dialog.thread, dialog.message, "Ждём звонка в пятницу.", at=NOW)
    await handoff.process(
        session,
        (await handoff.start(session, dialog.thread.id, enqueue=lambda _id: None)).id,
        deps(http, alerts, kommo),
    )

    assert list(kommo.leads) == [9301], "вторая сделка после закрытия руками"
    [note] = kommo.leads[9301].notes.values()
    assert "пятницу" in note


async def test_close_without_a_deal_keeps_every_job_away_from_kommo(
    session: AsyncSession,
    http: httpx.AsyncClient,
    alerts: Alerts,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """«Ничего не нужно»: Kommo отказал, человек закрыл. Повтор сообщения или другая задача
    без нового ответа запись не открывает; консоль говорит, что новый ответ заведёт сделку."""
    dialog = await sales_dialog(session)
    row = await hand_off(session, dialog, deps(http, alerts, RefusingKommo()))
    assert row.kommo is HandoffKommo.FAILED

    code = await cli.run_close(session, row.id, "лид уже у телемаркетолога по телефону", None)

    assert code == cli.EXIT_OK
    assert "следующий ответ лида заведёт сделку в Kommo заново" in capsys.readouterr().out
    kommo = KommoFixture()
    await handoff.process(session, row.id, deps(http, alerts, kommo))
    assert kommo.leads == {}, "закрытую передачу задача снова понесла в Kommo"
    row = await reread(session, row.id)
    assert (row.kommo, row.kommo_lead_id) == (HandoffKommo.DONE, None)


@pytest.mark.parametrize("note", ["", "   \n\t"])
async def test_close_needs_a_note(
    session: AsyncSession,
    http: httpx.AsyncClient,
    alerts: Alerts,
    capsys: pytest.CaptureFixture[str],
    note: str,
) -> None:
    _, row, _ = await _unconfirmed(session, http, alerts)

    code = await cli.run_close(session, row.id, note, None)

    assert code == cli.EXIT_REFUSED
    assert "записка обязательна" in capsys.readouterr().out
    assert (await reread(session, row.id)).kommo is HandoffKommo.UNCONFIRMED
    assert await _journal(session, row.id) == []


async def test_close_with_another_deal_than_the_known_one_is_refused(
    session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    row = await _in_state(session, "known", HandoffKommo.FAILED)
    row.kommo_lead_id = 9417
    await session.commit()

    code = await cli.run_close(session, row.id, "сделка есть", 9418)

    assert code == cli.EXIT_REFUSED
    assert "уже есть сделка №9417" in capsys.readouterr().out
    assert (await reread(session, row.id)).kommo is HandoffKommo.FAILED
    assert await _journal(session, row.id) == []


# --- отказы словами --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kommo", [HandoffKommo.DONE, HandoffKommo.RETRY, HandoffKommo.OFF, HandoffKommo.PENDING]
)
@pytest.mark.parametrize("command", ["retry", "close"])
async def test_handoff_in_another_state_is_refused_in_words(
    session: AsyncSession, capsys: pytest.CaptureFixture[str], kommo: HandoffKommo, command: str
) -> None:
    row = await _in_state(session, kommo.value, kommo)
    queued: list[int] = []

    if command == "retry":
        code = await cli.run_retry(session, row.id, enqueue=queued.append, now=NOW)
    else:
        code = await cli.run_close(session, row.id, "записка", None, now=NOW)

    out = capsys.readouterr().out
    assert code == cli.EXIT_REFUSED
    assert f"передача №{row.id} — «" in out
    assert "можно только неподтверждённую запись в Kommo или отказ Kommo" in out
    assert queued == []
    assert (await reread(session, row.id)).kommo is kommo
    assert await _journal(session, row.id) == []


@pytest.mark.parametrize("command", ["retry", "close"])
async def test_handoff_held_by_a_job_is_refused(
    session: AsyncSession,
    http: httpx.AsyncClient,
    alerts: Alerts,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    _, row, _ = await _unconfirmed(session, http, alerts)
    row.claimed_at = NOW - timedelta(minutes=2)
    await session.commit()

    if command == "retry":
        code = await cli.run_retry(session, row.id, enqueue=lambda _id: None, now=NOW)
    else:
        code = await cli.run_close(session, row.id, "записка", None, now=NOW)

    assert code == cli.EXIT_REFUSED
    assert "сейчас держит задача" in capsys.readouterr().out
    assert (await reread(session, row.id)).kommo is HandoffKommo.UNCONFIRMED


async def test_missing_handoff_is_refused(
    session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await cli.run_retry(session, 987655, enqueue=lambda _id: None) == cli.EXIT_REFUSED
    assert "передачи №987655 нет" in capsys.readouterr().out


# --- ключи ------------------------------------------------------------------------------------


async def test_kommo_key_and_bot_token_stay_out_of_the_output_and_the_journal(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cfg, "KOMMO_TOKEN", KOMMO_KEY)
    monkeypatch.setattr(cfg, "TELEGRAM_BOT_TOKEN", TOKEN)
    row = await _in_state(session, "leaky", HandoffKommo.FAILED)
    row.last_error = f"Kommo: отказ с ключом {KOMMO_KEY}; Telegram: адрес /bot{TOKEN}/sendMessage"
    await session.commit()

    await cli.run_list(session)
    await cli.run_close(session, row.id, f"ключ {KOMMO_KEY} не трогал", None)

    out = capsys.readouterr().out
    [entry] = await _journal(session, row.id)
    journal = str(entry.details)
    for text in (out, journal):
        assert KOMMO_KEY not in text
        assert TOKEN not in text
        assert "<ключ Kommo>" in text
    assert "<токен бота продаж>" in out


# --- консоль --------------------------------------------------------------------------------


def test_commands_are_registered_in_the_console() -> None:
    """Три команды — в перечне продаж (`cli/sales_commands.py`); прерывание говорит правду."""
    for name in ("sales-handoffs", "sales-handoff-retry", "sales-handoff-close"):
        assert name in _COMMANDS
        assert name in _KEPT_ON_INTERRUPT
    parsed = build_parser().parse_args(
        ["sales-handoff-close", "17", "--note", "сделка есть", "--deal", "9301"]
    )
    assert (parsed.handoff, parsed.note, parsed.deal) == (17, "сделка есть", 9301)
    assert build_parser().parse_args(["sales-handoff-retry", "17"]).handoff == 17
    with pytest.raises(SystemExit):
        build_parser().parse_args(["sales-handoff-close", "17"])


async def test_deal_number_that_is_not_a_number_is_refused(
    session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    row = await _in_state(session, "zero", HandoffKommo.FAILED)

    assert await cli.run_close(session, row.id, "сделка есть", 0) == cli.EXIT_REFUSED

    assert "№0 — не номер сделки" in capsys.readouterr().out
    assert (await reread(session, row.id)).kommo is HandoffKommo.FAILED


@pytest.mark.parametrize(
    "argv",
    [
        ["sales-handoff-retry", "987655"],
        ["sales-handoff-close", "987655", "--note", "сделка есть"],
    ],
)
def test_decisions_run_through_main_and_refuse_a_missing_handoff(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    monkeypatch.setattr(storage, "DSN", TEST_DSN)
    monkeypatch.setattr("backend.cli.main.setup_logging", lambda: None)

    assert main(argv) == cli.EXIT_REFUSED

    assert "передачи №987655 нет" in capsys.readouterr().out


def test_list_runs_through_main_on_a_clean_base(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(storage, "DSN", TEST_DSN)
    monkeypatch.setattr("backend.cli.main.setup_logging", lambda: None)

    assert main(["sales-handoffs"]) == cli.EXIT_OK

    assert "Передач, где Kommo решает человек, нет." in capsys.readouterr().out
