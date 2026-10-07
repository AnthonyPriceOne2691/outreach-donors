"""Набор прогона агента продаж по манифесту и файл прогона.

**Набор — вне репозитория.** Он из настоящих писем, обезличенных, а репозиторий публичный:
в нём только манифест — какие файлы лежат в наборе, сколько в нём случаев не меньше,
контрольные суммы. Каталог набора — `SALES_REPLAY_DIR` (`config/sales.py`) или явный путь.
Набора нет — `SetNotFoundError` со словами «набор не найден» и путём; пуст или мал —
`SetError`: прогон по пустому списку дал бы «0 расхождений» — зелёный, который ничего не
проверил. Строка набора — один случай JSON (`case_of`): переписка по порядку, последним —
письмо собеседника, и решение человека.

**Файл прогона** — исход версии по каждому случаю рядом с решением человека, версия и
полнота прогона; переписки в нём нет. По нему прежний прогон сравнивается с новым
(`replay_gate.compare`), не прогоняя прежнюю версию заново.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.features.agent.writer import Turn
from backend.features.sales.agent.replay import (
    LABELS,
    REPLIED,
    Case,
    Decision,
    Human,
    Outcome,
    Result,
    Run,
    SetError,
    SetNotFoundError,
)

#: Формы манифеста и файла прогона: поменялась форма — поменялся номер.
SET_FORMAT = "sales-replay-set-v1"
RUN_FORMAT = "sales-replay-run-v1"


@dataclass(frozen=True, slots=True)
class SetSpec:
    """Набор по манифесту: файлы, сколько в нём случаев не меньше, где лежит."""

    name: str
    files: tuple[str, ...]
    min_cases: int
    #: Каталог в репозитории рядом с манифестом; `None` — вне репозитория.
    folder: str | None = None
    #: Контрольные суммы файлов; файла здесь нет — не сверяется.
    sha256: Mapping[str, str] = field(default_factory=dict)


def specs(raw: Mapping[str, Any]) -> dict[str, SetSpec]:
    """Манифест → наборы по имени. Не та форма — `SetError`."""
    if raw.get("format") != SET_FORMAT:
        raise SetError(f'манифест не той формы: ждём format = "{SET_FORMAT}"')
    sets = raw.get("sets")
    if not isinstance(sets, dict) or not sets:
        raise SetError("в манифесте нет наборов [sets.<имя>]")
    return {str(name): _spec(str(name), value) for name, value in sets.items()}


def _spec(name: str, raw: object) -> SetSpec:
    where = f"набор «{name}» в манифесте"
    if not isinstance(raw, dict):
        raise SetError(f"{where}: ждём раздел [sets.{name}]")
    files, least = raw.get("files"), raw.get("min_cases")
    folder, sums = raw.get("dir"), raw.get("sha256", {})
    if not isinstance(files, list) or not files or not all(isinstance(f, str) for f in files):
        raise SetError(f"{where}: нет файлов (files)")
    if not isinstance(least, int) or isinstance(least, bool) or least < 1:
        raise SetError(f"{where}: min_cases — сколько случаев в наборе не меньше, от 1")
    if not (folder is None or isinstance(folder, str)) or not isinstance(sums, dict):
        raise SetError(f"{where}: dir — строка, sha256 — таблица «файл = сумма»")
    return SetSpec(name, tuple(files), least, folder, {str(k): str(v) for k, v in sums.items()})


def located(spec: SetSpec, folder: Path | None) -> list[Path]:
    """Файлы набора на диске. Каталога или файла нет — «набор не найден» с путём."""
    if folder is None:
        raise SetNotFoundError(
            f"набор «{spec.name}» не найден: каталог не задан — SALES_REPLAY_DIR или --dir"
        )
    paths = [folder / name for name in spec.files]
    if missing := [str(path) for path in paths if not path.is_file()]:
        raise SetNotFoundError(f"набор «{spec.name}» не найден: нет {', '.join(missing)}")
    return paths


def read_set(spec: SetSpec, paths: Sequence[Path]) -> list[Case]:
    """Случаи набора. Пуст, мал, повтор номера, не та строка или сумма — `SetError`."""
    cases = [
        case
        for name, path in zip(spec.files, paths, strict=True)
        for case in _cases_in(spec, name, path)
    ]
    if twice := sorted(name for name, count in Counter(c.id for c in cases).items() if count > 1):
        raise SetError(f"набор «{spec.name}»: номера случаев повторяются: {', '.join(twice[:5])}")
    if len(cases) < spec.min_cases:
        what = "пуст" if not cases else f"мал: случаев {len(cases)}"
        raise SetError(
            f"набор «{spec.name}» {what}, а нужно не меньше {spec.min_cases} — "
            "прогон по нему ничего бы не проверил"
        )
    return cases


def _cases_in(spec: SetSpec, name: str, path: Path) -> list[Case]:
    data = path.read_bytes()
    want = spec.sha256.get(name)
    if want and hashlib.sha256(data).hexdigest() != want.strip().lower():
        raise SetError(
            f"набор «{spec.name}»: {name} не тот, что в манифесте — контрольная сумма не "
            "сходится; выгрузили заново — обновите сумму в манифесте"
        )
    lines = enumerate(data.decode("utf-8").splitlines(), start=1)
    return [case_of(_json(line, f"{name}:{n}"), f"{name}:{n}") for n, line in lines if line.strip()]


def _json(line: str, where: str) -> object:
    try:
        return json.loads(line)
    except json.JSONDecodeError as exc:
        raise SetError(f"{where}: строка не JSON ({exc.msg})") from None


def case_of(raw: object, where: str) -> Case:
    """Строка набора → случай. Не та форма — `SetError` с местом и причиной."""
    if not isinstance(raw, dict):
        raise SetError(f"{where}: строка набора — объект случая")
    name = raw.get("id")
    if not isinstance(name, str) or not name.strip():
        raise SetError(f"{where}: у случая нет id")
    return Case(name.strip(), _turns(raw.get("turns"), where), _human(raw.get("human"), where))


def _turns(raw: object, where: str) -> tuple[Turn, ...]:
    turns = tuple(_turn(item, where) for item in (raw if isinstance(raw, list) else []))
    if not turns or turns[-1].ours:
        raise SetError(f"{where}: в переписке (turns) последним должно быть письмо собеседника")
    return turns


def _turn(raw: object, where: str) -> Turn:
    side = raw.get("from") if isinstance(raw, dict) else None
    text = raw.get("text") if isinstance(raw, dict) else None
    if side not in ("us", "them") or not isinstance(text, str):
        raise SetError(f'{where}: письмо переписки — {{"from": "us" | "them", "text": "…"}}')
    return Turn(ours=side == "us", text=text)


def _human(raw: object, where: str) -> Human:
    if not isinstance(raw, dict):
        raise SetError(f"{where}: нет решения человека (human)")
    found = {d.value: d for d in Decision}.get(str(raw.get("decision")))
    if found is None:
        known = ", ".join(Decision)
        raise SetError(f"{where}: решение «{raw.get('decision')}» незнакомо; есть: {known}")
    replied = raw.get("replied", found in REPLIED)
    fixed = found is not Decision.REJECTED  # у остальных «ответил ли» следует из решения
    if not isinstance(replied, bool) or (fixed and replied != (found in REPLIED)):
        raise SetError(f"{where}: «ответил ли человек» (replied) не сходится с «{found.value}»")
    label, reason = raw.get("situation"), raw.get("reason")
    if label is not None and label not in LABELS:
        raise SetError(f"{where}: метки ситуации «{label}» нет; есть: {', '.join(sorted(LABELS))}")
    said = reason.strip() if isinstance(reason, str) and reason.strip() else None
    return Human(found, replied, None if label is None else str(label), "human", said)


# --- файл прогона --------------------------------------------------------------------------


def dumped(run: Run) -> dict[str, Any]:
    """Прогон — в файл: решение человека рядом с исходом версии; переписки в файле нет."""
    return {
        "format": RUN_FORMAT,
        "source": run.source,
        "version": dict(run.version),
        "stopped": run.stopped,
        "errors": [{"case": case, "error": error} for case, error in run.errors],
        "results": [
            {
                "case": r.case,
                "digest": r.digest,
                "human": {
                    "decision": r.human.decision.value,
                    "replied": r.human.replied,
                    "situation": r.human.situation,
                    "labelled_by": r.human.labelled_by,
                    "reason": r.human.reason,
                },
                "label": r.label,
                "outcome": r.outcome.value,
                "violations": list(r.violations),
                "why": r.why,
                "draft": r.draft,
            }
            for r in run.results
        ],
    }


def loaded(raw: object, where: str) -> Run:
    """Файл прогона → прогон. Не та форма — `SetError` словами."""
    if not isinstance(raw, dict) or raw.get("format") != RUN_FORMAT:
        raise SetError(f"{where}: не файл прогона ({RUN_FORMAT})")
    try:
        results = tuple(_result_of(item) for item in raw["results"])
        errors = tuple((str(e["case"]), str(e["error"])) for e in raw.get("errors") or [])
    except (KeyError, TypeError, ValueError) as exc:
        raise SetError(f"{where}: файл прогона повреждён ({exc!r})") from None
    stopped = raw.get("stopped")
    return Run(str(raw.get("source")), raw.get("version") or {}, results, errors, stopped)


def _result_of(item: Mapping[str, Any]) -> Result:
    human = item["human"]
    return Result(
        case=str(item["case"]),
        digest=str(item["digest"]),
        human=Human(
            Decision(human["decision"]),
            bool(human["replied"]),
            human.get("situation"),
            str(human.get("labelled_by") or "human"),
            human.get("reason"),
        ),
        label=item.get("label"),
        outcome=Outcome(item["outcome"]),
        violations=tuple(str(found) for found in item.get("violations") or []),
        why=item.get("why"),
        draft=str(item.get("draft") or ""),
    )
