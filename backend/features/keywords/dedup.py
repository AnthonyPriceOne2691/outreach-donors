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


def _words(phrase: str) -> frozenset[str]:
    return frozenset(phrase.lower().split())


def _sift(phrases: Sequence[str], threshold: float) -> list[tuple[str, str | None]]:
    """Каждая непустая фраза — и чьим почти повтором она вышла (`None` — оставлена).

    Один проход на оба ответа: «что оставить» и «почему отброшено» не должны
    разойтись. Сравнение — с уже оставленными; первая совпавшая — та, чьим
    повтором фраза названа.
    """
    kept: list[tuple[str, frozenset[str]]] = []
    out: list[tuple[str, str | None]] = []
    for phrase in phrases:
        words = _words(phrase)
        if not words:
            continue
        twin = next((other for other, seen in kept if _similarity(words, seen) >= threshold), None)
        if twin is None:
            kept.append((phrase, words))
        out.append((phrase, twin))
    return out


def drop_near_duplicates(
    phrases: Sequence[str], *, threshold: float = NEAR_DUPLICATE_THRESHOLD
) -> list[str]:
    """Убрать почти одинаковые фразы, оставив первую из каждой группы.

    Порядок сохраняется: первой идёт та, что пришла раньше, — а раньше
    приходят фразы более дорогих углов.
    """
    return [phrase for phrase, twin in _sift(phrases, threshold) if twin is None]


def near_duplicate_of(
    phrases: Sequence[str], *, threshold: float = NEAR_DUPLICATE_THRESHOLD
) -> dict[str, str]:
    """Тот же отсев с ответом «почему»: отброшенная фраза → оставленная, чьим
    почти повтором она вышла.

    Проверка на проде 14.10.2026: причина «почти повтор другого ключа» не называла,
    какого, и человек не понимал, что отсеялось и почему. Точный повтор назван
    сам собой («ключ» → «ключ»): ответ — по фразе, а не по месту в списке.
    """
    return {phrase: twin for phrase, twin in _sift(phrases, threshold) if twin is not None}


def overlap(first: str, second: str) -> tuple[int, int]:
    """Сколько слов у двух фраз общих и сколько разных всего — числа причины
    «2 общих слова из 3», тем же счётом, каким режет отсев (`_similarity`)."""
    a, b = _words(first), _words(second)
    return len(a & b), len(a | b)


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
