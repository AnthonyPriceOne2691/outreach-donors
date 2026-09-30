#!/usr/bin/env python3
"""Форма поставки: фаза, класс, обязательные артефакты фазы.

Часть `delivery_check.py` (`delivery@1.53`) — гейт разрезан по планке 300 строк
(§9.1a п.5), из 1969 строк 986 приходились на одну функцию `main`. Здесь первый
раздел: он же вычисляет то, что читают следующие, и отдаёт это `ActiveCtx` —
восемь значений, а не полсотни локальных переменных, как было внутри `main`.
"""

from __future__ import annotations

import re

from delivery_base import (ACTIVE, ActiveCtx, field,
                           is_placeholder, read)
from delivery_decisions import signature_verdict
from delivery_diff import diff_identifiers, diff_stats
from delivery_risk import risk_review_gaps, risky_classes
from delivery_runtime import (breaker_value, declared_surfaces,
                              model_surface_gaps, rule_enforcer_gaps,
                              runtime_proof_gaps, runtime_touched,
                              unsigned_irreversible_gaps)

def check_phase_and_class(raw_phase: str, raw_class: str, phase: str,
                          klass: str, allowed: set[str], errors: list[str],
                          warnings: list[str]) -> None:
    """Фаза и класс: значения из закрытого набора (§2.2).

    Шов `check_status_shape` (`delivery@1.60`).
    """
    if is_placeholder(raw_phase):
        errors.append(
            f"STATUS.md: phase not filled in (template placeholder {raw_phase!r})"
        )
    elif not phase:
        errors.append("STATUS.md: missing phase")
    elif phase not in allowed:
        # constitution is project-level file, not active phase
        errors.append(
            f"STATUS.md: unknown phase '{phase}' "
            f"(allowed: {', '.join(sorted(allowed))})"
        )

    if not klass:
        msg = (
            f"STATUS.md: class not resolved (value {raw_class!r}) — "
            "cannot apply §2.2 artifact gates; write exactly one of S|M|L"
        )
        # На specify класс ещё может уточняться; с plan и дальше это блокер:
        # неизвестный класс = молча отключённые требования spec/plan.
        if phase == "specify":
            warnings.append(msg)
        else:
            errors.append(msg)


def check_required_artifacts(status: str, phase: str, klass: str, spec, plan,
                             tasks, verify, eval_smoke, implement_like: set[str],
                             errors: list[str], warnings: list[str]) -> None:
    """Артефакты, обязательные для этого класса и фазы (§2.2).
    """
    if klass in {"M", "L"} and phase in {
        "plan",
        "tasks",
        *implement_like,
    }:
        if not spec.is_file():
            errors.append(f"class {klass} at phase={phase}: missing active/spec.md")

    check_spec_signature(status, klass, phase, spec, plan, tasks,
                         implement_like, errors, warnings)

    check_plan_signature(status, klass, phase, tasks, implement_like,
                         errors, warnings)

    check_verify_report(status, phase, verify, errors, warnings)

    if klass in {"M", "L"} and phase in {"verify", "converge", "handoff"}:
        if not eval_smoke.is_file():
            errors.append(
                "class M/L at verify+: missing active/eval-smoke.md "
                "(product oracles are mandatory, §6.2)"
            )


def check_verify_report(status: str, phase: str, verify,
                        errors: list[str], warnings: list[str]) -> None:
    """verify-report на месте и заполнен (§3.2).
    """
    if not verify.is_file():
        if phase == "verify":
            # Фаза в процессе: отчёт ещё пишется — но выйти из неё без него нельзя.
            warnings.append(
                "phase=verify: active/verify-report.md not created yet"
            )
        elif phase in {"converge", "handoff"}:
            errors.append(
                f"phase={phase}: missing active/verify-report.md (DoD §3.2.2)"
            )


def check_risky_diff(status: str, args, phase: str, verify,
                     errors: list[str], warnings: list[str]) -> None:
    """Маячок на рисковый дифф (§12.5) и пути, проверяемые исполнением (§12.6).
    """
    # --- §12.5: маячок на рисковый дифф. Предупреждение на verify, ОТКАЗ на
    # handoff — та же лестница, что у `deferred` (§2.2b): работать не мешаем,
    # объявить сделанным не даём. Блокировать на каждом коммите нельзя: правка
    # опечатки получала бы требование ревью, и маячок сняли бы через неделю.
    if phase in {"verify", "converge", "handoff"}:
        rbase = args.diff_base or "HEAD~1"
        rstats = diff_stats(rbase)
        if rstats and rstats[4]:
            gaps = risk_review_gaps(
                read(verify),
                risky_classes(rstats[4], rbase),
                diff_identifiers(rstats[4], rbase),
            )
            for g in gaps:
                (errors if phase == "handoff" else warnings).append(
                    f"рисковый дифф: {g}"
                )

    check_runtime_paths(status, args, phase, verify, errors, warnings)
    check_model_surface(status, args, phase, verify, errors, warnings)
    # §3.4a. Зовётся БЕЗУСЛОВНО, не внутри соседа: красная черта не зависит
    # ни от модели, ни от объявленных runtime_paths — проект может не иметь
    # ни того, ни другого и всё равно дотягиваться до необратимого.
    for g in unsigned_irreversible_gaps(status):
        (errors if phase == "handoff" else warnings).append(
            f"необратимое без человека: {g}"
        )


def check_runtime_paths(status: str, args, phase: str, verify,
                        errors: list[str], warnings: list[str]) -> None:
    """Путь, проверяемый только исполнением (§12.6).

    ⚠ Дифф считается ЗДЕСЬ, а не приходит параметром: у вызывающего он живёт
    внутри `if phase in …`, и передача наружу дала `UnboundLocalError` на фазах,
    где ветка не сработала. Значение, посчитанное в ветке, за границу шва не
    выносится — это тот же класс, что убегающий `return` (`delivery@1.59`).
    """
    if phase not in {"verify", "converge", "handoff"}:
        return                    # ⚠ ОХРАНА ФАЗЫ: блок жил под ней у вызывающего
    rstats = diff_stats(args.diff_base or "HEAD~1")
    if not (rstats and rstats[4]):
        return                    # ⚠ ВТОРАЯ охрана: блок жил и под непустым диффом
    # --- §12.6: путь, проверяемый только исполнением. Та же лестница
    # (предупреждение на verify, отказ на handoff) и по той же причине.
    surfaces, declared = declared_surfaces(status, "runtime_paths")
    if not declared:
        (errors if phase == "handoff" else warnings).append(
            "нет строки `runtime_paths:` в STATUS (§12.6) — назови "
            "пути, чей отказ не виден ни сборке, ни тестам (экран, "
            "права, железо, тракт съёмки), либо `none reason=…`. "
            "Молчание тут читается как «таких нет», а замер поля дал "
            "четыре падения подряд ровно в них"
        )
    else:
        touched = runtime_touched(rstats[4], surfaces)
        for g in runtime_proof_gaps(read(verify), touched):
            (errors if phase == "handoff" else warnings).append(
                f"исполнение: {g}"
            )
        check_surface_breaker(status, phase, touched, errors, warnings)


def check_model_surface(status: str, args, phase: str, verify,
                        errors: list[str], warnings: list[str]) -> None:
    """Поверхность поведения модели объявлена, и правка по ней не молчит (§14).

    Обе охраны — фазы и непустого диффа — стоят явными `return`, как у соседа:
    значение, посчитанное в ветке, за границу шва не выносится (`delivery@1.60`).

    ⚠ Контур здесь СПРАШИВАЕТ, а не судит. «Сколько оракулов» и «что показал
    дифференциальный прогон» — содержание, и §14.4 сама запрещает ставить его
    механикой раньше работающей порчи промпта.
    """
    if phase not in {"verify", "converge", "handoff"}:
        return
    rstats = diff_stats(args.diff_base or "HEAD~1")
    if not (rstats and rstats[4]):
        return
    # Лестница §12.6 выбирается ОДИН раз: три инлайновых выбора подряд подняли
    # сложность за порог §2.1 (cx 11), а правило одно — отказ только на handoff.
    sink = errors if phase == "handoff" else warnings
    surfaces, declared = declared_surfaces(status, "model_surface")
    if not declared:
        sink.append(
            "нет строки `model_surface:` в STATUS (§14.1) — назови промпты, пин "
            "модели, параметры сэмплирования, схемы инструментов, конфиг "
            "извлечения, схему выхода, пин судьи и версию провайдера, либо "
            "`n/a reason=…`. Молчание читается как «модель не зовём», а замер "
            "полигона дал обратное: правка промпта проезжает мимо всех гейтов"
        )
        return
    for g in model_surface_gaps(read(verify), runtime_touched(rstats[4], surfaces)):
        sink.append(f"поверхность модели: {g}")
    for g in rule_enforcer_gaps(status, surfaces):
        sink.append(f"двойник ниже модели: {g}")


def check_spec_signature(status: str, klass: str, phase: str, spec, plan, tasks,
                         implement_like: set[str], errors: list[str],
                         warnings: list[str]) -> None:
    """Спека подписана человеком там, где это требует класс (§3.1d).
    """
    if klass in {"M", "L"} and phase in implement_like:
        if not plan.is_file():
            errors.append(f"class {klass} at phase={phase}: missing active/plan.md")
        if not tasks.is_file():
            errors.append(f"class {klass} at phase={phase}: missing active/tasks.md")
        # §3.3 называет это stop-gate'ом — значит error, не warning.
        # `deferred` (§2.2b) — законный третий вариант: человека нет, работа
        # идёт под запись долга. Он пускает до converge включительно, но НЕ на
        # handoff: подпись под ожиданиями обязательна там, где человек всё
        # равно есть — на мерже. Без этого значения агент без доступного
        # человека занижал класс до S, обходя требование спеки законным с виду
        # способом (найдено независимым развёртыванием).
        e, w = signature_verdict("human_ok_spec", field(status, "human_ok_spec"), klass, phase)
        errors += e
        warnings += w


def check_plan_signature(status: str, klass: str, phase: str, tasks,
                         implement_like: set[str], errors: list[str],
                         warnings: list[str]) -> None:
    """Подпись плана для класса L и задачи для S (§2.2, §3.3)."""
    if klass == "L" and phase in implement_like:
        # §2.2b распространяется на ВСЕ поля-подписи, а не только на спеку:
        # `human_ok_plan` упирался в тот же тупик, и агент без человека выбирал
        # между «встать» и «занизить класс» — дыра, закрытая один раз, вернулась
        # во втором поле. Развилка теперь одна на класс полей (см. функцию).
        e, w = signature_verdict("human_ok_plan", field(status, "human_ok_plan"), klass, phase)
        errors += e
        warnings += w

    if klass == "S" and phase in implement_like and not tasks.is_file():
        errors.append("class S at implement+: missing active/tasks.md (mini-spec)")


def check_surface_breaker(status: str, phase: str, touched,
                          errors: list[str], warnings: list[str]) -> None:
    """Breaker по ПОВЕРХНОСТЯМ, а не по объёму (§12.6).

    §3.4 считает файлы и строки, и три правки в три подсистемы на десяток
    строк проходят его не заметив — а именно так выглядел составной отказ,
    который потом разбирали перебором.
    """
    # Breaker по ПОВЕРХНОСТЯМ, а не по объёму. §3.4 считает файлы
    # и строки, и три правки в три подсистемы на десяток строк
    # проходят его не заметив — а именно так и выглядел составной
    # отказ, который потом разбирали перебором.
    cap = breaker_value(status, "max_runtime_paths")
    if len(touched) > cap:
        (errors if phase == "handoff" else warnings).append(
            f"исполнение: одной поставкой задето {len(touched)} "
            f"рисковых путей ({', '.join(sorted(touched))}) при "
            f"пороге {cap} (§12.6) — ставь по одному: составной "
            "отказ разбирается перебором, а перебор оплачивает "
            "человек. Осознанно — строкой "
            "`max_runtime_paths=<N> reason=… by=human:…` в STATUS"
        )


def check_status_shape(status: str, args, errors: list[str], warnings: list[str]) -> ActiveCtx:
    """Фаза, класс и артефакты фазы. → контекст для остальных разделов."""
    allowed = {
        "specify",
        "plan",
        "tasks",
        "implement",
        "verify",
        "converge",
        "handoff",
    }
    implement_like = {"implement", "verify", "converge", "handoff"}

    raw_phase = field(status, "phase")
    raw_class = field(status, "class")

    phase_m = (
        re.match(r"(?i)^([a-z_]+)", raw_phase)
        if not is_placeholder(raw_phase)
        else None
    )
    class_m = (
        re.match(r"(?i)^([SML])\b", raw_class)
        if not is_placeholder(raw_class)
        else None
    )
    phase = phase_m.group(1).lower() if phase_m else ""
    klass = class_m.group(1).upper() if class_m else ""

    check_phase_and_class(raw_phase, raw_class, phase, klass, allowed,
                          errors, warnings)

    spec = ACTIVE / "spec.md"
    plan = ACTIVE / "plan.md"
    tasks = ACTIVE / "tasks.md"
    verify = ACTIVE / "verify-report.md"
    eval_smoke = ACTIVE / "eval-smoke.md"

    check_required_artifacts(status, phase, klass, spec, plan, tasks, verify,
                             eval_smoke, implement_like, errors, warnings)

    check_risky_diff(status, args, phase, verify, errors, warnings)


    return ActiveCtx(phase=phase, klass=klass, spec=spec, plan=plan, tasks=tasks, verify=verify, eval_smoke=eval_smoke, implement_like=implement_like)
