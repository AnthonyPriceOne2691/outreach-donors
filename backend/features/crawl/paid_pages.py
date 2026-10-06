"""Статья, помеченная платной целиком, и о ком она.

Площадки, продающие посты, метят `rel=sponsored` всю статью — вместе со
ссылками-источниками (обход 06.10: у ethereum.org, coingecko, benzinga
рядом с самим рекламодателем статьи). Полный балл скоринга — тому, о ком
статья (`scoring.py`), остальным — «источник» на решение человека.
"""

from __future__ import annotations

from collections import defaultdict
from urllib.parse import urlsplit

from backend.features.crawl.anchors import POSSESSIVE, tokens
from backend.features.crawl.links import OutLink
from backend.features.crawl.markers import marker_reason

#: Статья помечена платной целиком — `rel=sponsored` или пометка рекламы
#: на ссылках к трём и более разным сайтам. Так площадки, продающие посты,
#: метят всю статью вместе со ссылками-источниками (обход 06.10:
#: `rel=sponsored` у ethereum.org, coingecko, benzinga рядом с самим
#: рекламодателем статьи). Полный балл — тому, о ком статья (его имя
#: в адресе статьи); остальным — «источник», на решение человека.
WIDE_PAID_TARGETS = 3
#: Сайт метит платные статьи целиком: среди ссылок из тела на страницах с
#: `rel=sponsored` помечено почти всё. Тогда пометка — свойство статьи, а не
#: ссылки, на любой его платной странице — и с одной-двумя ссылками тоже.
SITE_WIDE_SHARE = 0.8
SITE_WIDE_MIN_PAGES = 3
#: Сколько букв нужно слову адреса статьи и имени домена, чтобы считать
#: совпадением: «near», «aave», «x» совпадали бы со всем подряд.
SUBJECT_MIN_LETTERS = 5


def paid_pages(links: list[OutLink]) -> dict[str, frozenset[str]]:
    """Статьи, помеченные платными целиком, и о ком каждая из них.

    Ключ — адрес статьи, значение — домен, о котором статья: его имя есть
    в адресе статьи, и он там один («…-as-zentrix-confirms-…» — статья
    о zentrixpresale.example, а ethereum.org и coingecko в ней — источники).
    Два имени в адресе («cardano-price-prediction-…-while-zetafrog-…») —
    крючок и рекламодатель, и кто из них кто, по адресу не понять: оба
    идут на решение человека. Пустое значение — статья ни о ком из них.
    """
    by_page: defaultdict[str, set[str]] = defaultdict(set)
    plain: defaultdict[str, set[str]] = defaultdict(set)
    for link in links:
        if link.in_body and (link.sponsored or marker_reason(link) is not None):
            by_page[link.page_url].add(link.target_root)
            if not POSSESSIVE.search(link.anchor):
                plain[link.page_url].add(link.target_root)
    whole = _marks_whole_articles(links, set(by_page))
    return {
        page: _subject(page, roots, plain[page])
        for page, roots in by_page.items()
        if whole or len(roots) >= WIDE_PAID_TARGETS
    }


def _marks_whole_articles(links: list[OutLink], paid: set[str]) -> bool:
    """Сайт метит `sponsored` всю платную статью, а не одну ссылку в ней."""
    if len(paid) < SITE_WIDE_MIN_PAGES:
        return False
    on_paid = [link for link in links if link.in_body and link.page_url in paid]
    marked = sum(1 for link in on_paid if link.sponsored)
    return bool(on_paid) and marked / len(on_paid) >= SITE_WIDE_SHARE


def _subject(page_url: str, roots: set[str], plain: set[str]) -> frozenset[str]:
    """О ком статья: имя в её адресе, и ссылка на него — не «чей-то что-то».

    `plain` — домены, на которые статья ссылается не только притяжательным
    оборотом. «Solana’s Alpenglow upgrade work», «Tether’s USDT» — так
    ссылаются на чужое в любой нише («Apple’s report»), а рекламодателя
    называют брендом, ключом или адресом (площадка платных постов, 06.10).
    """
    sections = [section for section in urlsplit(page_url).path.split("/") if section]
    words = (
        {w for w in tokens(sections[-1]) if len(w) >= SUBJECT_MIN_LETTERS} if sections else set()
    )
    named = {root for root in roots & plain if _named_in(root, words)}
    if len(named) == 1:
        return frozenset(named)
    if not named and len(roots) == 1 and roots <= plain:
        # Платная статья с одной ссылкой — о ней, как бы статья ни называлась.
        return frozenset(roots)
    return frozenset()


def _named_in(root: str, words: set[str]) -> bool:
    name = root.split(".", maxsplit=1)[0].replace("-", "")
    if len(name) < SUBJECT_MIN_LETTERS:
        return False
    return any(word in name or name in word for word in words)
