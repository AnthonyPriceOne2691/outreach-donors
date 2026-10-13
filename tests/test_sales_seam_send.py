"""Отправка продаж в бою — ревью стыков R4: то, что включается сейчас, держится тестом.

Включения владельца: автоотправка и автосборка, предохранитель снят строкой с пустым значением,
пять доменов продаж по ящику, разгон с первого дня. Здесь — путь письма целиком на настоящей
базе дерева, почта подставная (нулевой транспорт с памятью или боевой транспорт на подставном
HTTP), часы у теста:

- первый день пяти новых ящиков: первые письма по ящикам поровну, по одному с ящика за проход,
  пауза ящика не короче нижнего края, ступень разгона — и дальше «писать некому» словами;
- исход письма неизвестен (почта не ответила): ящик отдыхает, как после ушедшего письма, а
  проход не шлёт вслепую дальше;
- предохранитель — строкой `.env`: пустая — пишем всем, и экран и журнал говорят это словами;
  с адресами — только им; строки нет — продажи не подключены;
- отписка со страницы письма продаж снимает письма компании — первое в очереди, добивку, — и ни
  проход автоотправки, ни проход добивок им больше не пишут.

Тексты и адреса выдуманы; живых писем нет.
"""

from __future__ import annotations

import itertools
import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
from backend.api.unsubscribe import routes as unsubscribe_routes
from backend.config import outreach as outreach_cfg
from backend.config import sales as sales_cfg
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.outreach import MessageModel
from backend.features.letters import batch, followups, unsubscribe
from backend.features.letters.chain import FIRST_STEP
from backend.features.letters.sendgrid import PROVIDER_ID_HEADER, SendGridTransport
from backend.features.letters.transport import MaybeSentError, Outgoing
from backend.features.outreach import senders
from backend.features.runs.prune import PruneRefusedError, plan_prune
from backend.features.sales import auto_lines, autosend, autosend_jobs, queue
from backend.features.sales.autosend import Pass, Stop
from backend.features.sales.autosend_jobs import Watch
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.test_mail_accounts import _ByStage
from tests.test_mail_identity import Recording
from tests.test_sales_send import _seen, _transports

NOW = w.NOW
MINUTE = timedelta(minutes=1)
JANE = "jane@acme.example.test"
OLGA = "olga@acme.example.test"
IVAN = "ivan@gamma.example.test"
#: Ещё четыре ящика продаж — у каждого свой домен, как у пяти доменов продаж в бою.
MORE_BOXES = tuple(f"box{number}@mail-sales-{number}.example.test" for number in range(2, 6))
UNSURE = "почта не ответила — выдуманные слова теста"


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


async def _queued(session: AsyncSession, world: w.World, *emails: str) -> list[MessageModel]:
    """Лиды гипотезы и их первые письма — сборкой продаж, все разом, по порядку адресов."""
    for email in emails:
        await w.lead(session, world.hypothesis_id, email, name=email.split("@")[0].title())
    await queue.build(
        session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=len(emails)
    )
    rows = await session.scalars(
        select(MessageModel).where(MessageModel.step == FIRST_STEP).order_by(MessageModel.id)
    )
    return list(rows)


async def _five_new_boxes(session: AsyncSession, world: w.World, started: datetime) -> list[str]:
    """Пять ящиков продаж на пяти доменах, разгон — с `started` (`sender-add` ставит минуту
    заведения)."""
    boxes = [world.sales_box, *[await w.mailbox(session, email) for email in MORE_BOXES]]
    for box in boxes:
        box.warmup_started_at = started
    await session.flush()
    return [box.email for box in boxes]


# --- пять новых ящиков: разгон с первого дня ---------------------------------------------------


@pytest.mark.parametrize("day", [1, 3])
async def test_five_new_boxes_send_evenly_one_per_pass_with_the_pause_up_to_the_warmup(
    session: AsyncSession, world: w.World, day: int
) -> None:
    """Пять доменов по ящику, разгон с первого дня: за день каждый ящик отдаёт ровно ступень
    разгона, первые письма — по ящикам поровну, за проход — не больше письма с ящика, между
    письмами ящика — пауза не короче нижнего края; ступень вышла — «писать некому» словами. Третий
    день разгона — вторая ступень."""
    started = NOW - timedelta(days=day - 1)
    boxes = await _five_new_boxes(session, world, started)
    allowance = senders.warmup_state(world.sales_box, now=NOW).allowance
    assert allowance == senders.WARMUP_STEPS[(day - 1) // senders.WARMUP_STEP_DAYS]
    emails = [f"lead{number}@company{number}.example.test" for number in range(allowance * 5 + 3)]
    await _queued(session, world, *emails)
    source = _transports()
    went: dict[str, list[datetime]] = defaultdict(list)
    last: Pass = Pass()

    # Проход раз в две минуты, пять часов — дольше, чем нужно ступени при самых длинных паузах.
    for step in range(150):
        now = NOW + MINUTE * 2 * step
        last = await autosend.run(session, source, now=now)
        assert len(last.sent) == len(set(last.sent.values())), "два письма с ящика за проход"
        for box in last.sent.values():
            went[box].append(now)

    assert sorted(went) == sorted(boxes)
    assert {box: len(times) for box, times in went.items()} == dict.fromkeys(boxes, allowance)
    lower = autosend.gap() * (1 - autosend.SPREAD)
    assert all(b - a >= lower for times in went.values() for a, b in itertools.pairwise(times))
    assert last.stop is Stop.CAP
    assert last.stopped is not None
    assert last.stopped.startswith("Сегодня писать некому")
    assert len({outgoing.to for outgoing in _seen(source)}) == allowance * 5
    # В каждом письме — отписка в один клик меткой письма и физический адрес «Отправителя».
    for outgoing in _seen(source):
        assert outgoing.unsubscribe_url.startswith("https://unsub.example.test/u/u")
        assert f"m{outgoing.message_id}." in outgoing.unsubscribe_url
        assert outgoing.body.endswith(f"\n\n{w.ADDRESS}")


@pytest.mark.xfail(
    strict=True,
    reason=(
        "общий код outreach/senders.warmup_state: разгон считается от заведения ящика "
        "(warmup_started_at, `sender-add`), а не от его первого письма — ящик, заведённый "
        "заранее и ещё не писавший, в первый день боя пишет ступень по возрасту записи, а не "
        "первую; правка — PR «общее» или решение владельца (ревью стыков R4)"
    ),
)
async def test_a_box_set_up_a_week_ahead_starts_its_first_day_on_the_first_step(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Домены продаж заводят до боя: DNS, подпись у платформы, ящик — `sender-add`. Ящик не
    отправил ни одного письма, а разгон с минуты заведения уже прошёл ступени: в первый день
    боя он пишет полный кап, а не первую ступень (ТЗ: «с постепенным разгоном новых ящиков»)."""
    world.sales_box.warmup_started_at = NOW - timedelta(days=7)
    monkeypatch.setattr(sales_cfg, "AUTOSEND_GAP_MIN", 1)
    emails = [f"lead{number}@company{number}.example.test" for number in range(25)]
    await _queued(session, world, *emails)
    source = _transports()

    for step in range(40):
        await autosend.run(session, source, now=NOW + MINUTE * 2 * step)

    assert len(_seen(source)) == senders.WARMUP_FIRST_DAY


# --- исход неизвестен: ни паузы мимо, ни писем вслепую ----------------------------------------


class _Unsure(Recording):
    """Почта не ответила, когда запрос мог дойти (`sendgrid.py`): исход письма неизвестен."""

    async def send(self, outgoing: Outgoing) -> str:
        self.seen.append(outgoing)
        raise MaybeSentError(UNSURE)


def _unsure() -> _ByStage:
    return _ByStage(sales=_Unsure(), donors=Recording(), advertisers=Recording())


async def test_a_letter_whose_outcome_is_unknown_rests_its_box_like_a_sent_one(
    session: AsyncSession, world: w.World
) -> None:
    """Письмо ушло к почте, а ответа нет: оно «отправляется», `sent_at` пуст. Пауза ящика —
    от последнего первого письма, отданного почте любым путём: следующий проход минутой позже
    не берёт с того же ящика второе письмо. Иначе до `STUCK_AFTER` ящик слал бы раз в минуту, и
    кап этого не видел бы — «отправляется» в дневной счёт не входит."""
    letters = await _queued(session, world, JANE, IVAN)

    unsure = await autosend.run(session, _unsure(), now=NOW)
    # Захват пишет время базы, а часы прохода — у теста: передача началась минутой прохода.
    letters[0].updated_at = NOW
    await session.flush()
    source = _transports()
    later = await autosend.run(session, source, now=NOW + MINUTE)

    await session.refresh(letters[0])
    assert letters[0].status is MessageStatus.SENDING
    assert (unsure.stop, unsure.unsure, unsure.sent) == (Stop.UNKNOWN, letters[0].id, {})
    assert (later.sent, later.stop) == ({}, Stop.RESTING)
    assert _seen(source) == []


async def test_after_its_own_unknown_outcome_the_pass_sends_nothing_blind(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch, told: list[str]
) -> None:
    """ТЗ среза: «пока у продаж есть письмо с неизвестным исходом, автоотправка стоит». Проход
    сам получил «почта не ответила» — дальше он не шлёт ни с другого ящика, ни через минуту:
    стоит словами, пока письмо «отправляется» (с `STUCK_AFTER` его держит блок «Исход
    неизвестен»). Слать вслепую нельзя: ответ платформы был бы тем же, и неизвестных стало бы
    пять. Событие платформы решило исход — проход идёт с другого ящика: этот отдыхает."""
    monkeypatch.setattr(sales_cfg, "ENABLED", True)
    monkeypatch.setattr(sales_cfg, "AUTOSEND", True)
    letters = await _queued(session, world, JANE, IVAN)
    await w.mailbox(session, MORE_BOXES[0])
    mail: dict[str, _ByStage] = {"now": _unsure()}

    async def on_test_base(now: datetime) -> Pass:
        return await autosend.run(session, mail["now"], now=now)

    monkeypatch.setattr(autosend_jobs, "_pass", on_test_base)
    watch = Watch()

    await autosend_jobs.tick(now=NOW, watch=watch)
    letters[0].updated_at = NOW
    await session.flush()
    mail["now"] = calm = _transports()
    for minutes in (1, 2, 4):
        await autosend_jobs.tick(now=NOW + MINUTE * minutes, watch=watch)
    held = list(_seen(calm))
    said = watch.said
    letters[0].status, letters[0].sent_at = MessageStatus.SENT, NOW
    await session.flush()
    await autosend_jobs.tick(now=NOW + MINUTE * 5, watch=watch)

    assert held == []
    assert said is Stop.UNKNOWN
    [went] = _seen(calm)
    assert (went.to, went.from_email) == (IVAN, MORE_BOXES[0])


async def test_a_sales_followup_whose_outcome_is_unknown_is_never_sent_again(
    session: AsyncSession, world: w.World
) -> None:
    """Почта не ответила на добивку продаж: она «отправляется», срок погашен захватом до вызова
    почты — проход через час её не повторит (второе письмо тому же человеку), и следующая
    добивка не назначена, пока исход не решён."""
    await _queued(session, world, JANE)
    await autosend.run(session, _transports(), now=NOW)
    due = NOW + timedelta(days=3)

    unsure = await followups.send_due(session, transport=_unsure(), limit=5, now=due)
    calm = _transports()
    again = await followups.send_due(session, transport=calm, limit=5, now=due + timedelta(hours=1))

    assert (unsure.unknown, unsure.sent, unsure.postponed) == (1, 0, 0)
    assert (again.sent, again.postponed, again.unknown) == (0, 0, 0)
    assert _seen(calm) == []
    step = await session.scalar(select(MessageModel).where(MessageModel.step == FIRST_STEP + 1))
    assert step is not None
    assert (step.status, step.next_action_at) == (MessageStatus.SENDING, None)


# --- предохранитель — строкой .env ------------------------------------------------------------


def _accepted(asked: list[str]) -> httpx.AsyncClient:
    """Платформа принимает письмо и называет его номер — подставной HTTP, сети нет."""

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(json.loads(request.content)["personalizations"][0]["to"][0]["email"])
        return httpx.Response(202, headers={PROVIDER_ID_HEADER: f"made-up-{len(asked)}"})

    return httpx.AsyncClient(transport=httpx.MockTransport(answer))


def _env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, line: str | None) -> None:
    """Учётка продаж — строками `.env`, как на сервере; окружение процесса их не перекрывает."""
    for name in ("OUTREACH_SALES_SENDGRID_API_KEY", "OUTREACH_SALES_ALLOWED_RECIPIENTS"):
        monkeypatch.delenv(name, raising=False)
    lines = [f"OUTREACH_SALES_SENDGRID_API_KEY={w.OWN_KEY}", *([line] if line is not None else [])]
    (tmp_path / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)


async def test_an_empty_allowlist_line_writes_to_everybody_and_says_so(
    session: AsyncSession,
    world: w.World,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`OUTREACH_SALES_ALLOWED_RECIPIENTS=` — пустое значение, а не удалённая строка: у продаж
    своя учётка, и предохранитель у неё свой. Пусто — пишем всем: проход отдаёт письмо чужому
    адресу боевым транспортом, журнал на каждое письмо говорит «предохранитель снят», экран —
    что письма уходят всем лидам очереди."""
    [letter] = await _queued(session, world, JANE)
    _env_file(tmp_path, monkeypatch, "OUTREACH_SALES_ALLOWED_RECIPIENTS=")
    monkeypatch.setattr(sales_cfg, "AUTOSEND", True)
    caplog.set_level(logging.WARNING, logger="backend.features.letters.sendgrid")
    asked: list[str] = []
    account = outreach_cfg.mail_account(Stage.SALES.value)
    real = SendGridTransport(account=account, http=_accepted(asked))
    source = _ByStage(sales=real, donors=Recording(), advertisers=Recording())

    guarded = autosend.guard()
    found = await autosend.run(session, source, now=NOW)
    line = await auto_lines.autosend_line(session, NOW)

    assert (account.allowed_recipients, account.refusal, guarded) == ((), None, None)
    assert found.sent == {letter.id: w.SALES_BOX}
    assert asked == [JANE]
    said = [r.getMessage() for r in caplog.records if r.name == "backend.features.letters.sendgrid"]
    assert said == [
        f"письма: предохранитель снят — письмо №{letter.id} уходит на боевой адрес {JANE}"
    ]
    assert line.enabled is True
    assert line.line.startswith(
        "Автоотправка включена, список разрешённых адресатов почты продаж пуст — письма уходят "
        "всем лидам очереди: "
    )


async def test_an_allowlist_with_addresses_keeps_letters_to_them_only(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Строка со своими адресами — письма уходят только им: автоотправка не включается и
    говорит почему, а кнопка «Отправить очередь» получает отказ предохранителя по письму чужому
    адресу — до платформы письмо не доходит."""
    [letter] = await _queued(session, world, JANE)
    _env_file(tmp_path, monkeypatch, "OUTREACH_SALES_ALLOWED_RECIPIENTS=me@ours.example.test")
    monkeypatch.setattr(sales_cfg, "AUTOSEND", True)
    asked: list[str] = []
    account = outreach_cfg.mail_account(Stage.SALES.value)
    real = SendGridTransport(account=account, http=_accepted(asked))
    source = _ByStage(sales=real, donors=Recording(), advertisers=Recording())

    line = await auto_lines.autosend_line(session, NOW)
    report = await batch.send_queue(session, source, stage=Stage.SALES)

    assert account.allowed_recipients == ("me@ours.example.test",)
    assert line.enabled is False
    assert line.line == (
        "Автоотправка не включается: список разрешённых адресатов почты продаж не пуст "
        "(OUTREACH_SALES_ALLOWED_RECIPIENTS) — письма уходят только на свои адреса, кнопкой "
        "«Отправить очередь» или по одному."
    )
    assert (report.sent, dict(report.refused)) == (0, {"предохранитель: адрес не из своих": 1})
    assert asked == []
    await session.refresh(letter)
    assert letter.status is MessageStatus.QUEUED


async def test_a_removed_allowlist_line_disconnects_sales_and_names_the_line(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Строку удалили вместо пустого значения — у своей учётки нет своего предохранителя:
    продажи не подключены, проход стоит словами, имя строки — в отказе транспорта. Экран не
    говорит «список пуст — письма уходят всем лидам» и «в окне 0 писем» (замер стенда 13.10:
    говорил, рядом со «Стоит: … не задан свой список»): автоотправка включена, а не шлёт."""
    await _queued(session, world, JANE)
    _env_file(tmp_path, monkeypatch, None)
    monkeypatch.setattr(sales_cfg, "AUTOSEND", True)

    account = outreach_cfg.mail_account(Stage.SALES.value)
    found = await autosend.run(session, _transports(), now=NOW)
    line = await auto_lines.autosend_line(session, NOW)

    assert account.refusal is not None
    assert account.refusal.startswith("OUTREACH_SALES_ALLOWED_RECIPIENTS не задан")
    assert found.stop is Stop.NOT_CONNECTED
    assert found.stopped == (
        "продажи к почте не подключены — у учётки почты продаж не задан свой список "
        "разрешённых адресов — задаёт администратор"
    )
    assert (line.enabled, line.line) == (True, f"{auto_lines.SEND_STOPPED}: {found.stopped}.")


async def test_the_sales_trial_is_cleaned_only_while_the_allowlist_names_its_inbox(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Порядок выкатки. Проба продаж — лид со своим ящиком — уходит с `prune --test-traces`,
    пока свой ящик назван в списке разрешённых продаж: по нему чистка узнаёт свои ящики
    (`outreach/own_inboxes.py`). Список сняли пустой строкой — узнать не по чему: без общего
    списка чистка отказывает словами, проба остаётся в базе, в воронке и в очереди — и
    автоотправка шлёт её письмо на свой ящик, тратя кап первого дня."""
    own = "checker@ours.example.test"
    monkeypatch.setattr(outreach_cfg, "ALLOWED_RECIPIENTS", ())
    monkeypatch.setenv("OUTREACH_SALES_ALLOWED_RECIPIENTS", own)
    [letter] = await _queued(session, world, own)

    before = await plan_prune(session, run_ids=[], test_traces=True)
    monkeypatch.setenv("OUTREACH_SALES_ALLOWED_RECIPIENTS", "")
    with pytest.raises(PruneRefusedError, match="Тестовых ящиков нет: предохранитель"):
        await plan_prune(session, run_ids=[], test_traces=True)
    went = await autosend.run(session, _transports(), now=NOW)

    assert before.sales_trials is not None
    assert [lead.email for lead in before.sales_trials.leads] == [own]
    assert went.sent == {letter.id: w.SALES_BOX}


# --- отписка со страницы письма продаж ---------------------------------------------------------


async def test_unsubscribing_from_a_sales_letter_stops_every_letter_to_that_company(
    session: AsyncSession, world: w.World, client: AsyncClient
) -> None:
    """Лиду ушло первое письмо, коллеге той же компании письмо ждёт в очереди. Нажатие на
    странице отписки письма продаж закрывает сайт: письмо коллеги — «снято», срок добивки
    лида погашен, и ни автоотправка, ни проход добивок им больше не пишут (ТЗ: «отписавшимся
    больше не пишем ни с одного домена»)."""
    first, colleague = await _queued(session, world, JANE, OLGA)
    source = _transports()
    went = await autosend.run(session, source, now=NOW)
    assert went.sent == {first.id: w.SALES_BOX}
    await session.commit()
    unsubscribe_routes._throttle.clear("127.0.0.1")
    label = unsubscribe.label_for(first.domain_id, letter_id=first.id)

    pressed = await client.post(f"/api/unsubscribe/{label}")
    later = await autosend.run(session, source, now=NOW + timedelta(hours=2))
    due = await followups.send_due(session, transport=source, limit=5, now=NOW + timedelta(days=4))

    assert pressed.status_code == 200
    await session.refresh(first)
    await session.refresh(colleague)
    assert colleague.status is MessageStatus.STOPPED
    assert first.next_action_at is None
    assert (later.sent, later.stop) == ({}, Stop.EMPTY)
    assert (due.sent, due.postponed) == (0, 0)
    assert [outgoing.to for outgoing in _seen(source)] == [JANE]


# --- все ящики на паузе ------------------------------------------------------------------------


async def _all_paused(session: AsyncSession, world: w.World) -> Pass:
    """Единственный ящик продаж встал на паузу сам (отказы) — проход над письмом в окне."""
    await _queued(session, world, JANE)
    senders.disable(world.sales_box, "отказов 3 в окне 50 писем — выдуманная пауза", now=NOW)
    await session.flush()
    return await autosend.run(session, _transports(), now=NOW)


async def test_all_boxes_paused_stop_the_pass_in_the_words_of_the_box_choice(
    session: AsyncSession, world: w.World
) -> None:
    """Все ящики продаж на паузе — проход стоит словами выбора ящика, а не «кап набран»: кап
    тут ни при чём, и ждать завтрашнего дня бесполезно. Тревоги — у сторожа почты («все ящики
    направления на паузе») и у автопаузы."""
    found = await _all_paused(session, world)

    assert found.stop is Stop.CAP
    assert found.stopped is not None
    assert found.stopped.startswith("Сегодня писать некому")
    assert "кап набран" not in found.stopped


@pytest.mark.xfail(
    strict=True,
    reason=(
        "общий код letters/mailbox._free_box: у этапа нет ни одного пишущего ящика (все на "
        "паузе или выключены), а отказ обещает «завтра лимит откроется заново» — завтра ящик на "
        "паузе тоже не напишет; правка — PR «общее»: при пустом списке включённых ящиков этапа — "
        "«ящиков, которые пишут, нет: все выключены или на паузе — экран «Домены рассылки»» "
        "(ревью стыков R4)"
    ),
)
async def test_all_boxes_paused_is_not_promised_to_reopen_tomorrow(
    session: AsyncSession, world: w.World
) -> None:
    found = await _all_paused(session, world)

    assert found.stopped is not None
    assert "завтра лимит откроется" not in found.stopped
    assert "на паузе" in found.stopped
