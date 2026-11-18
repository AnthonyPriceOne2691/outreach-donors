"""Имя сайта для людей: домен IDN — письменностью сайта, а не `xn--…`.

База хранит имена хостов в ASCII (IDNA: «пример.рф» — `xn--e1afmkfd.xn--p1ai`): так их кодирует
httpx, так их сравнивают защита исходящих, стоп-лист и поиск адресов. Человеку — в тексте письма,
на экране, в отчёте — `xn--…` ничего не говорит. 18.11.2026 на проде у продаж все 72 письма 25
лидам с кириллическим доменом ушли с `xn--` в теме или тексте: `{{site}}` подставлял имя из базы.

Раскодируется только имя, у которого есть метка `xn--`: прочие — как есть, без смены регистра.
Не раскодировалось (битая метка, имя вне IDNA 2008) — как есть: показать `xn--…` лучше, чем не
показать ничего. Ссылки и адреса, по которым шлют, — ASCII всегда: они не для людей, а для почты и
браузера (`compose._substitute` раскодирует подстановку только в тексте).
"""

from __future__ import annotations

import logging

import idna

logger = logging.getLogger(__name__)

#: Префикс метки IDNA (ACE): `xn--` и дальше punycode.
ACE_PREFIX = "xn--"


def has_ace(host: str) -> bool:
    """Есть ли в имени метка IDNA (`xn--…`), без учёта регистра."""
    return any(label.lower().startswith(ACE_PREFIX) for label in host.split("."))


def shown_host(host: str) -> str:
    """Имя хоста письменностью сайта: `xn--e1afmkfd.xn--p1ai` → «пример.рф». Без метки `xn--` или
    не раскодировалось — как есть."""
    if not has_ace(host):
        return host
    try:
        return str(idna.decode(host))
    except (idna.IDNAError, UnicodeError) as exc:
        logger.debug("имя %r не раскодировано из IDNA (%s)", host[:200], exc)
        return host


def shown_address(address: str) -> str:
    """Адрес почты для людей: домен после последней «@» — `shown_host`, имя ящика — как есть."""
    local, at, domain = address.rpartition("@")
    if not at:
        return address
    return f"{local}@{shown_host(domain)}"


def ascii_host(host: str) -> str:
    """Имя хоста в ASCII, как его хранит база: «пример.рф» → `xn--e1afmkfd.xn--p1ai`.

    Обратное к `shown_host` — для ключа домена: одно имя сайта — одна запись, в какой бы письменности его ни
    дали. Кодируется каждая метка не в ASCII — IDNA 2008 по UTS 46 без переходной схемы, как у httpx и
    браузера (встроенный `encodings.idna` — IDNA 2003: «ß» → «ss», и ключ вышел бы другим). Имя в ASCII — как
    есть, байт в байт, и регистр тоже: подчёркивание в поддомене IDNA бы не пропустила. Не кодируется
    (пробел, пустая метка — «..» или точка в конце, знак вне IDNA, метка длиннее 63 знаков) — как есть, без
    исключения: решать, мусор ли это, — вызывающему.
    """
    if host.isascii():
        return host
    labels = host.split(".")
    encoded = None if "" in labels else _ascii_labels(labels)
    return host if encoded is None else encoded


def stored_name(typed: str) -> str | None:
    """Набранное в поле поиска имя сайта или адрес почты — в ASCII, как их хранит база: «пример.рф» →
    `xn--e1afmkfd.xn--p1ai`, «info@пример.рф» → `info@xn--e1afmkfd.xn--p1ai`.

    Те же метки, что у `ascii_host`, но набранное бывает куском имени: пустая метка («пример.» — точка без
    зоны, «.рф») остаётся как есть. Уже ASCII, имя ящика не в ASCII, знак вне IDNA (пробел, запятая) или
    метка длиннее 63 знаков — `None`: другой формы у набранного нет.
    """
    text = typed.strip()
    local, at, domain = text.rpartition("@")
    if text.isascii() or not local.isascii():
        return None
    encoded = _ascii_labels(domain.split("."))
    return None if encoded is None else f"{local}{at}{encoded}"


def _ascii_labels(labels: list[str]) -> str | None:
    """Метки через точку в ASCII: метка в ASCII — как есть, иначе IDNA (UTS 46: регистр и ширина знаков
    сводятся); хоть одна не кодируется — `None`."""
    try:
        return ".".join(
            label if label.isascii() else idna.encode(label, uts46=True).decode("ascii")
            for label in labels
        )
    except (idna.IDNAError, UnicodeError) as exc:
        logger.debug("имя %r не кодируется в IDNA (%s)", ".".join(labels)[:200], exc)
        return None
