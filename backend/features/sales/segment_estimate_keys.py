"""Ключи сметы, снятые человеком, и отпечаток сметы, которую он видел на запуске.

**Снятый ключ не покупается.** Человек снимает галочку у взятого ключа сметы — ключ уходит из
выдачи, из чисел сметы и из сметы прогона, а в ответе стоит отдельно от отсеянных: отсеянный —
мусор или повтор, так решил сервер; снятый — решил человек, и галочку можно вернуть.

**Снимают номер строки, как её вписал человек** (пустые — тоже строки), а не место в списке
взятых: снятие не сдвигает «повтор строки N» у отсеянных и не меняет, что отсеяно, — отсев идёт
по всему набору, снятие — после него. Номер взятого ключа — первая строка, чья фраза после общей
нормализации и есть этот ключ (`keyword_lines`): так его берёт `segment_keywords.clean_keywords`,
первым написанием, и тем же разбором строк (`_written`). Номер, под которым взятого ключа нет
(мусор, повтор, пустая строка, за концом набора), ничего не снимает и пишется в журнал: экран шлёт
только номера взятых ключей, чужой номер — его ошибка, а не решение человека.

**Отпечаток сметы** (`seen_digest`) — то, что человек видел и по чему запускает: гипотеза,
покупаемые ключи, снятые, описание сегмента (отпечатком судьи — `segment_judge.segment_digest`),
режим судьи, режим выдачи, рынок, язык писем и глубина. Запуск считает смету заново и сверяет
отпечаток (`api/sales/segment.py`): правка описания, режима или ключей после сметы — «смета
устарела». Числа сметы (сайты, остаток потолка модели) в отпечаток не входят: их меняют чужие
прогоны, а отказ по ним запуск проверяет сам.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.features.keywords import hygiene
from backend.features.sales.segment_judge import segment_digest
from backend.features.sales.segment_keywords import _written
from backend.features.sales.segment_models import SalesSegmentRunModel

if TYPE_CHECKING:
    from backend.features.sales.segment_estimate import Estimate

logger = logging.getLogger(__name__)

#: Ключ снятых в смете прогона (`Estimate.as_dict`): ключ → номер строки, как вписан.
EXCLUDED_KEY = "excluded"
#: Знаков отпечатка: шестнадцать шестнадцатеричных — 64 бита, случайного совпадения не бывает.
DIGEST_LENGTH = 16
#: Сколько чужих номеров строк назвать в журнале: остальное — числом.
STRAY_SHOWN = 20


@dataclass(frozen=True, slots=True)
class Chosen:
    """Взятые ключи после снятия: покупаемые — в том порядке, как вписаны; снятые и номера строк
    всех взятых — ключ → номер строки, как вписан."""

    bought: list[str]
    excluded: dict[str, int]
    lines: dict[str, int]


def keyword_lines(text: str, taken: Iterable[str]) -> dict[str, int]:
    """Номер строки, как вписана, у каждого взятого ключа: первая строка, чья фраза после общей
    нормализации — этот ключ. Взятый ключ — первое написание своего запроса, и раньше его
    строки той же фразы нет: иначе взятой была бы она (`segment_keywords.clean_keywords`)."""
    wanted = set(taken)
    lines: dict[str, int] = {}
    for number, line in _written(text):
        phrase = hygiene.normalize(line)
        if phrase in wanted:
            lines.setdefault(phrase, number)
    return lines


def chosen(text: str, taken: Sequence[str], excluded: Iterable[int]) -> Chosen:
    """Снять ключи по номерам строк: снятые — из взятых, номера чужих строк — в журнал."""
    lines = keyword_lines(text, taken)
    asked = set(excluded)
    off = {phrase: line for phrase, line in lines.items() if line in asked}
    if stray := sorted(asked - set(off.values())):
        logger.warning(
            "сбор продаж: снятые номера строк — не взятые ключи, ничего не снято",
            extra={"lines": stray[:STRAY_SHOWN], "count": len(stray)},
        )
    return Chosen([phrase for phrase in taken if phrase not in off], off, lines)


def seen_digest(found: Estimate, hypothesis_id: int) -> str:
    """Отпечаток того, что человек видел в смете и по чему запускает (см. докстроку модуля)."""
    seen = {
        "hypothesis": hypothesis_id,
        "keywords": found.keywords,
        "excluded": sorted(found.excluded.items(), key=lambda item: item[1]),
        "segment": segment_digest(found.segment),
        "mode": found.mode,
        "serp_mode": found.serp_mode,
        "country": found.country,
        "language": found.language,
        "depth_pages": found.depth_pages,
    }
    text = json.dumps(seen, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:DIGEST_LENGTH]


def run_excluded(run: SalesSegmentRunModel) -> list[str]:
    """Снятые на запуске ключи прогона — из его сметы, по порядку строк; прогон старше снятия
    или чужое поле — пусто, а не падение отчёта."""
    found = (run.estimate or {}).get(EXCLUDED_KEY)
    if not isinstance(found, dict):
        return []
    pairs = [(line, str(phrase)) for phrase, line in found.items() if isinstance(line, int)]
    return [phrase for _, phrase in sorted(pairs)]
