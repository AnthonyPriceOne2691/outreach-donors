"""Прогон версии агента продаж против решений людей и ворота «новая не хуже прежней».

Решение человека известно, а ответа версии он не видел: версия меряется тем, что сделала
бы (`replay.Outcome`) — по брифу, правилам и судье.

- **Ложное молчание** — версия молчит («ответ не нужен»), а человек ответил. Цена — лид:
  молчание в ответ на вопрос никто не исправит. Рядом — верное молчание и ответ там, где
  человек молчал: оба нужны порогам автоответа.
- **Нарушения** — черновики, где правила или судья нашли нарушения с первой попытки, до
  правок: так меряется писатель версии.
- **Доли** «прошло бы как есть», «правка», «отклонено», «молчание» — по судье и правилам,
  рядом с решениями людей (как есть, с правкой, ответил сам, отклонил, молчал) и таблицей
  «человек → версия».
- **Метка ситуации** — совпадение с эталоном: меткой человека (набор) или меткой версии,
  писавшей живой черновик, — раздельно: второе — не точность, а сдвиг против прежней.

**Ворота** (`compare`): на общих случаях двух прогонов — тот же номер и тот же отпечаток
(переписка и решение человека) — у новой версии не больше ложного молчания и не больше
черновиков с нарушениями (`strict` — нарушений строго меньше, если они были). Иначе
закрыто — с разбором по ситуациям и случаями, где стало хуже. Неполный прогон и пустое
пересечение — тоже закрыто: сравнение, которое ничего не сравнило, не зелёное.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from backend.features.sales.agent.replay import Decision, Outcome, Result, Run

NO_LABEL = "без метки"
#: Сколько случаев «стало хуже» показать строками; остальные — числом.
SHOWN = 30

DECISIONS = {
    Decision.SENT_AS_IS: "как есть",
    Decision.SENT_EDITED: "с правкой",
    Decision.OWN: "ответил сам",
    Decision.REJECTED: "отклонил",
    Decision.SILENT: "молчал",
}
OUTCOMES = {
    Outcome.AS_IS: "прошло бы как есть",
    Outcome.EDITED: "правка",
    Outcome.REJECTED: "отклонено",
    Outcome.SILENT: "молчание",
}
_BY = {"human": "с меткой человека", "model": "с меткой прежней версии"}
#: Исходы, где версия ответила бы письмом; «отклонено» — не ответ, а человеку.
_ANSWERED = frozenset({Outcome.AS_IS, Outcome.EDITED})


@dataclass(slots=True)
class Tally:
    """Числа прогона по случаям."""

    cases: int = 0
    replied: int = 0
    false_silence: int = 0
    true_silence: int = 0
    needless: int = 0
    violations: int = 0
    reasons: int = 0
    outcomes: Counter[Outcome] = field(default_factory=Counter)
    decisions: Counter[Decision] = field(default_factory=Counter)
    #: Случаев с меткой-эталоном и совпало — по тому, кто поставил метку.
    labelled: Counter[str] = field(default_factory=Counter)
    same: Counter[str] = field(default_factory=Counter)

    def add(self, result: Result) -> None:
        human, silent = result.human, result.outcome is Outcome.SILENT
        self.cases += 1
        self.replied += human.replied
        self.false_silence += result.false_silence
        self.true_silence += silent and not human.replied
        self.needless += result.outcome in _ANSWERED and not human.replied
        self.violations += bool(result.violations)
        self.reasons += len(result.violations)
        self.outcomes[result.outcome] += 1
        self.decisions[human.decision] += 1
        if human.situation is not None:
            self.labelled[human.labelled_by] += 1
            self.same[human.labelled_by] += result.label == human.situation


def tally(results: Iterable[Result]) -> Tally:
    found = Tally()
    for result in results:
        found.add(result)
    return found


def by_situation(results: Iterable[Result]) -> dict[str, list[Result]]:
    """Случаи по ситуации-эталону; эталона нет — «без метки»."""
    groups: dict[str, list[Result]] = {}
    for result in results:
        groups.setdefault(result.human.situation or NO_LABEL, []).append(result)
    return dict(sorted(groups.items()))


def _counted[K](counts: Counter[K], names: Mapping[K, str], whole: int) -> str:
    return " · ".join(f"{title} {_share(counts[key], whole)}" for key, title in names.items())


def _share(part: int, whole: int) -> str:
    return f"{part} ({part / whole:.0%})" if whole else str(part)


def _version(run: Run) -> str:
    version = run.version
    prompts = version.get("prompts") or {}
    files = " ".join(f"{name} {digest}" for name, digest in prompts.items())
    settings = version.get("settings")
    settings = "умолчания" if settings is None else f"v{settings}"
    return f"файлы {files or '—'}; база {version.get('kb')}; настройки {settings}"


def _labels(found: Tally) -> list[str]:
    return [
        f"Метка ситуации {_BY.get(by, by)}: совпала {found.same[by]} из {count}"
        for by, count in sorted(found.labelled.items())
    ]


def _row(name: str, group: Sequence[Result]) -> str:
    found = tally(group)
    shares = "/".join(str(found.outcomes[outcome]) for outcome in Outcome)
    same = sum(found.same.values())
    return (
        f"  {name:<16} {found.cases:>3} · {found.false_silence} · {found.violations} · "
        f"{shares} · {same}/{sum(found.labelled.values())}"
    )


def _matrix(results: Sequence[Result]) -> list[str]:
    pairs = Counter((result.human.decision, result.outcome) for result in results)
    rows = [
        f"  {title:<12} " + " · ".join(str(pairs[decision, outcome]) for outcome in Outcome)
        for decision, title in DECISIONS.items()
        if any(pairs[decision, outcome] for outcome in Outcome)
    ]
    return ["Человек → версия (" + " · ".join(OUTCOMES.values()) + "):", *rows]


def incomplete(run: Run) -> str | None:
    """Почему прогон неполный — словами; `None` — полный."""
    if run.complete:
        return None
    errors = "; ".join(f"{case}: {error}" for case, error in run.errors[:3])
    parts = [run.stopped or "", f"не прогнано случаев {len(run.errors)}" if run.errors else ""]
    return "; ".join(part for part in (*parts, errors) if part)


def summary(run: Run) -> list[str]:
    """Прогон словами: решения людей, исходы версии, молчание, нарушения, метки, ситуации."""
    found = tally(run.results)
    lines = [
        f"Прогон: {run.source}; случаев {found.cases}",
        f"Версия: {_version(run)}",
        "Решения людей: " + _counted(found.decisions, DECISIONS, found.cases),
        "Версия по судье и правилам: " + _counted(found.outcomes, OUTCOMES, found.cases),
        f"Ложное молчание (версия молчит, человек ответил): {found.false_silence} "
        f"из {found.replied} ответивших",
        f"Верное молчание: {found.true_silence} из {found.cases - found.replied} молчавших; "
        f"ответ там, где человек молчал: {found.needless}",
        f"Нарушения первого черновика: черновиков {found.violations}, замечаний {found.reasons}",
        *_labels(found),
        "По ситуациям (случаев · ложное молчание · нарушения · "
        "как есть/правка/отклонено/молчание · метка совпала):",
        *(_row(name, group) for name, group in by_situation(run.results).items()),
        *_matrix(run.results),
    ]
    if (why := incomplete(run)) is not None:
        lines.append(f"ПРОГОН НЕПОЛНЫЙ: {why}")
    return lines


# --- ворота --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Gate:
    """Итог ворот: что не так (пусто — открыты) и разбор словами."""

    problems: tuple[str, ...]
    lines: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.problems


def paired(old: Run, new: Run) -> tuple[list[Result], list[Result], str]:
    """Общие случаи двух прогонов — тот же номер и отпечаток — и что разошлось, словами."""
    before = {result.case: result for result in old.results}
    after = {result.case: result for result in new.results}
    both = [case for case in before if case in after]
    common = [case for case in both if before[case].digest == after[case].digest]
    note = (
        f"Общих случаев: {len(common)} (только в прежнем {len(before.keys() - after.keys())}, "
        f"только в новом {len(after.keys() - before.keys())}, с другой перепиской или "
        f"решением {len(both) - len(common)})"
    )
    return [before[case] for case in common], [after[case] for case in common], note


def _worse(name: str, was: int, now: int) -> str:
    mark = "  ← хуже" if now > was else ""
    return f"  {name:<28} {was} → {now}{mark}"


def _totals(o: Tally, n: Tally) -> list[str]:
    lines = [
        _worse("ложное молчание", o.false_silence, n.false_silence),
        _worse("черновиков с нарушениями", o.violations, n.violations),
        f"  {'замечаний':<28} {o.reasons} → {n.reasons}",
        *(
            f"  {title:<28} {o.outcomes[outcome]} → {n.outcomes[outcome]}"
            for outcome, title in OUTCOMES.items()
        ),
    ]
    lines += [
        f"  метка {_BY.get(by, by)}: {o.same[by]}/{count} → {n.same[by]}/{n.labelled[by]}"
        for by, count in sorted(o.labelled.items())
    ]
    return lines


def _situations(before: Sequence[Result], after: Sequence[Result]) -> list[str]:
    old_groups, new_groups = by_situation(before), by_situation(after)
    lines = ["По ситуациям (случаев · ложное молчание · черновиков с нарушениями):"]
    for name, group in old_groups.items():
        o, n = tally(group), tally(new_groups.get(name, []))
        silence = _worse("", o.false_silence, n.false_silence).strip()
        broken = _worse("", o.violations, n.violations).strip()
        lines.append(f"  {name:<16} {o.cases:>3} · {silence} · {broken}")
    return lines


def worse_cases(before: Sequence[Result], after: Sequence[Result]) -> list[str]:
    """Случаи, где новая версия хуже прежней: замолчала там, где человек ответил, или
    первый черновик получил нарушения."""
    found = []
    for was, now in zip(before, after, strict=True):
        where = f"{now.case} ({now.human.situation or NO_LABEL})"
        if now.false_silence and not was.false_silence:
            decision = DECISIONS[now.human.decision]
            found.append(f"  {where}: версия молчит («{now.label}»), человек — {decision}")
        if now.violations and not was.violations:
            found.append(f"  {where}: нарушения первого черновика — {'; '.join(now.violations)}")
    if len(found) > SHOWN:
        found = [*found[:SHOWN], f"  … и ещё {len(found) - SHOWN}"]
    return found


def compare(old: Run, new: Run, *, strict: bool = False) -> Gate:
    """Ворота: новая версия против прежней на общих случаях."""
    before, after, note = paired(old, new)
    o, n = tally(before), tally(after)
    problems = [
        f"{name} прогон неполный: {why}"
        for name, run in (("прежний", old), ("новый", new))
        if (why := incomplete(run)) is not None
    ]
    if not before:
        problems.append("общих случаев нет — сравнивать нечего")
    if n.false_silence > o.false_silence:
        problems.append(f"ложного молчания больше: было {o.false_silence}, стало {n.false_silence}")
    if n.violations > o.violations:
        problems.append(
            f"черновиков с нарушениями больше: было {o.violations}, стало {n.violations}"
        )
    elif strict and 0 < o.violations <= n.violations:
        problems.append(
            f"черновиков с нарушениями не меньше: было {o.violations}, стало {n.violations}"
        )
    worse = worse_cases(before, after)
    lines = [
        f"Прежняя версия: {_version(old)}",
        f"Новая версия: {_version(new)}",
        note,
        "Прежняя → новая:",
        *_totals(o, n),
        *_situations(before, after),
        *(["Стало хуже:", *worse] if worse else []),
    ]
    return Gate(tuple(problems), tuple(lines))
