#!/usr/bin/env python3
"""Доказательства: происхождение ожидания, ревью утверждений, репро-тест.

Часть `delivery_check.py` (`delivery@1.53`). Раздел отвечает на вопрос «чем
подтверждено», а не «что заполнено»: подпись под примерами, дифф утверждений,
тест, падавший до фикса.
"""

from __future__ import annotations

import re

from delivery_base import (ACTIVE, ActiveCtx, field, is_placeholder,
                           read)
from delivery_base import SKIP_DIR_PARTS, TEST_TEXT_SUFFIXES
from delivery_decisions import signature_verdict
from delivery_history import debt_is_not_frozen, expectation_predates_tests

def check_archive_obligation(status: str, ctx: ActiveCtx, phase: str,
                             errors: list[str]) -> None:
    """Поставка уходит в archive с обязательством проверить (§13.1).

    Шов `check_evidence` (`delivery@1.57`). Отдаёт дальше только `vr` — текст
    verify-report, — и ниже он не читается: замер, а не догадка.
    """
    verify = ctx.verify
    # --- §13.1: поставка уходит в archive вместе с обязательством проверить
    # её в проде. Без этих полей «готово» проверено во всех средах, кроме
    # единственной значимой.
    if phase == "handoff":
        sig = field(status, "observe_signal")
        until = field(status, "observe_until")
        if is_placeholder(sig):
            errors.append(
                "phase=handoff: пусто observe_signal (§13.2) — что именно "
                "будет проверено в проде; «ошибок нет» сигналом не является, "
                "молчание одинаково выглядит и при не вызываемом коде"
            )
        if is_placeholder(until):
            errors.append(
                "phase=handoff: пусто observe_until (§13.1) — до какой даты "
                "наблюдаем; обязательство без срока не выполняется"
            )
        # Уровень наблюдаемости — значение проверяемое, а не декларативное
        # (урок §10.4: самозаявленное `ci-oracles: tooling` при красном CI).
        check_observability_ladder(status, sig, errors)

    if phase == "handoff" and verify.is_file():
        vr = read(verify)
        if not re.search(r"READY FOR HANDOFF", vr, re.I):
            errors.append(
                "phase=handoff: verify-report.md lacks 'READY FOR HANDOFF' verdict"
            )
        # §9.2: метрики снимаются на handoff. Без гейта ритуал не выполняется.
        if "Harness metrics" not in vr:
            errors.append(
                "phase=handoff: verify-report.md lacks the 'Harness metrics' "
                "block (§9.2 / A.10) — run: python scripts/delivery_metrics.py "
                "--base origin/main --write"
            )


def check_builder_verifier(status: str, ctx: ActiveCtx, klass: str, phase: str,
                           errors: list[str]) -> None:
    """Builder ≠ Verifier (§5.2) — заявление о независимости.

    Единственный из шести блоков, который НИЧЕГО не отдаёт дальше.
    """
    verify = ctx.verify
    # --- Builder ≠ Verifier (§5.2): не сама независимость, но явное заявление
    # о ней. Настоящее разделение обеспечивает required review в branch
    # protection (CQG §8.5); здесь ловим «сам построил, сам принял».
    if phase in {"verify", "converge", "handoff"} and verify.is_file():
        declared = field(read(verify), "Verifier") or field(status, "verifier")
        builder = field(status, "builder")
        if is_placeholder(declared):
            errors.append(
                "verify-report.md: Verifier not filled in — Builder must not "
                "accept own work (§5.2); write process:ci | agent:NAME | human:NAME"
            )
        elif (
            klass in {"M", "L"}
            and not is_placeholder(builder)
            and declared.strip().lower() == builder.strip().lower()
        ):
            errors.append(
                f"class {klass}: verifier == builder ('{declared}') — §5.2 "
                "requires a different agent/model/human, or process:ci"
            )


def check_assert_review(status: str, ctx: ActiveCtx, klass: str, phase: str,
                        errors: list[str], warnings: list[str]) -> None:
    """Ревью утверждений пропорционально (§3.1d ур. 3, DoD §3.2.7).
    """
    verify = ctx.verify
    # --- §3.1d уровень 3 / DoD §3.2.7: ревью утверждений пропорционально
    # непроверяемой части. `n/a` законен, только если КАЖДОЕ утверждение
    # ведёт к примеру, который человек подписал до кода; иначе ожидание
    # придумал исполнитель, и подпись обязана быть.
    if klass in {"M", "L"} and phase in {"verify", "converge", "handoff"} and verify.is_file():
        vr = read(verify)
        reviewed = field(vr, "asserts_reviewed_by")
        digest = re.search(r"(?im)^\s*asserts_without_example:\s*(\d+)\s*$", vr)
        if is_placeholder(reviewed):
            errors.append(
                f"class {klass}: verify-report.md без asserts_reviewed_by "
                "(§3.1d уровень 3, DoD §3.2.7) — последний рубеж против "
                "неверного ожидания; вставь дайджест: "
                "bash scripts/lint/assert_digest.sh >> verify-report.md"
            )
        elif reviewed.lower().startswith("n/a"):
            if not digest:
                errors.append(
                    "asserts_reviewed_by: n/a без вставленного дайджеста "
                    "(нет строки 'asserts_without_example: N') — n/a надо "
                    "заслужить, а не заявить (§3.1d уровень 3)"
                )
            elif int(digest.group(1)) > 0:
                errors.append(
                    f"asserts_reviewed_by: n/a, но {digest.group(1)} "
                    "утверждений не ссылаются на примеры спеки — их ожидание "
                    "придумал исполнитель; нужна подпись human:… at=… "
                    "(§3.1d уровень 3)"
                )
        else:
            # Третье появление той же дыры. §2.2b закрыла её для `human_ok_spec`,
            # v1.30 — для `human_ok_plan`, и оба раза правка была на ПОЛЕ, а не на
            # класс полей: здесь ветка принимала ЛЮБУЮ непустую строку, поэтому
            # `asserts_reviewed_by: посмотрел сам` и `by=agent:…` проходили, а
            # автономному агенту не оставалось законного значения вовсе. Теперь
            # поле идёт через ту же развилку: подпись человека, либо `deferred`
            # с причиной, либо заслуженное `n/a` выше.
            e, w = signature_verdict("asserts_reviewed_by", reviewed, klass, phase)
            errors += e
            warnings += w


def check_expectation_and_oracle(status: str, ctx: ActiveCtx, klass: str,
                                 phase: str, errors: list[str],
                                 warnings: list[str]) -> None:
    """Ожидание раньше кода (§3.1d ур. 1) и реляционный оракул (§6.5 ур. 2).

    Два раздела в одной функции, хотя комментарии обещают два шва: последний
    блок ВЛОЖЕН в `if` предыдущего, и граница по маркеру его отрывает. Замер
    сказал это заранее («не парсится отдельно»), а первый заход сигнал
    проигнорировал — и сломал модуль дважды.
    """
    # ⚠ `ctx.tasks` и `ctx.verify` здесь БОЛЬШЕ НЕ БЕРУТСЯ, и пустота этой строки
    # — сама улика: функция перестала читать документы поставки о себе.
    spec = ctx.spec
    eval_smoke, implement_like = ctx.eval_smoke, ctx.implement_like
    # --- §3.1d уровень 1: ожидание обязано появиться РАНЬШЕ кода.
    # Проверяем не качество примеров (это невозможно), а сам факт, что они
    # есть и на них ссылаются тесты: иначе ожидание придумал тот же, кто писал
    # код, и все гейты ниже проверяют его согласие с самим собой.
    if klass in {"M", "L"} and phase in implement_like and spec.is_file():
        spec_text = read(spec)
        ex_ids = re.findall(r"(?m)^\s*\|\s*([A-Z]\d+)\s*\|", spec_text)

        # Корпуса «где искать». Строятся БЕЗУСЛОВНО, потому что их читают ДВЕ
        # независимые проверки — ссылки на id ниже и реляционный оракул §6.5.
        #
        # lab-12: корпус строился внутри else-ветки, и обе ветки с ошибкой роняли
        # гейт `UnboundLocalError: haystack` ВМЕСТО того, чтобы напечатать уже
        # сформулированный диагноз. Класс: **проверка, обязанная поставить
        # диагноз, умирает вместо диагноза** — исполнитель видит поломку
        # инструмента там, где ему сообщали о его собственной ошибке. Это
        # зеркало «гейта, врущего зелёным»: тот молчит, когда должен говорить,
        # этот кричит не о том. Мера — не «инициализировать переменную», а
        # держать сбор данных отдельно от разбора причин.
        #
        # ⚠ Корпусов ДВА, и это несущее (`delivery@1.72`, поле). Был один, и в
        # него входили `tasks.md` и `verify-report.md` — то есть ДОКУМЕНТЫ О
        # ПОСТАВКЕ. §6.5 ищет в корпусе `@given`, поэтому проверка гасилась
        # УПОМИНАНИЕМ декоратора в прозе отчёта: зелёное получено словом о
        # свойстве, а не свойством. Поймано так: первая редакция отчёта
        # процитировала имя искомого декоратора, ОБЪЯСНЯЯ этот самый warning, —
        # и warning исчез. Ссылки на id держали тот же дефект и заметнее: отчёт
        # перечисляет id примеров штатно, поэтому «id встречается в тестах»
        # удовлетворялось таблицей в verify-report.
        #
        # Правило к КЛАССУ: корпус проверки не смеет содержать текст, который
        # ОПИСЫВАЕТ эту проверку, — описание там неотличимо от предмета. Канон
        # это правило уже произносил в двух шагах отсюда («в них нельзя спрятать
        # неверное ожидание», §6.5) и рядом же нарушал; ловит расхождение теперь
        # не глаз, а `test_green_without_the_thing.py`.
        #
        # Границы корпусов заданы тем, что проверка СПРАШИВАЕТ. §6.5 спрашивает
        # про тест — значит только тексты тестов. Ссылки на id канон и сам
        # описывал как «в тестах ИЛИ eval-smoke» (см. ветку ниже) — значит проза
        # попала в корпус мимо собственной формулировки: `tasks.md` обещает,
        # `verify-report.md` отчитывается, а вопрос был про проверку.
        tests_text = ""
        for root_dir in ("tests", "backend/tests", "src/tests"):
            d = ACTIVE.parent.parent / root_dir
            if not d.is_dir():
                continue
            for f in d.rglob("*test*"):
                # Только текстовые исходники и не служебные каталоги:
                # маска ловила .pyc и роняла гейт (F9).
                if (
                    f.is_file()
                    and f.suffix in TEST_TEXT_SUFFIXES
                    and not any(p in SKIP_DIR_PARTS for p in f.parts)
                ):
                    tests_text += read(f)

        check_examples_and_ids(spec_text, ex_ids,
                               tests_text + read(eval_smoke), klass, phase,
                               errors, warnings)

        check_relational_oracle(tests_text, klass, warnings)


def check_relational_oracle(tests_text: str, klass: str,
                            warnings: list[str]) -> None:
    """Хотя бы один реляционный оракул на M/L (§6.5 уровень 2).

    Пятый шов (`delivery@1.57`) — ВНУТРИ функции, а не по её шву: блок вложен
    в `if` соседнего раздела и берёт корпус параметром.

    ⚠ Параметр называется `tests_text`, а не `haystack`, намеренно: имя корпуса
    и есть содержание правки `delivery@1.72`. Сюда приходят ТОЛЬКО тексты
    тестов; проза поставки описывает искомое свойство теми же словами, и под
    именем «стог» это неразличимо.
    """
    # --- §6.5 уровень 2: хотя бы один реляционный оракул на M/L.
    # «Минимум один» стояло в §6.5 практикой и не принуждалось ничем —
    # та же форма, что F7. Пример со значением (`f(2) == 4`) закрепляет
    # баг, если ожидание списано с кода, и мутационный гейт его не поймает:
    # мутант будет честно убит НЕВЕРНЫМ утверждением. Свойство подделать
    # нельзя, потому что ожидаемого значения в нём нет.
    #
    # ⚠ Это проверка НАЛИЧИЯ, и она заполняется галочкой: `@given(...)` с
    # `assert True` её проходит. Ловит такое не она, а мутационный гейт —
    # декоративное свойство оставляет мутантов живыми. Поэтому требование
    # осмысленно ТОЛЬКО при работающем mutation (на macOS —
    # `brew install coreutils`, CQG §5 шаг 3), и об этом сказано в тексте
    # ошибки: иначе получим ритуал вместо оракула.
    if not re.search(r"@given|@hypothesis\.given|fc\.assert|fc\.property",
                     tests_text):
        warnings.append(
            f"class {klass}: ни одного реляционного оракула (§6.5) — "
            "не найдено ни `@given` (hypothesis), ни `fc.property` "
            "(fast-check). Инвариант, round-trip, идемпотентность, "
            "метаморфное отношение или differential: в них нет ожидаемого "
            "значения, поэтому в них нельзя спрятать неверное ожидание. "
            "Один инвариант обычно ловит больше десяти тестов-значений, "
            "потому что раннер перебирает входы, о которых автор не думал. "
            "Проверь заодно, что mutation-гейт у тебя не пропускается: "
            "иначе слабое свойство (`assert result is not None`) пройдёт"
        )


def check_examples_and_ids(spec_text: str, ex_ids: list[str], refs_text: str,
                           klass: str, phase: str, errors: list[str],
                           warnings: list[str]) -> None:
    """Блок примеров есть, id проставлены, id встречаются в тестах.

    Шестой шов (`delivery@1.57`): цепочка `if/elif/else` — это три ответа на
    один вопрос, и она уносит из вызывающей функции почти всю сложность.
    """
    if not re.search(r"(?im)^#+\s*acceptance\s+examples", spec_text):
        errors.append(
            f"class {klass}: spec.md без блока '## Acceptance examples' "
            "(§3.1d) — конкретные вход→выход, подтверждённые человеком "
            "ДО кода; проза «работает корректно» подписью не является"
        )
    elif not ex_ids:
        # Грамматика id названа ЯВНО: она не очевидна, а её нарушение
        # раньше выглядело как «примеров нет» при полной таблице примеров.
        # lab-12: арка написала id вида `EX1` и получила краш; вторая арка
        # переименовала свои id, обходя соседнюю проблему, — то есть
        # неназванное правило заставляло подстраиваться вслепую.
        errors.append(
            "spec.md: блок Acceptance examples есть, но примеров с id в "
            "нём нет (§3.1d). id читается из первой колонки таблицы и "
            "обязан быть вида ОДНА заглавная латинская буква + цифры "
            "(`A1`, `E2`); `EX1`, `A.1`, `1` и `случай-1` не считаются"
        )
    else:
        # id должны встречаться в тестах или eval-smoke: связь примера с
        # проверкой — то, что отличает обещание от отчёта о реализации.
        missing = [i for i in dict.fromkeys(ex_ids) if i not in refs_text]
        if missing:
            warnings.append(
                "acceptance-примеры без ссылки в тестах/eval-smoke: "
                f"{', '.join(missing)} (§3.1d) — пометь тест id примера"
            )

        # Порядок «пример раньше теста» — на verify и дальше: раньше
        # тестов может просто не быть, и проверка ругалась бы на штатное
        # состояние фазы implement.
        if phase in {"verify", "converge", "handoff"}:
            test_dirs = [d for d in ("tests", "backend/tests", "src/tests")
                         if (ACTIVE.parent.parent / d).is_dir()]
            warnings += expectation_predates_tests(ex_ids, test_dirs)


def check_observability_ladder(status: str, sig: str, errors: list[str]) -> None:
    """Ступень наблюдаемости 2–3 требует своих полей (§13.3).

    Седьмой шов (`delivery@1.57`): вынесен не по длине, а по СЛОЖНОСТИ —
    `check_archive_obligation` укладывалась в 80 строк, но давала cx 11 при
    пороге 10. §2.1 меряет обе оси, и вторая тут была решающей.
    """
    level = field(status, "observability")
    if not is_placeholder(level) and level.strip().startswith(("2", "3")):
        # Цифра, которой НЕ предшествует латинская буква: так число порога
        # (`180мс`) отличается от id примера (`A1`) и от имени метрики
        # (`p95`). Наивный поиск любой цифры проходил на сигнале
        # «A1 подтверждён, ошибок нет» — то есть по неверной причине.
        if not re.search(r"(?<![A-Za-z])\d", sig):
            errors.append(
                f"observability: {level.strip()} заявлен, но observe_signal "
                "без метрики с числом (§13.3) — уровень 2+ означает замер "
                "ДО и порог, а не «мониторинг вроде подключён»"
            )


def check_evidence(status: str, args, errors: list[str], warnings: list[str], ctx: ActiveCtx) -> None:
    """Ожидание раньше кода, утверждения отревьюены, багфикс принёс тест."""
    phase, klass, spec, tasks, verify, eval_smoke, implement_like = (
        ctx.phase,
        ctx.klass,
        ctx.spec,
        ctx.tasks,
        ctx.verify,
        ctx.eval_smoke,
        ctx.implement_like)
    # --- Долг не должен болтаться вечно (§3.5). Проверяется ТОЛЬКО на handoff:
    # на каждом коммите это блокировало бы работу, не связанную со старым
    # долгом, и такой гейт отключают через неделю. Работать не мешаем —
    # не даём объявить сделанным.
    if phase == "handoff":
        errors.extend(debt_is_not_frozen())

    check_archive_obligation(status, ctx, phase, errors)

    check_builder_verifier(status, ctx, klass, phase, errors)

    check_assert_review(status, ctx, klass, phase, errors, warnings)

    check_expectation_and_oracle(status, ctx, klass, phase, errors, warnings)

