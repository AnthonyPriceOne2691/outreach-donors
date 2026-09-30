#!/usr/bin/env python3
"""Пределы поставки: CI-слой, объёмные breaker'ы, рисковый дифф.

Часть `delivery_check.py` (`delivery@1.53`). Здесь то, что ограничивает поставку
снаружи: чем закрыт CI, не разрослась ли она объёмом и прочитано ли то, что
помечено рисковым.
"""

from __future__ import annotations

import re

from delivery_base import (ACTIVE, ARCHIVE, BREAKER_EXCLUDE, ActiveCtx,
                           DEFAULT_BREAKERS, GENERATED_FILENAMES, field,
                           is_placeholder, read)
from delivery_diff import applicable_lessons, diff_stats

def weak_ci_consequences(status: str, waivers: str, ctx: ActiveCtx,
                         errors: list[str], warnings: list[str]) -> None:
    """Что тянет за собой честное `ci-oracles: weak` (§10.4).

    Четвёртый шов (`delivery@1.56`): ветка `weak` — самая длинная в проверке
    CI-слоя и самостоятельная по смыслу. Свободные имена посчитаны `ast`, а
    не глазами (см. `check_ci_layer`).
    """
    phase, klass, verify = ctx.phase, ctx.klass, ctx.verify
    # Было `"ci" not in waivers`: строка `waivers: ci` снимала требование
    # ПОДСТРОКОЙ — без причины и без подписи. Форма waiver'а одна на весь
    # контур (§4.3a). Нашло приёмочное развёртывание.
    ci_waived = bool(re.search(r"ci\b.*reason=.*by=human:", waivers))
    # Компенсация §10.4 правило 4 — ТРЕТИЙ законный выход, наравне с waiver'ом.
    # Она была описана прозой и не проверялась ничем, поэтому автономное
    # развёртывание в проект без хостинга не имело **честного** способа сделать
    # гейт зелёным: waiver требует человека, занижать класс запрещено §2.2b, а
    # `blockers:` + `escalation.md` гейт не принимал. Рядом лежали две дешёвые
    # лазейки (занизить класс, написать `waivers: ci`), то есть канон учил
    # обходить себя законным с виду способом — ровно то, против чего §2.2b.
    # Асимметрия была видна в собственном выводе: для `human_ok_*` третье
    # значение сделали, для `ci-oracles` — нет.
    #
    # Компенсация проверяется по ФАКТУ артефакта, а не по заявлению: строка
    # `clean_clone_run:` в verify-report (что прогнали и когда) + непустые
    # `blockers:` + существующий `escalation.md` (§12.3 сам требует там два
    # варианта с ценой). Это не эквивалент CI и не снятие требования: остаётся
    # громкий warning, `ci-oracles` остаётся `weak`, и строка приёмки §6
    # закрывается как `weak`, а не как `auto`.
    compensated = (
        bool(re.search(r"(?im)^\s*\**clean_clone_run\**\s*:\s*\S", read(verify)))
        and bool(field(status, "blockers").lower() not in {"", "none", "-"})
        and (ACTIVE / "escalation.md").is_file()
    )
    if klass == "L" and not ci_waived and not compensated:
        errors.append(
            "class L with ci-oracles: weak — нужен либо human waiver "
            "(`waivers: ci reason=… by=human:…`), либо компенсация §10.4 п.4: "
            "строка `clean_clone_run: <что прогнал> at=<дата>` в "
            "verify-report.md + непустые blockers: + active/escalation.md. "
            "Занижать класс, чтобы гейт позеленел, запрещено (§2.2b)"
        )
    elif klass == "L" and compensated and not ci_waived:
        warnings.append(
            "class L, ci-oracles: weak — закрыто компенсацией §10.4 п.4 "
            "(clean_clone_run + blockers + escalation.md). Это НЕ эквивалент "
            "CI: строка приёмки §6 закрывается как weak, не как auto"
        )
    else:
        warnings.append(
            "ci-oracles: weak — local gates are bypassable (§10.4); "
            "Verifier must attach a clean-clone run to verify-report.md"
        )
    # 'tooling' — легитимный режим (гейт мержа в репо, серверного нет по тарифу),
    # а не поддавки: см. §10.4. Недопустим только 'weak'.
    #
    # ⚠ У флага ОБЯЗАН быть первый прогон, иначе он замкнут в круг (`delivery@1.52`).
    # §10.4 велит ставить `deployed` по факту ЗЕЛЁНОГО прогона, а флаг стоит в
    # том самом прогоне: честное `weak` держит его красным, а красный прогон не
    # даёт значению стать честным. Восьмое развёртывание вышло из круга флипом в
    # том же коммите — законно, но канон об этом не говорил, и агент имел ровно
    # два выхода, оба выглядящие как обход собственного правила.
    #
    # Развязка по СМЫСЛУ флага: он защищает не «каждый прогон», а ЗАКРЫТИЕ
    # поставки. На фазе `handoff` `weak` — ошибка, как и было. До неё — громкий
    # warning, который называет ровно один ход: обновить STATUS по факту этого
    # прогона, в том же коммите. Реальность (прогон исполняется) старше записи,
    # и спорить с ней значит требовать вранья в одну или другую сторону.


def check_ci_layer(status: str, args, ctx: ActiveCtx, errors: list[str],
                   warnings: list[str]) -> None:
    """Слой CI (§10.4), waiver'ы и строка стека.

    Третий шов `check_limits` (`delivery@1.56`).

    ⚠ **Зависимости вырезаемого блока считаются МЕХАНИЧЕСКИ, а не списком имён,
    который держишь в голове.** Первая редакция этого выреза проверила пять имён
    из головы, пропустила `args` (его читает `--require-ci`) и уронила 73 теста —
    ровно тем же способом, каким днём раньше откатилась попытка резать пачкой.
    Правильно: свободные имена = загруженные минус связанные внутри минус
    модульные, и считает это `ast`, а не внимание.
    """
    phase, klass, verify = ctx.phase, ctx.klass, ctx.verify
    ci = field(status, "ci-oracles").lower()
    waivers = field(status, "waivers").lower()
    if is_placeholder(ci):
        warnings.append(
            "STATUS.md: missing ci-oracles (weak|tooling|deployed) — §10.4"
        )
    elif ci.startswith("weak"):
        weak_ci_consequences(status, waivers, ctx, errors, warnings)
    if args.require_ci and not ci.startswith(("deployed", "tooling")):
        if phase == "handoff":
            errors.append(
                "--require-ci: ci-oracles is neither 'deployed' nor 'tooling' "
                "— поставку с weak CI не закрываем (§10.4)"
            )
        else:
            warnings.append(
                "--require-ci: ci-oracles = weak, а прогон уже идёт. Это первый "
                "прогон: поставь `ci-oracles: deployed` по факту ЭТОГО прогона "
                "в том же коммите (§10.4). На фазе handoff это станет ошибкой"
            )

    stack = field(status, "stack")
    if is_placeholder(stack) or "delivery@" not in stack:
        warnings.append(
            "STATUS.md: missing stack version "
            "(e.g. 'delivery@<version>, cqg@<version>, okf@absent')"
        )


def check_starting_diff(args, phase: str, n_files: int, warnings: list[str],
                        errors: list[str]) -> None:
    """Стартовый дифф на ранних фазах — чужие изменения (lab-11 F12/F13/F8).

    Пятый шов (`delivery@1.56`), и последний, который уводит `check_limits`
    под планку §2.1. Свободные имена посчитаны `ast`.
    """
    # --- Стартовый дифф (lab-11 F12/F13/F8). На specify/plan/tasks
    # поставка ещё не написала продуктового кода: всё, что дифф
    # показывает сверх BREAKER_EXCLUDE, принесено извне — незамерженная
    # предыдущая поставка (bootstrap, который мержится ПОКА активен,
    # §7.2 шаг 6) или ветка от старой базы после squash-мержа. Обе арки
    # lab-11 обнаружили это на handoff ценой полного цикла: breaker
    # мерил 33 файла при собственных 2, и совет «split the PR» резал
    # нечего. Здесь тот же тупик называется на входе и стоит минуту.
    if phase in ("specify", "plan", "tasks") and n_files:
        # Слито в локальный main, но не запушено — отдельный честный
        # диагноз: чинится push'ем, а не rebase'ом.
        alt_clean = False
        for cand in ("main", "master"):
            alt = None if cand == args.diff_base else diff_stats(cand)
            if alt is not None and alt[0] == 0:
                alt_clean = True
                break
        if alt_clean:
            warnings.append(
                f"стартовый дифф против {args.diff_base} непуст "
                f"({n_files} файл(ов)), против локального main пуст: "
                "предыдущая поставка слита, но не запушена — CI и "
                "breakers меряют чужой дифф, пока push не ушёл (§3.4)"
            )
        else:
            errors.append(
                f"стартовый дифф непуст: {n_files} файл(ов) кода в "
                f"{args.diff_base}..HEAD уже на фазе {phase} — это "
                "чужие изменения, поставка не стартует поверх них "
                "(§7.2 шаг 6). Незамерженная предыдущая — слей через "
                "merge_guard (bootstrap мержится ПОКА активен); ветка "
                "от старой базы после squash-мержа — git rebase --onto "
                f"{args.diff_base} <старая-база> (lab-11 F12/F8)"
            )


def breaker_limits(status: str, errors: list[str]) -> dict[str, int]:
    """Лимиты breaker'ов с учётом override и human waiver (§3.4).

    Вынесено из `check_limits` (`delivery@1.56`): та была 228 строк при планке
    §2.1 в 80 — контур держал на чужом коде правило, которого сам не выполнял.
    Шов выбран по ДАННЫМ, а не по комментарию-разделителю: разбор лимитов ничего
    не отдаёт дальше, кроме словаря, а соседний блок про объём диффа связан с
    остальной функцией пятью значениями сразу и режется отдельно.

    Одна и та же форма покрывает и override в `circuit_breakers`, и human waiver
    (`max_files_touched=40 reason=… by=human:…`), поэтому смотрим СТРОКУ, где
    стоит override, а не только совпадение: §3.4 различает настройку проекта и
    подпись человека. Про waiver канон говорил «только человек», а парсер
    принимал `max_loc_diff=99999` без причины и подписи — требование жило в
    прозе, механика позволяла агенту поднять себе лимит молча. Три независимых
    развёртывания нашли это по отдельности.
    """
    limits = dict(DEFAULT_BREAKERS)
    for m in re.finditer(r"(max_[a-z_]+)[ \t]*=[ \t]*(\d+)", status):
        if m.group(1) not in limits:
            continue
        bol = status.rfind("\n", 0, m.start()) + 1
        eol = status.find("\n", m.end())
        line = status[bol: eol if eol != -1 else len(status)]
        if "circuit_breakers" in line.lower():
            limits[m.group(1)] = int(m.group(2))
            continue
        if "reason=" not in line or "by=human:" not in line:
            errors.append(
                f"waiver {m.group(1)}={m.group(2)} без reason= и by=human: "
                "(§3.4) — лимит поднимает человек, не исполнитель. Нет "
                "человека — не waiver, а `blockers:` + escalation.md (§12.3) "
                "или более узкая поставка; настройка проекта пишется строкой "
                "circuit_breakers:"
            )
            continue
        limits[m.group(1)] = int(m.group(2))
    return limits


def breaker_verdicts(n_files: int, net: int, klass: str, limits: dict[str, int],
                     errors: list[str], warnings: list[str]) -> None:
    """Пороги объёма: класс S, файлы, строки (§2.2b, §3.4).

    Второй шов `check_limits` (`delivery@1.56`). Берёт уже посчитанные числа —
    поэтому режется чисто, в отличие от блока, который эти числа добывает.

    **§2.2b: класс S при S-нетипичном объёме — warning, не ошибка.** Большой
    механический багфикс бывает, но и занижение класса ради обхода
    `human_ok_spec` выглядит именно так. Молчать нельзя: молчание эту дыру и
    создало.
    """
    if klass == "S" and (n_files > 5 or net > 200):
        warnings.append(
            f"class S, а тронуто {n_files} файл(ов) / {net} строк — "
            "класс занижен? (§2.2b) Класс определяется работой, а не "
            "доступностью человека; нет человека — human_ok_spec: deferred"
        )
    if n_files > limits["max_files_touched"]:
        errors.append(
            f"circuit breaker: files_touched {n_files} > "
            f"{limits['max_files_touched']} — split the PR or add a "
            "human waiver line to STATUS (§3.4)"
        )
    if net > limits["max_loc_diff"]:
        errors.append(
            f"circuit breaker: net loc_diff {net} > "
            f"{limits['max_loc_diff']} — split the PR or add a "
            "human waiver line to STATUS (§3.4)"
        )


def check_limits(status: str, args, errors: list[str], warnings: list[str], ctx: ActiveCtx) -> None:
    """CI-слой, объёмные breaker'ы и рисковый дифф."""
    phase, klass, plan, tasks, verify = (
        ctx.phase,
        ctx.klass,
        ctx.plan,
        ctx.tasks,
        ctx.verify)
    check_ci_layer(status, args, ctx, errors, warnings)

    # --- Circuit breakers (§3.4): анти-oneshot по объёму поставки.
    #
    # `kind: bootstrap` breaker'ом не мерится, и это не поддавки. Bootstrap — не
    # поставка продукта: у него своя DoD (§7.3), он по построению трогает всё
    # дерево контура и не может быть «разрезан на части» — совет гейта «split the
    # PR» для него не выполним ни в каком виде. Приёмочное развёртывание показало
    # это числом: даже после исключения механики контура остаётся 30 файлов и
    # 2437 строк конфигов, снимков и workflow'ов против лимита 800. Единственным
    # выходом оставался рутинный waiver, который §4.3a сам называет
    # обесцениванием механизма — то есть гейт учил себя обходить.
    #
    # Объём при этом не замалчивается: он печатается строкой ниже, и §7.3 требует
    # приёмку §6 — bootstrap проверяется своей процедурой, а не breaker'ом.
    if field(status, "kind").lower().startswith("bootstrap"):
        print(
            "breakers: kind=bootstrap — объём не мерится (§3.4): развёртывание "
            "контура не режется на части, его DoD — §7.3 + приёмка §6"
        )
    elif args.diff_base:
        limits = breaker_limits(status, errors)
        stats = diff_stats(args.diff_base)
        if stats is None:
            warnings.append(
                f"circuit breakers: ref '{args.diff_base}' unavailable "
                "(shallow clone? need full history)"
            )
        else:
            n_files, added, deleted, excluded, changed = stats
            # --- §2.2a: уроки по затронутым путям должны быть упомянуты.
            # Warning, не error: сопоставление «префикс пути → урок»
            # приблизительно, а по §4.3b ложное срабатывание дороже пропуска.
            lessons = applicable_lessons(read(ARCHIVE / "INDEX.md"), changed)
            if lessons:
                cited = read(plan) + read(tasks) + status
                unread = [
                    i for i in lessons if not re.search(rf"\b{i}\b", cited)
                ]
                if unread:
                    warnings.append(
                        "archive/INDEX.md: уроки по затронутым путям не "
                        f"упомянуты: {', '.join(unread)} (§2.2a) — прочти "
                        "строки и сошлись на id в plan.md/tasks.md либо "
                        "напиши, почему не применимо"
                    )
            net = abs(added - deleted)
            print(
                f"breakers: files={n_files} net_loc={net} (+{added}/-{deleted}), "
                f"excluded={excluded} "
                f"({'|'.join(BREAKER_EXCLUDE + GENERATED_FILENAMES)}), "
                f"limits={limits}"
            )
            check_starting_diff(args, phase, n_files, warnings, errors)
            breaker_verdicts(n_files, net, klass, limits, errors, warnings)
