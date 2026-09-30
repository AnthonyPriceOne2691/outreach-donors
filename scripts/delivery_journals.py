#!/usr/bin/env python3
"""Журналы поведения: диагноз, эскалация, отклонённые варианты, решения.

Часть `delivery_check.py` (`delivery@1.53`). §12.1–12.3: контур фиксировал
результат и не показывал, КАК агент к нему шёл. Проверяется форма записи —
альтернатива и причина обязательны, шаблон записью не считается.
"""

from __future__ import annotations

import re

from delivery_base import (ACTIVE, ActiveCtx, field, is_placeholder,
                           list_entries, read, unfilled, verdict_blocks)
from delivery_artifact import (check_artifact_oracle,
                               check_weak_names_the_cheap_gap)
from delivery_decisions import decision_lines, decisions_without_cost
from delivery_diff import diff_identifiers, diff_stats



def check_diagnosis(status: str, ctx: ActiveCtx, kind: str, phase: str,
                    errors: list[str], warnings: list[str]) -> None:
    """Багфикс обязан показать, КАК искали (§12.1).

    Шов `check_journals` (`delivery@1.58`), выбран по данным: ничего не отдаёт
    дальше, маркер того же отступа, что у соседей.
    """
    # --- §12.1: багфикс обязан показать, КАК искали, а не только что нашёл.
    # repro_test доказывает, что баг найден; журнал гипотез — что его не будут
    # искать заново с нуля.
    if kind.startswith(("bugfix", "hotfix")) and phase in {
        "verify",
        "converge",
        "handoff",
    }:
        diag = field(status, "diagnosis")
        diag_file = ACTIVE / "diagnosis.md"
        if is_placeholder(diag):
            errors.append(
                f"kind={kind.split()[0]} at verify+: missing diagnosis (§12.1) "
                "— журнал гипотез с вердиктами; n/a только с reason="
            )
        elif diag.lower().startswith("n/a"):
            if "reason=" not in diag.lower():
                errors.append(
                    "diagnosis: n/a без reason= (§12.1) — «нашёл сразу» и «не "
                    "искал» обязаны различаться в тексте, а не в тишине"
                )
        elif not diag_file.is_file():
            errors.append(
                "diagnosis указывает на файл, которого нет (§12.1) — "
                "ожидается delivery/active/diagnosis.md"
            )
        elif not verdict_blocks(read(diag_file)):
            errors.append(
                "diagnosis.md без заполненных вердиктов (§12.1) — гипотезы без "
                "ОПРОВЕРГНУТА/ПОДТВЕРЖДЕНА это список подозрений: следующий "
                "читатель обязан перепроверить всё заново. Ожидается вердикт "
                "внутри СТРУКТУРНОГО блока: пункт списка, строка таблицы или "
                "раздел (перенос строки внутри пункта допустим). Строки шаблона "
                "(с <…>) и проза о формате вердиктами не считаются"
            )


def check_escalation(status: str, ctx: ActiveCtx, phase: str,
                     errors: list[str], warnings: list[str]) -> None:
    """Остановка обязана быть оформлена (§12.3).

    Самый крупный блок (59 строк) и единственный, отдающий дальше имя (`ln` —
    переменная цикла); ниже оно не читается — замер, а не догадка.
    """
    # --- §12.3: остановка обязана быть оформлена, иначе восстановление
    # контекста перекладывается на человека — то самое время, которое контур
    # экономит.
    blockers = field(status, "blockers")
    if blockers and not is_placeholder(blockers) and blockers.lower() not in {
        "none",
        "нет",
        "n/a",
    }:
        esc = ACTIVE / "escalation.md"
        if not esc.is_file():
            errors.append(
                f"blockers непусты ({blockers[:50]}), а active/escalation.md "
                "нет (§12.3) — «я застрял» решением не является: нужны что "
                "пробовал, какой нужен ответ, два варианта с ценой"
            )
        else:
            # Строки шаблона не считаются: скопированный и незаполненный
            # escalation.md — это не оформленная остановка (ср. STATUS, v1.5).
            lines_e = [ln for ln in read(esc).splitlines() if not unfilled(ln)]
            # Разметка принимается ЛЮБАЯ разумная: жирная строка шаблона,
            # markdown-заголовок, list-item, строка таблицы. До delivery@1.39
            # принималась только жирная строка, и `## Вариант 1 — …` давало
            # «вариантов 0» — то есть диагноз про СОДЕРЖАНИЕ, когда причина
            # была в ФОРМЕ. На dogfooding'е (lab-10) я споткнулся об это трижды
            # в трёх артефактах; ни одна подстройка не была про смысл.
            opts = len(
                [
                    ln
                    for ln in lines_e
                    if re.match(
                        # `\s+` обязателен: с `\s*` заголовок раздела
                        # «## Варианты» считался вариантом («вариант» + «ы»),
                        # и одного варианта хватало на два. Поймал существующий
                        # тест, а не я.
                        r"(?i)^\s*(?:#{1,6}\s*|[-*]\s+|\|\s*)?\**\s*"
                        r"(?:вариант|option)\s+\w",
                        ln,
                    )
                ]
            )
            costs = len(
                [ln for ln in lines_e if re.search(r"(?i)\bцена\b|\bcost\b", ln)]
            )
            if opts < 2:
                errors.append(
                    f"escalation.md: заполненных вариантов {opts}, нужно ≥2 "
                    "(§12.3) — один вариант это просьба согласиться, а не выбор; "
                    "второй всегда есть и называется «не делать / отложить». "
                    "Ожидается строка, начинающаяся со слова «Вариант» (можно "
                    "как заголовок `## Вариант A`, жирным `**Вариант A:**`, "
                    "пунктом списка или строкой таблицы)"
                )
            elif costs < 2:
                errors.append(
                    f"escalation.md: вариантов {opts}, а названных цен {costs} "
                    "(§12.3) — вариант без цены выбрать нельзя"
                )


def check_rejected_options(ctx: ActiveCtx, klass: str, phase: str,
                           errors: list[str], warnings: list[str]) -> None:
    """Отклонённые варианты оставляют след (§12.2a).
    """
    plan, implement_like = ctx.plan, ctx.implement_like
    # --- §12.2a: отклонённые варианты. Без следа тот же тупик предлагают
    # снова — и он снова выглядит разумным, потому что причина отказа нигде
    # не записана.
    if klass in {"M", "L"} and phase in implement_like and plan.is_file():
        plan_text = read(plan)
        sec = re.search(
            r"(?ims)^#{2,3}\s*rejected\s+alternatives\s*$(.*?)(?=^#{2,3}\s|\Z)",
            plan_text,
        )
        msg_alt = ""
        if not sec:
            msg_alt = (
                "plan.md без секции '## Rejected alternatives' (§12.2a) — "
                "отброшенный подход без записанной причины предлагается снова"
            )
        else:
            # Та же склейка переносов, что у журналов: причина часто уезжает на
            # вторую строку, и построчный разбор объявлял запись «без причины».
            entries = [
                ln
                for ln in list_entries(sec.group(1))
                if not unfilled(ln)
                and re.search(r"(?i)потому что|because|reason=", ln)
            ]
            if not entries:
                msg_alt = (
                    "plan.md: 'Rejected alternatives' без заполненных записей "
                    "с причиной (§12.2a) — законна и запись «рассматривали "
                    "только X, потому что Y исключён требованием Z»"
                )
        if msg_alt:
            (errors if klass == "L" else warnings).append(msg_alt)


def check_decisions_format(status: str, args, ctx: ActiveCtx, klass: str, phase: str,
                           errors: list[str], warnings: list[str]) -> None:
    """Журнал решений: проверяется ФОРМАТ ленты (§12.2).
    """
    implement_like = ctx.implement_like
    # --- §12.2: журнал решений. Проверяется ФОРМАТ: лента без альтернатив
    # неаудируема, а именно аудируемость — весь смысл файла.
    dec_file = ACTIVE / "decisions.md"
    late = phase in {"verify", "converge", "handoff"}
    if dec_file.is_file():
        good, bad = decision_lines(read(dec_file))
        if bad:
            errors.append(
                f"decisions.md: {len(bad)} строк(а) не в формате §12.2 "
                "«выбрал X вместо Y — потому что Z»: "
                + "; ".join(b[:55] for b in bad[:3])
            )
        if klass == "L" and late and not good:
            errors.append(
                "class L: decisions.md без ни одного решения (§12.2) — эпик "
                "или новый модуль без выбора не бывает"
            )
        # Цена, а не предпочтение: «потому что дешевле» неопровержимо, значит
        # аудита не даёт, ради которого файл и ведётся (§12.2a).
        # Идентификаторы диффа — то, с чем сверяется «цена». Диффа нет
        # (нет ref'а, не git) -> `None`, и проверка молчит: предупреждение по
        # неверной причине учит игнорировать гейт.
        # Без `--diff-base` берём `HEAD~1`: иначе проверка молчала бы на
        # каждом локальном прогоне, а в CI работала — то есть вела бы себя
        # по-разному там, где решение и принимается.
        check_decision_cost(args, dec_file, late, errors, warnings)
    elif klass == "L" and late:
        errors.append(
            "class L at verify+: missing active/decisions.md (§12.2)"
        )
    elif klass == "M" and late:
        warnings.append(
            "class M at verify+: нет active/decisions.md (§12.2) — поведение "
            "агента неаудируемо без чтения всего диффа"
        )

    check_artifact_oracle(status, phase, klass, errors, warnings)
    check_weak_names_the_cheap_gap(status, errors, warnings)


def check_decision_cost(args, dec_file, late: bool, errors: list[str],
                        warnings: list[str]) -> None:
    """Решение без названной цены — отчёт о сделанном, а не выбор (§12.2).

    Девятый шов (`delivery@1.58`) и снова по СЛОЖНОСТИ: `check_decisions_format`
    укладывалась в 54 строки при cx 15. Поймал не глаз, а собственный оракул
    «новых функций сверх §2.1 не заводим» (`cqg@1.84`) — правило сработало
    против того, кто его писал, и это ровно та проверка, ради которой оно есть.
    """
    dec_base = args.diff_base or "HEAD~1"
    dec_stats = diff_stats(dec_base)
    diff_ids = diff_identifiers(dec_stats[4], dec_base) if dec_stats else None
    no_cost = decisions_without_cost(read(dec_file), diff_ids)
    if no_cost and late:
        warnings.append(
            f"decisions.md: {len(no_cost)} решен(ий) без цены — причина не "
            "названа ни числом, ни ссылкой в изменённый код: "
            + "; ".join(n[:55] for n in no_cost[:3])
            + ". «Дешевле» и «проще» проверить нельзя ничем; назови число "
            "или процитируй то, что менял (файл, функцию, поле) — такое "
            "не напишешь, не открыв дифф (§12.2a)"
        )


def check_journals(status: str, args, errors: list[str], warnings: list[str], ctx: ActiveCtx) -> None:
    """Диагноз, эскалация, отклонённые варианты и лента решений."""
    phase, klass, plan, implement_like = (
        ctx.phase,
        ctx.klass,
        ctx.plan,
        ctx.implement_like)
    # kind/behavior читаем один раз — их используют обе проверки ниже.
    kind = field(status, "kind").lower()
    behavior = field(status, "behavior-oracles").lower()

    # --- §3.1c: багфикс обязан принести тест, который падал до фикса
    if kind.startswith("bugfix") and phase in {"verify", "converge", "handoff"}:
        repro = field(status, "repro_test")
        if is_placeholder(repro):
            errors.append(
                "kind=bugfix at verify+: missing repro_test (§3.1c) — тест, "
                "который падал до фикса; иначе баг вернётся"
            )
        elif repro.lower().startswith("n/a") and "reason=" not in repro.lower():
            errors.append(
                "repro_test: n/a без reason= (§3.1c) — «не воспроизводится» "
                "обычно значит «не пробовал»"
            )

    check_diagnosis(status, ctx, kind, phase, errors, warnings)

    check_escalation(status, ctx, phase, errors, warnings)

    check_rejected_options(ctx, klass, phase, errors, warnings)

    check_decisions_format(status, args, ctx, klass, phase, errors, warnings)

    # --- §3.1b: рефакторинг без behavior-oracles = тихая регрессия
    if kind.startswith("refactor") and phase in {"verify", "converge", "handoff"}:
        covered = (
            not behavior.startswith("weak")
            or "characteriz" in status.lower()
            or "характеризац" in status.lower()
            or "refactor" in field(status, "waivers").lower()
        )
        if not covered:
            errors.append(
                "kind=refactor with behavior-oracles: weak — «компилируется» не "
                "доказывает сохранение поведения (§3.1b). Нужны характеризационные "
                "тесты, наблюдаемая проверка или human waiver со словом refactor"
            )

    # --- CI (§10.4): единственный слой, который агент не может обойти локально
