"""Eval вида ответа продаж (волна В3а): набор, ворота, порча промпта.

Живая модель здесь не зовётся: её заменяет подставной вызов. Это проверка
механики ворот — что синтетика годится, что опасная ошибка и порча промпта
закрывают ворота, что внешний набор сверяется с манифестом. Живой прогон
на наборе — координатор по слову владельца, числа — в verify-report.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest
from backend.features.sales import reply_kind
from backend.features.sales.reply_kind import KindFound, SalesKind, Unanswered
from scripts import eval_sales_reply as ev

ORACLE: dict[str, dict[str, Any]] = {case["text"]: case["expect"] for case in ev.load(ev.SYNTHETIC)}


class Oracle:
    """Подставная модель, которая «знает» ответ — но только те виды, что
    определены в промпте: снятое определение она назвать не может и путает
    его с соседним видом, уверенно. Так порча промпта видна без сети."""

    def __init__(self, *, wrong: dict[str, SalesKind] | None = None) -> None:
        self.wrong = wrong or {}

    async def classify(self, *, text: str, subject: str) -> KindFound | Unanswered:
        expect = ORACLE[text]
        kind = SalesKind(expect["kind"])
        known = f'- "{kind.value}":' in reply_kind.load_prompt()
        if not known:
            kind = SalesKind.INTERESTED
        kind = self.wrong.get(text, kind)
        quote = text.split(".", maxsplit=1)[0]
        return KindFound(kind, 0.9, quote=quote, contact=expect.get("contact"), tokens=50)

    async def aclose(self) -> None:
        return None


@pytest.fixture
def oracle(monkeypatch: pytest.MonkeyPatch) -> Oracle:
    fake = Oracle()
    monkeypatch.setattr(ev, "KindClient", lambda model=None: fake)
    # Порча промпта подменяет функцию модуля — вернуть её после теста.
    monkeypatch.setattr(reply_kind, "load_prompt", reply_kind.load_prompt)
    return fake


# --- синтетика --------------------------------------------------------------------------


def test_synthetic_set_covers_every_kind_in_twenty_to_thirty_cases() -> None:
    cases = ev.load(ev.SYNTHETIC)

    assert 20 <= len(cases) <= 30
    assert len({case["id"] for case in cases}) == len(cases)
    kinds = {case["expect"]["kind"] for case in cases}
    assert kinds == reply_kind.MODEL_KINDS
    assert any("injection" in case["tags"] for case in cases)


def test_synthetic_set_has_only_made_up_addresses_found_in_the_text() -> None:
    for case in ev.load(ev.SYNTHETIC):
        for address in re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", case["text"]):
            assert address.endswith(".example"), case["id"]
        contact = case["expect"].get("contact")
        if contact:
            assert case["expect"]["kind"] == "referral"
            assert contact in case["text"], case["id"]


# --- ворота -------------------------------------------------------------------------------


def test_eval_is_green_when_every_kind_is_right(
    oracle: Oracle, capsys: pytest.CaptureFixture[str]
) -> None:
    assert ev.main([]) == 0
    out = capsys.readouterr().out
    assert "ОПАСНЫХ: 0" in out
    assert f"Версия промпта: {reply_kind.PROMPT_VERSION}" in out


def test_spoiled_prompt_closes_the_gates(
    oracle: Oracle, capsys: pytest.CaptureFixture[str]
) -> None:
    """Обратный прогон: снято определение «хочет говорить» — eval красный."""
    assert ev.main(["--drop-kind", "wants_to_talk"]) == 1
    out = capsys.readouterr().out
    assert "ПОРЧА ПРОМПТА" in out
    assert "полнота wants_to_talk 0% < 95%" in out
    assert '- "wants_to_talk":' not in reply_kind.load_prompt()


def test_dangerous_mistake_closes_the_gates(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """«Хочет говорить», уверенно принятое за «не интересно», — опасная ошибка."""
    call = next(text for text, expect in ORACLE.items() if expect["kind"] == "wants_to_talk")
    fake = Oracle(wrong={call: SalesKind.NOT_INTERESTED})
    monkeypatch.setattr(ev, "KindClient", lambda model=None: fake)

    assert ev.main([]) == 1
    assert "опасных 1 > 0" in capsys.readouterr().out


def test_false_unsubscribe_closes_the_gates(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    question = next(text for text, expect in ORACLE.items() if expect["kind"] == "question")
    fake = Oracle(wrong={question: SalesKind.UNSUBSCRIBE})
    monkeypatch.setattr(ev, "KindClient", lambda model=None: fake)

    assert ev.main([]) == 1
    assert "ложных отписок 1 > 0" in capsys.readouterr().out


def test_a_doubt_below_the_threshold_is_not_dangerous() -> None:
    """Ниже порога ответ ждёт человека — это минута человека, а не опасная ошибка."""
    case = {"id": "x", "expect": {"kind": "wants_to_talk"}}
    shaky = ev.judged(case, KindFound(SalesKind.NOT_INTERESTED, 0.5, quote="x"), 0.8)
    refused = ev.judged(case, Unanswered("модель не ответила: сеть", False), 0.8)

    assert (shaky.dangerous, refused.dangerous) == (False, False)
    assert refused.refusal == "модель не ответила: сеть"


def test_spoiling_a_kind_the_prompt_does_not_define_is_refused() -> None:
    with pytest.raises(ev.SetError, match="портить нечего"):
        ev.without_kind("no kinds here", "wants_to_talk")


# --- внешний набор: вне репозитория, сверка с манифестом ------------------------------------


def test_golden_set_is_required_not_assumed_empty(capsys: pytest.CaptureFixture[str]) -> None:
    assert ev.main(["--golden"], golden_dir="") == 1
    assert "набор не найден" in capsys.readouterr().out


def test_golden_set_must_match_the_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    golden = tmp_path / "sales_reply_golden.jsonl"
    golden.write_bytes(ev.SYNTHETIC.read_bytes())
    digest = hashlib.sha256(golden.read_bytes()).hexdigest()
    manifest = json.loads(ev.MANIFEST.read_text(encoding="utf-8"))

    assert manifest["file"] == golden.name
    assert ev.golden(str(tmp_path))  # манифест без хэша — набор берётся, хэш печатается
    assert digest in capsys.readouterr().out

    spoiled = tmp_path / "manifest.json"
    spoiled.write_text(json.dumps({**manifest, "sha256": "0" * 64}), encoding="utf-8")
    with pytest.raises(ev.SetError, match="не тот, что в манифесте"):
        ev.golden(str(tmp_path), manifest=spoiled)
    counted = tmp_path / "counted.json"
    counted.write_text(json.dumps({**manifest, "sha256": digest, "count": 3}), encoding="utf-8")
    with pytest.raises(ev.SetError, match="примеров 28 ≠ 3"):
        ev.golden(str(tmp_path), manifest=counted)


def test_manifest_has_no_data_only_numbers() -> None:
    """В репозитории — только манифест: число, хэш, дата, метрика и ворота."""
    manifest = json.loads(ev.MANIFEST.read_text(encoding="utf-8"))

    assert set(manifest) >= {"file", "count", "sha256", "date", "baseline", "gates"}
    assert manifest["gates"] == {
        "wants_to_talk_recall": ev.Gates().wants_recall,
        "unsubscribe_recall": ev.Gates().unsubscribe_recall,
        "dangerous": ev.Gates().dangerous,
        "false_unsubscribe": ev.Gates().false_unsubscribe,
    }
