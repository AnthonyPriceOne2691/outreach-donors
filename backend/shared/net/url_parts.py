"""Разбор адреса из внешнего мира без исключений.

`urlsplit`, `urlparse` и `urljoin` бросают `ValueError` на адресах, которые
встречаются в живом HTML, редиректах и выдаче:

- «[» или «]» без пары в имени хоста (`http://[broken`, `https://exa[mple.com/`)
  читается как начало адреса IPv6 — «Invalid IPv6 URL»;
- полноширинная косая черта или решётка (`example.com／x`) после нормализации
  NFKC меняет имя хоста — «contains invalid characters under NFKC normalization».

Одна такая ссылка роняла разбор всей страницы: сбор ссылок рекламодателей,
очередь обхода, поиск раздела контактов, разбор выдачи. Битый адрес — мусор
на входе, а не поломка: здесь он становится `None`, и вызывающий пропускает
его так же, как ссылку на чужой сайт.

**Невидимый знак в адресе — не повод терять обход** (`clean_url`, 23.11.2026). Стандартная
библиотека такой адрес разбирает, а клиент (httpx) отказывается его открыть:
«Invalid non-printable ASCII character in URL». Одна такая ссылка со страницы донора роняла
задачу обхода, и продолжения падали на ней же, пока обход не закрывали мёртвым. Браузер
читает её по стандарту WHATWG URL — и мы читаем так же; чего не прочтёт и он, — `None`.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import ParseResult, SplitResult, urljoin, urlparse, urlsplit

import httpx

logger = logging.getLogger(__name__)

#: Управляющие знаки ASCII: адрес хоть с одним из них клиент не открывает вовсе.
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
#: Табуляция и перевод строки: браузер выбрасывает их из адреса, где бы они ни стояли.
_TAB_OR_NEWLINE = re.compile(r"[\t\n\r]")
#: Что браузер срезает с краёв ссылки: управляющие знаки C0 и пробел.
_EDGES = "".join(map(chr, range(0x21)))


def split_url(url: str) -> SplitResult | None:
    """`urlsplit`, но битый адрес — `None`, а не исключение."""
    try:
        return urlsplit(url)
    except ValueError as exc:
        logger.debug("адрес не разбирается: %r (%s)", url[:200], exc)
        return None


def parse_url(url: str) -> ParseResult | None:
    """`urlparse`, но битый адрес — `None`, а не исключение."""
    try:
        return urlparse(url)
    except ValueError as exc:
        logger.debug("адрес не разбирается: %r (%s)", url[:200], exc)
        return None


def join_url(base: str, href: str) -> str | None:
    """`urljoin`, но битая ссылка — `None`, а не исключение."""
    try:
        return urljoin(base, href)
    except ValueError as exc:
        logger.debug("ссылка %r со страницы %s не разбирается (%s)", href[:200], base, exc)
        return None


def clean_url(url: str) -> str | None:
    """Адрес так, как его прочтёт браузер (стандарт WHATWG URL), — или `None`: не открыть и так.

    С краёв срезаются управляющие знаки и пробелы, табуляция и перевод строки выбрасываются
    отовсюду, прочие управляющие знаки в пути, запросе и якоре кодируются процентом (знак
    с кодом 2 — `%02`): ровно это делает браузер, переходя по такой ссылке. Управляющий знак
    в имени хоста он считает ошибкой — здесь это `None`, как и всё, что не разбирает клиент
    (`httpx.InvalidURL`: невидимый знак в имени, порт не числом). Обычный адрес — как был.
    """
    cleaned = _TAB_OR_NEWLINE.sub("", url.strip(_EDGES))
    parts = split_url(cleaned)
    if parts is None:
        return None
    if _CONTROL.search(parts.netloc):
        logger.debug("адрес не открыть: управляющий знак в имени хоста — %r", url[:200])
        return None
    cleaned = _CONTROL.sub(lambda found: f"%{ord(found.group()):02X}", cleaned)
    try:
        httpx.URL(cleaned)
    except httpx.InvalidURL as exc:
        logger.debug("адрес не открыть: %r (%s)", url[:200], exc)
        return None
    return cleaned
