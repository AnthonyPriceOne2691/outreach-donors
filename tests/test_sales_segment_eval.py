"""Eval судьи сегмента сбора базы (волна В3в): синтетика, ворота, порча промпта, внешний набор
по манифесту и заготовка разметки из прогона.

Живая модель здесь не зовётся: её заменяет подставной транспорт — «оракул», который знает
решение человека, но отвечает только тем, что разрешает промпт: без правила дословной цитаты
он пересказывает, без определения «no» говорит «unsure», без правила v2 «сайт о бизнесах вида —
не бизнес вида» принимает справочник под нестрогим описанием («сайты, связанные с …») — как
модель на калибровке. Так порча промпта видна без сети, а цитата проверяется тем же кодом
судьи (`segment_judge.parse`). Живой прогон на наборе — координатор по слову владельца, числа —
в verify-report.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import httpx
import pytest
from backend.features.core.models.domain import DomainModel
from backend.features.sales import segment_judge as sj
from backend.features.sales.segment_judge import SegmentClient, SiteText
from backend.features.sales.segment_models import Lane, SalesSegmentCandidateModel
from backend.features.sales.segment_window import SPLIT_TOKENS
from scripts import eval_sales_segment as ev
from scripts.eval_sales_segment_metrics import CLASSES
from scripts.eval_sales_segment_sets import cases_of_run
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_segment_judge import MODEL_KEY
from tests.test_sales_segment_run import hypothesis, make_run

Capture = pytest.CaptureFixture[str]
SYNTHETIC = ev.load(ev.SYNTHETIC)
#: Двуязычные сайты: модель их судит, но и сверка языка их могла бы задеть — в «отсечённые
#: моделью» их не берём, чтобы тест мерил одно.
LANGUAGE_FREE = {case["host"] for case in SYNTHETIC if "language" in case["tags"]}


class Oracle:
    """Подставная модель: решение человека — тем, что разрешает промпт. Случай — по домену и
    описанию: один сайт под строгим и нестрогим описанием — два случая."""

    def __init__(
        self,
        cases: Iterable[dict[str, Any]],
        wrong: dict[str, str] | None = None,
        usage: dict[str, Any] | None = None,
    ):
        self.cases = {(case["host"], case["segment"].strip()): case for case in cases}
        self.wrong = wrong or {}
        #: `usage` ответа — как назвал бы провайдер; по умолчанию только итог.
        self.usage = usage or {"total_tokens": 211}
        self.asked: list[str] = []
        #: Усилие рассуждения каждого запроса.
        self.efforts: list[str | None] = []
        #: Промпт и текст сайта каждого запроса.
        self.systems: list[str] = []
        self.users: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        system, user = (m["content"] for m in payload["messages"])
        host = user.split("\n", 1)[0].removeprefix("Domain: ")
        segment = user.split(f"{sj.SEGMENT_OPEN}\n", 1)[1].split(f"\n{sj.SEGMENT_CLOSE}", 1)[0]
        self.asked.append(host)
        self.efforts.append(payload.get("reasoning_effort"))
        self.systems.append(system)
        self.users.append(user)
        case = self.cases[host, segment]
        # Сайт без разметки — тот, что судья принял сам и человек не смотрел.
        fit = self.wrong.get(host, (case["expect"] or "yes").split("|")[0])
        if {"about-businesses", "loose"} <= set(case["tags"]) and ev.RULES["about"] not in system:
            fit = "yes"  # без правила v2 нестрогое описание принимает справочник, как на калибровке
        if "unseen" in case["tags"] and ev.RULES["unseen"] not in system:
            fit = "no"  # без «не видно» признак сегмента, которого нет в тексте, режет — как v2
        if "intermediary" in case["tags"] and ev.RULES["intermediary"] not in system:
            fit = "yes"  # без сигналов посредника площадка заявок проходит за своё дело
        if "foreign-only" in case["tags"] and ev.RULES["foreign"] not in system:
            fit = "yes"  # без подпункта чужая география — «не видно», как у v3 на живом наборе
        if f'- "{fit}": ' not in system:
            fit = "unsure"  # слова, которого промпт не определяет, оракул не говорит
        quote = case["evidence"]
        if ev.RULES["quote"] not in system:
            quote = " ".join(reversed(quote.split()))  # без правила — пересказ, а не цитата
        kind = "intermediary" if "intermediary" in case["tags"] else "own"
        body = {"kind": kind, "fit": fit, "quote": quote, "why": "по тексту сайта"}
        answer = {"choices": [{"message": {"content": json.dumps(body)}}], "usage": self.usage}
        return httpx.Response(200, json=answer, request=request)


def use(monkeypatch: pytest.MonkeyPatch, oracle: Oracle) -> None:
    def client(model: str | None = None) -> SegmentClient:
        http = httpx.AsyncClient(transport=httpx.MockTransport(oracle))
        return SegmentClient(http, model=model or "gpt-5-mini", api_key=MODEL_KEY)

    monkeypatch.setattr(ev, "SegmentClient", client)


@pytest.fixture
def oracle(monkeypatch: pytest.MonkeyPatch) -> Oracle:
    fake = Oracle(SYNTHETIC)
    use(monkeypatch, fake)
    return fake


# --- синтетика --------------------------------------------------------------------------------


def test_synthetic_set_covers_niches_languages_every_decision_and_the_rules() -> None:
    """Правки — под любые ниши и языки: набор многонишевый (калибровка 15.10 и свои ловушки под
    нестрогим описанием), с контрольными сегментами, которые сами просят справочники и площадки."""
    assert len({case["id"] for case in SYNTHETIC}) == len(SYNTHETIC) >= 60
    assert len({case["hypothesis"] for case in SYNTHETIC}) >= 10
    assert {"yes", "no", "unsure", "no|unsure"} <= {case["expect"] for case in SYNTHETIC}
    tags = Counter(tag for case in SYNTHETIC for tag in case["tags"])
    assert {"injection", "platform", "public-zone", "empty", "denied", "control"} <= set(tags)
    kinds = {"directory", "map", "registry", "company-check", "marketplace", "classifieds",
             "exchange", "tenders", "jobs", "aggregator", "booking-aggregator", "news", "blog",
             "review"}  # fmt: skip
    assert kinds <= set(tags)
    loose = [c for c in SYNTHETIC if {"about-businesses", "loose"} <= set(c["tags"])]
    assert len(loose) >= 8
    assert len({c["segment"] for c in loose}) >= 4
    # Письменности сайтов: латиница, кириллица, казахская кириллица, турецкая и польская латиница.
    text = " ".join(case["text"] for case in SYNTHETIC)
    assert all(letter in text for letter in "ąöüñşçіұқә")


def test_evidence_is_verbatim_in_one_line_of_the_site_text() -> None:
    """Слова решения — дословно из текста сайта: иначе оракул учил бы судью выдумывать."""
    for case in SYNTHETIC:
        if case["evidence"]:
            assert SiteText.parse(case["text"]).holds(case["evidence"]), case["id"]
            # Короче — «на проверку» по правилу цитаты, и оракул учил бы судью не тому.
            assert sj.quote_words(case["evidence"]) >= sj.MIN_QUOTE_WORDS, case["id"]
        # Домены выдуманы: «-sample.», выдуманная зона `.zz` или платформа из правила по имени.
        assert re.fullmatch(r"[a-z0-9-]+-sample(\.[a-z]{2,})+|[a-z0-9.-]+\.zz|youtube\.com",
                            case["host"]), case["id"]  # fmt: skip


# --- ворота --------------------------------------------------------------------------------------


def test_eval_is_green_when_the_judge_agrees_with_the_human(
    oracle: Oracle, capsys: Capture
) -> None:
    assert ev.main([]) == 0

    out = capsys.readouterr().out
    assert "ЛОЖНЫХ «ПРИНЯТЬ»: 0" in out
    assert f"Версия промпта: {sj.PROMPT_VERSION}" in out
    # Правила до модели модель не спрашивают: платформа, госзона, пустое, отказ доступа.
    ruled = {case["host"] for case in SYNTHETIC if "rule" in case["tags"]}
    assert ruled.isdisjoint(oracle.asked)


def test_spoiled_quote_rule_closes_the_gates(oracle: Oracle, capsys: Capture) -> None:
    """Обратный прогон: снято правило дословной цитаты — пересказ уходит человеку, eval красный."""
    assert ev.main(["--spoil"]) == 1

    out = capsys.readouterr().out
    assert "ПОРЧА ПРОМПТА: снято правило «дословная цитата»" in out
    # Пересказ уходит человеку: принятых нет, кроме пар «да|не понять», где «на проверку» — верно.
    pairs = sum(case["expect"] == "yes|unsure" for case in SYNTHETIC)
    fits = sum("yes" in case["expect"].split("|") for case in SYNTHETIC)
    assert f"полнота «принять» {pairs / fits:.0%} < 70%" in out.split("ВОРОТА ЗАКРЫТЫ:")[1]


def test_spoiled_no_definition_closes_the_gates(oracle: Oracle, capsys: Capture) -> None:
    assert ev.main(["--spoil", "no"]) == 1

    assert "полнота «отказ»" in capsys.readouterr().out


def test_a_misfit_accepted_without_a_human_closes_the_gates(
    monkeypatch: pytest.MonkeyPatch, capsys: Capture
) -> None:
    """Новости, обзор, доска объявлений, сервис сравнения и площадка заявок, уверенно принятые с
    настоящей цитатой, — стали бы лидами без человека. Пяти хватает, чтобы перейти 5 % на нынешнем
    наборе."""
    hosts = ("pedal-weekly-sample.org", "softcompare-sample.com", "pisoportal-sample.es")
    hosts += ("denticompara-sample.com", "yurist-zayavka-sample.com")
    use(monkeypatch, Oracle(SYNTHETIC, wrong=dict.fromkeys(hosts, "yes")))

    assert ev.main([]) == 1

    out = capsys.readouterr().out
    assert (
        "ЛОЖНЫХ «ПРИНЯТЬ»: 5 ['en-news-1', 'saas-no-review', 're-no-portal', 'law-requests', "
        "'mx-dental-booking']" in out
    )
    others = sum("yes" not in case["expect"].split("|") for case in SYNTHETIC)
    assert len(hosts) / others > ev.Gates().false_accept_max
    assert f"ложных «принять» {len(hosts) / others:.0%} > 5%" in out


def test_a_fit_cut_off_closes_the_gates(monkeypatch: pytest.MonkeyPatch, capsys: Capture) -> None:
    """Подходящие сайты, отсечённые моделью, — потерянные лиды: их чуть больше 10 % — ворота закрыты."""
    fits = [case for case in SYNTHETIC if "yes" in case["expect"].split("|")]
    need = int(len(fits) * ev.Gates().false_reject_max) + 1
    hosts = [
        case["host"] for case in fits if case["expect"] == "yes" and "rule" not in case["tags"]
    ]
    hosts = [host for host in hosts if host not in LANGUAGE_FREE][:need]
    use(monkeypatch, Oracle(SYNTHETIC, wrong=dict.fromkeys(hosts, "no")))

    assert ev.main([]) == 1

    assert f"ложных «отказ» {need / len(fits):.0%} > 10%" in capsys.readouterr().out


def test_spoiled_v2_rule_lets_directories_in_and_closes_the_gates(
    oracle: Oracle, capsys: Capture
) -> None:
    """Обратный прогон правила v2: без него справочники, карты, реестры и агрегаторы под
    нестрогим описанием приняты — ложные «принять» выше ворот, порча поймана словами."""
    assert ev.main(["--spoil", "about"]) == 1

    out = capsys.readouterr().out
    assert "ПОРЧА ПРОМПТА: снято правило «сайт о бизнесах вида — не бизнес вида»" in out
    assert "ПОРЧА ПОЙМАНА: без правила «сайт о бизнесах вида — не бизнес вида»" in out
    loose = [c["id"] for c in SYNTHETIC if {"about-businesses", "loose"} <= set(c["tags"])]
    traps = sum("about-businesses" in c["tags"] for c in SYNTHETIC)
    assert f"(about-businesses): ложных «принять» {len(loose)} из {traps} {loose}" in out
    assert "ложных «принять»" in out.split("ВОРОТА ЗАКРЫТЫ:")[1]


def test_with_the_v2_rule_the_traps_are_rejected(oracle: Oracle, capsys: Capture) -> None:
    assert ev.main([]) == 0

    traps = sum("about-businesses" in c["tags"] for c in SYNTHETIC)
    assert f"(about-businesses): ложных «принять» 0 из {traps}" in capsys.readouterr().out


def test_a_spoil_the_set_does_not_feel_is_said_in_words(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Capture
) -> None:
    """Набор без ловушек правила порчу не ловит — итог говорит это словами, а не молчит."""
    strict = [case for case in SYNTHETIC if "loose" not in case["tags"]]
    path = tmp_path / "strict.jsonl"
    path.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in strict), "utf-8")
    use(monkeypatch, Oracle(strict))

    assert ev.main(["--spoil", "about", str(path)]) == 0

    assert "ПОРЧА НЕ ПОЙМАНА: без правила «сайт о бизнесах вида — не бизнес вида»" in (
        capsys.readouterr().out
    )


def test_the_v2_spoil_takes_out_exactly_the_v2_rule_line() -> None:
    """Без правила v2 промпт — ровно прежний: тот же прогон на наборе даёт замер v1."""
    prompt = sj.load_prompt()

    spoiled = ev.without_rule(prompt, "about")

    assert "what it writes about" not in spoiled
    kept = [line for line in prompt.splitlines() if not line.startswith("- Judge what the site")]
    assert spoiled.splitlines() == kept
    assert len(kept) == len(prompt.splitlines()) - 1


# --- «не уверен»: пара ответов, любой верен --------------------------------------------------


def _result(expect: str, lane: Lane) -> ev.Result:
    return ev.Result("c", "h", expect, lane.value, "", None, "model", False, 0)


@pytest.mark.parametrize(
    ("expect", "lane", "right", "false_accept", "false_reject"),
    [
        ("yes|unsure", Lane.ACCEPT, True, False, False),
        ("yes|unsure", Lane.REVIEW, True, False, False),
        ("yes|unsure", Lane.REJECT, False, False, True),
        ("no|unsure", Lane.REJECT, True, False, False),
        ("no|unsure", Lane.REVIEW, True, False, False),
        ("no|unsure", Lane.ACCEPT, False, True, False),
        ("unsure", Lane.ACCEPT, False, True, False),
        ("yes", Lane.REVIEW, False, False, False),
    ],
)
def test_either_answer_of_a_pair_is_right(
    expect: str, lane: Lane, right: bool, false_accept: bool, false_reject: bool
) -> None:
    found = _result(expect, lane)

    assert (found.right, found.false_accept, found.false_reject) == (
        right,
        false_accept,
        false_reject,
    )


def test_pairs_count_as_hits_in_recall_and_by_their_yes_or_no_in_the_false_shares() -> None:
    """`yes|unsure` — в знаменателе полноты «принять» и ложных «отказ», `no|unsure` — полноты
    «отказ» и ложных «принять». «На проверку» по паре — попадание, а не промах."""
    results = [
        _result("yes", Lane.ACCEPT),
        _result("yes|unsure", Lane.REVIEW),
        _result("yes|unsure", Lane.REJECT),
        _result("no", Lane.REJECT),
        _result("no|unsure", Lane.REVIEW),
        _result("no|unsure", Lane.ACCEPT),
        _result("unsure", Lane.REVIEW),
    ]

    found = ev.metrics(results)

    assert found["accept_recall"] == pytest.approx(2 / 3)
    assert found["false_reject"] == pytest.approx(1 / 3)
    assert found["reject_recall"] == pytest.approx(2 / 3)
    assert found["false_accept"] == pytest.approx(1 / 4)


def test_the_set_takes_pairs_with_unsure_and_refuses_other_pairs(tmp_path: Path) -> None:
    path = tmp_path / "set.jsonl"
    for expect, fine in (("yes|unsure", True), ("no|unsure", True), ("yes|no", False)):
        case = dict(SYNTHETIC[0], expect=expect)
        path.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")
        if fine:
            assert ev.load(path)[0]["expect"] == expect
        else:
            with pytest.raises(ev.SetError, match="не размечен"):
                ev.load(path)


def test_spoiling_a_rule_the_prompt_does_not_have_is_refused() -> None:
    with pytest.raises(ev.SetError, match="портить нечего"):
        ev.without_rule("no rules here", "quote")


def test_the_run_is_written_to_a_file(oracle: Oracle, tmp_path: Path, capsys: Capture) -> None:
    out = tmp_path / "run.json"

    assert ev.main(["--out", str(out)]) == 0

    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["prompt_version"] == sj.PROMPT_VERSION
    assert written["metrics"]["false_accept"] == 0
    assert len(written["results"]) == len(SYNTHETIC)
    assert "Итог записан" in capsys.readouterr().out


# --- замер до правки продукта: усилие рассуждения и токены раздельно ----------------------------


def test_the_eval_asks_with_the_effort_named_and_the_product_keeps_its_own(
    oracle: Oracle, tmp_path: Path, capsys: Capture
) -> None:
    """`--effort minimal` — тот же набор при другом усилии: модель спрошена с ним, итог называет
    его, а судья прогона после eval — с прежним."""
    product = sj.REASONING_EFFORT
    out = tmp_path / "run.json"

    assert ev.main(["--effort", "minimal", "--out", str(out)]) == 0

    assert set(oracle.efforts) == {"minimal"}
    assert json.loads(out.read_text(encoding="utf-8"))["reasoning_effort"] == "minimal"
    assert f"Усилие рассуждения: minimal (у судьи прогона — {product})" in capsys.readouterr().out
    assert product == sj.REASONING_EFFORT


def test_without_the_flag_the_eval_asks_with_the_products_effort(oracle: Oracle) -> None:
    assert ev.main([]) == 0

    assert set(oracle.efforts) == {sj.REASONING_EFFORT}


def test_the_products_effort_comes_back_when_the_eval_breaks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    product = sj.REASONING_EFFORT

    def broken(*_args: object) -> None:
        raise RuntimeError("сеть оборвалась")

    monkeypatch.setattr(ev, "_evaluate", broken)

    with pytest.raises(RuntimeError, match="сеть оборвалась"):
        ev.main(["--effort", "minimal"])
    assert product == sj.REASONING_EFFORT


@pytest.mark.parametrize(
    ("usage", "split"),
    [
        (
            {
                "prompt_tokens": 180,
                "completion_tokens": 31,
                "total_tokens": 211,
                "completion_tokens_details": {"reasoning_tokens": 24},
            },
            (180, 31, 0, 24),
        ),
        ({"total_tokens": 211}, (0, 0, 211, 0)),
    ],
    ids=["split", "total-only"],
)
def test_the_eval_counts_the_tokens_in_total_and_split(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    usage: dict[str, Any],
    split: tuple[int, int, int, int],
) -> None:
    """Ввод, вывод, «раздельно неизвестно», рассуждение — суммой по набору: сравнить усилия
    можно не только воротами, но и тем, сколько вывода ушло на рассуждение."""
    oracle = Oracle(SYNTHETIC, usage=usage)
    use(monkeypatch, oracle)
    out = tmp_path / "run.json"

    assert ev.main(["--out", str(out)]) == 0

    found = json.loads(out.read_text(encoding="utf-8"))["metrics"]
    calls = len(oracle.asked)
    assert found["tokens"] == 211 * calls
    assert tuple(found[key] for key in SPLIT_TOKENS) == tuple(part * calls for part in split)


# --- внешний набор: вне репозитория, сверка с манифестом ---------------------------------------


def _golden(tmp_path: Path, per: int) -> tuple[Path, list[dict[str, Any]]]:
    """Набор из синтетики: `per` сайтов на гипотезу, свои номера и домены."""
    cases = []
    for name in ("en", "cis"):
        own = [case for case in SYNTHETIC if case["hypothesis"] == name]
        for n in range(per):
            case = dict(own[n % len(own)])
            case["id"] = f"{case['id']}-{n}"
            if case["host"] not in ("youtube.com", "transport.gov.zz"):
                case["host"] = f"n{n}-{case['host']}"
            cases.append(case)
    path = tmp_path / "sales_segment_golden.jsonl"
    path.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), "utf-8")
    return path, cases


def _manifest(tmp_path: Path, **fields: Any) -> Path:
    spec = json.loads(ev.MANIFEST.read_text(encoding="utf-8")) | fields
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path


def test_the_golden_set_is_read_by_the_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Capture
) -> None:
    path, cases = _golden(tmp_path, 41)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(ev, "MANIFEST", _manifest(tmp_path, sha256=digest, count=len(cases)))
    use(monkeypatch, Oracle(cases))

    assert ev.main(["--golden"], golden_dir=str(tmp_path)) == 0

    assert f"sha256 {digest}" in capsys.readouterr().out


#: Поле манифеста, которое портит случай; `None` — каждое поле закреплено верно.
PINS: dict[str, tuple[int, dict[str, Any], str]] = {
    "hash": (41, {"sha256": "0" * 64}, "набор не тот, что в манифесте: sha256"),
    "count": (41, {"count": 7}, "набор не тот, что в манифесте: случаев 82 ≠ 7"),
    "too-small": (
        39,
        {},
        "набор мал: {'en': 39, 'cis': 39} — на гипотезу нужно не меньше 40 сайтов",
    ),
    "unpinned-hash": (41, {"sha256": None}, "набор не закреплён в манифесте: впишите count 82"),
    "unpinned-count": (41, {"count": None}, "набор не закреплён в манифесте"),
}


@pytest.mark.parametrize("case", sorted(PINS))
def test_a_golden_set_other_than_the_manifest_says_is_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Capture, case: str
) -> None:
    """Набора нет, он не тот, мал или не закреплён числом и хэшем — красный словами, а не
    «0 расхождений»: не закреплённый набор прошёл бы и чужим файлом."""
    per, fields, words = PINS[case]
    path, cases = _golden(tmp_path, per)
    pinned = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "count": len(cases)}
    monkeypatch.setattr(ev, "MANIFEST", _manifest(tmp_path, **(pinned | fields)))
    oracle = Oracle(cases)
    use(monkeypatch, oracle)

    assert ev.main(["--golden"], golden_dir=str(tmp_path)) == 1

    out = capsys.readouterr().out
    assert f"НАБОР НЕ ПРОЧИТАН: {words}" in out
    assert oracle.asked == []


def test_the_manifest_pins_the_live_set_its_size_hash_and_the_v3_baseline() -> None:
    """Живой набор — вне репозитория: манифест называет файл, закрепляет число случаев и хэш (другой
    файл — красный), язык писем набора и метки сайтов без решения человека и хранит базы v3 и v2 —
    живой eval на этом наборе; прежний набор — числами в `previous`."""
    spec = json.loads(ev.MANIFEST.read_text(encoding="utf-8"))

    assert (spec["file"], spec["count"], spec["env"]) == (
        "sales_segment_golden.jsonl",
        174,
        "SALES_SEGMENT_GOLDEN_DIR",
    )
    assert re.fullmatch(r"[0-9a-f]{64}", spec["sha256"])
    assert spec["count"] >= spec["per_hypothesis"]["min"]
    assert (spec["letters"], spec["labeled"]) == ("ru", 118)
    assert spec["unlabeled_tags"] == ["model-yes-unreviewed", "rule-cut"]
    base, before = spec["baseline"], spec["before"]
    assert base["prompt_version"] == sj.PROMPT_VERSION
    assert before["prompt_version"] == "sales-segment-v2"
    for found in (base, before, spec["with_home_text"]):
        assert found["cases"] == spec["count"]
        assert {key for key, *_ in ev.CHECKS} <= set(found)
        assert set(found["false_reject_by"]) == set(CLASSES)
    assert spec["previous"]["count"] == 74
    # Ни доменов, ни текстов: только числа и слова о замере.
    assert not re.search(
        r"[a-z0-9-]+\.(?:ru|kz|com|net|org|ua|by|lt|lv|eu|de|pl)\b", json.dumps(spec)
    )


def test_no_golden_directory_is_red_in_words(capsys: Capture) -> None:
    assert ev.main(["--golden"], golden_dir="") == 1

    assert (
        "НАБОР НЕ ПРОЧИТАН: набор не найден: задайте SALES_SEGMENT_GOLDEN_DIR"
        in capsys.readouterr().out
    )


def test_an_unlabeled_case_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "set.jsonl"
    case = dict(SYNTHETIC[0], expect=None)
    path.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")

    with pytest.raises(ev.SetError, match="не размечен"):
        ev.load(path)


# --- заготовка разметки из прогона ------------------------------------------------------------


async def test_a_run_is_exported_for_labeling_without_the_judge_verdict(
    session: AsyncSession,
) -> None:
    owner = await hypothesis(session)
    run = await make_run(session, owner)
    domain = DomainModel(host="velo-shop-sample.com")
    session.add(domain)
    await session.flush()
    text = sj.site_text(title="Горные велосипеды — магазин", description="Доставка по стране")
    session.add(
        SalesSegmentCandidateModel(
            run_id=run.id, hypothesis_id=owner.id, domain_id=domain.id, lane=Lane.ACCEPT,
            verdict=Lane.ACCEPT, reason="магазин велосипедов", site_text=text.for_model(),
        )
    )  # fmt: skip
    await session.flush()

    [case] = await cases_of_run(session, run.id)

    assert case["host"] == "velo-shop-sample.com"
    assert case["expect"] is None
    assert SiteText.parse(case["text"]) == text
    assert (case["hypothesis"], case["segment"]) == (owner.name, owner.description)
    assert "verdict" not in case
    assert "reason" not in case
