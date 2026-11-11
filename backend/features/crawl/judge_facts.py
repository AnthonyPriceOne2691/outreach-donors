"""Факты судьи рекламодателей: строгая форма ответа модели и проверка цитат кодом.

Модель отвечает на простые вопросы «да/нет» о ссылке и о домене (`prompts/advertiser_judge.md`): что
за анкор (денежные слова, поисковая фраза, имя), что ссылка
делает на странице (призыв, выбор, подборка, источник факта, «не по теме», «как пример», карточка
каталога, рекламное место, навигация, статья о домене, пресс-релиз по пометке), слова раскрытия
рекламы на любом языке и что за сайт у домена (продаёт своё, отправляет к другим, переадресация,
некоммерческий, издание, приметы партнёрского сайта). Совет ставит код по таблице правил
(`judge_rules.py`): правка таблицы пересчитывает совет по сохранённым фактам без модели. Признаки
вместо одной «роли ссылки» и одного «вида сайта» — по стенду 04.11.2026: крупный признак склеивал
разные случаи, мелкие модели читают согласнее, и правила по ним реже ошибаются.

**Форма — у провайдера** (`json_schema`, `strict`), и код всё равно сверяет её: подставная модель и
провайдер без строгой формы её не держат. Нет поля, лишнее поле, не тот тип, не JSON — ответ не
разобран (`Read.broken`): это не факты и не суд, модель спросят снова.

**Цитаты проверяет код** (`shared/quote.quote_holds`), каждую — в строках своей стороны: слова о
ссылке — в статье, о домене — на главной, раскрытие рекламы — в статье. Цитаты нет — признаки её
стороны не доказаны и считаются «нет»; признаки анкора от цитаты статьи не зависят: анкор — наша
строка над статьёй, модель только называет его вид. Цитата короче трёх слов засчитывается, только
если это строка целиком («Visit Website»): длиннее строки на странице нет. Цитата есть, а в тексте её
нет — модель её сочинила (`Read.missing`): по ней модель спрашивают второй раз, а не судят по
выдумке. Раскрытие — обычно одно-два слова («Реклама», «Anzeige»), и вхождение строкой нашло бы «Ad»
внутри «Address»: короткое раскрытие ищется целыми словами.

**Признаки кода** (`sponsored` — пометка ссылки, `same_owner` — имя донора на главной домена) модель
не спрашивают: их ставит проход (`with_code_facts`). **Исход второго круга** (`SECOND_KEYS`,
11.11.2026) — тоже поля кода, текстом: что решила вторая модель по той же паре, слова её правила и
кто она (`judge_second.py`). Модель их не видит и не возвращает; в строке суда они есть, только
когда второй круг был: строка без него — прежней формы и читается как раньше.

**Общий ответ модели — последним вопросом того же вызова** (`verdict`, стенд 05.11.2026): стоит ли
писать этому домену — «write», «skip» или «unclear». Сам совета он не ставит: код сверяет его с
таблицей (`judge_rules.advise`). Отдельным вызовом он на тех же парах не держался лучше, а стоил почти
половину токенов сверху. В строках суда до него этого поля нет: по ним совет ставит одна таблица.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from backend.features.crawl.judge_text import LABELS, JudgeText, holds
from backend.shared.quote import MIN_QUOTE_WORDS, quote_words, unlabeled
from backend.shared.text import normalize

logger = logging.getLogger(__name__)

#: Признаки анкора — по нашей строке анкора: цитата статьи им не нужна.
ANCHOR_FLAGS = ("money_anchor", "keyword_anchor", "brand_anchor")
#: Признаки ссылки на странице — стоят на цитате статьи (`link_quote`).
LINK_FLAGS = (
    "calls_to_buy",
    "visit_button",
    "recommends",
    "offer_list",
    "source_of_fact",
    "off_topic",
    "named_as_example",
    "directory_entry",
    "ad_slot",
    "site_navigation",
    "article_about_target",
    "press_release",
)
#: Признаки сайта домена — стоят на цитате главной (`target_quote`).
TARGET_FLAGS = (
    "target_sells",
    "target_refers",
    "target_redirect",
    "target_noncommercial",
    "target_media",
    "target_affiliate_signs",
)
FLAGS = (*ANCHOR_FLAGS, *LINK_FLAGS, *TARGET_FLAGS)
#: Что может сказать модель на общий вопрос.
VERDICTS = ("write", "skip", "unclear")
#: Ключи ответа в порядке подсказки — все обязательны, других нет.
KEYS = (
    *ANCHOR_FLAGS,
    *LINK_FLAGS,
    "disclosure",
    *TARGET_FLAGS,
    "verdict",
    "link_quote",
    "target_quote",
    "why",
)
#: Признаки кода — в строке суда рядом с фактами модели, у модели их не спрашивают.
CODE_FLAGS = ("sponsored", "same_owner")
#: Исход второго круга — поля кода текстом (шапка модуля): совет второй модели, слова его правила и
#: сама модель. Пусты — второго круга у строки не было.
SECOND_KEYS = ("second_advice", "second_words", "second_model")
#: Короче — раскрытие ищется целыми словами: вхождение строкой нашло бы его внутри другого слова.
WORDS_FOR_SUBSTRING = 3
#: Слова «почему» модели — для человека, не для кода: столько знаков, дальше срез.
WHY_CHARS = 300
#: Имя донора короче — не признак «того же владельца»: «news», «info» стоят на любой главной.
OWNER_NAME_CHARS = 5

_TEXT = {"type": "string"}
_VERDICT = {"type": "string", "enum": list(VERDICTS)}
#: Строгая форма ответа у провайдера.
SCHEMA: dict[str, Any] = {
    "name": "advertiser_facts",
    "schema": {
        "type": "object",
        "properties": {
            key: {"type": "boolean"} if key in FLAGS else _VERDICT if key == "verdict" else _TEXT
            for key in KEYS
        },
        "required": list(KEYS),
        "additionalProperties": False,
    },
}
#: Типы полей для своей сверки формы.
_TYPES: dict[str, type] = {key: bool if key in FLAGS else str for key in KEYS}


@dataclass(frozen=True, slots=True)
class LinkFacts:
    """Факты после проверки кодом: признаки «да/нет», раскрытие и цитаты. Цитата пуста — признаки
    её стороны «нет» (кроме признаков анкора); раскрытие — само цитата."""

    money_anchor: bool = False
    keyword_anchor: bool = False
    brand_anchor: bool = False
    calls_to_buy: bool = False
    visit_button: bool = False
    recommends: bool = False
    offer_list: bool = False
    source_of_fact: bool = False
    off_topic: bool = False
    named_as_example: bool = False
    directory_entry: bool = False
    ad_slot: bool = False
    site_navigation: bool = False
    article_about_target: bool = False
    press_release: bool = False
    disclosure: str = ""
    target_sells: bool = False
    target_refers: bool = False
    target_redirect: bool = False
    target_noncommercial: bool = False
    target_media: bool = False
    target_affiliate_signs: bool = False
    verdict: str = ""
    link_quote: str = ""
    target_quote: str = ""
    why: str = ""
    sponsored: bool = False
    same_owner: bool = False
    second_advice: str = ""
    second_words: str = ""
    second_model: str = ""

    @property
    def link_proven(self) -> bool:
        return bool(self.link_quote)

    @property
    def target_proven(self) -> bool:
        return bool(self.target_quote)

    def as_json(self) -> dict[str, Any]:
        """Для строки суда: читается обратно `from_json`. Исход второго круга пишется, только когда
        он был: строка без него — прежней формы."""
        body = dataclasses.asdict(self)
        if any(body[key] for key in SECOND_KEYS):
            return body
        return {key: value for key, value in body.items() if key not in SECOND_KEYS}

    @classmethod
    def from_json(cls, data: object) -> LinkFacts | None:
        """Факты из строки суда; не той формы (правили руками, старая версия фактов) — `None`, громко
        в лог: модель спросят снова, а не решат по обломку."""
        if not isinstance(data, dict):
            logger.warning("судья рекламодателей: факты строки суда не той формы")
            return None
        # Строка суда до общего ответа модели — без него: совет по ней ставит одна таблица.
        ours = (*CODE_FLAGS, *SECOND_KEYS)
        said = {"verdict": ""} | {k: v for k, v in data.items() if k not in ours}
        if _broken(said) is not None:
            logger.warning("судья рекламодателей: факты строки суда не той формы")
            return None
        code: dict[str, Any] = {key: bool(data.get(key)) for key in CODE_FLAGS}
        # Строка без второго круга — без его полей; не строка (правили руками) — как без него.
        again: dict[str, Any] = {
            key: found for key in SECOND_KEYS if isinstance(found := data.get(key), str)
        }
        return cls(**{key: said[key] for key in KEYS}, **code, **again)


def with_code_facts(
    facts: LinkFacts, *, sponsored: bool, donor_host: str, home: Sequence[str]
) -> LinkFacts:
    """Признаки кода к фактам модели: пометка `sponsored` ссылки и «тот же владелец» — имя донора
    (первая метка домена, от пяти знаков) отдельным словом на главной домена."""
    return dataclasses.replace(facts, sponsored=sponsored, same_owner=_same_owner(donor_host, home))


def _same_owner(donor_host: str, home: Sequence[str]) -> bool:
    name = donor_host.lower().removeprefix("www.").split(".")[0]
    if len(name) < OWNER_NAME_CHARS:
        return False
    text = " ".join(home).lower()
    return re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", text) is not None


def _broken(body: dict[str, Any]) -> str | None:
    """Почему объект — не ответ строгой формы, словами; `None` — форма та."""
    if missing := [key for key in KEYS if key not in body]:
        return f"в ответе модели нет поля «{missing[0]}»"
    if extra := sorted(key for key in body if key not in KEYS):
        return f"в ответе модели лишнее поле «{str(extra[0])[:40]}»"
    for key, kind in _TYPES.items():
        if not isinstance(body[key], kind):
            return f"в ответе модели поле «{key}» не того типа"
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
    if broken is None and body["verdict"] not in VERDICTS:
        broken = "в ответе модели поле «verdict» не из списка"
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
        """Цитата, найденная в строках своей стороны; пустая — пусто; короткая — только строка
        целиком («Visit Website»), иначе пусто."""
        words = _bare(raw)
        if not words:
            return ""
        if quote_words(words) < MIN_QUOTE_WORDS:
            wanted = _words_of(words)
            return words if wanted and any(_words_of(line) == wanted for line in lines) else ""
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
    link_quote = check.quote(form["link_quote"], text.article)
    target_quote = check.quote(form["target_quote"], text.home)
    # Признак без цитаты своей стороны не доказан — «нет»; признаки анкора цитаты статьи не ждут.
    flags = {key: form[key] for key in ANCHOR_FLAGS}
    flags |= {key: form[key] and bool(link_quote) for key in LINK_FLAGS}
    flags |= {key: form[key] and bool(target_quote) for key in TARGET_FLAGS}
    facts = LinkFacts(
        **flags,
        disclosure=check.disclosure(form["disclosure"]),
        verdict=form["verdict"],
        link_quote=link_quote,
        target_quote=target_quote,
        why=" ".join(form["why"].split())[:WHY_CHARS],
    )
    if check.missing:
        shown = check.missing[0][:120]
        return Read(None, f"цитата модели не нашлась в тексте: «{shown}»", tuple(check.missing))
    return Read(facts)
