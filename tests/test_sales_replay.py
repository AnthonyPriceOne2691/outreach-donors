"""Прогон версии агента продаж на накопленных ответах — срез 3.6.

Модель ситуации и судьи — подставной HTTP (`httpx.MockTransport`), писатель — подставной:
пишет по строкам брифа (язык письма, призыв и ссылка хода). Живая модель не зовётся.
Проверяется то, чего не видно по зелёному прогону:

- новая версия молчит там, где человек ответил, — ворота закрыты с разбором по ситуациям,
  и в форме «две версии промптов», и в форме «два прогона из файлов» (A1);
- набора нет по пути — отказ «набор не найден», а не «0 расхождений»; модель не зовётся (A2);
- ворота не закрыты всегда: та же версия проходит; черновики с нарушениями закрывают;
- живые продажи читаются из черновиков на настоящей базе дерева — с решением человека и
  перепиской без нашего ответа на письмо;
- прогон идёт тем же путём, что агент шва: тот же черновик и те же попытки судьи.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from backend.config import llm as llm_cfg
from backend.config import sales as sales_cfg
from backend.features.agent import drafting
from backend.features.agent.settings import AgentSettingsRepository
from backend.features.agent.writer import Request, Turn, Written
from backend.features.core.domain import DraftStatus, MessageStatus, ReplyKind, Stage
from backend.features.core.models.agent import AgentDraftModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.letters.chain import ANSWER_STEP
from backend.features.sales.agent import (
    calling,
    facts,
    moves,
    parts,
    replay,
    replay_drafts,
    replay_gate,
    replay_sets,
    situation,
)
from backend.features.sales.agent.facts import Context
from backend.features.sales.agent.replay import Case, Decision, Human, Outcome, Result, Run
from scripts import sales_replay
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_agent_brief import CALL, OFFER, world
from tests.test_sales_agent_stage import sales_on
from tests.test_sales_model import ROOT
from tests.test_sales_stage_mail import sales_world

__all__ = ["sales_on"]  # фикстура строки продаж в реестре — отсюда её видит pytest

TOKENS = 13
WRITER_TOKENS = 7
ALLOW = {"claims": [], "promises": [], "tone": {"ok": True, "problem": ""}}
#: Пометка в промпте ситуации новой версии: подставная модель отвечает по её таблице.
NEW = "Пометка новой версии для проверки прогона."
#: Метка по письму собеседника — первая подходящая строка, порядок важен.
LABELS = {
    "Сколько стоит": "asks_price",
    "tell me more": "asks_info",
    "созвонимся": "wants_to_talk",
    "примеры работ": "asks_info",
    "Спасибо, получил": "ack",
    "out of the office": "autoresponder",
    "Not now": "not_now",
}
#: Новая версия хуже: вопрос после «спасибо» принимает за «спасибо» и молчит.
WORSE = {**LABELS, "примеры работ": "ack"}


def _last_letter(user: str) -> str:
    block = user.split("<<<CONVERSATION\n", 1)[1].rsplit("\nCONVERSATION>>>", 1)[0]
    return next(item["text"] for item in reversed(json.loads(block)) if item["from"] == "them")


class Model:
    """Модель ситуации и судьи на месте сети.

    Ситуация — по письму собеседника, первой подходящей строкой таблицы; пометка новой
    версии в промпте ситуации — таблица новой версии. Судья пропускает всё. `status` не
    200 — отказ провайдера.
    """

    def __init__(
        self, labels: dict[str, str], new: dict[str, str] | None = None, status: int = 200
    ) -> None:
        self.labels, self.new, self.status = labels, new or labels, status
        self.situations: list[str] = []
        self.judges: list[str] = []
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "nope"}})
        system, user = (item["content"] for item in json.loads(request.content)["messages"])
        answer: dict[str, Any] = ALLOW
        if user.startswith("records:"):
            self.judges.append(system)
        else:
            self.situations.append(system)
            table = self.new if NEW in system else self.labels
            letter = _last_letter(user)
            label = next((value for key, value in table.items() if key in letter), "asks_info")
            answer = {"situation": label, "confidence": 0.9}
        content = json.dumps(answer, ensure_ascii=False)
        body = {"choices": [{"message": {"content": content}}], "usage": {"total_tokens": TOKENS}}
        return httpx.Response(200, json=body)


Plug = Callable[..., Model]


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> Plug:
    def plugged(
        labels: dict[str, str] = LABELS, new: dict[str, str] | None = None, status: int = 200
    ) -> Model:
        found = Model(labels, new, status)
        monkeypatch.setattr(
            calling, "client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(found))
        )
        monkeypatch.setattr(llm_cfg, "API_KEY", "test-key")
        monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", 0)
        monkeypatch.setattr(llm_cfg, "RUN_TOKEN_CAP", 0)
        return found

    return plugged


RU = {
    "hello": "Добрый день.",
    "body": "Спасибо за ответ, мы делаем аудит сайтов.",
    "close": "Спасибо, что ответили. Хорошей недели.",
    "call": "Удобнее обсудить на коротком созвоне: {link}.",
    "telegram": "Напишите нам в Telegram: {link}.",
    "price": "Аудит стоит 500 долларов.",
}
EN = {
    "hello": "Hello,",
    "body": "Thank you for the reply. We audit websites.",
    "close": "Thank you for letting us know. Have a good week.",
    "call": "It is easier to discuss the details on a short call: {link}.",
    "telegram": "Message us on Telegram: {link}.",
    "price": "The audit costs 500 dollars.",
}


def letter(context: Context, *, sign: str, priced: bool = False) -> str:
    """Черновик по брифу: на языке письма; с призывом и ссылкой хода — или без призыва;
    подпись — именем из запроса, как велит промпт."""
    words = RU if context.language == "ru" else EN
    if context.cta is None:
        lines = [words["hello"], words["close"]]
    else:
        kind, link = context.cta
        lines = [words["hello"], words["body"], words[kind.value].format(link=link)]
    if priced:
        lines.insert(2, words["price"])
    return "\n".join([*lines, sign])


class Writer:
    """Писатель на месте модели: пишет по строкам брифа. `priced` — первый черновик с
    суммой не из базы: судья вернёт его на правку, второй — без неё."""

    def __init__(self, *, priced: bool = False) -> None:
        self.priced = priced
        self.seen: list[Request] = []

    async def write(self, request: Request) -> Written:
        self.seen.append(request)
        priced = self.priced and not request.corrections
        body = letter(facts.read(request.facts), sign=request.sign_as, priced=priced)
        return Written(body=body, needs_human=False, reason=None, tokens=WRITER_TOKENS)


def run_args(*extra: str) -> argparse.Namespace:
    return sales_replay.parser().parse_args(["run", *extra])


async def replayed(session: AsyncSession, writer: Writer, *extra: str) -> int:
    """Команда `run` на выдуманном наборе репозитория — с сессией базы дерева."""
    cases, source = sales_replay.load_set("synthetic", None)
    args = run_args("--set", "synthetic", *extra)
    return await sales_replay.execute(session, writer, args, cases, source)


def version_dir(folder: Path, *, marked: bool) -> Path:
    """Каталог версии: промпт ситуации пакета, у новой — с пометкой в конце."""
    folder.mkdir()
    text = situation.PROMPT.read_text(encoding="utf-8")
    (folder / "situation.md").write_text(f"{text}\n\n{NEW}\n" if marked else text, "utf-8")
    return folder


# --- A1: новая версия хуже по ложному молчанию -----------------------------------------------


async def test_a1_two_prompt_versions_new_silent_where_the_human_answered_closes_the_gate(
    session: AsyncSession, model: Plug, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:  # A1
    await world(session)
    found = model(LABELS, new=WORSE)
    old, new = (
        version_dir(tmp_path / "old", marked=False),
        version_dir(tmp_path / "new", marked=True),
    )

    code = await replayed(session, Writer(), "--prompts", str(new), "--against", str(old))

    out = capsys.readouterr().out
    assert code == 1
    assert "ВОРОТА ЗАКРЫТЫ: ложного молчания больше: было 0, стало 1" in out
    assert "  asks_info          2 · 0 → 1  ← хуже · 0 → 0" in out
    assert "syn-thanks-cases-ru (asks_info): версия молчит («ack»), человек — ответил сам" in out
    assert sum(NEW in system for system in found.situations) == 7  # новая версия дошла до модели
    assert sum(NEW not in system for system in found.situations) == 7


async def test_a1_two_run_files_new_silent_where_the_human_answered_closes_the_gate(
    session: AsyncSession, model: Plug, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:  # A1
    await world(session)
    before, after = tmp_path / "old.json", tmp_path / "new.json"
    model(LABELS)
    assert await replayed(session, Writer(), "--out", str(before)) == 0
    model(WORSE)

    code = await replayed(session, Writer(), "--against", str(before), "--out", str(after))

    assert code == 1
    assert "ложного молчания больше: было 0, стало 1" in capsys.readouterr().out
    # Та же пара — командой compare из файлов, без базы и модели.
    assert sales_replay.main(["compare", str(before), str(after)]) == 1
    out = capsys.readouterr().out
    assert "  asks_info          2 · 0 → 1  ← хуже · 0 → 0" in out
    assert "ВОРОТА ЗАКРЫТЫ: ложного молчания больше: было 0, стало 1" in out


async def test_same_version_twice_opens_the_gate_and_counts_against_the_humans(
    session: AsyncSession, model: Plug, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    await world(session)
    model(LABELS)

    code = await replayed(
        session, Writer(), "--against", str(version_dir(tmp_path / "v", marked=False))
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "ВОРОТА ОТКРЫТЫ: новая версия не хуже прежней" in out
    assert "Ложное молчание (версия молчит, человек ответил): 0 из 4 ответивших" in out
    assert "Верное молчание: 2 из 3 молчавших; ответ там, где человек молчал: 1" in out
    assert "Метка ситуации с меткой человека: совпала 7 из 7" in out
    assert "Общих случаев: 7 (только в прежнем 0, только в новом 0" in out


async def test_pilot_runs_only_the_first_cases_and_says_so(
    session: AsyncSession, model: Plug, capsys: pytest.CaptureFixture[str]
) -> None:
    await world(session)
    found = model(LABELS)

    assert await replayed(session, Writer(), "--limit", "2") == 0

    out = capsys.readouterr().out
    assert "Пилот: первые 2 из 7 случаев — ворота судят только их" in out
    assert "; случаев 2" in out
    assert len(found.situations) == 2


def test_pilot_of_no_cases_is_refused() -> None:
    with pytest.raises(SystemExit):
        run_args("--limit", "0")


async def test_drafts_with_violations_close_the_gate(
    session: AsyncSession, model: Plug, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    await world(session)
    model(LABELS)
    before = tmp_path / "old.json"
    assert await replayed(session, Writer(), "--out", str(before)) == 0

    code = await replayed(session, Writer(priced=True), "--against", str(before))

    out = capsys.readouterr().out
    assert code == 1
    assert "ВОРОТА ЗАКРЫТЫ: черновиков с нарушениями больше: было 0, стало 5" in out
    assert "Версия по судье и правилам: прошло бы как есть 0 (0%) · правка 5 (71%)" in out
    assert "syn-price-ru (asks_price): нарушения первого черновика — сумма 500" in out


# --- A2: набора нет ------------------------------------------------------------------------


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Команда не должна дойти до базы и модели: отказ — раньше них."""

    async def reached(*_: object) -> int:
        raise AssertionError("команда дошла до базы и модели, а должна была отказать раньше")

    monkeypatch.setattr(sales_replay, "_live", reached)


@pytest.mark.usefixtures("offline")
def test_a2_no_set_directory_is_refused_in_words_without_the_model(
    monkeypatch: pytest.MonkeyPatch, model: Plug, capsys: pytest.CaptureFixture[str]
) -> None:  # A2
    found = model()
    monkeypatch.setattr(sales_cfg, "REPLAY_DIR", "")

    assert sales_replay.main(["run", "--set", "crm"]) == 1

    assert capsys.readouterr().out.startswith(
        "НАБОР НЕ НАЙДЕН: набор «crm» не найден: каталог не задан — SALES_REPLAY_DIR или --dir"
    )
    assert found.calls == 0


@pytest.mark.usefixtures("offline")
def test_a2_no_set_by_the_path_is_refused_not_zero_discrepancies(
    monkeypatch: pytest.MonkeyPatch, model: Plug, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:  # A2
    found = model()
    monkeypatch.setattr(sales_cfg, "REPLAY_DIR", str(tmp_path / "nowhere"))

    assert sales_replay.main(["run", "--set", "crm"]) == 1

    out = capsys.readouterr().out
    assert f"набор «crm» не найден: нет {tmp_path / 'nowhere' / 'crm' / 'cases.jsonl'}" in out
    assert "расхожден" not in out
    assert found.calls == 0


@pytest.mark.usefixtures("offline")
def test_a2_empty_set_is_refused_too(
    model: Plug, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:  # A2
    found = model()
    (tmp_path / "crm").mkdir()
    (tmp_path / "crm" / "cases.jsonl").write_text("\n", "utf-8")

    assert sales_replay.main(["run", "--set", "crm", "--dir", str(tmp_path)]) == 1

    assert "набор «crm» пуст, а нужно не меньше 100" in capsys.readouterr().out
    assert found.calls == 0


@pytest.mark.usefixtures("offline")
def test_missing_run_file_is_refused(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    assert sales_replay.main(["compare", str(tmp_path / "a.json"), str(tmp_path / "b.json")]) == 1
    assert f"прогон не найден: {tmp_path / 'a.json'}" in capsys.readouterr().out


def test_the_command_starts_in_a_clean_process() -> None:
    """Команда, модули прогона, шов и агент продаж импортируются первыми без круга."""
    done = subprocess.run(
        [sys.executable, "scripts/sales_replay.py", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert "{run,compare}" in done.stdout


# --- набор и манифест --------------------------------------------------------------------------

SPEC = replay_sets.SetSpec("t", ("cases.jsonl",), 1)
GOOD = {
    "id": "c1",
    "turns": [{"from": "us", "text": "Добрый день."}, {"from": "them", "text": "Сколько стоит?"}],
    "human": {"decision": "sent_edited", "situation": "asks_price"},
}


def _set(tmp_path: Path, *lines: object, spec: replay_sets.SetSpec = SPEC) -> list[Case]:
    path = tmp_path / "cases.jsonl"
    text = "\n".join(line if isinstance(line, str) else json.dumps(line) for line in lines)
    path.write_text(text, "utf-8")
    return replay_sets.read_set(spec, replay_sets.located(spec, tmp_path))


def test_a_set_line_becomes_a_case_with_the_human_decision(tmp_path: Path) -> None:
    [case] = _set(tmp_path, GOOD)

    assert case.turns == (
        Turn(ours=True, text="Добрый день."),
        Turn(ours=False, text="Сколько стоит?"),
    )
    assert case.human == Human(Decision.SENT_EDITED, True, "asks_price", "human", None)


@pytest.mark.parametrize(
    ("line", "said"),
    [
        ("{oops", "cases.jsonl:1: строка не JSON"),
        (
            {**GOOD, "turns": [{"from": "them", "text": "?"}, {"from": "us", "text": "!"}]},
            "последним должно быть письмо собеседника",
        ),
        ({**GOOD, "human": {"decision": "maybe"}}, "решение «maybe» незнакомо"),
        ({**GOOD, "human": {"decision": "silent", "replied": True}}, "не сходится с «silent»"),
        (
            {**GOOD, "human": {"decision": "own", "situation": "lunch"}},
            "метки ситуации «lunch» нет",
        ),
        ({**GOOD, "id": " "}, "у случая нет id"),
        (
            {**GOOD, "human": {"decision": "own", "situation": ["ack"]}},
            "метки ситуации «\\['ack'\\]» нет",
        ),
    ],
)
def test_a_bad_set_line_is_refused_with_the_place(tmp_path: Path, line: object, said: str) -> None:
    with pytest.raises(replay.SetError, match=said):
        _set(tmp_path, line)


def test_repeated_case_numbers_and_a_small_set_are_refused(tmp_path: Path) -> None:
    with pytest.raises(replay.SetError, match="номера случаев повторяются: c1"):
        _set(tmp_path, GOOD, GOOD)
    with pytest.raises(replay.SetError, match="мал: случаев 1, а нужно не меньше 2"):
        _set(tmp_path, GOOD, spec=replay_sets.SetSpec("t", ("cases.jsonl",), 2))


def test_a_set_file_not_in_utf8_is_refused_in_words(tmp_path: Path) -> None:
    (tmp_path / "cases.jsonl").write_bytes(json.dumps(GOOD, ensure_ascii=False).encode("cp1251"))

    with pytest.raises(replay.SetError, match=r"cases\.jsonl не в UTF-8"):
        replay_sets.read_set(SPEC, replay_sets.located(SPEC, tmp_path))


def test_checksum_from_the_manifest_is_checked(tmp_path: Path) -> None:
    spec = replay_sets.SetSpec("t", ("cases.jsonl",), 1, sha256={"cases.jsonl": "0" * 64})

    with pytest.raises(replay.SetError, match="контрольная сумма не сходится"):
        _set(tmp_path, GOOD, spec=spec)


def test_the_manifest_in_the_repository_reads_and_its_synthetic_set_too() -> None:
    cases, source = sales_replay.load_set("synthetic", None)

    assert len(cases) == 7
    assert source.startswith("набор «synthetic»")
    assert {case.human.decision for case in cases} == set(Decision)


# --- ворота на собранных прогонах ------------------------------------------------------------


def _result(
    case: str, outcome: Outcome, *, replied: bool = True, broken: int = 0, digest: str = "d"
) -> Result:
    human = Human(Decision.SENT_AS_IS if replied else Decision.SILENT, replied, "asks_info")
    reasons = tuple(f"нарушение {n}" for n in range(broken))
    return Result(case, digest, human, "asks_info", outcome, violations=reasons)


def _run(*results: Result, stopped: str | None = None) -> Run:
    return Run("тест", {}, results, (), stopped)


def test_strict_gate_wants_fewer_violations_when_there_were_some() -> None:
    old = _run(_result("a", Outcome.EDITED, broken=2), _result("b", Outcome.EDITED, broken=1))
    new = _run(_result("a", Outcome.EDITED, broken=1), _result("b", Outcome.EDITED, broken=1))

    assert replay_gate.compare(old, new).passed
    assert replay_gate.compare(old, new, strict=True).problems == (
        "черновиков с нарушениями не меньше: было 2, стало 2",
    )
    clean = _run(_result("a", Outcome.AS_IS), _result("b", Outcome.AS_IS))
    assert replay_gate.compare(clean, clean, strict=True).passed


def test_cases_that_changed_between_runs_are_not_compared() -> None:
    old = _run(_result("a", Outcome.AS_IS, digest="one"))
    new = _run(_result("a", Outcome.SILENT, digest="two"))

    gate = replay_gate.compare(old, new)

    assert gate.problems == ("общих случаев нет — сравнивать нечего",)
    assert (
        "Общих случаев: 0 (только в прежнем 0, только в новом 0, с другой перепиской или решением 1)"
        in gate.lines
    )


def test_an_incomplete_run_does_not_pass_the_gate() -> None:
    whole = _run(_result("a", Outcome.AS_IS))
    cut = _run(_result("a", Outcome.AS_IS), stopped="модель отказала насовсем: нет ключа")

    assert replay_gate.compare(whole, cut).problems == (
        "новый прогон неполный: модель отказала насовсем: нет ключа",
    )


def test_case_print_changes_with_the_letters_and_the_human_decision() -> None:
    letters = (Turn(ours=True, text=OFFER), Turn(ours=False, text="Сколько стоит?"))
    case = Case("c", letters, Human(Decision.SENT_AS_IS, True, "asks_price"))

    assert case.digest() == Case("другой номер", letters, case.human).digest()
    assert case.digest() != Case("c", letters, Human(Decision.OWN, True, "asks_price")).digest()
    assert case.digest() != Case("c", letters[1:], case.human).digest()


def test_a_run_file_reads_back_as_the_same_run() -> None:
    run = Run(
        "набор",
        {"kb": "kb-1"},
        (_result("a", Outcome.EDITED, broken=1), _result("b", Outcome.SILENT, replied=False)),
        (("c", "сеть"),),
        None,
    )

    assert replay_sets.loaded(json.loads(json.dumps(replay_sets.dumped(run))), "файл") == run
    with pytest.raises(replay.SetError, match="не файл прогона"):
        replay_sets.loaded({"format": "other"}, "файл")
    odd = {**replay_sets.dumped(run), "version": ["не таблица"]}
    assert replay_sets.loaded(odd, "файл").version == {}


# --- прогон тем же путём, что агент ------------------------------------------------------------


async def test_no_model_key_stops_the_run_at_the_first_case_in_words(
    session: AsyncSession,
    model: Plug,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await world(session)
    found = model()
    monkeypatch.setattr(llm_cfg, "API_KEY", "")

    assert await replayed(session, Writer()) == 1

    out = capsys.readouterr().out
    assert "ПРОГОН НЕПОЛНЫЙ: модель отказала насовсем" in out
    assert "не прогнано случаев 1" in out
    assert found.calls == 0


async def test_spend_cap_stops_the_run_in_words(
    session: AsyncSession,
    model: Plug,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await world(session)
    model(LABELS)
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", TOKENS)  # хватит на одну ситуацию

    assert await replayed(session, Writer()) == 1

    out = capsys.readouterr().out
    assert "ПРОГОН НЕПОЛНЫЙ: потолок расхода на модель: потолок расхода на модель за день" in out
    assert "Прогон: набор «synthetic»" in out


async def test_files_of_a_version_reach_the_agent_and_are_handed_back_after_the_run(
    session: AsyncSession, model: Plug, tmp_path: Path
) -> None:
    await world(session)
    found = model(LABELS)
    folder = tmp_path / "v"
    folder.mkdir()
    table = moves.TABLE.read_text(encoding="utf-8").replace(
        '"sales-moves-v1"', '"sales-moves-test"'
    )
    (folder / "moves.toml").write_text(table, "utf-8")
    (folder / "reply.md").write_text(parts.PROMPT.read_text(encoding="utf-8"), "utf-8")
    judge_text = replay.PACKAGED.judge.read_text(encoding="utf-8")
    (folder / "judge.md").write_text(f"{judge_text}\n\n{NEW}\n", "utf-8")
    cases, _ = sales_replay.load_set("synthetic", None)
    writer = Writer()

    run = await replay.run(
        session, writer, cases[:1], source="тест", prompts=replay.from_folder(folder)
    )

    assert run.version["declared"]["moves"] == "sales-moves-test"
    assert [request.prompt for request in writer.seen] == [folder / "reply.md"]
    assert [NEW in system for system in found.judges] == [True]
    assert found.calls == 2  # ситуация и судья одного случая
    restored = (situation.PROMPT, moves.TABLE)
    assert restored == (replay.PACKAGED.situation, replay.PACKAGED.moves)
    assert moves.table().version == "sales-moves-v1"


async def test_bad_moves_table_of_a_version_is_refused_before_the_model(
    session: AsyncSession, model: Plug, tmp_path: Path
) -> None:
    await world(session)
    found = model(LABELS)
    (tmp_path / "v").mkdir()
    (tmp_path / "v" / "moves.toml").write_text('version = "x"\n', "utf-8")
    cases, _ = sales_replay.load_set("synthetic", None)

    with pytest.raises(replay.SetError, match="таблица ходов версии"):
        await replay.run(
            session, Writer(), cases, source="тест", prompts=replay.from_folder(tmp_path / "v")
        )

    assert found.calls == 0
    restored = moves.TABLE
    assert restored == replay.PACKAGED.moves


async def test_letter_the_brief_hands_to_a_human_counts_as_rejected_not_silence(
    session: AsyncSession, model: Plug
) -> None:
    await world(session)
    found = model(LABELS)
    case = Case(
        "held",
        (Turn(ours=True, text=OFFER), Turn(ours=False, text="Γεια σας, πόσο κοστίζει;")),
        Human(Decision.OWN, True),
    )

    run = await replay.run(session, Writer(), [case], source="тест")

    [result] = run.results
    assert (result.outcome, result.label, result.false_silence) == (Outcome.REJECTED, None, False)
    assert result.why is not None
    assert result.why.startswith("язык письма не определён")
    assert found.calls == 0


@pytest.mark.usefixtures("sales_on")  # черновик шва — со строкой продаж в реестре
@pytest.mark.parametrize("priced", [False, True])
async def test_replay_matches_the_seam_path_on_the_same_conversation(
    session: AsyncSession, model: Plug, priced: bool
) -> None:
    """Черновик шва и прогон на той же переписке: тот же текст, те же попытки судьи."""
    await world(session)
    lead = await sales_world(session, status=MessageStatus.SENT)
    await AgentSettingsRepository(session).save(Stage.SALES, parts.DEFAULTS, author="тест")
    model(LABELS)
    outcome = await drafting.draft_answer(session, Writer(priced=priced), lead.reply.id)
    stored = await session.scalar(
        select(AgentDraftModel).where(AgentDraftModel.reply_id == lead.reply.id)
    )
    assert stored is not None
    assert outcome.status is DraftStatus.DRAFTED
    case = Case(
        "seam",
        (Turn(ours=True, text=lead.letter.body or ""), Turn(ours=False, text=lead.reply.raw_body)),
        Human(Decision.SENT_AS_IS, True),
    )

    run = await replay.run(session, Writer(priced=priced), [case], source="тест")

    [result] = run.results
    assert result.draft == stored.body
    assert [a["reasons"] for a in stored.meta["attempts"]][:1] == [list(result.violations)]
    assert result.outcome is (Outcome.EDITED if priced else Outcome.AS_IS)
    assert result.label == stored.meta["situation"] == "asks_info"


# --- живые продажи из черновиков ---------------------------------------------------------------

NOW = datetime.now(UTC)


async def lead_reply(
    session: AsyncSession, number: int, text: str, *, days: float, stage: Stage = Stage.SALES
) -> ReplyModel:
    """Переписка этапа: наше письмо и ответ собеседника `days` дней назад."""
    domain = DomainModel(host=f"lead{number}.example.test")
    session.add(domain)
    await session.flush()
    campaign = CampaignModel(name=f"Рассылка {number}", stage=stage, status="draft")
    session.add(campaign)
    await session.flush()
    thread = ThreadModel(domain_id=domain.id, campaign_id=campaign.id)
    session.add(thread)
    await session.flush()
    letter_row = MessageModel(
        campaign_id=campaign.id,
        thread_id=thread.id,
        domain_id=domain.id,
        step=0,
        status=MessageStatus.SENT,
        subject="Вопрос",
        body=OFFER,
        idempotency_key=f"replay:{number}:0",
        sent_at=NOW - timedelta(days=days + 1),
    )
    session.add(letter_row)
    await session.flush()
    reply = ReplyModel(
        thread_id=thread.id,
        message_id=letter_row.id,
        kind=ReplyKind.HUMAN,
        raw_body=text,
        from_email=f"ceo@lead{number}.example.test",
        created_at=NOW - timedelta(days=days),
    )
    session.add(reply)
    await session.flush()
    return reply


async def answered(session: AsyncSession, reply: ReplyModel, text: str) -> None:
    """Наш ответ на письмо — ушёл позже него и в переписку случая не попадает."""
    thread = await session.get(ThreadModel, reply.thread_id)
    assert thread is not None
    session.add(
        MessageModel(
            campaign_id=thread.campaign_id,
            thread_id=thread.id,
            domain_id=thread.domain_id,
            step=ANSWER_STEP,
            status=MessageStatus.SENT,
            subject="Re: Вопрос",
            body=text,
            idempotency_key=f"replay:answer:{reply.id}",
            sent_at=reply.created_at + timedelta(hours=2),
            answers_reply_id=reply.id,
        )
    )
    await session.flush()


async def drafted(
    session: AsyncSession, reply: ReplyModel, status: DraftStatus, settings_id: int, **values: Any
) -> AgentDraftModel:
    row = AgentDraftModel(
        reply_id=reply.id,
        settings_id=settings_id,
        status=status,
        body=values.pop("body", "Черновик агента."),
        meta={"situation": values.pop("label", "asks_price")},
        model="test-model",
        prompt_version="test",
        tokens=0,
        **values,
    )
    session.add(row)
    await session.flush()
    return row


async def live_world(session: AsyncSession) -> dict[str, AgentDraftModel]:
    settings = await AgentSettingsRepository(session).save(
        Stage.SALES, parts.DEFAULTS, author="тест"
    )
    sid = settings.id
    edited = await lead_reply(session, 1, "Сколько стоит аудит?", days=8)
    await answered(session, edited, "Наш ответ после правки.")
    as_is = await lead_reply(session, 2, "Давайте созвонимся на этой неделе.", days=7)
    await answered(session, as_is, "Черновик агента.")
    rejected = await lead_reply(session, 3, "Not now, maybe next quarter.", days=6)
    own = await lead_reply(session, 4, "Спасибо, получил. А примеры работ у вас есть?", days=5)
    await answered(session, own, "Ответ человека сам.")
    silent = await lead_reply(session, 5, "Спасибо, получил.", days=5)
    fresh = await lead_reply(session, 6, "Спасибо, получил.", days=1)
    waiting = await lead_reply(session, 7, "Сколько стоит аудит?", days=2)
    donors = await lead_reply(session, 8, "How much?", days=9, stage=Stage.DONORS)
    return {
        "edited": await drafted(
            session,
            edited,
            DraftStatus.SENT,
            sid,
            edited=True,
            final_body="Наш ответ после правки.",
        ),
        "as_is": await drafted(
            session, as_is, DraftStatus.SENT, sid, edited=False, label="wants_to_talk"
        ),
        "rejected": await drafted(
            session,
            rejected,
            DraftStatus.REJECTED,
            sid,
            reject_reason="не по делу",
            label="not_now",
        ),
        "own": await drafted(
            session, own, DraftStatus.SENT, sid, body="", edited=True, label="ack"
        ),
        "silent": await drafted(session, silent, DraftStatus.SKIPPED, sid, body="", label="ack"),
        "fresh": await drafted(session, fresh, DraftStatus.SKIPPED, sid, body="", label="ack"),
        "waiting": await drafted(session, waiting, DraftStatus.DRAFTED, sid),
        "donors": await drafted(session, donors, DraftStatus.SENT, sid, edited=False),
    }


async def test_live_sales_are_read_from_the_drafts_with_the_human_decision(
    session: AsyncSession,
) -> None:
    rows = await live_world(session)

    found = await replay_drafts.from_drafts(session, now=NOW)

    by_id = {case.id: case for case in found.cases}
    assert (found.pending, found.fresh) == (1, 1)
    assert set(by_id) == {
        f"draft-{rows[name].id}" for name in ("edited", "as_is", "rejected", "own", "silent")
    }
    decisions = {
        name: by_id[f"draft-{rows[name].id}"].human
        for name in ("edited", "as_is", "rejected", "own", "silent")
    }
    assert decisions == {
        "edited": Human(Decision.SENT_EDITED, True, "asks_price", "model", None),
        "as_is": Human(Decision.SENT_AS_IS, True, "wants_to_talk", "model", None),
        "rejected": Human(Decision.REJECTED, False, "not_now", "model", "не по делу"),
        "own": Human(Decision.OWN, True, "ack", "model", None),
        "silent": Human(Decision.SILENT, False, "ack", "model", None),
    }
    # Переписка — к приходу письма: наш ответ на него в неё не попал.
    assert by_id[f"draft-{rows['edited'].id}"].turns == (
        Turn(ours=True, text=OFFER),
        Turn(ours=False, text="Сколько стоит аудит?"),
    )


async def test_live_sales_replay_end_to_end_through_the_command(
    session: AsyncSession, model: Plug, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    await world(session)
    rows = await live_world(session)
    model(LABELS)
    out_file = tmp_path / "live.json"
    args = run_args("--drafts", "--out", str(out_file))

    code = await sales_replay.execute(session, Writer(), args, None, "")

    out = capsys.readouterr().out
    assert code == 0
    assert "Живые продажи: случаев с исходом 5; ждут решения 1; пропусков моложе 3 дн. 1" in out
    assert "Прогон: черновики продаж (agent_drafts); случаев 5" in out
    assert "Метка ситуации с меткой прежней версии: совпала 4 из 5" in out
    saved = replay_sets.loaded(json.loads(out_file.read_text("utf-8")), "файл")
    own = next(r for r in saved.results if r.case == f"draft-{rows['own'].id}")
    assert (own.label, own.outcome, own.human.decision) == (
        "asks_info",
        Outcome.AS_IS,
        Decision.OWN,
    )
    edited = next(r for r in saved.results if r.case == f"draft-{rows['edited'].id}")
    assert CALL in edited.draft


async def test_no_live_decisions_yet_is_refused(
    session: AsyncSession, model: Plug, capsys: pytest.CaptureFixture[str]
) -> None:
    found = model()

    assert await sales_replay.execute(session, Writer(), run_args("--drafts"), None, "") == 1

    assert "НАБОР ПУСТ: у черновиков продаж ещё нет решений людей" in capsys.readouterr().out
    assert found.calls == 0
