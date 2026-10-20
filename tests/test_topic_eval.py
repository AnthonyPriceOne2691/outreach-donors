"""Eval тематики: набор годен, метрики считаются верно, ворота закрываются.

Модель здесь не зовётся: ответы записаны руками (`test_topic_eval_answers.json`) — это
проверка механики оценки, а не замер модели. Живой прогон — `python scripts/eval_topic.py`
с ключом, по слову владельца.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
from backend.config import llm as llm_cfg
from backend.config import topics as topics_cfg
from backend.features.topics.taxonomy import BY_CODE
from scripts import eval_topic as ev

ANSWERS = Path(__file__).with_name("test_topic_eval_answers.json")


@pytest.fixture(autouse=True)
def _no_real_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ключ из `.env` разработчика в оценку не идёт: записанный клиент в сеть не ходит,
    но запрос собирается с ключом — пусть он будет заведомо не настоящим."""
    monkeypatch.setattr(llm_cfg, "API_KEY", "sk-recorded")


def test_the_set_is_multilingual_and_covers_every_topic() -> None:
    cases = ev.load(ev.DATA)

    assert len(cases) >= ev.MIN_CASES
    assert len({case.lang for case in cases}) >= ev.MIN_LANGS
    assert {case.expect for case in cases} >= set(BY_CODE), "у каждой темы есть случай"
    assert len({case.host for case in cases}) == len(cases), "домен — ключ записанного ответа"
    assert all(case.host.endswith(".example") for case in cases), "репозиторий публичный"
    assert all(" " not in case.host for case in cases)


def test_the_set_reads_a_lone_home_title_like_the_backfill() -> None:
    """«Только заголовок» — тот же текст, что досуд берёт у домена без открытой главной:
    и с темой по заголовку, и с одним названием, по которому темы не понять."""
    lone = [
        case
        for case in ev.load(ev.DATA)
        if case.text.source == "home" and "\n" not in case.text.text
    ]
    assert {case.expect for case in lone} >= {"none", "finance"}
    assert len(lone) >= 4
    assert all(case.text.text.startswith("Главная: ") for case in lone)


def _run(tmp_path: Path, answers: dict[str, str], *extra: str) -> tuple[int, dict[str, object]]:
    recorded = tmp_path / "answers.json"
    recorded.write_text(json.dumps(answers, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "report.json"
    code = ev.main(["--answers", str(recorded), "--out", str(out), *extra])
    return code, json.loads(out.read_text(encoding="utf-8"))


def _recorded() -> dict[str, str]:
    return dict(json.loads(ANSWERS.read_text(encoding="utf-8")))


def test_recorded_answers_pass_the_gates_with_their_numbers(tmp_path: Path) -> None:
    code, made = _run(tmp_path, _recorded())

    assert code == ev.EXIT_PASSED
    assert made["cases"] == 52
    assert made["primary"] == pytest.approx(48 / 52, abs=1e-4)
    assert made["either"] == pytest.approx(51 / 52, abs=1e-4)
    assert made["by_language"]["en"] == 1.0
    assert made["by_language"]["ru"] == pytest.approx(8 / 9, abs=1e-4)
    assert (made["unanswered"], made["unreadable"]) == (0, 0)
    assert (made["tokens"], made["prompt_tokens"], made["completion_tokens"]) == (
        78_000,
        65_000,
        13_000,
    )
    missed = {miss["id"]: miss for miss in made["misses"]}  # type: ignore[union-attr]
    assert set(missed) == {
        "ru_health_fitness",
        "es_family",
        "de_society_ngo",
        "fr_news_sport_story",
    }
    assert missed["es_family"]["either_right"] is True, "вторая тема попала"
    assert missed["fr_news_sport_story"]["either_right"] is False


def test_label_instead_of_code_and_broken_json_still_count(tmp_path: Path) -> None:
    """«Finance» вместо кода и кавычки внутри причины — та же тема, что в прогоне."""
    answers = _recorded()

    _, made = _run(tmp_path, answers)

    missed = {miss["id"] for miss in made["misses"]}  # type: ignore[union-attr]
    assert "en_finance_insurance" not in missed
    assert "ru_news_city" not in missed


def test_a_broad_answer_closes_the_gates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Модель, отвечающая «обо всём» на всё, — ворота закрыты, и сказано чем."""
    everything = {
        key: json.dumps({"topic": "general", "secondary": None, "why": "разное"})
        for key in _recorded()
    }

    code, made = _run(tmp_path, everything)

    assert code == ev.EXIT_GATES
    assert made["primary"] < 0.1
    printed = capsys.readouterr().out
    assert "ВОРОТА ЗАКРЫТЫ: основная верна в" in printed
    assert "язык en:" in printed


@pytest.mark.parametrize(
    "answer",
    ["Это страница входа в личный кабинет.", '{"topic": "login page"}', '{"why": "вход"}'],
)
def test_a_broken_answer_is_not_an_unclear_hit_and_closes_the_gates(
    tmp_path: Path, answer: str
) -> None:
    """На случае «не понять» сбой формы — не попадание: ворота видят его своим числом."""
    answers = {**_recorded(), "ru_unclear_login": answer}

    code, made = _run(tmp_path, answers)

    assert code == ev.EXIT_GATES
    assert (made["unreadable"], made["unanswered"]) == (1, 0)
    assert "ответ не по форме у 1" in " ".join(made["gates"])  # type: ignore[arg-type]
    missed = {miss["id"]: miss for miss in made["misses"]}  # type: ignore[union-attr]
    assert missed["ru_unclear_login"]["unreadable"] is True


def test_a_silent_model_is_not_a_hit_and_closes_the_gates(tmp_path: Path) -> None:
    answers = _recorded()
    del answers["en_pets"]

    code, made = _run(tmp_path, answers)

    assert code == ev.EXIT_GATES
    assert made["unanswered"] == 1
    assert "нет записи" in str(made["unanswered_reason"])
    assert "модель не ответила по 1" in " ".join(made["gates"])  # type: ignore[arg-type]


def test_a_small_set_is_refused_not_scored(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    lines = ev.DATA.read_text(encoding="utf-8").splitlines()[:3]
    small = tmp_path / "small.jsonl"
    small.write_text("\n".join(lines), encoding="utf-8")

    assert ev.main([str(small), "--answers", str(ANSWERS)]) == ev.EXIT_SET
    assert "набор мал: 3 случаев" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("broken", "said"),
    [
        ({"expect": "cooking"}, "темы нет в списке — cooking"),
        ({"serp": None, "home": None}, "текста для модели нет"),
        ({"host": None}, "случай не разобран — нет host"),
        ({"serp": "строка"}, "случай не разобран"),
    ],
)
def test_a_broken_case_is_named(tmp_path: Path, broken: dict[str, object], said: str) -> None:
    rows = [json.loads(line) for line in ev.DATA.read_text(encoding="utf-8").splitlines()]
    rows[0] = {**rows[0], **broken}
    bad = tmp_path / "bad.jsonl"
    bad.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), "utf-8")

    with pytest.raises(ev.SetError, match=said):
        ev.load(bad)


def test_a_repeated_case_and_a_missing_file_are_named(tmp_path: Path) -> None:
    lines = ev.DATA.read_text(encoding="utf-8").splitlines()
    twice = tmp_path / "twice.jsonl"
    twice.write_text("\n".join([*lines, lines[0]]), encoding="utf-8")

    with pytest.raises(ev.SetError, match="записаны дважды: en_news_money"):
        ev.load(twice)
    with pytest.raises(ev.SetError, match="набора нет"):
        ev.load(tmp_path / "absent.jsonl")


def test_effort_is_taken_for_the_measurement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(topics_cfg, "REASONING_EFFORT", "low")

    code, _ = _run(tmp_path, _recorded(), "--effort", "minimal")

    assert code == ev.EXIT_PASSED
    assert topics_cfg.REASONING_EFFORT == "minimal"
    assert Counter(ev.EFFORTS)["minimal"] == 1
