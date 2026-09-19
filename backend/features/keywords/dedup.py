"""Дедупликация пула: точные повторы и почти одинаковые фразы.

Самое дорогое место всей генерации, и цена измерена нами: прогон
18.09.2026 дал 50 ключей → 500 результатов выдачи → 85 уникальных
доменов. **83% выдачи схлопнулось в дубли.**

Две почти одинаковые фразы — это два оплаченных запроса и один и тот же
набор доменов. При цене выдачи в 74 юнита за ключ сотня лишних
почти-дублей выбрасывает 7 400 юнитов ещё до сбора метрик.

Отсев лексический и без модели намеренно: он должен быть дешёвым,
одинаковым от запуска к запуску и объяснимым. «Модель решила, что это
дубль» объяснить нельзя.
"""

from __future__ import annotations

from collections.abc import Sequence

#: Доля общих слов, при которой вторая фраза считается повтором первой.
#: Две трети — консервативно: ловит «новости манилы» против «последние
#: новости манилы» (0,67), но не режет «утренние» и «вечерние новости»
#: (0,5). Порог настраивается по боевым прогонам, а не по вкусу.
NEAR_DUPLICATE_THRESHOLD = 0.6


def _similarity(first: frozenset[str], second: frozenset[str]) -> float:
    """Доля общих слов: пересечение к объединению."""
    if not first or not second:
        return 0.0
    return len(first & second) / len(first | second)


def drop_near_duplicates(
    phrases: Sequence[str], *, threshold: float = NEAR_DUPLICATE_THRESHOLD
) -> list[str]:
    """Убрать почти одинаковые фразы, оставив первую из каждой группы.

    Порядок сохраняется: первой идёт та, что пришла раньше, — а раньше
    приходят фразы более дорогих углов.
    """
    kept: list[str] = []
    kept_words: list[frozenset[str]] = []

    for phrase in phrases:
        words = frozenset(phrase.lower().split())
        if not words:
            continue
        if any(_similarity(words, other) >= threshold for other in kept_words):
            continue
        kept.append(phrase)
        kept_words.append(words)

    return kept


def interleave(pools: Sequence[Sequence[str]], cap: int) -> list[str]:
    """Слить наборы по очереди: по фразе из каждого, пока не кончится место.

    Нужно для многоязычных рынков: без чередования первый язык занимает
    весь потолок, и второго в пуле не остаётся вовсе.
    """
    out: dict[str, str] = {}
    longest = max((len(pool) for pool in pools), default=0)

    for position in range(longest):
        for pool in pools:
            if position >= len(pool):
                continue
            out.setdefault(pool[position].strip().lower(), pool[position])
            if len(out) >= cap:
                return list(out.values())[:cap]

    return list(out.values())[:cap]


def split_over_angles(cap: int, angles: int, *, favour_first: int = 0) -> list[int]:
    """Разделить потолок между углами.

    Остаток отдаётся первым углам — тем, что перечислены раньше и дают
    больше доноров. Без деления один угол съедает пул, и база выходит
    перекошенной в одну тематику.
    """
    if cap <= 0 or angles <= 0:
        return [0] * max(angles, 0)

    base, remainder = divmod(cap, angles)
    counts = [base] * angles
    favoured = min(favour_first, angles) or angles

    for index in range(remainder):
        counts[index % favoured] += 1
    return counts
