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
