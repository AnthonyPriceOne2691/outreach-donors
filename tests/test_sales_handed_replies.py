"""Новое письмо лида, уже переданного телемаркетологу: в ту же сделку и телемаркетологу.

ТЗ: тёплый лид — сделка «с контактом, компанией и перепиской» и сообщение телемаркетологу;
Ф5: повторный ответ того же человека — примечание, а не вторая сделка. Любой новый
ответ переданного лида — кроме автоответа — уходит той же передачей: примечание к той же
сделке, сообщение «Лид написал снова» лично и копией в группу, ответ снят с ожидания. Отписка
словами — стоп-лист, как была, и то же письмо телемаркетологу. Повторы — механизмом передачи;
то же письмо дважды не уходит: ключ — номер ответа.

Разбор ответа — `SalesReplies` с подставным видом; передача — настоящая (`handoff.start`,
`handoff.process`) на настоящей базе; Kommo — `KommoFixture` и её подклассы, Telegram —
`SalesBot` поверх `httpx.MockTransport`. Сети нет; адреса и тексты выдуманные.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from functools import partial

import httpx
import pytest
from backend.features.core.domain import ReplyKind, Stage
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import ReplyModel
from backend.features.core.usage import LlmCapExceededError
from backend.features.outreach.threads import review_of
from backend.features.sales import handoff, replies
from backend.features.sales.kommo import KommoFixture
from backend.features.sales.models import HandoffKommo, HandoffTelegram, SalesHandoffModel
from backend.features.sales.replies import SalesReplies
from backend.features.sales.reply_handoff import FORWARDED, FORWARDED_LINK
from backend.features.sales.reply_kind import KindFound, SalesKind, Unanswered
from backend.workers import sales_jobs
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.migration_helpers import columns_down_and_up
from tests.test_sales_handoff import (
    AGAIN,
    DEAL,
    HEAD,
    NOW,
    Alerts,
    DownKommo,
    alerts,
    api,
    deps,
    dialog_line,
    hand_off,
    http,
    reread,
    sent,
)
from tests.test_sales_handoff_rows import Dialog, answer, sales_dialog
from tests.test_sales_reply_handoff import _job_on_test_base
from tests.test_sales_reply_routing import FakeClassifier
from tests.test_sales_switch import sales_switched_on
from tests.test_sales_telegram import GROUP, PERSONAL, Recorder, ok

__all__ = ["alerts", "api", "http", "sales_switched_on"]  # оснастка передачи и выключатель

QUESTION = KindFound(SalesKind.QUESTION, 0.92, quote="А сколько стоит аудит ссылок?")
LATER = NOW + timedelta(hours=3)
REVISION = "1045a2d42013_sales_handoffs_told_reply.py"


class Handed:
    """Лид передан: первая передача прошла, сделка 9301, телемаркетолог получил «Новый лид»."""

    def __init__(self, session: AsyncSession, dialog: Dialog, row: SalesHandoffModel) -> None:
        self.session, self.dialog, self.row = session, dialog, row
        self.queued: list[int] = []

    def replies(self, found: KindFound | Unanswered) -> SalesReplies:
        start = partial(handoff.start, enqueue=self.queued.append, now=lambda: LATER)
        return SalesReplies(self.session, FakeClassifier(found), hand_over=start)

    async def wrote(self, text: str, kind: ReplyKind = ReplyKind.HUMAN) -> ReplyModel:
        reply = await answer(self.session, self.dialog.thread, self.dialog.message, text, at=LATER)
        reply.kind = kind
        await self.session.flush()
        return reply

    async def sorted_out(self, reply: ReplyModel, found: KindFound | Unanswered) -> None:
        """Задача ответа: разбор, коммит, передача — как `sales_jobs.handle`."""
        sales = self.replies(found)
        handled = await sales.handle(reply.id)
        await self.session.commit()
        await sales.pass_on(handled)


async def handed(session: AsyncSession, http: httpx.AsyncClient, alerts: Alerts) -> Handed:
    dialog = await sales_dialog(session)
    row = await hand_off(session, dialog, deps(http, alerts, KommoFixture()))
    return Handed(session, dialog, row)


async def _snap(session: AsyncSession, reply: ReplyModel) -> ReplyModel:
    found = await session.get(ReplyModel, reply.id, populate_existing=True)
    assert found is not None
    return found


# --- вопрос переданного лида: примечание к той же сделке, сообщение, ответ не ждёт ---------


async def test_question_of_a_handed_lead_is_a_note_to_the_same_deal_and_a_message(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    kommo = KommoFixture()
    dialog = await sales_dialog(session)
    row = await hand_off(session, dialog, deps(http, alerts, kommo))
    lead = Handed(session, dialog, row)
    reply = await lead.wrote("А сколько стоит аудит ссылок? У нас интернет-магазин мебели.")

    await lead.sorted_out(reply, QUESTION)
    assert lead.queued == [row.id], "задача передачи поставлена"
    await handoff.process(session, row.id, deps(http, alerts, kommo, at=LATER))
    row = await reread(session, row.id)

    assert list(kommo.leads) == [9301], "вторая сделка"
    first, second = kommo.leads[9301].notes.values()
    assert "Давайте созвонимся" in first
    assert "А сколько стоит аудит ссылок?" in second
    assert "Диалог в сервисе:" in second
    again = f"{AGAIN}Ссылка на сделку в коммо: {DEAL}"
    assert sent(api)[2:] == [(PERSONAL, again), (GROUP, again)]
    assert again.count("\n") == 2, "три строки, как у «Новый лид»"
    assert (row.noted_reply_id, row.told_reply_id, row.told_again) == (reply.id, reply.id, True)
    snap = (await _snap(session, reply)).model_parse or {}
    reason = f"задал вопрос: {FORWARDED}"
    assert (snap["kind"], snap["route"], snap["waits"], snap["reason"]) == (
        "question",
        "agent",
        False,
        reason,
    )
    review = review_of(await _snap(session, reply), Stage.SALES)
    assert (review.waiting, review.reason) == (False, reason), "ответ снят с ожидания"
    assert reason == "задал вопрос: передан телемаркетологу — письмо ушло в сделку"


async def test_handed_lead_gets_no_agent_draft(
    session: AsyncSession, http: httpx.AsyncClient, alerts: Alerts
) -> None:
    lead = await handed(session, http, alerts)
    reply = await lead.wrote("Расскажите подробнее, пожалуйста.")

    handled = await lead.replies(QUESTION).handle(reply.id)

    assert (handled.again, handled.handoff_thread) == (True, lead.dialog.thread.id)
    assert handled.drafted is False, "письмо ушло телемаркетологу — черновик лиду не нужен"


@pytest.mark.parametrize(
    ("found", "words"),
    [
        (KindFound(SalesKind.WANTS_TO_TALK, 0.95, quote="Так что, созвон?"), "хочет говорить"),
        (KindFound(SalesKind.INTERESTED, 0.95, quote="Так что, созвон?"), "интересуется"),
    ],
    ids=["wants-to-talk", "interested"],
)
async def test_other_kinds_that_waited_are_settled_with_the_same_words(
    session: AsyncSession, http: httpx.AsyncClient, alerts: Alerts, found: KindFound, words: str
) -> None:
    lead = await handed(session, http, alerts)
    reply = await lead.wrote("Так что, созвон?")

    await lead.sorted_out(reply, found)

    snap = (await _snap(session, reply)).model_parse or {}
    assert (snap["waits"], snap["reason"]) == (False, f"{words}: {FORWARDED}")


async def test_closing_kind_keeps_its_words_and_tells_where_the_letter_went(
    session: AsyncSession, http: httpx.AsyncClient, alerts: Alerts
) -> None:
    lead = await handed(session, http, alerts)
    reply = await lead.wrote("Передумали, не актуально.")

    await lead.sorted_out(reply, KindFound(SalesKind.NOT_INTERESTED, 0.95, quote="не актуально"))

    snap = (await _snap(session, reply)).model_parse or {}
    assert (snap["waits"], snap["reason"]) == (False, f"не интересно: диалог закрыт; {FORWARDED}")
    assert lead.queued == [lead.row.id]


async def test_unsure_kind_still_waits_for_a_human_but_the_letter_went(
    session: AsyncSession, http: httpx.AsyncClient, alerts: Alerts
) -> None:
    """Вид не разобран — решает человек (стоп-лист по неуверенной отписке без него не встанет),
    а письмо телемаркетологу уже ушло."""
    lead = await handed(session, http, alerts)
    reply = await lead.wrote("Хм.")

    await lead.sorted_out(reply, KindFound(SalesKind.UNSUBSCRIBE, 0.41, quote="Хм."))

    snap = (await _snap(session, reply)).model_parse or {}
    assert snap["waits"] is True
    assert snap["reason"].endswith(f"— решает человек; {FORWARDED}")
    assert lead.queued == [lead.row.id]


# --- через шлюз — сообщение со ссылкой на сделку: `test_sales_handoff_gateway.py` ---------
# --- Kommo молчит: ссылка на диалог и повтор ---------------------------------------------


async def test_kommo_silent_without_a_deal_sends_the_dialog_link_and_retries(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    kommo = DownKommo()
    dialog = await sales_dialog(session)
    row = await hand_off(session, dialog, deps(http, alerts, kommo))
    lead = Handed(session, dialog, row)
    reply = await lead.wrote("Вторник подходит, звоните.")

    await lead.sorted_out(reply, QUESTION)
    await handoff.process(session, row.id, deps(http, alerts, kommo, at=LATER))
    row = await reread(session, row.id)

    again = dialog_line(dialog, " — сделка в Kommo не создана, повторяем").replace(HEAD, AGAIN)
    assert sent(api)[2:] == [(PERSONAL, again), (GROUP, again)]
    assert (row.kommo, row.kommo_lead_id) == (HandoffKommo.RETRY, None)
    assert row.due_at is not None
    assert await handoff.due(session, now=row.due_at) == [row.id], "проход повторит запись"
    snap = (await _snap(session, reply)).model_parse or {}
    assert snap["reason"] == f"задал вопрос: {FORWARDED_LINK}"

    kommo.down = False  # Kommo ожил — сделка с письмом, телемаркетологу — ссылка на неё
    await handoff.process(session, row.id, deps(http, alerts, kommo, at=row.due_at))
    deal = f"{HEAD}Ссылка на сделку в коммо: {DEAL}"
    assert sent(api)[4:] == [(PERSONAL, deal), (GROUP, deal)]
    assert "Вторник подходит" in next(iter(kommo.leads[9301].notes.values()))


# --- автоответ — ничего; отписка — стоп-лист и телемаркетологу ----------------------------


async def test_autoreply_of_a_handed_lead_goes_nowhere(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    lead = await handed(session, http, alerts)
    reply = await lead.wrote("Я в отпуске до понедельника.", kind=ReplyKind.AUTO_REPLY)
    classifier = FakeClassifier(QUESTION)
    start = partial(handoff.start, enqueue=lead.queued.append, now=lambda: LATER)

    handled = await SalesReplies(session, classifier, hand_over=start).handle(reply.id)

    assert (handled.handoff_thread, handled.again) == (None, False)
    assert (lead.queued, classifier.calls, len(api.seen)) == ([], 0, 2)
    assert (await reread(session, lead.row.id)).told_reply_id == lead.dialog.reply.id


@pytest.mark.parametrize("road", ["model", "rules"])
async def test_unsubscribe_of_a_handed_lead_is_stop_listed_and_told(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts, road: str
) -> None:
    """Обе дороги отписки словами — модель и правила приёма: стоп-лист, как был, плюс
    примечание и сообщение — телемаркетологу надо знать, что лиду больше не пишут."""
    kommo = KommoFixture()
    dialog = await sales_dialog(session)
    row = await hand_off(session, dialog, deps(http, alerts, kommo))
    lead = Handed(session, dialog, row)
    kind = ReplyKind.HUMAN if road == "model" else ReplyKind.UNSUBSCRIBE
    reply = await lead.wrote("Больше нам не пишите.", kind=kind)

    await lead.sorted_out(reply, KindFound(SalesKind.UNSUBSCRIBE, 0.97, quote="не пишите"))
    await handoff.process(session, row.id, deps(http, alerts, kommo, at=LATER))

    stopped = await session.scalar(
        select(func.count())
        .select_from(SuppressionModel)
        .where(SuppressionModel.email == "ivan@acme.example.test")
    )
    assert stopped == 1, "стоп-лист, как раньше"
    assert "Больше нам не пишите." in list(kommo.leads[9301].notes.values())[-1]
    again = f"{AGAIN}Ссылка на сделку в коммо: {DEAL}"
    assert sent(api)[2:] == [(PERSONAL, again), (GROUP, again)]


# --- то же письмо дважды — одно примечание и одно сообщение -------------------------------


async def test_the_same_letter_twice_is_one_note_and_one_message(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    """Модель не ответила — письмо всё равно ушло (передаче вид не нужен), задача ответа
    повторилась и разобрала его: второй раз ни примечания, ни сообщения."""
    kommo = KommoFixture()
    dialog = await sales_dialog(session)
    row = await hand_off(session, dialog, deps(http, alerts, kommo))
    lead = Handed(session, dialog, row)
    reply = await lead.wrote("Сколько стоит?")
    work = deps(http, alerts, kommo, at=LATER)

    await lead.sorted_out(reply, Unanswered("модель не ответила: сеть", permanent=False))
    snap = (await _snap(session, reply)).model_parse or {}
    assert (snap["kind"], snap["waits"]) == (None, True), "вид не разобран — ответ ждёт"
    assert snap["reason"].endswith(f"; {FORWARDED}")
    await handoff.process(session, row.id, work)
    await lead.sorted_out(reply, QUESTION)  # повтор задачи ответа
    for _ in range(2):
        await handoff.start(session, dialog.thread.id, enqueue=lead.queued.append)
        await handoff.process(session, row.id, work)

    assert len(kommo.leads[9301].notes) == 2, "примечание о том же письме — одно"
    assert len(api.seen) == 4, "сообщение о том же письме — одно (лично и копией)"
    assert lead.queued == [row.id], "повтор разбора задачу второй раз не ставит"
    snap = (await _snap(session, reply)).model_parse or {}
    assert (snap["kind"], snap["waits"]) == ("question", False)


async def test_group_copy_retried_by_the_pass_keeps_the_again_words(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    kommo = KommoFixture()
    dialog = await sales_dialog(session)
    lead = Handed(session, dialog, await hand_off(session, dialog, deps(http, alerts, kommo)))
    reply = await lead.wrote("Сколько стоит?")
    await lead.sorted_out(reply, QUESTION)
    down = httpx.Response(503, json={"ok": False, "description": "Service Unavailable"})
    api.answers = [ok(), down]

    await handoff.process(session, lead.row.id, deps(http, alerts, kommo, at=LATER))
    row = await reread(session, lead.row.id)
    assert (row.telegram, row.told_again) == (HandoffTelegram.SENT, True)
    assert row.telegram_due_at is not None, "копия ждёт повтора"
    api.answers = [ok()]
    await handoff.process(
        session, row.id, deps(http, alerts, kommo, at=row.telegram_due_at), kommo=False
    )

    again = f"{AGAIN}Ссылка на сделку в коммо: {DEAL}"
    assert sent(api)[-1] == (GROUP, again), "копия повтором — тем же текстом, что личное"


# --- задача ответа: потолок модели письмо не держит ----------------------------------------


async def test_model_cap_does_not_hold_the_letter_of_a_handed_lead(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    http: httpx.AsyncClient,
    alerts: Alerts,
) -> None:
    lead = await handed(session, http, alerts)
    reply = await lead.wrote("Сколько стоит?")
    await session.commit()
    start = partial(handoff.start, enqueue=lead.queued.append, now=lambda: LATER)
    _job_on_test_base(monkeypatch, session, QUESTION, start, [])

    async def capped(*_: object, **__: object) -> None:
        raise LlmCapExceededError("потолок расхода на модель за сутки")

    monkeypatch.setattr(replies.usage, "ensure_llm_within_cap", capped)

    with pytest.raises(LlmCapExceededError):
        await sales_jobs.handle(reply.id)

    assert lead.queued == [lead.row.id], "письмо ушло телемаркетологу до переноса на завтра"
    snap = (await _snap(session, reply)).model_parse or {}
    assert (snap["kind"], snap["waits"]) == (None, True), "вид разберут завтра заново"
    assert snap["reason"] == f"{replies.CAPPED}; {FORWARDED}"


async def test_failed_forward_does_not_hide_the_model_cap(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    http: httpx.AsyncClient,
    alerts: Alerts,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Письмо переданного лида при потолке модели не передалось: исключение потолка остаётся —
    точка входа переносит задачу на завтра (`sales_reply` → `postpone`), как без сбоя, а не
    отдаёт её повторам очереди; сбой — словами в журнал."""
    lead = await handed(session, http, alerts)
    reply = await lead.wrote("Сколько стоит?")
    await session.commit()
    start = partial(handoff.start, enqueue=lead.queued.append, now=lambda: LATER)
    _job_on_test_base(monkeypatch, session, QUESTION, start, [])

    async def capped(*_: object, **__: object) -> None:
        raise LlmCapExceededError("потолок расхода на модель за сутки")

    async def broken(*_: object) -> None:
        raise RuntimeError("база не ответила")

    monkeypatch.setattr(replies.usage, "ensure_llm_within_cap", capped)
    monkeypatch.setattr(SalesReplies, "forward_unread", broken)

    with (
        caplog.at_level(logging.ERROR, logger=sales_jobs.__name__),
        pytest.raises(LlmCapExceededError, match="потолок расхода на модель за сутки"),
    ):
        await sales_jobs.handle(reply.id)

    assert "письмо переданного лида телемаркетологу не передано" in caplog.text
    assert "база не ответила" in caplog.text, "трассировка сбоя — в журнале"
    assert lead.queued == [], "письмо передаст завтрашний разбор"


async def test_switched_off_sales_forward_nothing(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession, http: httpx.AsyncClient, alerts: Alerts
) -> None:
    lead = await handed(session, http, alerts)
    reply = await lead.wrote("Сколько стоит?")
    monkeypatch.setattr(replies.cfg, "ENABLED", False)

    handled = await lead.replies(QUESTION).handle(reply.id)

    assert (handled.again, handled.handoff_thread, lead.queued) == (False, None, [])


# --- ревизия ключа сообщения ---------------------------------------------------------------


async def test_told_reply_revision_goes_down_and_up(session: AsyncSession) -> None:
    connection = await session.connection()

    down, up = await connection.run_sync(
        columns_down_and_up, REVISION, "sales_handoffs", {"told_reply_id", "told_again"}
    )

    assert (down, up) == (set(), {"told_reply_id", "told_again"})
