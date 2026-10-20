"""Примеры сайтов от человека: строка поля → сайт, главную которого откроем.

**Имя сайта — как написано, без `www.`, пути и порта.** Не корневой домен по списку
суффиксов (`donors/host.normalize_host`): пример `blog.example.com` — это блог, а не
главная компании на `example.com`, и тема берётся с того сайта, на который показали.
`www.` — не другой сайт, а другой вид адреса той же главной: его пробует сам обход
главной (`contacts/pages.home_variants`).

**Негодная строка — отказ до сети, с номером строки и словами.** Молча пропущенная
строка превращает опечатку в «пример не сработал», а мусор в адресе — в поход
по чужой сети. Повтор — не отказ: тот же сайт второй раз не открываем, и это
сказано числом.

**Имя хранится в ASCII** (IDNA, `xn--…`) — так его проверяет защита исходящих и так
оно уходит в запрос; человеку показывается письменностью сайта (`shown`).
"""

from __future__ import annotations

import ipaddress
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import SplitResult

import idna

from backend.shared.net.url_guard import BLOCKED_HOSTS
from backend.shared.net.url_parts import split_url

logger = logging.getLogger(__name__)

#: Больше примеров тему не уточняют, а главные открываются по очереди и вызов
#: о теме растёт с каждым: десять — потолок и на экране, и здесь.
MAX_EXAMPLES = 10

#: Предел длины имени по DNS: длиннее — не имя сайта.
MAX_HOST = 253

#: Метка имени в ASCII: буквы, цифры, дефис не по краям, до 63 знаков (RFC 1035).
_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
#: Зона: буквы или IDNA (`xn--p1ai`). Цифрами зона не пишется — так выглядит IP.
_ZONE = re.compile(r"^(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})$")
#: Зоны, которых нет в интернете: туда защита исходящих и так не пустит, но сказать
#: это человеку надо до сети и словами.
_PRIVATE_ZONES = ("localhost", "local", "internal", "lan", "home", "corp", "intranet")
#: Адрес, который не разбирается: битые скобки, знаки, которых в имени не бывает.
_UNPARSED = "адрес не разбирается — проверьте скобки и знаки в нём"


@dataclass(frozen=True, slots=True)
class Example:
    """Один пример: строка поля, как её вписал человек, и имя сайта."""

    line: int  # номер строки поля с единицы; пустые строки — тоже строки
    host: str  # имя сайта в ASCII, без `www.`

    @property
    def shown(self) -> str:
        """Имя письменностью сайта: `xn--e1afmkfd.xn--p1ai` человеку — `пример.рф`."""
        return shown_host(self.host)


@dataclass(frozen=True, slots=True)
class Examples:
    """Примеры поля: сайты по порядку строк и сколько строк повторили уже взятый."""

    sites: list[Example]
    repeats: int = 0


class ExamplesError(ValueError):
    """Строки поля не годятся. Текст называет каждую и что с ней не так."""


def shown_host(host: str) -> str:
    """ASCII-имя письменностью сайта. Не раскодировалось — как есть: показать
    `xn--…` лучше, чем не показать ничего."""
    try:
        return str(idna.decode(host))
    except idna.IDNAError as exc:
        logger.debug("примеры: имя %s не раскодировано из IDNA (%s)", host, exc)
        return host


def _ascii(name: str) -> str:
    """Имя в ASCII по IDNA 2008 с поправками UTS 46 — как его кодирует httpx."""
    try:
        return str(idna.encode(name, uts46=True).decode("ascii"))
    except (idna.IDNAError, UnicodeError) as exc:
        raise ValueError("в имени сайта недопустимые знаки") from exc


def _is_ip(name: str) -> bool:
    try:
        ipaddress.ip_address(name.strip("[]"))
    except ValueError:
        logger.debug("примеры: %r не адрес IP — проверяем как имя", name)
        return False
    return True


def _parts(text: str) -> SplitResult:
    """Строка как адрес: один, разбирается, схема — http или https (или её нет)."""
    if any(char.isspace() for char in text):
        raise ValueError("пробел внутри — в строке должен быть один адрес")
    parts = split_url(text if "://" in text else f"//{text}")
    if parts is None:
        raise ValueError(_UNPARSED)
    if parts.scheme and parts.scheme.lower() not in ("http", "https"):
        raise ValueError(f"схема «{parts.scheme}» — открываем только http и https")
    return parts


def _name_of(text: str) -> str:
    """Имя хоста из строки — или `ValueError` словами, почему его нет."""
    parts = _parts(text)
    try:
        name = (parts.hostname or "").rstrip(".")
    except ValueError as exc:
        raise ValueError(_UNPARSED) from exc
    if not name:
        raise ValueError("нет имени сайта — в строке только схема или путь")
    if _is_ip(name):
        raise ValueError("это адрес IP, а не имя сайта")
    return name


def site_host(text: str) -> str:
    """Имя сайта из строки поля: `HTTPS://WWW.Garden.example/blog?x=1` → `garden.example`.

    `ValueError` — строка не адрес сайта; текст говорит, что с ней не так.
    """
    host = _ascii(_name_of(text.strip()))
    host = host.removeprefix("www.")
    labels = host.split(".")
    if len(labels) < 2:
        raise ValueError("нет зоны — имя сайта пишется с точкой, как example.com")
    if len(host) > MAX_HOST or not all(_LABEL.match(label) for label in labels):
        raise ValueError("в имени сайта недопустимые знаки")
    if not _ZONE.match(labels[-1]):
        raise ValueError(f"зона «{labels[-1]}» — не зона сайта в интернете")
    if host in BLOCKED_HOSTS or labels[-1] in _PRIVATE_ZONES:
        raise ValueError("служебное имя — это не сайт в интернете")
    return host


def read_examples(lines: Sequence[str]) -> Examples:
    """Строки поля → примеры. `ExamplesError` — какая строка не годится и почему,
    или примеров нет вовсе, или их больше потолка.

    Пустые строки пропускаются, но номер сохраняют: человек ищет «строку 4» глазами
    в своём поле, а не в списке без пустых.
    """
    sites: list[Example] = []
    taken: set[str] = set()
    repeats = 0
    problems: list[str] = []
    for number, raw in enumerate(lines, 1):
        text = raw.strip()
        if not text:
            continue
        try:
            host = site_host(text)
        except ValueError as exc:
            logger.info("примеры: строка %d — не адрес сайта (%s)", number, exc)
            problems.append(f"строка {number}: «{text[:80]}» — {exc}")
            continue
        if host in taken:
            repeats += 1
            continue
        taken.add(host)
        sites.append(Example(line=number, host=host))
    if problems:
        raise ExamplesError("Не адрес сайта — " + "; ".join(problems))
    if not sites:
        raise ExamplesError("Примеров нет — впишите адрес или домен сайта, по одному в строке")
    if len(sites) > MAX_EXAMPLES:
        raise ExamplesError(
            f"Примеров {len(sites)} — берём до {MAX_EXAMPLES}: больше тему не уточняют, "
            f"а ждать дольше. Оставьте {MAX_EXAMPLES} самых похожих на то, что ищете"
        )
    return Examples(sites=sites, repeats=repeats)
