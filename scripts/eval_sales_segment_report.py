"""Итог eval судьи сегмента словами: ворота, числа по нише и языку, классы ошибок, правила таблицы,
разбор описаний и токены. Запуск и метрики — `scripts/eval_sales_segment.py`.

Доли ложных «принять» и «отказ» — с односторонней 95-процентной границей Клоппера–Пирсона
(`eval_sales_segment_stats.bounded`): на десятках сайтов доля шумит, и граница говорит, насколько.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from scripts.eval_sales_segment_metrics import Result, metrics
from scripts.eval_sales_segment_stats import bounded


@dataclass(frozen=True, slots=True)
class Gates:
    false_accept_max: float = 0.05
    false_reject_max: float = 0.10
    accept_recall_min: float = 0.70
    reject_recall_min: float = 0.70


def percent(value: float | None) -> str:
    return "—" if value is None else f"{value:.0%}"


#: Ворота: число итога, поле `Gates`, слова, потолок (`True`) или пол.
CHECKS = (
    ("false_accept", "false_accept_max", "ложных «принять»", True),
    ("false_reject", "false_reject_max", "ложных «отказ»", True),
    ("accept_recall", "accept_recall_min", "полнота «принять»", False),
    ("reject_recall", "reject_recall_min", "полнота «отказ»", False),
)


def closed(total: dict[str, Any], gates: Gates) -> list[str]:
    """Нарушенные ворота словами — пусто, если зелёный. Числа без знаменателя не судятся."""
    failed = []
    for key, field, words, ceiling in CHECKS:
        value, gate = total[key], getattr(gates, field)
        if value is not None and (value > gate if ceiling else value < gate):
            failed.append(f"{words} {value:.0%} {'>' if ceiling else '<'} {gate:.0%}")
    return failed


def by_group(results: list[Result], field: str, title: str) -> None:
    """Числа по гипотезе (нише) или по языку сайта: новая версия не хуже прежней — в каждой, а
    не в сумме."""
    print(title)
    for name in sorted({getattr(r, field) for r in results}):
        part = metrics([r for r in results if getattr(r, field) == name])
        print(
            f"  {name}: случаев {part['labeled']}, «принять» {percent(part['accept_recall'])}, "
            f"«отказ» {percent(part['reject_recall'])}, ложных «принять» "
            f"{percent(part['false_accept'])}, ложных «отказ» {percent(part['false_reject'])}, "
            f"человеку {percent(part['to_human'])}"
        )


def _errors(results: list[Result]) -> None:
    """Ложные «принять» и «отказ» — числом с границей и по правилу таблицы, которое их дало."""
    labeled = [r for r in results if r.labeled]
    fits = [r for r in labeled if "yes" in r.answers]
    misfits = [r for r in labeled if "yes" not in r.answers]
    wrong_yes = [r for r in misfits if r.false_accept]
    wrong_no = [r for r in fits if r.false_reject]
    print(
        f"Ложных «принять»: {bounded(len(wrong_yes), len(misfits))}; ложных «отказ»: "
        f"{bounded(len(wrong_no), len(fits))}"
    )
    print(f"  ложные «принять» по правилу: {dict(Counter(r.rule or '—' for r in wrong_yes))}")
    print(f"  ложные «отказ» по правилу: {dict(Counter(r.rule or '—' for r in wrong_no))}")


def summary(total: dict[str, Any]) -> None:
    """Классы ошибок, доли полос по варианту (b), правила и токены — словами."""
    by = total["false_reject_by"]
    print(
        f"Ложных «отказ» по классу: сегмент {by['segment']}, язык {by['language']}, "
        f"посредник {by['intermediary']}, правило {by['rule']}, «не пишем» {by['country']}; "
        f"«не понять» среди размеченных {percent(total['unsure'])}; без разметки "
        f"{total['cases'] - total['labeled']}"
    )
    print(
        f"Вариант (b): на проверку {percent(total['to_human'])}, судья отказал бы "
        f"{percent(total['would_reject'])}; до варианта (b) на проверку шло бы "
        f"{percent(total['to_human_shadow'])}"
    )
    print(f"Правила таблицы: {total['rules']}")
    print(
        f"Токены: на вызов модели {total['tokens_per_call'] or '—'} (вызовов "
        f"{total['model_calls']}, обрезано потолком вывода {total['cut_off']}); вид сайта по "
        f"модели {total['kinds']}"
    )


def scopes_said(scopes: dict[str, dict[str, Any]]) -> None:
    """Разбор описаний набора словами: как судья понял каждое описание и чего это стоило."""
    for digest, entry in scopes.items():
        said = entry.get("words") or f"не разобрано: {entry.get('failed')}"
        print(f"Описание {digest}: {said} (токенов {entry.get('tokens', 0)})")


def print_total(results: list[Result], scopes: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
    """Итог словами — всего и по гипотезам; возвращает числа итога."""
    by_group(results, "hypothesis", "По нише (гипотезе):")
    by_group(results, "site_language", "По языку сайта:")
    total = metrics(results)
    print(f"Всего: {total}")
    labeled = [r for r in results if r.labeled]
    wrong = Counter(f"{r.expect}→{r.lane}" for r in labeled if not r.right)
    print(f"Ждали → судья: {dict(wrong)}")
    dangerous = [r.case_id for r in labeled if r.false_accept]
    print(f"ЛОЖНЫХ «ПРИНЯТЬ»: {len(dangerous)} {dangerous or ''}")
    _errors(results)
    summary(total)
    if scopes:
        scopes_said(scopes)
    return total
