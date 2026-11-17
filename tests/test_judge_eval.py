"""Eval судьи площадки: итог по случаю файлом и ворота — без сети.

Живая модель здесь не зовётся: её заменяет подставной судья, который знает класс случая. Файл итога
(`--out`) — для парного сравнения двух моделей на одном наборе: общий итог не говорит, какие случаи
перевернулись. Живой прогон — по слову владельца.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from backend.features.donors.publisher_judge import Decider, Intent, Judgement, Recommendation
from scripts import eval_judge as ev

CASES = ev.load(ev.DEFAULT)
#: Класс случая → вид, который назвал бы верный судья.
INTENT = {
    "placement": Intent.SELLS_PLACEMENT,
    "publisher": Intent.EDITORIAL_ADS,
    "brand_door": Intent.SELLS_OWN,
    "brand": Intent.SELLS_OWN,
    "vendor": Intent.LINK_VENDOR,
}


def judge(cut: frozenset[str] = frozenset()) -> Any:
    """Подставной судья: верный совет по классу случая; хосты из `cut` — отрезаны."""
    expect = {case["host"]: case["expect"] for case in CASES}

    async def judge_host(_http: httpx.AsyncClient, *, host: str, **_: Any) -> Judgement:
        cls = expect[host]
        advice = Recommendation.REJECT if host in cut else ev.WANT[cls]
        # Правило, а не модель: подставному судье арбитр не нужен.
        return Judgement(
            INTENT[cls], advice, None, "подставной", "fake", tokens=7, decided_by=Decider.RULE
        )

    return judge_host


def test_the_report_file_holds_every_case(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ev, "judge_host", judge())
    out = tmp_path / "run.json"

    assert ev.main(["--out", str(out)]) == 0

    rows = json.loads(out.read_text(encoding="utf-8"))
    assert [row["id"] for row in rows] == [case["id"] for case in CASES]
    assert all(row["right"] and not row["dangerous"] for row in rows)
    assert {row["model"] for row in rows} == {"fake"}
    assert sum(row["tokens"] for row in rows) == 7 * len(CASES)
    assert f"случаев {len(CASES)}, токенов {7 * len(CASES)}" in capsys.readouterr().out


def test_a_cut_seller_closes_the_gates_and_is_in_the_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seller = next(case for case in CASES if case["expect"] == "placement")
    monkeypatch.setattr(ev, "judge_host", judge(frozenset({seller["host"]})))
    out = tmp_path / "run.json"

    assert ev.main(["--out", str(out)]) == 1

    assert "ВОРОТА ЗАКРЫТЫ: отрезано продавцов размещения 1" in capsys.readouterr().out
    [cut] = [row for row in json.loads(out.read_text(encoding="utf-8")) if row["dangerous"]]
    assert (cut["id"], cut["recommendation"], cut["right"]) == (seller["id"], "reject", False)


def test_without_out_there_is_no_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ev, "judge_host", judge())
    monkeypatch.chdir(tmp_path)

    assert ev.main([]) == 0

    assert list(tmp_path.iterdir()) == []
    assert "ОПАСНЫХ (донор отрезан / бренд или посредник принят): 0" in capsys.readouterr().out
