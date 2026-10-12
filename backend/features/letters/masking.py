"""Маскирование перед отправкой в модель и возврат после.

Требование буквально: «уходит текст письма и текст ответа; адреса
и имена заменяются метками до отправки и возвращаются после». Ответ
владельца задачи был «желательно не передавать» — маскирование делает это
и уникализации не мешает: модель переписывает фразу вокруг метки так же,
как вокруг адреса.

**Метка нумерованная, а не одинаковая.** Два разных адреса под одной
меткой вернулись бы одним и тем же — и письмо ушло бы не туда, куда
собиралось. Метка выглядит инородно (`[address 1]`, `[name 1]`) нарочно:
модель не принимает её за часть текста и оставляет на месте.

**Не вернувшаяся метка — поломка, а не мелочь.** Если модель метку
съела, письмо уйдёт с дырой на месте адреса. Поэтому возврат проверяет,
что вернулись все, и говорит, какие пропали.

**Имена — те, что мы знаем** (12.10.2026, сверка ТЗ: до неё уходили только адреса).
Их называет вызывающий (`names`): наше имя отправителя, имя из «От кого» у ответа.
Имя в тексте по словарю не узнать — «Mark», «Will», «Grant» и имена, и слова, —
поэтому прячется только названное: целиком в любом регистре («anna petrova»)
и по словам — с заглавной буквы в любом регистре дальше («Hi Anna», «ANNA», «Best,
Petrova»). Со строчной слово не прячется: «will», «mark», «grant» — слова языка.
Слово имени меньше двух букв, слово-роль и слово самого разбора — «Team», «Sales»,
«Guest», «Post», «Price», «Wise» — по отдельности не прячутся: «Guest Posts» в «От
кого» — не человек, а метка на месте «guest post» отняла бы у модели то, что она
разбирает. Имя-домен («Example.com», «example.co.uk») не прячется вовсе: по имени
сайта модель находит его строку в прайсе на несколько сайтов; «J.Smith» и «Dr.Smith» —
имена, а не домены (список публичных суффиксов, `donors/host.py`).

**Без имён — как было.** `mask(text)` прячет только адреса: продажи и агент зовут
его по-прежнему, и их поведение не изменилось.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from backend.features.donors.host import normalize_host

#: Адрес в тексте. Нарочно шире, чем проверка адреса при сборе контактов:
#: здесь задача не отобрать годные, а не выпустить наружу ни одного.
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+", re.UNICODE)

_ADDRESS = "address"
_NAME = "name"
_LABEL = "[{kind} {number}]"
#: Метка любого вида: по ней возврат узнаёт пропавшие и чужие.
_LABEL_RE = re.compile(r"\[(?:address|name) \d+\]")

#: Слово имени: буквы, внутри — дефис или апостроф («O'Brien», «Jean-Luc»); букв —
#: не меньше двух (`_name_word`): инициал «J» нашёлся бы в тексте где угодно.
_NAME_WORD = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*")
#: Слова, из которых складывается имя в «От кого»; разделители — всё прочее.
_TOKENS = re.compile(r"[^\W\d_](?:[\w'’-]*\w)?")
#: Слитное через точку: «techblog.example.test». Строчными — домен, даже если его зоны
#: нет в списке суффиксов (`_domain_like`).
_DOTTED = re.compile(r"[\w-]+(?:\.[\w-]+)+")
#: Граница имени в тексте: не часть слова, адреса или домена — «anna»
#: в «anna-blog.example.test» и «example» в «example.com» остаются на месте.
_BEFORE = r"(?<![\w@.-])"
_AFTER = r"(?![\w@-]|\.\w)"

#: Слова, которые стоят в имени отправителя, но не имя: роль, отдел, вид бизнеса,
#: служебные — и слова самого разбора: продукт, пометка, цена, оплата. Имя только
#: из них («Editorial Team», «Guest Posts») не прячется вовсе, в имени человека
#: они не прячутся по отдельности («Anna | Sales» прячет «Anna»).
_NOT_NAMES = frozenset(
    {
        # роль, отдел, вид бизнеса
        *("team", "editor", "editors", "editorial", "sales", "marketing", "support"),
        *("admin", "administrator", "info", "office", "contact", "contacts", "manager"),
        *("department", "desk", "staff", "press", "service", "services", "customer"),
        *("care", "help", "partnership", "partnerships", "partner", "partners"),
        *("business", "advertising", "ads", "media", "news", "blog", "magazine"),
        *("journal", "agency", "group", "company", "digital", "online", "web"),
        *("website", "site", "seo", "content", "outreach", "pr", "hr", "redaktion"),
        # служебные
        *("the", "and", "of", "from", "at", "by", "for", "via", "mr", "mrs", "ms", "dr"),
        *("ltd", "llc", "inc", "gmbh", "corp", "co", "name", "address", "noreply"),
        # слова разбора
        *("guest", "post", "posts", "posting", "article", "articles", "sponsored"),
        *("link", "links", "price", "prices", "pricing", "rate", "rates", "paypal"),
        *("wise", "payoneer", "skrill", "crypto", "card", "bank", "transfer", "invoice"),
        *("dofollow", "nofollow", "free"),
    }
)


class UnmaskError(ValueError):
    """Метка не вернулась из модели — письмо собрать нельзя."""


@dataclass(slots=True)
class Masked:
    """Замаскированный текст и словарь для возврата."""

    text: str
    labels: dict[str, str] = field(default_factory=dict)


def _tokens(name: str) -> tuple[str, ...]:
    return tuple(_TOKENS.findall(name))


def _domain_like(name: str) -> bool:
    """Есть ли в имени домен: зона из списка публичных суффиксов («Example.com»,
    «Example.co.uk») — или слитное через точку строчными («donor.example.test»).
    «J.Smith» и «Dr.Smith» — имена: зоны «smith» в списке нет, и пишут их с заглавной."""
    for chunk in name.split():
        part = chunk.strip(".,;:!?()[]<>\"'«»|")
        if not _DOTTED.fullmatch(part):
            continue
        if normalize_host(part) or part == part.lower():
            return True
    return False


def _a_person(name: str, tokens: tuple[str, ...]) -> bool:
    """Похоже ли названное на имя: не адрес, не домен, не одни слова-роли."""
    if not tokens or "@" in name or _domain_like(name):
        return False
    return any(token.casefold() not in _NOT_NAMES for token in tokens)


def _name_word(token: str, *, alone: bool) -> bool:
    """Прятать ли слово имени по отдельности: имя целиком — всегда, слово
    составного имени — если написано с большой буквы, как пишут имена."""
    if not _NAME_WORD.fullmatch(token) or token.casefold() in _NOT_NAMES:
        return False
    if sum(char.isalpha() for char in token) < 2:
        return False
    return alone or token[0].isupper()


def _spelled(word: str) -> str:
    """Слово имени в тексте: с заглавной буквы, дальше — в любом регистре («Anna»,
    «ANNA»), и как написано в имени, если в имени оно со строчной («anna»)."""
    capital = re.escape(word[0].upper())
    rest = f"(?i:{re.escape(word[1:])})" if len(word) > 1 else ""
    written = "" if word[0].isupper() else f"{re.escape(word)}|"
    return f"{written}{capital}{rest}"


def _name_forms(names: Iterable[str]) -> tuple[list[str], list[str]]:
    """Что прятать: имена целиком (из двух слов и больше) и отдельные слова —
    длинные вперёд, чтобы «Anna Petrova» ушла одной меткой раньше, чем «Anna»."""
    phrases: set[tuple[str, ...]] = set()
    words: set[str] = set()
    for name in names:
        tokens = _tokens(name)
        if not _a_person(name, tokens):
            continue
        if len(tokens) > 1:
            phrases.add(tokens)
        words.update(token for token in tokens if _name_word(token, alone=len(tokens) == 1))
    by_length = sorted(phrases, key=lambda tokens: (-len(" ".join(tokens)), tokens))
    return (
        [r"\W+".join(map(re.escape, tokens)) for tokens in by_length],
        [_spelled(word) for word in sorted(words, key=lambda word: (-len(word), word))],
    )


def _pattern(names: Iterable[str]) -> re.Pattern[str]:
    """Адрес — первым: «anna.petrova@site.test» уходит меткой адреса целиком, а не
    именем с хвостом. Имя целиком — без оглядки на регистр, слово — как написано."""
    phrases, words = _name_forms(names)
    alternatives = [f"(?P<{_ADDRESS}>{_EMAIL_RE.pattern})"]
    spelled = [f"(?i:{'|'.join(phrases)})"] if phrases else []
    if spelled or words:
        alternatives.append(f"(?P<{_NAME}>{_BEFORE}(?:{'|'.join(spelled + words)}){_AFTER})")
    return re.compile("|".join(alternatives), re.UNICODE)


class Masker:
    """Метки на несколько текстов одним словарём: адрес или имя, встреченные
    в письме, во вложении и в теме, получают одну метку, и модель видит, что это
    одно и то же. `labels` — метка → что под ней, для возврата."""

    def __init__(self, names: Iterable[str] = ()) -> None:
        self.labels: dict[str, str] = {}
        self._known: dict[str, str] = {}
        self._counts: dict[str, int] = {}
        self._pattern = _pattern(names)

    def __call__(self, text: str) -> str:
        """Текст с метками вместо адресов и имён."""
        return self._pattern.sub(self._label, text)

    def _label(self, match: re.Match[str]) -> str:
        found = match.group(0)
        if found not in self._known:
            kind = _ADDRESS if match.lastgroup == _ADDRESS else _NAME
            self._counts[kind] = self._counts.get(kind, 0) + 1
            label = _LABEL.format(kind=kind, number=self._counts[kind])
            self._known[found] = label
            self.labels[label] = found
        return self._known[found]


def mask(text: str, *, names: Iterable[str] = ()) -> Masked:
    """Заменить адреса метками — и имена из `names`, если их назвали."""
    masker = Masker(names)
    return Masked(text=masker(text), labels=masker.labels)


def unmask(text: str, labels: dict[str, str]) -> str:
    """Вернуть адреса и имена на место. Пропавшая метка — отказ."""
    result = text
    for label, address in labels.items():
        result = result.replace(label, address)

    lost = sorted({found.group(0) for found in _LABEL_RE.finditer(result)})
    if lost:
        raise UnmaskError(
            f"Модель вернула метки, которых мы не выдавали: {', '.join(lost)} — "
            "письмо собрать нельзя, зона остаётся шаблонной"
        )

    missing = [label for label in labels if label not in text]
    if missing:
        raise UnmaskError(
            f"Модель потеряла метки: {', '.join(missing)} — "
            "на их месте в письме была бы дыра, зона остаётся шаблонной"
        )
    return result


def restore(text: str, labels: dict[str, str]) -> str:
    """Вернуть адреса и имена туда, где модель оставила метки, — не требуя всех.

    `unmask` требует каждую выданную метку: зона письма по шаблону обязана
    держать свои адреса. Ответу собеседника все адреса переписки не нужны,
    и пропавшая метка здесь — норма. Чужая метка — по-прежнему отказ: адреса,
    которого мы не выдавали, в письме быть не может.
    """
    result = text
    for label, address in labels.items():
        result = result.replace(label, address)
    unknown = sorted({found.group(0) for found in _LABEL_RE.finditer(result)})
    if unknown:
        raise UnmaskError(
            f"Модель вернула метки, которых мы не выдавали: {', '.join(unknown)} — "
            "ни адреса, ни имени на их месте нет"
        )
    return result


def leaked(text: str, *, names: Iterable[str] = ()) -> str | None:
    """Адрес или названное имя, оставшиеся в тексте после маскирования. `None` — чисто.

    Проверка самой маскировки: она стоит перед вызовом модели, потому что
    правило «наружу не уходит ни один адрес» нельзя проверить после того,
    как запрос уже отправлен.
    """
    found = _pattern(names).search(text)
    return found.group(0) if found is not None else None
