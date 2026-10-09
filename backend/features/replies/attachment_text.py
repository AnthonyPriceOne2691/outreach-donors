"""Текст из вложения ответа: что читается, что нет — и почему отдельным процессом.

Прайс приходит файлом чаще, чем текстом письма, а цену модель брала только
из текста — и видела «see attached» без единого числа. Здесь файл становится
текстом для разбора цены (`extract`) и для экрана — или причиной словами,
почему не стал.

**Вид файла — по байтам, а не по типу отправителя и не по имени.** Тип пишет
кто угодно, имя — тоже: «price.pdf» бывает картинкой, а «price.xls», выгруженный
сайтом, — страницей HTML с таблицей. Сигнатура решает за двоичные форматы;
расширение — только там, где байты молчат: текст, CSV.

**Читается то, что даёт текст**: PDF, XLSX, DOCX, CSV, TXT и HTML. Картинки —
нет: распознавания текста нет. Архивы — нет: прайс кладут файлом, а распаковка
чужого архива — ещё одна поверхность атаки. Старые .xls и .doc — нет: их
разборщики тянут за собой больше, чем стоят два уходящих формата. Каждому «нет» —
строка словами (`FileText.note`): человек на экране должен знать, что файл не
прочитан и почему, а не гадать по пустому месту.

**Чтение — отдельным процессом с потолком времени.** Файл чужой, и разборщик
может на нём зависнуть: PDF с петлёй в дереве страниц, лист с миллионом пустых
ячеек. Поток не убить: он доедал бы процессор воркера и после отказа, а
`asyncio.run` ждал бы его на выходе. Процесс убивается по таймауту
(`subprocess.run`). Цена — около 0,2 с на запуск питона, и платится она раз на
файл: прочитанное хранится (`attachments.ReplyFiles`). Окружение у процесса
пустое — ключей и адреса базы в нём нет, — а потолок памяти он ставит себе сам
(`attachment_formats.MAX_MEMORY`). Здесь же нет ни одного разборщика: их
грузит только процесс чтения, а сервер и воркер — нет.
"""

from __future__ import annotations

import codecs
import io
import json
import logging
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

logger = logging.getLogger(__name__)

#: Секунд на один файл. Прайс читается за доли секунды; двадцать — запас на
#: медленную машину и большой PDF, а не на файл, который не кончается.
TIMEOUT_S = 20.0

#: Процесс чтения: тот же питон и модуль разборщиков. Рабочий каталог — тот,
#: откуда импортирован пакет `backend`: процесс берёт тот же код, что и
#: вызвавший, — в дереве разработки и в установленном колесе одинаково.
_READER = "backend.features.replies.attachment_formats"
_PACKAGE_ROOT = Path(__file__).resolve().parents[3]

PICTURE = "картинка: текст с картинок не читается, распознавания нет — файл можно скачать"
ARCHIVE = "архив: архивы не распаковываются — файл можно скачать"
PROTECTED = "файл защищён паролем — не читается, файл можно скачать"

_PDF = b"%PDF-"
_ZIP = (b"PK\x03\x04", b"PK\x05\x06")
#: Контейнер старого Office (OLE2): .xls, .doc — и новый файл, запертый паролем.
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_PICTURES = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"II*\x00", b"MM\x00*")
_ARCHIVES = (
    b"Rar!\x1a\x07",
    b"7z\xbc\xaf\x27\x1c",
    b"\x1f\x8b",
    b"\xfd7zXZ\x00",
    b"\x28\xb5\x2f\xfd",
)
#: Текст в UTF-16 и UTF-32 законно полон нулевых байтов — его выдаёт метка BOM.
_WIDE_BOMS = (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE, codecs.BOM_UTF32_BE)
_HTML_STARTS = (b"<!doctype html", b"<html", b"<table")

#: Имя обещает формат, которого в байтах нет, — словами, чего именно.
_PROMISED = {
    ".pdf": "PDF",
    ".xlsx": "таблица Excel",
    ".xlsm": "таблица Excel",
    ".docx": "документ Word",
    ".docm": "документ Word",
}
_OLD_OFFICE = {
    ".xls": "старый формат Excel (.xls)",
    ".doc": "старый формат Word (.doc)",
    ".ppt": "старый формат PowerPoint (.ppt)",
}
_PICTURE_NAMES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".heic", ".heif",
    ".avif", ".svg", ".ico",
})  # fmt: skip
_ARCHIVE_NAMES = frozenset({".zip", ".rar", ".7z", ".gz", ".tgz", ".tar", ".bz2", ".xz", ".zst"})


class Kind(StrEnum):
    """Что умеем читать. Значение — имя вида в команде процесса чтения."""

    PDF = "pdf"
    XLSX = "xlsx"
    DOCX = "docx"
    CSV = "csv"
    TXT = "txt"
    HTML = "html"


_TEXTUAL = {
    ".csv": Kind.CSV,
    ".tsv": Kind.CSV,
    ".txt": Kind.TXT,
    ".text": Kind.TXT,
    ".htm": Kind.HTML,
    ".html": Kind.HTML,
}


@dataclass(frozen=True, slots=True)
class FileText:
    """Что прочитано из файла.

    `text` — то, что увидят модель и человек; пусто — текста нет. `note` —
    человеку словами: почему текста нет или чем он неполон. `failure` — тип
    ошибки разборщика, только для журнала: трасса человеку ничего не скажет.
    """

    text: str | None = None
    note: str | None = None
    failure: str | None = None


class UnreadableError(ValueError):
    """Файл не читается. Сообщение словами говорит почему — оно идёт на экран."""


def extension_of(name: str) -> str:
    """Расширение так, как его поймёт система, которая файл откроет.

    Точки и пробелы в конце Windows отбрасывает: «прайс.exe.» у неё
    запускается как «.exe», и проверка по буквальному имени его пропустила бы.
    """
    tail = name.strip().rstrip(". ").lower()
    _, dot, extension = tail.rpartition(".")
    return f".{extension}" if dot else ""


def kind_of(name: str, data: bytes) -> Kind:
    """Вид файла: по сигнатуре байтов, а где байты молчат, — по расширению.

    Не читается — `UnreadableError` со словами, почему.
    """
    if not data:
        raise UnreadableError("файл пустой — читать нечего")
    extension = extension_of(name)
    if data.startswith(_ZIP):
        return _zipped(data, extension)
    if data.startswith(_OLE):
        raise UnreadableError(_old_office(extension))
    # Заголовок PDF законно стоит не с первого байта, а в первом килобайте.
    if _PDF in data[:1024]:
        return Kind.PDF
    if data.startswith(_PICTURES) or data[8:12] == b"WEBP":
        raise UnreadableError(PICTURE)
    if data.startswith(_ARCHIVES):
        raise UnreadableError(ARCHIVE)
    return _textual(extension, data)


def _zipped(data: bytes, extension: str) -> Kind:
    """ZIP: таблица Excel и документ Word узнаются по частям внутри, а не по
    имени. Остальное — архив или формат, который не читаем (.pptx, .odt)."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
    # Широко, а не `BadZipFile`: битое оглавление чужого ZIP роняет `zipfile`
    # и `ValueError`, и `OSError`, и `EOFError` — а итог для человека один.
    except Exception as exc:
        logger.info("вложение: оглавление ZIP не читается", extra={"error": repr(exc)})
        raise UnreadableError(
            "файл повреждён: оглавление ZIP не читается — файл можно скачать"
        ) from exc
    if "xl/workbook.xml" in names:
        return Kind.XLSX
    if "word/document.xml" in names:
        return Kind.DOCX
    if extension in _ARCHIVE_NAMES or not extension:
        raise UnreadableError(ARCHIVE)
    raise UnreadableError(f"«{extension}» не читается — файл можно скачать")


def _old_office(extension: str) -> str:
    """OLE2: старый формат Office — или новый, запертый паролем: Excel и Word
    кладут зашифрованный файл в тот же контейнер, что у .xls и .doc."""
    if extension in (".xlsx", ".xlsm", ".docx", ".docm"):
        return PROTECTED
    return f"{_OLD_OFFICE.get(extension, 'старый формат Office')} не читается — файл можно скачать"


def _textual(extension: str, data: bytes) -> Kind:
    """Байты не назвали формат: текст. Чем он размечен, говорит сама разметка
    HTML — «price.xls», выгруженный сайтом, внутри часто страница с таблицей, —
    а иначе расширение."""
    head = data[:1024].lstrip(codecs.BOM_UTF8 + b" \t\r\n").lower()
    if head.startswith(_HTML_STARTS):
        return Kind.HTML
    kind = _TEXTUAL.get(extension)
    if kind is None:
        raise UnreadableError(_by_extension(extension))
    if b"\x00" in data[:4096] and not data.startswith(_WIDE_BOMS):
        raise UnreadableError(
            f"файл назван «{extension}», но внутри двоичные данные, а не текст — файл можно скачать"
        )
    return kind


def _by_extension(extension: str) -> str:
    """Почему не читается файл, у которого нет ни сигнатуры, ни текстового расширения."""
    if extension in _PROMISED:
        return (
            f"файл назван «{extension}», но внутри не {_PROMISED[extension]} — "
            "не читается, файл можно скачать"
        )
    if extension in _OLD_OFFICE:
        return f"{_OLD_OFFICE[extension]} не читается — файл можно скачать"
    if extension in _PICTURE_NAMES:
        return PICTURE
    if extension in _ARCHIVE_NAMES:
        return ARCHIVE
    if not extension:
        return "файл без расширения не читается — файл можно скачать"
    return f"«{extension}» не читается — файл можно скачать"


def read_text(
    name: str, content_type: str | None, data: bytes, *, timeout: float = TIMEOUT_S
) -> FileText:
    """Текст файла донора — или словами, почему его нет. Не бросает.

    `content_type` — тип, который назвал отправитель: он идёт только в журнал,
    вид файла решают байты и имя (`kind_of`).
    """
    where: dict[str, object] = {"attachment": name, "declared_type": content_type}
    try:
        kind = kind_of(name, data)
    except UnreadableError as exc:
        logger.info("вложение не читается", extra={**where, "why": str(exc)})
        return FileText(note=str(exc))
    return _in_child(kind, data, timeout, {**where, "kind": kind.value})


def _command(kind: Kind) -> list[str]:
    """Команда процесса чтения. `-B` — не писать байткод на диск, `-s` — без
    пакетов пользователя: процессу нужен ровно наш код и его зависимости.
    Байты файла в команду не попадают никогда — только на вход."""
    return [sys.executable, "-B", "-s", "-m", _READER, kind.value]


def _in_child(kind: Kind, data: bytes, timeout: float, where: dict[str, object]) -> FileText:
    """Прочитать файл отдельным процессом; не кончил за `timeout` — убить."""
    try:
        done = subprocess.run(  # noqa: S603 — команда своя: тот же питон и наш модуль
            _command(kind),
            input=data,
            capture_output=True,
            timeout=timeout,
            cwd=_PACKAGE_ROOT,
            env={},
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("вложение: чтение не уложилось во время, процесс убит", extra=where)
        return FileText(
            note=f"файл не прочитан за {timeout:g} с — так долго прайс не читается; "
            "файл можно скачать",
            failure="timeout",
        )
    except OSError as exc:
        logger.exception("вложение: процесс чтения не запустился", extra=where)
        return FileText(note=f"не удалось прочитать файл: {type(exc).__name__}", failure="start")
    return _answer(done, where)


def _answer(done: subprocess.CompletedProcess[bytes], where: dict[str, object]) -> FileText:
    """Итог процесса чтения. Оборвался — словами; что он успел сказать в stderr,
    уходит в журнал, а не на экран."""
    found = _parsed(done.stdout) if done.returncode == 0 else None
    if found is None:
        logger.warning(
            "вложение: процесс чтения оборвался",
            extra={
                **where,
                "code": done.returncode,
                "stderr": done.stderr[-2000:].decode("utf-8", "replace"),
            },
        )
        return FileText(
            note=f"не удалось прочитать файл: процесс чтения не дал ответа (код {done.returncode})",
            failure="crash",
        )
    if found.failure is not None:
        logger.warning(
            "вложение: разборщик не прочитал файл", extra={**where, "error": found.failure}
        )
    return found


def _parsed(stdout: bytes) -> FileText | None:
    """Ответ процесса чтения — JSON с полями `FileText`. Не он — `None`."""
    try:
        return FileText(**json.loads(stdout))
    except (ValueError, TypeError) as exc:
        logger.warning("вложение: ответ процесса чтения не разобран", extra={"error": repr(exc)})
        return None
