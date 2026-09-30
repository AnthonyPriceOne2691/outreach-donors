#!/usr/bin/env python3
"""Видит ли подключённый гейт код ЭТОГО проекта (`cqg@1.69`; сверка с деревом 1.80).

Часть доктора (`cqg@1.82`, вход — `contour_doctor.py`). Вторая половина вопроса
«а судит ли вписанное»: первая — «умеет ли гейт краснеть» — закрыта канарейкой
(`doctor_probes.py`), эта — «нацелен ли он на код». Значения `LINT_*` и `files:`
хука читает `doctor_layout.py`: это раскладка, а суждение здесь.
"""

from __future__ import annotations

import fnmatch
import re

from doctor_core import AUTO, DEAD, NOT_GATES, SKIP, SKIP_WORDS, SUCCESS_WORDS, WEAK, run

#: Часть гейта, подключённая РЯДОМ: `. "$SCRIPT_DIR/x.sh"` у shell, `from x import`
#: у python. Оба написания есть в payload'е — с `cqg@1.82` он режется по планке 300
#: строк, и у семи гейтов половина кода живёт в соседнем файле.
_PART_SH = re.compile(r'\$\{?SCRIPT_DIR\}?/([A-Za-z0-9_]+\.sh)')
#: ⚠ `\b` обязателен: без него `ESLINT_ABS` читается как переменная `LINT_ABS`
#: (внутри слова «ESLINT» лежит «LINT»), и доктор знал бы имена, которых нет.
_LINT_VAR = re.compile(r'\bLINT_[A-Z_]+')
_PART_PY = re.compile(r'(?m)^from\s+([a-z0-9_]+)\s+import\b')


def _komplekt(script) -> str:
    """Тело гейта ВМЕСТЕ с его частями — комплектом, а не входным файлом.

    Читать один вход нельзя: после разреза `cqg@1.82` вопрос «какие переменные
    гейт читает» переехал в часть вместе с кодом (у гейта сложности TS-половина
    целиком в `complexity_halves.sh`), и по входу он выглядел бы не читающим
    ничего. Тот же приём, что `extract.whole()` в сьюте канона.
    """
    head = script.read_text(encoding="utf-8", errors="replace")
    out = [head]
    for name in _PART_SH.findall(head) + [m + ".py" for m in _PART_PY.findall(head)]:
        part = script.parent / name
        if part.is_file() and part.name != script.name:
            out.append(part.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(out)


def _own_vars(env: dict, body: str) -> dict:
    """`LINT_*` из хука, которые гейт ДЕЙСТВИТЕЛЬНО читает (`cqg@2.04`).

    Ложное красное, найденное полем: доктор печатал `DEAD область
    check_grep_gate.sh — маска гейта не покрывает 18 файл(ов) области:
    backend/tests/…` при ВЕРНО настроенном гейте. В `entry:` стоял общий на все
    гейты префикс (`LINT_PY_SRC=… LINT_COV_PKG=… LINT_FE_DIR=… LINT_BE_DIR=…`),
    и `LINT_BE_DIR` затянул в знаменатель весь backend — при том что предмет
    гейта `backend/app/**`, а тесты он не смотрит НАМЕРЕННО. Переменную эту
    `check_grep_gate.sh` не читает вовсе.

    Общий префикс — законная и удобная запись: §6 велит держать значения в
    `entry:`, и раскладывать их по хукам поштучно она не требует. Значит корни
    области обязаны браться из переменных гейта, а не из строки хука.

    Признак вычислим и проверяется тем же грепом: имя `LINT_*` в КОДЕ комплекта.
    Комментарии сняты, и это не микрооптимизация — шестой рецидив класса, ради
    которого в сьюте живёт `extract.code_only()`: `check_file_length.sh`
    упоминает `LINT_PY_SRC` в комментарии, который ОБЪЯСНЯЕТ, что гейт его не
    читает. По комментарию доктор бы этот корень и взял.
    """
    code = "\n".join(l for l in body.splitlines() if not l.lstrip().startswith("#"))
    named = set(_LINT_VAR.findall(code))
    return {k: v for k, v in env.items() if k in named}


def _gate_command(script, text: str, *, multi_rule: bool) -> list[str] | None:
    """Чем запускать гейт; None — запускать нечем: правил много, а `--rule` нет.

    Шов по данным: наружу блок отдавал одно имя — `cmd`. `wired` посчитан в
    ветке и в ней же прочитан, поэтому за границу не выносится, а выход из
    цикла остаётся у вызывающего — здесь он превращается в `None`.

    Многоправильный гейт без `--rule` падает с usage. Область у правил одного
    скрипта общая, поэтому довольно первого вписанного.
    """
    cmd = (["python3"] if script.suffix == ".py" else ["bash"]) + [str(script)]
    if not multi_rule:
        return cmd
    wired = re.findall(rf"{re.escape(script.name)}\s+--rule\s+([A-Za-z0-9_-]+)", text)
    if not wired:
        return None
    return cmd + ["--rule", wired[0]]


class AreaChecks:
    # --- Видит ли подключённый гейт код ЭТОГО проекта (cqg@1.69) --------------
    # Вторая половина вопроса «а судит ли вписанное». Первая — «умеет ли гейт
    # краснеть» — закрыта канарейкой. Эта — «нацелен ли он на код», и до сих пор
    # её не задавал никто:
    #
    #   gate-coverage  — вписан ли скрипт в конфиг. ТЕКСТОВЫЙ вопрос: хук с
    #                    неверной маской вписан и проходит;
    #   канарейка      — умеет ли гейт краснеть. Проба СВОЯ, поэтому слепой гейт
    #                    ловит канарейку прекрасно и остаётся слепым к проекту;
    #   сьют канона    — ловит ли правило нарушение. Стенд синтетический: тест
    #                    сам пишет файлы, которые потом проверяет, поэтому
    #                    расхождение маски с расширениями проекта там невыразимо
    #                    ПО ПОСТРОЕНИЮ.
    #
    # Замер, из которого правило родилось (Astro/TS, четыре гейта разом):
    # `file-length` с дефолтной маской `*.py *.ts *.tsx` не видел `.astro` —
    # файл на 900 строк давал «OK — просмотрено 1 файл(ов)», exit 0; eslint,
    # его ратчет и prettier стояли с шаблонной маской `^frontend/src/`, каталога
    # такого нет — три `Skipped (no files to check)`. Мета-гейт при этом зелёный,
    # доктор `DEAD 0`. Роль объявлена закрытой в карте ролей, гейт смотрит в
    # пустоту, и молчание выглядит как успех.
    #
    # Гоняются ТОЛЬКО гейты, вписанные в `.pre-commit-config.yaml`, и это не
    # осторожность, а точный признак дешевизны: §8.6 держит бюджет коммита в 5
    # секунд, значит всё, что там стоит, дёшево ПО ПОСТРОЕНИЮ. Сетевые и
    # минутные (`deps-audit`, `mutation`, `diff-coverage`) живут в CI и сюда не
    # попадают — их и не запустим.
    # ⚠ ДВЕ формулировки, и порядок слов в них РАЗНЫЙ: успех печатает
    # «просмотрено N файл(ов)», а ноль — «0 файлов просмотрено — проверь LINT_…».
    # Первая редакция знала только первую форму, и слепой гейт попадал в SKIP
    # вместо DEAD — то есть проверка на «зелёный на непроверенном» сама молчала
    # ровно на том случае, ради которого написана. Поймано третьим прогоном на
    # живом проекте (`check_grep_gate.sh` с дефолтным `LINT_PY_SRC`).
    #
    # ЕДИНИЦА захватывается вместе с числом, и это несущая часть (`cqg@1.80`):
    # гейты считают разное — «файл(ов)», «манифест(ов)» (deps-audit),
    # «модул(ей)» (слои ts). Сверять число манифестов с числом файлов дерева
    # значило бы выдумать расхождение там, где сравнивают несравнимое.
    #: Маска, которую гейт называет сам (`cqg@1.88`): «по маске: *.py src/**».
    MASK_RE = re.compile(r"по маске:\s*(.+?)\s*$", re.M)

    SCANNED_RE = re.compile(r"просмотрено\s+(\d+)\s*([А-Яа-яЁё]*)"
                            r"|(\d+)\s+([А-Яа-яЁё]+)\s+просмотрено")

    def check_gates_see_code(self) -> None:
        d = self.root / "scripts" / "lint"
        cfg = self.root / ".pre-commit-config.yaml"
        if not d.is_dir() or not cfg.is_file():
            return
        raw = cfg.read_text(encoding="utf-8", errors="replace")
        # ⚠ Конфиг читается БЕЗ комментариев. Шапка поставляемого файла объясняет
        # настройку ПРИМЕРОМ («… bash scripts/lint/check_grep_gate.sh --rule …»),
        # и проверка «вписан ли гейт» ловила этот пример как проводку: доктор
        # гонял гейт, снятый с коммита, и честно объявлял его слепым. Пятый
        # рецидив класса, ради которого в сьюте живёт `extract.code_only()`, —
        # и второй за одну правку: читалку env чинили тем же способом абзацем ниже.
        text = "\n".join(l for l in raw.splitlines() if not l.lstrip().startswith("#"))

        # ⚠ Судить слепоту можно ТОЛЬКО там, где есть чему быть увиденным.
        # На bootstrap-развёртывании (контур поставлен, кода ещё нет) ноль
        # просмотренных — честная бедность, а не ложь, и красный доктор на таком
        # стенде снимут первым: §4.3b, и ровно об этом предупреждает собственный
        # тест процедуры («доктор, красный на неполном стенде, снимают первым»).
        # Поймано этим тестом, а не полем.
        files = self._source_files()
        if not files:
            self.add(SKIP, "область гейтов",
                     "в репозитории нет исходников вне scripts/lint — слепоту "
                     "судить не на чем (bootstrap: контур есть, кода ещё нет)")
            return
        off_commit: list[str] = []
        self._blind_pending: list = []
        #: Маски гейтов, которые ДЕЙСТВИТЕЛЬНО судят: попадают сюда только те,
        #: что просмотрели хоть один файл и не промолчали на своей канарейке.
        #: Слепой или мёртвый гейт покрытием не считается — иначе объединение
        #: масок оправдывало бы файл ссылкой на того, кто его не судит, а это
        #: ровно та ложь, против которой вся проверка площади.
        self._live_masks: list[str] = []
        for script in sorted(d.glob("check_*")):
            label = self._judge_gate(script, text, files)
            if label:
                off_commit.append(label)
        self._judge_blindness_together()

        if off_commit:
            self.add(WEAK, "область: не на коммите",
                     f"{len(off_commit)} гейт(ов) вписаны мимо коммит-стадии и "
                     f"областью НЕ пробованы: {', '.join(off_commit)}. §8.6 их "
                     "не покрывает (сеть/минуты), поэтому доктор их не запускает")

    def _judge_gate(self, script, text: str, files: list[str]) -> str | None:
        """Один гейт: слеп ли он к коду проекта. → метка «не на коммите» либо None.

        Шов по данным: из тела цикла наружу уходило РОВНО одно имя — строка для
        `off_commit`. Всё прочее (`cmd`, `env`, `out`, `n`) живёт одну итерацию,
        поэтому каждый `continue` честно становится `return None`, а решение
        «добавлять ли в список» остаётся у вызывающего.

        ⚠ Гейт ЗАПУСКАЕТСЯ со всем окружением хука, а СУДИТСЯ по своим
        переменным (`_own_vars`): передать меньше значило бы мерить не тот путь,
        а сверять по чужим — обвинять за общий префикс в `entry:` (`cqg@2.04`).
        """
        name = script.name
        if not script.is_file() or name in NOT_GATES or name not in text:
            return None                       # не гейт либо не вписан в конфиг
        label = self._off_commit_label(text, name)
        if label:
            return label
        point = f"область {name}"
        body = _komplekt(script)
        multi_rule = "--list-rules" in body
        cmd = _gate_command(script, text, multi_rule=multi_rule)
        if cmd is None:              # многоправильный гейт вписан без `--rule`
            return None
        env = self._hook_env(text, name)
        code, out = run(cmd, self.root, env=env)
        mine = _own_vars(env, body)
        scanned = self._scanned(out)
        if scanned is None:
            # Гейт не отчитывается числом — либо не сканирующий, либо красный
            # по делу. Молча зачесть за успех нельзя, но и ронять не за что.
            self.add(SKIP, point, "гейт не печатает «просмотрено N» — "
                                  "сканирующий ли он, отсюда не видно")
            return None
        n, unit = scanned
        if not n:
            self._judge_zero(point, out, mine, multi_rule=multi_rule)
            return None
        mask_m = self.MASK_RE.search(out)
        mask = mask_m.group(1).split() if mask_m else []
        if name not in self.dead_gates:
            self._live_masks.extend(mask)
        self._judge_area(point, n, unit, files, mine, multi_rule=multi_rule,
                         files_re=self._hook_files_re(text, name), mask=mask)
        return None

    def _scanned(self, out: str) -> tuple[int, str] | None:
        """«просмотрено N единиц» из вывода гейта; None — числа гейт не назвал.

        Шов по данным: из match'а дальше читаются только две величины — число и
        единица, сам он границу не пересекает. Поэтому обе формы записи (порядок
        слов в них РАЗНЫЙ, см. комментарий у `SCANNED_RE`) разбираются в одном
        месте, а не там, где решают, что с числом делать.
        """
        m = self.SCANNED_RE.search(out)
        if not m:
            return None
        return (int(m.group(1) or m.group(3)),
                (m.group(2) or m.group(4) or "").lower())

    def _source_files(self) -> list[str]:
        """Исходники проекта вне контура — знаменатель для суждения о слепоте.

        Шов по данным: наружу блок отдавал РОВНО одно имя — `files`. Код
        возврата git границу не пересекает и схлопывается в пустой список,
        который вызывающий читает как «слепоту судить не на чем».
        """
        code, listing = run(["git", "ls-files"], self.root)
        if code != 0:
            return []
        return [l for l in listing.splitlines()
                if l.endswith(self.SOURCE_EXT) and not self.CONTOUR_RE.match(l)]

    def _off_commit_label(self, text: str, name: str) -> str | None:
        """Гейт вписан, но мимо коммит-стадии → строка для списка, иначе None.

        Шов по данным: наружу блок отдавал одно значение — строку для
        `off_commit`; сами `stages` ниже не читает никто.

        Вписан, но не на коммите: `stages: [manual]` / `[push]`. Гонять его
        здесь нельзя — §8.6 не покрывает эти стадии, а `deps-audit` оттуда
        ходит в сеть. Пропускаем МОЛЧА по одному, а списком — вслух в
        `check_gates_see_code`: молчаливый пропуск всех гейтов разом дал бы
        «лжи нет» на проекте, где на коммите не проверяется ничего.
        """
        stages = self._hook_stages(text, name)
        if not stages or any(s.startswith("commit") or s == "pre-commit"
                             for s in stages):
            return None
        return f"{name} ({', '.join(stages)})"

    def _missing_roots(self, env: dict) -> list[str]:
        """Корни гейта, которых в дереве НЕТ, — «смотреть физически некуда».

        Шов по сложности, а не по длине: две вложенные выборки поднимали ветку
        нуля выше порога §2.1, а наружу отдают одно имя — список пропавших.
        """
        roots = [v for k, v in env.items()
                 if k.endswith("_SRC") or k.endswith("_DIR")]
        return [r for r in roots if r and not (self.root / r).is_dir()]

