"""Нормализация ссылки в ключ домена.

Ключ дедупликации — корневой домен без протокола, `www` и субдоменов.
Наивно срезать всё до последних двух меток нельзя: `someone.wordpress.com`
и `user.github.io` принадлежат разным владельцам, и схлопывание их в
`wordpress.com` и `github.io` слило бы тысячи не связанных между собой сайтов
в один. Поэтому границу проводит список публичных суффиксов.

Обратная ошибка не менее дорогая: если `blog.example.com` и `example.com`
останутся разными записями, мы дважды заплатим Ahrefs за один сайт и дважды
напишем одному владельцу.
"""

from __future__ import annotations

import unicodedata

import tldextract

from backend.shared.net.url_parts import split_url

# Список суффиксов берётся из снимка, вшитого в пакет: ходить за ним в сеть
# на каждом запуске значит поставить прогон в зависимость от чужой доступности
# и получать разный результат в разные дни.
#
# `include_psl_private_domains=True` включает приватную секцию списка — без неё
# `user.github.io` и `shop.myshopify.com` схлопнулись бы в домены платформ.
# Секция покрывает github.io, blogspot.com, myshopify.com, netlify.app,
# vercel.app, herokuapp.com и ещё сотни площадок.
#
# Известное ограничение: wordpress.com и medium.com из списка исключены самими
# площадками, поэтому `blog.wordpress.com` даст `wordpress.com`. Для нашей
# задачи это терпимо — сайты на бесплатных поддоменах в доноры и так не годятся
# и отсеются порогами; но если такие домены начнут попадаться в отчётах как
# «подходящие», сюда нужен явный список исключений.
_extract = tldextract.TLDExtract(suffix_list_urls=(), include_psl_private_domains=True)

#: Потолки доменного имени (RFC 1035): метка и всё имя без завершающей точки.
MAX_LABEL_CHARS = 63
MAX_NAME_CHARS = 253

#: Знаки метки, кроме букв и цифр. Подчёркивание — не по RFC, но в поддоменах оно живёт.
_LABEL_MARKS = "-_"


def normalize_host(raw: str | None) -> str:
    """`HTTPS://WWW.Blog.Example.co.uk/path?x=1` → `example.co.uk`.

    Возвращает пустую строку, если на входе мусор: вызывающий трактует её как
    «обрабатывать нечего» и выходит до платного вызова, а не падает.
    """
    if not raw or not isinstance(raw, str):
        return ""

    candidate = raw.strip().lower()
    if not candidate:
        return ""

    # urlsplit разбирает схему только при её наличии; без неё весь адрес
    # уезжает в path, поэтому схему подставляем.
    if "//" not in candidate:
        candidate = f"//{candidate}"
    split = split_url(candidate)
    host = split.hostname if split else None
    if not host:
        return ""

    parts = _extract(host)
    if not parts.domain or not parts.suffix:
        return ""
    return f"{parts.domain}.{parts.suffix}"


def domain_name(host: str) -> str | None:
    """Хост как доменное имя, без завершающей точки; не имя — `None`.

    Хост из ссылки чужой страницы — любая строка: шаблон на JavaScript, телефон, абзац текста.
    Суффикс здесь не сверяется: новые зоны появляются каждый год, и снимок списка им не судья.
    Проверяется форма. Буквы и цифры — любого алфавита, это категории Юникода, а не `isalnum`:
    гласные деванагари и тайского — знаки, и `isalnum` потерял бы настоящие зоны вроде «.भारत».
    Зона из одних цифр не бывает (RFC 3696): `v1.2` — номер версии, а не сайт.
    """
    name = host.removesuffix(".")
    labels = name.split(".")
    if len(labels) < 2 or len(name) > MAX_NAME_CHARS or labels[-1].isdigit():
        return None
    return name if all(_is_label(label) for label in labels) else None


def _is_label(label: str) -> bool:
    """Метка имени: 1–63 знака, буквы и цифры любого алфавита, дефис не с края."""
    if not 1 <= len(label) <= MAX_LABEL_CHARS or label[0] == "-" or label[-1] == "-":
        return False
    return all(char in _LABEL_MARKS or unicodedata.category(char)[0] in "LMN" for char in label)


def split_host(host: str) -> tuple[str, str, str]:
    """`a.b.example.co.uk` → (`a.b`, `example`, `co.uk`): поддомен, имя, суффикс.

    Граница — по тому же вшитому списку, что и у `normalize_host`: `sentry.co.uk`
    — это сайт, а не поддомен, и `.ovh` — зона, а не имя хостера.
    """
    parts = _extract(host)
    return parts.subdomain, parts.domain, parts.suffix
