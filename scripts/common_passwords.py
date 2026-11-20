"""Список частых паролей для отказа при смене пароля — сборка из открытых списков.

Аудит 20.11.2026: смена пароля пропускала любой пароль нужной длины, в том числе тот, с
которого начинается любой подбор. Отказ сверяет новый пароль со списком
`backend/features/access/common_passwords.txt` (`access/passwords.assert_strong_enough`),
а этот скрипт список собирает. Тот же ввод — тот же файл байт в байт; хеши ввода — в шапке.

**Источник** — SecLists (лицензия MIT), два списка от частого к редкому: десять миллионов пар,
опубликованных Марком Бёрнеттом (`xato-net-…`), и сборник утечек Pwdb (`Pwdb_top-…`). Снимок
репозитория закреплён коммитом (`SOURCES`). Скачать оба и подать путями:

    .venv/bin/python -I scripts/common_passwords.py xato-net-10-million-passwords-1000000.txt \\
        Pwdb_top-1000000.txt backend/features/access/common_passwords.txt

**Что берём.** Запись, приведённую как при проверке (NFC, без регистра), длиной от `MIN_LENGTH`
знаков: короче отказывает длина раньше списка, а частые пароли почти все короче — среди
10 000 самых частых длиннее пятнадцати знаков один. Из длинных — те, что в первой сотне тысяч
любого списка (`HEAD`), или те, что есть в первом миллионе обоих (`DEPTH`): глубже одно
присутствие — чаще привычка одного человека, чем частый пароль.

**Чего не берём** — того, что в публичный репозиторий не кладут и чего никто другой не выберет:
похожее на почту, адрес сайта или хеш; ряд из шести цифр и больше, не сложенный из узоров
(`STEPS`, `DIGIT_PATTERNS`, `patterned`), — номер телефона или дата рождения одного человека;
не печатное ASCII и одни знаки вопроса — битая кодировка; запись с «#» впереди — так в файле
начинается шапка.

Только стандартная библиотека и ни одного соседа: скрипт идёт голым `python -I`, а совпадение
минимума и приведения с проверкой держит тест списка (`tests/test_access_core.py`).
"""

from __future__ import annotations

import hashlib
import re
import sys
import unicodedata
from pathlib import Path

#: Минимум длины — тот же, что у отказа (`access/passwords.MIN_PASSWORD_LENGTH`).
MIN_LENGTH = 15
#: Голова списка: здесь запись частая уже потому, что она здесь.
HEAD = 100_000
#: Глубина, на которой нужна вторая утечка: запись есть в обоих списках.
DEPTH = 1_000_000

COMMIT = "f90961c9c5be42d80e2f1b6e27b968530918862d"  # pragma: allowlist secret
_RAW = "https://raw.githubusercontent.com/danielmiessler/SecLists"
SOURCES = (
    f"{_RAW}/{COMMIT}/Passwords/Common-Credentials/xato-net-10-million-passwords-1000000.txt",
    f"{_RAW}/{COMMIT}/Passwords/Common-Credentials/Pwdb_top-1000000.txt",
)

#: Подряд вверх и вниз по кругу — узор уже с трёх цифр: «123», «321», «890».
STEPS = ("0123456789" * 3, "9876543210" * 3)

#: Узоры реже — с четырёх цифр: столбцы и строки цифровой клавиатуры, счёт до двадцати (и с
#: нулём впереди), десятками и чётными, число пи. С трёх случайный номер складывался бы из
#: них по кускам: «891» из счёта, «6123» с клавиатуры. Счёт — только двузначными: в
#: «…120130…» нашёлся бы кусок любой даты.
DIGIT_PATTERNS = (
    "147258369" * 2,
    "963852741" * 2,
    "789456123" * 2,
    "321654987" * 2,
    "741852963" * 2,
    "369258147" * 2,
    "159357" * 3,
    "753951" * 3,
    "".join(str(n) for n in range(1, 21)),
    "".join(str(n) for n in range(20, -1, -1)),
    "".join(f"{n:02d}" for n in range(21)),
    "".join(f"{n:02d}" for n in range(20, -1, -1)),
    "".join(str(n) for n in range(10, 101, 10)),
    "".join(str(n) for n in range(2, 21, 2)),
    "3141592653589793238462643383279",
)

_SITE = re.compile(r"://|www\.|/|^htt|\.[a-z]{2,}")
_HEX = re.compile(r"\*?[0-9a-f]+")
_LONG_DIGITS = re.compile(r"\d{6,}")
_REPEATS = re.compile(r"(\d)\1+")

#: Лицензия SecLists — дословно (sha256 файла LICENSE того же коммита начинается с 3dbdc93d):
#: MIT требует нести её с каждой существенной частью.
LICENSE = """\
MIT License

Copyright (c) 2018 Daniel Miessler

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def fold(text: str) -> str:
    """Как запись сверяется: без регистра и в NFC — так же приводит пароль проверка."""
    return unicodedata.normalize("NFC", text.casefold())


def patterned(run: str) -> bool:
    """Ряд цифр, набранный по узору: складывается из кусков, каждый — одна цифра повтором или
    цифры подряд (с трёх), отрезок узора реже или две цифры через одну (с четырёх). Сдвоенные
    цифры («11223344») сжимаются до одной."""
    return _covered(run) or _covered(_REPEATS.sub(r"\1", run))


def _covered(run: str) -> bool:
    reach = [True] + [False] * len(run)
    for start in range(len(run)):
        if reach[start]:
            for end in range(start + 3, len(run) + 1):
                reach[end] = reach[end] or _piece(run[start:end])
    return reach[-1]


def _piece(piece: str) -> bool:
    if len(set(piece)) == 1 or any(piece in steps for steps in STEPS):
        return True
    alternating = piece == (piece[:2] * len(piece))[: len(piece)]
    longer = len(piece) > 3
    return longer and (alternating or any(piece in pattern for pattern in DIGIT_PATTERNS))


def _hash_like(entry: str) -> bool:
    """Шестнадцатеричная запись — цифры вперемешку хотя бы с двумя разными буквами a–f, со
    звёздочкой впереди или без, — или запись hashcat `$hex[…]`: это хеш, а не пароль."""
    if entry.startswith("$hex["):
        return True
    letters = {char for char in entry if char in "abcdef"}
    digits = {char for char in entry if char.isdigit()}
    return bool(_HEX.fullmatch(entry)) and len(letters) > 1 and len(digits) > 1


def rejected(entry: str) -> bool:
    """Запись, которую не берём, — см. «Чего не берём»."""
    return _broken(entry) or _personal(entry)


def _broken(entry: str) -> bool:
    """Битая кодировка (не печатное ASCII, одни знаки вопроса), пробел с краю, решётка шапки."""
    printable = entry.isascii() and entry.isprintable()
    edged = entry != entry.strip() or entry.startswith("#")
    return not printable or edged or set(entry) == {"?"}


def _personal(entry: str) -> bool:
    """Почта, адрес сайта, хеш — или ряд цифр не по узору: номер телефона, дата рождения."""
    if "@" in entry or _SITE.search(entry) or _hash_like(entry):
        return True
    return not all(patterned(run) for run in _LONG_DIGITS.findall(entry))


def ranked(path: Path) -> dict[str, int]:
    """Запись не короче минимума → её первое место среди первых `DEPTH` строк списка."""
    places: dict[str, int] = {}
    with path.open(encoding="utf-8") as lines:
        for place, line in enumerate(lines):
            if place >= DEPTH:
                break
            entry = fold(line.rstrip("\n"))
            if len(entry) >= MIN_LENGTH:
                places.setdefault(entry, place)
    return places


def chosen(first: dict[str, int], second: dict[str, int]) -> list[str]:
    """Голова любого списка или есть в обоих — без отброшенного, по алфавиту."""

    def common(entry: str) -> bool:
        head = min(first.get(entry, DEPTH), second.get(entry, DEPTH)) < HEAD
        return head or (entry in first and entry in second)

    return sorted(entry for entry in first.keys() | second.keys() if common(entry))


def header(sources: list[Path], count: int) -> str:
    """Шапка файла: что это, откуда, хеши ввода и лицензия источника."""
    lines = [
        f"Частые и утёкшие пароли от {MIN_LENGTH} знаков — отказ при смене пароля",
        "(access/passwords.assert_strong_enough, аудит 20.11.2026). Запись на строку, NFC,",
        "без регистра; строки с решёткой впереди — шапка.",
        "",
        f"Собрано scripts/common_passwords.py из SecLists, коммит {COMMIT}:",
    ]
    for url, path in zip(SOURCES, sources, strict=True):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines += [f"  {url}", f"    sha256 {digest}"]
    lines += [f"Что взято и что выброшено — в описании скрипта. Записей: {count}.", ""]
    lines += ["SecLists распространяется по лицензии MIT:", "", *LICENSE.splitlines()]
    return "".join(f"# {line}".rstrip() + "\n" for line in lines)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    xato, pwdb, out = (Path(arg) for arg in argv)
    entries = [entry for entry in chosen(ranked(xato), ranked(pwdb)) if not rejected(entry)]
    text = header([xato, pwdb], len(entries)) + "".join(f"{entry}\n" for entry in entries)
    out.write_text(text, encoding="utf-8", newline="\n")
    print(f"записей: {len(entries)}, байт: {len(text.encode('utf-8'))} → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
