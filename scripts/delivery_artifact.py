#!/usr/bin/env python3
"""§6.5a: артефакт сборки судится, а `weak` называет дешёвое непокрытое.

Часть `delivery_check.py` (`delivery@1.54`). Оба правила — про одну границу:
между «исходник корректен» и «продукт правилен». Форму исходника судит CQG,
поведение — тесты, а собранный артефакт до сих пор не читал никто, и «зелёный
CI» читался как «продукт правилен».
"""

from __future__ import annotations

import re

from delivery_base import ROOT, field, is_placeholder

BUILD_MARKERS = (
    ("package.json", r'"scripts"\s*:\s*\{[^{}]*"build"\s*:'),
    ("Makefile", r"(?m)^build\s*:"),
    ("justfile", r"(?m)^build\s*:"),
)


def declares_build(root) -> str:
    """Чем объявлен шаг сборки; пусто — проект ничего не собирает.

    Ищем ОБЪЯВЛЕНИЕ, а не угадываем стек: `scripts.build` в `package.json`,
    цель `build:` в Makefile/justfile. Не найдено — правило §6.5a не применяется,
    и это честнее, чем требовать артефакт у библиотеки.
    """
    for name, pattern in BUILD_MARKERS:
        p = root / name
        if p.is_file() and re.search(pattern, p.read_text(encoding="utf-8", errors="replace")):
            return name
    return ""


def check_artifact_oracle(status, phase, klass, errors, warnings) -> None:
    """§6.5a: у артефакта сборки обязан быть машинный инвариант.

    **Замер восьмого развёртывания.** Развёрнуто пятнадцать ролей CQG, CI зелёный,
    и при этом ни одна проверка не читает `dist/`: между «исходник корректен» и
    «продукт правилен» лежит зона, которую не судит ничто. Это не дыра каталога,
    а его предмет — форма исходника и поведение продукта разные слои, — но пока
    зона не названа, «зелёный CI» читается как «продукт правилен».

    Объявление живёт в STATUS (`artifact_oracle:`), как `runtime_paths` и
    `repro_test`, и проверяется по ФАКТУ файла: объявленный оракул без скрипта —
    снятая проверка с видом усиления (§4.6, тот же класс).
    """
    built = declares_build(ROOT)
    if not built:
        return
    declared = field(status, "artifact_oracle")
    hard = phase == "handoff" and klass in {"M", "L"}
    if not declared or is_placeholder(declared):
        (errors if hard else warnings).append(
            f"artifact_oracle: проект собирает артефакт ({built}), а оракула на "
            "него в STATUS нет — между «исходник корректен» и «продукт правилен» "
            "не судит ничто (§6.5a). Объяви проверку собранного или `n/a reason=…`"
        )
        return
    low = declared.lower()
    if low.startswith(("n/a", "нет", "no ")):
        if "reason=" not in low:
            errors.append(
                "artifact_oracle: n/a без reason= — «не делали» и «нечем» это "
                "разные вещи, и различать их обязана запись (§6.5a)"
            )
        return
    path = declared.split()[0].strip("`\"',")
    if not (ROOT / path).exists():
        errors.append(
            f"artifact_oracle: {path} — файла нет. Объявленный оракул без скрипта "
            "это снятая проверка с видом усиления (§4.6)"
        )


def check_weak_names_the_cheap_gap(status, errors, warnings) -> None:
    """`weak` обязан называть, что проверяемо машиной и не сделано (§6.5a).

    «Не развёрнуто» и «непроверяемо» — разные вещи, а `weak` их смешивает и
    потому живёт вечно. Замер того же развёртывания: слой behavior пуст, но
    большая часть непокрытого проверяется дёшево и без новых зависимостей —
    разбор собранного html, контраст как арифметика над токенами, переключатель
    языков как функция пути. Пока это не названо, `weak` читается как «нельзя».

    Правило то же, что §9.1a п.2 даёт гейтам: молчащий механизм либо убирают,
    либо пишут, почему держат. Здесь — либо покрывают, либо называют дешёвое.
    """
    raw = field(status, "behavior-oracles")
    if not raw.lower().startswith("weak"):
        return
    tail = raw[len("weak"):].strip(" —-:;()")
    if tail and not is_placeholder(tail):
        return
    warnings.append(
        "behavior-oracles: weak без названного — «не развёрнуто» и «непроверяемо» "
        "разные вещи (§6.5a). Что из непокрытого проверяемо машиной СЕГОДНЯ? "
        "Форма: `behavior-oracles: weak — дёшево: <что именно>`"
    )
