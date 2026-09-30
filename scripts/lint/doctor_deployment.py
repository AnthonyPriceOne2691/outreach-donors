#!/usr/bin/env python3
"""Состав контура: что канон объявил, против того, что в проекте лежит.

Часть доктора (вход — `contour_doctor.py`). Отдельным модулем, а не строкой в
соседнем, потому что вопрос ДРУГОЙ, чем у всех остальных проверок. Те спрашивают
про развёрнутое: подключён ли гейт, умеет ли краснеть, видит ли код, не
разошлось ли тело. Ни одна не может спросить про НЕразвёрнутое — обход идёт по
дереву проекта, и файл, который не скопировали, в цикл не попадает по построению.

Замер, из которого модуль родился (2026-08-18): полигон с четырьмя снимками
канонов и ДВУМЯ скриптами payload'а из пятидесяти доктор объявил здоровым —
`EXIT 0`, «Лжи нет», ни строки про недостающее.

⚠ Инвентарь берётся у извлекателя, который едет вместе с канонами
(`docs/canon/extract_payload.py --manifest --by-canon`), а не собирается здесь
своим разбором: второй парсер payload'а — это `one-notion-one-place`, и цена
его известна замером («чужой извлекатель нашёл 73 файла из 74»). Рецепт этой
сверки канон уже носил — командой в три строки в шапке извлекателя. Тем она и
была: командой, которую никто не запускает.
"""

from __future__ import annotations

import hashlib
import json
import re

from doctor_core import AUTO, DEAD, SKIP, WEAK, run


class DeploymentScreen:
    CANON_DIR = "docs/canon"
    NA_FILE = "scripts/lint/not-applicable.json"

    def _payload_by_canon(self) -> tuple[dict[str, str], str]:
        """`{путь: канон}` от извлекателя проекта либо причина, почему не вышло."""
        ex = self.root / self.CANON_DIR / "extract_payload.py"
        if not ex.is_file():
            return {}, (f"нет {self.CANON_DIR}/extract_payload.py — состав "
                        "сверить нечем; извлекатель едет вместе с канонами (§5.0)")
        code, out = run(["python3", str(ex), "--canon-dir",
                         str(self.root / self.CANON_DIR),
                         "--manifest", "--by-canon"], self.root)
        if code != 0:
            # Старый извлекатель падает на разборе аргумента. `SKIP` с причиной,
            # а не сверка без разбивки по канонам: снимок несёт ЧЕТЫРЕ канона и
            # там, где развёрнуто три, поэтому неотфильтрованный список обвинил
            # бы законную раскладку варианта A/B — а ложное срабатывание
            # снимают вместе с проверкой (§4.3b).
            return {}, ("извлекатель не знает `--manifest --by-canon` (payload "
                        "старой ревизии) — обнови docs/canon/ по §5.5")
        table = {}
        for line in out.splitlines():
            canon, _, rel = line.partition("\t")
            if rel and canon:
                table[rel] = canon
        return table, "" if table else "извлекатель вернул пустой состав"

    def _canon_refused(self, prefix: str) -> bool:
        """Канон объявлен НЕразвёрнутым (`<канон>@absent`) — его payload не ждём.

        Записей может не быть вовсе: это не отказ, а молчание, и тогда payload
        всё равно ждём. Иначе проект без единой записи `stack:` выключал бы
        сверку состава целиком — ровно тот отказ, который доктор чинил в
        `cqg@2.22` на соседнем вопросе.
        """
        found, _ = self._records(prefix)
        return bool(found) and all(v.endswith("@absent") for _, v in found)

    def _declared_absent_files(self) -> set[str]:
        """Чего в проекте нет НАРОЧНО — `not-applicable.json` с причиной.

        Файл уже несёт ровно это понятие («роль не покрыта: на этом стеке у неё
        нет предмета»), и четвёртого объявления рядом с `adapted.json`,
        `canaries.json` и ним заводить нельзя. Ключ — путь payload'а либо имя
        файла; причина обязательна, иначе объявление не считается: пустая
        строка тут была бы самым дешёвым способом купить тишину.
        """
        f = self.root / self.NA_FILE
        if not f.is_file():
            return set()
        try:
            raw = json.loads(f.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            return set()          # битый файл ругается в своём месте, не тут
        out = set()
        for key, spec in raw.items():
            reason = spec.get("reason") if isinstance(spec, dict) else spec
            if str(reason or "").strip():
                out.add(str(key).split(":")[0])
        return out

    #: Роли, которые контур УМЕЕТ закрыть, и признак, при котором роль применима.
    #: Форма: (что объявлять и искать · альтернативные имена · признак · роль).
    #:
    #: **Принцип отбора назван, потому что список без принципа — класс из
    #: собственного реестра.** Сюда попадает файл, чьё ОТСУТСТВИЕ означает, что
    #: роль не закрыта ВОВСЕ, и чьё наличие проверяется по ИМЕНИ, а не по пути из
    #: канона: раскладки у проектов разные, и сверка по префиксу уже однажды
    #: увезла наверх дефект, невидимый на раскладке нашедшего (`delivery@1.76`).
    #:
    #: Сознательно НЕ здесь, и это решение, а не пропуск: `backend/pyproject.toml`
    #: и `requirements-dev.txt` — проект законно живёт на uv/poetry, а «применён
    #: ли фрагмент канона» это вопрос сверки ТЕЛ, и у неё свой владелец;
    #: `.prettierrc` — формат, а не роль каталога §3; `delivery/active/**` — пуст
    #: между поставками штатно (§2.3a).
    ROLES = (
        (".pre-commit-config.yaml", None, "git", "коммит-гейты"),
        ("eslint.config.js", r"eslint\.config\.[cm]?js|\.eslintrc(\..+)?",
         "frontend", "ESLint-половина сложности и ратчет предупреждений"),
        (".dependency-cruiser.cjs", None, "frontend", "направление зависимостей на TS"),
        (".importlinter", None, "python", "направление зависимостей на python"),
        ("quality.yml", None, "github", "CI-роль"),
        (".gitlab-ci.yml", None, "gitlab", "CI-роль"),
        ("CONSTITUTION.md", None, "delivery", "конституция поставки"),
        ("STACK-ACCEPTANCE.md", None, "delivery", "карта ролей"),
    )

    def _stack_signs(self) -> tuple[dict[str, bool], set[str]]:
        """Признаки стека и имена отслеживаемых файлов — один обход на все роли.

        ⚠ Файлы САМОГО контура из признака исключаются, и это не тонкость:
        `docs/canon/extract_payload.py` и `stack_selftest.py` едут в каждый
        проект, поэтому «есть python» было бы истиной даже на чистом Astro/TS.
        Замер на `portfolio-site`: единственные `.py` в дереве — контурные, и
        без исключения проект потребовал бы `.importlinter`, которого ему не
        нужно. Это ровно ложное срабатывание, по которому проверку снимают.

        Хостинг спрашивается у `remote.origin.url`, а файл — фолбэк: обратный
        порядок уже ронял GitHub-проект с зеркальным `.gitlab-ci.yml`
        (`cqg@2.01`). Нет ни remote, ни конфигов — оба признака ложны, и CI-роль
        отсюда не требуется вовсе: «CI нет» судит §10.4, а не эта проверка.
        """
        code, listing = run(["git", "ls-files"], self.root)
        files = listing.splitlines() if code == 0 else []
        own = re.compile(r"^(scripts/|docs/canon/|tests/)")
        names = {f.rsplit("/", 1)[-1] for f in files}
        gh, gl = self._hosting(files)
        return {
            "git": bool(files),
            "python": any(f.endswith(".py") and not own.match(f) for f in files),
            "frontend": any(n == "package.json" for n in names)
                        and not all("node_modules" in f for f in files
                                    if f.endswith("package.json")),
            "delivery": bool(self._declared_version("delivery")),
            "github": gh,
            "gitlab": gl,
        }, names

    def _hosting(self, files: list[str]) -> tuple[bool, bool]:
        """(GitHub, GitLab) — remote спрашивается РАНЬШЕ файла.

        Шов по вопросу: соседний блок отвечает «какой у проекта стек», этот —
        «где он живёт», и вместе они стоили суждению двенадцати ветвлений при
        планке §2.1 в десять. Обратный порядок признаков уже ронял GitHub-проект
        с зеркальным `.gitlab-ci.yml` (`cqg@2.01`), поэтому файл только фолбэк.
        """
        code, url = run(["git", "config", "--get", "remote.origin.url"], self.root)
        url = url.strip().lower() if code == 0 else ""
        if "github" in url or "gitlab" in url:
            return "github" in url, "gitlab" in url
        return (any(f.startswith(".github/workflows/") for f in files),
                ".gitlab-ci.yml" in files)

    def check_snapshot_integrity(self) -> None:
        """Провенанс снимка ЧИТАЕТСЯ, а не только пишется (§5 шаг 11).

        Находка обновления-3 (lab-15, F23), и она про молчание, а не про сбой.
        `SHA256SUMS` и `VENDORED.json` пересчитывались при КАЖДОМ
        перевендоривании — и не читались никем: замер прямым поиском по живому
        проекту дал ноль вызовов `sha256sum`/`shasum`/`hashlib.sha256` и ноль
        чтений обоих файлов. Провенанс писался в пустоту.

        Цена молчания замерена там же. Едущий `stack_selftest.py`, развёрнутый
        КОПИЕЙ вне снимка, отстал на 23 дня и ТРИ обновления; `selftest_sizes.py`
        не был развёрнут вовсе, хотя его sha256 в провенансе записан. Селфтест
        канона всё это время печатал `0 failures`, молча пропуская 36 блоков.

        ⚠ Едущие файлы НЕ в манифесте извлекателя — у них своего заголовка в
        канонах нет, они и есть снимок. Поэтому население тут второе и берётся у
        второго владельца — записанного провенанса. Взять `scripts/**` было бы
        четвёртым промахом того же префикса подряд.
        """
        recorded, why = self._provenance()
        if why:
            self.add(SKIP, "провенанс снимка", why)
            return
        adapted = self._declared_adaptations()
        torn, gone, stale = [], [], []
        for name, want in sorted(recorded.items()):
            snap = self.root / self.CANON_DIR / name
            if not snap.is_file():
                gone.append(name)
                continue
            body = snap.read_bytes()
            if hashlib.sha256(body).hexdigest() != want:
                torn.append(name)
                continue
            stale += self._diverged_copies(name, body, adapted)
        self._provenance_verdict(len(recorded), torn, gone, stale)

    def _provenance(self) -> tuple[dict[str, str], str]:
        """`{имя: sha256}` из `VENDORED.json` либо причина, почему не вышло."""
        f = self.root / self.CANON_DIR / "VENDORED.json"
        if not f.is_file():
            return {}, (f"нет {self.CANON_DIR}/VENDORED.json — провенанс снимка "
                        "не записан, сверять нечем (§5 шаг 11)")
        try:
            raw = json.loads(f.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError) as exc:
            return {}, f"{self.CANON_DIR}/VENDORED.json не разобран ({exc})"
        table = raw.get("sha256") or {}
        return table, "" if table else "VENDORED.json без раздела sha256"

    def _diverged_copies(self, name: str, body: bytes, adapted: dict) -> list[str]:
        """Копии едущего файла ВНЕ снимка, разошедшиеся с ним.

        Исполняют именно копию: `run_stack_selftest.sh` зовёт `stack_selftest.py`
        из корня, а не из `docs/canon/`. Сверка тел её не видит — та ходит по
        манифесту, а едущих файлов там нет; сверка состава тоже — она судит
        наличие, а не тело. Между двумя проверками была щель ровно в один файл,
        и он в неё провалился на 23 дня.
        """
        code, listing = run(["git", "ls-files"], self.root)
        out = []
        for rel in (listing.splitlines() if code == 0 else []):
            if rel.startswith(f"{self.CANON_DIR}/") or rel.rsplit("/", 1)[-1] != name:
                continue
            if rel in adapted or name in adapted:
                continue
            f = self.root / rel
            if f.is_file() and f.read_bytes() != body:
                out.append(rel)
        return out

    def _provenance_verdict(self, total: int, torn: list[str], gone: list[str],
                            stale: list[str]) -> None:
        """Три беды снимка, и все со ЗНАМЕНАТЕЛЕМ."""
        point = "провенанс снимка"
        if torn:
            self.add(DEAD, point, f"тело разошлось с записанным sha256: "
                                  f"{', '.join(torn)} — снимок переписан после "
                                  "вендоринга (линтер проекта? §6), провенанс лжёт")
        elif gone:
            self.add(DEAD, point, f"в провенансе {total}, на диске нет: "
                                  f"{', '.join(gone)} — снимок неполон")
        elif stale:
            self.add(DEAD, point, f"развёрнутая копия разошлась со снимком: "
                                  f"{', '.join(stale)} — исполняют её, а не "
                                  f"снимок; доложи из {self.CANON_DIR}/ либо "
                                  f"объяви в {self.ADAPTED} с причиной")
        else:
            self.add(AUTO, point, f"{total} файл(ов) сверены с записанным sha256")

    def check_white_spots(self) -> None:
        """Роль применима к этому стеку — и не закрыта, и не объявлена (§5.0).

        Второй вопрос скрининга. Первый («что канон объявил составом») судит
        ФАЙЛЫ payload'а безусловно; этот судит РОЛИ, и безусловным он быть не
        может: `.gitlab-ci.yml` не нужен на GitHub, конфиг фронта — без фронта.
        Поэтому у каждой роли есть признак, и молчание при ложном признаке —
        не пропуск, а ответ.

        Замер на флоте перед выбором строгости (2026-08-18): из четырёх
        развёрнутых проектов трое чисты, а `local-web-agent` при живом
        `frontend/package.json` не имеет ни `.dependency-cruiser.cjs`, ни
        объявления — роль «направление зависимостей на TS» не закрыта и не
        названа. Одна находка на четыре проекта: проверка не шумит и не пуста.

        ⚠ **Вердикт `WEAK`, а не `DEAD`, и граница проведена по природе ответа.**
        Состав судит ФАКТ: канон перечислил файлы, их либо доложили, либо нет, и
        молчаливая недостача при заявленной версии — ложь. Здесь же
        применимость ВЫВЕДЕНА из признаков, а вывод роняющим прогон быть не
        должен: тот же довод, по которому `WEAK` стоит у сверки записей о версии
        («быть посреди обновления законно»). Первая редакция ставила `DEAD` — и
        покраснела на четырёх чужих фикстурах, включая ту, что нарочно проверяет
        ОТСУТСТВИЕ `CONSTITUTION.md`. Это §4.3b в чистом виде: проверка, которая
        краснеет на законном состоянии, снимается вместе с пользой.
        """
        signs, names = self._stack_signs()
        absent_ok = self._declared_absent_files()
        gaps = []
        for name, alt, sign, label in self.ROLES:
            if not signs.get(sign) or name in absent_ok:
                continue
            if name in names or (alt and any(re.fullmatch(alt, n) for n in names)):
                continue
            gaps.append(f"{label} ({name})")
        if gaps:
            self.add(WEAK, "белые пятна",
                     "стек есть, роль не закрыта и не объявлена: " + " · ".join(gaps)
                     + f" — закрой по §5 либо объяви в {self.NA_FILE} с причиной")

    def check_deployment_completeness(self) -> None:
        """Скрининг состава: развёрнуто N из M, и чего нет молча.

        ⚠ Судятся только `scripts/**`, и это названный предел, а не забывчивость.
        Конфиги и шаблоны отсутствуют ЗАКОННО на каждом втором проекте:
        `.gitlab-ci.yml` не нужен на GitHub, `.dependency-cruiser.cjs` — без
        фронта, а `delivery/active/*` пуст между поставками штатно (§2.3a).
        Требовать их значило бы краснеть на здоровом проекте; их судят признаки
        стека — отдельный вопрос и отдельная проверка. Скрипты же копируются
        целиком: стек решает, ВПИСАН ли гейт (это `gate-coverage`), а не есть ли
        файл.

        Вердикт зависит от ЗАЯВЛЕНИЯ, и в этом весь смысл: проект, объявивший
        версию канона, объявил его развёрнутым — молчаливая недостача у него
        `DEAD`. Проект без заявления разворачивается прямо сейчас, у него это
        `WEAK` со списком: не «ты соврал», а «осталось вот это».
        """
        payload, why = self._payload_by_canon()
        if why:
            self.add(SKIP, "состав контура", why)
            return
        expected = [rel for rel, canon in sorted(payload.items())
                    if rel.startswith("scripts/") and "<" not in rel
                    and not self._canon_refused(canon)]
        absent_ok = self._declared_absent_files()
        missing = [r for r in expected if not (self.root / r).is_file()]
        undeclared = [r for r in missing
                      if r not in absent_ok and r.rsplit("/", 1)[-1] not in absent_ok]
        self._completeness_verdict(len(expected), missing, undeclared)

    def _completeness_verdict(self, total: int, missing: list[str],
                              undeclared: list[str]) -> None:
        """Три состояния состава одной строкой — и всегда СО ЗНАМЕНАТЕЛЕМ.

        Шов по вопросу, а не по длине: выше считают, здесь судят. Число без
        знаменателя («не хватает сорока») не отвечает ни на что — `40 из 51` и
        `40 из 41` это разные новости, и `number-without-its-denominator` лежит
        в реестре классов ровно за это.
        """
        point = "состав контура"
        here = total - len(missing)
        if not missing:
            self.add(AUTO, point, f"развёрнуто {here} из {total}")
        elif not undeclared:
            self.add(AUTO, point, f"развёрнуто {here} из {total}, "
                                  f"{len(missing)} объявлены неприменимыми")
        else:
            claimed = any(self._declared_version(p)
                          for p in ("cqg", "delivery", "okf"))
            names = ", ".join(r.rsplit("/", 1)[-1] for r in undeclared[:6])
            tail = f" … и ещё {len(undeclared) - 6}" if len(undeclared) > 6 else ""
            self.add(DEAD if claimed else WEAK, point,
                     f"развёрнуто {here} из {total}, НЕ ОБЪЯВЛЕНО "
                     f"{len(undeclared)}: {names}{tail} — либо доложи из "
                     f"payload'а (§5.0), либо объяви в {self.NA_FILE} с причиной")
