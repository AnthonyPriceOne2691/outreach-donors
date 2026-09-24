"""Что не должно оказаться в письме.

Пока запрет один, и он стоит дорого: **метрики Ahrefs никогда не попадают
в текст письма адресату** — это правило самого Ahrefs, и нарушение бьёт
не по письму, а по ключу, на котором держится весь сбор базы.

Проверка стоит на собранном письме, после уникализации, а не на шаблоне.
Причина в том, что дописать метрику может и модель: она видит домен
донора и охотно объясняет, почему он нам интересен, — «your DR 46 site»
выглядит естественной персонализацией и является нарушением.
"""

from __future__ import annotations

import re

#: Запрещённое и то, как оно называется вслух. Проверка по фразам,
#: а не по словам: «traffic» в письме законно, «organic traffic» — нет.
_FORBIDDEN: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"ahrefs", re.I), "название сервиса метрик"),
    (re.compile(r"domain\s+rating", re.I), "рейтинг домена"),
    (re.compile(r"domain\s+authority", re.I), "рейтинг домена"),
    (re.compile(r"organic\s+traffic", re.I), "органический трафик"),
    (re.compile(r"organic\s+keywords", re.I), "органические ключи"),
    (re.compile(r"referring\s+domains", re.I), "ссылающиеся домены"),
    (re.compile(r"\bdr\s*[:=]?\s*\d{1,3}\b", re.I), "значение DR"),
)


class ForbiddenContentError(ValueError):
    """В письме то, чего в нём быть не может. Сообщение называет что и где."""

    #: Повтор задачи это не исправит (`runs/failures.py`).
    permanent = True


def metrics_leak(text: str) -> str | None:
    """Что из метрик просочилось в текст. `None` — чисто.

    Возвращает не «да/нет», а сам кусок: запрет, о котором известно
    только то, что он сработал, отлаживают перечитыванием письма.
    """
    for pattern, title in _FORBIDDEN:
        found = pattern.search(text)
        if found is not None:
            return f"{title}: «{found.group(0)}»"
    return None


def assert_no_metrics(text: str) -> None:
    """Отказать, если метрики в письме. Зовётся перед постановкой в очередь."""
    leak = metrics_leak(text)
    if leak is not None:
        raise ForbiddenContentError(
            f"В письме метрики Ahrefs ({leak}) — правила Ahrefs это запрещают, "
            "и нарушение стоит ключа, а не письма. "
            "Убрать из шаблона или переписать зону заново"
        )
