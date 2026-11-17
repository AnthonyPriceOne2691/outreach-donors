"""Eval разбора ответов: итог по случаю файлом и ворота — без сети.

Живая модель здесь не зовётся: её заменяет подставной разбор, который знает ответ случая. Файл итога
(`--out`) — для парного сравнения двух моделей на одном наборе: сдвиг уверенности у порога и
перевернувшиеся случаи видны только по случаю. Живой прогон — по слову владельца.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from backend.features.replies.extract import Extracted
from backend.features.replies.inbound import Incoming
from scripts import eval_reply_parse as ev

CASES = ev.load(ev.DEFAULT)


def _money(raw: Any) -> Decimal | None:
    return None if raw is None else Decimal(str(raw))


class Parser:
    """Подставной разбор: поля случая как есть; у `wrong` — белая цена на 1 больше."""

    def __init__(self, wrong: frozenset[str] = frozenset()) -> None:
        self.by_text = {case["text"]: case for case in CASES}
        self.wrong = wrong

    async def extract(self, incoming: Incoming) -> Extracted:
        case = self.by_text[incoming.text]
        expect = case["expect"]
        white = _money(expect["price_white"])
        if case["id"] in self.wrong and white is not None:
            white += 1
        return Extracted(
            price_white=white,
            price_grey=_money(expect["price_grey"]),
            currency=expect["currency"],
            placement=expect["placement"],
            confidence=0.95,
            tokens_spent=11,
        )


def test_the_report_file_holds_every_case(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ev, "ExtractClient", Parser)
    out = tmp_path / "run.json"

    assert ev.main(["--out", str(out)]) == 0

    rows = json.loads(out.read_text(encoding="utf-8"))
    assert [row["id"] for row in rows] == [case["id"] for case in CASES]
    assert all(row["wrong"] == [] and not row["dangerous"] for row in rows)
    assert {(row["confidence"], row["tokens"]) for row in rows} == {(0.95, 11)}
    priced = next(row for row in rows if row["price_white"] is not None)
    assert isinstance(priced["price_white"], str)  # Decimal — строкой, без потери знаков
    assert f"Все поля верно:        {len(CASES)}/{len(CASES)}" in capsys.readouterr().out


def test_a_wrong_price_laid_by_itself_is_dangerous_in_the_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    auto = next(case for case in CASES if case.get("decision") == "auto")
    monkeypatch.setattr(ev, "ExtractClient", lambda: Parser(frozenset({auto["id"]})))
    out = tmp_path / "run.json"

    assert ev.main(["--gate", "0", "--out", str(out)]) == 1

    assert "ВОРОТА ЗАКРЫТЫ: опасных" in capsys.readouterr().out
    [bad] = [row for row in json.loads(out.read_text(encoding="utf-8")) if row["dangerous"]]
    assert (bad["id"], bad["decision"], bad["wrong"]) == (auto["id"], "auto", ["price_white"])


def test_without_out_there_is_no_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ev, "ExtractClient", Parser)
    monkeypatch.chdir(tmp_path)

    assert ev.main([]) == 0

    assert list(tmp_path.iterdir()) == []
    assert "ОПАСНЫХ (неверная цена легла бы сама): 0/" in capsys.readouterr().out
