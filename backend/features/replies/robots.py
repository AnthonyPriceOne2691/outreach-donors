"""Адреса, за которыми нет человека: noreply и почтовая служба.

Правило одно на три места приёма, и в каждом ошибка стоит своего:

* **вид ответа** (`classify`): текст отказа доставки значит отказ только
  у письма, которое написала почта, — от mailer-daemon или postmaster;
  noreply — робот, и «ящик не читается» у него о себе, а не о доноре;
* **адрес, с которого ответили** (`repository.remember_answering_address`):
  он становится предпочтительным адресом донора. Письмо от noreply без
  служебных фраз правила вида считают человеком, и это верно — его
  увидит человек. Но запомнить такой адрес значит отправить следующее
  письмо донору роботу, который его выбросит, а отказ доставки ударит
  по репутации нашего домена;
* **адрес из автоответа мёртвого ящика** (`redirect`): «пишите
  на noreply@» — не адрес для писем.

Два списка на одно понятие разошлись бы на первой правке: одно место
узнало бы `do-not-reply@`, а другое — нет.

Локальная часть сверяется с начала: `noreply-bounces@`, `no_reply+42@`,
`mailer-daemon.eu@` — роботы, а `info.noreply-team@` — нет.
"""

from __future__ import annotations

import re

#: Обратный адрес почтового сервера — так подписывают отказы доставки.
_MAIL_SYSTEM = re.compile(
    r"^(?:mailer[-_.]?daemon|mail[-_.]?daemon|postmaster)(?:[-_.+].*)?$", re.I
)

#: Обратный адрес робота, на который не отвечают.
_NOREPLY = re.compile(r"^(?:no[-_.]?reply|do[-_.]?not[-_.]?reply)(?:[-_.+].*)?$", re.I)


def local_part(address: str) -> str:
    """Имя ящика до «@»."""
    return address.strip().partition("@")[0]


def mail_system(address: str) -> bool:
    """Письмо с этого адреса написала почта: отказ доставки, отчёт о ней."""
    return bool(_MAIL_SYSTEM.match(local_part(address)))


def no_reply(address: str) -> bool:
    """Робот, на письма которого никто не отвечает и письма которому никто
    не читает."""
    return bool(_NOREPLY.match(local_part(address)))


def robot(address: str) -> bool:
    """За адресом нет человека — адресом донора он не становится."""
    return mail_system(address) or no_reply(address)
