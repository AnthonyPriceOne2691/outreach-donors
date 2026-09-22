"""Тело статьи: где на странице текст, ради которого её открыли.

Зачем это отдельная работа, а не «взять все ссылки со страницы»:
навигация, подвал и блок «читайте также» дают ссылки на каждой странице
сайта, и рекламодателем от этого никто не становится. Допуск по ложным
рекламодателям — десять процентов; меню донора выберет его в одиночку.

**Тело ищется тремя способами подряд, и каждый следующий хуже
предыдущего.** Сначала разметка, которую сайт написал сам (`<article>`,
`itemprop="articleBody"`, `<main>`); потом обычные имена классов
движков; и только потом плотность текста. Порядок такой, потому что
первые два способа говорят, что сайт считает статьёй, а третий —
что мы думаем о его вёрстке.

**Плотность ссылок решает там, где не решила разметка.** В меню ссылки
занимают почти весь текст, в статье — малую часть. Это единственный
признак, который не зависит ни от движка, ни от языка, ни от того,
как сайт назвал свои классы.

**«Не нашли» — это `None`, а не пустая строка.** Страница без тела
бывает: раздел со списком, карточка товара, выдача поиска. Вернув
пустое тело, мы записали бы «ссылок нет» там, где их не искали.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

from selectolax.parser import HTMLParser, Node

logger = logging.getLogger(__name__)

#: Что выбрасывается до всякого разбора: там нет текста статьи, но есть
#: ссылки, и без выброса они попадут в тело через вложенность.
NOISE_TAGS: tuple[str, ...] = (
    "script", "style", "noscript", "template", "svg", "iframe", "form",
    "nav", "header", "footer", "aside",
)  # fmt: skip

#: Имена, которыми движки называют не-статью. Ищутся в `class` и `id`
#: подстрокой: вариантов написания больше, чем стоит перечислять.
NOISE_MARKERS: tuple[str, ...] = (
    "nav", "menu", "sidebar", "footer", "header", "breadcrumb",
    "comment", "share", "social", "related", "recommend", "popular",
    "widget", "banner", "promo", "subscribe", "newsletter", "cookie",
    "pagination", "tags", "author-box", "meta",
)  # fmt: skip

#: Разметка, которой сайт сам говорит «здесь статья».
SEMANTIC_SELECTORS: tuple[str, ...] = (
    "article",
    '[itemprop="articleBody"]',
    '[role="main"]',
    "main",
)

#: Имена классов, которыми статью называют распространённые движки.
CONTENT_SELECTORS: tuple[str, ...] = (
    ".entry-content", ".post-content", ".article-content", ".article-body",
    ".post-body", ".content-body", ".td-post-content", ".elementor-widget-theme-post-content",
    "#content", ".single-post-content",
)  # fmt: skip

#: Короче этого тело статьёй не считаем: подпись под картинкой и строка
#: «читайте также» набирают сотню символов и ссылку в придачу.
MIN_BODY_CHARS = 400

#: Доля ссылочного текста, выше которой блок считается навигацией.
#: Замерять её незачем: в меню она близка к единице, в статье — к нулю,
#: и любое значение в середине разделяет их одинаково.
MAX_LINK_DENSITY = 0.5


class BodySource(StrEnum):
    """Чем нашли тело. Едет в отчёт: способ говорит, насколько верить."""

    SEMANTIC = "semantic"  # разметка самого сайта
    CONTENT_CLASS = "content_class"  # обычное имя класса движка
    DENSITY = "density"  # наша догадка по плотности текста


@dataclass(frozen=True, slots=True)
class Article:
    """Выделенное тело: узел, его текст и то, чем мы его нашли."""

    node: Node
    text: str
    source: BodySource

    @property
    def length(self) -> int:
        return len(self.text)


def _is_noise(node: Node) -> bool:
    """Блок, который статьёй не бывает, — по тегу или по имени класса."""
    if node.tag in NOISE_TAGS:
        return True
    attrs = node.attributes
    names = f"{attrs.get('class') or ''} {attrs.get('id') or ''}".lower()
    return any(marker in names for marker in NOISE_MARKERS)


def strip_structural_noise(tree: HTMLParser) -> None:
    """Выбросить только то, что не бывает содержимым ни у кого.

    Отличается от `strip_noise` тем, что не смотрит на имена классов.
    Нужна для второго прохода — когда ссылки собираются со всей страницы
    и помечаются «в теле / вне тела»: там выбрасывать блок по имени
    класса значит решить за скоринг, что витрина офферов не считается.
    """
    for node in tree.css("*"):
        if node.tag and node.tag in NOISE_TAGS:
            node.decompose()


def strip_noise(tree: HTMLParser) -> None:
    """Выбросить из дерева всё, что не бывает статьёй.

    Делается до поиска тела, а не после: блок «читайте также» лежит
    внутри `<article>` у половины движков, и найдя тело первым, мы взяли
    бы его ссылки вместе со статьёй.
    """
    for node in tree.css("*"):
        # Узел мог быть удалён вместе с родителем — у такого нет тега.
        if node.tag and _is_noise(node):
            node.decompose()


def link_density(node: Node) -> float:
    """Какая доля текста блока лежит внутри ссылок.

    В меню это почти единица, в статье — малая часть. Признак не зависит
    ни от движка, ни от языка, ни от того, как сайт назвал классы.
    """
    text = (node.text() or "").strip()
    if not text:
        return 1.0
    inside = sum(len((a.text() or "").strip()) for a in node.css("a"))
    return min(1.0, inside / len(text))


def _text_of(node: Node) -> str:
    return " ".join((node.text() or "").split())


def _by_selectors(tree: HTMLParser, selectors: tuple[str, ...]) -> Node | None:
    """Первый подходящий узел: достаточно длинный и не забитый ссылками."""
    for selector in selectors:
        for node in tree.css(selector):
            if len(_text_of(node)) < MIN_BODY_CHARS:
                continue
            if link_density(node) > MAX_LINK_DENSITY:
                continue
            return node
    return None


def _by_density(tree: HTMLParser) -> Node | None:
    """Догадка: блок с самым длинным текстом вне ссылок.

    Считается по абзацам, а не по всему тексту узла: иначе побеждает
    `<body>` — он содержит статью, а вместе с ней и всё остальное.
    """
    best: Node | None = None
    best_score = 0.0
    for node in tree.css("div, section, td"):
        if not node.tag:
            continue
        paragraphs = node.css("p")
        if len(paragraphs) < 2:
            continue
        text = sum(len(_text_of(p)) for p in paragraphs)
        if text < MIN_BODY_CHARS:
            continue
        score = text * (1.0 - link_density(node))
        if score > best_score:
            best, best_score = node, score
    return best


def extract_article(html: str) -> Article | None:
    """Тело статьи или `None`, если страница статьёй не является.

    Порядок способов — от того, что сказал сайт, к тому, что мы про него
    подумали. `None` здесь законный ответ: раздел со списком и карточка
    товара статьями не являются, и делать вид, что являются, значит
    собрать ссылки меню как редакционные.
    """
    tree = HTMLParser(html)
    strip_noise(tree)

    node = _by_selectors(tree, SEMANTIC_SELECTORS)
    source = BodySource.SEMANTIC
    if node is None:
        node = _by_selectors(tree, CONTENT_SELECTORS)
        source = BodySource.CONTENT_CLASS
    if node is None:
        node = _by_density(tree)
        source = BodySource.DENSITY

    if node is None:
        logger.debug("тело статьи не выделено: страница не похожа на статью")
        return None
    return Article(node=node, text=_text_of(node), source=source)
