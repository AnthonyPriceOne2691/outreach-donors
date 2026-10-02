"""Колонки базы лидов: какое поле в какой колонке — по таблице синонимов.

Базы приходят с разными заголовками: «Почта», «E-mail», «Work email». Синоним —
строка данных в таблице ниже, а не ветка кода: новый заголовок добавляется
строкой. Сверка — без регистра и без знаков `_ - . : *` между словами, поэтому
`First_Name` и «first name» — одно имя.

Чего в таблице нет, то не угадывается: колонка остаётся без поля, и сопоставление
поправит человек — предпросмотр показывает все колонки и угаданное.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from enum import StrEnum


class LeadField(StrEnum):
    """Поле лида, в которое ложится колонка файла."""

    EMAIL = "email"
    NAME = "name"
    FIRST_NAME = "first_name"
    LAST_NAME = "last_name"
    POSITION = "position"
    COMPANY = "company"
    WEBSITE = "website"
    COUNTRY = "country"
    LANGUAGE = "language"


#: Заголовки, по которым поле узнаётся само. Один заголовок — одно поле: «имя»
#: — это `name`, а не `first_name`: рядом с «фамилией» они сложатся в имя целиком.
SYNONYMS: dict[LeadField, tuple[str, ...]] = {
    LeadField.EMAIL: (
        "email", "e-mail", "mail", "email address", "work email", "business email",
        "почта", "эл. почта", "электронная почта", "e-mail адрес", "адрес почты", "емейл",
    ),
    LeadField.NAME: ("name", "full name", "contact name", "имя", "фио", "имя и фамилия"),
    LeadField.FIRST_NAME: ("first name", "firstname", "given name"),
    LeadField.LAST_NAME: ("last name", "lastname", "surname", "family name", "фамилия"),
    LeadField.POSITION: ("position", "title", "job title", "role", "должность"),
    LeadField.COMPANY: (
        "company", "company name", "organization", "organisation", "компания",
        "название компании", "организация",
    ),
    LeadField.WEBSITE: (
        "website", "site", "web site", "url", "domain", "company website", "company domain",
        "сайт", "домен", "веб-сайт", "сайт компании",
    ),
    LeadField.COUNTRY: ("country", "страна"),
    LeadField.LANGUAGE: ("language", "lang", "язык"),
}  # fmt: skip

#: Поле → номер колонки, с нуля.
Mapping = dict[LeadField, int]

_SEPARATORS = re.compile(r"[\s_\-.:*]+")


def key(title: str) -> str:
    """`  First_Name: ` → `first name`: заголовок в том виде, в каком его сверяют."""
    return _SEPARATORS.sub(" ", title.lower()).strip()


_BY_TITLE = {key(title): field for field, titles in SYNONYMS.items() for title in titles}


def guess(titles: Sequence[str]) -> Mapping:
    """Поля, узнанные по заголовкам. Две колонки на одно поле — берётся левая."""
    found: Mapping = {}
    for index, title in enumerate(titles):
        field = _BY_TITLE.get(key(title))
        if field is not None:
            found.setdefault(field, index)
    return found
