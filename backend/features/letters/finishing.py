"""Письмо из ответа модели — один путь у сборки очереди и у переписывания шаблонных писем.

Сборка (`building.py`) готовит первое письмо адресату; переписывание (`rewrite_queue.py`) —
то же письмо заново, когда в первый раз модель отказала. Что модель знает об адресате, подбор
зон под коридор, запреты, отличие от шаблона и потолок расхода перед пачкой — здесь, одной
копией: две разошлись бы на первой правке, и переписанное письмо проходило бы не те проверки,
что собранное.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import outreach as outreach_cfg
from backend.features.core import usage
from backend.features.letters import compose, guards, selection
from backend.features.letters.rewrite import Personalization, RewriteResult
from backend.features.letters.uniqueness import difference

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Finished:
    """Письмо из ответа модели — каким его кладёт в очередь сборка."""

    letter: compose.Letter
    #: Доля изменённых слов против шаблона с теми же подстановками (`uniqueness.difference`).
    uniqueness: float
    #: Сколько переписанных зон подбор под коридор оставил текстом шаблона; 0 — не оставлял.
    dropped: int = 0


def finish(rendered: compose.Rendered, rewritten: RewriteResult, *, seed: int) -> Finished:
    """Письмо из ответа модели: подбор зон под коридор отличия, запреты, отличие от шаблона.

    Набор зон подбирается из того же ответа модели (`selection.py`): вызов на письмо один, как
    и был. Берётся наибольший набор в коридоре; среди равных решает зерно `seed` — номер домена
    адресата: выбор повторим (тот же ответ — тот же набор), а у разных адресатов наборы
    расходятся, и шаблонные абзацы писем не совпадают.

    Ни один набор не в коридоре — все принятые зоны, как до подбора: письмо встаёт в
    очередь с честным числом и пометкой «вне коридора» на экране, решает человек.
    Повтора модели, как у продаж, нет: токенов на письмо столько же, сколько до подбора.

    Снятая подбором зона — не отказ модели: ноты «осталась шаблонной» о ней нет, она
    в отчёте отдельным числом (`dropped`).
    """
    chosen = selection.select(rendered, rewritten.zones, seed=seed)
    letter = compose.assemble(rendered, rewritten.zones if chosen is None else chosen.zones)
    # Проверки — по собранному письму выбранного набора: метрики в теме и тексте.
    guards.assert_letter_clean(letter.subject, letter.body)
    return Finished(
        letter=letter,
        uniqueness=difference(letter.plain_body, letter.body),
        dropped=0 if chosen is None else len(chosen.dropped),
    )


async def cap_reached(session: AsyncSession, *, run_id: int | None, what: str) -> str | None:
    """Потолок расхода на модель — до каждой пачки: вызовы пачки уходят разом, и проверка между
    ними ничего бы не остановила. Достигнут — причина словами; `None` — звать модель можно.
    `what` — что остановлено, для журнала: сборка или переписывание очереди."""
    try:
        await usage.ensure_llm_within_cap(session, run_id=run_id)
    except usage.LlmCapExceededError as exc:
        logger.warning("письма: стоп на потолке расхода модели (%s) — %s", what, exc)
        return str(exc)
    return None


def personalization(
    host: str, *, country: str, niche: tuple[str, ...], about: str | None = None
) -> Personalization:
    """Что модель знает об адресате: сайт, страна письма, ниша и цитата его страницы."""
    return Personalization(
        host=host,
        country=country,
        niche=niche,
        about=about,
        # Наше имя отправителя модель не видит (имена — метками): его ставят в зону,
        # когда правят шаблон.
        names=(outreach_cfg.SENDER_NAME,),
    )
