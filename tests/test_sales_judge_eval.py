"""Eval судьи продаж (волна В3б, Spec 3.4 A1 и A3): набор, ворота, порча промптов.

Живая модель не зовётся: на её месте подставной HTTP с записанными ответами
(`tests/test_sales_judge_eval_answers.json`) — по случаю и по промпту: обычному и
испорченному, без правила «цены только из базы». Ответы записаны вручную по форме
судьи и генератора, а не сняты с живой модели: это проверка механики ворот, числа
живой модели — прогон координатора по слову владельца.

Проверяется то, чего не видно по зелёному прогону: доли опасных и ложных block
считаются и закрывают ворота ниже порога (A1); испорченные промпты красят eval, а
итог обратного прогона ложится файлом (A3); набора нет или он не тот, что в
манифесте, — красный, а не «0 расхождений»; судья, который не смог проверить
ничего, не выглядит безупречным; набор «стиль» судится только правилами стиля.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from backend.config import llm as llm_cfg
from backend.config import sales as sales_cfg
from backend.config.sales import SalesJudgeMode
from backend.features.agent.writer import load_prompt
from backend.features.sales.agent import calling, judge, parts
from scripts import eval_sales_judge as ev

ANSWERS: dict[str, dict[str, Any]] = json.loads(
    Path(__file__).with_name("test_sales_judge_eval_answers.json").read_text(encoding="utf-8")
)
CASES = ev.load(ev.SYNTHETIC)
TOKENS = 53
Capture = pytest.CaptureFixture[str]


def _between(text: str, head: str, tail: str) -> str:
    return text.split(head, 1)[1].split(tail, 1)[0]


class Recorded:
    """Модель на месте сети: отвечает записанным ответом случая на тот промпт, что пришёл.

    Случай узнаётся по черновику (судья) или по письму собеседника (генератор);
    промпт без правила «цены только из базы» — испорченный, и ответ берётся записанный
    для испорченного. Статус не 200 — отказ провайдера на всё.
    """

    def __init__(self, answers: dict[str, dict[str, Any]], status: int = 200) -> None:
        self.answers, self.status = answers, status
        self.calls: list[tuple[str, str, bool]] = []
        self.by_draft: dict[str, str] = {}
        self.by_letter: dict[str, str] = {}
        for case in CASES:
            if "draft" in case:
                self.by_draft[case["draft"]] = case["id"]
                continue
            self.by_letter[case["letter"]] = case["id"]
            for key in ("writer", "writer_spoiled"):
                self.by_draft[answers[case["id"]][key]["body"]] = case["id"]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        system, user = (message["content"] for message in payload["messages"])
        spoiled = ev.PRICE_RULE not in system.casefold()
        if "<<<DRAFT" in user:
            key, case_id = "judge", self.by_draft[_between(user, "<<<DRAFT\n", "\nDRAFT>>>")]
        else:
            turns = json.loads(_between(user, "<<<CONVERSATION\n", "\nCONVERSATION>>>"))
            letter = [turn["text"] for turn in turns if turn["from"] == "them"][-1]
            key, case_id = "writer", self.by_letter[letter]
        self.calls.append((key, case_id, spoiled))
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "nope"}})
        answer = self.answers[case_id][f"{key}_spoiled" if spoiled else key]
        content = json.dumps(answer, ensure_ascii=False)
        body = {"choices": [{"message": {"content": content}}], "usage": {"total_tokens": TOKENS}}
        return httpx.Response(200, json=body)


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> Any:
    def plugged(answers: dict[str, dict[str, Any]] = ANSWERS, status: int = 200) -> Recorded:
        fake = Recorded(answers, status)
        monkeypatch.setattr(
            calling, "client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(fake))
        )
        monkeypatch.setattr(llm_cfg, "API_KEY", "test-key")
        return fake

    return plugged


def changed(case_id: str, key: str, answer: dict[str, Any]) -> dict[str, dict[str, Any]]:
    answers = copy.deepcopy(ANSWERS)
    answers[case_id][key] = answer
    return answers


ALLOW = {"claims": [], "promises": [], "tone": {"ok": True, "problem": ""}}


# --- синтетика ----------------------------------------------------------------------------


def test_synthetic_set_covers_every_kind_in_twenty_to_thirty_cases() -> None:
    kinds = {case["kind"] for case in CASES}

    assert 20 <= len(CASES) <= 30
    assert len({case["id"] for case in CASES}) == len(CASES)
    assert kinds == {*ev.VIOLATIONS, ev.GOOD, ev.GENERATE}
    assert sum(case["kind"] in ev.DANGEROUS for case in CASES) >= 8


def test_synthetic_set_has_only_made_up_links_and_every_model_answer_recorded() -> None:
    for case in CASES:
        text = " ".join([case["letter"], case.get("draft", ""), *case["facts"]])
        for host in re.findall(r"https?://([^/\s)]+)", text):
            assert host.endswith(".example") or host == "t.me", case["id"]
        assert "@" not in text, case["id"]  # адрес почты замаскировался бы до модели
    needs_model = {case["id"] for case in CASES if case["kind"] in ("good", "claim", "tone")}
    assert needs_model <= set(ANSWERS)


# --- A1: доли и ворота ----------------------------------------------------------------------


def test_a1_eval_reports_the_shares_and_is_green_above_the_gates(
    model: Any, capsys: Capture, tmp_path: Path
) -> None:  # A1
    fake = model()
    out = tmp_path / "run.json"

    assert ev.main(["--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "ОПАСНЫХ поймано: 11/11 (100%); ложных block на хороших: 0/8 (0%)" in printed
    assert f"Версии: судья {judge.PROMPT_VERSION}, черновик {parts.PROMPT_VERSION}" in printed
    assert "генератор: случаев 3, назвал цену 0" in printed
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert (saved["dangerous"], saved["false_block"], saved["failed"]) == (
        {"caught": 11, "total": 11},
        {"stopped": 0, "total": 8},
        [],
    )
    by_rules = {case["case_id"] for case in saved["cases"] if case["by_rules"]}
    assert by_rules == {
        "price-digits-ru", "price-from-ru", "price-discount-ru", "language-en-letter-ru-draft",
        "two-cta-ru", "foreign-link-ru", "deferral-again-ru", "form-exclaim-ru",
    }  # fmt: skip
    assert not any(case_id in by_rules for _, case_id, _ in fake.calls)  # правила — без модели


@pytest.mark.parametrize(
    ("case_id", "answer", "closed"),
    [
        ("price-words-ru", ALLOW, "опасных поймано 91% < 95%"),
        (
            "good-info-en",
            {**ALLOW, "tone": {"ok": False, "problem": "сухо"}},
            "ложных block 12% > 10%",
        ),
    ],
    ids=["dangerous-missed", "good-blocked"],
)
def test_a1_below_the_threshold_is_red(
    model: Any, capsys: Capture, case_id: str, answer: dict[str, Any], closed: str
) -> None:  # A1
    model(changed(case_id, "judge", answer))

    assert ev.main([]) == 1
    printed = capsys.readouterr().out
    assert f"!! {case_id}" in printed
    assert f"ВОРОТА ЗАКРЫТЫ: {closed}" in printed


def test_thresholds_are_parameters(model: Any, capsys: Capture) -> None:
    model(changed("price-words-ru", "judge", ALLOW))

    assert ev.main(["--dangerous-min", "0.9"]) == 0
    assert "ОПАСНЫХ поймано: 10/11 (91%)" in capsys.readouterr().out


def test_a_judge_that_checks_nothing_is_not_flawless(model: Any, capsys: Capture) -> None:
    """Модель отказывает на всём: опасные «пойманы» эскалацией, но хорошие тоже стоят —
    ворота закрыты, а не 100 % пойманных при 0 % ложных."""
    model(status=503)

    assert ev.main([]) == 1
    assert "ложных block 100% > 10%" in capsys.readouterr().out


def test_eval_measures_the_verdict_not_the_mode(
    model: Any, capsys: Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sales_cfg, "JUDGE_MODE", SalesJudgeMode.SHADOW)
    model()

    assert ev.main([]) == 0
    assert "ОПАСНЫХ поймано: 11/11 (100%)" in capsys.readouterr().out


def test_no_key_is_refused_before_any_call(
    model: Any, capsys: Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = model()
    monkeypatch.setattr(llm_cfg, "API_KEY", "")

    assert ev.main([]) == 1
    assert "LLM_API_KEY не задан" in capsys.readouterr().out
    assert fake.calls == []


# --- A3: порча промптов -------------------------------------------------------------------


def test_both_prompts_carry_the_price_rule_in_one_line() -> None:
    for path in (judge.PROMPT, parts.PROMPT):
        prompt = load_prompt(path)
        assert sum(ev.PRICE_RULE in line.casefold() for line in prompt.splitlines()) == 1
        spoiled = ev.without_price_rule(prompt)
        assert len(spoiled.splitlines()) == len(prompt.splitlines()) - 1
        assert ev.PRICE_RULE not in spoiled.casefold()


def test_a3_spoiled_prompts_turn_the_eval_red_and_the_reverse_run_is_written(
    model: Any, capsys: Capture, tmp_path: Path
) -> None:  # A3
    fake = model()
    out = tmp_path / "reverse-run.json"

    assert ev.main(["--spoil", "--out", str(out)]) == 1
    printed = capsys.readouterr().out
    assert "ПОРЧА ПРОМПТА: снято правило «цены только из базы» у генератора и судьи" in printed
    assert "ВОРОТА ЗАКРЫТЫ: опасных поймано 50% < 95%" in printed
    assert "генератор: случаев 3, назвал цену 3, судья не пропустил 0" in printed
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["spoiled"] is True
    assert saved["versions"]["judge"] == f"{judge.PROMPT_VERSION}+spoiled"
    assert saved["dangerous"] == {"caught": 7, "total": 14}
    assert saved["failed"] == ["опасных поймано 50% < 95%"]
    assert {spoiled for _, _, spoiled in fake.calls} == {True}  # модель видела только порчу
    assert ev.PRICE_RULE in load_prompt(judge.PROMPT).casefold()  # файлы промптов целы


def test_spoiling_a_prompt_without_the_rule_is_refused() -> None:
    with pytest.raises(ev.SetError, match="портить нечего"):
        ev.without_price_rule("no price rule here")


# --- внешние наборы: вне репозитория, сверка с манифестом ------------------------------------


def test_golden_set_is_required_not_assumed_empty(model: Any, capsys: Capture) -> None:  # E1
    fake = model()

    assert ev.main(["--golden"], golden_dir="") == 1
    assert "набор не найден: задайте SALES_JUDGE_GOLDEN_DIR" in capsys.readouterr().out
    assert fake.calls == []


def test_missing_set_file_is_red(capsys: Capture, tmp_path: Path) -> None:  # E1
    assert ev.main([str(tmp_path / "нет.jsonl")]) == 1
    assert "НАБОР НЕ ПРОЧИТАН: набор не найден" in capsys.readouterr().out


def _golden(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **spec: Any) -> Path:
    folder = tmp_path / "golden"
    folder.mkdir()
    data = folder / "sales_judge_golden.jsonl"
    data.write_bytes(ev.SYNTHETIC.read_bytes())
    manifest = json.loads(ev.MANIFEST.read_text(encoding="utf-8")) | spec
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(ev, "MANIFEST", path)
    return folder


@pytest.mark.parametrize(
    ("spec", "why"),
    [
        ({"sha256": "0" * 64}, "набор не тот, что в манифесте: sha256"),
        ({"count": 41}, "набор не тот, что в манифесте: случаев 29 ≠ 41"),
        ({}, "в наборе 29 случаев, нужно не меньше 40"),
    ],
    ids=["hash", "count", "too-small"],
)
def test_golden_set_must_match_the_manifest(
    model: Any,
    capsys: Capture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    spec: Any,
    why: str,
) -> None:
    model()
    folder = _golden(tmp_path, monkeypatch, **spec)

    assert ev.main(["--golden"], golden_dir=str(folder)) == 1
    assert why in capsys.readouterr().out


def test_golden_set_that_matches_the_manifest_runs(
    model: Any, capsys: Capture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digest = hashlib.sha256(ev.SYNTHETIC.read_bytes()).hexdigest()
    model()
    folder = _golden(tmp_path, monkeypatch, sha256=digest, count=29, min_count=20)

    assert ev.main(["--golden"], golden_dir=str(folder)) == 0
    assert f"случаев 29, sha256 {digest}" in capsys.readouterr().out


def test_case_of_an_unknown_kind_is_refused(model: Any, capsys: Capture, tmp_path: Path) -> None:
    model()
    path = tmp_path / "set.jsonl"
    path.write_text(json.dumps({**CASES[0], "kind": "weird"}) + "\n", encoding="utf-8")

    assert ev.main([str(path)]) == 1
    assert f"{CASES[0]['id']}: незнакомый вид weird" in capsys.readouterr().out


# --- набор «стиль»: только правила стиля, отсрочки и длины ----------------------------------

STYLE: list[dict[str, Any]] = [
    {"id": "s1", "verdict": "bad", "draft": "Добрый день!\nСпасибо. Ждём ответа.\nОтдел продаж"},
    {
        "id": "s2",
        "verdict": "bad",
        "draft": "Здравствуйте,\nСпасибо. Пришлю кейс на днях.",
        "deferred": ["Пришлю кейс завтра."],
    },
    {"id": "s3", "verdict": "bad", "draft": "Ок."},
    {
        "id": "s4",
        "verdict": "good",
        "draft": "Добрый день,\nСпасибо за ответ. Вот кейс: рост в два раза.\nОтдел",
    },
    {
        "id": "s5",
        "verdict": "good",
        "draft": "Hello,\nThank you. Here is the case we promised.\nSales",
    },
]


def _style(tmp_path: Path, rows: list[dict[str, Any]]) -> Path:
    folder = tmp_path / "style"
    folder.mkdir()
    lines = (json.dumps(row, ensure_ascii=False) for row in rows)
    (folder / "sales_style_golden.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return folder


def test_style_set_is_judged_by_the_style_rules_alone(
    model: Any, capsys: Capture, tmp_path: Path
) -> None:
    fake = model()

    assert ev.main(["--style"], style_dir=str(_style(tmp_path, STYLE))) == 0
    printed = capsys.readouterr().out
    assert "Плохих поймано: 3/3 (100%)" in printed
    assert "Хороших задержано: 0/2 (0%)" in printed
    assert fake.calls == []  # без модели


def test_style_set_holding_good_drafts_is_red(model: Any, capsys: Capture, tmp_path: Path) -> None:
    model()
    rows = [*STYLE[:4], {**STYLE[4], "draft": "Hello,\nThank you! Here is the case.\nSales"}]

    assert ev.main(["--style"], style_dir=str(_style(tmp_path, rows))) == 1
    assert "ВОРОТА ЗАКРЫТЫ: хороших задержано 50% > 10%" in capsys.readouterr().out


def test_style_set_is_required(capsys: Capture) -> None:  # E1
    assert ev.main(["--style"], style_dir="") == 1
    assert "набор не найден: задайте SALES_STYLE_GOLDEN_DIR" in capsys.readouterr().out
