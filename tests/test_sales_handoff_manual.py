"""«Передать телемаркетологу» — модуль `handoff_manual`: слова состояния передачи, итог нажатия
словами и стык с задачей разбора ответа.

Слова состояния — строка «передан телемаркетологу …» на экране диалога: что с Kommo и с сообщением
телемаркетологу, сбой — словами строки передачи, без ключей. Стык: ответ, снятый с ожидания
человеком, задача разбора, пришедшая позже, не берёт — решение человека модель не перезапишет.
Маршрут, отказы и права — `test_api_sales_handoff.py`. Номера, адреса и ключи — выдуманные.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from backend.config import sales as cfg
from backend.features.core.domain import Stage
from backend.features.outreach.threads import ThreadState, review_of, summarize
from backend.features.sales import handoff_manual
from backend.features.sales.handoff_manual import MANUAL, HandedOver
from backend.features.sales.models import HandoffKommo, HandoffTelegram, SalesHandoffModel
from backend.features.sales.replies import SalesReplies
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_handoff_rows import sales_dialog
from tests.test_sales_reply_routing import FakeClassifier
from tests.test_sales_switch import sales_switched_on

__all__ = ["sales_switched_on"]  # продажи включены: передача лида идёт

AT = datetime(2026, 10, 26, 9, 41, tzinfo=UTC)
WHO = "anna@ours.example.test"
#: Ключи — выдуманные: в слова экрана они не попадают ни при каком сбое.
KOMMO_KEY = "kommo-key-7f3e1c-for-tests"
BOT_TOKEN = "8123456789:bot-token-for-tests"


def _row(
    kommo: HandoffKommo = HandoffKommo.PENDING,
    telegram: HandoffTelegram = HandoffTelegram.PENDING,
    *,
    deal: int | None = None,
    error: str | None = None,
    due: datetime | None = None,
) -> SalesHandoffModel:
    return SalesHandoffModel(
        thread_id=7,
        lead_id=11,
        kommo=kommo,
        telegram=telegram,
        kommo_lead_id=deal,
        last_error=error,
        telegram_due_at=due,
        created_at=AT,
    )


@pytest.mark.parametrize(
    ("kommo", "deal", "error", "said"),
    [
        (HandoffKommo.PENDING, None, None, "запись ждёт задачи"),
        (HandoffKommo.DONE, 9301, None, "сделка №9301"),
        (HandoffKommo.DONE, None, None, "передача закрыта руками, сделки нет"),
        (HandoffKommo.OFF, None, None, "только ссылка на диалог: Kommo не подключён"),
        (
            HandoffKommo.RETRY,
            9301,
            "Kommo: Kommo не ответил: связь оборвалась (ReadError)",
            "сделка №9301; не ответил — повторяем по расписанию",
        ),
        (
            HandoffKommo.UNCONFIRMED,
            None,
            None,
            "запись не подтверждена — проверить в Kommo руками",
        ),
        (
            HandoffKommo.FAILED,
            None,
            "Kommo: ключ Kommo отклонён (HTTP 401) — выпустить новый",
            "отказ: ключ Kommo отклонён (HTTP 401) — выпустить новый",
        ),
        # Сбой у строки один на обе стороны: сбой сообщения — не причина отказа Kommo.
        (
            HandoffKommo.FAILED,
            None,
            "Telegram: чат телемаркетолога не найден (HTTP 400)",
            "отказ: причина — в тревоге эксплуатации",
        ),
    ],
)
def test_kommo_words_name_the_deal_the_dialog_link_or_the_refusal(
    kommo: HandoffKommo, deal: int | None, error: str | None, said: str
) -> None:
    assert handoff_manual.kommo_said(_row(kommo, deal=deal, error=error)) == said


@pytest.mark.parametrize(
    ("telegram", "error", "due", "said"),
    [
        (HandoffTelegram.PENDING, None, None, "ещё не отправлено"),
        (HandoffTelegram.SENT, None, None, "отправлено"),
        (
            HandoffTelegram.UNDELIVERED,
            "Telegram: сеть не ответила (ConnectTimeout)",
            AT,
            "не доставлено, повторим по расписанию: сеть не ответила (ConnectTimeout)",
        ),
        (
            HandoffTelegram.UNDELIVERED,
            "Telegram: бот заблокирован телемаркетологом (HTTP 403)",
            None,
            "не доставлено, повторов больше нет: бот заблокирован телемаркетологом (HTTP 403)",
        ),
        (HandoffTelegram.UNDELIVERED, "Kommo: отказ", None, "не доставлено, повторов больше нет"),
    ],
)
def test_telegram_words_say_sent_or_why_not(
    telegram: HandoffTelegram, error: str | None, due: datetime | None, said: str
) -> None:
    assert handoff_manual.telegram_said(_row(telegram=telegram, error=error, due=due)) == said


@pytest.mark.parametrize("kommo", list(HandoffKommo))
@pytest.mark.parametrize("telegram", list(HandoffTelegram))
def test_every_state_of_a_handoff_has_words(kommo: HandoffKommo, telegram: HandoffTelegram) -> None:
    """Новое состояние передачи без слов уронило бы выдачу переписки, а не строку на экране."""
    row = _row(kommo, telegram, deal=9301)
    assert handoff_manual.kommo_said(row)
    assert handoff_manual.telegram_said(row)


def test_keys_never_reach_the_words_of_the_screen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "KOMMO_TOKEN", KOMMO_KEY)
    monkeypatch.setattr(cfg, "TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    kommo = _row(HandoffKommo.FAILED, error=f"Kommo: ключ {KOMMO_KEY} отклонён (HTTP 401)")
    telegram = _row(
        telegram=HandoffTelegram.UNDELIVERED,
        error=f"Telegram: https://api.telegram.org/bot{BOT_TOKEN}/sendMessage — 401",
    )

    words = handoff_manual.kommo_said(kommo) + handoff_manual.telegram_said(telegram)

    assert KOMMO_KEY not in words
    assert BOT_TOKEN not in words
    assert "<ключ Kommo>" in words


@pytest.mark.parametrize(
    ("queued", "released", "tail"),
    [
        (True, (), "сделка в Kommo и сообщение в Telegram уходят задачей очереди продаж."),
        (
            True,
            (5,),
            "сделка в Kommo и сообщение в Telegram уходят задачей очереди продаж; "
            "ответ больше не ждёт человека.",
        ),
        (
            False,
            (5, 6, 9),
            "задача не встала в очередь — передачу возьмёт проход по расписанию в пределах "
            "15 мин; с ожидания человека сняты ответы: 3.",
        ),
    ],
)
def test_outcome_in_words_says_where_the_task_is_and_what_stopped_waiting(
    queued: bool, released: tuple[int, ...], tail: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cfg, "HANDOFF_RETRY_SEC", 15 * 60)
    done = HandedOver(_row(), queued=queued, released=released)
    assert handoff_manual.said(done) == f"Лид передан телемаркетологу: {tail}"


async def test_a_late_reply_job_leaves_the_human_decision_alone(session: AsyncSession) -> None:
    """Ответ ещё не разобран — задача разбора в очереди, — а человек уже передал лида. Задача,
    пришедшая после, ответ не берёт («решён человеком»), модель не зовётся, и ответ не
    возвращается в ожидание человека."""
    dialog = await sales_dialog(
        session,
        host="atelier.example.test",
        email="chloe@atelier.example.test",
        text="Bonjour, appelez-moi au 01 00 00 00 00 ou au +33 6 00 00 00 00.",
    )
    await session.commit()
    queued: list[int] = []

    done = await handoff_manual.hand_over(
        session, dialog.thread.id, by=WHO, author_id=None, enqueue=queued.append, now=AT
    )
    classifier = FakeClassifier()
    handled = await SalesReplies(session, classifier).handle(dialog.reply.id)

    assert done.released == (dialog.reply.id,)
    assert queued == [done.row.id]
    assert handled.skipped == "решён человеком"
    assert classifier.calls == 0
    await session.refresh(dialog.reply)
    assert dialog.reply.model_parse == {
        "stage": Stage.SALES.value,
        "kind": None,
        "waits": False,
        "reason": f"{MANUAL}: {WHO}",
    }
    assert (dialog.reply.reviewed_by, dialog.reply.reviewed_at) == (WHO, AT)
    assert review_of(dialog.reply, Stage.SALES).waiting is False
    summary = summarize([dialog.message], [dialog.reply], Stage.SALES)
    assert summary.state is ThreadState.REPLIED
    row = await session.scalar(
        select(SalesHandoffModel).where(SalesHandoffModel.thread_id == dialog.thread.id)
    )
    assert row is not None
    assert row.due_at is not None
