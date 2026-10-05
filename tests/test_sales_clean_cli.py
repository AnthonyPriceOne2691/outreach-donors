"""Команда `outreach sales-clean` — срез 1.4: сводка словами, коды выхода, отказ на старте.

Путь тот же, что в консоли: доводы разбирает `build_parser`, база настоящая,
сеть — заглушка `mail_route` и `httpx.MockTransport`. Вывод сверяется целиком:
человек читает его глазами, и каждая строка — обещание.
"""

from __future__ import annotations

import argparse
from typing import Any

import httpx
import pytest
from backend.cli.main import (
    _COMMANDS,
    _FAILURES,
    EXIT_CANCELLED,
    EXIT_MISCONFIGURED,
    build_parser,
    main,
)
from backend.cli.sales import EXIT_NO_HYPOTHESIS, EXIT_OK, EXIT_VERIFIER_STOPPED, run_clean
from backend.config import contacts as contacts_cfg
from backend.config import sales as sales_cfg
from backend.config.startup_checks import ConfigError
from backend.features.contacts.mx import MailRoute
from backend.features.core.models.domain import DomainModel
from backend.features.sales import cleaning
from backend.features.sales.models import (
    LeadSource,
    LeadStatus,
    SalesHypothesisModel,
    SalesLeadModel,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

ROUTES = {"none.example.test": MailRoute.NONE, "silent.example.test": MailRoute.UNKNOWN}
SUMMARY = """Проверяльщик адресов: fixture — вердикты выдуманные, для писем включите live
Очистка продаж: проверено 6; готово 2; отклонено 4; не проверено 0.
  дубль: 1
  негодный адрес: 1
  домен не принимает почту: 1
  адрес не существует: 1
  DNS не ответил по доменам: 1 — адреса прошли дальше непроверенными
  вердиктов проверяльщика: 3, платных: 0
"""


@pytest.fixture(autouse=True)
def _dns_by_table(monkeypatch: pytest.MonkeyPatch) -> None:
    async def route(host: str, **_kwargs: object) -> MailRoute:
        return ROUTES.get(host, MailRoute.MX)

    monkeypatch.setattr(cleaning, "mail_route", route)
    monkeypatch.setattr(sales_cfg, "VERIFIER_PROVIDER", "fixture")


@pytest.fixture
async def hypothesis(session: AsyncSession) -> SalesHypothesisModel:
    found = SalesHypothesisModel(name="сайты EN")
    session.add(found)
    await session.flush()
    return found


async def _leads(session: AsyncSession, hypothesis: SalesHypothesisModel, *emails: str) -> None:
    """Лиды `new`; домен компании — домен адреса, одна строка `domains` на хост."""
    domains: dict[str, int] = {}
    for email in emails:
        host = email.rpartition("@")[2]
        if host not in domains:
            domain = DomainModel(host=host)
            session.add(domain)
            await session.flush()
            domains[host] = domain.id
        session.add(
            SalesLeadModel(
                hypothesis_id=hypothesis.id,
                domain_id=domains[host],
                email=email,
                source=LeadSource.IMPORT,
            )
        )
    await session.flush()


async def _statuses(session: AsyncSession) -> list[LeadStatus]:
    return list(await session.scalars(select(SalesLeadModel.status).order_by(SalesLeadModel.id)))


async def _run(session: AsyncSession, *extra: str, http: httpx.AsyncClient | None = None) -> int:
    args = build_parser().parse_args(["sales-clean", *extra])
    return await run_clean(session, args, http=http)


def _live(monkeypatch: pytest.MonkeyPatch, status: int, payload: Any) -> httpx.AsyncClient:
    monkeypatch.setattr(sales_cfg, "VERIFIER_PROVIDER", "live")
    monkeypatch.setattr(contacts_cfg, "HUNTER_API_KEY", "k-test")
    return httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(status, json=payload))
    )


def test_console_knows_the_command() -> None:
    args = build_parser().parse_args(["sales-clean", "--hypothesis", "сайты EN"])
    assert (args.command, args.hypothesis) == ("sales-clean", "сайты EN")
    assert build_parser().parse_args(["sales-clean"]).hypothesis is None


async def test_summary_names_every_outcome_in_words_and_a_second_pass_finds_nothing(
    session: AsyncSession, hypothesis: SalesHypothesisModel, capsys: pytest.CaptureFixture[str]
) -> None:
    await _leads(
        session,
        hypothesis,
        "ivan@acme.example.test",
        "ivan@acme.example.test",
        "privacy@acme.example.test",
        "bounce@acme.example.test",
        "a@none.example.test",
        "c@silent.example.test",
    )

    assert await _run(session) == EXIT_OK
    assert capsys.readouterr().out == SUMMARY

    assert await _run(session) == EXIT_OK
    assert capsys.readouterr().out.endswith("Очистка продаж: лидов new нет — проверять нечего.\n")


async def test_hypothesis_narrows_the_pass_and_an_unknown_one_is_refused(
    session: AsyncSession, hypothesis: SalesHypothesisModel, capsys: pytest.CaptureFixture[str]
) -> None:
    other = SalesHypothesisModel(name="сайты DE")
    session.add(other)
    await session.flush()
    await _leads(session, hypothesis, "ivan@acme.example.test")
    await _leads(session, other, "hans@beta.example.test")

    assert await _run(session, "--hypothesis", "нет такой") == EXIT_NO_HYPOTHESIS
    assert capsys.readouterr().out == (
        "Гипотезы «нет такой» нет — заведите её: outreach sales-hypothesis-add\n"
    )
    assert await _statuses(session) == [LeadStatus.NEW, LeadStatus.NEW]

    assert await _run(session, "--hypothesis", "сайты  EN") == EXIT_OK
    assert "Очистка продаж: проверено 1; готово 1; отклонено 0; не проверено 0.\n" in (
        capsys.readouterr().out
    )
    assert await _statuses(session) == [LeadStatus.READY, LeadStatus.NEW]


async def test_stopped_paid_part_has_its_own_code_and_says_what_to_do(
    session: AsyncSession,
    hypothesis: SalesHypothesisModel,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # A6
    # A6 — пример спеки
    await _leads(session, hypothesis, "ivan@acme.example.test")
    quota = {"errors": [{"id": "usage_exceeded", "details": "monthly"}]}

    async with _live(monkeypatch, 429, quota) as http:
        assert await _run(session, http=http) == EXIT_VERIFIER_STOPPED

    assert capsys.readouterr().out == (
        "Проверяльщик адресов: hunter\n"
        "Очистка продаж: проверено 1; готово 0; отклонено 0; не проверено 1.\n"
        "\nПлатная часть остановлена: квота исчерпана: monthly. Непроверенные лиды "
        "остались new — повторите очистку, когда причина снята.\n"
    )
    assert await _statuses(session) == [LeadStatus.NEW]


async def test_a_passing_failure_is_exit_zero_and_promises_a_retry(
    session: AsyncSession,
    hypothesis: SalesHypothesisModel,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # A6
    # A6 — пример спеки
    await _leads(session, hypothesis, "ivan@acme.example.test")

    async with _live(monkeypatch, 503, {"errors": [{"details": "down"}]}) as http:
        assert await _run(session, http=http) == EXIT_OK

    assert capsys.readouterr().out.endswith(
        "\nНе проверенные лиды остались new: следующая очистка повторит их.\n"
    )
    assert await _statuses(session) == [LeadStatus.NEW]


async def test_a7_live_without_a_key_refuses_before_touching_a_lead(
    session: AsyncSession,
    hypothesis: SalesHypothesisModel,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # A7
    # A7 — пример спеки
    monkeypatch.setattr(sales_cfg, "VERIFIER_PROVIDER", "live")
    monkeypatch.setattr(contacts_cfg, "HUNTER_API_KEY", "")
    await _leads(session, hypothesis, "ivan@acme.example.test")

    with pytest.raises(ConfigError, match="CONTACTS_HUNTER_API_KEY пуст"):
        await _run(session)

    assert capsys.readouterr().out == ""
    assert await _statuses(session) == [LeadStatus.NEW]
    # Консоль переводит отказ настроек в код 2 одной таблицей на все команды.
    assert {kind: code for kind, code, _ in _FAILURES}[ConfigError] == EXIT_MISCONFIGURED


@pytest.mark.parametrize(
    ("argv", "kept"),
    [
        (["sales-import", "--hypothesis", "сайты EN", "--file", "база.csv"], "одной транзакцией"),
        (["sales-stoplist-add", "--file", "стоп.csv"], "одной транзакцией"),
        (["sales-clean"], "партии остались"),
    ],
)
def test_second_ctrl_c_says_what_each_sales_command_kept(
    argv: list[str],
    kept: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Подсказка точки входа — по команде: загрузка и стоп-лист пишутся одной транзакцией,
    очистка — партиями; общее умолчание про домены к продажам не относится."""

    async def interrupted(_args: argparse.Namespace) -> int:
        raise KeyboardInterrupt

    monkeypatch.setitem(_COMMANDS, argv[0], interrupted)
    # Настоящая настройка журнала снимает с корневого логгера хендлеры pytest, и
    # соседние тесты, идущие позже (`test_cli_main.py`), перестают слышать журнал.
    monkeypatch.setattr("backend.cli.main.setup_logging", lambda: None)

    code = main(argv)

    assert code == EXIT_CANCELLED
    err = capsys.readouterr().err
    assert kept in err
    assert "домены остались" not in err
