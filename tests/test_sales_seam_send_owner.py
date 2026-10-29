"""Отправка продаж — решения владельца 13.10 по вопросам ревью стыков отправки (R4, §5).

- **Отказ платформы одному письму (400).** Письмо возвращалось в очередь и бралось проходом
  автоотправки каждую минуту. Теперь после трёх отказов платформы письмо снимается с очереди
  с причиной словами — в проходах, где уходили другие письма: отказ всем — не о письме.
- **F8: добивку не собрать нынешней цепочкой** (цепочку поправили, у лида нет имени). Добивка
  откладывалась на час — каждый час, молча. Теперь уходит текстом, сохранённым при сборке
  письма; нет его — цепочка останавливается с причиной словами — в журнале и на экране очереди.
- **F7: очередь пуста, а автосборка не может собрать** (потолок модели, серия вне коридора,
  сборка падает) при включённой автоотправке. Поток стоял без тревоги. Теперь — одна тревога
  при переходе в это состояние и «прошло» при выходе.

База — настоящая; почта, модель, Telegram и очередь задач — подставные. Тексты и адреса
выдуманы.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from backend.config import llm as llm_cfg
from backend.config import sales as sales_cfg
from backend.features.core import usage
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.outreach import MessageModel
from backend.features.letters import followups
from backend.features.letters.batch import send_queue
from backend.features.letters.chain import FIRST_STEP
from backend.features.letters.sendgrid import PROVIDER_ID_HEADER, SendGridTransport
from backend.features.letters.sending import Sending
from backend.features.letters.transport import LetterRefusalError, Outgoing
from backend.features.ops.alarms import marked
from backend.features.sales import (
    autobuild,
    autobuild_alarm,
    autosend,
    autosend_jobs,
    autosend_refusals,
    chain,
    chain_text,
    corridor,
    followup_texts,
    mail,
    queue,
)
from backend.features.sales.autosend import Pass
from backend.features.sales.autosend_jobs import Watch
from backend.features.sales.queue_start import build_job_id
from rq.job import JobStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.test_mail_accounts import _ByStage
from tests.test_mail_identity import Recording
from tests.test_sales_autobuild import _AutoJobs
from tests.test_sales_autosend import Guarded
from tests.test_sales_followup_texts import EVERY, FOLLOW, Recorder, _chain, _links
from tests.test_sales_send import FIRST_DUE, SECOND_DUE, SIGNED, _first_sent, _lead_of, _seen

NOW = w.NOW
MINUTE = timedelta(minutes=1)
JANE = "jane@acme.example.test"
OTHERS = ("olga@beta.example.test", "ivan@gamma.example.test", "nina@delta.example.test")
#: Отказ платформы письму (400) — её словами.
REFUSAL = '{"errors":[{"message":"made-up bad address"}]}'


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    return await w.world(session, monkeypatch)


@pytest.fixture
def told(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Тревоги в общий канал — списком; Telegram их принимает. Один путь у тревог автоотправки
    и автосборки (`autosend_jobs.say`)."""
    said: list[str] = []

    async def alert(text: str) -> bool:
        said.append(text)
        return True

    monkeypatch.setattr(autosend_jobs, "send_alert", alert)
    return said


@pytest.fixture
def switched_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Продажи и автоотправка включены, предохранитель снят строкой с пустым значением; пауза
    ящика — минута с разбросом: проход раз в две минуты застаёт ящик свободным."""
    monkeypatch.setattr(sales_cfg, "ENABLED", True)
    monkeypatch.setattr(sales_cfg, "AUTOSEND", True)
    monkeypatch.setattr(sales_cfg, "AUTOSEND_GAP_MIN", 1)
    monkeypatch.setenv("OUTREACH_SALES_ALLOWED_RECIPIENTS", "")


async def _letters(session: AsyncSession, world: w.World, *emails: str) -> list[MessageModel]:
    """Первые письма лидов — сборкой продаж, по порядку адресов."""
    for email in emails:
        await w.lead(session, world.hypothesis_id, email, name=email.split("@")[0].title())
    await queue.build(
        session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=len(emails)
    )
    rows = await session.scalars(
        select(MessageModel).where(MessageModel.step == FIRST_STEP).order_by(MessageModel.id)
    )
    return list(rows)


# --- отказ платформы одному письму: после трёх — с очереди --------------------------------------


def _sendgrid(refused: str) -> tuple[_ByStage, list[str]]:
    """Боевой транспорт продаж на подставном HTTP: письму `refused` платформа отвечает 400 (о
    письме), остальные принимает. Второй — адресаты писем, которые дошли до платформы."""
    asked: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        to = json.loads(request.content)["personalizations"][0]["to"][0]["email"]
        asked.append(to)
        if to == refused:
            return httpx.Response(400, text=REFUSAL)
        return httpx.Response(202, headers={PROVIDER_ID_HEADER: f"made-up-{len(asked)}"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    sales = SendGridTransport(api_key=w.OWN_KEY, allowlist=(), http=http)
    return _ByStage(sales=sales, donors=Recording(), advertisers=Recording()), asked


def _on_test_base(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, source: _ByStage
) -> list[Pass]:
    """Проход по расписанию (`autosend_jobs.tick`) — на тестовой сессии и подставной почте."""
    seen: list[Pass] = []

    async def work(now: datetime) -> Pass:
        found = await autosend.run(session, source, now=now)
        seen.append(found)
        return found

    monkeypatch.setattr(autosend_jobs, "_pass", work)
    return seen


async def _passes(count: int, watch: Watch) -> None:
    """Проходы раз в две минуты — ящик к каждому свободен."""
    for step in range(count):
        await autosend_jobs.tick(now=NOW + MINUTE * 2 * step, watch=watch)


async def test_a_letter_the_platform_refuses_leaves_the_queue_after_three_refusals(
    session: AsyncSession,
    world: w.World,
    switched_on: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Мутанты «счёта нет» и «снимается раньше»: платформа отказывает письму Джейн (400), а
    письма соседей принимает. Три прохода — три отказа; на третьем письмо снято с очереди с
    причиной словами, журнал говорит это громкой строкой один раз, и четвёртый проход платформу
    о нём не спрашивает."""
    caplog.set_level(logging.INFO, logger="backend.features.sales.autosend_jobs")
    letters = await _letters(session, world, JANE, *OTHERS)
    source, asked = _sendgrid(JANE)
    _on_test_base(session, monkeypatch, source)
    watch = Watch()

    await _passes(4, watch)

    assert asked.count(JANE) == autosend_refusals.REFUSALS == 3
    await session.refresh(letters[0])
    assert letters[0].status is MessageStatus.STOPPED
    assert letters[0].failure_reason == (
        "платформа отказала письму в 3-й раз — снято с очереди, проход его больше не берёт: "
        f"Платформа отказала (400): {REFUSAL}"
    )
    dropped = [r for r in caplog.records if "снято с очереди" in r.getMessage()]
    assert [r.levelno for r in dropped] == [logging.WARNING]
    assert dropped[0].getMessage().startswith(f"продажи: автоотправка — письмо №{letters[0].id}: ")
    for letter in letters[1:]:
        await session.refresh(letter)
        assert letter.status is MessageStatus.SENT
    assert watch.bounced == {}


async def test_refusals_while_no_other_letter_goes_are_not_counted(
    session: AsyncSession, world: w.World, switched_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Мутант «считается каждый отказ»: в окне одно письмо, и платформа ему отказывает — отказ
    всем не отличить от отказа письму (сбой сборки письма, ящик). Письмо не снимается: о проходе,
    где не ушло ни одно письмо окна, — тревога через 15 минут (`REFUSED_ALL`)."""
    [letter] = await _letters(session, world, JANE)
    source, asked = _sendgrid(JANE)
    _on_test_base(session, monkeypatch, source)
    watch = Watch()

    await _passes(5, watch)

    assert asked == [JANE] * 5
    await session.refresh(letter)
    assert letter.status is MessageStatus.QUEUED
    assert watch.bounced == {}


async def test_a_refusal_by_our_own_rules_is_not_a_platform_refusal(
    session: AsyncSession, world: w.World, switched_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Мутант «считается любой отказ почты»: предохранитель (отказ своих правил до платформы)
    письмо с очереди не снимает, сколько бы проходов ни было."""
    letters = await _letters(session, world, JANE, *OTHERS)
    guarded = Guarded(JANE)
    source = _ByStage(sales=guarded, donors=Recording(), advertisers=Recording())
    _on_test_base(session, monkeypatch, source)

    await _passes(4, Watch())

    await session.refresh(letters[0])
    assert letters[0].status is MessageStatus.QUEUED


async def test_the_count_is_the_memory_of_the_process(
    session: AsyncSession, world: w.World, switched_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Счёт — память процесса разбора, как у тревоги: после перезапуска (новая память) письмо
    получает отказы заново и с очереди не снимается раньше своего третьего."""
    letters = await _letters(session, world, JANE, *OTHERS)
    source, asked = _sendgrid(JANE)
    _on_test_base(session, monkeypatch, source)
    before = Watch()
    await _passes(2, before)

    after = Watch()
    await autosend_jobs.tick(now=NOW + MINUTE * 4, watch=after)

    assert before.bounced == {letters[0].id: 2}
    assert after.bounced == {letters[0].id: 1}
    await session.refresh(letters[0])
    assert letters[0].status is MessageStatus.QUEUED
    assert asked.count(JANE) == 3


async def test_the_button_and_the_batch_do_not_count_platform_refusals(
    session: AsyncSession, world: w.World
) -> None:
    """Пачка и кнопка — человек видит отказ сразу: письмо остаётся в очереди, сколько бы раз ни
    нажали. Отказ этому письму — `LetterRefusalError`, у пачки — «почта отказала», как раньше."""
    [letter] = await _letters(session, world, JANE)
    source, asked = _sendgrid(JANE)

    reports = [await send_queue(session, source, stage=Stage.SALES) for _ in range(4)]

    assert [dict(report.refused) for report in reports] == [{"почта отказала": 1}] * 4
    assert asked == [JANE] * 4
    await session.refresh(letter)
    assert letter.status is MessageStatus.QUEUED
    sales = source.transports["sales"]
    outgoing = Outgoing(
        message_id=1,
        to=JANE,
        from_email=w.SALES_BOX,
        from_name="Mira",
        reply_to=None,
        subject="s",
        body="b",
        internet_message_id="<made-up.m1@mail-sales.example.test>",
    )
    with pytest.raises(LetterRefusalError, match=r"Платформа отказала \(400\)"):
        await sales.send(outgoing)


# --- F8: добивку не собрать нынешней цепочкой ----------------------------------------------------

#: Вторая добивка после правки цепочки: голый `{{name}}` — лиду без имени её не собрать.
NAMED_FOLLOW = "[hello] fixed\nHi {{name}},\n\n" + FOLLOW


async def _edited_after_the_first_letter(
    session: AsyncSession, world: w.World
) -> tuple[_ByStage, MessageModel]:
    """Своя цепочка гипотезы с добивками из зон `rewrite`: письмо Джейн собрано (тексты добивок —
    у переписки) и ушло; затем имя Джейн стёрли, а в первую добивку цепочки вписали голый
    `{{name}}`."""
    await _chain(session, world)
    await w.lead(session, world.hypothesis_id, JANE)
    await queue.build(session, Recorder(EVERY), hypothesis_id=world.hypothesis_id, limit=5)
    [first] = await session.scalars(select(MessageModel))
    source = _ByStage(sales=Recording(), donors=Recording(), advertisers=Recording())
    await Sending(session, source, now=NOW).send(first.id)
    lead = await _lead_of(session, first)
    lead.name = None
    new = chain_text.step_template(step=2, language="en", body=NAMED_FOLLOW)
    await chain.save(session, new, hypothesis_id=world.hypothesis_id, author="тест", author_id=None)
    await session.flush()
    return source, first


async def test_a_followup_the_edited_chain_cannot_build_goes_with_the_kept_text(
    session: AsyncSession, world: w.World, caplog: pytest.LogCaptureFixture
) -> None:
    """Мутант «прежнего текста нет»: нынешней цепочкой добивку не собрать — она уходит текстом,
    сохранённым при сборке письма; журнал говорит почему. Экран очереди говорит то же до ухода."""
    caplog.set_level(logging.INFO, logger="backend.features.sales.mail")
    source, _first = await _edited_after_the_first_letter(session, world)
    [link] = await _links(session)
    assert link.followups is not None
    kept = link.followups["steps"]["2"]["body"]
    dialog = await mail.lead_dialog(session, link.lead_id)
    assert dialog is not None

    [ahead, _] = await followup_texts.ahead(session, dialog.link, dialog.lead, dialog.host)
    report = await followups.send_due(session, transport=source, limit=5, now=FIRST_DUE)

    assert (ahead.body, ahead.words.split(":")[0]) == (
        kept,
        "сохранённым прежним текстом — нынешней цепочкой не собрать",
    )
    assert (report.sent, report.stopped) == (1, 0)
    assert _seen(source)[-1].body == f"{kept}{SIGNED}"
    assert any("уходит сохранённым прежним текстом" in r.getMessage() for r in caplog.records)


async def test_a_followup_without_a_kept_text_stops_the_chain_in_words_on_the_screen_too(
    session: AsyncSession, world: w.World, caplog: pytest.LogCaptureFixture
) -> None:
    """Мутант «добивка ждёт час без конца»: шаг без зон `rewrite` текста не хранит, а нынешней
    цепочкой его лиду без имени не собрать. Экран очереди говорит, что добивка не уйдёт и
    почему; проход добивок останавливает цепочку словами: срок погашен, письмо добивки —
    «остановлено», вторая добивка не придёт."""
    caplog.set_level(logging.INFO, logger="backend.features.letters.followups")
    source, [first] = await _first_sent(session, world)
    lead = await _lead_of(session, first)
    lead.name = None
    await session.flush()
    dialog = await mail.lead_dialog(session, lead.id)
    assert dialog is not None

    [ahead, _] = await followup_texts.ahead(session, dialog.link, dialog.lead, dialog.host)
    report = await followups.send_due(session, transport=source, limit=5, now=FIRST_DUE)
    later = await followups.send_due(session, transport=source, limit=5, now=SECOND_DUE)

    reason = "в письме подстановка без значения: «NAME НЕ ЗАДАНО»"
    assert ahead.body is None
    assert ahead.words == (
        f"не уйдёт — {followup_texts.NOT_BUILT}: {reason}; проход добивок остановит цепочку"
    )
    assert (report.sent, report.stopped, report.postponed) == (0, 1, 0)
    assert (later.sent, later.stopped) == (0, 0)
    await session.refresh(first)
    assert first.next_action_at is None
    stopped = await session.scalar(
        select(MessageModel).where(
            MessageModel.thread_id == first.thread_id, MessageModel.step == 1
        )
    )
    assert stopped is not None
    assert stopped.status is MessageStatus.STOPPED
    assert f"Письмо №{stopped.id}: добивку {followup_texts.NOT_BUILT} — {reason}" in caplog.text


# --- F7: очередь пуста, а автосборка не может собрать ---------------------------------------------


@pytest.fixture
def autobuild_on(monkeypatch: pytest.MonkeyPatch, switched_on: None) -> None:
    monkeypatch.setattr(sales_cfg, "AUTOBUILD", True)


def _capped(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """Потолок модели за день набран: расход записан, потолок ниже."""
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", 41)
    usage.record(session, operation="sales_letter_rewrite", units=43)


async def test_an_empty_queue_the_cap_keeps_empty_is_one_alarm_and_one_all_clear(
    session: AsyncSession,
    world: w.World,
    autobuild_on: None,
    told: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Мутанты «тревоги нет» и «тревога на каждом проходе»: очередь пуста, лид ждёт письма, а
    потолок модели набран — автосборка не соберёт. Одна тревога при переходе, второй проход
    молчит; потолок подняли — сборка поставлена, одно «прошло»."""
    await w.lead(session, world.hypothesis_id, JANE)
    _capped(session, monkeypatch)
    await session.flush()
    jobs, watch = _AutoJobs(), autobuild_alarm.Watch()
    now = datetime.now(UTC)  # потолок считается с начала суток базы

    first = await autobuild.step(session, jobs, now=now, watch=watch)
    await autobuild.step(session, jobs, now=now, watch=watch)
    alarmed = list(told)
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", 10**9)
    built = await autobuild.step(session, jobs, now=now, watch=watch)

    assert first.stuck
    assert first.why is not None
    assert alarmed == [marked(f"{autobuild_alarm.STUCK}: {first.why}", autobuild_alarm.EMPTY)]
    assert "потолок расхода на модель за день достигнут" in alarmed[0]
    assert built.hypothesis_id == world.hypothesis_id
    assert told[1:] == [f"{autobuild_alarm.UNSTUCK} (стояла: {first.why})"]


async def test_a_streak_off_the_corridor_with_an_empty_queue_is_an_alarm(
    session: AsyncSession, world: w.World, autobuild_on: None, told: list[str]
) -> None:
    """Сборка гипотезы встала на серии писем вне коридора, очередь пуста — проход её не
    пересобирает, и это тревога словами серии."""
    await w.lead(session, world.hypothesis_id, JANE)
    jobs = _AutoJobs()
    started = await autobuild.run(session, jobs, now=NOW)
    jobs.known[build_job_id(world.hypothesis_id)].finish(
        {"prepared": 0, "stopped": corridor.streak_stop()}
    )

    found = await autobuild.step(session, jobs, now=NOW, watch=autobuild_alarm.Watch())

    assert started.hypothesis_id == world.hypothesis_id
    assert found.why == autobuild.STREAK
    assert told == [marked(f"{autobuild_alarm.STUCK}: {autobuild.STREAK}", autobuild_alarm.EMPTY)]


async def test_a_failing_build_is_one_alarm_until_a_build_ends_without_failing(
    session: AsyncSession, world: w.World, autobuild_on: None, told: list[str]
) -> None:
    """Мутанты «помнит только нынешнюю задачу» и «идущая сборка — упавшая»: пока первая сборка
    идёт, тревоги нет. Она упала (повторы кончились) — тревога; проход ставит её снова, и пока
    новая идёт, тревога держится без повтора и без «прошло». Новая собрала письма — очередь не
    пуста, «прошло»."""
    await w.lead(session, world.hypothesis_id, JANE)
    jobs, watch = _AutoJobs(), autobuild_alarm.Watch()
    await autobuild.step(session, jobs, now=NOW, watch=watch)
    await autobuild.step(session, jobs, now=NOW, watch=watch)
    quiet = list(told)
    job = jobs.known[build_job_id(world.hypothesis_id)]
    job.status = JobStatus.FAILED

    again = await autobuild.step(session, jobs, now=NOW, watch=watch)
    running = await autobuild.step(session, jobs, now=NOW, watch=watch)
    alarmed = list(told)
    await queue.build(session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=1)
    jobs.known[build_job_id(world.hypothesis_id)].finish({"prepared": 1})
    await autobuild.step(session, jobs, now=NOW, watch=watch)

    failed = autobuild_alarm.FAILED.format(name="Выдуманная гипотеза")
    assert quiet == []
    assert again.hypothesis_id == world.hypothesis_id  # поставлена снова
    assert running.why is not None
    assert running.why.startswith("идёт сборка")
    assert alarmed == [marked(f"{autobuild_alarm.STUCK}: {failed}", autobuild_alarm.EMPTY)]
    assert told[1:] == [f"{autobuild_alarm.UNSTUCK} (стояла: {failed})"]


async def test_no_alarm_when_nothing_is_wrong_with_the_autobuild(
    session: AsyncSession, world: w.World, autobuild_on: None, told: list[str]
) -> None:
    """Очередь пуста, а лидов для писем нет вовсе — это не «не может собрать»: решение
    владельца называет потолок, серию вне коридора и падение сборки."""
    found = await autobuild.step(session, _AutoJobs(), now=NOW, watch=autobuild_alarm.Watch())

    assert found.why == autobuild.NO_CANDIDATES
    assert told == []


@pytest.mark.parametrize("off", ["autosend", "letters"])
async def test_no_alarm_while_autosend_is_off_or_the_queue_has_letters(
    session: AsyncSession,
    world: w.World,
    autobuild_on: None,
    told: list[str],
    monkeypatch: pytest.MonkeyPatch,
    off: str,
) -> None:
    """Мутанты «тревога без автоотправки» и «тревога при письмах в очереди»: потолок набран, но
    автоотправка выключена (пачка — рычаг человека) или в очереди есть письма — поток идёт."""
    if off == "autosend":
        monkeypatch.setattr(sales_cfg, "AUTOSEND", False)
        await w.lead(session, world.hypothesis_id, JANE)
    else:
        world.sales_box.daily_cap = 5
        await _letters(session, world, JANE)
        await w.lead(session, world.hypothesis_id, OTHERS[0])
    _capped(session, monkeypatch)
    await session.flush()

    found = await autobuild.step(
        session, _AutoJobs(), now=datetime.now(UTC), watch=autobuild_alarm.Watch()
    )

    assert found.stuck
    assert told == []
