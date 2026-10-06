"""Что статья говорит о себе: пометка рекламного материала и дата выхода.

Ссылка не знает, что статья вокруг неё оплачена: раскрытие пишется
не в ссылке, а в статье — рубрикой в разметке, разделом в мета-данных,
фразой «This post is sponsored by…». Обход хранит ссылки, а не текст
страницы, поэтому снять это можно только здесь, пока страница в памяти.

**Что пометкой НЕ считается — важнее того, что считается.** Пометка даёт
+4 каждой ссылке из тела статьи: одна ложная пометка — несколько ложных
«купленных» разом.

- Раскрытие партнёрских ссылок («This post may contain affiliate links»)
  стоит на каждой статье финансового блога и говорит о ссылках самого
  автора, а не о проданном месте.
- Сквозная оговорка сайта («публикуем новости, пресс-релизы, sponsored
  content, advertorials…») стоит на каждой странице и ничего не говорит
  про ЭТУ статью.
- Рубрики соседних статей (блок «похожие» с `category-sponsored`)
  принадлежат соседним статьям: смотрим только статью с заголовком страницы.
- Слово в теме статьи («How to land sponsored posts on Instagram») — тема:
  фраза раскрытия ищется в начале и в конце текста и только как
  утверждение о самой статье («this post is sponsored»).

Фразы — английские: доноры прогонов англоязычные. Другой язык — свой
список фраз, а не перевод этого.

**Дата** — для свежести: размещения покупают сейчас, а обход по карте
сайта приносит и статьи двадцатилетней давности. По карте свежесть видна
не всегда: у одного из доноров дата правки обновлена у всех статей разом.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from selectolax.parser import HTMLParser, Node

from backend.features.crawl.markers import names_a_mark

logger = logging.getLogger(__name__)

#: Сколько текста статьи с начала и с конца смотрим на фразу раскрытия.
#: Раскрытие ставят первой или последней строкой; середина статьи — её тема.
HEAD_CHARS = 400
TAIL_CHARS = 600

#: Длиннее этого пометку не храним: её читает человек в причинах балла.
MAX_LABEL_CHARS = 120

#: Мета-данные, где сайт сам называет раздел и метки статьи.
SECTION_META: tuple[str, ...] = (
    'meta[property="article:section"]',
    'meta[property="article:tag"]',
    'meta[name="parsely-section"]',
)

#: Где сайт пишет дату выхода — от самого надёжного к наименее.
DATE_META: tuple[str, ...] = (
    'meta[property="article:published_time"]',
    'meta[name="article:published_time"]',
    'meta[itemprop="datePublished"]',
    'meta[name="parsely-pub-date"]',
    'meta[name="publish-date"]',
    'meta[name="pubdate"]',
    'meta[name="date"]',
)

#: Утверждение о самой статье: «this post is sponsored», «this is a guest post».
_STATEMENT = re.compile(
    r"\bthis\s+(?:is\s+an?\s+)?(?:sponsored|paid|guest|partner)\s+"
    r"(?:post|article|review|content|conversation)\b"
    r"|\bthis\s+(?:post|article|content|review|story|piece|giveaway|episode|video)\s+"
    r"(?:is|was|has\s+been)\s+(?:sponsored|paid\s+for|brought\s+to\s+you\s+by)\b",
    re.IGNORECASE,
)
#: Подпись первой строкой: «Sponsored post: …», «Guest post by …», «Advertorial».
#: После «post» нужен знак или «by»: «Guest post pitching: 10 tips» — тема.
_LEADING = re.compile(
    r"(?:sponsored|paid|guest|partner)\s+(?:post|content|article)\s*(?:[:|–—-]|by\b)"
    r"|sponsored\s+by\b|advertorial\b",
    re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"[.!?]")
_LD_SECTION = re.compile(r'"articleSection"\s*:\s*(\[[^\]]*\]|"[^"]*")')
_LD_DATE = re.compile(r'"datePublished"\s*:\s*"([^"]{8,40})"')
_QUOTED = re.compile(r'"([^"]*)"')
_ISO_DAY = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

#: Раньше этого года статей в вебе не бывает: дата старше — мусор разметки.
FIRST_YEAR = 1995


@dataclass(frozen=True, slots=True)
class PageFacts:
    """Снятое со страницы: чем статья помечена и когда вышла. `None` — не нашли."""

    label: str | None = None
    published: date | None = None


def _short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= MAX_LABEL_CHARS else text[: MAX_LABEL_CHARS - 1] + "…"


def _main_posts(tree: HTMLParser) -> list[Node]:
    """Обёртки статьи, в которой заголовок страницы.

    Блок «похожие статьи» тоже из `<article>` с рубриками — но заголовка
    страницы в нём нет. Без `<h1>` берётся `<article>`, если он один.
    """
    wrappers = [
        node
        for node in tree.css("article, .hentry, .type-post")
        if node.css_first("h1") is not None
    ]
    if wrappers:
        return wrappers
    articles = tree.css("article")
    return articles if len(articles) == 1 else []


def _rubric(tree: HTMLParser) -> str | None:
    """Рубрика или метка статьи в классах WordPress: `category-sponsored`, `tag-guest-post`."""
    nodes = _main_posts(tree)
    if tree.body is not None:
        nodes.append(tree.body)
    for node in nodes:
        for token in (node.attributes.get("class") or "").lower().split():
            kind, _, slug = token.partition("-")
            if kind in ("category", "tag") and slug and names_a_mark(slug):
                return f"рубрика {slug}"
    return None


def _ld_scripts(tree: HTMLParser) -> list[str]:
    return [node.text() or "" for node in tree.css('script[type="application/ld+json"]')]


def _section(tree: HTMLParser) -> str | None:
    """Раздел статьи в мета-данных: `article:section`, `articleSection` в JSON-LD."""
    for selector in SECTION_META:
        for node in tree.css(selector):
            value = (node.attributes.get("content") or "").strip()
            if value and names_a_mark(value):
                return f"раздел «{_short(value)}»"
    for script in _ld_scripts(tree):
        for match in _LD_SECTION.finditer(script):
            for value in _QUOTED.findall(match.group(1)):
                if value.strip() and names_a_mark(value):
                    return f"раздел «{_short(value)}»"
    return None


def _disclosure(text: str) -> str | None:
    """Фраза раскрытия в начале или в конце текста статьи."""
    head, tail = text[:HEAD_CHARS], text[-TAIL_CHARS:]
    leading = _LEADING.match(head.lstrip())
    if leading is not None:
        return f"«{_short(leading.group(0).strip(' :|–—-'))}»"
    for part in (head, tail):
        found = _STATEMENT.search(part)
        if found is not None:
            end = _SENTENCE_END.search(part, found.end())
            sentence = part[found.start() : end.start() if end else len(part)]
            return f"«{_short(sentence)}»"
    return None


def _day(value: str) -> date | None:
    """День из ISO-строки разметки. Невозможная дата — `None`, а не исключение."""
    found = _ISO_DAY.search(value)
    if found is None:
        return None
    try:
        day = date(int(found.group(1)), int(found.group(2)), int(found.group(3)))
    except ValueError as exc:
        logger.debug("дата статьи %r не разбирается: %s", value[:40], exc)
        return None
    # Раньше веба статей не бывает, позже завтрашнего — тоже (день запаса —
    # на часовой пояс сайта): такая дата — мусор разметки или отложенная публикация.
    if day.year < FIRST_YEAR or day > datetime.now(UTC).date() + timedelta(days=1):
        return None
    return day


def _published(tree: HTMLParser) -> date | None:
    for selector in DATE_META:
        for node in tree.css(selector):
            day = _day(node.attributes.get("content") or "")
            if day is not None:
                return day
    for script in _ld_scripts(tree):
        for match in _LD_DATE.finditer(script):
            day = _day(match.group(1))
            if day is not None:
                return day
    for post in _main_posts(tree):
        for node in post.css("time[datetime]"):
            day = _day(node.attributes.get("datetime") or "")
            if day is not None:
                return day
    return None


def read_page(html: str, article_text: str | None = None) -> PageFacts:
    """Пометка и дата статьи. `article_text` — уже выделенное тело (`article.py`).

    Порядок пометок — от того, что сайт написал в разметке, к тому, что
    мы прочли в тексте: рубрика и раздел — это решение сайта, фраза —
    наше чтение.
    """
    tree = HTMLParser(html)
    label = _rubric(tree) or _section(tree)
    if label is None and article_text:
        label = _disclosure(article_text)
    return PageFacts(label=label, published=_published(tree))
