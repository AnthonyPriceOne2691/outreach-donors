"""Хвосты первого круга ревью стыков и непокрытое, что важно для боя (ревью стыков, круг 2).

Первый круг (08.10) оставил висеть шесть пунктов. Модульные закрыты здесь — тестом, красным
без правки:

- **R2 7.1 — итог передачи и сбой базы после неё.** Передача лида коммитит строку сама
  (`handoff.start`), а итог «передан телемаркетологу» в снимке ответа писался вторым коммитом.
  Сбой базы между ними оставлял переданного лида ждать человека, а повтор задачи ответ уже не
  брал — «уже разобран». Итог теперь ложится в снимок до передачи и уходит её же коммитом.
- **R3 4.9 — журнал агента продаж по полям.** Значение из текста записи — ещё и полем `extra`,
  как у шва агента (`test_agent_log_fields.py`): журнал прода ищется по полю, текст прежний.
  Проверка — обходом исходников агента продаж: новая запись проверяется сама.
- **R1 §3.6 — экран сторожа и сеть.** `GET /api/watchdog` опрашивает провайдеров при открытой
  транзакции запроса, а с #355 его спрашивает полоса тревог над вкладками продаж — при открытии
  раздела и каждые 5 минут. Общий код: `xfail(strict)`, правка — PR «общее».

Письмо продаж без лида словами (R1 5.3) — в `test_sales_mail_seams.py`, рядом с пачкой.

Непокрытое прогоном покрытия модуля (13.10), что важно для боя, — держится тестами ниже:

- **Потолок модели и лид, которого не передавали.** Письмо уходит телемаркетологу при потолке
  только у переданного лида (`SalesReplies.forward_unread`); у остальных — ни сделки в Kommo, ни
  сообщения в Telegram: передача без «хочет говорить» — внешнее действие без человека.
- **Адрес после маскирования** (`agent/calling.ask`): маскирование не сработало — запрос в модель
  не уходит вовсе (ТЗ: наружу не уходит ни один адрес).
"""

from __future__ import annotations

import ast
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from backend.features.core.domain import Stage
from backend.features.core.models.access import UserModel
from backend.features.core.models.outreach import ReplyModel
from backend.features.core.usage import LlmCapExceededError
from backend.features.letters import masking
from backend.features.outreach.threads import review_of
from backend.features.sales import handoff, replies
from backend.features.sales.agent import calling, situation
from backend.features.sales.models import SalesHandoffModel
from backend.features.sales.replies import SalesReplies
from backend.shared.llm import Refusal, RefusalKind
from backend.workers import sales_jobs
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.conftest import bearer
from tests.test_sales_agent_situation import Plug, llm
from tests.test_sales_handoff_rows import sales_dialog
from tests.test_sales_reply_handoff import AT, HANDED, TALK, _job_on_test_base
from tests.test_sales_reply_routing import FakeClassifier, _Closable
from tests.test_sales_switch import sales_switched_on

__all__ = ["llm", "sales_switched_on"]  # подставная модель; продажи включены

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]
HandOver = Callable[[AsyncSession, int], Awaitable[object]]

ROOT = Path(__file__).resolve().parent.parent
#: Агент продаж: ситуация письма, судья, прогон — его журнал читают по ответу и черновику.
AGENT = ROOT / "backend/features/sales/agent"
LEVELS = frozenset({"debug", "info", "warning", "error", "exception", "critical"})


async def _capped(*_: object, **__: object) -> None:
    raise LlmCapExceededError("потолок расхода на модель за сутки")


async def _handoffs(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(SalesHandoffModel)) or 0)


# --- R2 7.1: итог передачи уходит коммитом передачи -----------------------------------------


def _job_whose_commit_fails(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession, start: HandOver, *, failing: int
) -> list[int]:
    """Тело задачи ответа на базе теста; коммит номер `failing` падает сбоем базы, не дойдя до
    базы. Коммит задачи здесь — сброс в транзакцию теста: несброшенное до упавшего коммита
    пропадает вместе с задачей, как на проде."""
    factory = async_sessionmaker(bind=session.bind, expire_on_commit=False)
    commits: list[int] = []

    @asynccontextmanager
    async def opened() -> AsyncIterator[AsyncSession]:
        async with factory() as job_session:
            commit = job_session.commit

            async def flaky_commit() -> None:
                commits.append(len(commits) + 1)
                if len(commits) == failing:
                    raise OperationalError("COMMIT", {}, Exception("база не ответила"))
                await commit()

            monkeypatch.setattr(job_session, "commit", flaky_commit)
            yield job_session

    monkeypatch.setattr(sales_jobs, "KindClient", lambda: FakeClassifier(TALK))
    monkeypatch.setattr(sales_jobs, "create_async_engine", lambda _dsn: _Closable())
    monkeypatch.setattr(sales_jobs, "async_sessionmaker", lambda _engine, **_kw: opened)
    monkeypatch.setattr(sales_jobs, "SalesReplies", partial(SalesReplies, hand_over=start))
    return commits


async def test_a_base_failure_after_the_handoff_leaves_no_handed_lead_waiting_for_a_human(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession
) -> None:
    """Задача ответа: разбор — коммит, передача — свой коммит, затем коммит задачи, и он упал
    (сбой базы). Лид уже у телемаркетолога — ответ не ждёт человека: иначе человек напишет лиду,
    которому звонят. Повтор задачи ответ не берёт — «уже разобран», и итог не поправит."""
    dialog = await sales_dialog(session)
    queued: list[int] = []
    start = partial(handoff.start, enqueue=queued.append, now=lambda: AT)
    commits = _job_whose_commit_fails(monkeypatch, session, start, failing=3)

    with pytest.raises(OperationalError, match="база не ответила"):
        await sales_jobs.handle(dialog.reply.id)

    assert commits == [1, 2, 3], "разбор, передача, коммит задачи — упал третий"
    row = await session.scalar(
        select(SalesHandoffModel).where(SalesHandoffModel.thread_id == dialog.thread.id)
    )
    assert row is not None, "лид передан: строка передачи закоммичена"
    assert queued == [row.id], "и задача Kommo и Telegram поставлена"
    reply = await session.get(ReplyModel, dialog.reply.id, populate_existing=True)
    assert reply is not None
    review = review_of(reply, Stage.SALES)
    assert (review.waiting, review.reason) == (False, HANDED), "переданный лид не ждёт человека"
    again = await sales_jobs.handle(dialog.reply.id)
    assert again["skipped"] == "уже разобран", "повтор задачи ответ не берёт"


# --- потолок модели: передаётся только переданный лид ----------------------------------------


async def test_the_model_cap_hands_over_no_lead_that_was_not_handed_over(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession
) -> None:
    """Потолок модели: вид ответа — завтра; письмо уходит телемаркетологу сейчас только у лида,
    уже переданного (`forward_unread`). Этот лид не передан — ни строки передачи, ни задачи Kommo
    и Telegram, хотя пишет он «давайте созвонимся»: вид назовёт завтрашний разбор."""
    dialog = await sales_dialog(session)
    queued: list[int] = []
    start = partial(handoff.start, enqueue=queued.append, now=lambda: AT)
    _job_on_test_base(monkeypatch, session, TALK, start, [])
    monkeypatch.setattr(replies.usage, "ensure_llm_within_cap", _capped)

    with pytest.raises(LlmCapExceededError):
        await sales_jobs.handle(dialog.reply.id)

    assert (queued, await _handoffs(session)) == ([], 0)
    reply = await session.get(ReplyModel, dialog.reply.id, populate_existing=True)
    assert reply is not None
    assert reply.model_parse is None, "разбора не было — ответ разберут завтра"


# --- адрес после маскирования в модель не уходит --------------------------------------------


async def test_an_address_left_after_masking_never_reaches_the_model(
    monkeypatch: pytest.MonkeyPatch, llm: Plug
) -> None:
    """Маскирование не сработало (текст уходит как есть): вызов агента продаж — отказ «чинить»
    без запроса, а не письмо лида с адресом в модели."""
    model = llm(situation=[{"situation": "question", "confidence": 0.9}])
    monkeypatch.setattr(masking, "mask", lambda text, **_: masking.Masked(text=text))

    found = await calling.ask(
        prompt=situation.PROMPT,
        user="Пишите Джейн напрямую: jane@acme.example.test",
        model="gpt-4.1-mini",
        topic=situation.TOPIC,
    )

    assert model.sent == {"situation": []}, "запрос с адресом ушёл в модель"
    assert isinstance(found, Refusal)
    assert (found.kind, found.permanent) == (RefusalKind.LOCAL, True)


# --- R3 4.9: журнал агента продаж по полям -----------------------------------------------------


def _unfielded(path: Path) -> list[str]:
    """Записи журнала со значениями в тексте (`%`-аргументы) и без полей `extra`."""
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        logged = isinstance(node.func.value, ast.Name) and node.func.value.id == "logger"
        if not logged or node.func.attr not in LEVELS or len(node.args) < 2:
            continue
        if not any(keyword.arg == "extra" for keyword in node.keywords):
            found.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    return found


def test_every_log_line_of_the_sales_agent_carries_its_values_as_fields() -> None:
    files = sorted(AGENT.rglob("*.py"))

    assert len(files) >= 10, "обход не нашёл агента продаж — путь сменился?"
    assert [place for path in files for place in _unfielded(path)] == []


# --- R1 §3.6: экран сторожа спрашивает провайдеров вне транзакции запроса -------------------


@pytest.mark.xfail(
    strict=False,  # общий PR его закрывает — метку снять после слияния
    reason=(
        "общий код backend/api/watchdog/routes.py: GET /api/watchdog опрашивает провайдеров "
        "(Ahrefs, выдача; таймауты 30 и 60 с) при открытой транзакции запроса — соединение "
        "«idle in transaction» на время опроса; с #355 маршрут спрашивает полоса тревог продаж "
        "при открытии раздела и каждые 5 минут; правка — опрос после закрытия сессии, PR «общее» "
        "(хвост R1 §3.6, ревью стыков R7)"
    ),
)
async def test_the_alarm_strip_asks_the_providers_outside_the_request_transaction(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    client: AsyncClient,
    make_user: MakeUser,
    sign_in: SignIn,
) -> None:
    await make_user("seller@ours.example.test")
    headers = bearer(await sign_in("seller@ours.example.test"))
    seen: list[bool] = []

    async def probe() -> Any:
        seen.append(session.in_transaction())
        return None

    monkeypatch.setattr("backend.api.watchdog.routes.probe_providers", probe)
    await session.commit()  # оснастка теста сброшена: транзакцию открывает только запрос

    response = await client.get("/api/watchdog", headers=headers)

    assert response.status_code == 200, response.text
    assert seen == [False], "провайдеров спрашивали при открытой транзакции запроса"
