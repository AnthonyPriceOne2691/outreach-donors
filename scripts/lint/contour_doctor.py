#!/usr/bin/env python3
"""Диагностика РАЗВЁРНУТОГО контура: что здесь работает, что бедно, что мертво.

Зачем отдельный инструмент, когда есть мета-гейт. `check_gate_coverage.sh`
отвечает «вписан ли гейт в конфиг» — это текст. Между «вписан» и «судит» лежит
целый класс отказов, и он не гипотетический: за одну сессию 2026-08-03 нашлось
пять случаев, все замерены. Мутационный гейт не судил на дефолтной раскладке
(ключи мутантов не совпадали с путями импорта). Сканер незаполненных шаблонов был
мёртв под `LC_ALL=C` — GNU grep ругался на кириллический диапазон и не находил
ничего. `new-dependency` был CI-only на проекте без раннера, то есть не исполнялся
ни разу за поставку. `deps-audit` на отсутствующем инструменте выходит нулём, и
сводка pre-commit печатает `Passed`. Сам CI был красным восемь пушей подряд, пока
локально было зелено.

Ни один из этих отказов не виден ни мета-гейту (он смотрит конфиг), ни сьюту
(он про канон, а не про этот проект), ни `check_gate_value.sh` (ему нужна история
срабатываний из живого CI).

**Принцип: пробовать ИСПОЛНЕНИЕМ, а не чтением.** Каждому гейту подсовывается его
собственная канарейка — файл с заведомым нарушением ровно того класса, который
гейт стережёт, — и проверяется, что гейт краснеет. Гейт, промолчавший на своей
канарейке, объявляется **DEAD**: это не бедность, это ложь, и только она роняет
доктора. Отсутствие инструмента (`ABSENT`) и мягкий пропуск с названной причиной
(`WEAK`) — честная непокрытость, exit 0.

Это ИНСТРУМЕНТ, не гейт: слот бюджета §9.1a он не занимает (как
`check_gate_value.sh` и `assert_digest.sh`) и в pre-commit не вписывается.

Запуск из корня проекта с развёрнутым контуром:

    python3 scripts/lint/contour_doctor.py            # таблица + вердикт
    python3 scripts/lint/contour_doctor.py --json     # машинно-читаемо

Разрезан на модули (`cqg@1.82`): 1082 строки при планке 300 (Delivery §9.1a п.5),
и рос он быстрее проверяемого — это назвал полевой отчёт раньше, чем ратчет веса.
Здесь вход и сборка; части лежат рядом и грузятся из каталога скрипта:

    doctor_core.py       вердикты, палитра, запуск подпроцесса (не знает соседей)
    doctor_layout.py     принуждение, инструменты, снимки, тела конфигов, хуки
    doctor_versions.py   что ЗАЯВЛЕНО: шапки канонов и записи `stack:` (2.02)
    doctor_hooks.py      чтение `.pre-commit-config.yaml`
    doctor_areas.py      видит ли гейт код проекта (1.69): прогон и чтение числа
    doctor_area_verdicts.py  вердикт о площади: просмотрено N из M (1.80/1.88)
    doctor_canaries.py   данные проб и объявления проекта
    doctor_probes.py     сами пробы исполнением
    doctor_deployment.py состав контура: что канон объявил против того, что лежит

Развёртывание копирует ВСЕ десять файлов: без любого из них доктор не стартует —
и это лучше, чем стартовать без части проверок (§5.0 инвентарь). Число здесь
сверяется механикой (`test_ts_stack_coverage`): до `cqg@2.02` стояло «шесть» при
восьми — счётчик разъехался на `cqg@1.98` и пережил две правки, потому что
единственным его читателем был глаз.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ⚠ Байт-код НЕ пишем, и это не микрооптимизация. Доктор работает В ДЕРЕВЕ
# проекта, а с `cqg@1.82` он импортирует соседние модули — Python положил бы
# `scripts/lint/__pycache__/` в чужой рабочий каталог, и `git status` после
# диагностики показал бы мусор, которого не просили. Проба обязана уходить не
# оставив следов — то же правило, что у канареек (`cqg@1.67`). Флаг выставляется
# ДО импорта частей: после них кэш уже записан. Поймано собственным тестом
# «проба оставила дерево как было», а не полем.
sys.dont_write_bytecode = True

from doctor_area_verdicts import AreaVerdicts
from doctor_areas import AreaChecks
from doctor_canaries import CanaryData
from doctor_core import ABSENT, AUTO, COLOR, DEAD, ORDER, RESET, SKIP, TOOL, WEAK, run
from doctor_deployment import DeploymentScreen
from doctor_hooks import HookReaders
from doctor_layout import LayoutChecks
from doctor_versions import VersionChecks
from doctor_probes import ProbeChecks


class Doctor(VersionChecks, LayoutChecks, HookReaders, AreaChecks, AreaVerdicts,
             CanaryData, ProbeChecks, DeploymentScreen):
    def __init__(self, root: Path) -> None:
        self.root = root
        self.rows: list[tuple[str, str, str]] = []
        #: Гейты, промолчавшие на своей канарейке. Заполняется пробами и читается
        #: суждением о площади: маска гейта, который НЕ судит, покрытием не
        #: считается. Данными, а не разбором собственных напечатанных строк —
        #: второй парсер своего же вывода это `one-notion-one-place`.
        self.dead_gates: set[str] = set()
        self.own = self._read_own_canaries()

    def add(self, verdict: str, point: str, detail: str) -> None:
        self.rows.append((verdict, point, detail))


    # --- вывод ---------------------------------------------------------------
    def _counts(self) -> dict[str, int]:
        """Сколько вердиктов каждого класса — один счёт на оба вида вывода.

        Шов по повтору: одна и та же выборка стояла в машинной ветке и в
        людской, и каждая копия стоила двух ветвлений на пустом месте.
        """
        return {k: sum(1 for r in self.rows if r[0] == k) for k in ORDER}

    def _report_json(self, dead: list) -> int:
        """Машинный вывод: те же строки и тот же код возврата, что у людского.

        Шов по данным: ветка отдаёт наружу одно значение — код возврата; печать
        и счёт живут внутри неё, а `dead` считает вызывающий, потому что читает
        его и сам.
        """
        print(json.dumps({
            "verdicts": [{"verdict": v, "point": p, "detail": d}
                         for v, p, d in self.rows],
            "counts": self._counts(),
            "dead": len(dead),
        }, ensure_ascii=False, indent=2))
        return 1 if dead else 0

    def report(self, as_json: bool) -> int:
        dead = [r for r in self.rows if r[0] == DEAD]
        if as_json:
            return self._report_json(dead)

        for verdict, point, detail in sorted(self.rows, key=lambda r: (ORDER[r[0]], r[1])):
            print(f"{COLOR[verdict]}{verdict:6}{RESET} {point:44} {detail}")

        counts = self._counts()
        print(f"\ncontour-doctor: AUTO {counts[AUTO]} · WEAK {counts[WEAK]} · "
              f"ABSENT {counts[ABSENT]} · TOOL {counts[TOOL]} · "
              f"SKIP {counts[SKIP]} · DEAD {counts[DEAD]}")
        # Примечание про SKIP печатается ТОЛЬКО когда они есть. Иначе доктор
        # объявлял бы слепоту, которой у него нет, — а преувеличенная скромность
        # так же уводит от правды, как преувеличенная уверенность.
        if counts[SKIP]:
            print("SKIP — область, которую доктор не пробовал: это его собственная "
                  "непокрытость,\nи она названа, а не спрятана.")
        if dead:
            print(f"\n\033[31mERROR{RESET}: {len(dead)} проверк(и) объявлены и МОЛЧАТ "
                  "на своём же нарушении.")
            print("Это не бедность (ABSENT/WEAK честны и не роняют доктора), а ложь: "
                  "контур\nсообщает о защите, которой нет.")
            return 1
        print("\nЛжи нет: всё объявленное либо судит, либо честно названо непокрытым.")
        return 0




def main() -> int:
    ap = argparse.ArgumentParser(description="диагностика развёрнутого контура")
    ap.add_argument("--json", action="store_true", help="машинно-читаемый вывод")
    ap.add_argument("--root", default=None, help="корень проекта (по умолчанию — git-root)")
    args = ap.parse_args()

    root = Path(args.root) if args.root else None
    if root is None:
        code, out = run(["git", "rev-parse", "--show-toplevel"], Path.cwd())
        root = Path(out.strip()) if code == 0 and out.strip() else Path.cwd()

    doc = Doctor(root.resolve())
    doc.check_deployment_completeness()
    doc.check_snapshot_integrity()
    doc.check_white_spots()
    doc.check_canons()
    doc.check_stack_records()
    doc.check_model_surface_claim()
    doc.check_enforcement()
    doc.check_tools()
    doc.check_snapshots()
    doc.check_canaries()
    doc.check_gates_see_code()
    doc.check_divergence_from_canon()
    return doc.report(args.json)


if __name__ == "__main__":
    sys.exit(main())
