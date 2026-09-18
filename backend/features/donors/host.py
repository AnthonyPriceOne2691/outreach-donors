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

from urllib.parse import urlsplit

import tldextract

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
    host = urlsplit(candidate).hostname or ""
    if not host:
        return ""

    parts = _extract(host)
    if not parts.domain or not parts.suffix:
        return ""
    return f"{parts.domain}.{parts.suffix}"
