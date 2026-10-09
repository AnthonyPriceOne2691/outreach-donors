"""Текст из присланного файла: PDF, XLSX, DOCX, CSV, TXT и HTML — под потолками.

Файл прислал не наш человек (`docs/SECURITY.md`), и разбирается он как данные:
макросы, скрипты и формулы не исполняются никогда — читается только то, что
в файле уже записано, и только в память. Читать ли файл вообще и сколько
ждать, решает `attachment_text`: этот модуль исполняется отдельным процессом,
который тот убивает, если чтение зависло (`python -m …attachment_formats <вид>`,
файл — на stdin, итог — JSON на stdout).

**Потолки — во время чтения, а не после.** PDF на тысячу страниц и таблица на
миллион строк — не прайс, а способ занять воркер: читается начало, а пометка
в тексте и `note` говорят человеку, что дальше не читали. Цена стоит в первых
строках прайса, как и в письме (`inbound.MAX_TEXT_CHARS`).

**XLSX и DOCX — это ZIP, и размер после распаковки проверяется до распаковки.**
Файл в десять мегабайт, сжатый в сотню раз, — гигабайт XML. Объявленным
размерам можно верить: `zipfile` не отдаёт из части больше объявленного,
дальше — ошибка контрольной суммы.

**XML — без DTD и сущностей** (`defusedxml`): «миллиард смешков» съел бы память.
DOCX разбирается им напрямую, openpyxl берёт его сам, когда он установлен.

**Ячейка — тем, что видно на экране**, а не голым числом: 150 с форматом «$» —
это «$ 150». Без валюты цена из прайса уходила бы человеку с пометкой
«цена названа, а валюта — нет».
"""

from __future__ import annotations

import codecs
import csv
import dataclasses
import io
import itertools
import json
import logging
import re
import resource
import sys
import warnings
import zipfile
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from functools import lru_cache
from typing import Any
from xml.etree.ElementTree import Element

import chardet
import openpyxl
from defusedxml.ElementTree import fromstring as parse_xml
from pypdf import PdfReader

from backend.features.replies import charsets
from backend.features.replies.attachment_text import FileText, Kind
from backend.features.replies.html_text import text_from_html

logger = logging.getLogger(__name__)

_MB = 1024 * 1024

#: Знаков текста с одного файла — столько же, сколько письма уходит модели
#: (`inbound.MAX_TEXT_CHARS`): прайс в них укладывается, дальше — уже не прайс.
MAX_CHARS = 20_000
#: Страниц PDF. Медиакит — десяток страниц; тридцать — с запасом.
MAX_PAGES = 30
#: Листов таблицы и строк на лист.
MAX_SHEETS = 10
MAX_ROWS = 2_000
#: ZIP (XLSX, DOCX): всё распакованное вместе и число частей.
MAX_UNPACKED = 50 * _MB
MAX_PARTS = 1_000
#: Память процесса чтения. Файл, прошедший потолки размеров и всё же
#: раздувшийся, кончается `MemoryError` здесь, а не убийцей памяти у воркера.
MAX_MEMORY = 1024 * _MB

#: Чем кончается обрезанный текст.
CUT = "\n[… обрезано: дальше — в самом файле]"

#: Управляющие знаки, кроме табуляции и перевода строки: нулевой байт база
#: не хранит (`inbound.storable`), а остальным в тексте прайса делать нечего.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_BLANK_LINES = re.compile(r"\n{3,}")
#: Хвост незаконченного слова: обрезанное «150» стало бы «15» — другим числом.
_PARTIAL_WORD = re.compile(r"\S+\Z")


@dataclass
class _Text:
    """Текст файла под потолком знаков. Читатель смотрит на `full` и бросает
    чтение: дочитывать файл, который всё равно обрежется, — терять время."""

    parts: list[str] = field(default_factory=list)
    size: int = 0
    notes: list[str] = field(default_factory=list)
    full: bool = False
    #: Заголовок, который встанет перед первой непустой строкой: лист без
    #: единого значения не должен давать текста.
    heading: str | None = None

    def add(self, chunk: str) -> None:
        if self.full or not chunk.strip():
            return
        if self.heading is not None:
            heading, self.heading = self.heading, None
            self.add(heading)
        room = MAX_CHARS - len(CUT) - self.size
        if len(chunk) < room:
            self.parts.append(chunk)
            self.size += len(chunk) + 1
            return
        piece = chunk[: max(room, 0)]
        self.parts.append(_PARTIAL_WORD.sub("", piece) or piece)
        self.full = True

    def done(self, empty: str) -> FileText:
        """Итог: текст с пометкой обрезки — или, если текста нет, почему."""
        text = "\n".join(self.parts).replace("\r\n", "\n").replace("\r", "\n")
        text = _BLANK_LINES.sub("\n\n", _CONTROL.sub("", text)).strip()
        # Одиночный суррогат (битая таблица шрифта в PDF) драйвер базы не кодирует.
        text = text.encode("utf-8", "replace").decode("utf-8")
        if not text:
            return FileText(note=_joined(empty, *self.notes))
        if self.full:
            text += CUT
            self.notes.append(
                f"текст длиннее {MAX_CHARS:_} знаков — прочитано начало".replace("_", " ")
            )
        return FileText(text=text, note=_joined(*self.notes))


def _joined(*notes: str) -> str | None:
    return "; ".join(notes) or None


# --- PDF ---


def _pdf(data: bytes) -> FileText:
    """PDF: текст страниц по порядку.

    Запертый пустым паролем читается — так часто запрещают только печать
    и правку, — запертый настоящим нет."""
    pdf = PdfReader(io.BytesIO(data))
    if pdf.is_encrypted and not pdf.decrypt(""):
        return FileText(note="PDF защищён паролем — не читается, файл можно скачать")
    text = _Text()
    pages = len(pdf.pages)
    for page in itertools.islice(pdf.pages, MAX_PAGES):
        text.add(page.extract_text())
        if text.full:
            break
    if pages > MAX_PAGES and not text.full:
        text.notes.append(f"прочитаны первые {MAX_PAGES} страниц из {pages}")
    return text.done(
        "в PDF нет текста — похоже, страницы отсканированы, а распознавания текста нет; "
        "файл можно скачать"
    )


# --- ZIP: XLSX и DOCX ---


def _zip_refusal(data: bytes) -> str | None:
    """Почему не распаковывать ZIP, словами; `None` — можно. По оглавлению,
    до распаковки: бомба узнаётся по объявленным размерам."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        parts = archive.infolist()
    if len(parts) > MAX_PARTS:
        return f"в файле больше {MAX_PARTS} частей — так прайс не выглядит; файл можно скачать"
    if sum(part.file_size for part in parts) > MAX_UNPACKED:
        return (
            f"распакованный файл больше {MAX_UNPACKED // _MB} МБ — так прайс не выглядит; "
            "файл можно скачать"
        )
    return None


def _xlsx(data: bytes) -> FileText:
    """Таблица: видимые листы по порядку, строка — значения ячеек через табуляцию.

    Значения, а не формулы (`data_only`): формула — это код, а посчитанное
    Excel сохранил рядом. Скрытые листы не читаются: человек, открыв файл,
    их не увидит, и цена оттуда — не та, что донор показал.
    """
    refusal = _zip_refusal(data)
    if refusal is not None:
        return FileText(note=refusal)
    book = openpyxl.load_workbook(
        io.BytesIO(data), read_only=True, data_only=True, keep_links=False
    )
    try:
        return _book(book)
    finally:
        book.close()


def _book(book: Any) -> FileText:
    text = _Text()
    shown = [sheet for sheet in book.worksheets if sheet.sheet_state == "visible"]
    for sheet in shown[:MAX_SHEETS]:
        _sheet(sheet, text)
        if text.full:
            break
    if len(shown) > MAX_SHEETS and not text.full:
        text.notes.append(f"прочитаны первые {MAX_SHEETS} листов из {len(shown)}")
    if hidden := len(book.worksheets) - len(shown):
        text.notes.append(f"скрытых листов не читали: {hidden}")
    return text.done("в таблице нет ни одного значения")


def _sheet(sheet: Any, text: _Text) -> None:
    # Размеры листа, записанные в файле, бывают неверны: с ними читалась бы
    # одна ячейка из тысячи. Без них строки идут такими, какие они в файле.
    sheet.reset_dimensions()
    title = " ".join(str(sheet.title).split())
    text.heading = f"[лист «{title}»]"
    for number, row in enumerate(sheet.iter_rows(), start=1):
        if number > MAX_ROWS:
            text.notes.append(f"лист «{title}»: прочитаны первые {MAX_ROWS} строк")
            return
        text.add("\t".join(map(_cell, row)).rstrip("\t"))
        if text.full:
            return


def _cell(cell: Any) -> str:
    """Ячейка так, как её видно на экране: дата — датой, число — с валютой формата."""
    value = cell.value
    match value:
        case None:
            return ""
        case bool():
            return str(value).upper()
        case int() | float():
            return _number(value, cell.number_format)
        case datetime() | date() | time():
            return _moment(value)
        case _:
            return " ".join(str(value).split())


def _number(value: float, number_format: str | None) -> str:
    # Пятнадцать значащих цифр — столько показывает сам Excel: 0,1 + 0,2 — это
    # «0.3», а не «0.30000000000000004», которого в прайсе нет.
    shown = f"{value:.15g}" if isinstance(value, float) else str(value)
    before, after = _units(number_format or "General")
    return " ".join(part for part in (before, shown, after) if part)


#: Что из формата числа видно рядом с числом: текст в кавычках, валюта в скобках
#: (`[$€-407]`), знак после обратной черты и голый `$`. Отступ `_x` и заполнитель
#: `*x` — не текст: они только съедаются.
_SHOWN = re.compile(r'_.|\*.|"([^"]*)"|\[\$([^\]-]*)[^\]]*\]|\\(.)|(\$)')
#: То, что цифрой числа не бывает, даже если в нём цифры: «[$€-407]», «"10"».
_NOT_DIGITS = re.compile(r'"[^"]*"|\[[^\]]*\]|[\\_*].')
_DIGIT = re.compile(r"[0#?]")


@lru_cache(maxsize=256)
def _units(number_format: str) -> tuple[str, str]:
    """Текст формата до числа и после: `"$"#,##0` → («$», «»), `#,##0 "€"` → («», «€»)."""
    section = number_format.split(";", 1)[0]
    bare = _NOT_DIGITS.sub(lambda found: " " * len(found.group(0)), section)
    digit = _DIGIT.search(bare)
    at = digit.start() if digit else len(section)
    shown = [(found.start(), _shown(found)) for found in _SHOWN.finditer(section)]
    before = "".join(text for start, text in shown if start < at)
    after = "".join(text for start, text in shown if start >= at)
    return before.strip(), after.strip()


def _shown(found: re.Match[str]) -> str:
    return next((group for group in found.groups() if group), "")


def _moment(value: datetime | date | time) -> str:
    """Дата без полуночи: «2026-10-09», а не «2026-10-09T00:00:00»."""
    if isinstance(value, datetime) and value.time() == time.min:
        return value.date().isoformat()
    return value.isoformat()


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
#: Что в прогоне текста, кроме самого текста, даёт знаки.
_RUN_MARKS = {f"{_W}tab": "\t", f"{_W}br": "\n", f"{_W}cr": "\n", f"{_W}noBreakHyphen": "-"}


def _docx(data: bytes) -> FileText:
    """Документ Word: абзацы и таблицы по порядку, строка таблицы — ячейки через
    табуляцию. Колонтитулы и сноски не читаются: прайс в них не пишут."""
    refusal = _zip_refusal(data)
    if refusal is not None:
        return FileText(note=refusal)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root: Element = parse_xml(archive.read("word/document.xml"), forbid_dtd=True)
    _drop_fallbacks(root)
    text = _Text()
    for block in _blocks(root):
        text.add(_paragraph(block) if block.tag == f"{_W}p" else _table(block))
        if text.full:
            break
    return text.done("в документе нет текста")


def _drop_fallbacks(root: Element) -> None:
    """Надпись Word хранит дважды — рисунком и запасной копией (`mc:Fallback`);
    вторая повторила бы её текст."""
    for alternate in list(root.iter(f"{_MC}AlternateContent")):
        for fallback in alternate.findall(f"{_MC}Fallback"):
            alternate.remove(fallback)


def _blocks(root: Element) -> Iterator[Element]:
    """Абзацы и таблицы тела по порядку; поля формы (`w:sdt`) раскрываются."""
    stack = root.findall(f"{_W}body/*")[::-1]
    while stack:
        node = stack.pop()
        if node.tag in (f"{_W}p", f"{_W}tbl"):
            yield node
        elif node.tag == f"{_W}sdt":
            stack.extend(node.findall(f"{_W}sdtContent/*")[::-1])


def _paragraph(paragraph: Element) -> str:
    """Текст абзаца: прогоны подряд — Word режет слова на прогоны как угодно."""
    return "".join(
        (child.text or "") if child.tag == f"{_W}t" else _RUN_MARKS.get(child.tag, "")
        for run in paragraph.iter(f"{_W}r")
        for child in run
    )


def _table(table: Element) -> str:
    """Строки таблицы, ячейки через табуляцию; вложенная таблица — текстом ячейки."""
    rows = (
        "\t".join(
            " ".join(filter(None, map(_paragraph, cell.iter(f"{_W}p"))))
            for cell in row.findall(f"{_W}tc")
        ).rstrip("\t")
        for row in table.findall(f"{_W}tr")
    )
    return "\n".join(row for row in rows if row.strip())


# --- текст: CSV, TXT, HTML ---

_DELIMITERS = (",", ";", "\t", "|")
#: Сколько байт отдаётся угадыванию кодировки: `chardet` на мегабайтах — секунды.
_SAMPLE = 64 * 1024
#: Метки кодировки в начале файла. UTF-32 — раньше UTF-16: её метка начинается так же.
_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


def _decoded(data: bytes) -> str:
    """Байты в текст. Кодировку файла не называет никто: метка BOM, затем
    строгий UTF-8, затем догадка `chardet` по началу файла — и непрочитанное
    заменяется «�», а не роняет чтение (`charsets.decode`)."""
    marked = next((name for mark, name in _BOMS if data.startswith(mark)), None)
    if marked is not None:
        return data.decode(marked, "replace")
    text, problem = charsets.decode(data, "utf-8")
    if problem is None:
        return text
    return charsets.decode(data, _guess(data[:_SAMPLE]))[0]


#: Кодировки, которыми файлы пишут на деле. Прочие догадки `chardet` — Mac, DOS,
#: KOI8-T — на коротком тексте обычно ошибка: «Услуга;Цена» в cp1251 он зовёт
#: MacCyrillic с перевесом в четыре десятитысячных и читает «”слуга».
_LIVE = re.compile(
    r"windows-125\d|iso-?8859-\d+|koi8-[ru]|utf-|shift_jis|cp932|euc-|gb|big5|cp949|ascii",
    re.I,
)


def _guess(sample: bytes) -> str | None:
    """Кодировка по догадке `chardet`: первая живая из его кандидатов, иначе лучшая."""
    found = [candidate["encoding"] for candidate in chardet.detect_all(sample)]
    return next((name for name in found if name and _LIVE.match(name)), found[0] if found else None)


def _csv(data: bytes) -> FileText:
    """CSV: строки — ячейки через табуляцию, как у листа таблицы. Запятая между
    числами склеила бы их: «250,300» читалось бы одним числом. Разделитель —
    по первой строке: Excel на русской Windows пишет «;», а не «,»."""
    content = _decoded(data)
    first = next((line for line in content[:4096].splitlines() if line.strip()), "")
    rows = csv.reader(io.StringIO(content, newline=""), delimiter=max(_DELIMITERS, key=first.count))
    text = _Text()
    try:
        for number, row in enumerate(rows, start=1):
            if number > MAX_ROWS:
                text.notes.append(f"прочитаны первые {MAX_ROWS} строк")
                break
            text.add("\t".join(" ".join(cell.split()) for cell in row).rstrip("\t"))
            if text.full:
                break
    except csv.Error as exc:
        logger.info("вложение: CSV не разобран — беру текст как есть", extra={"error": str(exc)})
        return _plain(content, "таблица не разобрана как CSV — текст как есть")
    return text.done("в файле нет ни одного значения")


def _txt(data: bytes) -> FileText:
    return _plain(_decoded(data))


def _html(data: bytes) -> FileText:
    """HTML: текст страницы теми же правилами, что у письма без текстовой части
    (`html_text`), — скрипты и стили выброшены, строки на месте."""
    return _plain(text_from_html(_decoded(data), limit=MAX_CHARS * 2))


def _plain(content: str, *notes: str) -> FileText:
    text = _Text(notes=list(notes))
    text.add(content)
    return text.done("в файле нет текста")


_READERS: dict[Kind, Callable[[bytes], FileText]] = {
    Kind.PDF: _pdf,
    Kind.XLSX: _xlsx,
    Kind.DOCX: _docx,
    Kind.CSV: _csv,
    Kind.TXT: _txt,
    Kind.HTML: _html,
}


def read(kind: Kind, data: bytes) -> FileText:
    """Текст файла известного вида. Не бросает: файл чужой, и разборщик законно
    падает на нём чем угодно — тогда `note` словами и тип ошибки для журнала."""
    try:
        with warnings.catch_warnings():
            # Предупреждения разборщиков — о присланном файле («нет стиля по
            # умолчанию»), а не о нашем коде: ни тексту, ни журналу они не нужны.
            warnings.simplefilter("ignore")
            return _READERS[kind](data)
    except Exception as exc:  # noqa: BLE001 — разборщик чужого файла падает чем угодно
        failure = type(exc).__name__
        logger.warning(
            "вложение: разборщик не прочитал файл", extra={"kind": kind, "error": failure}
        )
        return FileText(note=f"не удалось прочитать файл: {failure}", failure=failure)


def _limit_memory() -> None:
    try:
        resource.setrlimit(resource.RLIMIT_AS, (MAX_MEMORY, MAX_MEMORY))
    except (ValueError, OSError) as exc:
        # macOS потолок адресного пространства не ставит; прод — Linux, там ставится.
        logger.info(
            "вложение: потолок памяти процесса чтения не поставлен", extra={"error": str(exc)}
        )


def main(argv: Sequence[str]) -> None:
    """Процесс чтения: вид — аргументом, файл — на stdin, итог — JSON на stdout."""
    _limit_memory()
    found = read(Kind(argv[1]), sys.stdin.buffer.read())
    sys.stdout.write(json.dumps(dataclasses.asdict(found)))


if __name__ == "__main__":
    main(sys.argv)
