"""Подбор переписанных зон под коридор отличия: какие из них принять в письмо.

Модель переписывает зоны `rewrite`, но просьбу «поменяй N% слов» при большой переписываемой
доле не исполняет — переписывает зоны почти целиком: живые прогоны продаж 11.10.2026 у
шаблона целиком `rewrite` дали письма с отличием 42–66% при коридоре 15–25%, ни одного в
коридоре. Каждая переписанная зона при этом — годный текст, и письмо ложится в коридор, если
часть зон оставить текстом шаблона (те же прогоны: 20 писем из 20 одним вызовом). Какие —
решает отличие собранного письма. Поэтому `rewrite` значит «может быть переписана»: в
отдельном письме зона может остаться текстом шаблона.

**Выбор — с зерном.** В коридор попадают несколько наборов — выбор среди них
`random.Random(seed)`, зерно — номер лида или донора. Детерминированно: сборка того же
адресата повторима, тесты стабильны. А у разных адресатов наборы разные: «ближайший к
середине коридора» почти всегда давал бы один набор, и шаблонные абзацы писем совпадали бы.

**Ни один набор не в коридоре — `None`**: что дальше (повтор модели, письмо как есть),
решает вызывающий. Проверки письма — метрики, подпись, адрес — тоже у него: ядро только
подбирает по отличию собранного письма — `difference` текста целиком, а не сумма по зонам.

**Перебор — полный до `limit` переписанных зон** (`LIMIT` — 2^6 − 1 = 63 сборки письма),
дальше — жадно: по зоне за шаг, ту, что ближе всего подводит письмо к середине коридора,
пока оно приближается, — не больше k(k+1)/2 сборок. Шаблон в 15 зон `rewrite` не вешает
сборку на 2^15 наборов, а отказа при записи шаблона нет: записанные раньше собираются.
"""

from __future__ import annotations

import itertools
import random
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from backend.config import outreach as cfg
from backend.features.letters import compose
from backend.features.letters.compose import Rendered
from backend.features.letters.uniqueness import difference, in_corridor

#: Переписанных зон, до которых перебор полный: 2^6 − 1 = 63 сборки письма. Больше — жадно.
LIMIT = 6


@dataclass(frozen=True, slots=True)
class Selection:
    """Подобранный набор: принятые переписанные зоны, отличие письма с ними и переписанные
    зоны, оставленные текстом шаблона."""

    #: Принятые переписанные зоны — подмножество переписанных.
    zones: dict[str, str]
    #: Отличие собранного письма: `difference(assembled.plain_body, assembled.body)`.
    difference: float
    #: Переписанные зоны, оставленные текстом шаблона, — по порядку шаблона.
    dropped: tuple[str, ...]


def _assembled(
    rendered: Rendered, rewritten: Mapping[str, str], names: Sequence[str], kept: Collection[str]
) -> Selection:
    """Письмо с принятыми зонами `kept` — и его отличие от шаблона, по собранному тексту."""
    zones = {name: rewritten[name] for name in names if name in kept}
    letter = compose.assemble(rendered, zones)
    dropped = tuple(name for name in names if name not in kept)
    return Selection(zones, difference(letter.plain_body, letter.body), dropped)


def _every(
    rendered: Rendered, rewritten: Mapping[str, str], names: Sequence[str]
) -> list[Selection]:
    """Все непустые наборы — меньшие первыми, внутри по порядку шаблона."""
    return [
        _assembled(rendered, rewritten, names, kept)
        for size in range(1, len(names) + 1)
        for kept in itertools.combinations(names, size)
    ]


def _greedy(
    rendered: Rendered, rewritten: Mapping[str, str], names: Sequence[str]
) -> list[Selection]:
    """Зон больше предела: к набору — по зоне, что ближе всего подводит письмо к середине
    коридора, пока оно приближается. Каждый собранный набор — кандидат."""
    middle = (cfg.UNIQUENESS_TARGET_MIN + cfg.UNIQUENESS_TARGET_MAX) / 2
    seen: list[Selection] = []
    kept: set[str] = set()
    best: float | None = None
    while len(kept) < len(names):
        steps = [
            _assembled(rendered, rewritten, names, kept | {name})
            for name in names
            if name not in kept
        ]
        seen += steps
        step = min(steps, key=lambda found: abs(found.difference - middle))
        distance = abs(step.difference - middle)
        if best is not None and distance >= best:
            break
        best, kept = distance, set(step.zones)
    return seen


def select(
    rendered: Rendered, rewritten: Mapping[str, str], *, seed: int, limit: int = LIMIT
) -> Selection | None:
    """Какие переписанные зоны принять, чтобы письмо легло в коридор отличия.

    Наборов в коридоре несколько — выбор среди них с зерном `seed` (номер лида или донора);
    ни одного (переписанных нет, всё ниже нижнего края, даже одна зона выше верхнего) —
    `None`. Перебор полный до `limit` переписанных зон, дальше — жадно."""
    names = [zone.name for zone in rendered.rewritable() if zone.name in rewritten]
    search = _every if len(names) <= limit else _greedy
    fits = [found for found in search(rendered, rewritten, names) if in_corridor(found.difference)]
    if not fits:
        return None
    # Разнообразие писем, а не тайна: зерно — номер адресата, сборка повторима.
    return random.Random(seed).choice(fits)  # noqa: S311
