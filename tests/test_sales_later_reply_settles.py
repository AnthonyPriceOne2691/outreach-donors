"""Решение по позднему ответу лида снимает ожидание с ранних ответов того же диалога.

Проверка прода 09.11.2026: лид ответил одним именем — ответ ждал человека; следующим письмом
назвал адрес коллеги — модуль завёл нового лида и закрыл диалог, а первый ответ так и висел в
«ждёт человека». Теперь окончательное решение по позднему ответу —
диалог закрыт («не интересно», «не сейчас»), отсылка к коллеге, отписка, передача телемаркетологу —
снимает с ожидания и ранние ответы словами «решён следующим ответом №N: <вид>»
(`earlier_replies.settle_earlier`).

Что проверяется:

- снимаются только ранние ответы этого диалога, которые ждут человека и уже разобраны (в снимке
  есть вид); поля решения человека (`reviewed_*`) не трогаются — решал сервис;
- поздний ответ, который ждёт сам (ниже порога, «вопрос», «интересуется», «хочет говорить» до
  передачи), ранние не трогает;
- неразобранный ранний ответ, уже решённый, решённый человеком, автоответ, ответ соседнего
  диалога и более поздний ответ того же диалога — как были;
- «хочет говорить» снимает ранние только удачной передачей (`reply_handoff.pass_on`): отказ
  передачи их не трогает, а сбой самого снятия передачу не отменяет — причина в журнале; и когда
  ошибка базы прервала транзакцию посреди снятия — тоже (точка сохранения).

Разбор — `SalesReplies` с подставным видом (`FakeClassifier`), передача — настоящая
(`handoff.start`) с подставной очередью: Kommo и Telegram тесты не трогают. База настоящая.
Тексты ответов выдуманные, на разных языках; домены — `*.example.test`.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import pytest
from backend.features.core.domain import ReplyKind, Stage
from backend.features.core.models.outreach import ReplyModel
from backend.features.outreach.threads import ThreadState, review_of, summarize
from backend.features.sales import handoff, reply_handoff
from backend.features.sales.earlier_replies import LATER, settle_earlier
from backend.features.sales.models import LeadStatus, SalesHandoffModel
from backend.features.sales.replies import SWITCHED_OFF, SalesReplies
from backend.features.sales.reply_handoff import HANDED_OVER, NOT_HANDED_OVER
from backend.features.sales.reply_kind import KindFound, SalesKind
from backend.features.sales.verifier import FixtureVerifier
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_handoff_rows import SENT_AT, Dialog, answer, sales_dialog
from tests.test_sales_referral import mail_is_accepted
from tests.test_sales_reply_routing import FakeClassifier
from tests.test_sales_switch import sales_switched_on

#: Продажи включены: вид разбирает модель, лида передают; почта домена названного адреса — без DNS.
__all__ = ["mail_is_accepted", "sales_switched_on"]

#: Порог уверенности — явный: тест не зависит от настройки `SALES_REPLY_CONFIDENCE`.
THRESHOLD = 0.8

#: Когда передача заведена — некруглое, заведомо выдуманное время.
AT = datetime(2026, 11, 9, 14, 37, tzinfo=UTC)

#: Ранний ответ ждёт человека: вид назван неуверенно — ниже порога, путь «человек».
UNSURE = {
    "stage": "sales",
    "kind": "referral",
    "confidence": 0.61,
    "route": "manual",
    "waits": True,
    "reason": "модель: назвал другого человека, уверенность 61% ниже порога 80% — решает человек",
}

#: Ранний ответ уже решён сервисом: отсылка к коллеге — новый лид заведён, диалог закрыт.
DECIDED = {
    "stage": "sales",
    "kind": "referral",
    "confidence": 0.93,
    "route": "referral",
    "waits": False,
    "reason": "назвал другого человека: новый лид №4417 прошёл очистку и ждёт письма; диалог закрыт",
}

#: «Хочет говорить» — вид, который передаёт лида.
TALK = KindFound(SalesKind.WANTS_TO_TALK, 0.95, quote="Ring me on Thursday")


async def _waiting(session: AsyncSession, host: str, email: str, first: str) -> Dialog:
    """Диалог продаж, где ранний ответ лида ждёт человека: вид назван ниже порога."""
    dialog = await sales_dialog(session, host=host, email=email, text=first)
    dialog.reply.model_parse = dict(UNSURE)
    await session.flush()
    return dialog


async def _wrote(
    session: AsyncSession, dialog: Dialog, text: str, *, hours: int = 50
) -> ReplyModel:
    """Ещё один ответ лида в тот же диалог — позже первого."""
    at = SENT_AT + timedelta(hours=hours)
    return await answer(
        session, dialog.thread, dialog.message, text, at=at, sender=dialog.lead.email
    )


def _sales(
    session: AsyncSession, found: KindFound, queued: list[int] | None = None
) -> SalesReplies:
    """Задача ответа с подставным видом; передача — настоящая, очередь — список."""
    start = partial(
        handoff.start, enqueue=(queued if queued is not None else []).append, now=lambda: AT
    )
    return SalesReplies(
        session,
        FakeClassifier(found),
        hand_over=start,
        verifier=FixtureVerifier,
        threshold=THRESHOLD,
        now=AT,
    )


async def _reread(session: AsyncSession, reply: ReplyModel) -> ReplyModel:
    """Ответ, как он лежит в базе."""
    found = await session.get(ReplyModel, reply.id, populate_existing=True)
    assert found is not None
    return found


async def _state(session: AsyncSession, dialog: Dialog) -> ThreadState:
    """Состояние диалога в списке — по всем его ответам, как они лежат в базе."""
    replies = await session.scalars(
        select(ReplyModel)
        .where(ReplyModel.thread_id == dialog.thread.id)
        .order_by(ReplyModel.id)
        .execution_options(populate_existing=True)
    )
    return summarize([dialog.message], list(replies), Stage.SALES).state


async def _handoff_of(session: AsyncSession, dialog: Dialog) -> SalesHandoffModel | None:
    found: SalesHandoffModel | None = await session.scalar(
        select(SalesHandoffModel).where(SalesHandoffModel.thread_id == dialog.thread.id)
    )
    return found


def _settled(snapshot: dict[str, Any], later: ReplyModel, words: str) -> dict[str, Any]:
    """Снимок раннего ответа, снятого с ожидания решением по позднему."""
    return {**snapshot, "waits": False, "reason": f"решён следующим ответом №{later.id}: {words}"}


# --- окончательное решение по позднему ответу снимает ранний ----------------------------------


@pytest.mark.parametrize(
    ("found", "texts", "words", "said", "state"),
    [
        pytest.param(
            KindFound(SalesKind.NOT_INTERESTED, 0.95, quote="kein Interesse"),
            (
                "werkstatt.example.test",
                "jonas@werkstatt.example.test",
                "Jonas hier.",
                "Danke, aber wir haben kein Interesse.",
            ),
            "не интересно",
            "не интересно: диалог закрыт",
            ThreadState.REPLIED,
            id="not-interested-de",
        ),
        pytest.param(
            KindFound(SalesKind.NOT_NOW, 0.95, quote="Ahora no"),
            (
                "taller.example.test",
                "lucia@taller.example.test",
                "¿Quién me escribe?",
                "Ahora no, quizá en primavera.",
            ),
            "не сейчас",
            "не сейчас: диалог закрыт",
            ThreadState.REPLIED,
            id="not-now-es",
        ),
        pytest.param(
            KindFound(SalesKind.UNSUBSCRIBE, 0.95, quote="remove me from your list"),
            (
                "harbour.example.test",
                "grace@harbour.example.test",
                "Who is this?",
                "Please remove me from your list.",
            ),
            "просит не писать",
            "просит не писать: адрес закрыт во всех направлениях",
            ThreadState.UNSUBSCRIBED,
            id="unsubscribe-en",
        ),
        pytest.param(
            KindFound(
                SalesKind.REFERRAL,
                0.95,
                quote="лучше напишите коллеге: zakupki@zavod.example.test",
                contact="zakupki@zavod.example.test",
            ),
            (
                "zavod.example.test",
                "olga@zavod.example.test",
                "Тимофей",
                "Спасибо, лучше напишите коллеге: zakupki@zavod.example.test",
            ),
            "назвал другого человека",
            "назвал другого человека: новый лид №",
            ThreadState.REPLIED,
            id="referral-ru",
        ),
    ],
)
async def test_final_decision_on_a_later_answer_settles_the_earlier_one(
    session: AsyncSession,
    found: KindFound,
    texts: tuple[str, str, str, str],
    words: str,
    said: str,
    state: ThreadState,
) -> None:
    host, email, first, second = texts
    dialog = await _waiting(session, host, email, first)
    later = await _wrote(session, dialog, second)

    handled = await _sales(session, found).handle(later.id)
    await session.commit()

    assert handled.waits is False, "поздний ответ решён без человека"
    assert str(handled.reason).startswith(said), "путь вида прошёл на оснастке диалога"
    early = await _reread(session, dialog.reply)
    assert early.model_parse == _settled(UNSURE, later, words)
    assert (early.reviewed_at, early.reviewed_by) == (None, None), "решал сервис, а не человек"
    review = review_of(early, Stage.SALES)
    assert (review.waiting, review.reason) == (
        False,
        f"решён следующим ответом №{later.id}: {words}",
    )
    assert review.reason == f"{LATER} №{later.id}: {words}"
    own = (await _reread(session, later)).model_parse or {}
    assert (own["waits"], own["reason"]) == (False, handled.reason), "поздний — со своим решением"
    assert await _state(session, dialog) is state, "диалог не ждёт человека"


# --- поздний ответ ждёт сам — ранний ждёт как был ---------------------------------------------


@pytest.mark.parametrize(
    ("found", "second", "route"),
    [
        pytest.param(
            KindFound(SalesKind.NOT_INTERESTED, 0.55, quote="Pas sûr"),
            "Pas sûr que ce soit pour nous.",
            "manual",
            id="below-threshold-fr",
        ),
        pytest.param(
            KindFound(SalesKind.QUESTION, 0.95, quote="Quanto costa"),
            "Quanto costa un audit dei link?",
            "agent",
            id="question-it",
        ),
        pytest.param(
            KindFound(SalesKind.INTERESTED, 0.95, quote="Parece interessante"),
            "Parece interessante, contem mais.",
            "agent",
            id="interested-pt",
        ),
    ],
)
async def test_later_answer_that_waits_itself_leaves_the_earlier_one_waiting(
    session: AsyncSession, found: KindFound, second: str, route: str
) -> None:
    dialog = await _waiting(
        session, "atelier.example.test", "claire@atelier.example.test", "Bonjour, c'est qui ?"
    )
    later = await _wrote(session, dialog, second)

    handled = await _sales(session, found).handle(later.id)
    await session.commit()

    assert (handled.route, handled.waits) == (route, True)
    early = await _reread(session, dialog.reply)
    assert early.model_parse == UNSURE
    assert review_of(early, Stage.SALES).waiting is True
    assert await _state(session, dialog) is ThreadState.SALES_PENDING


# --- ранний ответ, которого снятие не касается ------------------------------------------------


@pytest.mark.parametrize(
    "snapshot",
    [
        pytest.param(None, id="no-snapshot"),
        pytest.param(
            {
                "stage": "sales",
                "kind": None,
                "refusal": "модель вернула пустой ответ",
                "route": "manual",
                "waits": True,
                "reason": "модель вернула пустой ответ — задача попробует ещё раз",
            },
            id="model-refused",
        ),
        pytest.param(
            {
                "stage": "sales",
                "kind": None,
                "route": "manual",
                "waits": True,
                "reason": SWITCHED_OFF,
            },
            id="sales-switched-off",
        ),
    ],
)
async def test_earlier_answer_without_a_kind_keeps_waiting(
    session: AsyncSession, snapshot: dict[str, Any] | None
) -> None:
    """Неразобранный ранний ответ задача разбора возьмёт снова: закрытый здесь, он бы открылся."""
    dialog = await sales_dialog(
        session, host="biuro.example.test", email="ola@biuro.example.test", text="Kto pisze?"
    )
    dialog.reply.model_parse = None if snapshot is None else dict(snapshot)
    await session.flush()
    later = await _wrote(session, dialog, "Dziękujemy, nie jesteśmy zainteresowani.")

    found = KindFound(SalesKind.NOT_INTERESTED, 0.95, quote="nie jesteśmy zainteresowani")
    handled = await _sales(session, found).handle(later.id)
    await session.commit()

    assert handled.waits is False
    early = await _reread(session, dialog.reply)
    assert early.model_parse == snapshot
    assert review_of(early, Stage.SALES).waiting is True
    assert await _state(session, dialog) is ThreadState.SALES_PENDING


async def test_decided_reviewed_and_auto_replies_keep_their_snapshots(
    session: AsyncSession,
) -> None:
    """Уже решённый, решённый человеком и автоответ — как были; ждущий рядом с ними — снят."""
    dialog = await sales_dialog(
        session,
        host="dukkan.example.test",
        email="ayse@dukkan.example.test",
        text="Meslektaşıma yazın: satis@dukkan.example.test",
    )
    dialog.reply.model_parse = dict(DECIDED)
    reviewed = await _wrote(session, dialog, "Hmm, kim bu?", hours=30)
    reviewed.model_parse = dict(UNSURE)
    reviewed.reviewed_at = SENT_AT + timedelta(hours=31)
    reviewed.reviewed_by = "seller@ours.example.test"
    auto = await _wrote(session, dialog, "Ofis dışındayım, pazartesi döneceğim.", hours=36)
    auto.kind = ReplyKind.AUTO_REPLY
    auto.model_parse = dict(UNSURE)
    waiting = await _wrote(session, dialog, "Kimsiniz?", hours=40)
    waiting.model_parse = dict(UNSURE)
    await session.flush()
    later = await _wrote(session, dialog, "Şimdi değil, belki sonbaharda.")

    found = KindFound(SalesKind.NOT_NOW, 0.95, quote="Şimdi değil")
    await _sales(session, found).handle(later.id)
    await session.commit()

    assert (await _reread(session, dialog.reply)).model_parse == DECIDED, "решение — своё"
    kept = await _reread(session, reviewed)
    assert (kept.model_parse, kept.reviewed_by) == (UNSURE, "seller@ours.example.test")
    assert kept.reviewed_at == SENT_AT + timedelta(hours=31)
    assert (await _reread(session, auto)).model_parse == UNSURE, "автоответ человека не ждёт"
    settled = await _reread(session, waiting)
    assert settled.model_parse == _settled(UNSURE, later, "не сейчас")


async def test_waiting_answer_of_another_dialog_is_left_alone(session: AsyncSession) -> None:
    neighbour = await _waiting(
        session, "ufficio.example.test", "giulia@ufficio.example.test", "Chi parla?"
    )
    dialog = await _waiting(session, "kantoor.example.test", "daan@kantoor.example.test", "Wie?")
    later = await _wrote(session, dialog, "Nee, dank u, geen interesse.")
    assert neighbour.reply.id < later.id, "по номеру сосед — ранний"

    found = KindFound(SalesKind.NOT_INTERESTED, 0.95, quote="geen interesse")
    await _sales(session, found).handle(later.id)
    await session.commit()

    other = await _reread(session, neighbour.reply)
    assert other.model_parse == UNSURE
    assert review_of(other, Stage.SALES).waiting is True
    assert await _state(session, neighbour) is ThreadState.SALES_PENDING
    own = await _reread(session, dialog.reply)
    assert own.model_parse == _settled(UNSURE, later, "не интересно")


async def test_decision_on_an_older_answer_leaves_a_newer_waiting_one_alone(
    session: AsyncSession,
) -> None:
    """Повтор задачи раннего ответа (модель отказала в прошлый раз) пришёл, когда более поздний
    уже ждёт человека: решение по раннему ответу на поздний не отвечает — снимаются только
    ответы с меньшим номером."""
    dialog = await sales_dialog(
        session,
        host="kontor.example.test",
        email="lena@kontor.example.test",
        text="Kein Bedarf, danke.",
    )
    dialog.reply.model_parse = {
        "stage": "sales",
        "kind": None,
        "refusal": "модель не ответила вовремя",
        "route": "manual",
        "waits": True,
        "reason": "модель не ответила вовремя — задача попробует ещё раз",
    }
    newer = await _wrote(session, dialog, "Wer sind Sie eigentlich?")
    newer.model_parse = dict(UNSURE)
    await session.flush()

    found = KindFound(SalesKind.NOT_INTERESTED, 0.95, quote="Kein Bedarf")
    handled = await _sales(session, found).handle(dialog.reply.id)
    await session.commit()

    assert handled.waits is False
    after = await _reread(session, newer)
    assert after.model_parse == UNSURE
    assert review_of(after, Stage.SALES).waiting is True
    assert await _state(session, dialog) is ThreadState.SALES_PENDING


# --- «хочет говорить»: ранние снимает удачная передача ----------------------------------------


async def test_wants_to_talk_settles_the_earlier_answer_once_the_lead_is_handed_over(
    session: AsyncSession,
) -> None:
    dialog = await _waiting(
        session, "harbour.example.test", "tom@harbour.example.test", "Tom from Harbour here."
    )
    later = await _wrote(session, dialog, "Ring me on Thursday, happy to talk.")
    queued: list[int] = []
    sales = _sales(session, TALK, queued)

    handled = await sales.handle(later.id)
    await session.commit()

    assert (handled.route, handled.waits) == ("handoff", True)
    assert (await _reread(session, dialog.reply)).model_parse == UNSURE, "до передачи — ждёт"

    after = await sales.pass_on(handled)

    row = await _handoff_of(session, dialog)
    assert row is not None, "передача заведена"
    assert queued == [row.id]
    assert (after.waits, after.reason) == (False, f"хочет говорить: {HANDED_OVER}")
    early = await _reread(session, dialog.reply)
    assert early.model_parse == _settled(UNSURE, later, "хочет говорить")
    assert (early.reviewed_at, early.reviewed_by) == (None, None)
    assert review_of(early, Stage.SALES).reason == (
        f"решён следующим ответом №{later.id}: хочет говорить"
    )
    assert await _state(session, dialog) is ThreadState.REPLIED


async def test_refused_handoff_leaves_the_earlier_answer_waiting(session: AsyncSession) -> None:
    """Лида отсеяли, а явной связи диалога с лидом нет — передавать некого (`handoff.lead_of`)."""
    dialog = await _waiting(
        session, "souk.example.test", "layla@souk.example.test", "مرحبا، من معي؟"
    )
    dialog.lead.status = LeadStatus.REJECTED
    later = await _wrote(session, dialog, "اتصلوا بي يوم الخميس من فضلكم")
    queued: list[int] = []
    sales = _sales(session, TALK, queued)

    handled = await sales.handle(later.id)
    await session.commit()
    after = await sales.pass_on(handled)

    assert await _handoff_of(session, dialog) is None
    assert queued == []
    assert after.waits is True
    assert str(after.reason).startswith(f"хочет говорить: {NOT_HANDED_OVER} — ")
    early = await _reread(session, dialog.reply)
    assert early.model_parse == UNSURE
    assert review_of(early, Stage.SALES).waiting is True
    assert await _state(session, dialog) is ThreadState.SALES_PENDING


async def _unreachable(*_args: object) -> tuple[int, ...]:
    """База не ответила на первый же запрос снятия."""
    raise OperationalError("SELECT replies", {}, Exception("база недоступна"))


async def _aborted(session: AsyncSession, reply: ReplyModel, words: str) -> tuple[int, ...]:
    """Ранние ответы уже помечены в сессии, и тут база отвечает ошибкой — транзакция прервана: без
    отката к точке сохранения упали бы итоговый коммит передачи и все запросы после него."""
    await settle_earlier(session, reply, words)
    await session.execute(text("SELECT 1 / 0"))
    return ()


@pytest.mark.parametrize(
    "broken",
    [
        pytest.param(_unreachable, id="db-unreachable"),
        pytest.param(_aborted, id="transaction-aborted"),
    ],
)
async def test_failed_settling_does_not_undo_the_handoff(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
    broken: Callable[..., Awaitable[tuple[int, ...]]],
) -> None:
    """Снятие ранних ответов упало с базой — лид всё равно передан, итог передачи прежний; ранние
    ждут, а почему — в журнале."""
    monkeypatch.setattr(reply_handoff, "settle_earlier", broken)
    dialog = await _waiting(
        session, "chaguan.example.test", "wei@chaguan.example.test", "您好，请问您是哪位？"
    )
    early_id = dialog.reply.id  # после отката к точке сохранения ранний в сессии просрочен
    later = await _wrote(session, dialog, "请周四给我打电话，我们聊聊。")
    queued: list[int] = []
    sales = _sales(session, TALK, queued)

    handled = await sales.handle(later.id)
    await session.commit()
    with caplog.at_level(logging.ERROR, logger="backend.features.sales.reply_handoff"):
        after = await sales.pass_on(handled)

    row = await _handoff_of(session, dialog)
    assert row is not None, "передача заведена"
    assert queued == [row.id]
    assert (after.waits, after.reason) == (False, f"хочет говорить: {HANDED_OVER}")
    own = (await _reread(session, later)).model_parse or {}
    assert (own["waits"], own["reason"]) == (False, f"хочет говорить: {HANDED_OVER}")
    early = await session.get(ReplyModel, early_id, populate_existing=True)
    assert early is not None
    assert early.model_parse == UNSURE, "ранний ждёт"
    [record] = [
        r for r in caplog.records if "ранние ответы диалога с ожидания не сняты" in r.getMessage()
    ]
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None
    assert isinstance(record.exc_info[1], SQLAlchemyError)
    assert record.__dict__["reply"] == later.id
