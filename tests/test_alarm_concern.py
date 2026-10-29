"""Пометка тревоги outreach — стоит ли она внимания (слово Anthony 29.10.2026).

«Тревога такая-то, повода для беспокойства нет или есть»: поводом стала «Платформа молчит о
доставке», где вебхук работал, а одно письмо ждало сервер получателя. Здесь держится:

- **защита:** тревога outreach без пометки краснит здесь, а не уходит без строки, — и
  `Alarm(...)` без `concern=`, и `send_alert(...)` мимо ленты без `marked(...)`; сам поиск
  проверен на тревоге без пометки. Код продаж не ищется: их тревогам пометка не обязательна;
- **лента:** «тревога» кончается строкой пометки, «прошло» — без неё; тревога без пометки (у
  продаж) идёт, как шла; повод появился у той же тревоги — она приходит ещё раз, ушёл — нет;
  перезапуск повтора не даёт; длинная тревога пометку не теряет;
- **каждое правило:** есть ли повод и почему; повод от числа — по числу (кап Ahrefs по
  уровням, «отправить некому» за выдержкой домена);
- **слова:** коротко, без точки в конце частей и без технического мусора — имён настроек,
  файлов и адресов (`_plain`, его зовёт каждая проверка правила);
- **экран:** `GET /api/watchdog` отдаёт пометку каждой тревоги.
"""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import backend.config
import pytest
from backend.api.watchdog import routes
from backend.config import crawl as crawl_cfg
from backend.features.core import stages, usage
from backend.features.core.domain import (
    CrawlOutcome,
    MessageStatus,
    RunStatus,
    SenderStatus,
    Stage,
    StopReason,
)
from backend.features.core.models.crawl import CrawlRunModel
from backend.features.core.models.outreach import SenderModel, SendingDomainModel
from backend.features.core.models.run import RunModel
from backend.features.core.stages import MailPolicy
from backend.features.crawl import lifecycle
from backend.features.crawl.spend import PROXY_OPERATION
from backend.features.letters import autosend_alarm
from backend.features.ops import alarm_feed, funds_watch, key_watch, mail_watch, silence
from backend.features.ops.alarm_feed import Feed
from backend.features.ops.alarms import Alarm, Concern, cause, marked, no_cause
from backend.features.outreach import autopause, senders
from backend.features.outreach.repository import EVERY_STAGE
from backend.features.runs import stopped
from backend.features.runs.repository import RunRepository
from backend.shared.alerts import MAX_CHARS, SOURCE
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_ahrefs_cap import known_key
from tests.test_mail_watch import _letter as _mail_letter
from tests.test_mail_watch_seams import FakeRedis
from tests.test_sales_stage_bridge import FakeSalesMail
from tests.test_sales_stage_mail import sender
from tests.test_silence_stages import MakeUser, SignIn
from tests.test_silence_watchdog import NOW, T, _letter

#: Код сервиса — в нём ищутся тревоги.
SERVICE = Path(__file__).resolve().parent.parent / "backend"

#: Код продаж: их тревогам пометка не обязательна (решение 29.10.2026), и правят его продажи.
SALES = ("features/sales/", "api/sales/")

#: Где текст тревоги собирается сам: лента (`alarms.told`, «прошло» — без пометки) и отправка.
OWN = ("features/ops/alarm_feed.py", "shared/alerts.py")

#: Пометка — одна строка в конце сообщения: длиннее — уже не пометка, а второе описание.
MAX_WORDS = 200

#: Технический мусор в словах человеку: имена настроек, обратные кавычки, файлы и адреса.
TECHNICAL = re.compile(r"[A-Z][A-Z0-9]*_[A-Z0-9_]+|`|\.env\b|https?://")

#: Ящик продаж — сторож почты этапа идёт только у политики, которая его просит (у продаж).
BOX = "hello@mail-sales.example.test"


def _plain(concern: Concern) -> None:
    """Пометка коротка, без точки в конце частей и без технического мусора; у повода есть «что
    сделать», у его отсутствия — нет."""
    words = concern.words
    assert len(words) <= MAX_WORDS, words
    assert TECHNICAL.search(words) is None, words
    assert not concern.why.endswith("."), words
    assert not concern.todo.endswith("."), words
    assert bool(concern.todo) is concern.cause, words


def _concern(found: Sequence[Alarm], code: str) -> Concern:
    """Пометка тревоги `code` среди найденных — проверенная `_plain`."""
    [alarm] = [one for one in found if one.code == code]
    assert alarm.concern is not None, f"{code}: тревога без пометки"
    _plain(alarm.concern)
    return alarm.concern


# --- защита: тревога outreach без пометки краснит здесь -------------------------------------


def _callee(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    return func.attr if isinstance(func, ast.Attribute) else None


def _calls(tree: ast.AST, name: str) -> Iterator[ast.Call]:
    return (
        node for node in ast.walk(tree) if isinstance(node, ast.Call) and _callee(node.func) == name
    )


def unmarked_alarms(name: str, tree: ast.AST) -> list[str]:
    """Тревоги без пометки: `Alarm(...)` без `concern=` или с `concern=None`."""
    found = []
    for call in _calls(tree, "Alarm"):
        given = {word.arg: word.value for word in call.keywords}.get("concern")
        if given is None or (isinstance(given, ast.Constant) and given.value is None):
            found.append(f"{name}:{call.lineno}")
    return found


def _starts(node: ast.expr, prefix: str) -> bool:
    """Строка или f-строка, которая начинается с `prefix`."""
    if isinstance(node, ast.JoinedStr):
        return bool(node.values) and _starts(node.values[0], prefix)
    return isinstance(node, ast.Constant) and str(node.value).startswith(prefix)


def unmarked_alerts(name: str, tree: ast.AST) -> list[str]:
    """Тревоги мимо ленты без пометки: текст `send_alert(...)` — не `marked(...)` и не «прошло»
    (оно пометки не несёт: само говорит, что причины больше нет)."""
    found = []
    for call in _calls(tree, "send_alert"):
        said = call.args[0] if call.args else None
        marked_text = isinstance(said, ast.Call) and _callee(said.func) == "marked"
        if not marked_text and not (said is not None and _starts(said, "прошло: ")):
            found.append(f"{name}:{call.lineno}")
    return found


def _service() -> Iterator[tuple[str, ast.AST]]:
    """Модули сервиса вне продаж и вне мест, где текст тревоги собирается сам."""
    for path in sorted(SERVICE.rglob("*.py")):
        name = path.relative_to(SERVICE).as_posix()
        if not name.startswith(SALES) and name not in OWN:
            yield name, ast.parse(path.read_text(encoding="utf-8"))


def test_every_outreach_alarm_names_its_concern() -> None:
    modules = list(_service())
    alarms = [call for _, tree in modules for call in _calls(tree, "Alarm")]

    assert len(alarms) >= 18, f"поиск не видит тревог сторожа: {len(alarms)}"
    assert [place for name, tree in modules for place in unmarked_alarms(name, tree)] == []


def test_every_outreach_alert_past_the_feed_ends_with_its_concern() -> None:
    modules = list(_service())
    sends = [call for _, tree in modules for call in _calls(tree, "send_alert")]

    assert len(sends) >= 5, f"поиск не видит тревог мимо ленты: {len(sends)}"
    assert [place for name, tree in modules for place in unmarked_alerts(name, tree)] == []


def test_the_guard_sees_an_alarm_and_an_alert_without_a_mark() -> None:
    """Поиск не пустой: тревога без пометки, с `concern=None` и голый текст тревоги — находки;
    пометка и «прошло» — нет."""
    tree = ast.parse(
        'Alarm(code="a", title="A", detail="a")\n'
        'Alarm(code="b", title="B", detail="b", concern=None)\n'
        'Alarm(code="c", title="C", detail="c", concern=MARK)\n'
        'send_alert("тревога: a")\n'
        "send_alert(words)\n"
        'send_alert(marked("тревога: c", MARK))\n'
        'send_alert(f"прошло: {title}")\n'
    )

    assert unmarked_alarms("x.py", tree) == ["x.py:1", "x.py:2"]
    assert unmarked_alerts("x.py", tree) == ["x.py:4", "x.py:5"]


# --- слова пометки -------------------------------------------------------------------------


def test_the_mark_says_whether_there_is_a_cause_why_and_what_to_do() -> None:
    assert cause("ни одно письмо не получает статусов", "проверить вебхук").words == (
        "Есть повод для беспокойства — ни одно письмо не получает статусов: проверить вебхук."
    )
    assert no_cause("исход придёт сам").words == "Повода для беспокойства нет — исход придёт сам."


def test_a_long_alarm_keeps_its_mark() -> None:
    """Отправка режет сообщение с конца по потолку Telegram — режется текст, а не пометка."""
    mark = cause("работа стоит", "сделать что-то")

    said = marked("тревога: " + "я" * MAX_CHARS, mark)

    assert said.endswith(f"…\n{mark.words}")
    assert len(f"{SOURCE}: {said}") == MAX_CHARS
    assert marked("тревога: коротко", mark) == f"тревога: коротко\n{mark.words}"
    assert marked("тревога: продаж", None) == "тревога: продаж"


@pytest.mark.parametrize(
    "mark",
    [autosend_alarm.STALLED, autopause.PAUSED, stopped.STOPPED, lifecycle.STOPPED],
    ids=["очередь стоит", "ящик на паузе сам", "прогон остановлен", "обход остановлен"],
)
def test_alarms_past_the_feed_have_a_cause(mark: Concern) -> None:
    """Очередь стоит не по делу, ящик встал на паузу сам, прогон или обход остановлен — само
    не пройдёт: повод есть всегда."""
    _plain(mark)
    assert mark.cause


# --- лента: «тревога» с пометкой, «прошло» без неё -----------------------------------------

PENDING = Alarm(
    code="delivery-pending:donors",
    title="Письма без исхода доставки — направление доноров",
    detail="Письмо №7 ушло больше 6 часов назад",
    concern=no_cause("сервер получателя тянет"),
)
#: Та же тревога, когда повод появился.
OVERDUE = Alarm(
    code=PENDING.code,
    title=PENDING.title,
    detail=PENDING.detail,
    concern=cause("исхода нет и после повторов", "найти письмо в журнале платформы"),
)


@pytest.fixture
def telegram(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Что лента сказала в Telegram — по порядку; Telegram принимает всё."""
    said: list[str] = []

    async def send(text: str) -> bool:
        said.append(text)
        return True

    monkeypatch.setattr(alarm_feed, "send_alert", send)
    return said


def _alarm_text(alarm: Alarm) -> str:
    assert alarm.concern is not None
    return f"тревога: {alarm.title}. {alarm.detail}\n{alarm.concern.words}"


async def test_the_alarm_ends_with_its_mark_and_the_all_clear_has_none(
    telegram: list[str],
) -> None:
    feed = Feed("concern-mark")

    await feed.tell([PENDING])
    for _ in range(alarm_feed.QUIET_PASSES):
        await feed.tell([])

    assert telegram == [_alarm_text(PENDING), f"прошло: {PENDING.title}"]


async def test_an_alarm_without_a_mark_goes_as_before(telegram: list[str]) -> None:
    """Тревоге продаж пометка не обязательна: без неё — ни строки, ни «повод появился»."""
    bot = Alarm(code="sales-bot-token", title="Бот продаж без токена", detail="ждут 2")
    feed = Feed("concern-sales")

    for _ in range(2):
        await feed.tell([bot])

    assert telegram == ["тревога: Бот продаж без токена. ждут 2"]


async def test_a_cause_that_appears_is_told_again_and_one_that_goes_is_not(
    telegram: list[str],
) -> None:
    """Повод зависит от числа и меняется под тем же кодом: появился — тревога ещё раз, с ним;
    держится — молчание; ушёл — молчание до «прошло», и новое «появился» тоже молчит:
    мигание не становится потоком сообщений."""
    feed = Feed("concern-grows")

    for alarm in (PENDING, PENDING, OVERDUE, OVERDUE, PENDING, OVERDUE):
        await feed.tell([alarm])

    assert telegram == [_alarm_text(PENDING), _alarm_text(OVERDUE)]


async def test_after_a_restart_the_first_pass_only_remembers_the_cause(
    telegram: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сказанную прежним процессом тревогу первый проход нового не повторяет — каким был её
    повод, он не знает; застал её без повода — скажет, когда повод появится."""
    redis = FakeRedis()
    monkeypatch.setattr(alarm_feed, "connection", lambda: redis)
    await Feed("concern-restart").tell([PENDING])

    worried = Feed("concern-restart")
    await worried.tell([OVERDUE])
    calm = Feed("concern-restart")
    await calm.tell([PENDING])
    await calm.tell([OVERDUE])

    assert telegram == [_alarm_text(PENDING), _alarm_text(OVERDUE)]


# --- доставка, ответы и добивки --------------------------------------------------------------


async def test_a_letter_waiting_for_the_recipient_server_is_no_cause(
    session: AsyncSession,
) -> None:
    await _letter(session, status=MessageStatus.SENT, sent_at=NOW - timedelta(hours=8))
    later = await _letter(
        session, status=MessageStatus.DELIVERED, sent_at=NOW - timedelta(hours=7), number=1
    )
    later.delivered_at = NOW - timedelta(hours=7)
    await session.flush()

    mark = _concern(
        await silence.alarms(session, stages=EVERY_STAGE, now=NOW), "delivery-pending:donors"
    )

    assert not mark.cause
    assert "исход придёт сам" in mark.words


async def test_no_status_for_any_letter_is_a_cause(session: AsyncSession) -> None:
    await _letter(session, status=MessageStatus.SENT, sent_at=NOW - timedelta(hours=8))

    mark = _concern(
        await silence.alarms(session, stages=EVERY_STAGE, now=NOW), "delivery-silence:donors"
    )

    assert mark.cause
    assert mark.todo == "проверить адрес вебхука и подпись в кабинете платформы"


async def test_no_replies_on_a_delivered_batch_is_a_cause(session: AsyncSession) -> None:
    for number in range(20):
        await _letter(
            session, status=MessageStatus.DELIVERED, number=number, sent_at=NOW - timedelta(days=1)
        )

    mark = _concern(
        await silence.alarms(session, stages=EVERY_STAGE, now=NOW), "reply-silence:donors"
    )

    assert mark.cause
    assert "Inbound Parse" in mark.todo


async def test_followups_standing_still_are_a_cause(session: AsyncSession) -> None:
    await _letter(
        session,
        status=MessageStatus.SENT,
        sent_at=NOW - timedelta(days=8),
        next_action_at=NOW - timedelta(hours=3),
    )

    mark = _concern(
        await silence.alarms(session, stages=EVERY_STAGE, now=NOW), "followups-stuck:donors"
    )

    assert mark.cause
    assert "процесс добивок" in mark.todo


# --- прогоны, ключ Ahrefs, счёт выдачи, обход ----------------------------------------------


async def test_a_silent_run_is_a_cause(session: AsyncSession) -> None:
    settings = await RunRepository(session).create_settings(
        T,
        geo_top_n=5,
        geo_min_share=0.2,
        metrics_ttl_days=90,
        price_ttl_days=150,
        units_cap=100_000,
    )
    run = RunModel(
        stage=Stage.DONORS,
        settings_id=settings.id,
        status=RunStatus.RUNNING,
        keywords=["a"],
        country="us",
    )
    session.add(run)
    await session.flush()
    run.updated_at = NOW - timedelta(hours=2)
    await session.flush()

    mark = _concern(await silence.alarms(session, stages=EVERY_STAGE, now=NOW), "runs-stuck")

    assert mark.cause


@pytest.mark.parametrize(
    ("spent", "code", "caused"),
    [
        (80_000, key_watch.WARN_CODE, False),
        (95_000, key_watch.ALARM_CODE, True),
        (100_000, key_watch.REACHED_CODE, True),
    ],
    ids=["80 % — предупреждение заранее", "95 % — прогон может не уложиться", "кап выбран"],
)
async def test_the_ahrefs_cap_is_a_cause_from_ninety_five_percent(
    session: AsyncSession, spent: int, code: str, caused: bool
) -> None:
    """Уровень — в коде тревоги, и повод говорится вместе с ним: переход 80 → 95 — новое
    сообщение с поводом (`key_watch`)."""
    await known_key(session, key_limit=100_000)
    usage.record(session, operation="batch_metrics", units=spent)
    await session.flush()

    mark = _concern(await silence.alarms(session, stages=EVERY_STAGE), code)

    assert mark.cause is caused
    assert ("на экране «Расход»" in mark.todo) is caused


async def test_a_low_serp_balance_is_a_cause(session: AsyncSession) -> None:
    funds_watch.remember(0.5)

    mark = _concern(await silence.alarms(session, stages=EVERY_STAGE), funds_watch.CODE)

    assert mark.cause
    assert mark.todo == "пополнить счёт DataForSEO"


async def test_a_blocked_crawl_is_a_cause(session: AsyncSession) -> None:
    session.add(
        CrawlRunModel(
            host="donor.example.test",
            outcome=CrawlOutcome.BLOCKED,
            stop_reason=StopReason.UNHEALTHY,
            pages_opened=0,
            articles=0,
        )
    )
    await session.flush()

    mark = _concern(await silence.alarms(session, stages=EVERY_STAGE), "crawl-blocked")

    assert mark.cause


async def test_the_crawl_ceiling_reached_is_no_cause(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Потолок Этапа 2 держит деньги, обход идёт бесплатными уровнями: устройство, а не поломка."""
    monkeypatch.setattr(crawl_cfg, "MONTH_CAP_USD", 5.0)
    usage.record(session, operation=PROXY_OPERATION, units=10, amount_usd=Decimal("5.00"))
    await session.flush()

    mark = _concern(await silence.alarms(session, stages=EVERY_STAGE), "crawl-cap-reached")

    assert not mark.cause
    assert "как задуман" in mark.why


async def test_broken_crawl_settings_are_a_cause(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Опечатка в настройках обхода: конфиг грузится проходом — и не грузится."""
    monkeypatch.delitem(sys.modules, "backend.config.crawl")
    monkeypatch.delattr(backend.config, "crawl")
    monkeypatch.setenv("CRAWL_MONTH_CAP_USD", "50 usd")

    mark = _concern(await silence.alarms(session, stages=EVERY_STAGE), "crawl-config-broken")

    assert mark.cause


def test_a_silent_provider_is_a_cause_and_says_what_to_check() -> None:
    """Отказ, который сам не пройдёт, — ключ и настройки; долгое молчание — сеть и провайдер."""
    clock = [0.0]
    watch = silence.ProviderWatch(clock=lambda: clock[0])
    revoked = [silence.Miss("Ahrefs", "ключ отозван", permanent=True)]
    timeout = [silence.Miss(silence.SERP, "таймаут")]

    lasting = [watch.judge(revoked) for _ in range(2)][-1]
    watch = silence.ProviderWatch(clock=lambda: clock[0])
    watch.judge(timeout)
    clock[0] = silence.PROVIDER_DOWN_MINUTES * 60
    outage = watch.judge(timeout)

    assert lasting is not None
    assert outage is not None
    assert (lasting.concern, outage.concern) == (silence.LASTING, silence.OUTAGE)
    _plain(silence.LASTING)
    _plain(silence.OUTAGE)


# --- почта этапа: сторож у политики, которая его просит (продажи) --------------------------


@pytest.fixture
def watched(monkeypatch: pytest.MonkeyPatch) -> FakeSalesMail:
    """Продажи со сторожем в политике — подставным модулем, как в `tests/test_mail_watch.py`."""
    monkeypatch.setattr(stages._SALES, "load", None)
    found = FakeSalesMail(rules=MailPolicy(watch=True))
    stages.register_sales(lambda: found)
    return found


async def _mail(session: AsyncSession, at: datetime = NOW) -> list[Alarm]:
    return await mail_watch.alarms(session, at, stages=EVERY_STAGE)


async def _queued(session: AsyncSession, box: SenderModel) -> None:
    """Первое письмо продаж в очереди, ящик пишет; его домен — строкой домена рассылки."""
    await _mail_letter(session, Stage.SALES, None, status=MessageStatus.QUEUED)
    assert box.enabled


async def test_a_policy_the_sales_module_did_not_give_is_a_cause(
    session: AsyncSession, watched: FakeSalesMail
) -> None:
    watched.broken = "policy"

    mark = _concern(await _mail(session), "no-policy:sales")

    assert mark.cause


@pytest.mark.usefixtures("watched")
async def test_a_failed_watch_query_is_a_cause(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await sender(session, BOX, Stage.SALES)

    async def broken(session: AsyncSession, box: SenderModel, now: datetime) -> Alarm | None:
        await session.execute(text("SELECT 1 FROM made_up_table_of_the_watch"))
        return None

    monkeypatch.setattr(mail_watch, "_quiet", broken)

    mark = _concern(await _mail(session), "watch-failed:sales")

    assert mark.cause


@pytest.mark.usefixtures("watched")
async def test_all_boxes_paused_is_a_cause(session: AsyncSession) -> None:
    box = await sender(session, BOX, Stage.SALES)
    box.status = SenderStatus.PAUSED

    mark = _concern(await _mail(session), "all-paused:sales")

    assert mark.cause


@pytest.mark.usefixtures("watched")
async def test_nobody_to_send_behind_a_young_domain_is_no_cause(session: AsyncSession) -> None:
    """Выдержка домена кончится сама, и письма пойдут: повода нет — и сказано, до какой минуты.
    Закрыт ещё и чужой домен на паузе — повода всё равно нет: письма уйдут с первого."""
    box = await sender(session, BOX, Stage.SALES)
    other = await sender(session, "hi@mail-sales2.example.test", Stage.SALES)
    young = datetime(2026, 10, 9, 8, 30, tzinfo=UTC)
    session.add_all(
        [
            SendingDomainModel(
                domain=box.domain, stage=Stage.SALES, daily_limit=5, young_until=young
            ),
            SendingDomainModel(
                domain=other.domain, stage=Stage.SALES, daily_limit=5, paused_at=NOW
            ),
        ]
    )
    await _queued(session, box)

    mark = _concern(await _mail(session), "nobody-to-send:sales")

    assert not mark.cause
    assert mark.why == (
        "домен mail-sales.example.test на выдержке до 09.10 08:30 UTC, и письма пойдут, когда "
        "она кончится"
    )


@pytest.mark.usefixtures("watched")
async def test_nobody_to_send_behind_a_paused_domain_is_a_cause(session: AsyncSession) -> None:
    box = await sender(session, BOX, Stage.SALES)
    session.add(
        SendingDomainModel(domain=box.domain, stage=Stage.SALES, daily_limit=5, paused_at=NOW)
    )
    await _queued(session, box)

    mark = _concern(await _mail(session), "nobody-to-send:sales")

    assert mark == mail_watch.NOBODY


@pytest.mark.usefixtures("watched")
async def test_a_quiet_box_is_a_cause(session: AsyncSession) -> None:
    box = await sender(session, BOX, Stage.SALES)
    await _mail_letter(
        session,
        Stage.SALES,
        box,
        status=MessageStatus.SENT,
        sent_at=NOW - timedelta(days=3),
        next_action_at=NOW - timedelta(minutes=20),
    )

    mark = _concern(await _mail(session), f"quiet-box:{BOX}")

    assert mark.cause


@pytest.mark.usefixtures("watched")
async def test_a_retired_and_a_deleted_box_are_a_cause(session: AsyncSession) -> None:
    box = await sender(session, BOX, Stage.SALES)
    senders.disable(box, "вручную", now=NOW - timedelta(minutes=3))
    await sender(session, "hi@mail-sales2.example.test", Stage.SALES)
    for number, owner in ((1, box), (2, None)):
        await _mail_letter(
            session,
            Stage.SALES,
            owner,
            number,
            status=MessageStatus.SENT,
            sent_at=NOW - timedelta(days=3),
            next_action_at=NOW - timedelta(minutes=1),
        )

    found = await _mail(session)

    assert _concern(found, f"retired-box:{BOX}") == mail_watch.RETIRED
    assert _concern(found, "retired-box:deleted:sales") == mail_watch.DELETED


# --- экран: пометка каждой тревоги -------------------------------------------------------


async def test_the_screen_gets_the_mark_of_each_alarm(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    client: AsyncClient,
    make_user: MakeUser,
    sign_in: SignIn,
) -> None:
    """Те же слова, что последней строкой в Telegram: «Обзор» ставит по ним метку."""
    await make_user("viewer@concern.example.test")
    headers = bearer(await sign_in("viewer@concern.example.test"))
    await _letter(session, status=MessageStatus.SENT, sent_at=NOW - timedelta(hours=8))
    await session.commit()

    async def answered() -> Alarm | None:
        return None

    monkeypatch.setattr(routes, "probe_providers", answered)

    response = await client.get("/api/watchdog", headers=headers)

    assert response.status_code == 200, response.text
    [card] = [one for one in response.json()["alarms"] if one["code"] == "delivery-silence:donors"]
    assert card["concern"] == {
        "cause": True,
        "words": (
            "Есть повод для беспокойства — ни одно письмо направления не получает статусов: "
            "проверить адрес вебхука и подпись в кабинете платформы."
        ),
    }
    assert routes.AlarmCard.of(Alarm(code="x", title="X", detail="x")).concern is None
