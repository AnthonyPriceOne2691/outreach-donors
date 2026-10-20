"""Судья сегмента v4: модель отдаёт факты, полосу ставит таблица правил кода по полю гипотезы.

Каждое правило таблицы (`segment_rules.RULES`) — на фактах, без модели: ниши, страны и письменности
разные (греческий, японский, польский, армянский, английский, испанский), домены выдуманы
(`*-sample.*`, `*.example.test`). Страна сегмента и страны «не пишем» — данные теста, ни одной страны
«по умолчанию» в коде нет. Разбор фактов и описания — строгая форма и проверка цитат кодом.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from backend.config.judge import JudgeMode
from backend.features.sales import segment_judge as sj
from backend.features.sales.hypothesis_countries import CountryPolicy, OtherCountries
from backend.features.sales.segment_facts import Place, SiteFacts, read_facts
from backend.features.sales.segment_models import Decider, Lane
from backend.features.sales.segment_rules import RULES, SCOPE_FAILED, Case, first
from backend.features.sales.segment_scope import Scope, read_scope, remembered
from backend.features.sales.segment_text import PAGE_DESCRIPTION, PAGE_TITLE, SiteText
from backend.features.sales.zone_country import country_of_host

REVIEW = CountryPolicy()
ACCEPT = CountryPolicy(OtherCountries.ACCEPT)
#: Сегмент со страной и виды «сайты самих компаний».
GREECE = Scope(frozenset({"GR"}), frozenset({"own"}))
JAPAN = Scope(frozenset({"JP"}), frozenset({"own"}))
ANYWHERE = Scope(frozenset(), frozenset({"own"}))
DIRECTORIES = Scope(frozenset(), frozenset({"own", "intermediary"}))
NO_NETWORKS = Scope(frozenset({"ES"}), frozenset({"own"}), excludes_networks=True)


def facts(kind: str = "own", core: str = "yes", **over: Any) -> SiteFacts:
    base = SiteFacts(kind, core, "три слова цитаты", "сайт по тексту")
    return SiteFacts(
        base.kind,
        base.core,
        base.quote,
        base.why,
        over.get("home", ""),
        over.get("home_quote", ""),
        tuple(Place(tuple(codes), "слова места") for codes in over.get("served", ())),
        over.get("only", ""),
        over.get("local", False),
        over.get("network", False),
    )


def lane_of(
    known: SiteFacts | None,
    *,
    scope: Scope | None = GREECE,
    policy: CountryPolicy = REVIEW,
    host: str = "clinic-sample.com",
) -> tuple[Lane, str]:
    verdict = sj.settle(host=host, policy=policy, scope=scope, said=sj.Said(known, "сбой"))
    return verdict.lane, verdict.rule or ""


# --- слова «чужой географии» -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("known", "words"),
    [
        # Видны только другие страны.
        (facts(home="CY", served=[["CY"]]), "видна только чужая география — Кипр"),
        # Страна сегмента — одна из многих у бизнеса из другой страны: не «только чужая».
        (
            facts(home="TR", served=[["TR", "GR", "BG", "RO", "GE"]]),
            "страна бизнеса — Турция (по тексту сайта); страна сегмента — лишь одна из многих",
        ),
        # Только «по всему миру»: страна сегмента отдельно не названа.
        (
            facts(served=[["*"]]),
            "сайт работает «по всему миру» — страна сегмента отдельно не названа",
        ),
        (facts(served=[["*"], ["CY"]]), "видна только чужая география — Кипр и «по всему миру»"),
    ],
)
def test_foreign_geography_is_named_as_it_is(known: SiteFacts, words: str) -> None:
    verdict = sj.settle(host="ferry-sample.com", policy=REVIEW, scope=GREECE, said=sj.Said(known))

    assert (verdict.lane, verdict.rule) == (Lane.REVIEW, "foreign_review")
    assert verdict.reason.startswith(words)


# --- таблица правил: по правилу -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("known", "scope", "policy", "lane", "rule"),
    [
        # Посредник и издание, которых сегмент не просит, — «нет»; просит справочники — подходят.
        (facts("intermediary", "no"), GREECE, ACCEPT, Lane.REJECT, "kind_unwanted"),
        (facts("media", "no"), ANYWHERE, REVIEW, Lane.REJECT, "kind_unwanted"),
        (facts("other", "no"), JAPAN, ACCEPT, Lane.REJECT, "kind_unwanted"),
        (facts("intermediary", "yes"), DIRECTORIES, REVIEW, Lane.ACCEPT, "segment_anywhere"),
        # Другое и смежное дело — «нет» при любом поле.
        (facts(core="no"), GREECE, ACCEPT, Lane.REJECT, "other_business"),
        (facts(core="adjacent"), JAPAN, ACCEPT, Lane.REJECT, "adjacent"),
        # Дело не видно — человеку.
        (facts("unclear", "unsure"), GREECE, ACCEPT, Lane.REVIEW, "unclear"),
        # Сегмент без места — «да».
        (facts(home="BR", served=[["BR"]]), ANYWHERE, REVIEW, Lane.ACCEPT, "segment_anywhere"),
        # Страна сегмента — страна бизнеса или рынок сайта (строка не длиннее трёх стран).
        (facts(home="GR", home_quote="Αθήνα"), GREECE, REVIEW, Lane.ACCEPT, "segment_market"),
        (facts(served=[["GR", "CY"]]), GREECE, REVIEW, Lane.ACCEPT, "segment_market"),
        # Мест в тексте не видно — «да», «не видно» не отказ.
        (facts(), JAPAN, REVIEW, Lane.ACCEPT, "no_places"),
        # Видна только чужая география — по полю: человеку или «да».
        (facts(home="KR", served=[["KR"]]), JAPAN, REVIEW, Lane.REVIEW, "foreign_review"),
        (facts(home="KR", served=[["KR"]]), JAPAN, ACCEPT, Lane.ACCEPT, "foreign_accept"),
        # «Только там» вне сегмента — «нет».
        (facts(served=[["IT"]], only="solo in Italia"), GREECE, ACCEPT, Lane.REJECT, "only_elsewhere"),
        # Местный бизнес в другой стране — «нет» при любом поле: он работает только у себя.
        (facts(home="IT", local=True), GREECE, ACCEPT, Lane.REJECT, "local_elsewhere"),
        (facts(served=[["IT"]], local=True), GREECE, REVIEW, Lane.REJECT, "local_elsewhere"),
        # Местный бизнес в стране сегмента — «да».
        (facts(home="GR", local=True), GREECE, REVIEW, Lane.ACCEPT, "segment_market"),
    ],
    ids=[
        "intermediary", "media", "state-body", "segment-asks-directories", "another-business",
        "adjacent", "unclear", "segment-anywhere", "home-in-segment", "market-line", "no-places",
        "foreign-review", "foreign-accept", "only-elsewhere", "local-abroad-by-home",
        "local-abroad-by-place", "local-at-home",
    ],
)  # fmt: skip
def test_each_rule_of_the_table(
    known: SiteFacts, scope: Scope, policy: CountryPolicy, lane: Lane, rule: str
) -> None:
    assert lane_of(known, scope=scope, policy=policy) == (lane, rule)


def test_the_segment_country_among_many_counts_only_when_the_business_country_is_not_seen() -> None:
    """«По всей Европе» модель раскрывает в страны, и страна сегмента попадает туда сама: бизнес из
    другой страны «для клиентов по всей Европе» — чужая география, а не сегмент."""
    europe = [["PL", "DE", "FR", "IT", "ES", "PT", "NL", "GR"]]

    assert lane_of(facts(served=europe)) == (Lane.ACCEPT, "segment_among")
    assert lane_of(facts(home="CL", home_quote="Santiago de Chile", served=europe)) == (
        Lane.REVIEW,
        "foreign_review",
    )


@pytest.mark.parametrize("policy", [REVIEW, ACCEPT], ids=["review", "accept"])
@pytest.mark.parametrize(
    ("known", "host", "scope"),
    [
        (facts(home="PE", home_quote="Lima, Perú"), "panaderia-sample.com", GREECE),
        (facts(), "panaderia-sample.com.pe", JAPAN),
        (facts(home="PE"), "panaderia-sample.com", ANYWHERE),
    ],
    ids=["by-text", "by-zone", "segment-anywhere"],
)
def test_a_business_from_an_excluded_country_is_no_with_either_position(
    known: SiteFacts, host: str, scope: Scope, policy: CountryPolicy
) -> None:
    """«Не пишем» — данные гипотезы: компания из такой страны — «нет» при любом положении
    переключателя и у сегмента без места; страна бизнеса — из текста, а не видна — из зоны."""
    excluded = CountryPolicy(policy.other, frozenset({"PE"}))

    verdict = sj.settle(host=host, policy=excluded, scope=scope, said=sj.Said(known))

    assert (verdict.lane, verdict.rule) == (Lane.REJECT, "excluded_country")
    assert verdict.reason.startswith("страна бизнеса — Перу")
    assert "гипотеза не пишет компаниям оттуда" in verdict.reason


def test_a_large_network_is_no_only_when_the_description_says_so() -> None:
    """Крупная сеть — правило гипотезы: действует, только если описание так говорит."""
    chain = facts(network=True)

    assert lane_of(chain, scope=NO_NETWORKS) == (Lane.REJECT, "network_excluded")
    assert lane_of(chain, scope=JAPAN) == (Lane.ACCEPT, "no_places")


def test_a_failed_description_parse_is_review_in_words_and_never_no_place() -> None:
    """Сбой разбора описания — не «мест нет»: правило «сегмент без места» не срабатывает, сайт —
    человеку словами, следующий проход спросит снова, а факты сайта остаются (оплачены)."""
    known = facts(home="KR", served=[["KR"]])

    verdict = sj.settle(host="x-sample.com", policy=ACCEPT, scope=None, said=sj.Said(known))

    assert (verdict.lane, verdict.rule, verdict.reason) == (
        Lane.REVIEW,
        "scope_failed",
        f"{SCOPE_FAILED}. Сайт по тексту",
    )
    assert verdict.unanswered
    assert verdict.proven  # факты — в кэш: следующий проход не платит за них снова


def test_rules_before_the_facts_keep_their_own_verdicts() -> None:
    ruled = sj.SegmentVerdict(Lane.REJECT, "платформам не пишем", decided_by=Decider.RULE)
    language = sj.SegmentVerdict(Lane.REJECT, "язык сайта — другой", decided_by=Decider.LANGUAGE)

    by_rule = sj.settle(host="x", policy=REVIEW, scope=GREECE, ruled=ruled)
    by_language = sj.settle(host="x", policy=REVIEW, scope=GREECE, language=language)

    assert (by_rule.decided_by, by_rule.rule, by_rule.reason) == (
        Decider.RULE,
        "domain",
        "платформам не пишем",
    )
    assert (by_language.decided_by, by_language.rule) == (Decider.LANGUAGE, "language")


def test_the_table_has_a_last_rule_that_always_fires_and_unique_names() -> None:
    names = [rule.name for rule in RULES]

    assert len(names) == len(set(names))
    assert first(Case("x", facts=facts(served=[["KR"]]), scope=JAPAN, policy=ACCEPT)).name == (
        "foreign_accept"
    )


# --- факты: строгая форма и проверка цитат ------------------------------------------------------------

SITE = SiteText(
    (
        (PAGE_TITLE, "Οδοντιατρείο Αθηνών — εμφυτεύματα και ορθοδοντική"),
        (PAGE_DESCRIPTION, "Κλείστε ραντεβού στην Αθήνα και στη Θεσσαλονίκη"),
    )
)


def answer(**over: Any) -> str:
    body: dict[str, Any] = {
        "kind": "own",
        "home": "GR",
        "home_quote": "στην Αθήνα και στη Θεσσαλονίκη",
        "served": [{"countries": ["GR"], "quote": "στην Αθήνα και στη Θεσσαλονίκη"}],
        "only": "",
        "network": False,
        "local": True,
        "core": "yes",
        "quote": "Οδοντιατρείο Αθηνών — εμφυτεύματα και ορθοδοντική",
        "why": "стоматология в Афинах",
    }
    return json.dumps(body | over, ensure_ascii=False)


def test_facts_with_quotes_from_the_text_are_kept() -> None:
    found = read_facts(answer(), SITE)

    assert isinstance(found, SiteFacts)
    assert (found.kind, found.core, found.home, found.local, found.dropped) == (
        "own",
        "yes",
        "GR",
        True,
        0,
    )
    assert found.places == frozenset({"GR"})


def test_a_place_without_its_quote_or_with_a_made_up_code_does_not_count_and_is_counted() -> None:
    """Пропуск считается, а не исчезает молча: место без цитаты из текста, код не из ISO 3166-1,
    страна бизнеса без своей цитаты."""
    found = read_facts(
        answer(
            served=[
                {"countries": ["GR"], "quote": "στην Αθήνα και στη Θεσσαλονίκη"},
                {"countries": ["CY"], "quote": "και στη Λευκωσία"},
                {"countries": ["ZZ", "GR"], "quote": "στην Αθήνα και στη Θεσσαλονίκη"},
            ],
            home_quote="στη Λάρνακα",
        ),
        SITE,
    )

    assert isinstance(found, SiteFacts)
    assert found.places == frozenset({"GR"})
    assert found.home == ""
    assert found.dropped == 3  # цитата места, код ZZ, цитата страны бизнеса


@pytest.mark.parametrize(
    ("content", "words"),
    [
        (answer(kind="clinic"), "ответ модели не из списка"),
        (answer(network="нет"), "ответ модели не той формы"),
        (json.dumps({"kind": "own"}), "в ответе модели нет поля"),
        (answer(quote="лучшие зубы Европы"), sj.MADE_UP),
    ],
    ids=["kind-out-of-list", "flag-not-boolean", "missing-key", "made-up-quote"],
)
def test_an_answer_not_of_the_form_is_words_not_facts(content: str, words: str) -> None:
    found = read_facts(content, SITE)

    assert isinstance(found, str)
    assert words in found


def test_facts_go_to_the_cache_and_back_unchanged() -> None:
    found = read_facts(answer(network=True), SITE)
    assert isinstance(found, SiteFacts)

    assert SiteFacts.from_json(found.as_json()) == found
    assert SiteFacts.from_json({"kind": "own"}) is None


# --- разбор описания ----------------------------------------------------------------------------------


def test_the_description_parse_is_countries_kinds_and_networks_in_words() -> None:
    found = read_scope(
        json.dumps({"countries": ["am", "GE"], "kinds": ["own"], "excludes_networks": True})
    )

    assert found == Scope(frozenset({"AM", "GE"}), frozenset({"own"}), excludes_networks=True)
    assert found.words == (
        "страна сегмента по описанию — Армения, Грузия; виды сайтов — сайты самих компаний; "
        "крупные международные сети — не подходят"
    )


@pytest.mark.parametrize(
    ("content", "words"),
    [
        ("не JSON", "не разобран"),
        (json.dumps({"countries": ["Armenia"], "kinds": ["own"]}), "не код страны"),
        (json.dumps({"countries": [], "kinds": []}), "не назвал вида сайтов"),
        (json.dumps({"countries": "GE", "kinds": ["own"]}), "не той формы"),
    ],
    ids=["not-json", "a-name-not-a-code", "no-kinds", "not-a-list"],
)
def test_a_description_parse_not_of_the_form_is_a_failure_in_words(
    content: str, words: str
) -> None:
    found = read_scope(content)

    assert isinstance(found, str)
    assert words in found


def test_the_run_report_keeps_the_parse_of_the_same_description_only() -> None:
    parsed = {"countries": ["JP"], "kinds": ["own"], "excludes_networks": False}
    report = {"scope": {"digest": "d1", "version": "sales-segment-scope-v1", **parsed}}

    assert remembered(report, "d1") == JAPAN
    assert remembered(report, "d2") is None
    assert remembered({"scope": {**report["scope"], "failed": "сбой"}}, "d1") is None


# --- страна по доменной зоне --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "code"),
    [
        ("bakery-sample.co.jp", "JP"),
        ("tienda-sample.com.pe", "PE"),
        ("studio-sample.co.uk", "GB"),
        ("clinic-sample.xn--3e0b707e", "KR"),
        ("app-sample.io", ""),
        ("union-sample.eu", ""),
        ("shop.example.test", ""),
    ],
)
def test_the_zone_names_the_country_only_for_country_zones(host: str, code: str) -> None:
    assert country_of_host(host) == code


def test_shadow_keeps_a_refusal_of_the_table_in_its_own_lane() -> None:
    verdict = sj.settle(
        host="x-sample.com", policy=REVIEW, scope=GREECE, said=sj.Said(facts(core="adjacent"))
    )

    assert sj.effective(verdict, JudgeMode.SHADOW)[0] is Lane.WOULD_REJECT
    assert sj.effective(verdict, JudgeMode.ENFORCE)[0] is Lane.REJECT


# --- синтетика под v4: поле гипотезы в разных нишах и письменностях --------------------------------


def test_the_synthetic_set_holds_the_field_in_many_niches_and_scripts() -> None:
    """Слово владельца: правки универсальные — поле гипотезы в синтетике держат разные ниши и
    письменности: оба положения переключателя, «не пишем» не меньше чем в четырёх нишах, местный
    бизнес в другой стране, бизнес через границу без страны в тексте, страна бизнеса только по зоне."""
    path = Path(__file__).resolve().parents[1] / "scripts/data/sales_segment_synthetic.jsonl"
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    field = [case for case in cases if "v4-field" in case["tags"]]
    accept = {c["hypothesis"] for c in field if c.get("foreign_countries") == "fit"}
    review = {c["hypothesis"] for c in field if c.get("foreign_countries") is None}
    excluded = {c["hypothesis"] for c in field if c.get("not_write")}
    tags = {tag for case in field for tag in case["tags"]}

    assert len(accept) >= 4
    assert len(review) >= 2
    assert len(excluded) >= 4
    assert {"local", "crossborder", "zone-only"} <= tags
    scripts = {"cyrillic", "georgian", "latin", "rtl", "armenian"} & tags
    assert len(scripts) >= 3
    for case in field:
        assert re.fullmatch(r"[a-z0-9-]+-sample(\.[a-z]{2,})+", case["host"]), case["id"]
