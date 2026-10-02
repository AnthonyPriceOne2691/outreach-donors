"""Что известно об адресах заранее: ролевые части и бесплатная почта.

Общее для извлечения и оценки. Оценка (`quality.py`) ставит ролевой адрес
выше личного, а адрес на домене сайта — выше бесплатной почты. Извлечение
(`extract.py`) по ролевым частям и почте без портала решает, считать ли
адресом запись, где «at» стоит словом без скобок: «ads at site.com»,
«mike at gmail.com».
"""

from __future__ import annotations

# Ролевые адреса. Берём даже на чужом домене: это контакт редакции,
# а не личная почта случайного человека.
ROLE_LOCAL_PARTS = frozenset(
    {
        "info", "contact", "contacts", "hello", "hi", "mail", "email", "office",
        "admin", "administrator", "webmaster", "support", "help",
        "editor", "editorial", "redaktion", "redazione", "redaksi",
        "press", "media", "marketing", "sales", "partnership", "partnerships",
        "ads", "advertise", "advertising", "advertisement", "iklan",
        "enquiries", "enquiry", "inquiries", "team", "kontakt", "kontak", "contacto",
    }
)  # fmt: skip

# Бесплатная почта. Личный ящик вебмастера — валидный контакт, но весит
# меньше адреса на домене сайта: на домене сидит тот, кто им распоряжается.
FREE_MAILBOX_DOMAINS = frozenset(
    {
        "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "yahoo.co.id",
        "hotmail.com", "hotmail.co.uk", "outlook.com", "live.com", "aol.com",
        "icloud.com", "me.com", "proton.me", "protonmail.com", "gmx.com", "gmx.net",
        "mail.ru", "yandex.ru", "yandex.com", "zoho.com", "mail.com",
        "qq.com", "163.com", "126.com", "naver.com",
        # Местная бесплатная почта. Без неё ящик владельца на bk.ru или web.de
        # весил как адрес чужого домена, а не как личный (находка «Продаж»,
        # 02.10.2026): рунет, Украина, немецкий, французский, итальянский,
        # польский, чешский рынки и местные зеркала больших почт.
        "bk.ru", "list.ru", "inbox.ru", "internet.ru", "ya.ru", "rambler.ru", "ro.ru",
        "yandex.by", "yandex.kz", "yandex.ua", "ukr.net", "i.ua", "meta.ua",
        "web.de", "gmx.de", "gmx.at", "gmx.ch", "t-online.de", "freenet.de", "arcor.de",
        "posteo.de", "mailbox.org", "orange.fr", "wanadoo.fr", "free.fr", "laposte.net",
        "sfr.fr", "libero.it", "virgilio.it", "tiscali.it", "alice.it", "wp.pl", "o2.pl",
        "onet.pl", "interia.pl", "seznam.cz", "yahoo.de", "yahoo.fr", "yahoo.es",
        "yahoo.it", "hotmail.fr", "hotmail.de", "hotmail.it", "hotmail.es", "outlook.de",
        "outlook.fr", "live.de", "live.fr", "msn.com", "pm.me", "protonmail.ch",
        "tutanota.com", "tuta.io",
    }
)  # fmt: skip

# Бесплатная почта без портала: сайт домена — вход в ящик, и только. Голое
# «at» читается адресом лишь перед такой почтой: «mike at gmail.com». О почте-
# портале — новости, поиск, магазин — пишут прозой: «Read more at msn.com»,
# «Shop at Orange.fr», и слово перед «at» становилось адресом (ревью e6 #144).
# Цена — «hans at web.de» не читается: такая запись редка, а проза о портале
# — нет. Список разрешающий: новая почта попадает сюда, только если портала
# у неё нет.
MAIL_ONLY_DOMAINS = frozenset(
    {
        "gmail.com", "googlemail.com", "hotmail.com", "hotmail.co.uk", "hotmail.fr",
        "hotmail.de", "hotmail.it", "hotmail.es", "outlook.com", "outlook.de",
        "outlook.fr", "live.com", "live.de", "live.fr", "icloud.com", "me.com",
        "proton.me", "protonmail.com", "protonmail.ch", "pm.me", "tutanota.com",
        "tuta.io", "gmx.com", "posteo.de", "mailbox.org", "bk.ru", "list.ru",
        "inbox.ru", "internet.ru", "ro.ru", "laposte.net", "126.com",
    }
)  # fmt: skip
