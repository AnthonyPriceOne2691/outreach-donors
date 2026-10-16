"""Ключи прогона сбора: что берёт выдача, что отсеяно и почему — в смете и в отчёте прогона.

**Отсев — только мусор и точный повтор.** Мусор — по общей гигиене (`keywords/hygiene.clean`:
поисковый оператор, прошедший год, остаток разметки) с её причиной. Точный повтор — тот же
ключ после общей нормализации (`hygiene.normalize`) и для выдачи (`serp/protocol.keyword_key`):
второй раз за тот же запрос не платят. Повтор в отсеянных — **фразой, как её вписал человек**, и с
номером строки взятой («повтор строки 2»): под нормализованным ключом одна и та же фраза стояла
бы на экране и во «взятых», и в «отсеянных — повтор».

**Почти повтор — пометка, а не отсев.** Проверка на проде 14.10: общий ключ («… компания»)
отсеял как почти повторы все свои «… + город» — а у каждого города своя выдача и свои сайты.
Насколько ключи разные, решает человек: ключ взят, рядом пометка с именем похожего ключа и
числом общих слов. Сходство — общим правилом пула доноров («дубль чего»,
`keywords/dedup.near_duplicate_of` и `overlap`): своей копии правила у продаж нет.

**Ключ — название фирмы — тоже пометка, а не отсев.** Ключ с организационно-правовой формой
отдельным словом («ТОО …», «… GmbH», «… Sp. z o.o.») — имя одной фирмы: выдача найдёт её реестры и
карточки, а не сегмент. Формы многих стран и письменностей — данными в `legal_forms.py`; пометка
ложится в то же поле слов, что «похож на «…»», через «; » — ключ взят, решает человек.

**В отчёте прогона — и то, что вышло с покупкой** (`report.serp`): ключи, чью выдачу провайдер
не отдал (заплачено — не пришло), и ключи, по которым выдача пуста. Это разные исходы: первое —
сбой провайдера, второе — ключ ничего не находит (`runs/planning.Candidates.lost_keywords`).

**Почти повтор в отчёте — группой под похожим ключом, а не словами у каждого** (просьба владельца
16.10: одиннадцать ключей, у семи — одна и та же пометка, и где ключ, а где пометка, не видно).
Группы — тем же правилом, что пометка на запуске (`dedup.near_duplicate_of` по купленным
ключам); прочие пометки (название фирмы и любая другая) отчёт показывает один раз под списком.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.features.keywords import dedup, hygiene
from backend.features.sales.legal_forms import firm_form
from backend.features.sales.segment_models import SalesSegmentRunModel
from backend.features.serp.protocol import keyword_key

#: Второй такой же ключ — тот же запрос выдачи: платить за него второй раз не за что. `{line}` —
#: номер строки взятого ключа, как вписаны строки (пустые — тоже строки).
REPEAT = "повтор строки {line}: для выдачи это тот же запрос — второй раз за него не платим"
#: Почти повтор: ключ взят, выдача может частично совпасть — решает человек.
NEAR = (
    "похож на «{twin}» — общих слов {common} из {total}: выдача может частично совпасть, ключ взят"
)
#: Ключ с формой фирмы — имя одной фирмы; `{form}` — форма, как её вписал человек.
FIRM = "похоже на название фирмы («{form}»): выдача найдёт её реестры и карточки, а не сегмент"
#: Две пометки одного ключа — в одном поле слов.
NOTES_JOINT = "; "
#: Начало пометки почти повтора — до имени похожего ключа: по нему отчёт отделяет её от прочих.
NEAR_LEAD = NEAR.split("{twin}", 1)[0]


@dataclass(frozen=True, slots=True)
class Keywords:
    """Ключи набора: взятые — в том порядке, как вписаны; отсеянные — с причиной словами;
    пометки взятых (почти повтор) — словами."""

    taken: list[str]
    rejected: dict[str, str]
    notes: dict[str, str]


def _written(text: str) -> list[tuple[int, str]]:
    """Ключи, как их вписал человек, с номером строки поля: пустые строки — тоже строки."""
    lines = enumerate(text.splitlines(), 1)
    return [(number, kept) for number, line in lines if (kept := line.strip())]


def clean_keywords(text: str) -> Keywords:
    """Ключи строками → взятые, отсеянные и пометки. Взятые — ровно те, за которые заплатит
    выдача: по запросу на ключ, в первом написании. Повтор — в отсеянных фразой, как вписана, с
    номером строки взятой."""
    written = _written(text)
    lines = [hygiene.normalize(line) for _, line in written]
    good, rejected = hygiene.clean(lines)
    fit = set(good)
    taken: dict[str, str] = {}  # взятый ключ → как вписан
    first: dict[str, int] = {}  # ключ выдачи → номер строки взятого
    for (number, as_written), phrase in zip(written, lines, strict=True):
        if phrase not in fit:
            continue  # мусор: причина гигиены уже в `rejected`
        key = keyword_key(phrase)
        if key in first:
            rejected.setdefault(as_written, REPEAT.format(line=first[key]))
            continue
        first[key] = number
        taken[phrase] = as_written
    phrases = list(taken)
    return Keywords(phrases, rejected, _joined(phrases, _near(phrases), _firms(taken)))


def _firms(taken: dict[str, str]) -> dict[str, str]:
    """Пометки «название фирмы» — по ключу, как его вписал человек: регистр нужен формам
    (`legal_forms.CASED`), а взятый ключ уже в нижнем."""
    found = {phrase: firm_form(as_written) for phrase, as_written in taken.items()}
    return {phrase: FIRM.format(form=form) for phrase, form in found.items() if form is not None}


def _joined(taken: list[str], *notes: dict[str, str]) -> dict[str, str]:
    """Пометки ключа — одним полем слов: почти повтор и название фирмы вместе, через «; »."""
    joined = {phrase: [found[phrase] for found in notes if phrase in found] for phrase in taken}
    return {phrase: NOTES_JOINT.join(words) for phrase, words in joined.items() if words}


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
    #: Почти повтор → ключ, на который он похож: отчёт ставит его под этим ключом.
    twins: dict[str, str]
    #: Прочие пометки взятых — без «похож на «…»»: её заменяет группа под похожим ключом.
    remarks: dict[str, str]


def _remarks(notes: dict[str, str]) -> dict[str, str]:
    """Пометки ключей без почти повтора; ключ, у которого была только она, — без пометки."""
    kept = {
        phrase: [part for part in words.split(NOTES_JOINT) if not part.startswith(NEAR_LEAD)]
        for phrase, words in notes.items()
    }
    return {phrase: NOTES_JOINT.join(parts) for phrase, parts in kept.items() if parts}


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
    notes = _words(estimate.get("notes"))
    return RunKeywords(
        taken=list(run.keywords),
        notes=notes,
        rejected=_words(estimate.get("rejected")),
        lost=_phrases(serp.get("lost_keywords")),
        empty=_phrases(serp.get("empty_keywords")),
        twins=dedup.near_duplicate_of(list(run.keywords)),
        remarks=_remarks(notes),
    )
