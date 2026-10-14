"""Прогоны рассылки: из каких можно собрать письма и что они задают письму.

Сборка (`building.py`) спрашивает это до того, как заведена рассылка: отказ
по прогонам не должен оставлять пустую рассылку с их именем. Отдельно от
сборки — потому что тот же ответ нужен экрану до постановки задачи
(`api/letters/routes.py`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from backend.features.core.domain import Stage
from backend.features.core.models.run import RunModel
from backend.features.core.stages import mail_stage
from backend.features.letters.repository import LetterRepository


class LetterScopeError(ValueError):
    """Из этих прогонов рассылку не собрать. Сообщение говорит, что сделать."""

    #: Повтор задачи это не исправит (`runs/failures.py`).
    permanent = True


@dataclass(frozen=True, slots=True)
class RunScope:
    """Что прогоны рассылки задают письму: страну и нишу каждого донора."""

    country: str | None = None
    #: Домен → ключи, по которым он нашёлся. Это ниша для переписывания
    #: вступления — точнее, чем ключи всей рассылки через запятую.
    found_by: dict[str, list[str]] = field(default_factory=dict)


async def run_scope(
    repo: LetterRepository, run_ids: Sequence[int], *, stage: Stage = Stage.DONORS
) -> RunScope:
    """Проверить прогоны рассылки и достать из них страну и нишу.

    Отказ — до постановки сборки: рассылка по прогонам разных стран ушла
    бы одним языком, а по прогону с неоконченным поиском контактов — части
    принятых, и остальные молча выпали бы (так же сборку держит соседняя
    система, пока контакты собираются).

    У рекламодателей прогонов нет: их находит обход доноров, а не выдача.
    Прогоны, названные для Этапа 2, — ошибка формы, и молча их не взять:
    человек решил бы, что рассылка сужена до них.

    Продажам — отказ до всего остального: сборка с этапом продаж шла бы
    путём доноров и заводила бы рассылку продаж из принятых доноров.
    """
    if mail_stage(stage, "Очередь писем не собрана") is Stage.ADVERTISERS and run_ids:
        raise LetterScopeError(
            "Рекламодатели собираются не по прогонам: их находит обход доноров, "
            "а не выдача. Прогоны для рассылки рекламодателям не выбирать"
        )
    if not run_ids:
        return RunScope()
    runs = await repo.runs(run_ids)
    country = _one_country(runs, run_ids)
    pending = await repo.contacts_pending(run_ids)
    if pending:
        raise LetterScopeError(
            f"Контакты ещё не искали у {pending} принятых доноров этих прогонов. "
            "Дождаться поиска контактов — иначе они выпадут из рассылки"
        )
    return RunScope(country=country, found_by=_found_by(runs))


def _one_country(runs: Sequence[RunModel], run_ids: Sequence[int]) -> str:
    missing = sorted(set(run_ids) - {run.id for run in runs})
    if missing:
        raise LetterScopeError(f"Прогонов {', '.join(map(str, missing))} нет")
    countries = sorted({run.country.lower() for run in runs})
    if len(countries) > 1:
        raise LetterScopeError(
            f"Прогоны из разных стран ({', '.join(countries)}): язык письма берётся от страны. "
            "Собрать по рассылке на страну"
        )
    return countries[0]


def _found_by(runs: Sequence[RunModel]) -> dict[str, list[str]]:
    """Ключи, по которым нашёлся каждый домен, — из всех прогонов рассылки."""
    merged: dict[str, list[str]] = {}
    for run in runs:
        for host, keys in ((run.candidates or {}).get("found_by") or {}).items():
            known = merged.setdefault(host, [])
            known.extend(key for key in keys if key not in known)
    return merged
