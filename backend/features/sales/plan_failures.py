"""Отказы сметы и остановки прогона «Плана работ» — по видам: что с ними делает конвейер.

Смета прогона волны отказывает словами (`segment_estimate.Estimate.refusal`), а прогон встаёт словами
своей причины (`run.error`: `segment_run.execute`, `segment_jobs`). Конвейер (`plan_tick.py`) решает
по виду, а слова кладёт человеку как есть:

- **повтор** (`RETRY`) — временное: потолок модели (смета выше остатка дневного потолка, судья встал
  на потолке), у модели кончились кредиты посреди судьи, сбой, не прошедший и после повторов задачи
  (сеть, база). Гипотеза собирает дальше: проход продолжит тот же прогон или посчитает смету заново,
  но не чаще `SALES_PLAN_RETRY_MIN`;
- **у гипотезы** (`STOP`) — постоянное и только её: описание сегмента стёрто, рынок листа не в карте
  выдачи, язык писем без цепочки, ключей у пачки нет. Гипотеза — `stopped` словами, конвейер идёт к
  следующей;
- **у сервиса** (`PAUSE`) — остальное: счёт выдачи мал (`serp/funds`), модель не ответила проверке
  (`segment_judging.unready`: нет кредитов, ключ не принят), настройка сервиса (источник выдачи, ключ
  модели, проверка адресов, потолок модели или проверок не прочитан). Такое встало бы у каждой
  гипотезы — план на паузу словами, одним местом, а не `stopped` у гипотез одна за другой до конца
  плана.

**Незнакомое — у сервиса**: пауза плана видна и снимается одной кнопкой, а повтор по кругу тратил бы
модель, остановка гипотез подряд — весь план.

Вид отказа сметы — по её полям, где они есть (токены и остаток потолка, описание, рынок, язык,
ключи), и по словам настроек — постоянным сметы; вид остановки — по началу слов
(`segment_run.JUDGE_STOPPED`, `segment_jobs.RETRIES_SPENT`) и слову «описание стёрто»
(`segment_estimate.NO_SEGMENT`).
"""

from __future__ import annotations

from enum import StrEnum

from backend.config.judge import JudgeMode
from backend.features.sales import chain_text
from backend.features.sales.segment_estimate import (
    CAP_UNREAD,
    MODEL_NOT_SET,
    NO_SEGMENT,
    SALES_OFF,
    SANDBOX,
    SERP_AHREFS,
    SERP_NOT_SET,
    VERIFIER_NOT_SET,
    Estimate,
)
from backend.features.sales.segment_jobs import RETRIES_SPENT
from backend.features.sales.segment_run import JUDGE_STOPPED
from backend.features.serp.markets import SERP_LANGUAGE


class Kind(StrEnum):
    """Что конвейер делает с отказом сметы или остановкой прогона (шапка модуля)."""

    RETRY = "retry"  # временное — повтор не чаще `SALES_PLAN_RETRY_MIN`
    STOP = "stop"  # у гипотезы — `stopped` словами
    PAUSE = "pause"  # у сервиса — план на паузу словами


#: Отказы сметы по настройке сервиса — постоянными сметы: их судят раньше токенов, потому что смета
#: считает токены и при отказе настройки адресов.
SETTINGS = frozenset(
    {SALES_OFF, MODEL_NOT_SET, CAP_UNREAD, VERIFIER_NOT_SET, SANDBOX, SERP_NOT_SET, SERP_AHREFS}
)
#: Начало слов сбоя, не прошедшего и после повторов задачи (`segment_jobs.RETRIES_SPENT`).
SPENT_RETRIES = RETRIES_SPENT.partition("{why}")[0]


def _settings(found: Estimate) -> bool:
    """Отказ сметы — настройка сервиса: постоянная сметы или потолок проверок адреса не задан."""
    month = found.month.cap if found.month is not None else None
    return found.refusal in SETTINGS or (isinstance(month, str) and found.refusal == month)


def _sheet(found: Estimate) -> bool:
    """Отказ сметы — у гипотезы: описание стёрто, ключей нет, рынок или язык листа не годятся."""
    return (
        (not found.segment and found.mode != JudgeMode.OFF)
        or not found.keywords
        or found.country not in SERP_LANGUAGE
        or found.language not in chain_text.LANGUAGES
    )


def of_refusal(found: Estimate) -> Kind:
    """Вид отказа сметы прогона плана (шапка модуля); смета без отказа — не спрашивают."""
    if _settings(found):
        return Kind.PAUSE
    if found.tokens_left is not None and found.tokens > found.tokens_left:
        return Kind.RETRY
    return Kind.STOP if _sheet(found) else Kind.PAUSE


def of_stop(error: str | None) -> Kind:
    """Вид остановки прогона плана — по его словам (шапка модуля)."""
    words = error or ""
    if NO_SEGMENT in words:
        return Kind.STOP
    if words.startswith((JUDGE_STOPPED, SPENT_RETRIES)):
        return Kind.RETRY
    return Kind.PAUSE
