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
"""

from __future__ import annotations

import logging
from urllib.parse import ParseResult, SplitResult, urljoin, urlparse, urlsplit

logger = logging.getLogger(__name__)


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
