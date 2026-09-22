"""Маскирование перед отправкой в модель и возврат после.

Требование буквально: «уходит текст письма и текст ответа; адреса
и имена заменяются метками до отправки и возвращаются после». Ответ
владельца задачи был «желательно не передавать» — маскирование делает это
и уникализации не мешает: модель переписывает фразу вокруг метки так же,
как вокруг адреса.

**Метка нумерованная, а не одинаковая.** Два разных адреса под одной
меткой вернулись бы одним и тем же — и письмо ушло бы не туда, куда
собиралось. Метка выглядит инородно (`[address 1]`) нарочно: модель
не принимает её за часть текста и оставляет на месте.

**Не вернувшаяся метка — поломка, а не мелочь.** Если модель метку
съела, письмо уйдёт с дырой на месте адреса. Поэтому возврат проверяет,
что вернулись все, и говорит, какие пропали.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Адрес в тексте. Нарочно шире, чем проверка адреса при сборе контактов:
#: здесь задача не отобрать годные, а не выпустить наружу ни одного.
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+", re.UNICODE)

_LABEL = "[address {number}]"
_LABEL_RE = re.compile(r"\[address (\d+)\]")


class UnmaskError(ValueError):
    """Метка не вернулась из модели — письмо собрать нельзя."""


@dataclass(slots=True)
class Masked:
    """Замаскированный текст и словарь для возврата."""

    text: str
    labels: dict[str, str] = field(default_factory=dict)


def mask(text: str) -> Masked:
    """Заменить адреса метками."""
    labels: dict[str, str] = {}
    known: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        address = match.group(0)
        if address not in known:
            label = _LABEL.format(number=len(known) + 1)
            known[address] = label
            labels[label] = address
        return known[address]

    return Masked(text=_EMAIL_RE.sub(replace, text), labels=labels)


def unmask(text: str, labels: dict[str, str]) -> str:
    """Вернуть адреса на место. Пропавшая метка — отказ."""
    result = text
    for label, address in labels.items():
        result = result.replace(label, address)

    lost = sorted(set(_LABEL_RE.findall(result)))
    if lost:
        raise UnmaskError(
            f"Модель вернула метки, которых мы не выдавали: {', '.join(lost)} — "
            "письмо собрать нельзя, зона остаётся шаблонной"
        )

    missing = [label for label in labels if label not in text]
    if missing:
        raise UnmaskError(
            f"Модель потеряла метки: {', '.join(missing)} — "
            "на их месте в письме была бы дыра, зона остаётся шаблонной"
        )
    return result


def leaked(text: str) -> str | None:
    """Адрес, оставшийся в тексте после маскирования. `None` — чисто.

    Проверка самой маскировки: она стоит перед вызовом модели, потому что
    правило «наружу не уходит ни один адрес» нельзя проверить после того,
    как запрос уже отправлен.
    """
    found = _EMAIL_RE.search(text)
    return found.group(0) if found is not None else None
