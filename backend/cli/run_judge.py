"""Ветка судьи площадки в итоге консольного прогона (`outreach run`).

Отдельно от `main.py`: тот упёрся в потолок длины файла, а судья — отдельная
часть итога со своими словами для оператора.
"""

from __future__ import annotations

from backend.config import judge as judge_cfg
from backend.features.runs.report import RunReport

#: Кто решил — словами оператора, а не кодами модели.
DECIDERS = {"rule": "правилом", "model": "моделью по выдаче", "arbiter": "арбитром"}


def print_judge(report: RunReport) -> None:
    """Ветка судьи. Нет её вовсе при выключенном судье: ноль читался бы
    как «судил и никого не нашёл», а это другая новость."""
    judge = report.judge
    if judge is None:
        return
    shadow = judge_cfg.MODE is judge_cfg.JudgeMode.SHADOW
    mode = "наблюдение, не режет" if shadow else "режет"
    cut = "отрезал бы" if shadow else "отрезал"
    print(f"\nСудья площадки ({mode}):")
    tokens = f"{judge.tokens:,}".replace(",", " ")
    print(f"  судил {judge.judged}, из кэша {judge.from_cache}, токенов {tokens}")
    print(f"  {cut} {judge.would_cut}, к человеку {judge.to_review}")
    for who, count in sorted(judge.by_decider.items(), key=lambda kv: -kv[1]):
        print(f"    решено {DECIDERS.get(who, who):<18} {count}")
    if judge.from_index:
        print(f"  главная закрыта, судил по индексу поиска: {judge.from_index}")
    if judge.home_unreached:
        print(f"  главная не открылась и в индексе нет: {judge.home_unreached} — решала выдача")
    if judge.units_saved:
        saved = "сэкономил бы" if shadow else "сэкономил"
        units = f"{judge.units_saved:,}".replace(",", " ")
        print(f"  юнитов {saved}: {units} (нижняя граница)")
