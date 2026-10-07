"""Детерминированные проверки судьи продаж — чистые функции, первыми (Spec 3.3).

Судья-модель зовётся, только когда здесь чисто (`judge.py`): нарушение правила —
правка без трат на модель. Каждое нарушение — словами для писателя: петля правки
шва отдаёт их ему ключом `rewrite`, а человеку они видны в попытках черновика.

- **Числа и суммы** — только из записей базы брифа или письма собеседника.
  Цифры ссылок не в счёт: их проверяет правило ссылок.
- **Ссылки и адреса** — только из белого списка настроек отправителя (сайт,
  созвон, Telegram). Адрес почты в черновике тоже ссылка: письмо уходит в ту же
  переписку, и чужой адрес в нём — путь увести лида или оплату.
- **Призыв к действию** — ровно один, тот, что выбрал ход, и со ссылкой из
  настроек; ход без призыва (закрыть без давления) — ни одного. Призыв узнаётся
  по каналу: созвон, Telegram, «ответьте письмом»; в одном предложении со
  ссылкой созвона или Telegram «напишите» — тот же призыв, а не второй.
- **Язык** — как у письма собеседника, по алфавиту (`reading.language_of`).
- **Форма** — от двух до пяти предложений, без «!» и эмодзи.
- **Отсрочка** — уже сказанную в переписке не повторять: обещание выполняют.

Призыв по словам — эвристика: «мы не делаем холодных звонков» она прочтёт как
призыв на созвон. Ошибка в эту сторону стоит правки или человека, а не письма.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from backend.features.sales.agent import reading
from backend.features.sales.agent.facts import Context
from backend.features.sales.agent.moves import Cta

#: Предложений в письме: меньше — отписка, больше — уже не ответ на вопрос.
MIN_SENTENCES, MAX_SENTENCES = 2, 5

#: Каналы призыва — в этом порядке их называет нарушение.
CALL, TELEGRAM, REPLY = "call", "telegram", "reply"
_WORDS = {CALL: "на созвон", TELEGRAM: "в Telegram", REPLY: "ответить письмом"}
_CUES = {
    CALL: re.compile(
        r"созвон|позвон|\bзвон(?:ок|ка|ке|ку)\b|\bвстрет\w*|\bвстреч[уаи]\b|\bzoom\b|\bзум\b"
        r"|\bcalls?\b|\bmeeting\b|\bbook a\b",
        re.IGNORECASE,
    ),
    TELEGRAM: re.compile(r"телеграм|telegram|\btg\b", re.IGNORECASE),
    REPLY: re.compile(
        r"ответьте|напишите|дайте знать|сообщите|\breply (?:to|back|with|by)\b"
        r"|\b(?:just|please) reply\b|\bwrite back\b|\blet me know\b|\bdrop me a line\b",
        re.IGNORECASE,
    ),
}
#: Эмодзи и пиктограммы — кодами, а не знаками: вариант-селектор в исходнике не виден.
_EMOJI = re.compile("[\U0001f000-\U0001faff\u2600-\u27bf\u2b50\u2b55\ufe0f]")


def _named(channels: Iterable[str]) -> str:
    return ", ".join(_WORDS[channel] for channel in _WORDS if channel in set(channels))


def foreign_numbers(draft: str, *, incoming: str, known: Iterable[str]) -> list[str]:
    """Числа черновика, которых нет ни в записях базы, ни в письме собеседника."""
    allowed = reading.numbers_in("\n".join((*known, incoming)))
    amounts = reading.amounts_in(draft)
    found = []
    for value in sorted(reading.numbers_in(draft) - allowed):
        what, it = ("сумма", "её") if value in amounts else ("число", "его")
        found.append(
            f"{what} {value:f} не из базы и не из письма собеседника — "
            f"уберите {it} или возьмите из фактов"
        )
    return found


def foreign_links(draft: str, *, allowed: Iterable[str]) -> list[str]:
    """Ссылки и адреса черновика не из настроек отправителя."""
    white = {reading.normalized(link) for link in allowed}
    return [
        f"ссылка не из настроек отправителя: {link} — оставьте только сайт, созвон или "
        "Telegram из настроек"
        for link in dict.fromkeys(reading.links_in(draft))
        if reading.normalized(link) not in white
    ]


def channels(draft: str, context: Context) -> set[str]:
    """Куда черновик зовёт: каналы призыва по предложениям."""
    links = {
        reading.normalized(link): kind
        for kind, link in context.links.items()
        if kind in (CALL, TELEGRAM)
    }
    found: set[str] = set()
    for piece in reading.sentences(draft):
        here = {channel for channel, cue in _CUES.items() if cue.search(piece)}
        here |= {
            links[key] for key in map(reading.normalized, reading.links_in(piece)) if key in links
        }
        found |= here - {REPLY} if here & {CALL, TELEGRAM} else here
    return found


def _wanted(draft: str, found: set[str], channel: Cta, link: str) -> list[str]:
    """Ход зовёт: ровно один призыв, его канал и его ссылка из настроек."""
    want = _WORDS[channel.value]
    if not found:
        return [f"нет призыва — позовите {want} этой ссылкой: {link}"]
    if len(found) > 1:
        return [f"призывов {len(found)} ({_named(found)}) — нужен один: {want}"]
    if channel.value not in found:
        return [f"призыв не тот: {_named(found)} вместо {want}"]
    drafted = {reading.normalized(item) for item in reading.links_in(draft)}
    if reading.normalized(link) not in drafted:
        return [f"призыв без ссылки из настроек — нужна эта: {link}"]
    return []


def cta_problems(draft: str, context: Context) -> list[str]:
    """Призыв — ровно тот, что выбрал ход, и со ссылкой; ход без призыва — без него."""
    found = channels(draft, context)
    if context.cta is not None:
        return _wanted(draft, found, *context.cta)
    if found:
        return [f"ход без призыва, а черновик зовёт: {_named(found)} — уберите призыв"]
    return []


def language_problem(draft: str, *, incoming: str) -> list[str]:
    """Язык черновика — как у письма собеседника. Язык письма не определён — не здесь:
    такое письмо судья отдаёт человеку целиком (`judge.py`)."""
    letter, drafted = reading.language_of(incoming), reading.language_of(draft)
    if letter is None or drafted == letter:
        return []
    return [
        f"язык черновика — {drafted or 'не определён'}, а письма собеседника — {letter}: "
        "пишите на языке письма"
    ]


def form_problems(draft: str) -> list[str]:
    """От двух до пяти предложений, без «!» и эмодзи."""
    found = []
    count = sum(1 for piece in reading.sentences(draft) if reading.ended(piece))
    if not MIN_SENTENCES <= count <= MAX_SENTENCES:
        found.append(f"предложений {count}, нужно от {MIN_SENTENCES} до {MAX_SENTENCES}")
    if "!" in draft:
        found.append("восклицательный знак — пишите без «!»")
    if _EMOJI.search(draft):
        found.append("в черновике эмодзи — пишите без эмодзи")
    return found


def repeated_deferral(draft: str, *, deferred: tuple[str, ...]) -> list[str]:
    """Отсрочка уже была в переписке — второй раз не откладывать."""
    again = reading.deferrals(draft) if deferred else []
    if not again:
        return []
    return [
        f"отсрочка уже была: «{deferred[0]}» — не откладывайте снова («{again[0]}»): "
        "выполните обещание фактом из базы"
    ]


def violations(draft: str, *, incoming: str, context: Context) -> list[str]:
    """Все нарушения черновика словами — по порядку правил. Пусто — правила чисты."""
    return [
        *foreign_numbers(draft, incoming=incoming, known=context.kb.values()),
        *foreign_links(draft, allowed=context.links.values()),
        *cta_problems(draft, context),
        *language_problem(draft, incoming=incoming),
        *form_problems(draft),
        *repeated_deferral(draft, deferred=context.deferred),
    ]
