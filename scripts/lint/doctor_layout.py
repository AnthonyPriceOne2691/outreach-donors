#!/usr/bin/env python3
"""Раскладка контура: принуждение, инструменты, снимки, тела конфигов, хуки.

Часть доктора (`cqg@1.82`, вход — `contour_doctor.py`). Здесь вопросы «что
развёрнуто и как настроено»: они дёшевы, ничего не запускают и отвечаются до
любых проб. Сюда же чтение `.pre-commit-config.yaml` (`_hook_env`,
`_hook_files_re`) — это тоже раскладка, а не суждение о гейте.

Суждения о том, что проект ЗАЯВИЛ (версии в шапках и в записях `stack:`),
переехали в `doctor_versions.py` (`cqg@2.02`): файл дошёл до 406 строк при
планке 300, а границу между «что лежит» и «что заявлено» видно по данным —
новый модуль не читает ни хуков, ни инструментов.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from doctor_core import ABSENT, AUTO, DEAD, SKIP, WEAK, _block_after, run

# Инструменты, на которых стоят гейты. `timeout` отдельно: он GNU coreutils и на

TOOLS = {
    "ruff": "гейт сложности, основной линт",
    "mypy": "типы",
    # `pragma` — не украшение и не подавление находки. Имя инструмента содержит
    # слово из denylist'а его же KeywordDetector'а, а дальше по строке идут
    # двоеточие и значение в кавычках — та самая форма, которую он считает
    # секретом. Первое развёртывание намерило это находкой: канон поставлял
    # payload, который метит его же гейт. Форма подавления взята у самого
    # инструмента, чтобы след был читаемым, а не спрятанным в baseline проекта.
    # Регрессия — `tests/test_payload_clean_for_scanners.py`.
    "detect-secrets": "secrets-гейт (§2.7)",  # pragma: allowlist secret
    "pip-audit": "deps-audit, python-половина",
    "mutmut": "mutation — «тесты утверждают» (§3.7)",
    "jscpd": "DRY-гейт",
    "npm": "deps-audit, js-половина; eslint-ратчет",
    "timeout": "бюджет мутационного гейта (macOS: brew install coreutils)",
}

# Канарейка = «файл с нарушением ровно этого класса». Ключ — имя правила из
# `--list-rules`, чтобы список правил брался у СКРИПТА, а не дублировался здесь:


class LayoutChecks:
    # --- B. места принуждения ------------------------------------------------
    def check_enforcement(self) -> None:
        cfg = self.root / ".pre-commit-config.yaml"
        self.add(AUTO if cfg.is_file() else ABSENT, "конфиг pre-commit",
                 "есть" if cfg.is_file() else "нет — коммит-гейтов не существует")

        self._check_hooks_installed(cfg.is_file())

        # CI ищется у ОБОИХ хостингов (`cqg@2.00`). Первая редакция смотрела
        # только в `.github/workflows`, поэтому на GitLab-проекте с живым
        # пайплайном доктор печатал «CI workflow: нет — §10.4 закроет как weak».
        # Это ложное отрицание в документе, который для читателя И ЕСТЬ ответ про
        # покрытие: гейт мержа у контура чисто гитовый (`merge_guard.sh`), а
        # CI-контракт §10.4 описан шагами, а не файлом одного хостинга, — значит
        # привязка к пути была допущением, а не требованием. Тот же класс, что
        # питоновское допущение в шаблоне workflow (`cqg@1.85`).
        wf = sorted((self.root / ".github" / "workflows").glob("*.y*ml")) \
            if (self.root / ".github" / "workflows").is_dir() else []
        wf += [p for p in (self.root / ".gitlab-ci.yml",) if p.is_file()]
        self.add(AUTO if wf else ABSENT, "CI workflow",
                 ", ".join(p.name for p in wf) if wf else "нет — §10.4 закроет как weak")

        mg = self.root / "scripts" / "merge_guard.sh"
        self.add(AUTO if mg.is_file() else ABSENT, "гейт мержа merge_guard.sh",
                 "есть" if mg.is_file() else "нет — мерж не проверяет слитое состояние")

    def _git_hooks_dir(self) -> Path:
        """Каталог, куда git РЕАЛЬНО кладёт хуки этого дерева.

        Шов по данным: наружу блок отдавал одно имя — `gitdir`; код возврата
        git границу не пересекает.

        ⚠ Каталог хуков берётся у git, а не собирается из `.git/hooks`
        (`cqg@1.90`). В worktree `.git` — ФАЙЛ со ссылкой, хуки лежат в общем
        каталоге основного репозитория, и доктор объявлял их неустановленными
        ровно там, где они только что отработали на коммите. Ложное срабатывание
        — дефект проверки (§4.3b), причём этот стоил бы дороже обычного: два
        `DEAD` роняют прогон, то есть обновление контура в worktree выглядело бы
        провалом. Поймано первым же применением: канон разворачивали в worktree,
        чтобы не трогать рабочую ветку проекта.
        """
        code, common = run(["git", "rev-parse", "--git-common-dir"], self.root)
        gitdir = Path(common.strip()) if code == 0 and common.strip() else self.root / ".git"
        return gitdir if gitdir.is_absolute() else self.root / gitdir

    def _check_hooks_installed(self, has_cfg: bool) -> None:
        """Установлен ли хук ФАКТИЧЕСКИ: конфиг без `pre-commit install` — это
        список пожеланий: ни один хук не запустится, и об этом ничто не скажет.

        Шов по данным: цикл не отдаёт наружу ни одного имени, только вердикты, а
        из места принуждения ему нужен ровно один факт — есть ли конфиг.

        ⚠ **В CI этот вопрос НЕОТВЕЧАЕМ, и потому там `SKIP`, а не `DEAD`.**
        Хуки живут в клоне разработчика; чекаут их не ставит и ставить не должен —
        принуждение в CI даёт сам workflow, который гоняет `pre-commit run
        --all-files` напрямую. Без этой развилки доктор в CI красный по
        построению: конфиг в дереве есть, хуков нет, `DEAD 2`, exit 1 — то есть
        шаг был бы красным на КАЖДОМ проекте с развёрнутым контуром. Замерено на
        стенде: `DEAD 2` без хуков против `DEAD 0` с подставленными.

        Это ложное красное, а такие проверки снимают вместе со сверкой (§4.3b),
        поэтому цена развилки — не удобство. Но и `AUTO` тут нельзя: вопрос не
        «всё хорошо», а «здесь не спросить», и разница обязана быть в отчёте.
        Признак — переменная `CI`: её ставят оба хостинга (GitHub Actions и
        GitLab CI), поэтому второго признака контур не заводит.
        """
        in_ci = os.environ.get("CI", "").lower() not in ("", "0", "false")
        gitdir = self._git_hooks_dir()
        for hook in ("pre-commit", "pre-push"):
            h = gitdir / "hooks" / hook
            if h.is_file() and "pre-commit" in h.read_text("utf-8", errors="ignore"):
                self.add(AUTO, f"хук {hook} установлен",
                         str(h if not h.is_relative_to(self.root)
                             else h.relative_to(self.root)))
            elif in_ci:
                self.add(SKIP, f"хук {hook} установлен",
                         "прогон в CI: хуки живут в клоне разработчика, здесь "
                         "принуждение даёт workflow — вопрос неотвечаем, а не «ок»")
            else:
                self.add(DEAD if has_cfg else ABSENT, f"хук {hook} установлен",
                         "конфиг есть, а хук НЕ установлен: `pre-commit install"
                         f"{' --hook-type pre-push' if hook == 'pre-push' else ''}`"
                         if has_cfg else "нет")

    # --- C. инструменты ------------------------------------------------------
    def check_tools(self) -> None:
        # ⚠ `node_modules/.bin` в списке ОБЯЗАТЕЛЕН, и это не удобство. Локальная
        # devDependency npm-проекта живёт только там: без этого каталога доктор
        # печатал `ABSENT инструмент jscpd — нет → DRY-гейт не работает` и
        # четырьмя строками ниже `AUTO область check_jscpd_gate.sh — просмотрено
        # 2 файл(ов)`. Отчёт, содержащий взаимоисключающие строки о собственном
        # гейте, обесценивает подпись «лжи нет» в подвале — а она и есть продукт
        # доктора. Тот же класс, что чинили весь день: заявление разошлось с
        # деревом, и увидел это человек, а не механика.
        #
        # Проверка областей окружение хука уже воспроизводит (тянет `LINT_*` из
        # `entry:`), а проба инструментов — нет. Здесь минимум: каталоги, куда
        # инструмент кладут менеджеры пакетов обоих экосистем.
        fe = os.environ.get("LINT_FE_DIR", "frontend")
        venvs = [self.root / ".venv" / "bin", self.root / "backend" / ".venv" / "bin",
                 self.root / "node_modules" / ".bin", self.root / fe / "node_modules" / ".bin"]
        for tool, why in TOOLS.items():
            found = next((str(v / tool) for v in venvs
                          if (v / tool).is_file() or (v / tool).is_symlink()), None) \
                or shutil.which(tool) \
                or (shutil.which("gtimeout") if tool == "timeout" else None)
            self.add(AUTO if found else ABSENT, f"инструмент {tool}",
                     found if found else f"нет → {why} не работает")

    # --- D. снимки -----------------------------------------------------------
    def check_snapshots(self) -> None:
        d = self.root / "scripts" / "lint"
        if not d.is_dir():
            self.add(ABSENT, "снимки гейтов", "нет scripts/lint — контур не развёрнут")
            return
        found = sorted(p.name for p in d.glob("*baseline*.txt"))
        self.add(AUTO if found else WEAK, "снимки (baseline)",
                 f"{len(found)} шт: {', '.join(found)}" if found
                 else "ни одного: гейт без снимка красный на первом коммите, "
                      "и его снимают")


    # --- Что в контуре разошлось с каноном и почему (cqg@1.71) ----------------
    # Обновление контура было археологией: §5 описывает развёртывание на
    # greenfield, а что делать со СТАРЫМ проектом, часть скриптов которого
    # адаптирована под стек, не сказано нигде. Слепое копирование payload'а
    # адаптацию уничтожает — замерено на Swift-проекте: канонная python-версия
    # затёрла `check_grep_gate.sh` с тремя своими правилами (`force-unwrap`,
    # `hardcoded-network`), и спасло только чистое дерево и `git checkout`.
    #
    # Различать «адаптирован» и «просто устарел» приходилось глазами, и признак
    # неочевиден: из шести разошедшихся скриптов два оказались НЕ адаптированными
    # (ноль упоминаний своего стека в теле), один — настоящей адаптацией, а
    # ещё один нёс адаптацию ЛИШНЮЮ: маска Swift была зашита в тело, хотя
    # канонная версия давно принимает её через `LINT_LENGTH_GLOBS`.
    #
    # Отсюда `scripts/lint/adapted.json`: проект объявляет, что и почему изменено
    # против канона. Расхождение без объявления — не ложь (быть на версию позади
    # нормально), но и не молчание: печатается списком, чтобы обновление
    # начиналось с готового ответа, а не с раскопок.
    ADAPTED = "scripts/lint/adapted.json"

    #: Конфиги и workflow'ы, которые поставляет канон, — путь → (маркер, язык).
    #: Маркер у скрипта выводится из имени, у конфига — нет, поэтому таблица.
    #:
    #: ⚠ Их сверка появилась позже скриптов (`cqg@1.91`) и стоила затёртой
    #: адаптации: накат разложил канонный `quality.yml` поверх настроенного, `env`
    #: вернулся к дефолтам `backend/features`/`frontend` на проекте, где
    #: python-половины нет вовсе, а фронт в корне. Сверка смотрела только
    #: `scripts/lint/*`, поэтому не сказал никто — а §5.5 ради этого и написана.
    #: Конфиги адаптируют ШТАТНО (§6: значения живут в `env:`/`entry:`), значит
    #: расхождение тут нормально — но объявленное, а не молчаливое.
    #: ⚠ Это список ИСКЛЮЧЕНИЙ, а не инвентарь конфигов, и разница стоила
    #: молчания на шести файлах из десяти. Тело большинства конфигов лежит в
    #: каноне под заголовком вида ``### `путь` `` — маркер выводится ПРАВИЛОМ,
    #: ровно как у скриптов. Здесь перечислены только те, чьё тело живёт под
    #: ПРОЗАИЧЕСКИМ заголовком, из имени не выводимым.
    #:
    #: Прежняя редакция держала таблицу инвентарём и знала четыре файла из
    #: десяти: `.dependency-cruiser.cjs`, `backend/.importlinter`,
    #: `backend/pyproject.toml`, `backend/requirements-dev.txt`,
    #: `<frontend>/.prettierrc` и `<frontend>/eslint.config.js` не сверялись
    #: НИКОГДА — адаптированный без объявления конфиг проходил молча, тогда как
    #: `plan_update.py` на том же файле давал «стоп». Два инструмента одного
    #: контура расходились об одном файле, и слепым был тот, что ЕЗДИТ в проект
    #: и стоит в CI. Список рос дважды (`1.91`, `2.01`) и оба раза строкой, а не
    #: выводом из манифеста. Нашло третье полевое развёртывание.
    CANON_CONFIG_MARKERS = {
        ".github/workflows/quality.yml": "### 8.3. Workflow (GitHub Actions)",
        ".github/workflows/main-guard.yml":
            "**④ Красное на `main` не остаётся незамеченным.**",
        # Адаптер GitLab сверяется так же, как оба workflow'а (`cqg@2.01`). Без
        # этой строки §5.5 на GitLab-проекте не находила НИЧЕГО и молчала: и
        # «адаптер устарел», и «адаптирован без объявления» проходили тихо —
        # ровно та половина класса, которую `cqg@1.91` закрыл для GitHub.
        ".gitlab-ci.yml": "### 8.3a. GitLab: тот же контракт, тонкий адаптер",
    }

    def canon_configs(self, text: str) -> dict[str, str]:
        """{путь конфига: маркер} — ИНВЕНТАРЬ из самого канона, не из списка.

        Заголовки ``### `путь` `` вычитываются из снимка, поэтому новый конфиг
        попадает под сверку тем, что канон его вообще описывает, — руками
        дописывать нечего и забыть нечего. Скрипты отсеиваются: у них свой обход.

        Две формы заголовка канон использует и обе разобраны: суффикс-пояснение
        (``### `backend/pyproject.toml (фрагмент)` ``) и плейсхолдер каталога
        фронта (``### `<frontend>/eslint.config.js` ``), который разворачивается
        в настоящий каталог проекта.
        """
        fe = os.environ.get("LINT_FE_DIR", "frontend")
        out = dict(self.CANON_CONFIG_MARKERS)
        for m in re.finditer(r"^### `([^`]+)`\s*$", text, re.M):
            raw = m.group(1)
            rel = raw.split(" (", 1)[0].replace("<frontend>", fe)
            if rel.startswith("scripts/") or rel in out:
                continue
            out[rel] = f"### `{raw}`"
        return out

    def _compare_with_snapshot(self, text: str, rel: str, marker: str, lang: str,
                               declared: dict) -> None:
        """Сравнение по МАРКЕРУ — для конфигов, чьё тело лежит под заголовком."""
        live = self.root / rel
        if not live.is_file() or marker not in text:
            return                       # файла нет или канон его не поставляет
        body = _block_after(text, marker, lang)
        if body is not None:
            self._judge_divergence(rel, body, declared)

    def _judge_divergence(self, rel: str, body: str, declared: dict) -> None:
        """Вердикт по паре «тело канона · файл проекта». ОДИН на оба способа.

        Способа добыть канонное тело два — у конфига по маркеру, у скрипта от
        извлекателя, — а суждение обязано быть одно: два вердикта разошлись бы,
        и это `one-notion-one-place`.
        """
        live = self.root / rel
        if not live.is_file():
            return
        name = rel.rsplit("/", 1)[-1]
        spec = declared.get(rel) or declared.get(name)
        point = f"расхождение с каноном {name}"
        if body.strip() == live.read_text(encoding="utf-8", errors="replace").strip():
            if spec:
                self.add(WEAK, point, "объявлен адаптированным, а тело СОВПАДАЕТ "
                                      "с каноном — объявление устарело и мешает "
                                      "обновлению")
            return
        if isinstance(spec, dict) and str(spec.get("reason", "")).strip():
            self.add(WEAK, point, "адаптирован намеренно: " + str(spec["reason"])[:90])
            return
        self.add(WEAK, point,
                 "тело отличается от снимка канона, а объявления нет. "
                 "Либо устарел (обнови из payload'а), либо адаптирован "
                 f"под стек (объяви в {self.ADAPTED} с причиной) — "
                 "иначе обновление начнётся с раскопок и затрёт правку")

    def _declared_adaptations(self) -> dict:
        """Что проект ОБЪЯВИЛ изменённым против канона (`adapted.json`).

        Шов по данным: наружу блок отдаёт одно имя — `declared`. Нечитаемый файл
        схлопывается в пустой словарь, а жалоба на него остаётся рядом с
        чтением, потому что судить ею нечего.
        """
        f = self.root / self.ADAPTED
        if not f.is_file():
            return {}
        try:
            return json.loads(f.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError) as exc:
            self.add(SKIP, self.ADAPTED, f"не разобран ({exc}) — адаптации "
                                         "объявлены и не прочитаны")
            return {}

    def _judge_payload_bodies(self, canon_dir: Path, snap: Path,
                              declared: dict) -> None:
        """Разложить payload извлекателем и сверить тела скриптов.

        Отдельным блоком, потому что вопрос другой: снаружи — «что сверяем»,
        здесь — «как достаём». Пропуск ИМЕНУЕТСЯ обоими путями: молчаливое
        сужение населения и было дефектом, который эта проверка закрывает.
        """
        if self._bodies_from_extractor(canon_dir, declared):
            return
        seen = self._bodies_from_marker(snap, declared)
        self.add(SKIP, "сверка тел payload'а",
                 f"извлекателя нет или он старый — сверено {seen} файл(ов) "
                 "только из scripts/lint/**, скрипты delivery_/okf_ не сверены; "
                 "обнови docs/canon/ по §5.5")

    def _bodies_from_extractor(self, canon_dir: Path, declared: dict) -> bool:
        """Тела от владельца разбора. → сверил ли (False = зови фолбэк)."""
        ex = canon_dir / "extract_payload.py"
        if not ex.is_file():
            return False
        with tempfile.TemporaryDirectory(prefix="canon-payload-") as tmp:
            code, _ = run(["python3", str(ex), "--canon-dir", str(canon_dir),
                           "--extract", tmp], self.root)
            if code != 0:
                return False
            base = Path(tmp)
            for src in sorted(base.rglob("*")):
                rel = str(src.relative_to(base))
                if src.is_file() and rel.startswith("scripts/"):
                    self._judge_divergence(
                        rel, src.read_text(encoding="utf-8", errors="replace"),
                        declared)
        return True

    def _bodies_from_marker(self, snap: Path, declared: dict) -> int:
        """Прежний путь по маркеру. → сколько сверил.

        ⚠ Фолбэк, а не отказ, и это решение с ценой. Извлекателя может не быть
        (снимок старее `cqg@2.08`), и СНЯТЬ сверку целиком значило бы обменять
        шестнадцать невидимых скриптов на тридцать пять ослепших. Первая
        редакция правки так и сделала и уронила три собственных оракула: они
        разворачивают снимок БЕЗ извлекателя, то есть ровно старый случай.

        Маркер знает только форму CQG (``### `путь` ``), поэтому покрывает
        `scripts/lint/**` и молчит про `delivery_*`. Сужение возвращается ЧИСЛОМ
        и печатается: молчаливое сужение и было дефектом.
        """
        text = snap.read_text(encoding="utf-8", errors="replace")
        d = self.root / "scripts" / "lint"
        seen = 0
        for script in sorted(d.glob("*")) if d.is_dir() else []:
            if not script.is_file() or script.suffix not in (".sh", ".py"):
                continue
            seen += 1
            lang = "python" if script.suffix == ".py" else "bash"
            self._compare_with_snapshot(
                text, f"scripts/lint/{script.name}",
                f"### `scripts/lint/{script.name}`", lang, declared)
        return seen

    def check_divergence_from_canon(self) -> None:
        """Тела payload'а против снимка. И население, и ТЕЛА — от владельца.

        ⚠ Прежняя форма обходила `scripts/lint/*` глобом: путь-префикс стоял
        вместо понятия «payload» ТРЕТИЙ раз (первые два — ратчет веса мимо
        едущих файлов, остаток назван в `cqg@2.08`, и сверка состава рядом).
        Замер прямой пробой на живом проекте (обновление-3, F23): приписал
        строку в три файла разом — `scripts/lint/doctor_areas.py` пойман,
        `scripts/delivery_diff.py` и корневой `stack_selftest.py` прошли МОЛЧА.
        Вне обхода оставались **16 скриптов из 51** (`delivery_*`, `okf_*`,
        `merge_guard.sh`).

        Хуже дыры была соседняя запись: `canon_configs` отсеивал `scripts/`
        отсылкой «у них свой обход» — без адреса и без объёма, — и по ней
        принимали решение НЕ проверять. Обещание было шире населения.

        ⚠ **Первая правка этой дыры несла ТОТ ЖЕ дефект и умерла на пробе.**
        Она брала население из манифеста, но маркер строила по соглашению —
        ``### `путь` ``. У Delivery форма другая (``# Приложение B2 — `путь` ``),
        и сверка молча не нашла НИ ОДНОГО из шестнадцати. Соглашение о заголовке
        оказалось третьим способом узнать payload и разошлось, как расходятся
        все копии. Теперь тела даёт `--extract` извлекателя — единственного, кто
        знает все формы, и он же едет вместе с канонами. Цена замерена: полная
        раскладка 76 файлов — **0.14 с**.
        """
        canon_dir = self.root / "docs" / "canon"
        snap = canon_dir / "CODE_QUALITY_GATES.md"
        if not snap.is_file():
            return                       # снимка канона нет — сверять не с чем
        declared = self._declared_adaptations()
        self._judge_payload_bodies(canon_dir, snap, declared)
        text = snap.read_text(encoding="utf-8", errors="replace")

        # Язык блока для конфигов — `yaml` исторически; на деле сверяется ТЕЛО,
        # а не подсветка, поэтому одного значения хватает всем формам.
        for rel, marker in sorted(self.canon_configs(text).items()):
            self._compare_with_snapshot(text, rel, marker, "yaml", declared)

