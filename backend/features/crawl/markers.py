"""Пометка рекламы: раздел адреса статьи, то, что сайт сказал о ней сам, подпись в анкоре.

Пометка — **метка, а не подстрока**: словом целиком, в разделе адреса или
в начале адреса статьи. Подстрокой она на финансах (06.10) дала +4 всем
ссылкам статьи о компании «Partners Group».
"""

from __future__ import annotations

from itertools import pairwise
from urllib.parse import urlsplit

from backend.features.crawl.anchors import is_address, tokens
from backend.features.crawl.links import OutLink

#: Пометка рекламного материала — **метка, а не подстрока**. Ищется словом
#: целиком: в названии раздела адреса («/sponsored/…»), в начале адреса
#: статьи («/sponsored-…») и в анкоре, который не адрес. Подстрокой она
#: на финансах (06.10) дала +4 всем ссылкам статьи о компании «Partners
#: Group» и ссылке на регулятора с «/Advertising/» в адресе — десять
#: «купленных» из десяти оказались ложными.
MARKER_WORDS: frozenset[str] = frozenset(
    {"sponsored", "advertorial", "advertisement", "promoted", "guestpost"}
)
#: Метки из двух слов. «partner», «paid» и «guest» поодиночке — обычные
#: слова («Partners Group», «paid off the mortgage»), меткой их делает пара.
MARKER_PAIRS: frozenset[tuple[str, str]] = frozenset(
    {
        ("guest", "post"), ("guest", "posts"), ("paid", "post"), ("paid", "posts"),
        ("partner", "content"), ("partner", "post"), ("partner", "posts"),
        ("sponsored", "post"), ("brand", "partner"), ("paid", "content"),
    }
)  # fmt: skip
#: В анкоре метка — только то, чем подписывают рекламу. «promoted» в анкоре —
#: чаще «promoted to manager», чем подпись.
ANCHOR_MARKERS: frozenset[str] = frozenset({"sponsored", "advertorial", "advertisement"})


def _has_mark(tokens: list[str], words: frozenset[str]) -> bool:
    return any(token in words for token in tokens) or any(
        pair in MARKER_PAIRS for pair in pairwise(tokens)
    )


def _starts_with_mark(tokens: list[str]) -> bool:
    return bool(tokens) and (tokens[0] in MARKER_WORDS or tuple(tokens[:2]) in MARKER_PAIRS)


def _marked_address(page_url: str) -> bool:
    """Раздел адреса — метка («/sponsored/», «/partner-content/»), или адрес
    статьи с неё начинается («/sponsored-best-loans»). Слово в середине
    адреса статьи — её тема, а не метка: «/partners-group-…»."""
    segments = [s for s in urlsplit(page_url).path.split("/") if s]
    if not segments:
        return False
    sections, slug = segments[:-1], segments[-1]
    return any(_has_mark(tokens(s), MARKER_WORDS) for s in sections) or _starts_with_mark(
        tokens(slug)
    )


def names_a_mark(text: str) -> bool:
    """Текст называет метку рекламы словом: «Sponsored», «Partner Content», «guest-posts».

    Тот же словарь, что у адреса страницы, — для рубрик и разделов, которые
    сайт пишет в разметке (`page_facts`).
    """
    return _has_mark(tokens(text), MARKER_WORDS)


def marker_reason(link: OutLink) -> str | None:
    """Чем материал помечен рекламным, или `None`.

    Пометка статьи — раздел её адреса или то, что сайт сказал о ней сам, —
    достаётся только ссылкам **из тела**. Рядом со статьёй лежит то, что
    стоит на каждой странице сайта, и проданным в этой статье оно не стало.
    Подпись в анкоре — свойство самой ссылки, где бы она ни стояла.
    """
    if link.in_body and _marked_address(link.page_url):
        return "раздел адреса статьи"
    if link.in_body and link.page_label:
        return f"статья помечена: {link.page_label}"
    if not is_address(link.anchor) and _has_mark(tokens(link.anchor), ANCHOR_MARKERS):
        return "подпись в анкоре"
    return None


def has_marker(link: OutLink) -> bool:
    """Материал помечен рекламным (см. `marker_reason`)."""
    return marker_reason(link) is not None
