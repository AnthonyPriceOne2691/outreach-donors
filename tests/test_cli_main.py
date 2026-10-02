"""Точка входа `outreach` (`backend/cli/main.py`): что она печатает, что
возвращает и какой код выхода получает вызывающий скрипт.

Наружу тесты не ходят. Ahrefs — настоящий клиент на заглушке транспорта
(`httpx.MockTransport`): так проверяется и разбор ответа, и то, что клиент
закрыт после команды. Ответ терминала — подменённый `input`. Точке входа от
прогона (`cmd_run`) нужны только код возврата или исключение, и их отдаёт
подделка команды. Сам прогон исполняется только до решения о бюджете
(`TestRunBudget`): выдача — подделка, база — тестовая и только на чтение,
на «Запускать?» — «нет». Прогон целиком — `tests/test_execute_run.py`.

Вывод сверяется построчно, но без выравнивания: колонки — оформление,
а подписи, числа и порядок строк — то, что читает оператор.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import runpy
import signal
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
from backend.cli.main import (
    _COMMANDS,
    _confirm,
    _print_judge,
    _print_plan,
    _print_report,
    _print_review,
    _read_keywords,
    build_parser,
    cmd_quota,
    main,
)
from backend.config.judge import JudgeMode
from backend.config.startup_checks import ConfigError
from backend.features.ahrefs.client import AhrefsClient, AhrefsError
from backend.features.ahrefs.units import RunEstimate, estimate_run
from backend.features.core.domain import DonorStatus
from backend.features.donors.judging import JudgeSummary
from backend.features.review.candidates import QueueReport
from backend.features.runs.budget import CapExceededError, QuotaUnavailableError
from backend.features.runs.exclusions import ExclusionReason
from backend.features.runs.planning import Candidates, RunPlan
from backend.features.runs.report import RunReport
from backend.features.serp.factory import UnknownProviderError
from backend.features.serp.protocol import SerpResult
from tests.conftest import TEST_DSN

#: Доводы прогона, без которых разбор не пропустит команду.
RUN = ["run", "--keywords", "keys.txt", "--country", "us"]

#: Ответ Ahrefs об остатке. Упирается ключ: у него остаток меньше,
#: чем у рабочего пространства.
QUOTA: dict[str, object] = {
    "units_limit_workspace": 3_210_000,
    "units_usage_workspace": 543_210,
    "units_limit_api_key": 1_234_567,
    "units_usage_api_key": 987_654,
    "usage_reset_date": "2026-09-21T00:00:00Z",
}

#: Смета ровно на тысячу юнитов: расхождение с фактом читается в процентах сразу.
THOUSAND = RunEstimate(domains=10, screen=100, metrics=600, by_country=300)

#: Остаток месячного капа, который видит прогон: своя трата за месяц уже вычтена.
MONTH_LEFT = 7_315


def _lines(out: str) -> list[str]:
    """Непустые строки вывода без выравнивания."""
    return [" ".join(line.split()) for line in out.splitlines() if line.strip()]


def _units(number: int) -> str:
    """Число так, как его читает оператор: тысячи через пробел."""
    return f"{number:_}".replace("_", " ")


def _without_commas(line: str) -> str:
    """Строка «судил …» судьи без запятых. Разделитель тысяч в ней ставится
    заменой запятых на пробел, и замена задевает запятые текста; сверяются
    числа и подписи."""
    return " ".join(line.replace(",", " ").split())


class Ahrefs:
    """Ahrefs на заглушке: настоящий клиент, подставленный ответ.

    Встаёт на место `AhrefsClient()` в команде. Видно, куда клиент ходил
    и закрыт ли он, когда команда кончилась.
    """

    def __init__(self, payload: dict[str, object] | None = None, *, status: int = 200) -> None:
        self.payload = payload
        self.status = status
        self.paths: list[str] = []
        self.opened: list[httpx.AsyncClient] = []

    def __call__(self) -> AhrefsClient:
        def answer(request: httpx.Request) -> httpx.Response:
            self.paths.append(request.url.path)
            return httpx.Response(self.status, json={"limits_and_usage": self.payload})

        http = httpx.AsyncClient(transport=httpx.MockTransport(answer), base_url="https://api.test")
        self.opened.append(http)
        return AhrefsClient(api_key="k", http=http)

    @property
    def closed(self) -> bool:
        return bool(self.opened) and all(http.is_closed for http in self.opened)


class Command:
    """Подделка команды: запоминает доводы, отдаёт код или бросает."""

    def __init__(self, *, returns: int = 0, raises: BaseException | None = None) -> None:
        self.returns = returns
        self.raises = raises
        self.calls: list[argparse.Namespace] = []

    async def __call__(self, args: argparse.Namespace) -> int:
        self.calls.append(args)
        if self.raises is not None:
            raise self.raises
        return self.returns


@pytest.fixture
def logging_setups(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    """Вызовы настройки логов из точки входа — только счёт.

    Настоящая настройка снимает с корневого логгера все хендлеры, и хендлеры
    pytest тоже: после неё `caplog` в соседних тестах ничего не слышит.
    """
    calls: list[bool] = []
    monkeypatch.setattr("backend.cli.main.setup_logging", lambda: calls.append(True))
    return calls


class Serp:
    """Выдача-подделка: три домена на любой ключ — без сети и без денег."""

    name = "fake"

    async def search(
        self, keywords: Sequence[str], country: str, *, depth_pages: int = 1
    ) -> dict[str, list[SerpResult]]:
        urls = ["https://budget-one.com/", "https://budget-two.com/", "https://budget-three.com/"]
        return {key: [SerpResult(i + 1, url) for i, url in enumerate(urls)] for key in keywords}


@dataclass(slots=True)
class BudgetRun:
    """Прогон до решения о бюджете: доводы, Ahrefs и вопросы человеку."""

    argv: list[str]
    ahrefs: Ahrefs
    month_left: int = MONTH_LEFT
    prompts: list[str] = field(default_factory=list)


@pytest.fixture
def budget_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, logging_setups: list[bool]
) -> BudgetRun:
    """Настоящий `cmd_run` через `main` — без сети и без трат.

    Настройки — те, без которых прогон не стартует. База — тестовая, и прогон
    её только читает: на «Запускать?» он слышит «нет». Месячный остаток
    задаёт тест: это вход проверяемого правила, а не его часть.
    """
    keywords = tmp_path / "keys.txt"
    keywords.write_text("best seo tools\n", encoding="utf-8")
    run = BudgetRun(
        argv=["run", "--keywords", str(keywords), "--country", "us"], ahrefs=Ahrefs(QUOTA)
    )
    monkeypatch.setattr("backend.config.storage.DSN", TEST_DSN)
    monkeypatch.setattr("backend.config.storage.REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setattr("backend.config.ahrefs.API_KEY", "k")
    monkeypatch.setattr("backend.config.serp.PROVIDER", "fake")
    monkeypatch.setattr("backend.config.serp.SANDBOX", False)
    monkeypatch.setattr("backend.cli.main.AhrefsClient", run.ahrefs)
    monkeypatch.setattr("backend.cli.main.build_provider", lambda _client: Serp())

    async def month_left(_session: object, *, cap: int) -> int:
        return run.month_left

    def answer(prompt: str) -> str:
        run.prompts.append(prompt)
        return "n"

    monkeypatch.setattr("backend.cli.main.cap_left", month_left)
    monkeypatch.setattr("builtins.input", answer)
    return run


def _plan(
    *,
    new: int,
    fresh: int = 0,
    excluded: dict[str, ExclusionReason] | None = None,
    dropped: int = 0,
    duplicates: int = 0,
    empty_keywords: tuple[str, ...] = (),
    estimate: RunEstimate | None = None,
) -> RunPlan:
    """План прогона по четырём ключам: `new` к проверке, `fresh` уже
    проверенных, `excluded` отсечённых гейтом. Смета — как у `plan_run`,
    если не задана своя."""
    excluded = excluded or {}
    new_hosts = [f"new{i}.test" for i in range(new)]
    fresh_hosts = [f"fresh{i}.test" for i in range(fresh)]
    hosts = [*new_hosts, *fresh_hosts, *excluded]
    candidates = Candidates(
        hosts=hosts,
        keywords=4,
        results=len(hosts) + dropped + duplicates,
        empty_keywords=list(empty_keywords),
        dropped=dropped,
    )
    return RunPlan(
        candidates=candidates,
        fresh=fresh_hosts,
        new=new_hosts,
        estimate=estimate or estimate_run(new),
        excluded=excluded,
    )


def _judge() -> JudgeSummary:
    """Судья, у которого есть что сказать по каждой строке отчёта."""
    return JudgeSummary(
        judged=40,
        from_cache=5,
        would_cut=7,
        to_review=3,
        tokens=12_345,
        # `keyword` — слоя с таким кодом нет: так выглядит новый слой судьи,
        # у которого ещё нет слова для оператора.
        by_decider={"rule": 2, "model": 9, "arbiter": 1, "keyword": 4},
        would_cut_paid=40,
        home_unreached=1,
        from_index=2,
    )


class TestKeywordsFile:
    def test_one_keyword_per_line_without_blanks_and_edges(self, tmp_path: Path) -> None:
        path = tmp_path / "keys.txt"
        path.write_text(
            "  best seo tools \n\n\tкупить ссылки\r\n   \nwrite for us", encoding="utf-8"
        )

        assert _read_keywords(path) == ["best seo tools", "купить ссылки", "write for us"]

    def test_missing_file_is_a_settings_error_naming_the_path(self, tmp_path: Path) -> None:
        missing = tmp_path / "нет-такого.txt"

        with pytest.raises(ConfigError, match="не найден") as refused:
            _read_keywords(missing)

        assert str(missing) in str(refused.value)

    def test_file_of_blank_lines_is_a_settings_error(self, tmp_path: Path) -> None:
        """Пустой список — не прогон по нулю ключей, а отказ до первой траты."""
        path = tmp_path / "keys.txt"
        path.write_text("\n   \n\t\n", encoding="utf-8")

        with pytest.raises(ConfigError, match="нет ни одного ключевого слова") as refused:
            _read_keywords(path)

        assert str(path) in str(refused.value)


class TestQuota:
    async def test_shows_the_smaller_remainder_and_both_limits(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Запрос один и бесплатный; клиент после него закрыт."""
        ahrefs = Ahrefs(QUOTA)
        monkeypatch.setattr("backend.cli.main.AhrefsClient", ahrefs)

        assert await cmd_quota() == 0

        assert _lines(capsys.readouterr().out) == [
            "Доступно юнитов: 246 913",
            "ключ: 987 654 из 1 234 567",
            "пространство: 543 210 из 3 210 000",
            "обнуление: 2026-09-21T00:00:00Z",
            "Лимита два и действуют одновременно — доступен меньший остаток.",
        ]
        assert ahrefs.paths == ["/v3/subscription-info/limits-and-usage"]
        assert ahrefs.closed

    async def test_no_reset_line_when_the_provider_did_not_name_a_date(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        payload = {key: value for key, value in QUOTA.items() if key != "usage_reset_date"}
        monkeypatch.setattr("backend.cli.main.AhrefsClient", Ahrefs(payload))

        assert await cmd_quota() == 0

        lines = _lines(capsys.readouterr().out)
        assert lines[0] == "Доступно юнитов: 246 913"
        assert [line for line in lines if line.startswith("обнуление")] == []

    def test_provider_failure_is_exit_5_and_the_client_is_closed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        logging_setups: list[bool],
    ) -> None:
        ahrefs = Ahrefs(status=503)
        monkeypatch.setattr("backend.cli.main.AhrefsClient", ahrefs)

        assert main(["quota"]) == 5

        printed = capsys.readouterr()
        assert printed.out == ""
        assert printed.err.startswith("Провайдер не ответил: Остаток квоты недоступен")
        assert ahrefs.closed


class TestPlan:
    def test_everything_weighed_before_paying(self, capsys: pytest.CaptureFixture[str]) -> None:
        # Порядок вставки нарочно не тот, что в отчёте: причины идут по алфавиту.
        excluded = {
            "agency1.test": ExclusionReason.SUPPLIER,
            "spam1.test": ExclusionReason.STOPLIST,
            "quiet.test": ExclusionReason.SILENT,
            "agency2.test": ExclusionReason.SUPPLIER,
            "spam2.test": ExclusionReason.STOPLIST,
            "agency3.test": ExclusionReason.SUPPLIER,
        }
        plan = _plan(
            new=150,
            fresh=50,
            excluded=excluded,
            dropped=2,
            duplicates=7,
            empty_keywords=("пустой ключ",),
        )
        estimate = plan.estimate
        # Сценарий обязан доходить до тысяч и до обеих строк экономии —
        # иначе тест не видит ни разделителя, ни этих строк.
        assert estimate.total >= 1_000
        assert plan.savings_from_cache > 0
        assert plan.savings_from_gate > 0

        _print_plan(plan, 1_234_567)

        assert _lines(capsys.readouterr().out) == [
            "Ключей: 4",
            "Результатов выдачи: 215",
            "Уникальных доменов: 206",
            "схлопнуто дублей: 7",
            "не разобрано: 2",
            "ключей без выдачи: 1",
            "Исключены: 6 (в прогон не идут)",
            "в стоп-листе: 2",
            "писали, не ответил: 1",
            "поставщик агентства: 3",
            "Уже проверены: 50 (платить не нужно)",
            "Проверить сейчас: 150",
            f"Смета: {_units(estimate.total)} юнитов",
            f"просев по DR: {_units(estimate.screen)}",
            f"метрики: {_units(estimate.metrics)}",
            f"страны: {_units(estimate.by_country)}",
            "Доступно: 1 234 567 юнитов",
            f"Сэкономлено кэшем: {_units(plan.savings_from_cache)} юнитов",
            f"Сэкономлено гейтом: {_units(plan.savings_from_gate)} юнитов",
        ]

    def test_nothing_to_say_means_no_line(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Нет отсечённых, неразобранных, пустых ключей и экономии — нет и строк о них."""
        plan = _plan(new=3)
        estimate = plan.estimate

        _print_plan(plan, 500)

        assert _lines(capsys.readouterr().out) == [
            "Ключей: 4",
            "Результатов выдачи: 3",
            "Уникальных доменов: 3",
            "схлопнуто дублей: 0",
            "Уже проверены: 0 (платить не нужно)",
            "Проверить сейчас: 3",
            f"Смета: {_units(estimate.total)} юнитов",
            f"просев по DR: {_units(estimate.screen)}",
            f"метрики: {_units(estimate.metrics)}",
            f"страны: {_units(estimate.by_country)}",
            "Доступно: 500 юнитов",
        ]


class TestReport:
    def test_outcome_reasons_and_spending(self, capsys: pytest.CaptureFixture[str]) -> None:
        # Вставлено вразнобой: итог — по коду статуса, причины и траты — по убыванию.
        report = RunReport(
            plan=_plan(new=10, estimate=THOUSAND),
            by_status={
                DonorStatus.UNSUITABLE: 6,
                DonorStatus.SUITABLE: 3,
                DonorStatus.UNCHECKED: 1,
            },
            reject_reasons={"traffic": 2, "dr": 4},
            spent_units=1_450,
            spent_by_operation={"serp": 150, "by_country": 400, "batch_metrics": 900},
            free_by_operation={"by_country": 1, "batch_metrics": 3},
        )

        _print_report(report)

        lines = _lines(capsys.readouterr().out)
        assert lines[:-1] == [
            "── Итог ──",
            "suitable 3",
            "unchecked 1",
            "unsuitable 6",
            "Причины отказа:",
            "dr 4",
            "traffic 2",
            "Потрачено: 1 450 юнитов",
            "batch_metrics 900",
            "by_country 400",
            "serp 150",
            "Бесплатно из кэша Ahrefs: 4 запросов",
            "batch_metrics 3",
            "by_country 1",
            # Выдача в смету не входит: 900 + 400 против 1000 — это +30%.
            "Смета разошлась с фактом на +30%.",
        ]
        assert lines[-1].startswith("Трата выше сметы")

    def test_empty_run_is_the_outcome_and_zero_spent(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _print_report(RunReport(plan=_plan(new=0)))

        assert _lines(capsys.readouterr().out) == ["── Итог ──", "Потрачено: 0 юнитов"]

    @pytest.mark.parametrize(
        ("spent", "verdict"),
        [
            (1_300, ["Смета разошлась с фактом на +30%.", "Трата выше сметы"]),
            (1_201, ["Смета разошлась с фактом на +20%.", "Трата выше сметы"]),
            (1_200, []),
            (1_000, []),
            (800, []),
            (700, ["Смета разошлась с фактом на -30%."]),
        ],
    )
    def test_estimate_error_is_named_only_past_a_fifth(
        self, capsys: pytest.CaptureFixture[str], spent: int, verdict: list[str]
    ) -> None:
        """До 20% в любую сторону — шум воронки, о нём ни слова. Объяснение
        перерасхода — только при перерасходе, не при экономии."""
        report = RunReport(
            plan=_plan(new=10, estimate=THOUSAND),
            spent_units=spent,
            spent_by_operation={"batch_metrics": spent},
        )

        _print_report(report)

        lines = _lines(capsys.readouterr().out)
        tail = lines[lines.index(f"batch_metrics {_units(spent)}") + 1 :]
        assert len(tail) == len(verdict), tail
        for line, expected in zip(tail, verdict, strict=True):
            assert line.startswith(expected)

    def test_judge_and_queue_come_after_spending(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr("backend.config.judge.MODE", JudgeMode.SHADOW)
        report = RunReport(
            plan=_plan(new=0), judge=JudgeSummary(judged=2), review=QueueReport(pending=1)
        )

        _print_report(report)

        lines = _lines(capsys.readouterr().out)
        spent = lines.index("Потрачено: 0 юнитов")
        judge = lines.index("Судья площадки (наблюдение, не режет):")
        queue = lines.index("На рассмотрение: 1")
        assert spent < judge < queue


class TestJudge:
    def test_shadow_mode_says_what_it_would_cut(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr("backend.config.judge.MODE", JudgeMode.SHADOW)
        judge = _judge()
        assert judge.units_saved >= 1_000  # иначе разделитель тысяч не виден

        _print_judge(RunReport(plan=_plan(new=0), judge=judge))

        lines = _lines(capsys.readouterr().out)
        assert _without_commas(lines.pop(1)) == "судил 40 из кэша 5 токенов 12 345"
        assert lines == [
            "Судья площадки (наблюдение, не режет):",
            "отрезал бы 7, к человеку 3",
            # Кто решил — словами оператора и по убыванию; незнакомый код — как есть.
            "решено моделью по выдаче 9",
            "решено keyword 4",
            "решено правилом 2",
            "решено арбитром 1",
            "главная закрыта, судил по индексу поиска: 2",
            "главная не открылась и в индексе нет: 1 — решала выдача",
            f"юнитов сэкономил бы: {_units(judge.units_saved)} (нижняя граница)",
        ]

    def test_enforce_mode_says_it_cuts(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr("backend.config.judge.MODE", JudgeMode.ENFORCE)

        _print_judge(RunReport(plan=_plan(new=0), judge=_judge()))

        lines = _lines(capsys.readouterr().out)
        assert lines[0] == "Судья площадки (режет):"
        assert lines[2] == "отрезал 7, к человеку 3"

    def test_zero_counters_add_no_lines(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr("backend.config.judge.MODE", JudgeMode.SHADOW)

        _print_judge(RunReport(plan=_plan(new=0), judge=JudgeSummary(judged=3)))

        lines = _lines(capsys.readouterr().out)
        assert _without_commas(lines.pop(1)) == "судил 3 из кэша 0 токенов 0"
        assert lines == ["Судья площадки (наблюдение, не режет):", "отрезал бы 0, к человеку 0"]

    def test_switched_off_judge_is_silence_not_zeros(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Ноль читался бы как «судил и никого не нашёл», а это другая новость."""
        _print_judge(RunReport(plan=_plan(new=0)))

        assert capsys.readouterr().out == ""


class TestReview:
    def test_pending_carried_and_where_to_decide(self, capsys: pytest.CaptureFixture[str]) -> None:
        review = QueueReport(pending=4, carried={"accepted": 2, "rejected": 1})

        _print_review(RunReport(plan=_plan(new=0), review=review))

        assert _lines(capsys.readouterr().out) == [
            "На рассмотрение: 4",
            "решено раньше (accepted): 2",
            "решено раньше (rejected): 1",
            "Решить — экран прогона: контакты и письма получат только принятые.",
        ]

    def test_no_queue_no_lines(self, capsys: pytest.CaptureFixture[str]) -> None:
        _print_review(RunReport(plan=_plan(new=0)))

        assert capsys.readouterr().out == ""


class TestConfirm:
    @pytest.mark.parametrize("answer", ["y", "Y", "yes", "  YES ", "д", "Да"])
    def test_yes_in_either_language_starts_the_run(
        self, monkeypatch: pytest.MonkeyPatch, answer: str
    ) -> None:
        prompts: list[str] = []

        def typed(prompt: str) -> str:
            prompts.append(prompt)
            return answer

        monkeypatch.setattr("builtins.input", typed)

        assert _confirm() is True
        assert "[y/N]" in prompts[0]

    @pytest.mark.parametrize("answer", ["", "n", "no", "нет", "yes please", "ok"])
    def test_anything_else_and_bare_enter_is_no(
        self, monkeypatch: pytest.MonkeyPatch, answer: str
    ) -> None:
        monkeypatch.setattr("builtins.input", lambda _prompt: answer)

        assert _confirm() is False

    def test_no_terminal_is_no_and_names_the_flag(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Запуск без терминала и без `--yes` не тратит молча."""

        def no_terminal(_prompt: str) -> str:
            raise EOFError

        monkeypatch.setattr("builtins.input", no_terminal)

        assert _confirm() is False
        assert "--yes" in capsys.readouterr().out


class TestParser:
    def test_run_asks_before_spending_by_default(self) -> None:
        args = build_parser().parse_args(RUN)

        assert (args.command, args.keywords, args.country) == ("run", "keys.txt", "us")
        assert (args.depth, args.cap, args.yes) == (1, None, False)

    def test_run_numbers_arrive_as_numbers(self) -> None:
        args = build_parser().parse_args([*RUN, "--depth", "3", "--cap", "5000", "--yes"])

        assert (args.depth, args.cap, args.yes) == (3, 5000, True)

    def test_new_account_is_an_operator_unless_said_otherwise(self) -> None:
        """Админ заводит учётки: эту роль дают словами, а не умолчанием."""
        args = build_parser().parse_args(["user-add", "--email", "a@b.test"])

        assert args.role == "operator"

    @pytest.mark.parametrize(
        "argv",
        [
            ["run", "--country", "us"],
            ["run", "--keywords", "keys.txt"],
            [*RUN, "--depth", "две"],
            [*RUN, "--cap", "много"],
            ["user-add", "--email", "a@b.test", "--role", "root"],
            [],
        ],
        ids=["no-keywords", "no-country", "depth-text", "cap-text", "unknown-role", "no-command"],
    )
    def test_malformed_command_line_is_a_usage_error(
        self, capsys: pytest.CaptureFixture[str], argv: list[str]
    ) -> None:
        with pytest.raises(SystemExit) as exited:
            build_parser().parse_args(argv)

        assert exited.value.code == 2
        assert "error:" in capsys.readouterr().err

    def test_every_command_but_run_has_its_line_in_the_table(self) -> None:
        """Команды без строки в таблице `main` отдаёт прогону (`cmd_run`) —
        туда должна уходить только сама `run`."""
        (sub,) = [
            action
            for action in build_parser()._actions
            if isinstance(action, argparse._SubParsersAction)
        ]

        assert set(sub.choices) - set(_COMMANDS) == {"run"}
        assert set(_COMMANDS) <= set(sub.choices)

    @pytest.mark.parametrize("name", sorted(set(_COMMANDS) - {"quota"}))
    def test_command_runs_the_handler_of_its_own_name(self, name: str) -> None:
        """`letters` и `letters-send` различаются одним словом, а второе
        отправляет письма: перепутанная строка таблицы — необратимое действие
        по чужой команде. `quota` проверяется вызовом через `main`."""
        assert _COMMANDS[name].__name__ == f"cmd_{name.replace('-', '_')}"


class TestEntryPoint:
    def test_quota_goes_through_the_table(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        logging_setups: list[bool],
    ) -> None:
        monkeypatch.setattr("backend.cli.main.AhrefsClient", Ahrefs(QUOTA))

        assert main(["quota"]) == 0

        assert _lines(capsys.readouterr().out)[0] == "Доступно юнитов: 246 913"
        assert logging_setups == [True]

    def test_run_gets_its_arguments_and_its_code_reaches_the_caller(
        self, monkeypatch: pytest.MonkeyPatch, logging_setups: list[bool]
    ) -> None:
        """Прогон — единственная команда вне таблицы. Код, который он вернул
        (здесь «отменено»), — код выхода всей команды."""
        command = Command(returns=6)
        monkeypatch.setattr("backend.cli.main.cmd_run", command)

        assert main([*RUN, "--depth", "2", "--yes"]) == 6

        (args,) = command.calls
        assert (args.keywords, args.country, args.depth, args.yes) == ("keys.txt", "us", 2, True)

    # Коды — договор с вызывающими скриптами, поэтому числами, а не именами
    # констант: перенумерация должна краснеть здесь, а не в чужом скрипте.
    @pytest.mark.parametrize(
        ("failure", "code", "prefix"),
        [
            (ConfigError("не задан STORAGE_DSN"), 2, "Не хватает настроек"),
            (UnknownProviderError("нет источника «zzz»"), 2, "Источник выдачи не выбран"),
            (CapExceededError("обойдётся в 900, доступно 100"), 3, "Прогон не запущен"),
            (QuotaUnavailableError("остаток неизвестен"), 4, "Прогон не запущен"),
            (AhrefsError("503 от провайдера"), 5, "Провайдер не ответил"),
        ],
        ids=["config", "provider-choice", "cap", "quota", "ahrefs"],
    )
    def test_each_refusal_has_its_code_and_says_why_on_stderr(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        logging_setups: list[bool],
        failure: Exception,
        code: int,
        prefix: str,
    ) -> None:
        """Код — для скрипта, строка — для человека; ветвиться по тексту не нужно."""
        monkeypatch.setattr("backend.cli.main.cmd_run", Command(raises=failure))

        assert main(RUN) == code

        printed = capsys.readouterr()
        assert printed.err == f"{prefix}: {failure}\n"
        assert printed.out == ""

    def test_unknown_failure_keeps_its_traceback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        logging_setups: list[bool],
    ) -> None:
        """Чужая ошибка не подменяется строкой: трассировка — единственная подсказка."""
        monkeypatch.setattr("backend.cli.main.cmd_run", Command(raises=RuntimeError("неожиданное")))

        with pytest.raises(RuntimeError, match="неожиданное"):
            main(RUN)

        assert capsys.readouterr().err == ""

    def test_ctrl_c_is_exit_6_after_the_command_cleaned_up(
        self, monkeypatch: pytest.MonkeyPatch, logging_setups: list[bool]
    ) -> None:
        """Через настоящий сигнал: отмену доставляет сам `asyncio.run`.

        Текст о прерывании не проверяется намеренно — его меняют;
        код выхода — договор.
        """
        steps: list[str] = []

        async def interrupted(_args: argparse.Namespace) -> int:
            try:
                os.kill(os.getpid(), signal.SIGINT)
                await asyncio.sleep(5)  # отмена приходит на этом ожидании
                steps.append("дошла до конца")
            finally:
                steps.append("убрала за собой")
            return 0

        monkeypatch.setattr("backend.cli.main.cmd_run", interrupted)

        try:
            code = main(RUN)
        except KeyboardInterrupt as exc:
            # Без перехвата прерывание ушло бы в pytest и оборвало весь прогон.
            raise AssertionError("Ctrl-C вышел из точки входа необработанным") from exc

        assert code == 6
        assert steps == ["убрала за собой"]

    def test_module_run_as_a_script_hands_the_code_to_the_shell(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`python -m backend.cli.main` отдаёт оболочке код команды, а не ноль."""
        monkeypatch.setattr(sys, "argv", ["outreach", "quota"])
        monkeypatch.setattr("backend.features.ahrefs.client.AhrefsClient", Ahrefs(status=503))
        monkeypatch.setattr("backend.shared.logs.setup_logging", lambda: None)
        # Модуль исполняется заново, как при `-m`. Уже загруженный, он дал бы
        # предупреждение runpy о повторном исполнении.
        monkeypatch.delitem(sys.modules, "backend.cli.main")

        with pytest.raises(SystemExit) as exited:
            runpy.run_module("backend.cli.main", run_name="__main__")

        assert exited.value.code == 5
        assert capsys.readouterr().err.startswith("Провайдер не ответил")


class TestRunBudget:
    """`--cap` — свой потолок прогона: он только уменьшает месячный остаток,
    и ноль значит ноль."""

    @pytest.mark.parametrize(
        ("cap", "budget"),
        [([], MONTH_LEFT), (["--cap", "5321"], 5_321), (["--cap", "9876"], MONTH_LEFT)],
        ids=["no-cap", "cap-below-month", "cap-above-month"],
    )
    def test_budget_is_the_smaller_of_cap_and_month(
        self,
        budget_run: BudgetRun,
        capsys: pytest.CaptureFixture[str],
        cap: list[str],
        budget: int,
    ) -> None:
        # Смету показали и спросили; «нет» — код «отменено».
        assert main([*budget_run.argv, *cap]) == 6

        assert f"Доступно: {_units(budget)} юнитов" in _lines(capsys.readouterr().out)
        assert len(budget_run.prompts) == 1

    @pytest.mark.parametrize(
        ("cap", "month_left", "available"),
        [(["--cap", "0"], MONTH_LEFT, 0), (["--cap", "1"], MONTH_LEFT, 1), ([], 0, 0)],
        ids=["cap-zero", "cap-one", "month-used-up"],
    )
    def test_budget_below_the_estimate_refuses_before_asking(
        self,
        budget_run: BudgetRun,
        capsys: pytest.CaptureFixture[str],
        cap: list[str],
        month_left: int,
        available: int,
    ) -> None:
        """`--cap 0` — ноль, а не «без потолка»: прогон не запускается, как при
        `--cap 1` и как при кончившемся месячном остатке. Ни вопроса человеку,
        ни платного запроса — только бесплатный остаток квоты."""
        budget_run.month_left = month_left

        assert main([*budget_run.argv, *cap]) == 3

        printed = capsys.readouterr()
        assert printed.err.startswith("Прогон не запущен: ")
        assert f"доступно {available}." in printed.err
        assert budget_run.prompts == []
        assert budget_run.ahrefs.paths == ["/v3/subscription-info/limits-and-usage"]
