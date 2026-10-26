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
import unicodedata
from collections.abc import Sequence
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
        self.services: tuple[str, ...] = ()

    async def classify(
        self, *, text: str, subject: str, services: Sequence[str] = ()
    ) -> KindFound | Unanswered:
        expect = ORACLE[text]
        kind = SalesKind(expect["kind"])
        known = f'- "{kind.value}":' in reply_kind.load_prompt()
        if not known:
            kind = SalesKind.INTERESTED
        kind = self.wrong.get(text, kind)
        quote = text.split(".", maxsplit=1)[0]
        self.services = tuple(services)
        return KindFound(
            kind,
            0.9,
            quote=quote,
            contact=expect.get("contact"),
            contact_name=expect.get("contact_name"),
            project=expect.get("project"),
            services=tuple(expect.get("services") or ()),
            phones=tuple(expect.get("phones") or ()),
            tokens=50,
        )

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


def test_synthetic_set_covers_every_kind_in_sixty_to_ninety_cases() -> None:
    cases = ev.load(ev.SYNTHETIC)

    assert 60 <= len(cases) <= 90
    assert len({case["id"] for case in cases}) == len(cases)
    kinds = {case["expect"]["kind"] for case in cases}
    assert kinds == reply_kind.MODEL_KINDS
    assert any("injection" in case["tags"] for case in cases)


#: Телефонные случаи v3 по видам из задания: телефон в теле и только в подписи, отказ и отписка
#: с телефоном, реквизиты без телефона, номер другого человека, восточно-арабские цифры,
#: «здравствуйте <номер> <имя>», письменности справа налево и номер без пробелов.
PHONE_TAGS = frozenset(
    {
        "phone_body",
        "phone_signature",
        "refusal_phone",
        "ids_no_phone",
        "other_person",
        "eastern_digits",
        "greeting_number",
        "rtl",
        "no_spaces_number",
        "quote_number",
    }
)


def test_synthetic_set_has_thirty_phone_cases_of_every_sort_in_many_scripts() -> None:
    cases = ev.load(ev.SYNTHETIC)
    phone_cases = [case for case in cases if PHONE_TAGS & set(case["tags"])]

    assert len(phone_cases) >= 30
    assert {tag for case in phone_cases for tag in case["tags"]} >= PHONE_TAGS
    numbers = [phone for case in cases for phone in case["expect"].get("phones", [])]
    codes = ("+7 ", "8-7", "+49 30", "+44 20", "+1 (212)", "+971", "+86", "+380", "+48", "+90")
    assert all(any(phone.startswith(code) for phone in numbers) for code in codes)
    scripts = {unicodedata.name(char).split()[0] for phone in numbers for char in phone}
    assert {"DIGIT", "ARABIC-INDIC", "EXTENDED"} <= scripts, "цифры трёх письменностей"
    refusals = [case for case in phone_cases if "refusal_phone" in case["tags"]]
    assert {case["expect"]["kind"] for case in refusals} == {
        "not_interested",
        "not_now",
        "unsubscribe",
    }


def test_every_expected_phone_passes_the_code_check_on_its_own_text() -> None:
    """Ожидаемый номер — дословно в тексте, целиком и в 7–15 цифр: его примет сверка кодом."""
    for case in ev.load(ev.SYNTHETIC):
        wanted = tuple(case["expect"].get("phones", []))
        kept, notes = reply_kind.phones_of(wanted, reply_kind.own_words(case["text"]))
        assert (kept, notes) == (wanted, ()), case["id"]


def test_synthetic_set_has_only_made_up_addresses_found_in_the_text() -> None:
    for case in ev.load(ev.SYNTHETIC):
        for address in re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", case["text"]):
            assert address.rstrip(".").endswith((".example", ".example.test")), case["id"]
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


@pytest.mark.parametrize("kind", [kind.value for kind in ev.GUARDED])
def test_spoiled_prompt_keeps_no_trace_of_a_guarded_kind_name(kind: str) -> None:
    """Порча снимает охраняемый вид целиком: имя вида, оставшееся в другом правиле промпта,
    подсказало бы его модели, и обратный прогон остался бы зелёным. Так было на живом прогоне
    v3, пока правило «ответ — один номер телефона» называло вид по имени."""
    spoiled = ev.without_kind(reply_kind.load_prompt(), kind)

    assert f'"{kind}"' not in spoiled


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
    with pytest.raises(ev.SetError, match=f"примеров {len(ev.load(ev.SYNTHETIC))} ≠ 3"):
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
