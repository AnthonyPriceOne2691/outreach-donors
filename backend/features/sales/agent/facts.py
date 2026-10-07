"""Факты под ход — что агент продаж знает до письма, строками брифа.

Шов отдаёт писателю и судье от этапа только факты брифа строками
(`Brief.facts` → `Request.facts`, `GuardInput.facts`). Поэтому всё, что судье
продаж нужно сверх письма собеседника, едет здесь же — строками с метками:

- `[kb:<номер> <вид>] заголовок: текст` — записи базы под ход;
- `[move <ход>] что сделать` — ход по таблице (`moves.toml`);
- `[cta <вид>] ссылка` — куда позвать в этом письме: одна или ни одной;
- `[promised] <вид>` — что мы обещали прислать и ещё не прислали;
- `[deferred] фраза` — наша прежняя отсрочка: второй раз не откладывать;
- `[persona] имя — должность` — от чьего имени письмо;
- `[link <вид>] ссылка` — белый список ссылок из настроек отправителя;
- `[language] ru|en` — язык письма собеседника.

Писатель читает их как факты (метки знает промпт `reply.md`), судья — назад
этим же модулем (`read`): одна запись — одно чтение.

**Выборка без эмбеддингов** — база из десятков записей. Виды — каждому письму
(`always`), обещанные и под ход; запись — на языке письма, а нет записей вида на
нём — на любом (писатель переведёт); виды из `by_tags` сужаются до тегов письма,
если теги названы и нашлись. Записей и знаков — с потолком: бриф не документ.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from backend.features.sales.agent.moves import Cta, Move, Table, table
from backend.features.sales.agent.situation import Situation
from backend.features.sales.kb import Fact
from backend.features.sales.models import KbKind
from backend.features.sales.sender import Sender

#: Записей базы в брифе. Больше — уже не выборка под ход, а вся база.
MAX_FACTS = 20
#: Знаков текста одной записи: длиннее — начало и многоточие.
MAX_FACT_CHARS = 1500

#: Ссылки отправителя: вид в строке брифа → поле настроек.
LINK_FIELDS = {"website": "website", "call": "call_link", "telegram": "telegram"}


class Mark(StrEnum):
    KB = "kb"
    MOVE = "move"
    CTA = "cta"
    PROMISED = "promised"
    DEFERRED = "deferred"
    PERSONA = "persona"
    LINK = "link"
    LANGUAGE = "language"


#: Строка брифа: метка, номер записи, вид — и текст.
_LINE = re.compile(
    r"\[(?P<mark>[a-z]+)(?::(?P<id>\d+))?(?: (?P<arg>[a-z_]+))?\] (?P<text>.*)", re.DOTALL
)
_CTAS = frozenset(cta.value for cta in Cta)


@dataclass(frozen=True, slots=True)
class Selected:
    """Что бриф отдаёт шву строками — и что из этого нужно ему самому."""

    lines: tuple[str, ...]
    #: Номера записей базы в брифе — в `meta` черновика.
    kb_ids: tuple[int, ...]
    #: Куда зовёт письмо; `None` — никуда.
    cta: Cta | None
    #: Ход зовёт, а позвать некуда: ни одной ссылки из его списка в настройках.
    no_cta_link: bool


@dataclass(frozen=True, slots=True)
class Context:
    """Строки брифа, прочитанные назад, — то, что видит судья."""

    #: Номер записи → «заголовок: текст».
    kb: Mapping[int, str] = field(default_factory=dict)
    #: Вид ссылки → ссылка: белый список.
    links: Mapping[str, str] = field(default_factory=dict)
    cta: tuple[Cta, str] | None = None
    move: str | None = None
    deferred: tuple[str, ...] = ()
    language: str | None = None
    persona: str | None = None


def line(mark: Mark, text: str, arg: str = "", number: int | None = None) -> str:
    head = mark.value if number is None else f"{mark.value}:{number}"
    return f"[{head} {arg}] {text}" if arg else f"[{head}] {text}"


def _cut(text: str) -> str:
    plain = text.strip()
    return plain if len(plain) <= MAX_FACT_CHARS else plain[:MAX_FACT_CHARS].rstrip() + "…"


def _in_language(entry: Fact, language: str) -> bool:
    return entry.language == language or entry.language.startswith(f"{language}-")


def _kinds(rules: Table, move: Move, situation: Situation) -> list[KbKind]:
    """Виды по порядку: каждому письму, обещанные, под ход — без повторов."""
    return list(dict.fromkeys((*rules.always, *situation.promised, *move.kinds)))


def _of_kind(
    entries: Sequence[Fact], kind: KbKind, *, language: str, tags: Sequence[str], narrow: bool
) -> list[Fact]:
    found = [entry for entry in entries if entry.kind is kind]
    found = [entry for entry in found if _in_language(entry, language)] or found
    if narrow and tags:
        found = [entry for entry in found if set(entry.tags) & set(tags)] or found
    return found


def chosen(
    entries: Sequence[Fact], *, move: Move, situation: Situation, language: str
) -> list[Fact]:
    """Записи базы под ход — не больше `MAX_FACTS`."""
    rules = table()
    picked = [
        entry
        for kind in _kinds(rules, move, situation)
        for entry in _of_kind(
            entries,
            kind,
            language=language,
            tags=situation.tags,
            narrow=kind in rules.by_tags,
        )
    ]
    return picked[:MAX_FACTS]


def links_of(sender: Sender) -> dict[str, str]:
    """Ссылки, заданные в настройках отправителя: вид → ссылка."""
    found = {kind: sender.values.get(name) for kind, name in LINK_FIELDS.items()}
    return {kind: value for kind, value in found.items() if value}


def deferred_lines(deferred: Sequence[str]) -> list[str]:
    return [line(Mark.DEFERRED, phrase) for phrase in deferred]


def _persona(sender: Sender) -> list[str]:
    name, position = sender.values.get("sender_name"), sender.values.get("sender_position")
    if not name:
        return []
    return [line(Mark.PERSONA, f"{name} — {position}" if position else name)]


def select(
    entries: Sequence[Fact],
    *,
    move: Move,
    situation: Situation,
    language: str,
    sender: Sender,
    deferred: Sequence[str] = (),
) -> Selected:
    """Строки брифа под ход: что сделать, куда позвать, записи базы, персона, ссылки."""
    found = chosen(entries, move=move, situation=situation, language=language)
    links = links_of(sender)
    cta = next((option for option in move.cta if option.value in links), None)
    lines = [
        line(Mark.MOVE, move.does, move.name),
        *([line(Mark.CTA, links[cta.value], cta.value)] if cta is not None else []),
        *(line(Mark.PROMISED, kind.value) for kind in situation.promised),
        *deferred_lines(deferred),
        *(
            line(Mark.KB, f"{entry.title}: {_cut(entry.text)}", entry.kind.value, entry.id)
            for entry in found
        ),
        *_persona(sender),
        *(line(Mark.LINK, value, kind) for kind, value in links.items()),
        line(Mark.LANGUAGE, language),
    ]
    return Selected(
        lines=tuple(lines),
        kb_ids=tuple(entry.id for entry in found),
        cta=cta,
        no_cta_link=bool(move.cta) and cta is None,
    )


@dataclass(slots=True)
class _Seen:
    """Строки брифа по одной — в то, что увидит судья."""

    kb: dict[int, str] = field(default_factory=dict)
    links: dict[str, str] = field(default_factory=dict)
    cta: tuple[Cta, str] | None = None
    move: str | None = None
    deferred: list[str] = field(default_factory=list)
    language: str | None = None
    persona: str | None = None

    def take(self, mark: str, number: str | None, arg: str, text: str) -> None:
        if mark == Mark.KB and number is not None:
            self.kb[int(number)] = text
        elif mark == Mark.LINK and arg:
            self.links[arg] = text
        elif mark == Mark.CTA and arg in _CTAS:
            self.cta = (Cta(arg), text)
        else:
            self._note(mark, arg, text)

    def _note(self, mark: str, arg: str, text: str) -> None:
        if mark == Mark.MOVE:
            self.move = arg or None
        elif mark == Mark.DEFERRED:
            self.deferred.append(text)
        elif mark == Mark.LANGUAGE:
            self.language = text.strip() or None
        elif mark == Mark.PERSONA:
            self.persona = text.split(" — ", 1)[0].strip() or None

    def context(self) -> Context:
        return Context(
            kb=self.kb,
            links=self.links,
            cta=self.cta,
            move=self.move,
            deferred=tuple(self.deferred),
            language=self.language,
            persona=self.persona,
        )


def read(lines: Sequence[str]) -> Context:
    """Строки брифа → то, что видит судья. Строки без метки не читаются."""
    seen = _Seen()
    for raw in lines:
        found = _LINE.fullmatch(raw)
        if found is not None:
            seen.take(found["mark"], found["id"], found["arg"] or "", found["text"])
    return seen.context()
