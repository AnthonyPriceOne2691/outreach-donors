"""Факты судьи рекламодателей: строгая форма ответа модели и проверка цитат кодом.

Модель не решает, писать ли домену, — она называет факты о ссылке и о домене (`prompts/
advertiser_judge.md`): что ссылка делает в статье (`link_role`), о домене ли статья целиком
(`article_about_target`), слова раскрытия рекламы на любом языке (`disclosure`) и что за сайт у
домена (`target_kind`). Совет ставит код по таблице правил (`judge_rules.py`): правка таблицы
пересчитывает совет по сохранённым фактам без модели. Так судья продаж развёл факты и политику
(#440): модель, ставящая полосу, смешивала одно с другим, а правка политики стоила токенов.

**Форма — у провайдера** (`json_schema`, `strict`), и код всё равно сверяет её: подставная модель и
провайдер без строгой формы её не держат. Нет поля, лишнее поле, не тот тип, значение не из списка,
не JSON — ответ не разобран (`Read.broken`): это не факты и не суд, модель спросят снова.

**Цитаты проверяет код** (`shared/quote.quote_holds`), каждую — в строках своей стороны: слова о
ссылке — в статье, о домене — на главной, раскрытие рекламы — в статье. Цитаты нет или она короче
трёх слов — факт её стороны не доказан, и таблица правил на него не опирается. Цитата есть, а в
тексте её нет — модель её сочинила (`Read.missing`): по ней модель спрашивают второй раз, а не
судят по выдумке. Раскрытие — обычно одно-два слова («Реклама», «Anzeige»), и вхождение строкой
нашло бы «Ad» внутри «Address»: короткое раскрытие ищется целыми словами.
"""

from __future__ import annotations

import json
import logging
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from backend.features.crawl.judge_text import LABELS, JudgeText, holds
from backend.shared.quote import MIN_QUOTE_WORDS, quote_words, unlabeled
from backend.shared.text import normalize

logger = logging.getLogger(__name__)

#: Что ссылка делает в статье: рекламирует, источник факта, строка подборки предложений, меню или
#: блок ссылок, не понять.
LINK_ROLES = ("promotes", "cites", "listing", "navigation", "unclear")
#: Что за сайт у домена: продаёт своё, посредник, издание, госорган или некоммерческая организация,
#: личная страница, не понять.
TARGET_KINDS = ("sells", "intermediary", "media", "public", "personal", "unclear")
#: Ключи ответа — все обязательны, других нет.
KEYS = (
    "link_role",
    "article_about_target",
    "disclosure",
    "target_kind",
    "link_quote",
    "target_quote",
    "why",
)
#: Короче — раскрытие ищется целыми словами: вхождение строкой нашло бы его внутри другого слова.
WORDS_FOR_SUBSTRING = 3
#: Слова «почему» модели — для человека, не для кода: столько знаков, дальше срез.
WHY_CHARS = 300

_TEXT = {"type": "string"}
#: Строгая форма ответа у провайдера.
SCHEMA: dict[str, Any] = {
    "name": "advertiser_facts",
    "schema": {
        "type": "object",
        "properties": {
            "link_role": {"type": "string", "enum": list(LINK_ROLES)},
            "article_about_target": {"type": "boolean"},
            "disclosure": _TEXT,
            "target_kind": {"type": "string", "enum": list(TARGET_KINDS)},
            "link_quote": _TEXT,
            "target_quote": _TEXT,
            "why": _TEXT,
        },
        "required": list(KEYS),
        "additionalProperties": False,
    },
}
#: Типы полей для своей сверки формы.
_TYPES: dict[str, type] = {
    "link_role": str,
    "article_about_target": bool,
    "disclosure": str,
    "target_kind": str,
    "link_quote": str,
    "target_quote": str,
    "why": str,
}


@dataclass(frozen=True, slots=True)
class LinkFacts:
    """Факты после проверки кодом. Цитата пуста — факт её стороны не доказан: роль ссылки и «статья
    о домене» стоят на `link_quote`, вид сайта — на `target_quote`; раскрытие — само цитата."""

    link_role: str
    article_about_target: bool
    disclosure: str
    target_kind: str
    link_quote: str
    target_quote: str
    why: str = ""

    @property
    def link_proven(self) -> bool:
        return bool(self.link_quote)

    @property
    def target_proven(self) -> bool:
        return bool(self.target_quote)

    def as_json(self) -> dict[str, Any]:
        """Для строки суда: читается обратно `from_json`."""
        return {
            "link_role": self.link_role,
            "article_about_target": self.article_about_target,
            "disclosure": self.disclosure,
            "target_kind": self.target_kind,
            "link_quote": self.link_quote,
            "target_quote": self.target_quote,
            "why": self.why,
        }

    @classmethod
    def from_json(cls, data: object) -> LinkFacts | None:
        """Факты из строки суда; не той формы (правили руками) — `None`, громко в лог: модель
        спросят снова, а не решат по обломку."""
        if not isinstance(data, dict) or _broken(data) is not None:
            logger.warning("судья рекламодателей: факты строки суда не той формы")
            return None
        return cls(**{key: data[key] for key in KEYS})


def _broken(body: dict[str, Any]) -> str | None:
    """Почему объект — не ответ строгой формы, словами; `None` — форма та."""
    if missing := [key for key in KEYS if key not in body]:
        return f"в ответе модели нет поля «{missing[0]}»"
    if extra := sorted(key for key in body if key not in KEYS):
        return f"в ответе модели лишнее поле «{str(extra[0])[:40]}»"
    for key, kind in _TYPES.items():
        if not isinstance(body[key], kind):
            return f"в ответе модели поле «{key}» не того типа"
    if body["link_role"] not in LINK_ROLES or body["target_kind"] not in TARGET_KINDS:
        return (
            f"ответ модели не из списка: {body['link_role'][:20]!r}, {body['target_kind'][:20]!r}"
        )
    return None


def form_of(content: str) -> dict[str, Any] | str:
    """Ответ модели → объект строгой формы; иначе — почему нет, словами."""
    try:
        body = json.loads(content)
    except ValueError:
        logger.info("судья рекламодателей: ответ модели не JSON", extra={"content": content[:120]})
        return "ответ модели не разобран"
    if not isinstance(body, dict):
        return "ответ модели не объект"
    broken = _broken(body)
    return broken if broken is not None else body


def _words_of(text: str) -> list[str]:
    """Слова для поиска целыми словами: без знаков при буквах и регистра — как у сверки цитаты."""
    split = unicodedata.normalize("NFD", normalize(text))
    bare = "".join(char for char in split if unicodedata.category(char) != "Mn").casefold()
    words, word = [], []
    for char in bare:
        if unicodedata.category(char)[0] in "LN":
            word.append(char)
        elif word:
            words.append("".join(word))
            word = []
    if word:
        words.append("".join(word))
    return words


def _whole_words(phrase: str, lines: Sequence[str]) -> bool:
    wanted = _words_of(phrase)
    if not wanted:
        return False
    for line in lines:
        words = _words_of(line)
        size = len(wanted)
        if any(words[at : at + size] == wanted for at in range(len(words) - size + 1)):
            return True
    return False


def _bare(raw: str) -> str:
    """Слова цитаты без наших подписей строк (`judge_text.LABELS`)."""
    return unlabeled(raw, LABELS).strip()


@dataclass(frozen=True, slots=True)
class Read:
    """Ответ модели, прочитанный по тексту: факты — или почему их нет. `missing` — цитаты, которых
    в тексте нет (модель их сочинила): по ним модель спрашивают второй раз. `broken` — ответ не
    строгой формы."""

    facts: LinkFacts | None
    failure: str = ""
    missing: tuple[str, ...] = ()
    broken: bool = False


class _Checked:
    """Цитаты по сторонам текста: доказанная — в факты, сочинённая — в `missing`."""

    def __init__(self, text: JudgeText) -> None:
        self.text = text
        self.missing: list[str] = []

    def quote(self, raw: str, lines: Sequence[str]) -> str:
        """Цитата, найденная в строках своей стороны; короткая или пустая — пусто."""
        words = _bare(raw)
        if not words or quote_words(words) < MIN_QUOTE_WORDS:
            return ""
        if holds(words, lines):
            return words
        self.missing.append(words)
        return ""

    def disclosure(self, raw: str) -> str:
        """Раскрытие рекламы — в строках статьи; короткое — целыми словами."""
        words = _bare(raw)
        if not words:
            return ""
        lines = self.text.article
        short = quote_words(words) < WORDS_FOR_SUBSTRING
        found = holds(words, lines) and (not short or _whole_words(words, lines))
        if found:
            return words
        self.missing.append(words)
        return ""


def read(content: str, text: JudgeText) -> Read:
    """Ответ модели → факты по тексту; не разобран — почему, сочинённые цитаты — как есть."""
    form = form_of(content)
    if isinstance(form, str):
        return Read(None, form, broken=True)
    check = _Checked(text)
    facts = LinkFacts(
        link_role=form["link_role"],
        article_about_target=form["article_about_target"],
        disclosure=check.disclosure(form["disclosure"]),
        target_kind=form["target_kind"],
        link_quote=check.quote(form["link_quote"], text.article),
        target_quote=check.quote(form["target_quote"], text.home),
        why=" ".join(form["why"].split())[:WHY_CHARS],
    )
    if check.missing:
        shown = check.missing[0][:120]
        return Read(None, f"цитата модели не нашлась в тексте: «{shown}»", tuple(check.missing))
    return Read(facts)
