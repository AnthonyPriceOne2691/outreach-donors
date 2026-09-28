"""Текст письма из HTML — для ответа, у которого текстовой части нет.

Такие ответы бывают: веб-почта и телефоны отправляют один HTML. Пока его
не читали, ответ приходил пустым, и цена в нём не была видна ни модели,
ни человеку.

**Строки сохраняются.** Готовое извлечение текста в проекте есть (тело
статьи в обходе), но оно склеивает всё в одну строку — для статьи это
верно, для письма нет: цитата узнаётся по началу строки, подпись —
по отдельной строке, и без переносов их не отрезать.

**Цитата помечается «> » в начале строки.** Отрезает её `quoting` — по той
же метке, что у текстовых писем. Иначе в «написанное человеком» уехала бы
цитата нашего письма со словом «unsubscribe» в юридическом блоке, и ответ
стал бы отпиской. Цитату узнаём по разметке почтовых программ, а не по
содержимому: блок цитаты (`blockquote`, у Apple — `type="cite"`), обёртка
Gmail и Yahoo — цитата внутри них; у Outlook метка ставится **перед**
цитатой, и цитата — всё, что идёт за ней до конца письма.

Разбор — `selectolax`, как у страниц доноров: он собирает дерево по правилам
браузера и не падает на незакрытых тегах, которыми почта полна.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from selectolax.parser import HTMLParser, Node

#: Не текст письма вовсе.
_DROPPED = frozenset({"head", "script", "style", "template", "noscript", "title", "svg"})

#: Элементы, которые начинают строку. Перенос внутри строки даёт только `<br>`.
_BLOCKS = frozenset({
    "address", "article", "aside", "blockquote", "center", "dd", "details", "dir", "div",
    "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3",
    "h4", "h5", "h6", "header", "hr", "li", "main", "menu", "nav", "ol", "p", "pre",
    "section", "summary", "table", "tr", "ul", "caption",
})  # fmt: skip

#: Классы обёрток, внутри которых — цитата: Gmail (`gmail_quote`, у новых —
#: ещё и `gmail_quote_container`) и Yahoo.
_QUOTE_CLASSES = ("gmail_quote", "yahoo_quoted")

#: Метки Outlook, после которых всё — цитата: пустой `appendonsend` перед
#: чертой, блок «From: … Sent: …» (`divRplyFwdMsg`) и старый заголовок
#: `OutlookMessageHeader`.
_QUOTE_START_IDS = frozenset({"divrplyfwdmsg", "appendonsend"})
_QUOTE_START_CLASSES = frozenset({"outlookmessageheader"})

_SPACES = re.compile(r"[ \t\n\r\f\v]+")


@dataclass
class _Lines:
    """Строки текста с глубиной цитаты у каждой."""

    done: list[tuple[int, str]] = field(default_factory=list)
    current: list[str] = field(default_factory=list)
    depth: int = 0
    size: int = 0

    def add(self, text: str) -> None:
        if text:
            self.current.append(text)
            self.size += len(text)

    def soft_break(self) -> None:
        """Граница блока: новая строка, если в текущей что-то есть.

        Неразрывный пробел считается содержимым: абзац из одного `&nbsp;` —
        это пустая строка, которой Outlook разделяет абзацы.
        """
        line = "".join(self.current)
        if line.strip(" \t\n"):
            self.done.append((self.depth, line.replace("\xa0", " ").strip()))
        self.current = []

    def hard_break(self) -> None:
        """`<br>`: новая строка всегда — пустая строка между абзацами значима."""
        self.done.append((self.depth, "".join(self.current).replace("\xa0", " ").strip()))
        self.current = []

    def rendered(self) -> str:
        self.soft_break()
        out: list[str] = []
        for depth, line in self.done:
            mark = ">" * depth
            out.append(f"{mark} {line}" if depth and line else mark + line)
        return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _classes(node: Node) -> list[str]:
    return (node.attributes.get("class") or "").lower().split()


def _is_quote(node: Node) -> bool:
    if node.tag == "blockquote":
        return True
    return any(name.startswith(_QUOTE_CLASSES) for name in _classes(node))


def _starts_quote(node: Node) -> bool:
    if (node.attributes.get("id") or "").lower() in _QUOTE_START_IDS:
        return True
    return any(name in _QUOTE_START_CLASSES for name in _classes(node))


@dataclass
class _Walk:
    """Обход дерева с явным стеком: вложенность почтового HTML бывает такой,
    что рекурсия упёрлась бы в предел Python на пересылке пересылки."""

    lines: _Lines
    limit: int
    rest_quoted: bool = False
    preformatted: int = 0

    def enter(self, node: Node) -> bool:
        """Вход в узел. `False` — внутрь не заходить."""
        tag = node.tag
        if tag == "-text":
            self._text(node.text(deep=False) or "")
            return False
        if tag in _DROPPED or tag.startswith("_"):  # `_comment` и прочее служебное
            return False
        if _starts_quote(node) and not self.rest_quoted:
            self.lines.soft_break()
            self.rest_quoted = True
            self.lines.depth += 1
        self._open(node, tag)
        return True

    def _open(self, node: Node, tag: str) -> None:
        quote = _is_quote(node)
        if tag == "br":
            self.lines.hard_break()
        elif tag in ("td", "th") and self.lines.current:
            self.lines.add("\t")
        elif tag in _BLOCKS or quote:
            self.lines.soft_break()
        self.lines.depth += int(quote)
        self.preformatted += int(tag == "pre")
        if tag == "li":
            self.lines.add("- ")

    def leave(self, node: Node) -> None:
        if node.tag in _BLOCKS or _is_quote(node):
            self.lines.soft_break()
        self.lines.depth -= int(_is_quote(node))
        self.preformatted -= int(node.tag == "pre")

    def _text(self, text: str) -> None:
        if not self.preformatted:
            # Пробелы схлопываются и между узлами: «цена </b> <i>200» — один
            # пробел, как его показал бы браузер.
            collapsed = _SPACES.sub(" ", text)
            previous = self.lines.current[-1] if self.lines.current else " "
            if collapsed.startswith(" ") and previous.endswith((" ", "\t")):
                collapsed = collapsed[1:]
            self.lines.add(collapsed)
            return
        first, *rest = text.split("\n")
        self.lines.add(first)
        for line in rest:
            self.lines.hard_break()
            self.lines.add(line)

    @property
    def full(self) -> bool:
        return self.lines.size > self.limit


def text_from_html(html: str, *, limit: int = 1_000_000) -> str:
    """Текст письма из HTML: строки на месте, цитата помечена «> ».

    `limit` — сколько знаков текста собирать: дальше обход не идёт, всё
    равно отрежется по длине письма, а время на разбор ушло бы.
    """
    if not html.strip():
        return ""
    tree = HTMLParser(html)
    root = tree.body or tree.root
    if root is None:
        return ""
    walk = _Walk(_Lines(), limit)
    stack: list[tuple[Node, bool]] = [(root, False)]
    while stack and not walk.full:
        node, leaving = stack.pop()
        if leaving:
            walk.leave(node)
            continue
        if not walk.enter(node):
            continue
        stack.append((node, True))
        stack.extend((child, False) for child in reversed(list(node.iter(include_text=True))))
    return walk.lines.rendered()
