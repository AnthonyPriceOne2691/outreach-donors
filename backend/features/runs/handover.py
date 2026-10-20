"""После сбора — очередь человеку, меню главных и бизнесы ниши.

Отдельно от исполнения прогона (`pipeline.py`) с 20.10.2026: модуль исполнения упёрся
в потолок длины, когда прогон стал судить свежие домены сам (`runs/verdicts.py`).
Шов — по смыслу: здесь всё, что прогон отдаёт дальше, когда платное уже сделано.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from backend.features.core.models.run import RunModel
from backend.features.runs.failures import described
from backend.features.runs.planning import SerpText
from backend.features.runs.report import RunReport

if TYPE_CHECKING:
    from backend.features.runs.pipeline import RunDeps

logger = logging.getLogger(__name__)


async def hand_over(
    deps: RunDeps,
    run: RunModel,
    judged: Sequence[str],
    report: RunReport,
    pages: Mapping[str, SerpText],
) -> None:
    """Очередь человеку, меню главных, бизнесы ниши.

    В очередь — годные по вердикту этого прогона из тех, кого он судил: купленных
    сейчас и свежих (`judged`). Свежие — тоже: прогон их нашёл, и человек должен их
    увидеть, — просто платить за них не пришлось.
    """
    if deps.review is not None:
        report.review = await deps.review.queue_run(run.id, judged)
        await deps.runs.session_commit()
        await _check_doors(deps, run, report, pages)
    await _collect_niche(deps, run, report)


async def _check_doors(
    deps: RunDeps, run: RunModel, report: RunReport, pages: Mapping[str, SerpText]
) -> None:
    """Двери очереди — страница выдачи и меню главных: кто сам продаёт
    размещение — наверх.

    Сбой здесь прогон не роняет: платное уже сделано и лежит в очереди,
    а без двери очередь просто не поднимет продающих. Причина — в запись
    прогона, а не только в лог.
    """
    if deps.doors is None or deps.review is None:
        return
    try:
        report.doors = await deps.doors(
            deps.donors,
            await deps.review.pending_hosts(run.id),
            pages=pages,
            checkpoint=deps.runs.session_commit,
        )
    except Exception as exc:
        logger.exception("Прогон %s: меню главных не посмотрели", run.id)
        report.doors_failure = described(exc)[:300]


async def _collect_niche(deps: RunDeps, run: RunModel, report: RunReport) -> None:
    """Вердикт «продаёт своё» уже оплачен выдачей: такой сайт не донор, а
    кандидат в рекламодатели — решит человек.

    Сбой здесь прогон не роняет — как у меню главных: доноры оплачены и
    сохранены, а сбор повторяется из прогона кнопкой. Причина — в запись.
    """
    if deps.niche is None:
        return
    try:
        report.niche = await deps.niche(run.id)
        await deps.runs.session_commit()
    except Exception as exc:
        logger.exception("Прогон %s: бизнесы ниши не собрали", run.id)
        report.niche_failure = described(exc)[:300]
