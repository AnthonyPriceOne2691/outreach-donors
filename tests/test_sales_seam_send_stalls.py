"""Поток писем продаж не встаёт молча — ревью стыков R4: автоотправка и автосборка в бою.

Три остановки, которые до ревью не давали ни тревоги, ни пересборки:

- **«Отправителя» поправили** — подпись или физический адрес: письма в очереди собраны с
  прежними, и отправка отказывает каждому («соберите очередь заново»). Проход автоотправки
  перебирал окно каждую минуту, отказ по письму — не остановка, тревоги нет; автосборка
  считала эти письма очередью на сутки и не собирала; экран писал «Письма уходят».
- **Лидов снял стоп-лист продаж** (пополнили список клиентов): их письма остаются в очереди
  и получают отказ при отправке — автосборка считала и их.
- **Продажи включены, а к почте не подключены** (строку предохранителя удалили, поле
  «Отправителя» стёрли): проход стоял «по делу», без громкой строки и без тревоги, а сторож
  почты продаж молчит, пока продажи не подключены.

Тревога — подставная (`send_alert` запоминает текст), очередь задач — подставная, модель —
подделка коридора; база — настоящая. Тексты и адреса выдуманы.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import Any

import pytest
from backend.config import sales as sales_cfg
from backend.features.core.domain import AuditAction, MessageStatus, Stage
from backend.features.core.models.access import AuditLogModel
from backend.features.core.models.outreach import MessageModel
from backend.features.letters.chain import FIRST_STEP
from backend.features.letters.repository import LetterRepository
from backend.features.sales import (
    auto_lines,
    autobuild,
    autosend,
    autosend_jobs,
    off_queue,
    queue,
    stoplist_leads,
    verifier,
)
from backend.features.sales.autosend import Pass, Stop
from backend.features.sales.autosend_jobs import ALARM, RESUMED, Watch
from backend.features.sales.models import SalesLeadModel, SalesStoplistModel, SalesThreadModel
from backend.features.sales.queue_start import build_job_id
from redis.exceptions import ConnectionError as RedisConnectionError
from rq.job import JobStatus
from rq.results import Result
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.test_sales_autobuild import _AutoJobs
from tests.test_sales_clean_api import _Result
from tests.test_sales_send import _transports

NOW = w.NOW
MINUTE = timedelta(minutes=1)
JANE = "jane@acme.example.test"
OLGA = "olga@beta.example.test"
IVAN = "ivan@gamma.example.test"
#: Подпись «Отправителя» после правки на экране.
NEW_SIGNATURE = "Mira Testova\nA renamed made-up agency"


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    return await w.world(session, monkeypatch)


@pytest.fixture
def told(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Тревоги прохода — списком; Telegram их принимает."""
    said: list[str] = []

    async def alert(text: str) -> bool:
        said.append(text)
        return True

    monkeypatch.setattr(autosend_jobs, "send_alert", alert)
    return said


@pytest.fixture
def switched_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Тумблеры включены, предохранитель почты продаж снят строкой с пустым значением."""
    monkeypatch.setattr(sales_cfg, "ENABLED", True)
    monkeypatch.setattr(sales_cfg, "AUTOSEND", True)
    monkeypatch.setenv("OUTREACH_SALES_ALLOWED_RECIPIENTS", "")


async def _queued(session: AsyncSession, world: w.World, *emails: str) -> list[MessageModel]:
    for email in emails:
        await w.lead(session, world.hypothesis_id, email, name=email.split("@")[0].title())
    await queue.build(
        session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=len(emails)
    )
    rows = await session.scalars(
        select(MessageModel).where(MessageModel.step == FIRST_STEP).order_by(MessageModel.id)
    )
    return list(rows)


def _on_test_base(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> list[Pass]:
    """Проход `tick` — на тестовой сессии и нулевой почте; итоги проходов — списком."""
    source = _transports()
    seen: list[Pass] = []

    async def work(now: datetime) -> Pass:
        found = await autosend.run(session, source, now=now)
        seen.append(found)
        return found

    monkeypatch.setattr(autosend_jobs, "_pass", work)
    return seen


# --- «Отправителя» поправили: письма очереди собраны с прежней подписью ----------------------


async def test_every_letter_of_the_window_refused_is_a_stall_with_an_alarm_after_15_minutes(
    session: AsyncSession,
    world: w.World,
    monkeypatch: pytest.MonkeyPatch,
    switched_on: None,
    told: list[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Подпись поправили на экране — оба письма очереди с прежней, и каждое получает отказ.
    Письма в окне и свободный кап есть, а не уходит ни одно: проход стоит не по делу. Через 15
    минут — одна тревога словами, журнал называет причину каждого письма один раз. Очередь
    собрали заново — письма пошли, «прошло» одной строкой."""
    caplog.set_level(logging.INFO, logger="backend.features.sales.autosend_jobs")
    letters = await _queued(session, world, JANE, OLGA)
    await w.settings(session, signature=NEW_SIGNATURE)
    passes = _on_test_base(session, monkeypatch)
    watch = Watch()

    for minutes in (0, 5, 14):
        await autosend_jobs.tick(now=NOW + MINUTE * minutes, watch=watch)
    quiet = list(told)
    for minutes in (15, 16, 30):
        await autosend_jobs.tick(now=NOW + MINUTE * minutes, watch=watch)
    alarmed = list(told)
    await queue.build(session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=2)
    await autosend_jobs.tick(now=NOW + MINUTE * 31, watch=watch)

    assert quiet == []
    [alarm] = alarmed
    assert alarm == (
        f"{ALARM}. ни одно письмо в окне получателя не ушло — письмо не готово к отправке: 2; "
        "причина каждого — в журнале разбора"
    )
    assert passes[0].stop is Stop.REFUSED_ALL
    assert told[1:] == [f"{RESUMED} (стояла: {alarm.removeprefix(ALARM + '. ')})"]
    await session.refresh(letters[0])
    assert letters[0].status is MessageStatus.SENT
    journal = [record.getMessage() for record in caplog.records]
    stale = (
        f"продажи: автоотправка — письмо №{letters[0].id} не ушло: письмо не готово к отправке — "
        f"Письмо №{letters[0].id} не уходит: подписи и физического адреса из настроек "
        "отправителя в конце письма нет — письмо собрано с прежними: соберите очередь заново"
    )
    assert journal.count(stale) == 1


async def test_one_refused_letter_among_going_ones_is_no_stall(
    session: AsyncSession, world: w.World
) -> None:
    """Отказ одному письму, а следующее ушло — проход идёт, это не остановка."""
    letters = await _queued(session, world, JANE, OLGA)
    session.add(SalesStoplistModel(email=JANE))
    await session.flush()

    found = await autosend.run(session, _transports(), now=NOW)

    assert found.refused == {letters[0].id: "стоп-лист"}
    assert found.sent == {letters[1].id: w.SALES_BOX}
    assert found.stop is None


async def test_autobuild_counts_only_letters_that_will_go_and_rebuilds_a_changed_signature(
    session: AsyncSession, world: w.World
) -> None:
    """Ящик продаж — два письма в сутки, в очереди два письма: хватает. Подпись поправили —
    оба письма не уйдут, очередь на сутки пуста: автосборка ставит сборку гипотезы на два
    письма, и сборка пересобирает их с нынешней подписью — очереди снова хватает."""
    world.sales_box.daily_cap = 2
    await _queued(session, world, JANE, OLGA)
    jobs = _AutoJobs()
    enough = await autobuild.plan(session, jobs, now=NOW)

    await w.settings(session, signature=NEW_SIGNATURE)
    stale = await autobuild.plan(session, jobs, now=NOW)
    report = await queue.build(
        session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=stale.limit
    )
    again = await autobuild.plan(session, jobs, now=NOW)

    assert (enough.need, enough.have, enough.short) == (2, 2, 0)
    assert (stale.have, stale.short, stale.hypothesis_id) == (0, 2, world.hypothesis_id)
    assert (report.prepared, report.refreshed) == (0, 2)
    assert (again.have, again.short) == (2, 0)
    # Счёт кнопки «Отправить очередь» — все письма очереди, как был: пачка своё скажет по письму.
    assert await LetterRepository(session).queued_count(stage=Stage.SALES) == 2


async def test_letters_of_leads_closed_by_the_sales_stoplist_leave_the_queue_short(
    session: AsyncSession, world: w.World
) -> None:
    """Список клиентов пополнили — лид снят, его письмо осталось в очереди и не уйдёт
    (отправка откажет снятому лиду). Очередь на сутки — без него: автосборка соберёт письмо
    следующему лиду, а не будет ждать письма, которое не уйдёт никогда."""
    world.sales_box.daily_cap = 2
    await _queued(session, world, JANE, OLGA)
    await w.lead(session, world.hypothesis_id, IVAN)
    session.add(SalesStoplistModel(email=JANE))
    await session.flush()
    closed = await stoplist_leads.close(session, now=NOW)

    found = await autobuild.plan(session, _AutoJobs(), now=NOW)

    assert closed == 1
    assert (found.need, found.have, found.short) == (2, 1, 1)
    assert found.hypothesis_id == world.hypothesis_id


async def test_a_lead_edited_after_its_letter_makes_the_queue_short_and_rebuilds(
    session: AsyncSession, world: w.World
) -> None:
    """Лида поправили после сборки — его письмо с прежними данными отправка не пустит («собрано
    до правки лида»): в счёт очереди оно не идёт, а гипотеза — к сборке, которая его пересоберёт."""
    world.sales_box.daily_cap = 2
    letters = await _queued(session, world, JANE, OLGA)
    lead = await session.scalar(
        select(SalesLeadModel)
        .join(SalesThreadModel, SalesThreadModel.lead_id == SalesLeadModel.id)
        .where(SalesThreadModel.thread_id == letters[0].thread_id)
    )
    assert lead is not None
    # Правка — позже письма: время правки ставит база, а транзакция теста у всех одна.
    lead.updated_at = letters[0].updated_at + MINUTE
    await session.flush()

    found = await autobuild.plan(session, _AutoJobs(), now=NOW)

    assert (found.have, found.short, found.hypothesis_id) == (1, 1, world.hypothesis_id)


async def test_a_letter_of_a_lead_checked_by_the_made_up_verifier_under_the_live_one_is_not_counted(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Живой проверяльщик включён (`SALES_VERIFIER_PROVIDER=live`), а адрес лида проверял
    выдуманный: письмо не уйдёт, пока лида не очистят заново, и сборка его не тронет — в счёт
    очереди оно не идёт, и гипотеза к сборке из-за него не встаёт."""
    world.sales_box.daily_cap = 2
    letters = await _queued(session, world, JANE, OLGA)
    lead = await session.scalar(
        select(SalesLeadModel)
        .join(SalesThreadModel, SalesThreadModel.lead_id == SalesLeadModel.id)
        .where(SalesThreadModel.thread_id == letters[0].thread_id)
    )
    assert lead is not None
    lead.verification_status = f"{verifier.FIXTURE}:valid"
    await session.flush()
    monkeypatch.setattr(sales_cfg, "VERIFIER_PROVIDER", verifier.LIVE)

    found = await autobuild.plan(session, _AutoJobs(), now=NOW)

    assert (found.have, found.short) == (1, 1)
    assert (found.hypothesis_id, found.why) == (None, autobuild.NO_CANDIDATES)


# --- экран «Очередь писем» говорит то же, что проход ----------------------------------------------


@pytest.mark.parametrize(
    ("autobuild_on", "advice"),
    [(True, auto_lines.BY_AUTOBUILD), (False, auto_lines.BY_BUTTON)],
)
async def test_the_screen_says_the_flow_stands_when_every_letter_of_the_window_has_the_old_signature(
    session: AsyncSession,
    world: w.World,
    monkeypatch: pytest.MonkeyPatch,
    switched_on: None,
    autobuild_on: bool,
    advice: str,
) -> None:
    """Подпись поправили на экране «Отправитель» — проход стоит, отказывая каждому письму окна,
    а строка автоотправки писала «Ящики на паузе», потом «Письма уходят» (замер стенда 13.10:
    15 минут до тревоги экран говорил, что всё идёт). Теперь строка говорит, что не уйдёт ни
    одно письмо окна и что делать: с автосборкой — пересоберёт её проход, без неё — кнопка.
    Очередь собрали заново — снова «Письма уходят»."""
    monkeypatch.setattr(sales_cfg, "AUTOBUILD", autobuild_on)
    await _queued(session, world, JANE, OLGA)
    going = await auto_lines.autosend_line(session, NOW)
    await w.settings(session, signature=NEW_SIGNATURE)

    stands = await auto_lines.autosend_line(session, NOW)
    await queue.build(session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=2)
    again = await auto_lines.autosend_line(session, NOW)

    assert going.line.endswith("В окне получателя сейчас 2 письма. Письма уходят.")
    assert stands.enabled is True
    assert stands.line.endswith(
        f"В окне получателя сейчас 2 письма. Стоит: {auto_lines.STALE}: {advice}."
    )
    assert again.line == going.line


async def test_letters_of_leads_closed_by_the_sales_stoplist_leave_the_queue_without_an_alarm(
    session: AsyncSession, world: w.World, switched_on: None
) -> None:
    """Список клиентов пополнили — оба лида с письмами в окне сняты, и их письма уходят из очереди
    в момент снятия, словами стоп-листа (`off_queue.of_leads`). До этого проход отказывал каждому
    письму окна и через 15 минут поднимал тревогу «отказ каждому письму окна» о письмах, которые
    не уйдут никогда; теперь очередь пуста — её пополнит сборка. Одно письмо осталось — оно уходит."""
    letters = await _queued(session, world, JANE, OLGA)
    session.add(SalesStoplistModel(email=JANE))
    await session.flush()
    await stoplist_leads.close(session, now=NOW)
    one_goes = await auto_lines.autosend_line(session, NOW)
    session.add(SalesStoplistModel(email=OLGA))
    await session.flush()
    await stoplist_leads.close(session, now=NOW)

    none_left = await auto_lines.autosend_line(session, NOW)
    found = await autosend.run(session, _transports(), now=NOW)

    assert one_goes.line.endswith("В окне получателя сейчас 1 письмо. Письма уходят.")
    assert none_left.line.endswith(
        "В окне получателя сейчас 0 писем. Стоит: в очереди продаж писем нет — их собирает "
        "сборка очереди."
    )
    assert (found.stop, found.refused) == (Stop.EMPTY, {})
    for letter, email in zip(letters, (JANE, OLGA), strict=True):
        await session.refresh(letter)
        assert (letter.status, letter.failure_reason) == (
            MessageStatus.STOPPED,
            f"{off_queue.LEAD_CLOSED}: стоп-лист продаж: адрес {email}",
        )


async def test_the_screen_judges_the_letters_of_the_window_not_the_whole_queue(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch, switched_on: None
) -> None:
    """Письмо лида, у которого ночь, уйдёт как есть, но не сейчас; единственное письмо окна
    собрано до правки лида — проход отказывает ему и стоит. Строка судит письма окна, а не всю
    очередь: «Стоит», а не «Письма уходят»."""
    monkeypatch.setattr(sales_cfg, "AUTOBUILD", False)
    monkeypatch.setattr(sales_cfg, "SEND_OPENS", time(9))
    monkeypatch.setattr(sales_cfg, "SEND_CLOSES", time(17))
    await w.lead(session, world.hypothesis_id, JANE, name="Jane")
    await w.lead(session, world.hypothesis_id, OLGA, name="Olga", timezone="America/Los_Angeles")
    await queue.build(session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=2)
    jane = await session.scalar(select(SalesLeadModel).where(SalesLeadModel.email == JANE))
    assert jane is not None
    letter = await session.scalar(
        select(MessageModel)
        .join(SalesThreadModel, SalesThreadModel.thread_id == MessageModel.thread_id)
        .where(SalesThreadModel.lead_id == jane.id)
    )
    assert letter is not None
    jane.updated_at = letter.updated_at + MINUTE
    await session.flush()

    line = await auto_lines.autosend_line(session, NOW)
    found = await autosend.run(session, _transports(), now=NOW)

    assert line.line.endswith(
        f"В окне получателя сейчас 1 письмо. Стоит: {auto_lines.STALE}: {auto_lines.BY_BUTTON}."
    )
    assert (found.stop, list(found.refused)) == (Stop.REFUSED_ALL, [letter.id])


# --- продажи включены, а к почте не подключены -------------------------------------------------


async def test_sales_on_but_not_connected_alarm_after_15_minutes_even_with_an_empty_queue(
    session: AsyncSession,
    world: w.World,
    monkeypatch: pytest.MonkeyPatch,
    switched_on: None,
    told: list[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Физический адрес «Отправителя» стёрли: продажи не подключены, письма не уходят и не
    собираются — очередь пуста, писем в окне нет. Автоотправка включена владельцем, значит
    стоять ей не по делу: через 15 минут — тревога словами, чего не хватает; журнал — громкой
    строкой один раз. Адрес вернули — «прошло»."""
    caplog.set_level(logging.INFO, logger="backend.features.sales.autosend_jobs")
    await w.settings(session, physical_address=None)
    _on_test_base(session, monkeypatch)
    watch = Watch()

    for minutes in (0, 5, 14):
        await autosend_jobs.tick(now=NOW + MINUTE * minutes, watch=watch)
    quiet = list(told)
    for minutes in (15, 16):
        await autosend_jobs.tick(now=NOW + MINUTE * minutes, watch=watch)
    alarmed = list(told)
    await w.settings(session)
    await autosend_jobs.tick(now=NOW + MINUTE * 17, watch=watch)

    missing = (
        "продажи к почте не подключены — не задан физический адрес — заполните на экране "
        "«Продажи» → «Отправитель»"
    )
    assert quiet == []
    assert alarmed == [f"{autosend_jobs.OFFLINE}. {missing}"]
    assert told[1:] == [f"{RESUMED} (стояла: {missing})"]
    [line] = [r for r in caplog.records if "автоотправка стоит" in r.getMessage()][:1]
    assert line.levelno == logging.WARNING
    assert line.getMessage() == f"продажи: автоотправка стоит — {missing}"


async def test_sales_switched_off_is_no_alarm_however_long(
    monkeypatch: pytest.MonkeyPatch, told: list[str]
) -> None:
    """Продажи выключены (`SALES_ENABLED`) — проход не идёт вовсе: молчат они по решению."""
    monkeypatch.setattr(sales_cfg, "ENABLED", False)
    monkeypatch.setattr(sales_cfg, "AUTOSEND", True)
    watch = Watch()

    for minutes in range(0, 60, 5):
        await autosend_jobs.tick(now=NOW + MINUTE * minutes, watch=watch)

    assert told == []


# --- автосборка: очередь задач, подключение, журнал, итог упавшей сборки ------------------------


class _RefusingEnqueue(_AutoJobs):
    """Очередь задач ответила на проверку, а на постановку — нет (Redis лёг между ними)."""

    def enqueue(self, *args: object, **kwargs: object) -> Any:
        raise RedisConnectionError("очередь задач не отвечает — выдуманные слова теста")


async def test_a_job_queue_failing_at_the_enqueue_is_said_in_words_and_nothing_is_written(
    session: AsyncSession, world: w.World
) -> None:
    """Сборку не поставить — проход не падает и журнал действий не пишет сборку, которой нет:
    словами «очередь задач не отвечает», следующий проход попробует снова."""
    await w.lead(session, world.hypothesis_id, JANE)

    found = await autobuild.run(session, _RefusingEnqueue(), now=NOW)

    assert (found.hypothesis_id, found.why) == (None, autobuild.QUEUE_SILENT)
    started = await session.scalars(
        select(AuditLogModel).where(AuditLogModel.action == AuditAction.RUN_STARTED)
    )
    assert list(started) == []


async def test_autobuild_of_sales_not_connected_says_what_is_missing(
    session: AsyncSession, world: w.World
) -> None:
    """Физический адрес «Отправителя» стёрли — автосборка не собирает и говорит чего не хватает
    теми же словами, что отказ отправки; сборка не ставится."""
    await w.lead(session, world.hypothesis_id, JANE)
    await w.settings(session, physical_address=None)
    jobs = _AutoJobs()

    found = await autobuild.run(session, jobs, now=NOW)

    assert found.why == (
        "продажи к почте не подключены — не задан физический адрес — заполните на экране "
        "«Продажи» → «Отправитель»"
    )
    assert jobs.enqueued == []


def test_the_journal_says_why_autobuild_waits_on_change_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Проход раз в десять минут: строка журнала — при смене причины, а не на каждом проходе."""
    caplog.set_level(logging.INFO, logger="backend.features.sales.autobuild")
    said = autobuild.Said()
    waits = autobuild.Plan(need=5, have=0, why=autobuild.NO_CANDIDATES)

    for found in (waits, waits, autobuild.Plan(need=5, have=5), waits):
        said.tell(found)

    lines = [record.getMessage() for record in caplog.records]
    assert lines == [f"продажи: автосборка не собирает — {autobuild.NO_CANDIDATES}"] * 2


@pytest.mark.parametrize(
    ("result", "words"),
    [
        (None, ": упала — причина в журнале задач."),
        (
            {"error": "продажи к почте не подключены — выдуманная причина", "permanent": True},
            ": не выполнена — продажи к почте не подключены — выдуманная причина.",
        ),
    ],
)
async def test_a_failed_auto_build_is_said_on_the_screen(
    session: AsyncSession,
    world: w.World,
    monkeypatch: pytest.MonkeyPatch,
    result: dict[str, object] | None,
    words: str,
) -> None:
    """Сборка, поставленная самой, упала в воркере (модель недоступна — исключение, повторы
    кончились) или не выполнилась (продажи не подключены) — экран очереди говорит это в строке
    автосборки, а не «собрано 0»."""
    monkeypatch.setattr(sales_cfg, "AUTOBUILD", True)
    await w.lead(session, world.hypothesis_id, JANE)
    jobs = _AutoJobs()
    await autobuild.run(session, jobs, now=NOW)
    job = jobs.known[build_job_id(world.hypothesis_id)]
    job.status = JobStatus.FAILED if result is None else JobStatus.FINISHED
    jobs.redis.results[Result.get_key(job.id)] = _Result(result)

    line = await auto_lines.autobuild_line(session, jobs, NOW)

    assert line.line.endswith(words)
