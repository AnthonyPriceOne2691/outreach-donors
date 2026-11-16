"""Книга «План работ» (.xlsx) — в черновик плана: гипотезы по листам, поля карточки и ключи.

Доработка 14.11.2026, вид книги принят владельцем 16.11: одна книга задаёт работу на неделю или
месяц, лист — гипотеза. Режим (экономный или быстрый) и общая цель выбираются в окне загрузки — в книге их
нет. Здесь только чтение: ничего не пишет и никуда не ходит; смета плана — `plan_estimate.py`.

**Книга** — только .xlsx, по образцу загрузки доноров списком (`donors/bulk_file.py`): ZIP мерится
по оглавлению до распаковки (`attachment_formats.zip_size`) — книга-бомба на гигабайты съела бы
память сервера; значения, а не формулы (`data_only`); ячейка — строкой, как её видит человек
(`bulk_file._value`). Скрытые листы не читаются: человек их не видит. Лист «Как заполнять» —
инструкция, пустой лист — пустой: оба пропускаются, и черновик называет их словами.

**Лист** — над строкой «Ключи» карточка (`plan_card.py`), под ней ключи, по одному в строке, в
колонке A. Дальше колонки B лист не читается: в C — подсказки.

**Ключи — те, что купит выдача**: общей нормализацией (`hygiene.normalize`), без пустых строк и
точных повторов (`serp/protocol.keyword_key`) и без мусора гигиены (`hygiene.rejection_reason`) —
тем же отсевом, что у прогона сбора (`segment_keywords.clean_keywords`): иначе смета плана назвала
бы ключи, которые прогон отсеет. Сколько пустых строк и повторов убрано, что отсеяно и почему — в
черновике. Почти повторы здесь не ищутся: их сверка попарная (`dedup.near_duplicate_of`) — на 30 000
ключей это минуты, а ключ она не снимает, только помечает; пометку назовёт смета прогона.

**Ошибка листа не роняет книгу.** Неизвестная подпись, значение вне списка, не число в «Найти
сайтов», пустой список ключей, битый лист — ошибки своего листа словами: какой лист, какое поле, что
вписать. Книга отвергается целиком (`PlanFileError`) только когда её не прочитать: не .xlsx, больше
предела, бомба, битый ZIP, листов больше предела, ни одного листа-гипотезы.

**Предупреждения** — не ошибки: отсеянные ключи; ключи, которые есть и на другом листе (на общем
рынке выдачу по ним купят дважды).
"""

from __future__ import annotations

import io
import logging
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field, replace
from pathlib import PurePath
from typing import Any

import openpyxl

from backend.features.donors.bulk_file import _shown_name, _value
from backend.features.keywords import hygiene
from backend.features.replies.attachment_formats import MAX_PARTS, zip_size
from backend.features.sales.intake import IntakeError
from backend.features.sales.plan_card import (
    FIELDS,
    KEYS_LABEL,
    LABELS,
    Card,
    Field,
    SheetIssue,
    card_of,
    flat,
)
from backend.features.sales.segment_spend import number
from backend.features.serp.protocol import keyword_key

logger = logging.getLogger(__name__)

#: Файл — не больше: прокси пропускает тело до 8 МБ (`deploy/proxy/outreach.conf`), мегабайт —
#: обёртке формы. Замер 16.11.2026: книга из 10 листов по 30 000 ключей — 4,7 МБ.
MAX_BYTES = 7 * 2**20
#: Распакованная книга — не больше. Свой предел, а не общий у вложений (50 МБ): та же книга
#: распаковывается в 40 МБ (сжатие ×8,6), и книга под предел файла с ключами длиннее его бы не
#: прошла. Бомба сжата в сотни и тысячи раз — её этот предел ловит по-прежнему.
MAX_UNPACKED = 128 * 2**20
#: Листов-гипотез в книге: «пилотов в книге — до 10» (инструкция книги-примера).
MAX_SHEETS = 10
#: Ключей на листе: под нишу выгружают «10, 20, 30 тысяч» ключей на пилот (слово владельца 14.11).
MAX_SHEET_KEYS = 30_000
#: Ключей в книге — следствие двух пределов выше; своей проверки нет: больше набрать нельзя.
MAX_KEYS = MAX_SHEETS * MAX_SHEET_KEYS
#: Строк в листе Excel. Дальше лист не читается: строка с подложенным номером в миллиарды иначе
#: стоила бы миллиарда пустых строк — разборщик заполняет пропуски (`openpyxl`, `_cells_by_row`).
EXCEL_ROWS = 1_048_576
#: Ошибок «нет такой подписи» на лист — не больше: без строки «Ключи» ошибкой стал бы каждый ключ.
MAX_STRAYS = 3
#: Знаков ячейки в словах ошибки: в ячейке бывает абзац.
CELL_CHARS = 60
#: Примеров общих ключей в предупреждении.
SHOWN_COMMON = 3
#: Лист-инструкция книги-примера: гипотезой не читается.
GUIDE_SHEET = "Как заполнять"
#: Начало ZIP — книга .xlsx; без расширения файл узнаётся по нему.
ZIP_MAGIC = b"PK\x03\x04"

GUIDE = "инструкция — не гипотеза"
HIDDEN = "скрытый лист: человек его не видит, план его не читает"
EMPTY = "пустой лист"
NO_KEYS_ROW = (
    f"нет строки «{KEYS_LABEL}» — под карточкой нужна строка «{KEYS_LABEL}», ключи — под ней, "
    "по одному в строке, в колонке A"
)
NO_KEYS = f"под строкой «{KEYS_LABEL}» нет ни одного ключа — впишите по ключу в строке, в колонке A"
ALL_REJECTED = "все ключи отсеяны — выдаче покупать нечего: причина — у каждого ключа"
TOO_MANY_KEYS = f"ключей больше {number(MAX_SHEET_KEYS)} — столько берёт один лист: разделите ключи на два листа"
UNREADABLE_SHEET = "лист не читается — сохраните книгу в Excel заново"


class PlanFileError(IntakeError):
    """Книгу не прочитать целиком: текст говорит почему и что сделать. Отказ загрузки продаж —
    тот же ответ 400 словами (`api/errors.py`)."""


class _UnreadableSheetError(Exception):
    """Лист не читается: разборщик чужого файла падает чем угодно — битый XML, текст в числе."""


@dataclass(frozen=True, slots=True)
class PlanKeys:
    """Ключи листа после чистки."""

    #: Ключи, как их купит выдача: нормализованные, по порядку листа, без повторов и мусора.
    taken: tuple[str, ...] = ()
    #: Пустых строк между ключами — убрано.
    blank: int = 0
    #: Точных повторов внутри листа — убрано: второе и следующие написания того же запроса.
    repeats: int = 0
    #: Мусор гигиены: ключ → причина словами. Выдача его не купит.
    rejected: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PlanHypothesis:
    """Лист-гипотеза: поля карточки, ключи, ошибки и предупреждения. С ошибками к запуску не идёт,
    но прочитанное в нём показывается."""

    #: Имя листа, как в книге.
    sheet: str
    card: Card
    keys: PlanKeys
    issues: tuple[SheetIssue, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        """Годится к запуску: ошибок нет."""
        return not self.issues


@dataclass(frozen=True, slots=True)
class PlanDraft:
    """Черновик плана: гипотезы по листам в порядке книги — с ошибками и без; пропущенные листы —
    имя листа → почему пропущен."""

    hypotheses: tuple[PlanHypothesis, ...]
    skipped: dict[str, str] = field(default_factory=dict)

    @property
    def ready(self) -> tuple[PlanHypothesis, ...]:
        """Гипотезы без ошибок — те, что пойдут в смету и к запуску."""
        return tuple(hypothesis for hypothesis in self.hypotheses if hypothesis.ready)


def read_plan(data: bytes, name: str) -> PlanDraft:
    """Черновик плана из книги. Книгу не прочитать — `PlanFileError` словами."""
    shown = _shown_name(name)
    _check(data, name, shown)
    book = _opened(data, shown)
    try:
        return _draft(book, shown)
    finally:
        book.close()


def cleaned(written: Sequence[str], *, blank: int = 0) -> PlanKeys:
    """Ключи листа, как вписаны, → ключи, которые купит выдача: повторы и мусор — счётом и словами."""
    taken: list[str] = []
    rejected: dict[str, str] = {}
    seen: set[str] = set()
    for as_written in written:
        phrase = hygiene.normalize(as_written)
        key = keyword_key(phrase)
        if key in seen:
            continue
        seen.add(key)
        reason = hygiene.rejection_reason(phrase)
        if reason is None:
            taken.append(phrase)
        else:
            rejected[phrase] = reason
    return PlanKeys(tuple(taken), blank, len(written) - len(seen), rejected)


def _check(data: bytes, name: str, shown: str) -> None:
    """Размер и вид файла — до разбора."""
    if len(data) > MAX_BYTES:
        raise PlanFileError(
            f"файл {shown} больше {MAX_BYTES // 2**20} МБ — разделите план на две книги"
        )
    if not data:
        raise PlanFileError(f"файл {shown} пуст — в нём нет ни одного листа")
    suffix = PurePath(name or "").suffix.lower()
    if suffix == ".xls":
        raise PlanFileError(f"файл {shown} — старый формат Excel (.xls): сохраните книгу как .xlsx")
    if suffix not in ("", ".xlsx") or not data.startswith(ZIP_MAGIC):
        raise PlanFileError(
            f"файл {shown} — не книга Excel (.xlsx): план читается только из .xlsx, как в примере"
        )


def _opened(data: bytes, shown: str) -> Any:
    """Книга для чтения значений — после меры ZIP по оглавлению. Битый ZIP или XML книги —
    разборщик падает чем угодно (`attachment_formats`) — отказ словами, а не ответ 500."""
    try:
        parts, unpacked = zip_size(data)
        bomb = parts > MAX_PARTS or unpacked > MAX_UNPACKED
        book = None
        if not bomb:
            book = openpyxl.load_workbook(
                io.BytesIO(data), read_only=True, data_only=True, keep_links=False
            )
    except Exception as exc:
        raise PlanFileError(
            f"файл {shown} не читается как книга Excel (.xlsx) — сохраните её в Excel заново"
        ) from exc
    if book is None:
        raise PlanFileError(
            f"файл {shown} распаковывается в {unpacked // 2**20} МБ из {parts} частей — так книга "
            f"плана не выглядит: в книге из {MAX_SHEETS} листов по {number(MAX_SHEET_KEYS)} ключей "
            "около 40 МБ"
        )
    return book


def _draft(book: Any, shown: str) -> PlanDraft:
    """Листы книги по порядку: гипотезы и пропущенные."""
    skipped: dict[str, str] = {}
    sheets: list[tuple[str, Any]] = []
    for sheet in book.worksheets:
        title = str(sheet.title)
        if sheet.sheet_state != "visible":
            skipped[title] = HIDDEN
        elif flat(title) == flat(GUIDE_SHEET):
            skipped[title] = GUIDE
        else:
            sheets.append((title, sheet))
    if len(sheets) > MAX_SHEETS:
        raise PlanFileError(
            f"в книге {shown} листов-гипотез {len(sheets)} — план берёт до {MAX_SHEETS}: "
            "перенесите лишние в другую книгу"
        )
    found: list[PlanHypothesis] = []
    for title, sheet in sheets:
        hypothesis = _sheet(sheet, title)
        if hypothesis is None:
            skipped[title] = EMPTY
        else:
            found.append(hypothesis)
    if not found:
        raise PlanFileError(
            f"в книге {shown} нет ни одного листа-гипотезы: лист — карточка гипотезы и под ней "
            f"строка «{KEYS_LABEL}» с ключами"
        )
    return PlanDraft(tuple(_compared(found)), skipped)


def _rows(sheet: Any) -> Iterator[tuple[int, str, str]]:
    """Строки листа: номер, колонки A и B строками, как их видит человек."""
    # Размеры листа, записанные в файле, бывают неверны (`attachment_formats._sheet`).
    sheet.reset_dimensions()
    try:
        rows = sheet.iter_rows(max_row=EXCEL_ROWS, max_col=2, values_only=True)
        for index, row in enumerate(rows, start=1):
            first, second = (*row, None, None)[:2]
            yield index, _value(first), _value(second)
    except Exception as exc:
        raise _UnreadableSheetError(str(exc)[:CELL_CHARS]) from exc


def _sheet(sheet: Any, title: str) -> PlanHypothesis | None:
    """Лист-гипотеза; пустой лист — `None`."""
    reading = _Reading()
    try:
        for row, first, second in _rows(sheet):
            if not reading.take(row, first, second):
                break
    except _UnreadableSheetError as exc:
        logger.warning("план работ: лист не читается", extra={"sheet": title, "error": str(exc)})
        return PlanHypothesis(title, Card(), PlanKeys(), (SheetIssue(None, UNREADABLE_SHEET),))
    return reading.done(title) if reading.filled else None


def _cut(text: str) -> str:
    return text if len(text) <= CELL_CHARS else text[: CELL_CHARS - 1] + "…"


@dataclass(slots=True)
class _Reading:
    """Лист по ходу чтения: значения карточки по подписям, ключи как вписаны, ошибки строк."""

    values: dict[Field, str] = field(default_factory=dict)
    #: Строка подписи поля — для слов «поле дважды».
    rows: dict[Field, int] = field(default_factory=dict)
    issues: list[SheetIssue] = field(default_factory=list)
    in_keys: bool = False
    keys: list[str] = field(default_factory=list)
    blank: int = 0
    #: Пустые строки после последнего ключа: «между ключами» они, только если ниже будет ключ.
    pending: int = 0
    strays: int = 0
    filled: bool = False

    def take(self, row: int, first: str, second: str) -> bool:
        """Строка листа; `False` — дальше не читать: ключей больше предела."""
        self.filled = self.filled or bool(first or second)
        if self.in_keys:
            return self._key(first)
        if flat(first) == flat(KEYS_LABEL):
            self.in_keys = True
        elif first or second:
            self._card(row, first, second)
        return True

    def _key(self, first: str) -> bool:
        if not first:
            self.pending += 1
            return True
        self.blank += self.pending
        self.pending = 0
        self.keys.append(first)
        return len(self.keys) <= MAX_SHEET_KEYS

    def _card(self, row: int, first: str, second: str) -> None:
        found = FIELDS.get(flat(first))
        if found is None:
            self._stray(row, first, second)
        elif found in self.rows:
            self.issues.append(
                SheetIssue(
                    found,
                    f"«{LABELS[found]}» дважды — в строках {self.rows[found]} и {row}: "
                    "оставьте одну",
                )
            )
        else:
            self.rows[found] = row
            self.values[found] = second

    def _stray(self, row: int, first: str, second: str) -> None:
        """Строка карточки без известной подписи — ошибка; дальше предела — только счётом."""
        self.strays += 1
        if self.strays > MAX_STRAYS:
            return
        known = ", ".join(f"«{label}»" for label in LABELS.values())
        words = (
            f"строка {row}: подписи «{_cut(first)}» в карточке нет — поля карточки: {known}"
            if first
            else f"строка {row}: значение «{_cut(second)}» без подписи в колонке A"
        )
        self.issues.append(SheetIssue(None, words))

    def done(self, title: str) -> PlanHypothesis:
        """Прочитанный лист — в гипотезу черновика."""
        card, issues = card_of(self.values)
        keys = cleaned(self.keys, blank=self.blank)
        found = [*self.issues, *issues, *self._key_issues(keys)]
        warnings = []
        if keys.rejected:
            warnings.append(
                f"отсеяно ключей: {number(len(keys.rejected))} — выдача их не купит; причина — у "
                "каждого ключа"
            )
        return PlanHypothesis(title, card, keys, tuple(found), tuple(warnings))

    def _key_issues(self, keys: PlanKeys) -> list[SheetIssue]:
        issues: list[SheetIssue] = []
        if self.strays > MAX_STRAYS:
            issues.append(
                SheetIssue(None, f"и ещё строк без подписи карточки: {self.strays - MAX_STRAYS}")
            )
        if not self.in_keys:
            issues.append(SheetIssue(None, NO_KEYS_ROW))
        elif len(self.keys) > MAX_SHEET_KEYS:
            issues.append(SheetIssue(None, TOO_MANY_KEYS))
        elif not self.keys:
            issues.append(SheetIssue(None, NO_KEYS))
        elif not keys.taken:
            issues.append(SheetIssue(None, ALL_REJECTED))
        return issues


def _compared(found: list[PlanHypothesis]) -> list[PlanHypothesis]:
    """Листы между собой: имя гипотезы, уже занятое листом выше, — ошибка; общие ключи —
    предупреждение листа ниже."""
    queries = [frozenset(map(keyword_key, hypothesis.keys.taken)) for hypothesis in found]
    out: list[PlanHypothesis] = []
    for index, hypothesis in enumerate(found):
        issues = list(hypothesis.issues)
        warnings = list(hypothesis.warnings)
        for before, earlier in zip(out, queries, strict=False):
            name = hypothesis.card.name
            if name and name == before.card.name:
                issues.append(
                    SheetIssue(
                        Field.NAME,
                        f"«{LABELS[Field.NAME]}»: «{name}» уже у листа «{before.sheet}» — по имени "
                        "гипотезу выбирают, у листов плана имена разные",
                    )
                )
            if common := queries[index] & earlier:
                warnings.append(_common(hypothesis, before, common))
        out.append(replace(hypothesis, issues=tuple(issues), warnings=tuple(warnings)))
    return out


def _common(hypothesis: PlanHypothesis, before: PlanHypothesis, common: frozenset[str]) -> str:
    """Общие ключи двух листов словами: сколько, примеры, и купят ли выдачу по ним дважды."""
    shown = [phrase for phrase in hypothesis.keys.taken if keyword_key(phrase) in common]
    examples = ", ".join(f"«{phrase}»" for phrase in shown[:SHOWN_COMMON])
    shared = set(hypothesis.card.markets) & set(before.card.markets)
    tail = (
        "на общем рынке выдачу по ним купят дважды"
        if shared
        else "рынки у листов разные — выдача у ключей своя"
    )
    return (
        f"общих ключей с листом «{before.sheet}» — {number(len(common))} (например, {examples}): "
        f"{tail}"
    )
