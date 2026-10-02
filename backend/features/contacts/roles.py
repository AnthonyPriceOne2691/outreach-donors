"""Ролевые локальные части адреса: `info@`, `ads@`, `editor@`.

Общие для извлечения и оценки. Оценка (`quality.py`) ставит ролевой адрес
выше личного: это контакт редакции, а не случайного человека. Извлечение
(`extract.py`) по ним решает, считать ли адресом «ads at site.com», где
«at» записано словом без скобок: в обычной фразе перед «at» стоит что
угодно («here at …», «2024 At …»), а ролевая часть — признак адреса.
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
