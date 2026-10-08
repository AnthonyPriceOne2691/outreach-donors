"""Ревью стыков передачи лида (D1–D4): то, что прежние тесты не держали.

Kommo — `KommoFixture` и её подклассы, Telegram — `SalesBot` поверх `httpx.MockTransport`,
тревоги — список (оснастка `test_sales_handoff.py`). Гибель задачи посреди работы (выкатка,
нехватка памяти) — исключение вне `Exception`: так её видит код изнутри, ни одна ветка
`except Exception` его не ловит. Задача, умершая так, теряет всё незакоммиченное: её сессия —
своя точка сохранения на соединении теста (`create_savepoint`), и закрытие откатывает её.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import httpx
import pytest
from backend.config import sales as cfg
from backend.features.sales import handoff
from backend.features.sales.kommo import CreatedLead, KommoFixture, NewLead
from backend.features.sales.models import HandoffKommo, HandoffTelegram
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.test_sales_handoff import (
    NOW,
    Alerts,
    alerts,
    api,
    deps,
    hand_off,
    http,
    reread,
    sent,
)
from tests.test_sales_handoff_rows import Dialog, answer, sales_dialog
from tests.test_sales_telegram import TOKEN, Recorder

__all__ = ["alerts", "api", "http"]  # оснастка передачи: тревоги, бот продаж, клиент httpx


class _Killed(BaseException):
    """Процесс задачи убит посреди работы — `except Exception` этого не ловит."""


def _job_sessions(session: AsyncSession) -> async_sessionmaker[AsyncSession]:
    """Сессии задач — своими точками сохранения: незакоммиченное умирает с задачей."""
    return async_sessionmaker(
        bind=session.bind, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )


class _DiesOnTheNote(KommoFixture):
    """Сделку Kommo завёл, а задача умирает на примечании."""

    dying = True

    async def add_note(self, lead_id: int, text: str) -> int:
        if self.dying:
            raise _Killed
        return await super().add_note(lead_id, text)


class _DiesInsideTheDeal(KommoFixture):
    """Kommo сделку завёл, а ответа задача не дождалась: умерла посреди записи."""

    dying = True

    async def create_complex_lead(self, lead: NewLead) -> CreatedLead:
        made = await super().create_complex_lead(lead)
        if self.dying:
            raise _Killed
        return made


# --- D1: одна сделка на диалог и после гибели задачи ---------------------------------------


async def test_deal_number_outlives_a_job_killed_before_its_note(
    session: AsyncSession, http: httpx.AsyncClient, alerts: Alerts
) -> None:
    """Номер сделки коммитится сразу после ответа Kommo: задача, умершая на следующем
    запросе, его не теряет, и следующая пишет примечание к той же сделке. Мутант «номер
    не коммитится до следующего запроса» прежние тесты проходил зелёным: их сбои ловит
    `except Exception`, и снятие захвата коммитило номер заодно."""
    dialog = await sales_dialog(session)
    row = await handoff.start(session, dialog.thread.id, enqueue=lambda _id: None, now=lambda: NOW)
    kommo = _DiesOnTheNote()
    jobs = _job_sessions(session)
    async with jobs() as dying:
        with pytest.raises(_Killed):
            await handoff.process(dying, row.id, deps(http, alerts, kommo))
    kommo.dying = False
    stale = NOW + timedelta(seconds=cfg.HANDOFF_CLAIM_SEC + 1)

    async with jobs() as next_job:
        await handoff.process(next_job, row.id, deps(http, alerts, kommo, at=stale))

    assert list(kommo.leads) == [9301], "вторая сделка после гибели задачи"
    assert len(kommo.leads[9301].notes) == 1
    row = await reread(session, row.id)
    assert (row.kommo, row.kommo_lead_id) == (HandoffKommo.DONE, 9301)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "решение владельца: окно «Kommo сделку завёл — номер ещё не записан» — задача, "
        "умершая в нём (выкатка посреди запроса), на повторе заводит вторую сделку; правка — "
        "состояние «запись идёт» до запроса и разбор его следующей задачей"
    ),
)
async def test_job_killed_inside_the_deal_write_makes_no_second_deal(
    session: AsyncSession, http: httpx.AsyncClient, alerts: Alerts
) -> None:
    dialog = await sales_dialog(session)
    row = await handoff.start(session, dialog.thread.id, enqueue=lambda _id: None, now=lambda: NOW)
    kommo = _DiesInsideTheDeal()
    jobs = _job_sessions(session)
    async with jobs() as dying:
        with pytest.raises(_Killed):
            await handoff.process(dying, row.id, deps(http, alerts, kommo))
    kommo.dying = False
    stale = NOW + timedelta(seconds=cfg.HANDOFF_CLAIM_SEC + 1)

    async with jobs() as next_job:
        await handoff.process(next_job, row.id, deps(http, alerts, kommo, at=stale))

    assert len(kommo.leads) == 1, "повтор после гибели посреди записи завёл вторую сделку"


# --- D2: срок прохода и постановка задачи ------------------------------------------------


async def test_start_commits_the_row_before_the_job_is_queued(session: AsyncSession) -> None:
    """Задача, взятая воркером до коммита, строки передачи не увидит и кончится постоянным
    «передачи нет»: `start` ставит её только после коммита строки и срока прохода."""
    dialog = await sales_dialog(session)
    queued: list[tuple[int, bool]] = []

    def enqueue(handoff_id: int) -> None:
        queued.append((handoff_id, session.in_transaction()))

    row = await handoff.start(session, dialog.thread.id, enqueue=enqueue, now=lambda: NOW)

    assert queued == [(row.id, False)], "задача поставлена до коммита строки"
    assert row.due_at == NOW + timedelta(seconds=cfg.HANDOFF_RETRY_SEC)


async def test_queue_refusal_of_any_kind_after_the_row_is_left_to_the_pass(
    session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """Строка закоммичена — передачу доведёт проход повторов, чем бы ни отказала очередь
    (не только `RedisError`: адрес Redis не разобран, ошибка клиента очереди). Отказ
    постановки — строка в журнал, а не исключение вызывающему: иначе разбор ответа счёл бы,
    что передачи нет, и ответ ждал бы человека при живой передаче (ревью стыков, B5)."""
    dialog = await sales_dialog(session)

    def refuse(_handoff_id: int) -> None:
        raise ValueError("адрес очереди не разобран")

    with caplog.at_level(logging.ERROR, logger="backend.features.sales.handoff"):
        row = await handoff.start(session, dialog.thread.id, enqueue=refuse, now=lambda: NOW)

    assert "задача передачи лида не поставлена" in caplog.text
    assert "адрес очереди не разобран" in caplog.text
    later = NOW + timedelta(seconds=cfg.HANDOFF_RETRY_SEC + 7)
    assert await handoff.due(session, now=later) == [row.id]


# --- D3: токен бота — и в непечатном виде не роняет передачу ----------------------------------


async def test_bot_token_with_a_control_character_is_undelivered_in_words(
    session: AsyncSession,
    http: httpx.AsyncClient,
    api: Recorder,
    alerts: Alerts,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Табуляция в токене бота (склейка при копировании): адрес Bot API не собрать, и httpx
    бросает `InvalidURL` — не `HTTPError`. Раньше он пролетал мимо отказов бота: задача
    падала, проход повторов ставил её снова каждые несколько минут, а ни «не доставлено»,
    ни тревоги не было. Теперь — отказ словами без токена и тревога эксплуатации."""
    broken = f"{TOKEN[:10]}\t{TOKEN[10:]}"
    monkeypatch.setattr(cfg, "TELEGRAM_BOT_TOKEN", broken)
    dialog = await sales_dialog(session)

    row = await hand_off(session, dialog, deps(http, alerts, KommoFixture()))

    assert row.telegram is HandoffTelegram.UNDELIVERED
    assert row.last_error is not None
    assert "SALES_TELEGRAM_BOT_TOKEN" in row.last_error
    assert TOKEN[10:] not in row.last_error, "токен в тексте ошибки"
    assert api.seen == [], "запроса с таким адресом не было"
    [alert] = alerts
    assert "не доставлено" in alert
    assert TOKEN[10:] not in alert


# --- D4: гонка ответа и идущей задачи ----------------------------------------------------------


def _redis_down(_handoff_id: int) -> None:
    raise RedisConnectionError("очередь не отвечает")


class _AnsweredDuringTheNote(KommoFixture):
    """Пока задача пишет примечание, лид отвечает ещё раз — а очередь в эту минуту лежит:
    триггер своей сессией заводит работу, но задачу поставить не может."""

    def __init__(self, session: AsyncSession, dialog: Dialog) -> None:
        super().__init__()
        self._session = session
        self._dialog: Dialog | None = dialog

    async def add_note(self, lead_id: int, text: str) -> int:
        noted = await super().add_note(lead_id, text)
        dialog, self._dialog = self._dialog, None
        if dialog is not None:
            moment = NOW + timedelta(minutes=2)
            factory = async_sessionmaker(bind=self._session.bind, expire_on_commit=False)
            async with factory() as trigger:
                await answer(trigger, dialog.thread, dialog.message, "Созвон в четверг?", at=moment)
                await handoff.start(
                    trigger, dialog.thread.id, enqueue=_redis_down, now=lambda: moment
                )
        return noted


async def test_answer_during_a_job_while_the_queue_is_down_is_noted_without_another_answer(
    session: AsyncSession, http: httpx.AsyncClient, api: Recorder, alerts: Alerts
) -> None:
    """«Два отказа разом» (находка 5.3): ответ пришёл во время задачи, а очередь лежала —
    итог идущей задачи затирал срок прохода, и ответ ждал следующего ответа лида. Теперь
    задача сама видит ответ новее того, с которым начала, и оставляет срок: проход возьмёт
    передачу, и примечание ляжет к той же сделке без второго сообщения телемаркетологу."""
    dialog = await sales_dialog(session)
    row = await handoff.start(session, dialog.thread.id, enqueue=lambda _id: None, now=lambda: NOW)
    kommo = _AnsweredDuringTheNote(session, dialog)

    await handoff.process(session, row.id, deps(http, alerts, kommo))

    later = NOW + timedelta(seconds=cfg.HANDOFF_RETRY_SEC + 1)
    assert await handoff.due(session, now=later) == [row.id], "ответ ждал бы следующего ответа"
    await handoff.process(session, row.id, deps(http, alerts, kommo, at=later))
    notes = list(kommo.leads[9301].notes.values())
    assert len(notes) == 2
    assert "четверг" in notes[1]
    assert list(kommo.leads) == [9301]
    assert len(sent(api)) == 2, "телемаркетологу второй раз не пишем: ссылка та же"
    row = await reread(session, row.id)
    assert (row.kommo, row.due_at) == (HandoffKommo.DONE, None)
