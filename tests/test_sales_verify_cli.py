"""Команда `outreach sales-verify-email` — живая проверка одного адреса перед включением Hunter.

Путь тот же, что у очистки: проверяльщик по настройке (`verifier.build_verifier`), запрос —
`HunterVerifier` с повторами. Сеть — `httpx.MockTransport` в формате ответа Hunter, база
настоящая: команда пишет одну строку расхода и не трогает лидов. Вывод сверяется целиком —
по нему при включении Hunter судят, заработал ли механизм.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from backend.cli import sales_verify
from backend.cli.main import _KEPT_ON_INTERRUPT, build_parser
from backend.cli.sales import EXIT_BAD_INPUT, EXIT_OK, EXIT_UNAVAILABLE, EXIT_VERIFIER_STOPPED
from backend.cli.sales_verify import NOT_LIVE, SAID, run_verify_email
from backend.config import contacts as contacts_cfg
from backend.config import sales as sales_cfg
from backend.config.startup_checks import ConfigError
from backend.features.core import usage
from backend.features.core.domain import Stage, UsageProvider
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.ops import UsageRecordModel
from backend.features.sales.models import (
    LeadSource,
    LeadStatus,
    SalesHypothesisModel,
    SalesLeadModel,
)
from backend.features.sales.verifier import DELIVERABLE_BY_STATUS, TOO_OFTEN
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

EMAIL = "ivan@acme.example.test"
KEY = "k-test"
LIVE = "Проверяльщик адресов: hunter — живой, ключ общий с донорами.\n"
NO_VERDICT = "Вердикта нет — в журнал расхода ничего не записано, лиды не тронуты.\n"

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture(autouse=True)
def _live_with_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Живой проверяльщик с ключом; паузы повторов гасит общий `conftest`."""
    monkeypatch.setattr(sales_cfg, "VERIFIER_PROVIDER", "live")
    monkeypatch.setattr(contacts_cfg, "HUNTER_API_KEY", KEY)
    monkeypatch.setattr(sales_cfg, "VERIFIER_MONTH_CAP", "1500")


def _answer(status: int, payload: Any = None) -> tuple[Handler, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status) if payload is None else httpx.Response(status, json=payload)

    return handler, seen


async def _run(session: AsyncSession, handler: Handler, email: str = EMAIL) -> int:
    args = build_parser().parse_args(["sales-verify-email", "--email", email])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        return await run_verify_email(session, args, http=http)


async def _usage(
    session: AsyncSession,
) -> list[tuple[str, UsageProvider, int | None, Stage | None]]:
    rows = await session.execute(
        select(
            UsageRecordModel.operation,
            UsageRecordModel.provider,
            UsageRecordModel.units,
            UsageRecordModel.stage,
        )
    )
    return [tuple(row) for row in rows.tuples()]  # type: ignore[misc]


def test_console_knows_the_command_and_what_it_keeps_on_interrupt() -> None:
    args = build_parser().parse_args(["sales-verify-email", "--email", EMAIL])
    assert (args.command, args.email) == ("sales-verify-email", EMAIL)
    assert "лиды не тронуты" in _KEPT_ON_INTERRUPT["sales-verify-email"].lower()


async def test_live_verdict_is_said_in_words_and_its_one_unit_goes_to_the_journal(
    session: AsyncSession, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Тот путь, которым проверяют ключ при включении: один запрос, ключ заголовком, вердикт
    словами, одна единица — строкой расхода этапа продаж, закоммиченной: сессия команды
    закрывается сразу, и незакоммиченная строка ушла бы мимо журнала. Лид с тем же адресом не
    тронут."""
    at_commit: list[int] = []
    commit = session.commit

    async def watched() -> None:
        at_commit.append(len(await _usage(session)))
        await commit()

    monkeypatch.setattr(session, "commit", watched)
    hypothesis = SalesHypothesisModel(name="сайты EN")
    domain = DomainModel(host="acme.example.test")
    session.add_all([hypothesis, domain])
    await session.flush()
    lead = SalesLeadModel(
        hypothesis_id=hypothesis.id,
        domain_id=domain.id,
        email=EMAIL,
        source=LeadSource.IMPORT,
        status=LeadStatus.NEW,
    )
    session.add(lead)
    await session.flush()
    handler, seen = _answer(200, {"data": {"status": "valid", "score": 97}})

    assert await _run(session, handler, " Ivan@Acme.Example.Test ") == EXIT_OK

    assert capsys.readouterr().out == (
        LIVE
        + f"{EMAIL}: valid, уверенность 97 — адрес есть; очистка пустит такого лида к письмам.\n"
        "Единиц Hunter: 1 — записано в журнал расхода (sales_verify, продажи).\n"
        "Проверок адреса в этом месяце 1 из 1500.\n"
    )
    (request,) = seen
    assert request.url.params["email"] == EMAIL
    assert request.headers["Authorization"] == f"Bearer {KEY}"
    assert await _usage(session) == [("sales_verify", UsageProvider.HUNTER, 1, Stage.SALES)]
    assert at_commit == [1]
    await session.refresh(lead)
    assert (lead.status, lead.verification_status, lead.cleaning_note) == (
        LeadStatus.NEW,
        None,
        None,
    )


@pytest.mark.parametrize(
    ("data", "line"),
    [
        ({"status": "accept_all"}, "accept_all, уверенность не названа — сервер принимает любой адрес — ящик не подтверждён, но и не отвергнут; очистка пустит такого лида к письмам."),
        ({"status": "webmail", "score": 88}, "webmail, уверенность 88 — ящик на бесплатной почте; очистка пустит такого лида к письмам."),
        # Решение владельца 13.10: `unknown` к письмам не идёт, `accept_all` — идёт, как раньше.
        ({"status": "unknown", "score": 23}, "unknown, уверенность 23 — провайдер не знает; к письмам такой лид не пойдёт: очистка проверит его снова не раньше чем через 7 дней, второй такой ответ отклонит лида."),
        ({"status": "invalid", "score": 2}, "invalid, уверенность 2 — адрес не существует; очистка отсеет такого лида."),
        ({"status": "disposable"}, "disposable, уверенность не названа — одноразовый ящик; очистка отсеет такого лида."),
    ],
)  # fmt: skip
async def test_every_verdict_says_what_the_cleaning_will_do_with_such_a_lead(
    session: AsyncSession, capsys: pytest.CaptureFixture[str], data: dict[str, Any], line: str
) -> None:
    handler, _ = _answer(200, {"data": data})

    assert await _run(session, handler) == EXIT_OK

    assert f"{EMAIL}: {line}\n" in capsys.readouterr().out
    assert await _usage(session) == [("sales_verify", UsageProvider.HUNTER, 1, Stage.SALES)]


def test_every_word_of_the_provider_has_its_words() -> None:
    assert set(SAID) == set(DELIVERABLE_BY_STATUS)


@pytest.mark.parametrize(
    ("status", "payload", "code", "words"),
    [
        (401, {"errors": [{"id": "authentication_failed", "details": "No user found"}]}, EXIT_VERIFIER_STOPPED, "ключ проверки адресов не принят провайдером (HTTP 401, No user found) — ключ меняет администратор"),
        (429, {"errors": [{"id": "restricted_account", "details": "Your account was restricted"}]}, EXIT_VERIFIER_STOPPED, "учётка закрыта провайдером: Your account was restricted — квота тут ни при чём, зайти в кабинет и разобраться"),
        (429, {"errors": [{"id": "usage_exceeded", "details": "monthly quota"}]}, EXIT_VERIFIER_STOPPED, "квота исчерпана: monthly quota"),
        (429, None, EXIT_VERIFIER_STOPPED, TOO_OFTEN),
        (400, {"errors": [{"id": "blacklisted_domain", "details": "This domain is blacklisted"}]}, EXIT_VERIFIER_STOPPED, "провайдер не работает с доменом: This domain is blacklisted — владелец домена запретил обработку адресов"),
        (503, None, EXIT_UNAVAILABLE, "провайдер не ответил (HTTP 503)"),
        (202, {"data": {"status": "pending"}}, EXIT_UNAVAILABLE, "провайдер ещё проверяет адрес и после 2 переспросов — вердикта пока нет"),
        (200, {"data": {"status": "weird"}}, EXIT_UNAVAILABLE, "формат ответа поменялся: status='weird' (HTTP 200) — читать лог, не гадать"),
    ],
)  # fmt: skip
async def test_refusal_is_said_in_words_with_its_code_and_nothing_is_written(
    session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
    status: int,
    payload: Any,
    code: int,
    words: str,
) -> None:
    """Код 7 — повтор без человека не поможет (ключ, учётка, квота, частота и после повторов,
    домен, с которым сервис проверки не работает), код 8 — повтор поможет. Ни строки расхода, ни
    правки лидов: вердикта нет."""
    handler, _ = _answer(status, payload)

    assert await _run(session, handler) == code

    assert capsys.readouterr().out == LIVE + f"Hunter отказал: {words}.\n" + NO_VERDICT
    assert await _usage(session) == []


async def test_no_network_is_said_in_words_without_the_key(
    session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("сети нет")

    assert await _run(session, broken) == EXIT_UNAVAILABLE

    out = capsys.readouterr().out
    assert "Hunter отказал: сеть до провайдера не дошла: ConnectError('сети нет')." in out
    assert KEY not in out


async def test_made_up_verifier_does_not_pretend_to_be_a_live_check(
    session: AsyncSession, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`fixture` — настройка не та или контейнер не пересоздан: Hunter не спрошен, код 2. Совет —
    пересоздать (`up -d`): `restart` не перечитывает `.env`, и процесс остался бы на `fixture`."""
    monkeypatch.setattr(sales_cfg, "VERIFIER_PROVIDER", "fixture")
    handler, seen = _answer(200, {"data": {"status": "valid", "score": 97}})

    assert await _run(session, handler) == EXIT_BAD_INPUT

    assert capsys.readouterr().out == NOT_LIVE + "\n"
    assert "up -d, не restart" in NOT_LIVE
    assert (seen, await _usage(session)) == ([], [])


async def test_live_without_a_key_refuses_before_any_request(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отказ настроек — `ConfigError`: консоль переводит его в код 2 общей таблицей."""
    monkeypatch.setattr(contacts_cfg, "HUNTER_API_KEY", "")
    handler, seen = _answer(200, {"data": {"status": "valid", "score": 97}})

    with pytest.raises(ConfigError, match="CONTACTS_HUNTER_API_KEY пуст"):
        await _run(session, handler)

    assert seen == []


@pytest.mark.parametrize("email", ["ivan", "ivan@acme", "@acme.example.test", "ivan@.test"])
async def test_not_an_address_is_refused_before_any_request(
    session: AsyncSession, capsys: pytest.CaptureFixture[str], email: str
) -> None:
    handler, seen = _answer(200, {"data": {"status": "valid", "score": 97}})

    assert await _run(session, handler, email) == EXIT_BAD_INPUT

    assert capsys.readouterr().out == f"«{email}» — не адрес почты: нужен вид имя@домен.зона\n"
    assert seen == []


async def test_command_runs_on_its_own_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Точка входа — одна сессия на команду (`cli/prune.in_session`), как у соседних."""
    taken: list[str] = []

    async def own_session(_work: Any) -> int:
        taken.append("сессия")
        return EXIT_OK

    monkeypatch.setattr(sales_verify, "in_session", own_session)

    assert await sales_verify.cmd_sales_verify_email(argparse.Namespace(email=EMAIL)) == EXIT_OK
    assert taken == ["сессия"]


@pytest.mark.parametrize(
    ("cap", "spent", "words"),
    [
        ("", 0, "потолок проверки адресов на месяц не задан — задайте SALES_VERIFIER_MONTH_CAP"),
        ("5", 5, "потолок проверки адресов на месяц выбран: 5 из 5 — следующий месяц продолжит"),
    ],
)
async def test_month_cap_not_set_or_spent_stops_the_check_before_hunter(
    session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    cap: str,
    spent: int,
    words: str,
) -> None:
    """Тот же месячный потолок, что у очистки: не задан или выбран — Hunter не спрошен, код 7
    (повтор без человека не поможет), строки расхода нет."""
    monkeypatch.setattr(sales_cfg, "VERIFIER_MONTH_CAP", cap)
    if spent:
        usage.record(session, operation="sales_verify", units=spent)
        await session.flush()
    handler, seen = _answer(200, {"data": {"status": "valid", "score": 97}})

    assert await _run(session, handler) == EXIT_VERIFIER_STOPPED

    assert capsys.readouterr().out == LIVE + f"Проверка не сделана: {words}.\n" + NO_VERDICT
    assert seen == []
    assert len(await _usage(session)) == (1 if spent else 0)
