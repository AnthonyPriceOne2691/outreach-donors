"""Механика eval помощника описания сегмента (`scripts/eval_sales_segment_helper.py`) — на
подставной модели: черновики привязаны к гипотезам синтетики судьи, порча снимает ровно свою
строку промпта, живому набору нужен черновик файлом, ворота закрываются словами — судья по
описаниям помощника вне ворот, вывод «расплывчатый» неверен, помощник не ответил.

Судья здесь — подмена `eval_sales_segment.run`: проверяется счёт и ворота этого eval, а сам
судья — в `test_sales_segment_eval.py`.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest
from backend.features.sales import segment_helper as sh
from backend.features.sales.segment_helper import Answer, HelperClient, Suggestion
from backend.features.sales.segment_judge import SegmentClient
from backend.features.sales.segment_models import Lane
from scripts import eval_sales_segment as ev
from scripts import eval_sales_segment_helper as eh

GOOD = "описание помощника"


class FakeHelper(HelperClient):
    """Помощник без сети: ответ — по черновику; `silent` — черновики без ответа."""

    def __init__(self, *, loose: dict[str, bool] | None = None, silent: Iterable[str] = ()) -> None:
        # Без своего HTTP-клиента: сеть не нужна, и закрывать нечего (`aclose` — пустой).
        self._model, self._own_client = "fake", False
        self.loose = loose or {}
        self.silent = set(silent)
        self.systems: list[str | None] = []

    async def suggest(self, draft: str, *, system: str | None = None) -> Answer:
        self.systems.append(system)
        if draft in self.silent:
            return Answer(None, "пустой ответ модели", 4000, "fake")
        problems = ("«сайты о …» — тема, а не вид бизнеса",) if self.loose.get(draft, True) else ()
        return Answer(Suggestion(GOOD, ("вопрос?",), problems), None, 100, "fake")


def judged_by(segment_right: str) -> Any:
    """Подмена судьи: по описанию `segment_right` — верно; по другому — принимает всё."""

    async def fake(cases: Iterable[dict[str, Any]], client: Any, **_: Any) -> list[ev.Result]:
        results = []
        for case in cases:
            right = case["segment"] == segment_right
            wanted = ev.WANTED[case["expect"].split("|")[0]]
            lane = wanted if right else Lane.ACCEPT
            results.append(
                ev.Result(
                    case["id"],
                    case["hypothesis"],
                    case["expect"],
                    lane.value,
                    "",
                    None,
                    "model",
                    False,
                    10,
                )
            )
        return results

    return fake


@pytest.fixture
async def judge() -> Any:
    client = SegmentClient(api_key="k-test")  # pragma: allowlist secret
    yield client
    await client.aclose()


def test_every_draft_belongs_to_a_hypothesis_of_the_judge_set_and_traps_are_there() -> None:
    rows = eh.drafts(eh.DRAFTS)
    cases = ev.load(ev.SYNTHETIC)

    for row in rows:
        assert eh.cases_of(cases, row["hypothesis"])
    assert len({row["id"] for row in rows}) == len(rows)
    assert {row.get("trap") for row in rows} >= {"letters-language", "control"}
    # И расплывчатые, и строгие: вывод «расплывчатый» меряется в обе стороны.
    assert {row["loose"] for row in rows} == {True, False}


@pytest.mark.parametrize(
    ("row", "words"),
    [
        ({"id": "a", "hypothesis": "en", "draft": "x"}, "не размечен"),
        ({"id": "a", "hypothesis": "en", "draft": " ", "loose": True}, "нет поля draft"),
        ({"id": "a", "hypothesis": "en", "draft": "x", "loose": True, "trap": "?"}, "ловушка"),
    ],
)
def test_a_badly_written_draft_is_refused_in_words(
    tmp_path: Path, row: dict[str, Any], words: str
) -> None:
    path = tmp_path / "drafts.jsonl"
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    with pytest.raises(ev.SetError, match=words):
        eh.drafts(path)


def test_a_draft_of_an_unknown_hypothesis_is_refused() -> None:
    with pytest.raises(ev.SetError, match="гипотезы «нет такой» нет"):
        eh.cases_of(ev.load(ev.SYNTHETIC), "нет такой")


@pytest.mark.parametrize("rule", sorted(set(eh.RULES) - set(eh.INVERTED)))
def test_spoil_takes_away_exactly_its_rule_line(rule: str) -> None:
    prompt = sh.load_prompt()

    left = eh.spoiled(prompt, rule)

    assert len(left.splitlines()) == len(prompt.splitlines()) - 1
    assert eh.RULES[rule] not in left


@pytest.mark.parametrize("rule", sorted(eh.INVERTED))
def test_an_inverted_spoil_swaps_exactly_its_rule_line_for_the_opposite(rule: str) -> None:
    prompt = sh.load_prompt()
    line = next(line for line in prompt.splitlines() if line.startswith(eh.RULES[rule]))

    left = eh.spoiled(prompt, rule)

    assert len(left.splitlines()) == len(prompt.splitlines())
    assert line not in left
    assert eh.INVERTED[rule] in left


def test_spoil_of_a_rule_the_prompt_has_not_is_refused() -> None:
    with pytest.raises(ev.SetError, match="портить нечего"):
        eh.spoiled("короткий промпт", "language")


def test_the_live_set_needs_a_draft_file_and_gets_one_row_per_hypothesis(tmp_path: Path) -> None:
    cases = [{"hypothesis": "1"}, {"hypothesis": "1"}, {"hypothesis": "2"}]
    with pytest.raises(ev.SetError, match="--draft-file"):
        eh.golden_rows(None, cases)
    draft = tmp_path / "old.txt"
    draft.write_text("  Сайты, связанные с пекарнями  \n", encoding="utf-8")

    rows = eh.golden_rows(draft, cases)

    assert rows == [
        {"id": "golden-1", "hypothesis": "1", "draft": "Сайты, связанные с пекарнями"},
        {"id": "golden-2", "hypothesis": "2", "draft": "Сайты, связанные с пекарнями"},
    ]


async def test_helped_descriptions_in_the_gates_and_right_loose_marks_open_them(
    monkeypatch: pytest.MonkeyPatch, judge: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = eh.drafts(eh.DRAFTS)
    monkeypatch.setattr(ev, "run", judged_by(GOOD))
    helper = FakeHelper(loose={row["draft"]: row["loose"] for row in rows})

    done = await eh.run(rows, ev.load(ev.SYNTHETIC), helper, judge)
    failed = eh.report(done, ev.Gates())

    assert failed == []
    assert [d.draft_id for d in done] == [row["id"] for row in rows]
    shown = capsys.readouterr().out
    assert f"Вывод «расплывчатый» верен: {len(rows)} из {len(rows)}" in shown
    # По черновикам как есть подмена принимает всё — это видно рядом, ворот у них нет.
    assert "Всего, судья по черновикам как есть: ложных «принять» " in shown


async def test_wrong_loose_marks_and_a_silent_helper_close_the_gates(
    monkeypatch: pytest.MonkeyPatch, judge: Any
) -> None:
    rows = eh.drafts(eh.DRAFTS)
    monkeypatch.setattr(ev, "run", judged_by(GOOD))
    first = rows[0]["draft"]
    helper = FakeHelper(loose={row["draft"]: not row["loose"] for row in rows}, silent={first})

    failed = eh.report(await eh.run(rows, ev.load(ev.SYNTHETIC), helper, judge), ev.Gates())

    assert f"помощник не ответил на черновики: {rows[0]['id']}" in failed
    assert any(words.startswith("вывод «расплывчатый» верен на 0%") for words in failed)


async def test_a_judge_out_of_the_gates_on_helped_descriptions_closes_them(
    monkeypatch: pytest.MonkeyPatch, judge: Any
) -> None:
    rows = eh.drafts(eh.DRAFTS)
    monkeypatch.setattr(ev, "run", judged_by("не то описание"))
    helper = FakeHelper(loose={row["draft"]: row["loose"] for row in rows})

    failed = eh.report(await eh.run(rows, ev.load(ev.SYNTHETIC), helper, judge), ev.Gates())

    assert any(words.startswith("ложных «принять»") for words in failed)


def test_main_writes_the_versions_and_says_whether_the_spoil_was_caught(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = eh.drafts(eh.DRAFTS)
    monkeypatch.setattr(ev, "run", judged_by(GOOD))
    made: list[FakeHelper] = []

    def helper(model: str | None = None) -> FakeHelper:
        made.append(FakeHelper(loose={row["draft"]: row["loose"] for row in rows}))
        return made[-1]

    monkeypatch.setattr(eh, "HelperClient", helper)
    out = tmp_path / "run.json"

    code = eh.main(["--spoil", "not-fit", "--out", str(out)])

    assert code == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["helper_prompt_version"] == f"{sh.PROMPT_VERSION}+spoiled-not-fit"
    assert written["failed"] == []
    assert len(written["drafts"]) == len(rows)
    # Порченый промпт ушёл помощнику, и итог честно говорит, что порча не поймана.
    assert made[0].systems[0] == eh.spoiled(sh.load_prompt(), "not-fit")
    assert "ПОРЧА НЕ ПОЙМАНА" in capsys.readouterr().out
