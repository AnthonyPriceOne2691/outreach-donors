#!/usr/bin/env python3
"""Пробы исполнением: гейт обязан покраснеть на нарушении своего класса.

Часть доктора (`cqg@1.82`, вход — `contour_doctor.py`). Принцип — пробовать
ИСПОЛНЕНИЕМ, а не чтением: каждому гейту подсовывается канарейка (данные —
`doctor_canaries.py`), и молчание на своём же нарушении объявляется `DEAD`.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from doctor_canaries import (BARE_PATH, CANARIES, DIRECT_SETUP, OWN_CANARIES,
                             TOOL_DEPENDENT)
from doctor_core import (ABSENT, AUTO, DEAD, NOT_GATES, SKIP, SKIP_WORDS,
                         SUCCESS_WORDS, TOOL, WEAK, run)


def copy_with_parts(script: Path, dst_dir: Path) -> None:
    """Скопировать гейт В КОМПЛЕКТЕ с его модулями (`cqg@1.83`).

    С `cqg@1.82` payload режется по планке 300 строк, и «скопировать один файл»
    перестало работать: `check_ast_gate.py` без `ast_rules.py` падает на импорте.
    Проба, копирующая вход в одиночку, получила бы ненулевой код возврата и
    зачла его за «канарейка поймана» — то есть объявила бы работающим гейт,
    который вообще не запустился. Поймано собственным тестом сьюта, не полем.

    Модули отличаются от гейтов по имени: гейт — `check_*`, всё остальное `.py`
    и `.sh` рядом — части (bash режется тем же приёмом, только через `source`). Признак тот же, по которому их не считает мета-гейт, поэтому
    второго источника истины не возникает. Снимки (`*_baseline.txt`) НЕ копируются
    сознательно: канарейка, легализованная чужим снимком, не покраснеет.
    """
    shutil.copy(script, dst_dir / script.name)
    for part in sorted(script.parent.iterdir()):
        if part.suffix not in (".py", ".sh") or part.name == script.name:
            continue
        if not part.name.startswith("check_"):
            shutil.copy(part, dst_dir / part.name)


def probe_cmd(script: Path, target: Path | None = None,
              rule: str | None = None) -> list[str]:
    """Чем запускать гейт в пробе: интерпретатор по расширению плюс `--rule`.

    Шов по ПОВТОРУ: одну и ту же тройку (питон/баш, путь, правило) собирали
    четыре пробы, и каждый экземпляр стоил двух ветвлений функции, которая
    судит совсем о другом. `target` — путь В ПОЛИГОНЕ, если проба копирует гейт
    к себе; без него гейт запускается там, где лежит.
    """
    cmd = ["python3"] if script.suffix == ".py" else ["bash"]
    cmd += [str(script if target is None else target)]
    return cmd + ["--rule", rule] if rule else cmd


class ProbeChecks:
    def check_canaries(self) -> None:
        d = self.root / "scripts" / "lint"
        if not d.is_dir():
            self.add(ABSENT, "проба канарейкой", "нет scripts/lint")
            return
        for script in sorted(d.glob("*")):
            if not script.is_file():
                continue
            name = script.name
            if name in NOT_GATES or name == "contour_doctor.py":
                self.add(TOOL, f"{name}", "измерительный инструмент, не гейт — "
                                          "канарейке нечего ловить")
                continue
            if not name.startswith("check_"):
                continue
            self._probe_script(script)

    def _probe_script(self, script: Path) -> None:
        """Чем пробовать ЭТОТ гейт: своей канарейкой, честным пропуском, состоянием.

        Шов по данным: выбор ветки не отдаёт наружу ни одного имени — каждая
        проба печатает вердикт сама, поэтому `continue` тела цикла честно
        становится `return`, а обход остаётся у `check_canaries`.

        Объявленная проектом канарейка идёт ПЕРВОЙ — раньше пробы честного
        пропуска. До cqg@1.67 порядок был обратным, и для tool-зависимого гейта
        до `_probe_declared` дело не доходило НИКОГДА: объявление принималось и
        молча не исполнялось. Проект, положивший канарейку, тем самым
        утверждает, что инструмент у него есть и класс нарушения ему известен, —
        это сильнее, чем проверка «а честно ли гейт пропускает без инструмента».
        """
        name = script.name
        if name in self.own:
            for rule, path, body in self.own[name]:
                self._probe_declared(script, path, body, rule)
            return
        if name in TOOL_DEPENDENT:
            self._probe_honest_skip(script, TOOL_DEPENDENT[name])
            return
        if name in DIRECT_SETUP:
            self._probe_state(script, DIRECT_SETUP[name])
            return
        rules = self._rules_of(script)
        if not rules:
            # Односложный гейт: `--rule` он не принимает, канарейка ищется по
            # имени скрипта. Прежняя версия отправляла такие в SKIP целиком —
            # то есть доктор не пробовал file-length и сложность вообще.
            self._probe(script, None)
            return
        for rule in rules:
            self._probe(script, rule)

    def _probe_state(self, script: Path, setup) -> None:
        """Канарейка — СОСТОЯНИЕ репозитория, а не файл с нарушением в коде."""
        point = f"канарейка {script.name}"
        with tempfile.TemporaryDirectory(prefix="doctor-state-") as tmp:
            lab = Path(tmp)
            (lab / "scripts" / "lint").mkdir(parents=True)
            (lab / "backend" / "features").mkdir(parents=True)
            (lab / "backend" / "features" / "a.py").write_text("x = 1\n", encoding="utf-8")
            copy_with_parts(script, lab / "scripts" / "lint")
            run(["git", "init", "-q", "."], lab)
            run(["git", "add", "-A"], lab)
            run(["git", "-c", "user.email=d@d", "-c", "user.name=d",
                 "commit", "-qm", "base"], lab)
            extra_args, extra_env = setup(lab)
            cmd = probe_cmd(script, lab / "scripts" / "lint" / script.name) + extra_args
            code, out = run(cmd, lab, env={"LINT_PY_SRC": "backend/features",
                                           **extra_env})
            if code != 0:
                self.add(AUTO, point, "канарейка поймана")
            elif any(w in out for w in SKIP_WORDS):
                self.add(WEAK, point, out.strip().splitlines()[0][:78])
            else:
                self.add(DEAD, point,
                         f"МОЛЧИТ на подготовленном нарушении: {out.strip()[:66]!r}")

    def _probe_honest_skip(self, script: Path, tool: str) -> None:
        """Инструмента нет — гейт обязан НАЗВАТЬ пропуск и не выдать его за успех.

        Прямая канарейка тут потребовала бы самого инструмента (сеть, npm, venv), а
        это свойство важнее и проверяется без него. Класс ловился трижды: `timeout`
        на macOS ронял мутационный гейт в «мутанты не сгенерированы» и exit 0 (F14);
        secrets-хук с голым `command -v` печатал `Passed`, не просмотрев ни файла
        (F10); `deps_audit_waiver: no` работал как разрешение при critical=1.
        """
        # ⚠ В названии точки сказано, ЧЬЁ окружение проверяется. Проба идёт с
        # голым PATH и без переменных хука — это её замысел, но вердикт
        # «нет каталога фронта (frontend)» читался как диагноз ПРОЕКТУ, хотя под
        # pre-commit тот же гейт работает. Полевой аудит поймал ровно это:
        # доктор писал WEAK там, где настроено верно. Зонд, не воспроизводящий
        # окружение хука, обязан хотя бы не выдавать себя за него.
        point = f"честный пропуск {script.name} (в пробе нет {tool})"
        with tempfile.TemporaryDirectory(prefix="doctor-skip-") as tmp:
            lab = Path(tmp)
            (lab / "backend" / "features").mkdir(parents=True)
            (lab / "scripts" / "lint").mkdir(parents=True)
            copy_with_parts(script, lab / "scripts" / "lint")
            run(["git", "init", "-q", "."], lab)
            run(["git", "add", "-A"], lab)
            run(["git", "-c", "user.email=d@d", "-c", "user.name=d",
                 "commit", "-qm", "base"], lab)
            run(["git", "branch", "-f", "b0"], lab)
            # Изменённый prod-файл появляется ПОСЛЕ базы: иначе диффа нет, гейт
            # честно скажет «изменённых prod-файлов нет», а доктор примет честный
            # ответ за ложь. Первая версия пробы делала ровно так и дала ложный
            # DEAD у diff-coverage — неверна была проба, а не гейт.
            (lab / "backend" / "features" / "a.py").write_text(
                "def f(x):\n    return x + 1\n", encoding="utf-8")
            run(["git", "add", "-A"], lab)
            run(["git", "-c", "user.email=d@d", "-c", "user.name=d",
                 "commit", "-qm", "change"], lab)
            cmd = probe_cmd(script, lab / "scripts" / "lint" / script.name)
            code, out = run(cmd, lab, env={"PATH": BARE_PATH, "BASE": "b0",
                                           "LINT_PY_SRC": "backend/features",
                                           "MUTATION_NO_BUDGET": "0"})
            named = any(w in out for w in SKIP_WORDS)
            claims_ok = any(w in out for w in SUCCESS_WORDS)
            # Заявленный успех бьёт названный пропуск, а не наоборот. Гейт может
            # шепнуть «○ пропущено: …» в теле и напечатать `: OK` в итоге — читают
            # итог, и сводка pre-commit показывает `Passed`. Это форма F10, и она
            # DEAD, даже если пропуск где-то упомянут.
            # Проверено обратным прогоном: пока это условие стояло как
            # `claims_ok and not named`, переменная была МЁРТВОЙ — вердикт давала
            # финальная ветка, и опустошение SUCCESS_WORDS ничего не меняло.
            if claims_ok:
                self.add(DEAD, point,
                         f"инструмента НЕТ, а гейт заявляет успех: {out.strip()[:70]!r}")
            elif named:
                self.add(WEAK, point, out.strip().splitlines()[0][:78])
            elif code != 0:
                self.add(AUTO, point, "инструмента нет → гейт краснеет, а не молчит")
            else:
                self.add(DEAD, point,
                         f"пропуск НЕ НАЗВАН и не красный: {out.strip()[:70]!r}")

    def _probe_declared(self, script: Path, path: str, content: str,
                        rule: str | None = None) -> None:
        """Гейт по канарейке, объявленной проектом — ДИФФЕРЕНЦИАЛЬНО и В ДЕРЕВЕ.

        Прогонов два: **зелен без канарейки и красен с ней**. Одного «красный с
        канарейкой» мало — гейт мог упасть на отсутствии окружения; тогда судить
        нечем, и это SKIP с названной причиной, а не AUTO.

        ⚠ **Проба идёт в дереве САМОГО ПРОЕКТА, и это исправление, а не стиль.**
        До cqg@1.67 канарейка разворачивалась во временном репозитории, куда
        копировались только скрипт и `canaries.json`. Для гейта, стоящего на
        внешнем инструменте, там нет ни `node_modules`, ни `tsconfig.json`, ни
        конфига контрактов — то есть **проектный контракт был непроверяем в
        принципе**, и объявление молча не исполнялось. Замерено на живом
        развёртывании: проект объявил две канарейки, доктор напечатал `DEAD 0` и
        ни строки про них. Принятое и неисполненное объявление хуже отсутствующего.

        Цена: доктор пишет файл в рабочее дерево. Поэтому — отказ, если файл уже
        есть (чужое не трогаем), и удаление в `finally` вместе с каталогами,
        которые создали сами. Коммитить канарейку НЕЛЬЗЯ: индекс проекта не наш.
        """
        point = f"канарейка {script.name}" + (f":{rule}" if rule else "") + " (своя)"
        target = self.root / path
        cmd = probe_cmd(script, rule=rule)

        clean = target.exists()
        (before_code, before_out), (after_code, after_out) = self._run_with_canary(
            cmd, target, content)

        # Гейт, судящий по `git ls-files`, некоммитнутой канарейки не увидит, и
        # его зелёное здесь ничего не значит. Отличаем это от настоящего DEAD:
        # молчать «проверено» на непроверенном — ровно то, против чего доктор.
        body = script.read_text(encoding="utf-8", errors="replace")
        if before_code == 0 and after_code == 0 and "ls-files" in body:
            self.add(SKIP, point, "гейт судит по `git ls-files`, а канарейка не "
                                  "закоммичена — проверить исполнением нечем "
                                  "(индекс проекта доктор не трогает)")
            return

        if before_code != 0:
            self.add(SKIP, point, "гейт красный и БЕЗ канарейки "
                                  f"({before_out.strip()[:60]!r}) — судить нечем")
        elif after_code != 0:
            self.add(AUTO, point, "канарейка поймана"
                                  + (f" ({path})" if not clean else ""))
        elif any(w in after_out for w in SKIP_WORDS):
            self.add(WEAK, point, after_out.strip().splitlines()[0][:80])
        else:
            self.dead_gates.add(script.name)
            self.add(DEAD, point, "МОЛЧИТ на объявленной канарейке (exit 0): "
                                  f"{after_out.strip()[:60]!r}")

    def _run_with_canary(self, cmd: list[str], target: Path, content: str):
        """Два прогона гейта — БЕЗ канарейки и С ней — и дерево, как было.

        Шов по данным: наружу блок отдаёт ровно две пары «код, вывод»; `backup`
        и `made` границу не пересекают, и это несущее свойство — восстановление
        обязано стоять там же, где порча, в `finally` рядом с ней.

        Канарейка бывает ДВУХ видов, и оба законны: новый файл (нарушение,
        которого в дереве нет) и ПОДМЕНА существующего (вендоренная копия
        канона — находка 7). Первая редакция пробы в дереве отказывалась
        трогать существующий файл и тем сломала второй вид — поймано
        собственным сьютом, а не полем. Поэтому: содержимое сохраняем и
        возвращаем, созданное — удаляем.
        """
        backup = target.read_text(encoding="utf-8") if target.exists() else None
        made: list[Path] = []
        d = target.parent
        while not d.exists() and d != self.root:
            made.append(d)
            d = d.parent
        try:
            before = run(cmd, self.root)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            after = run(cmd, self.root)
        finally:
            if backup is None:
                if target.exists():
                    target.unlink()
                for d in made:                  # только свои, снизу вверх
                    try:
                        d.rmdir()
                    except OSError:
                        break
            else:
                target.write_text(backup, encoding="utf-8")
        return before, after

    def _probe(self, script: Path, rule: str | None) -> None:
        point = f"канарейка {script.name}" + (f":{rule}" if rule else "")
        canary = CANARIES.get(rule or script.name)
        if canary is None:
            declared = self.own.get(script.name)
            if declared:
                for d_rule, d_path, d_body in declared:
                    self._probe_declared(script, d_path, d_body, d_rule or rule)
                return
            self.add(SKIP, point, "канарейки для этого правила у доктора нет — "
                                  "правило НЕ проверено исполнением. Свой гейт? "
                                  f"объяви канарейку в {OWN_CANARIES} "
                                  '({"path": …, "content": …})')
            return
        fname, body = canary
        with tempfile.TemporaryDirectory(prefix="doctor-") as tmp:
            lab = Path(tmp)
            src = lab / "backend" / "features"
            src.mkdir(parents=True)
            (src / fname).write_text(body, encoding="utf-8")
            (lab / "scripts" / "lint").mkdir(parents=True)
            copy_with_parts(script, lab / "scripts" / "lint")
            run(["git", "init", "-q", "."], lab)
            run(["git", "add", "-A"], lab)
            run(["git", "-c", "user.email=d@d", "-c", "user.name=d",
                 "commit", "-qm", "canary"], lab)
            cmd = probe_cmd(script, lab / "scripts" / "lint" / script.name, rule)
            code, out = run(cmd, lab, env={"LINT_PY_SRC": "backend/features"})
            named_skip = any(w in out for w in
                             ("пропущен", "не судит", "не проверен", "нет каталога"))
            if code != 0:
                self.add(AUTO, point, "канарейка поймана")
            elif named_skip:
                self.add(WEAK, point, out.strip().splitlines()[0][:80])
            else:
                self.dead_gates.add(script.name)
                self.add(DEAD, point,
                         f"МОЛЧИТ на своём же нарушении (exit 0): {out.strip()[:70]!r}")

