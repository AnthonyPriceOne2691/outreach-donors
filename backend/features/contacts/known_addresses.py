"""Что известно об адресах заранее: ролевые части и бесплатная почта.

Общее для извлечения и оценки. Оценка (`quality.py`) ставит ролевой адрес
выше личного, а адрес на домене сайта — выше бесплатной почты. Извлечение
(`extract.py`) по тем же спискам решает, считать ли адресом запись, где
«at» стоит словом без скобок: «ads at site.com», «mike at gmail.com».
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
    }
)  # fmt: skip
