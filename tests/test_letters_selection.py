"""Подбор переписанных зон под коридор отличия (`letters/selection.py`) — без базы и модели.

Письма — из слов-меток: у каждой зоны свои слова («intro0 intro1 …»), переписанные слова —
тоже свои («intronew0 …»). Тогда отличие собранного письма считается ровно: переписанные
слова, делённые на слова письма, — и каждое ожидание видно по числам зон. Коридор — из
настроек (15–25%), письмо — 100 слов.
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Callable

import pytest
from backend.features.letters import compose, selection
from backend.features.letters.compose import Rendered
from backend.features.letters.selection import Selection, select
from backend.features.letters.template import Zone, ZoneKind
from backend.features.letters.uniqueness import difference, in_corridor


def _words(name: str, count: int) -> str:
    return " ".join(f"{name}{index}" for index in range(count))


def _letter(*rewrite: tuple[str, int], fixed: int) -> Rendered:
    """Письмо: зоны `rewrite` по словам и одна неизменяемая зона в конце."""
    zones = [Zone(name, ZoneKind.REWRITE, _words(name, count)) for name, count in rewrite]
    return Rendered(
        subject="Made-up test", zones=(*zones, Zone("rest", ZoneKind.FIXED, _words("rest", fixed)))
    )


def _rewritten(rendered: Rendered, **shares: float) -> dict[str, str]:
    """Что вернула модель: зона → доля её слов, переписанных с начала (1 — целиком)."""
    found: dict[str, str] = {}
    for zone in rendered.rewritable():
        if zone.name in shares:
            words = zone.text.split(" ")
            count = round(len(words) * shares[zone.name])
            found[zone.name] = " ".join(
                [f"{zone.name}new{i}" for i in range(count)] + words[count:]
            )
    return found


#: Четыре зоны `rewrite` — 40 слов из 100. Целиком переписанные наборы в коридоре:
#: {intro} 20%, {greeting, intro} 22%, {aside, close} 18%, {greeting, aside, close} 20%.
FOUR = _letter(("greeting", 2), ("intro", 20), ("aside", 10), ("close", 8), fixed=60)
FITTING = {("intro",), ("greeting", "intro"), ("aside", "close"), ("greeting", "aside", "close")}
#: Все четыре зоны — 22%, в коридоре и четыре меньших набора (16–20%): весь набор берётся целиком.
WHOLE = _letter(("greeting", 2), ("intro", 10), ("aside", 6), ("close", 4), fixed=78)
#: В коридоре пара {greeting, intro} 17% и одиночка {close} 24% — дальше от шаблона, но короче.
PAIR = _letter(("greeting", 5), ("intro", 12), ("close", 24), fixed=59)
#: Три пары по 20% — наибольшие равные: выбор между ними — за зерном.
EQUAL = _letter(("greeting", 10), ("intro", 10), ("close", 10), fixed=70)
#: Две одиночки в коридоре — {intro} 20% и {close} 21%, пары выше края: снимаются две зоны.
SINGLES = _letter(("greeting", 6), ("intro", 20), ("close", 21), fixed=53)


def _counted(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Сколько писем собрал подбор: каждая сборка — одно `difference`."""
    calls: list[int] = []
    real: Callable[[str, str], float] = difference

    def counting(before: str, after: str) -> float:
        calls.append(1)
        return real(before, after)

    monkeypatch.setattr(selection, "difference", counting)
    return calls


def test_the_one_set_in_the_corridor_is_taken_the_rest_stays_template() -> None:
    rendered = _letter(("intro", 20), ("close", 8), fixed=72)
    rewritten = _rewritten(rendered, intro=1, close=1)

    found = select(rendered, rewritten, seed=7)

    assert found is not None
    assert (found.zones, found.dropped) == ({"intro": rewritten["intro"]}, ("close",))
    assert found.difference == pytest.approx(0.20)


def test_difference_is_of_the_assembled_letter_not_a_sum_over_zones() -> None:
    """Отличие — `difference` собранного письма целиком: зона, переписанная перестановкой
    своих слов, по зоне дала бы одно, а по письму — другое."""
    rendered = _letter(("intro", 20), ("close", 8), fixed=72)
    shuffled = " ".join(reversed(rendered.rewritable()[0].text.split(" ")))
    rewritten = {"intro": shuffled, "close": _rewritten(rendered, close=1)["close"]}

    found = select(rendered, rewritten, seed=3)

    assert found is not None
    letter = compose.assemble(rendered, found.zones)
    assert found.difference == difference(letter.plain_body, letter.body)


def test_the_largest_set_in_the_corridor_is_taken_at_any_seed() -> None:
    """Наборов в коридоре четыре — берётся наибольший по числу зон, при любом зерне. Не по
    отличию: пара {greeting, intro} (22%) дальше от шаблона, чем тройка (20%), но короче."""
    rewritten = _rewritten(FOUR, greeting=1, intro=1, aside=1, close=1)

    for seed in range(40):
        found = select(FOUR, rewritten, seed=seed)
        assert found is not None
        assert tuple(found.zones) in FITTING
        assert (tuple(found.zones), found.dropped) == (("greeting", "aside", "close"), ("intro",))
        assert found.difference == pytest.approx(0.20)


def test_every_zone_in_the_corridor_is_taken_whole_at_any_seed() -> None:
    """Письмо, целиком лежащее в коридоре, уходит целиком: ни одна переписанная зона не
    снимается, хотя в коридоре и четыре меньших набора."""
    rewritten = _rewritten(WHOLE, greeting=1, intro=1, aside=1, close=1)

    for seed in range(40):
        found = select(WHOLE, rewritten, seed=seed)
        assert found is not None
        assert (found.zones, found.dropped) == (rewritten, ())
        assert found.difference == pytest.approx(0.22)


def test_a_pair_beats_a_single_at_any_seed() -> None:
    """Пара и одиночка в коридоре — берётся пара, при любом зерне, хотя одиночка дальше от
    шаблона (24% против 17%)."""
    rewritten = _rewritten(PAIR, greeting=1, intro=1, close=1)

    for seed in range(40):
        found = select(PAIR, rewritten, seed=seed)
        assert found is not None
        assert (tuple(found.zones), found.dropped) == (("greeting", "intro"), ("close",))


def test_the_seed_chooses_among_equally_large_sets() -> None:
    """Наибольших наборов три — по паре зон: тот же номер — тот же набор, разные номера —
    разные наборы, и любой выбранный — пара в коридоре."""
    rewritten = _rewritten(EQUAL, greeting=1, intro=1, close=1)

    chosen = {seed: select(EQUAL, rewritten, seed=seed) for seed in range(40)}

    picked = {tuple(found.zones) for found in chosen.values() if found is not None}
    assert picked <= {("greeting", "intro"), ("greeting", "close"), ("intro", "close")}
    assert len(picked) > 1
    assert all(found is not None and in_corridor(found.difference) for found in chosen.values())
    assert select(EQUAL, rewritten, seed=11) == chosen[11]


def test_dropped_names_the_rewritten_zones_left_as_template_in_template_order() -> None:
    rewritten = _rewritten(SINGLES, greeting=1, intro=1, close=1)

    for seed in range(40):
        found = select(SINGLES, rewritten, seed=seed)
        assert found is not None
        assert set(found.zones) | set(found.dropped) == set(rewritten)
        assert not set(found.zones) & set(found.dropped)
        assert len(found.dropped) == 2
        order = [zone.name for zone in SINGLES.rewritable()]
        assert list(found.dropped) == [name for name in order if name in found.dropped]


def test_nothing_in_the_corridor_is_none_for_the_caller() -> None:
    """Всё переписанное ниже нижнего края — даже целиком 10%: подбирать нечего, решает
    вызывающий. Контроль: те же зоны с переписанным вступлением — набор в коридоре есть."""
    assert select(FOUR, _rewritten(FOUR, greeting=1, close=1), seed=5) is None
    assert select(FOUR, _rewritten(FOUR, intro=1, close=1), seed=5) is not None


def test_single_rewrite_zone_is_all_or_nothing() -> None:
    """Одна зона `rewrite` на 40% письма: набор один — «всё»; «ничего» (шаблон) не кандидат.
    Переписанная целиком — выше коридора, и решает вызывающий (у продаж — повтор модели);
    переписанная наполовину — в коридоре."""
    rendered = _letter(("intro", 40), fixed=60)

    assert select(rendered, _rewritten(rendered, intro=1), seed=1) is None
    half = select(rendered, _rewritten(rendered, intro=0.5), seed=1)
    assert half is not None
    assert (list(half.zones), half.dropped) == (["intro"], ())
    assert half.difference == pytest.approx(0.20)


def test_no_rewritten_zones_is_none() -> None:
    assert select(FOUR, {}, seed=1) is None


def test_full_search_goes_up_to_the_limit_then_greedy(monkeypatch: pytest.MonkeyPatch) -> None:
    """До предела — все непустые наборы (2^6 − 1 = 63 сборки), дальше — жадно, по зоне за шаг."""
    six = _letter(*[(f"z{n}x", 4) for n in range(6)], fixed=76)
    seven = _letter(*[(f"z{n}x", 4) for n in range(7)], fixed=72)
    calls = _counted(monkeypatch)

    select(six, _rewritten(six, **{f"z{n}x": 1 for n in range(6)}), seed=2)
    exhaustive = len(calls)
    calls.clear()
    select(seven, _rewritten(seven, **{f"z{n}x": 1 for n in range(7)}), seed=2)

    assert (exhaustive, selection.LIMIT) == (63, 6)
    assert len(calls) <= 7 * 8 // 2 < 2**7 - 1


def test_many_rewrite_zones_do_not_hang_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """Число зон `rewrite` у шаблона не ограничено: 15 зон — 2^15 − 1 наборов полным перебором.
    Жадно — не больше 15·16/2 = 120 сборок письма, и письмо всё равно в коридоре. Держит суть
    число сборок, а не часы: время под замером покрытия мигает."""
    many = _letter(*[(f"part{n}x", 4) for n in range(15)], fixed=40)
    rewritten = _rewritten(many, **{f"part{n}x": 1 for n in range(15)})
    calls = _counted(monkeypatch)

    found = select(many, rewritten, seed=9)

    assert found is not None
    assert in_corridor(found.difference)
    assert len(calls) <= 15 * 16 // 2


def test_limit_is_the_callers_to_choose(monkeypatch: pytest.MonkeyPatch) -> None:
    """Предел — довод: вызывающий вправе сузить полный перебор. Четыре зоны при пределе 2 —
    жадно: четыре зоны поодиночке, затем три пары к лучшей — семь сборок вместо пятнадцати.
    Правило выбора то же: из кандидатов в коридоре ({intro} и {greeting, intro}) — больший."""
    calls = _counted(monkeypatch)

    found = select(FOUR, _rewritten(FOUR, greeting=1, intro=1, aside=1, close=1), seed=4, limit=2)

    assert found is not None
    assert tuple(found.zones) == ("greeting", "intro")
    assert len(calls) == 7


def test_contract_of_the_core_for_sales_and_donors() -> None:
    """Контракт ядра — на него опираются и продажи, и доноры: принятые зоны, отличие собранного
    письма, зоны, оставленные шаблоном; у `select` зерно и предел — только по имени."""
    assert [found.name for found in dataclasses.fields(Selection)] == [
        "zones",
        "difference",
        "dropped",
    ]
    params = inspect.signature(select).parameters
    assert [(name, param.kind.name) for name, param in params.items()] == [
        ("rendered", "POSITIONAL_OR_KEYWORD"),
        ("rewritten", "POSITIONAL_OR_KEYWORD"),
        ("seed", "KEYWORD_ONLY"),
        ("limit", "KEYWORD_ONLY"),
    ]
    assert params["limit"].default == selection.LIMIT
