"""Ключи прогона сбора: что берёт выдача, что отсеяно и почему — в смете и в отчёте прогона.

**Отсев — только мусор и точный повтор.** Мусор — по общей гигиене (`keywords/hygiene.clean`:
поисковый оператор, прошедший год, остаток разметки) с её причиной. Точный повтор — тот же
ключ после общей нормализации (`hygiene.normalize`) и для выдачи (`serp/protocol.keyword_key`):
второй раз за тот же запрос не платят.

**Почти повтор — пометка, а не отсев.** Проверка на проде 14.10: общий ключ («… компания»)
отсеял как почти повторы все свои «… + город» — а у каждого города своя выдача и свои сайты.
Насколько ключи разные, решает человек: ключ взят, рядом пометка с именем похожего ключа и
числом общих слов. Сходство — общим правилом пула доноров («дубль чего»,
`keywords/dedup.near_duplicate_of` и `overlap`): своей копии правила у продаж нет.

**В отчёте прогона — и то, что вышло с покупкой** (`report.serp`): ключи, чью выдачу провайдер
не отдал (заплачено — не пришло), и ключи, по которым выдача пуста. Это разные исходы: первое —
сбой провайдера, второе — ключ ничего не находит (`runs/planning.Candidates.lost_keywords`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.features.keywords import dedup, hygiene
from backend.features.sales.segment_models import SalesSegmentRunModel
from backend.features.serp.protocol import keyword_key

#: Второй такой же ключ — тот же запрос выдачи: платить за него второй раз не за что.
REPEAT = "повтор ключа выше: для выдачи это тот же запрос — второй раз за него не платим"
#: Почти повтор: ключ взят, выдача может частично совпасть — решает человек.
NEAR = (
    "похож на «{twin}» — общих слов {common} из {total}: выдача может частично совпасть, ключ взят"
)


@dataclass(frozen=True, slots=True)
class Keywords:
    """Ключи набора: взятые — в том порядке, как вписаны; отсеянные — с причиной словами;
    пометки взятых (почти повтор) — словами."""

    taken: list[str]
    rejected: dict[str, str]
    notes: dict[str, str]


def clean_keywords(text: str) -> Keywords:
    """Ключи строками → взятые, отсеянные и пометки. Взятые — ровно те, за которые заплатит
    выдача: по запросу на ключ, в первом написании."""
    lines = [hygiene.normalize(line) for line in text.splitlines() if line.strip()]
    good, rejected = hygiene.clean(lines)
    fit = set(good)
    taken: list[str] = []
    seen: set[str] = set()
    for phrase in lines:
        if phrase not in fit:
            continue  # мусор: причина гигиены уже в `rejected`
        if keyword_key(phrase) in seen:
            rejected.setdefault(phrase, REPEAT)
            continue
        seen.add(keyword_key(phrase))
        taken.append(phrase)
    return Keywords(taken, rejected, _near(taken))


def _near(taken: list[str]) -> dict[str, str]:
    """Пометки почти повторов — общим правилом пула доноров: отсеянный им ключ у продаж остаётся,
    а пометка называет, на какой взятый он похож и сколько у них общих слов."""
    notes: dict[str, str] = {}
    for phrase, twin in dedup.near_duplicate_of(taken).items():
        common, total = dedup.overlap(phrase, twin)
        notes[phrase] = NEAR.format(twin=twin, common=common, total=total)
    return notes


@dataclass(frozen=True, slots=True)
class RunKeywords:
    """Ключи прогона для отчёта: взятые с пометками и отсеянные — как их видел человек на
    запуске (смета прогона); пропавшие и пустые — что вышло с покупкой выдачи."""

    taken: list[str]
    notes: dict[str, str]
    rejected: dict[str, str]
    #: Выдачу провайдер не отдал: заплачено, а не пришло.
    lost: list[str]
    #: Выдача пришла пустой: по ключу ничего не нашлось.
    empty: list[str]


def _words(found: Any) -> dict[str, str]:
    """Словарь «ключ → слова» из JSON сметы; чужое — пусто, а не падение отчёта."""
    if not isinstance(found, dict):
        return {}
    return {str(key): str(value) for key, value in found.items()}


def _phrases(found: Any) -> list[str]:
    return [str(phrase) for phrase in found] if isinstance(found, list) else []


def run_keywords(run: SalesSegmentRunModel) -> RunKeywords:
    """Ключи прогона из его сметы и итога выдачи. Пока выдачу не купили — пропавших и пустых
    нет: их называет покупка (`report.serp`)."""
    estimate = run.estimate or {}
    serp = (run.report or {}).get("serp")
    serp = serp if isinstance(serp, dict) else {}
    return RunKeywords(
        taken=list(run.keywords),
        notes=_words(estimate.get("notes")),
        rejected=_words(estimate.get("rejected")),
        lost=_phrases(serp.get("lost_keywords")),
        empty=_phrases(serp.get("empty_keywords")),
    )
