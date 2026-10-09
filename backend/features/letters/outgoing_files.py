"""Файл к нашему ответу: какой уходит с письмом, какой нет и почему.

Файл прикладывает наш человек, но написал его не обязательно он: прайс из чужой
почты, файл донора, пересылаемый дальше. Уходит же он с наших доменов, а их
репутация не восстанавливается (`docs/SECURITY.md`): заражённый файл в письме —
это жалобы и закрытый домен. Антивируса нет. Отсюда правило, обратное приёму
вложений (`replies/attachments.py`): там потерять прайс хуже, чем сохранить
сомнительный файл, здесь — наоборот.

**При сомнении файл не уходит** (fail-closed). Берутся только типы, которые
узнаются по содержимому: PDF, документ Word и таблица Excel (DOCX, XLSX), CSV
и текст в UTF-8, картинки PNG и JPEG. Расширение и содержимое обязаны сойтись:
«прайс.pdf», который на деле программа или архив, — отказ. Исполняемое, архивы,
HTML и SVG не узнаются ничем из списка — и не уходят ни под каким именем.

**Внутри разрешённого — тоже без исполняемого.** Документ с макросами, элементами
ActiveX или вложенными объектами и PDF со скриптами, запуском программ или
вложенными файлами не уходят: такое содержимое проверил бы антивирус, а нам
проверить его нечем. Это проверка от случайного, а не от умысла: имя, спрятанное
сжатием или кодированием, она не найдёт — от умысла нужен антивирус.

**Тип назначаем мы** — по расширению из белого списка. Тип, который назвал
браузер, пишет та же сторона, что и сам файл, и он не читается вовсе.

**Отказ — словами**: что не так и какой предел. Его читает человек у кнопки,
и «файл не принят» без причины звало бы прикладывать тот же файл ещё раз.
"""

from __future__ import annotations

import io
import logging
import re
import unicodedata
import zipfile
from collections.abc import Callable
from dataclasses import dataclass

from backend.features.replies.attachments import megabytes

logger = logging.getLogger(__name__)

_MB = 1024 * 1024

#: Один файл. Прайс, медиакит и договор укладываются с запасом; больше —
#: видео или выгрузка, и письмо с таким файлом рискует не дойти.
MAX_FILE_BYTES = 10 * _MB

#: Все файлы одного письма вместе. Платформа принимает письмо меньше 30 МБ
#: вместе с текстом, заголовками и кодированием, а base64 добавляет к файлу
#: треть: двадцать мегабайт файлов — около 27 в письме, остальное — запас
#: под текст (`sendgrid.MAX_MESSAGE_BYTES`, сверяет тест).
MAX_LETTER_BYTES = 20 * _MB

#: Файлов на письмо. Больше — не ответ собеседнику, а выгрузка.
MAX_FILES = 5

#: Длина имени файла в знаках, вместе с расширением.
MAX_NAME = 120

#: Имя вместо пустого: у «.pdf» расширение есть, а имени нет.
NAMELESS = "attachment"

#: Что не так с содержимым файла. `None` — сошлось с расширением.
type Verdict = str | None


class OutgoingFileError(ValueError):
    """Файл не уходит с письмом: тип, содержимое, размер, число или имя. Текст — словами."""


@dataclass(frozen=True, slots=True)
class Checked:
    """Файл, который уходит: очищенное имя, наш тип и размер в байтах."""

    name: str
    content_type: str
    size: int


@dataclass(frozen=True, slots=True)
class _Kind:
    """Разрешённый тип: что пишем в письмо, как называем человеку, чем узнаём."""

    content_type: str
    words: str
    #: Сверка содержимого с расширением: `(байты, как называем) → Verdict`.
    mismatch: Callable[[bytes, str], Verdict]


# --- имя ----------------------------------------------------------------------

#: Разделители пути: браузер присылает имя файла, но бывает — путь целиком
#: («C:\fakepath\прайс.pdf»), а путь в имени — путь на диске получателя.
_PATH = re.compile(r"[\\/]")
#: Знаки, которых не бывает в имени файла у Windows: получатель его не сохранил бы.
_FORBIDDEN = re.compile(r'[<>:"|?*]')
#: Точки и пробелы в конце имени: Windows их отрезает, и сверялось бы не то
#: расширение, под которым файл сохранит получатель.
_TRAILING = re.compile(r"[\s.]+$")
#: Невидимые знаки: управляющие, форматирующие — среди них разворот строки
#: (U+202E), которым «gpj.exe» выдают за картинку, — суррогаты, частные,
#: неназначенные и разрывы строк.
_INVISIBLE = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})


def clean_name(raw: str | None) -> str:
    """Имя файла в письме: последнее звено пути, без невидимых знаков и знаков,
    запрещённых в имени, не длиннее `MAX_NAME` — расширение при обрезке целое.

    Имени без расширения нет: тип файла узнаётся по нему, и угадывать его
    по содержимому значило бы взять файл, о типе которого никто не договаривался.
    """
    base = _PATH.split(unicodedata.normalize("NFC", raw or ""))[-1]
    seen = "".join(char for char in base if unicodedata.category(char) not in _INVISIBLE)
    plain = _TRAILING.sub("", _FORBIDDEN.sub("_", seen).strip())
    stem, dot, extension = plain.rpartition(".")
    if not dot or not extension:
        raise OutgoingFileError(
            f"«{plain or 'без имени'}»: у файла нет расширения — по нему узнаётся тип, "
            "и без него файл с письмом не уходит"
        )
    room = max(MAX_NAME - len(extension) - 1, 0)
    return f"{stem.strip()[:room].rstrip() or NAMELESS}.{extension}"


# --- содержимое -----------------------------------------------------------------


def _starts(data: bytes, prefix: bytes, words: str) -> Verdict:
    return None if data.startswith(prefix) else f"по содержимому это не {words}"


def _png(data: bytes, words: str) -> Verdict:
    return _starts(data, b"\x89PNG\r\n\x1a\n", words)


def _jpeg(data: bytes, words: str) -> Verdict:
    return _starts(data, b"\xff\xd8\xff", words)


#: Имена PDF, за которыми исполняемое: скрипты, запуск программ, вложенные файлы,
#: мультимедиа со скриптами. Имя в PDF чувствительно к регистру и кончается
#: разделителем: `/JSON` — не `/JS`.
_PDF_ACTIVE = re.compile(rb"/(?:JavaScript|JS|Launch|EmbeddedFile|RichMedia)(?![A-Za-z0-9])")


def _pdf(data: bytes, words: str) -> Verdict:
    if not data.startswith(b"%PDF-"):
        return f"по содержимому это не {words}"
    if _PDF_ACTIVE.search(data):
        return (
            "внутри скрипты, запуск программ или вложенные файлы — такой PDF не уходит: "
            "сохраните его заново обычным PDF («Печать в PDF»)"
        )
    return None


#: Части пакета Office, в которых живёт исполняемое: макросы (`vbaProject.bin`,
#: `vbaData.xml`), элементы ActiveX, вложенные объекты OLE — в них кладут
#: и программы. Сравнивается имя части в нижнем регистре.
_OFFICE_ACTIVE = ("vbaproject", "vbadata", "activex", "oleobject")


def _parts(data: bytes) -> list[str]:
    """Имена частей zip-пакета в нижнем регистре; не пакет — пусто.

    Пакет обязан начинаться с zip-записи: иначе это склейка — программа,
    к концу которой приписан архив, а zip ищет опись с конца и её бы принял.
    Читается только опись, без распаковки: пакет в десять мегабайт может
    развернуться в гигабайты, а решению хватает имён частей.
    """
    if not data.startswith(b"PK\x03\x04"):
        return []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            return [info.filename.lower() for info in package.infolist()]
    except (zipfile.BadZipFile, NotImplementedError, ValueError, OSError, EOFError) as exc:
        logger.info("файлы письма: пакет Office не читается — %s", exc)
        return []


def _office(data: bytes, part: str, words: str) -> Verdict:
    """Документ Office — zip-пакет с описью типов и своей частью: `word/` у DOCX, `xl/` у XLSX."""
    parts = _parts(data)
    if "[content_types].xml" not in parts or not any(name.startswith(part) for name in parts):
        return f"по содержимому это не {words}"
    if any(marker in name for name in parts for marker in _OFFICE_ACTIVE):
        return f"{words} с макросами или встроенными объектами не уходит: сохраните его без них"
    return None


def _docx(data: bytes, words: str) -> Verdict:
    return _office(data, "word/", words)


def _xlsx(data: bytes, words: str) -> Verdict:
    return _office(data, "xl/", words)


#: Начало разметки: страница, картинка SVG, XML. Текстом с таким началом
#: прикидывается HTML или SVG, а они не уходят ни под каким именем.
_MARKUP = re.compile(rb"\s*<(?:!doctype|html|head|body|svg|\?xml|script|iframe)", re.IGNORECASE)


def _text(data: bytes, words: str) -> Verdict:
    if b"\x00" in data:
        return f"внутри нулевые байты — по содержимому это не {words}"
    if _MARKUP.match(data.removeprefix(b"\xef\xbb\xbf")):
        return "внутри разметка HTML или SVG — такие файлы с письмом не уходят"
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        logger.info("файлы письма: текст не в UTF-8 — %s", exc)
        return f"{words} не в кодировке UTF-8 — сохраните файл в UTF-8"
    return None


_KINDS: dict[str, _Kind] = {
    "pdf": _Kind("application/pdf", "PDF", _pdf),
    "docx": _Kind(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "документ Word",
        _docx,
    ),
    "xlsx": _Kind(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "таблица Excel",
        _xlsx,
    ),
    "csv": _Kind("text/csv", "таблица CSV", _text),
    "txt": _Kind("text/plain", "текст", _text),
    "png": _Kind("image/png", "картинка PNG", _png),
    "jpg": _Kind("image/jpeg", "картинка JPEG", _jpeg),
    "jpeg": _Kind("image/jpeg", "картинка JPEG", _jpeg),
}

#: Что можно приложить — словами для отказа.
ALLOWED = "PDF, DOCX, XLSX, CSV, TXT, PNG, JPG (JPEG)"


# --- решение ------------------------------------------------------------------


def check(raw_name: str | None, data: bytes) -> Checked:
    """Уходит ли файл с письмом. Да — имя, наш тип и размер; нет — `OutgoingFileError`.

    `data` читается не дальше предела и байтом сверх него (`MAX_FILE_BYTES + 1`):
    больший файл узнаётся по этому байту, а не по всему телу в памяти.
    """
    name = clean_name(raw_name)
    kind = _KINDS.get(name.rpartition(".")[2].lower())
    if kind is None:
        raise OutgoingFileError(f"«{name}»: такие файлы с письмом не уходят — можно {ALLOWED}")
    if not data:
        raise OutgoingFileError(f"«{name}» пустой — прикладывать нечего")
    if len(data) > MAX_FILE_BYTES:
        raise OutgoingFileError(
            f"«{name}» больше предела {megabytes(MAX_FILE_BYTES)} на файл — уменьшите его "
            "или пришлите ссылкой в тексте"
        )
    why = kind.mismatch(data, kind.words)
    if why is not None:
        raise OutgoingFileError(f"«{name}»: {why}")
    return Checked(name=name, content_type=kind.content_type, size=len(data))


def check_letter(count: int, total: int) -> None:
    """Файлы одного письма вместе: не больше `MAX_FILES` и `MAX_LETTER_BYTES`."""
    if count > MAX_FILES:
        raise OutgoingFileError(
            f"К письму — не больше {MAX_FILES} файлов, а приложено {count}: уберите лишние"
        )
    if total > MAX_LETTER_BYTES:
        raise OutgoingFileError(
            f"Файлы письма вместе — {megabytes(total)}, больше предела "
            f"{megabytes(MAX_LETTER_BYTES)} на письмо: уберите лишние"
        )
