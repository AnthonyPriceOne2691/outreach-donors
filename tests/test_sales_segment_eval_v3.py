"""Eval судьи сегмента под v3: случаи «не видно», посредники, язык, текст главной; порча новых
правил, классы ложных «отказ», доли полос по варианту (b), случаи без разметки и дочитывание
главных тем же кодом, что у прогона.

Модель — «оракул» из `test_sales_segment_eval.py`: отвечает решением человека тем, что разрешает
промпт. Сеть сайтов — подставным транспортом. Домены и тексты выдуманы.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
import pytest
from backend.config import sales as sales_cfg
from backend.features.sales import segment_judge as sj
from backend.features.sales import segment_language as sl
from backend.features.sales.segment_models import Lane
from scripts import eval_sales_segment as ev
from scripts import eval_sales_segment_home as eh
from scripts.eval_sales_segment_metrics import Result, metrics
from tests.test_sales_segment_eval import SYNTHETIC, Capture, Oracle, use

LANGUAGE_CUT = {c["host"] for c in SYNTHETIC if "language" in c["tags"] and c["expect"] == "no"}


@pytest.fixture
def oracle(monkeypatch: pytest.MonkeyPatch) -> Oracle:
    fake = Oracle(SYNTHETIC)
    use(monkeypatch, fake)
    return fake


def test_synthetic_covers_the_v3_rules_in_many_niches_and_scripts() -> None:
    tags = Counter(tag for case in SYNTHETIC for tag in case["tags"])
    for tag in ("unseen", "in-addition", "contradiction", "intermediary", "intermediary-lookalike",
                "home-text", "language", "bilingual", "names", "rtl", "unspaced"):  # fmt: skip
        assert tags[tag] >= 1, tag
    assert tags["intermediary"] >= 5
    assert tags["intermediary-lookalike"] >= 3
    assert tags["unseen"] >= 5
    unseen = {case["hypothesis"] for case in SYNTHETIC if "unseen" in case["tags"]}
    assert len(unseen) >= 5  # «не видно» — в разных нишах и странах, а не под одну
    letters = {case["letters"] for case in SYNTHETIC if "letters" in case}
    assert letters == {"ru", "en"}
    text = " ".join(case["text"] for case in SYNTHETIC if "language" in case["tags"])
    # кириллица (украинская, казахская), латиница (польская), арабское письмо, японское
    assert all(letter in text for letter in "іқłاשღ")
    assert "ソファ" in text
    assert "โรงแรม" in text


def _script(text: str) -> str | None:
    """Главная письменность текста случая."""
    found = Counter(script for char in text if char.isalpha() and (script := sl.script_of(char)))
    return found.most_common(1)[0][0] if found else None


@pytest.mark.parametrize(
    "tag",
    ["unseen", "contradiction", "intermediary", "intermediary-lookalike", "language",
     "foreign-only", "segment-among"],
)  # fmt: skip
def test_every_v3_rule_is_held_in_three_niches_and_three_scripts(tag: str) -> None:
    """Слово владельца: правки универсальные — каждое правило держат случаи из разных ниш и
    письменностей, а не одной ниши или языка живого прогона."""
    cases = [case for case in SYNTHETIC if tag in case["tags"]]

    assert len({case["hypothesis"] for case in cases}) >= 3, tag
    assert len({_script(case["text"]) for case in cases}) >= 3, tag


def test_only_foreign_geography_is_unsure_and_its_pair_with_the_segments_place_is_yes() -> None:
    """Сегмент называет страну работы, а сайт — только другие места: «не понять» (решает человек);
    та же ниша, где место сегмента видно среди других, — «да». Парами, в разных нишах и письменностях."""
    only = [case for case in SYNTHETIC if "foreign-only" in case["tags"]]
    among = [case for case in SYNTHETIC if "segment-among" in case["tags"]]

    assert {case["expect"] for case in only} == {"unsure"}
    assert {case["expect"] for case in among} == {"yes"}
    assert {case["hypothesis"] for case in only} == {case["hypothesis"] for case in among}
    assert len({case["hypothesis"] for case in only}) >= 5


def test_a_language_without_letters_of_its_own_is_unsure_in_the_set(oracle: Oracle) -> None:
    """Латиница и иероглифы при письмах на кириллице — «не понять», а не отказ."""
    unnamed = [case for case in SYNTHETIC if "unnamed" in case["tags"]]

    assert len({case["hypothesis"] for case in unnamed}) >= 2
    assert all(case["expect"] == "no|unsure" for case in unnamed)
    assert ev.main([]) == 0
    assert {case["host"] for case in unnamed}.isdisjoint(oracle.asked)


def test_a_site_in_another_language_never_reaches_the_model(
    oracle: Oracle, capsys: Capture
) -> None:
    assert ev.main([]) == 0

    assert LANGUAGE_CUT
    assert LANGUAGE_CUT.isdisjoint(oracle.asked)
    assert "mebli-plus-sample.com" in oracle.asked  # двуязычный, с переключателем на русский
    assert "Вариант (b):" in capsys.readouterr().out


def test_without_the_language_check_those_sites_go_to_the_model(
    oracle: Oracle, capsys: Capture
) -> None:
    """Замер v2: сверки языка не было — сайт на другом языке судила модель."""
    assert ev.main(["--no-language"]) == 0

    assert set(oracle.asked) >= LANGUAGE_CUT


@pytest.mark.parametrize(
    ("rule", "tag"),
    [("unseen", "unseen"), ("intermediary", "intermediary"), ("foreign", "foreign-only")],
)
def test_a_spoiled_v3_rule_closes_the_gates(
    oracle: Oracle, capsys: Capture, rule: str, tag: str
) -> None:
    """Без «не видно» сайты, где признака сегмента нет, отсечены; без сигналов посредника площадки
    заявок приняты — ворота закрыты, порча поймана словами, ловушки правила — отдельной строкой."""
    assert ev.main(["--spoil", rule]) == 1

    out = capsys.readouterr().out
    assert f"ПОРЧА ПОЙМАНА: без правила «{ev.RULE_WORDS[rule]}»" in out
    traps = sum(tag in case["tags"] for case in SYNTHETIC)
    assert f"({tag}): ложных «принять» " in out
    assert f" из {traps} " in out


def test_the_unseen_spoil_puts_the_v2_country_line_back_instead_of_the_rule() -> None:
    prompt = sj.load_prompt()

    spoiled = ev.without_rule(prompt, "unseen")

    assert ev.RULES["unseen"] not in spoiled
    assert ev.SPOILED_WITH["unseen"] in spoiled.splitlines()
    assert len(spoiled.splitlines()) == len(prompt.splitlines())


def test_the_intermediary_spoil_takes_out_exactly_its_line() -> None:
    prompt = sj.load_prompt()

    spoiled = ev.without_rule(prompt, "intermediary")

    assert "Signs of an intermediary" not in spoiled
    assert len(spoiled.splitlines()) == len(prompt.splitlines()) - 1


def _result(expect: str, lane: Lane, by: str = "model", kind: str | None = None) -> Result:
    return Result("c", "h", expect, lane.value, "", None, by, False, 100, {}, (), kind)


def test_false_rejects_are_split_by_who_cut() -> None:
    results = [
        _result("yes", Lane.REJECT, "language"),
        _result("yes", Lane.REJECT, "model", "intermediary"),
        _result("yes|unsure", Lane.REJECT, "model", "own"),
        _result("yes", Lane.REJECT, "rule"),
        _result("yes", Lane.ACCEPT),
        _result("no", Lane.REJECT, "language"),  # верный отказ — не в счёт
    ]

    found = metrics(results)

    assert found["false_reject_by"] == {"segment": 1, "language": 1, "intermediary": 1, "rule": 1}
    assert found["false_reject"] == pytest.approx(4 / 5)


def test_lane_shares_count_every_case_and_accuracy_only_labeled_ones() -> None:
    """Вариант (b): «на проверку» — только сомнительные, отказы судьи (модель и язык) — своей
    долей; в нынешнем наблюдении на проверку идут и те и другие. Отсев правила — ни там ни там."""
    results = [
        _result("yes", Lane.ACCEPT),
        _result("", Lane.ACCEPT),  # без разметки: судья принял сам, человек не смотрел
        _result("no", Lane.REJECT, "model", "intermediary"),
        _result("no", Lane.REJECT, "language"),
        _result("unsure", Lane.REVIEW),
        _result("no", Lane.REJECT, "rule"),
    ]

    found = metrics(results)

    assert (found["cases"], found["labeled"]) == (6, 5)
    assert found["to_human"] == pytest.approx(1 / 6)
    assert found["would_reject"] == pytest.approx(2 / 6)
    assert found["to_human_shadow"] == pytest.approx(3 / 6)
    assert found["unsure"] == pytest.approx(1 / 5)
    assert found["accept_recall"] == 1.0
    # Вызовы модели — и по сайтам без разметки; язык и правило модель не зовут.
    assert (found["model_calls"], found["tokens_per_call"]) == (4, 150)


# --- набор без разметки и язык набора ------------------------------------------------------------


def _set(tmp_path: Path, cases: list[dict[str, Any]]) -> Path:
    path = tmp_path / "sales_segment_golden.jsonl"
    path.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), "utf-8")
    return path


def test_an_unlabeled_case_is_judged_only_with_a_tag_the_manifest_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Capture
) -> None:
    """Сайты, которые человек не смотрел (судья принял их сам), судятся ради долей и токенов, но в
    точность не идут; язык писем набора — из манифеста."""
    own = [c for c in SYNTHETIC if c["hypothesis"] == "ru-furniture-lang"]
    cases = []
    for n in range(10):
        for case in own:
            copy = {key: value for key, value in case.items() if key != "letters"}
            copy |= {"id": f"{case['id']}-{n}", "host": f"n{n}-{case['host']}"}
            if case["expect"] == "yes" and n % 2:
                copy |= {"expect": None, "tags": ["model-yes-unreviewed"]}
            cases.append(copy)
    path = _set(tmp_path, cases)
    spec = json.loads(ev.MANIFEST.read_text(encoding="utf-8")) | {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "count": len(cases),
        "letters": "ru",
        "unlabeled_tags": ["model-yes-unreviewed"],
    }
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(spec), encoding="utf-8")
    monkeypatch.setattr(ev, "MANIFEST", manifest)
    oracle = Oracle(cases)
    use(monkeypatch, oracle)
    out_file = tmp_path / "run.json"

    assert ev.main(["--golden", "--out", str(out_file)], golden_dir=str(tmp_path)) == 0

    found = json.loads(out_file.read_text(encoding="utf-8"))["metrics"]
    assert (found["cases"], found["labeled"]) == (40, 30)
    assert found["false_reject"] == 0
    assert "язык писем: ru" in capsys.readouterr().out
    assert not [host for host in oracle.asked if "mebli-dim" in host or "zhihaz" in host]


# --- текст главной к набору ----------------------------------------------------------------------


def test_the_home_text_is_added_from_the_fetched_file_with_the_judges_cap(
    oracle: Oracle, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = next(c for c in SYNTHETIC if c["id"] == "law-in-addition")
    homes = tmp_path / "homes.jsonl"
    rows = [
        {"id": case["id"], "reached": True, "text": "Юрист Плюс · Ведём дела компаний · Запись"},
        {"id": "law-contradiction", "reached": False, "text": "не открылась"},
    ]
    homes.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")
    monkeypatch.setattr(sales_cfg, "SEGMENT_HOME_TEXT_CHARS", 1500)

    assert ev.main(["--home", str(homes), "--home-chars", "20"]) == 0

    [added] = [user for user in oracle.users if "yurist-plus-sample.com" in user]
    assert f"{sj.HOME_TEXT}: Юрист Плюс · Ведём" in added
    assert "Ведём дела компаний · Запись" not in added.split(f"{sj.HOME_TEXT}: ", 1)[1]
    [closed] = [user for user in oracle.users if "advokat-norma-sample.com" in user]
    assert sj.HOME_TEXT not in closed


def test_no_home_takes_the_home_text_out(oracle: Oracle) -> None:
    assert ev.main(["--no-home"]) == 0

    assert not [user for user in oracle.users if f"{sj.HOME_TEXT}:" in user]


def test_the_prompt_file_is_what_the_model_is_asked_with(
    oracle: Oracle, tmp_path: Path, capsys: Capture
) -> None:
    old = tmp_path / "v2.md"
    old.write_text(sj.load_prompt().replace("Rules:", "Rules (old):"), encoding="utf-8")
    out = tmp_path / "run.json"

    ev.main(["--prompt-file", str(old), "--out", str(out)])

    assert oracle.systems
    assert set(oracle.systems) == {old.read_text(encoding="utf-8").strip()}
    assert json.loads(out.read_text(encoding="utf-8"))["prompt_version"] == "file:v2.md"


def test_a_missing_homes_file_is_red_in_words(capsys: Capture) -> None:
    assert ev.main(["--home", "/nonexistent/homes.jsonl"]) == 1

    assert "НАБОР НЕ ПРОЧИТАН: файла главных нет" in capsys.readouterr().out


# --- дочитывание главных: тем же `check_home`, что у прогона -------------------------------------

PAGE = """<html lang="ru"><head><title>Юрист Плюс — юридические услуги</title>
<meta name="description" content="Ведём дела компаний"><script>var x = 1;</script>
<style>.a{color:red}</style></head><body><header>Юрист Плюс · +000 00 00</header>
<nav><a href="/">Главная</a><a href="/uslugi">Услуги</a></nav>
<main><h1>Юридические услуги для бизнеса</h1><p>Договоры, споры и регистрация компаний.</p>
<p>Договоры, споры и регистрация компаний.</p><svg><text>logo</text></svg></main></body></html>"""
STUB = "<html><head><title>Just a moment...</title></head><body>Checking your browser</body></html>"


def test_visible_text_drops_code_menus_and_repeats() -> None:
    text = eh.visible_text(PAGE)

    assert text == (
        "Юрист Плюс · +000 00 00 · Юридические услуги для бизнеса · "
        "Договоры, споры и регистрация компаний."
    )


def test_homes_are_fetched_by_the_runs_own_home_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Capture
) -> None:
    pages = {"yurist-plus-sample.com": PAGE, "advokat-norma-sample.com": STUB}

    def answer(request: httpx.Request) -> httpx.Response:
        html = pages.get(request.url.host.removeprefix("www."))
        if html is None or request.url.path not in ("", "/"):
            return httpx.Response(404, text="нет", request=request)
        return httpx.Response(200, html=html, request=request)

    def client(**kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("timeout", None)
        return httpx.AsyncClient(transport=httpx.MockTransport(answer), **kwargs)

    monkeypatch.setattr(eh, "guarded_client", client)
    cases = [
        c for c in SYNTHETIC if c["id"] in ("law-in-addition", "law-contradiction", "law-unseen")
    ]
    out = tmp_path / "homes.jsonl"

    assert eh.fetched_to(cases, out) == 0

    rows = {row["id"]: row for row in map(json.loads, out.read_text("utf-8").splitlines())}
    assert rows["law-in-addition"]["reached"] is True
    assert rows["law-in-addition"]["text"] == eh.visible_text(PAGE)
    assert (rows["law-contradiction"]["reached"], rows["law-contradiction"]["text"]) == (False, "")
    assert "заглушка" in rows["law-contradiction"]["error"]
    assert (rows["law-unseen"]["reached"], rows["law-unseen"]["text"]) == (False, "")
    assert "Главных дочитано: 1 из 3, с текстом 1" in capsys.readouterr().out
