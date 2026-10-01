"""Список доменов для прогона по файлу: как его прочитать.

Файл приходит откуда угодно: выгрузка доноров самого сервиса, чужой экспорт,
список «по домену на строку», таблица, сохранённая из Excel с табуляцией.
Поэтому чтение терпимо к форме и нетерпимо к молчанию.

**Кривая ячейка стоит строки, а не файла.** Строка, из которой домена
не вышло, считается и называется — номер и ячейка как есть, — а остальное
читается. До 30.09.2026 одна ячейка с `[` роняла чтение целиком чужим
текстом «Invalid IPv6 URL», без номера строки, а ячейка, где `//` стоял
в параметре ссылки, пропадала без следа.

**Заголовка может не быть.** Если в первой строке уже стоит домен, это
данные. Принять его за имя колонки значило бы потерять первый домен
списка молча — так и выходило с подсказкой прежней ошибки
`--column first-site.com`.
"""

from __future__ import annotations

import codecs
import csv
import io
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

#: Имена колонок, в которых может лежать домен. Порядок — порядок доверия.
#: «домен» — заголовок выгрузки доноров самого сервиса (`donors/export.py`):
#: без него собственный файл сервиса объявлялся файлом без домена.
HOST_COLUMNS = (
    "host_key", "host", "domain", "site", "site_url", "url", "website", "домен", "сайт",
)  # fmt: skip

#: Из чего угадывается разделитель. Порядок решает ничью: запятая раньше
#: точки с запятой — так было до табуляции, и прежние файлы читаются как прежде.
DELIMITERS = (",", ";", "\t", "|")

#: Разделитель, названный словами. Оболочка передаёт `--delimiter '\t'`
#: двумя знаками, и `csv` отвечал на это трассировкой TypeError.
_DELIMITER_WORDS = {"\\t": "\t", "tab": "\t", "таб": "\t", "табуляция": "\t"}

#: Схема в НАЧАЛЕ ячейки. Прежняя проверка «есть ли `//` где-нибудь»
#: принимала `site.com/go?url=https://…` за адрес со схемой, и хост
#: не находился вовсе.
_SCHEME = re.compile(r"^[a-z][a-z0-9+.\-]*://")


@dataclass(frozen=True, slots=True)
class DomainList:
    """Что вышло из файла: домены и то, что доменом не стало."""

    hosts: list[str]
    #: Непустые ячейки, где домена не нашлось: номер строки и ячейка как есть.
    unreadable: list[tuple[int, str]] = field(default_factory=list)
    #: Строки, где ячейка домена пуста, а другие заполнены.
    empty: int = 0


def host_from_cell(raw: str) -> str:
    """`https://WWW.News.example.com/path` → `news.example.com`. Не домен — пустая строка.

    Поддомен НЕ срезается, в отличие от `donors.host.normalize_host`:
    тот сводит домен к ключу дедупликации, а нам нужен адрес, по которому
    идти. `news.example.com` и `example.com` — разные сайты, и обойти надо
    тот, что дали на входе.

    Всё после первого пробела — пометка, а не адрес: `site.com [old]`.
    Исключений функция не бросает: отказ одной ячейки называет тот, кто
    знает номер её строки.
    """
    words = (raw or "").strip().lower().split()
    if not words:
        return ""
    candidate = words[0]
    if not _SCHEME.match(candidate):
        candidate = "//" + candidate.lstrip("/")
    try:
        host = urlsplit(candidate).hostname or ""
    except ValueError as exc:
        # `site.com[old]`: скобки в имени `urlsplit` читает как адрес IPv6.
        logger.debug("ячейка %r не разбирается как адрес: %s", raw, exc)
        return ""
    host = host.removeprefix("www.").rstrip(".")
    return host if _is_domain(host) else ""


def _is_domain(host: str) -> bool:
    """Похоже ли на имя сайта: две метки и больше, буквы, цифры и дефис.

    Зона из одних цифр — это адрес IP или обрывок числа. Без проверки
    `n/a` становился доменом `n`, и прогон честно ходил по нему.
    """
    labels = host.split(".")
    if len(labels) < 2 or labels[-1].isdigit():
        return False
    return all(
        0 < len(label) <= 63
        and not label.startswith("-")
        and not label.endswith("-")
        and all(char.isalnum() or char == "-" for char in label)
        for label in labels
    )


def _delimiter(first_line: str, given: str | None) -> str:
    """Разделитель: названный руками или угаданный по первой строке."""
    if not given:
        counts = {sep: first_line.count(sep) for sep in DELIMITERS}
        best = max(DELIMITERS, key=counts.__getitem__)
        # Ни одного разделителя — файл из одной колонки, подходит любой.
        return best if counts[best] else ","
    sep = _DELIMITER_WORDS.get(given.lower(), given)
    if len(sep) != 1:
        raise ValueError(
            f"разделитель — один знак, а дано {given!r}. Табуляцию можно передать как '\\t'"
        )
    return sep


def _locate(head: Sequence[str], wanted: str | None) -> tuple[int, bool]:
    """Где домен и заголовок ли первая строка.

    Первая строка — заголовок, если в ней есть известное имя колонки или
    имя, названное руками. Если же в ней уже стоит домен, это данные.
    """
    names = [cell.strip().lower() for cell in head]
    if wanted:
        key = wanted.strip().lower()
        if key not in names:
            raise ValueError(f"в файле нет колонки {wanted!r}; есть: {', '.join(head)}")
        index = names.index(key)
        # Имя колонки, которое само домен, — первый домен списка, а не заголовок.
        return index, not host_from_cell(head[index])
    for name in HOST_COLUMNS:
        if name in names:
            return names.index(name), True
    for index, cell in enumerate(head):
        if host_from_cell(cell):
            return index, False
    raise ValueError(
        "не нашлось колонки с доменом. Ожидаются "
        f"{', '.join(HOST_COLUMNS)} — или назовите её сами: --column <имя>. "
        f"В файле: {', '.join(head)}"
    )


def read_records(path: Path, *, delimiter: str | None = None) -> list[tuple[int, list[str]]]:
    """Непустые записи таблицы с номерами строк файла — начало любого чтения.

    Одно на всех читателей таблиц, пришедших из рук человека: кодировка,
    разделитель и номера строк проверяются в одном месте, а не в копиях.

    - Не UTF-8 — `ValueError` со словами, что делать: Excel на русской
      Windows пишет «CSV» в cp1251, и голое «'utf-8' codec can't decode
      byte» этого не говорило. Место — строкой файла: смещение из ошибки
      чтения по кускам считалось от начала куска, и в файле больше 8 КБ
      «байт 5200» стоял на деле в двухсотой тысяче.
    - BOM Excel снимается; разделитель угадывается по первой строке.
    - Номер — строки файла, где запись начинается, по счёту самого
      разборщика: ячейка в кавычках бывает многострочной, и счёт записей
      разошёлся бы со строками файла.
    """
    raw = path.read_bytes().removeprefix(codecs.BOM_UTF8)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        line = raw.count(b"\n", 0, exc.start) + 1
        raise ValueError(
            f"файл {path.name} не в UTF-8 (строка {line}). Сохраните его как "
            "«CSV UTF-8»: в Excel — «Сохранить как» → «CSV UTF-8 (разделитель — запятая)»"
        ) from exc
    handle = io.StringIO(text, newline="")
    first = handle.readline()
    handle.seek(0)
    reader = csv.reader(handle, delimiter=_delimiter(first, delimiter))
    records: list[tuple[int, list[str]]] = []
    ended = 0  # строка, на которой кончилась предыдущая запись
    for row in reader:
        if any(cell.strip() for cell in row):
            records.append((ended + 1, row))
        ended = reader.line_num
    return records


def read_list(path: Path, *, column: str | None = None, delimiter: str | None = None) -> DomainList:
    """Домены из файла без повторов, в порядке файла, и всё, что доменом не стало.

    Бросает `ValueError`, только если не прочитать ничего: файл не в UTF-8,
    нет колонки с доменом или разделитель невозможен. Кривая строка — не повод.
    """
    records = read_records(path, delimiter=delimiter)
    if not records:
        return DomainList(hosts=[])

    index, has_header = _locate(records[0][1], column)
    seen: dict[str, None] = {}
    unreadable: list[tuple[int, str]] = []
    empty = 0
    for line, row in records[1:] if has_header else records:
        cell = row[index].strip() if index < len(row) else ""
        if not cell:
            empty += 1
        elif host := host_from_cell(cell):
            seen.setdefault(host, None)
        else:
            unreadable.append((line, cell))
    return DomainList(hosts=list(seen), unreadable=unreadable, empty=empty)


def read_hosts(path: Path, *, column: str | None = None, delimiter: str | None = None) -> list[str]:
    """Только домены. Строки, из которых домена не вышло, уходят в лог с номером."""
    listed = read_list(path, column=column, delimiter=delimiter)
    for line, cell in listed.unreadable:
        logger.warning(
            "список %s: в строке %s домена нет (%r) — строка пропущена", path, line, cell
        )
    return listed.hosts
