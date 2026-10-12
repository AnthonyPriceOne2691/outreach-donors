"""Eval судьи сегмента сбора базы (волна В3в): синтетика, ворота, порча промпта, внешний набор
по манифесту и заготовка разметки из прогона.

Живая модель здесь не зовётся: её заменяет подставной транспорт — «оракул», который знает
решение человека, но отвечает только тем, что разрешает промпт: без правила дословной цитаты
он пересказывает, без определения «no» говорит «unsure». Так порча промпта видна без сети,
а цитата проверяется тем же кодом судьи (`segment_judge.parse`). Живой прогон на наборе —
координатор по слову владельца, числа — в verify-report.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import httpx
import pytest
from backend.features.core.models.domain import DomainModel
from backend.features.sales import segment_judge as sj
from backend.features.sales.segment_judge import SegmentClient, SiteText
from backend.features.sales.segment_models import Lane, SalesSegmentCandidateModel
from scripts import eval_sales_segment as ev
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_segment_judge import MODEL_KEY
from tests.test_sales_segment_run import hypothesis, make_run

Capture = pytest.CaptureFixture[str]
SYNTHETIC = ev.load(ev.SYNTHETIC)


class Oracle:
    """Подставная модель: решение человека — тем, что разрешает промпт."""

    def __init__(self, cases: Iterable[dict[str, Any]], wrong: dict[str, str] | None = None):
        self.cases = {case["host"]: case for case in cases}
        self.wrong = wrong or {}
        self.asked: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        system, user = (m["content"] for m in json.loads(request.content)["messages"])
        host = user.split("\n", 1)[0].removeprefix("Domain: ")
        self.asked.append(host)
        case = self.cases[host]
        fit = self.wrong.get(host, case["expect"])
        if f'- "{fit}": ' not in system:
            fit = "unsure"  # слова, которого промпт не определяет, оракул не говорит
        quote = case["evidence"]
        if ev.RULES["quote"] not in system:
            quote = " ".join(reversed(quote.split()))  # без правила — пересказ, а не цитата
        body = {"fit": fit, "quote": quote, "why": "по тексту сайта"}
        answer = {
            "choices": [{"message": {"content": json.dumps(body)}}],
            "usage": {"total_tokens": 211},
        }
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


def test_synthetic_set_covers_both_hypotheses_every_decision_and_the_rules() -> None:
    assert 20 <= len(SYNTHETIC) <= 30
    assert len({case["id"] for case in SYNTHETIC}) == len(SYNTHETIC)
    assert {case["hypothesis"] for case in SYNTHETIC} == {"en", "cis"}
    assert {case["expect"] for case in SYNTHETIC} == {"yes", "no", "unsure"}
    tags = {tag for case in SYNTHETIC for tag in case["tags"]}
    assert {"injection", "platform", "public-zone", "empty", "denied"} <= tags


def test_evidence_is_verbatim_in_one_line_of_the_site_text() -> None:
    """Слова решения — дословно из текста сайта: иначе оракул учил бы судью выдумывать."""
    for case in SYNTHETIC:
        if case["evidence"]:
            assert SiteText.parse(case["text"]).holds(case["evidence"]), case["id"]
        for host in (case["host"],):
            assert host.endswith(("-sample.com", "-sample.net", "-sample.org", "-sample.ru",
                                  "-sample.kz", "-sample.by", ".zz", "youtube.com")), case["id"]  # fmt: skip


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
    assert "полнота «принять» 0% < 70%" in out


def test_spoiled_no_definition_closes_the_gates(oracle: Oracle, capsys: Capture) -> None:
    assert ev.main(["--spoil", "no"]) == 1

    assert "полнота «отказ»" in capsys.readouterr().out


def test_a_misfit_accepted_without_a_human_closes_the_gates(
    monkeypatch: pytest.MonkeyPatch, capsys: Capture
) -> None:
    """Новостной сайт, уверенно принятый с настоящей цитатой, — стал бы лидом без человека."""
    use(monkeypatch, Oracle(SYNTHETIC, wrong={"pedal-weekly-sample.org": "yes"}))

    assert ev.main([]) == 1

    out = capsys.readouterr().out
    assert "ЛОЖНЫХ «ПРИНЯТЬ»: 1 ['en-news-1']" in out
    others = sum(case["expect"] != "yes" for case in SYNTHETIC)
    assert f"ложных «принять» {1 / others:.0%} > 5%" in out


def test_a_fit_cut_off_closes_the_gates(monkeypatch: pytest.MonkeyPatch, capsys: Capture) -> None:
    use(monkeypatch, Oracle(SYNTHETIC, wrong={"kodstart-sample.ru": "no"}))

    assert ev.main([]) == 1

    assert "ложных «отказ» 14% > 10%" in capsys.readouterr().out


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


@pytest.mark.parametrize(
    ("per", "fields", "words"),
    [
        (41, {"sha256": "0" * 64}, "набор не тот, что в манифесте: sha256"),
        (41, {"count": 7}, "набор не тот, что в манифесте: случаев 82 ≠ 7"),
        (39, {}, "набор мал: {'en': 39, 'cis': 39} — на гипотезу нужно не меньше 40 сайтов"),
    ],
    ids=["hash", "count", "too-small"],
)
def test_a_golden_set_other_than_the_manifest_says_is_red(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: Capture,
    per: int,
    fields: dict[str, Any],
    words: str,
) -> None:
    _golden(tmp_path, per)
    monkeypatch.setattr(ev, "MANIFEST", _manifest(tmp_path, **fields))

    assert ev.main(["--golden"], golden_dir=str(tmp_path)) == 1

    assert words in capsys.readouterr().out


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

    [case] = await ev.cases_of_run(session, run.id)

    assert case["host"] == "velo-shop-sample.com"
    assert case["expect"] is None
    assert SiteText.parse(case["text"]) == text
    assert (case["hypothesis"], case["segment"]) == (owner.name, owner.description)
    assert "verdict" not in case
    assert "reason" not in case
