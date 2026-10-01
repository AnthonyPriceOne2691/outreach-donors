"""Извлечение немейловых каналов связи: мессенджеры и телефон.

Почта — не единственный способ договориться о размещении, а на части
рынков и не главный: в Индонезии, Индии, Нигерии и СНГ вебмастер чаще
оставляет телеграм или WhatsApp, чем ящик. Скрейпер, который снимает
со страницы только адрес, на таком сайте уходит ни с чем, хотя канал
связи лежал на виду.

Телефон здесь тоже канал, а не отдельная сущность: номер со страницы
почти всегда и есть адрес в WhatsApp или Viber.

Как и `extract.py`, модуль ничего не фильтрует и не решает, годен ли
контакт: он отвечает на вопрос «что на странице написано». Два уровня
доверия разведены по функциям, а не по флагу, потому что звать их надо
по-разному:

    `extract_handles`        — сайт сам назвал канал ссылкой или схемой
                               (`t.me/nick`, `skype:login`, `tel:`);
    `extract_handle_guesses` — канал узнан из текста (`@nick`, `Skype: xxx`).

Вторая шумит неизбежно: `@nick` в тексте — это и телеграм, и аккаунт
в X, и просто обращение. Отказаться от неё нельзя, потому что именно
так канал и пишут в подвале: «пишите @webmaster». Поэтому доверие
остаётся за вызывающим — ровно как с `extract_obfuscated`.
"""

from __future__ import annotations

import html as html_entities
import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from selectolax.parser import HTMLParser

from backend.features.core.domain import PageKind


class MessengerKind(StrEnum):
    """Канал связи. Значение — то, что уедет в колонку таблицы."""

    TELEGRAM = "telegram"
    SKYPE = "skype"
    WHATSAPP = "whatsapp"
    VIBER = "viber"
    VK = "vk"
    PHONE = "phone"


class Trust(StrEnum):
    """Насколько канал назван прямо.

    `EXPLICIT` — ссылка или URI-схема: сайт сам объявил канал.
    `GUESSED` — узнано из текста, бывает ложным.
    """

    EXPLICIT = "explicit"
    GUESSED = "guessed"


@dataclass(frozen=True, slots=True)
class Handle:
    """Один канал связи со страницы.

    Хранит только то, что видно в разметке. Откуда страница взялась,
    какая ступень её принесла и что с каналом делать дальше — знает
    вызывающий, он же и дописывает эти поля при сохранении.
    """

    kind: MessengerKind
    value: str
    trust: Trust


# Ник телеграма: буква, дальше буквы, цифры и подчёркивания. Регистр не
# значит ничего, сравнение и хранение — в нижнем.
_TELEGRAM_NICK_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{3,31}")

# Служебные пути t.me. Это не ники: `joinchat` и `+хэш` — приглашения в
# закрытую группу, остальное — настройки клиента. Приглашение намеренно
# не берём: написать по нему нельзя, а в таблице оно выглядело бы контактом.
# Разделы с подпутём (`addlist/…`, `contact/…`) отсекает `_TG_TAIL`; здесь —
# те, что приходят и без него: `t.me/boost?c=…`.
_TELEGRAM_RESERVED = frozenset(
    {
        "joinchat", "share", "socks", "proxy", "addstickers", "addtheme",
        "addemoji", "setlanguage", "confirmphone", "login", "iv", "bg",
        "telegram", "telegramtips", "durov", "addlist", "boost", "contact",
    }
)  # fmt: skip

# Служебные пути vk.com: кнопки «поделиться» и виджеты стоят на каждой
# второй странице, и без этого списка ими заполнилась бы вся выборка.
# `rtrg` — пиксель ретаргетинга: картинка в `<noscript>` из стандартного кода.
_VK_RESERVED = frozenset(
    {
        "share", "share.php", "widget", "widget_comments.php", "widget_community.php",
        "js", "away.php", "feed", "wall", "video", "video_ext.php", "im", "login",
        "dev", "about", "blog", "press", "images", "doc", "rtrg",
    }
)  # fmt: skip

# Объект ВКонтакте с положительным владельцем: `photo1_2`, `wall12_34`. С
# отрицательным (`photo-1_2`) его отсекает `_VK_TAIL` по дефису, а этот по
# форме неотличим от имени — только по названию типа объекта в начале.
_VK_OBJECT_RE = re.compile(
    r"(?:wall|photo|video|audio|doc|note|poll|album|topic|board|market|product"
    r"|story|clip|app|podcast|write)s?\d+(?:_\d+)?"
)

# Разделитель внутри номера: пробел, но не перевод строки (в видимом тексте
# им кончается абзац), дефис любого начертания, скобка. `%2B` и `%20` —
# закодированные плюс и пробел: в href они чаще незакодированных, и без них
# половина ссылок WhatsApp прошла бы мимо. Разбирает их `_number`.
_NUMBER_SEP = r"(?:[^\S\n]|[-\u2010-\u2015\u2212()]|%20)"

# Номер — группы цифр через один–три разделителя: `+7 (916) 123 - 45 - 67`.
# Счёт по группам, а не по знакам: потолок в 19 знаков номер то обрезал на
# последней цифре, то доращивал соседней. Прогон берётся целиком, и длиннее
# номера он только при склейке — её отбросит `_valid_number`. Группа перед
# `:цифра` — часы работы («9:00»), не хвост номера. Атомарная группа не даёт
# отступить к обрезку, когда за прогоном `.цифра` — хвост, который не разобран.
_NUMBER = rf"((?>(?:[+\uff0b]|%2B)?\d+(?:{_NUMBER_SEP}{{1,3}}(?!\d+:\d)\d+)*))(?!\.\d)"

# Начало хоста. Без этой границы `t\.me` находится внутри `client.metrics`,
# и страница с аналитикой отдаёт «телеграм-ник» trics.
_HOST = r"(?<![\w.-])"

# Начало схемы. `skype:` и `tel:` законно стоят в начале значения атрибута,
# строки скрипта или слова в тексте — после кавычки, `=`, скобки, пробела или
# `>`. После буквы, точки, дефиса, `{`, `,` или `?` это уже код, а не адрес:
# `.fa-skype:before`, `{skype:t.skype}`, `n?skype:t.skype`, `/hotel:123`.
_SCHEME = r"(?<![^\s\"'`=(>])"

# Ник телеграма — последний сегмент пути. Дальше законны конец адреса, номер
# поста (`/123`) и история (`/s/5`); раздел с подпутём (`addlist/…`,
# `contact/…`, `invoice/…`) держит на месте ника имя раздела. Первая проверка
# не даёт обрезать путь длиннее ника до 32 знаков и выдать обрезок за ник.
_TG_TAIL = r"(?![\w-])(?!/(?!\d+(?![\w-])|s/\d)[\w@-])"

# Имя ВКонтакте — тоже последний сегмент. Дефис за ним — номер объекта
# (`photo-1_2`, `topic-1_2`, `market-1`), подпуть — раздел (`music/…`).
_VK_TAIL = r"(?![\w.-])(?!/[\w@-])"

# Ссылки и схемы. `+хэш`-приглашения телеграма не проходят по классу
# символов — это намеренно.
_EXPLICIT_RULES: tuple[tuple[re.Pattern[str], MessengerKind], ...] = (
    (
        re.compile(rf"{_HOST}(?:t|telegram)\.me/(?:s/)?(@?[A-Za-z0-9_]{{4,32}}){_TG_TAIL}", re.I),
        MessengerKind.TELEGRAM,
    ),
    (
        re.compile(rf"{_HOST}tlgrm\.ru/(@?[A-Za-z0-9_]{{4,32}}){_TG_TAIL}", re.I),
        MessengerKind.TELEGRAM,
    ),
    (
        re.compile(rf"{_SCHEME}tg://resolve\?domain=([A-Za-z0-9_]{{4,32}})(?![\w-])", re.I),
        MessengerKind.TELEGRAM,
    ),
    # Логин не обрезается: `skype:adsdesk@site.com` — не логин `adsdesk`.
    (
        re.compile(rf"{_SCHEME}skype:([A-Za-z0-9._\-:]{{3,64}})(?![\w.:@-])", re.I),
        MessengerKind.SKYPE,
    ),
    (
        re.compile(rf"{_HOST}join\.skype\.com/(?:invite/)?([A-Za-z0-9]{{4,64}})", re.I),
        MessengerKind.SKYPE,
    ),
    (re.compile(rf"{_HOST}wa\.me/{_NUMBER}", re.I), MessengerKind.WHATSAPP),
    (
        re.compile(rf"{_HOST}(?:api|web|chat)\.whatsapp\.com/send/?\?phone={_NUMBER}", re.I),
        MessengerKind.WHATSAPP,
    ),
    (re.compile(rf"{_SCHEME}whatsapp://send\?phone={_NUMBER}", re.I), MessengerKind.WHATSAPP),
    (
        re.compile(rf"{_SCHEME}viber://(?:chat|add|contact)\?number={_NUMBER}", re.I),
        MessengerKind.VIBER,
    ),
    (re.compile(rf"{_HOST}vk\.com/([A-Za-z0-9_.]{{2,64}}){_VK_TAIL}", re.I), MessengerKind.VK),
    (re.compile(rf"{_SCHEME}tel:{_NUMBER}", re.I), MessengerKind.PHONE),
)

# Разделитель за подписью канала. Дефис — только после пробела: «Telegram-chat»
# и «Skype-call» — составные слова, а не подпись с ником.
_LABEL_SEP = r"(?:[:：—–]|(?<=\s)-)"

# Конец ника: дальше не буква, не собака, не дефис и не продолжение адреса —
# `.` или `:` перед буквой или `/` (`https://…`, `site.com`). Точка в конце
# фразы ник не прячет: «пишите @adsdesk.» — законный ник.
_NICK_END = r"(?![\w@-]|[.:][\w/])"

# Канал, названный словами. `(?<![\w.@/\-])` перед `@` отсекает главный
# ложный источник — локальную часть адреса: в `info@site.com` собака
# стоит после буквы, и ником это не считается. Подпись — слово целиком:
# `tg` внутри «Next mtg: Friday» подписью не бывает.
_GUESS_RULES: tuple[tuple[re.Pattern[str], MessengerKind], ...] = (
    (
        re.compile(rf"(?<![\w.@/\-])@([A-Za-z][A-Za-z0-9_]{{3,31}}){_NICK_END}"),
        MessengerKind.TELEGRAM,
    ),
    (
        # Разделитель или собака ОБЯЗАТЕЛЬНЫ. Пока они были необязательны,
        # среди триггеров стоял короткий `tg`, и фраза «Наш tg media открыт»
        # отдавала ник `media`: любое слово после «tg» становилось ником.
        re.compile(
            r"\b(?:telegram|telegramm|tg|телеграм{1,2}|телега|тг)"
            rf"\s*(?:{_LABEL_SEP}\s*@?|@)([A-Za-z][A-Za-z0-9_]{{3,31}}){_NICK_END}",
            re.I,
        ),
        MessengerKind.TELEGRAM,
    ),
    # У Skype разделитель обязателен по той же причине: «Skype for Business»,
    # «via Skype and email» и подвал «Skype Telegram» отдавали ники `for`,
    # `and`, `telegram`. Логин не обрезается на собаке: `ads@site.com` — адрес.
    (
        re.compile(
            rf"\b(?:skype|скайп)\s*{_LABEL_SEP}\s*([A-Za-z0-9._\-:]{{3,64}})(?![\w.:@-])", re.I
        ),
        MessengerKind.SKYPE,
    ),
    # `live:.cid.xxx` — форма нового идентификатора Skype. Встречается и
    # без слова «skype» рядом, но сама по себе достаточно характерна.
    (re.compile(r"\b(live:[A-Za-z0-9._\-]{3,64})", re.I), MessengerKind.SKYPE),
    # Пробелы после разделителя — только ВМЕСТЕ с ним: `\s*[:]?\s*` делит
    # пробельный прогон между двумя `\s*` всеми способами, и на странице, где
    # за триггером идут тысячи пробелов без номера, откат квадратичный — 32 тыс.
    # пробелов держали цикл событий ~11 с. Смысл тот же, перебор линейный.
    (
        re.compile(
            r"(?:whatsapp|whats app|watsapp|вотсап|ватсап|ватсапп)\s*(?:[:：\-—–]\s*)?"
            rf"{_NUMBER}",
            re.I,
        ),
        MessengerKind.WHATSAPP,
    ),
)


def _nick(value: str) -> str:
    return value.lstrip("@").strip().lower()


def _number(value: str) -> str:
    """Цифры номера, латинские.

    Процентные последовательности убираются ДО цифр, и порядок здесь не
    косметика: в `%2B79161234567` наивное «оставить цифры» превратило бы
    плюс в двойку, и номер уехал бы в таблицу на одну цифру длиннее.
    Цифры другой письменности (`٧٩١`, `７９１`) `\\d` принимает, а в таблицу
    и в набор номера они обязаны уйти латиницей.
    """
    digits = re.sub(r"%[0-9A-Fa-f]{2}", "", value)
    return "".join(str(int(char)) for char in digits if char.isdecimal())


def _skype_id(value: str) -> str:
    """Логин из `skype:login?chat`: хвост с действием отрезаем."""
    return value.split("?", maxsplit=1)[0].strip(" .,;:").lower()


def _vk_name(value: str) -> str:
    """Имя ВКонтакте без точки в конце: `vk.com/mygroup.` — конец предложения."""
    return _nick(value).strip(".")


_NORMALIZE: dict[MessengerKind, Callable[[str], str]] = {
    MessengerKind.TELEGRAM: _nick,
    MessengerKind.SKYPE: _skype_id,
    MessengerKind.WHATSAPP: _number,
    MessengerKind.VIBER: _number,
    MessengerKind.VK: _vk_name,
    MessengerKind.PHONE: _number,
}


def _valid_telegram(value: str) -> bool:
    return bool(_TELEGRAM_NICK_RE.fullmatch(value)) and value not in _TELEGRAM_RESERVED


def _valid_skype(value: str) -> bool:
    # Имя в Skype — от шести знаков (правило самого Skype), так что обрывки
    # фразы вроде «me», «chat» или «call» логином не бывают.
    return len(value) >= 6 and not value.isdigit()


def _valid_number(value: str) -> bool:
    """Номер телефона, а не год, цена или идентификатор заказа.

    Семь цифр — нижняя граница местного номера, пятнадцать — верхняя
    по E.164. Всё, что короче, на странице чаще оказывается ценой.
    """
    return 7 <= len(value) <= 15


def _valid_vk(value: str) -> bool:
    return (
        len(value) >= 2
        and value not in _VK_RESERVED
        and not value.endswith(".php")
        and not _VK_OBJECT_RE.fullmatch(value)
    )


_VALID: dict[MessengerKind, Callable[[str], bool]] = {
    MessengerKind.TELEGRAM: _valid_telegram,
    MessengerKind.SKYPE: _valid_skype,
    MessengerKind.WHATSAPP: _valid_number,
    MessengerKind.VIBER: _valid_number,
    MessengerKind.VK: _valid_vk,
    MessengerKind.PHONE: _valid_number,
}


def _collect(
    text: str,
    rules: tuple[tuple[re.Pattern[str], MessengerKind], ...],
    trust: Trust,
) -> set[Handle]:
    found: set[Handle] = set()
    for pattern, kind in rules:
        for raw in pattern.findall(text):
            value = _NORMALIZE[kind](raw)
            if value and _VALID[kind](value):
                found.add(Handle(kind, value, trust))
    return found


#: Теги, содержимое которых человек на странице не видит. Убираются ДО
#: разбора текста: CSS-правила `@media` и `@keyframes` и ключи JSON-LD
#: (`"@type"`) иначе читаются как ники телеграма. Замер 27.09.2026 на живом
#: прогоне: из 9 977 «телеграмов» 8 661 оказались такими — то есть шум
#: заслонял находки почти в семь раз.
_UNREADABLE_TAGS = ["script", "style", "noscript", "template"]

#: Элементы, с которых на экране начинается новая строка. Между ними в тексте
#: перевод строки, и номер через него не продолжается: склеенные пробелом
#: абзацы отдавали номер вместе с часами работы из соседнего (`…45-67 9`).
_BLOCK_TAGS = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "caption", "dd", "details",
        "dialog", "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form",
        "h1", "h2", "h3", "h4", "h5", "h6", "header", "hgroup", "hr", "legend", "li",
        "main", "menu", "nav", "ol", "option", "p", "pre", "section", "summary", "table",
        "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
    }
)  # fmt: skip

#: Метка границы блока, пока пробелы не схлопнуты: перевод строки из
#: исходника — это пробел (так его показывает браузер), а не граница.
_BREAK = "\u2029"


def _visible_text(html: str) -> str:
    """Текст, как его видит человек: пробелы схлопнуты, блоки — с новой строки."""
    tree = HTMLParser(html)
    tree.strip_tags(_UNREADABLE_TAGS)
    blocks = [node for node in tree.root.traverse() if node.tag in _BLOCK_TAGS] if tree.root else []
    for node in blocks:
        node.insert_before(_BREAK)
        node.insert_after(_BREAK)
    text = re.sub(rf"[^\S{_BREAK}]+", " ", html_entities.unescape(tree.text(separator=" ")))
    return re.sub(rf" ?{_BREAK}[{_BREAK} ]*", "\n", text)


def _markup(html: str) -> str:
    """Разметка без стилей — то, где сайт может объявить канал.

    Стили убираются целиком: канала в CSS не бывает, а псевдокласс за именем
    класса (`.fa-skype:before`) по форме — схема `skype:`. Скрипты остаются:
    JSON-LD и настройки виджетов чата — законное место ссылки на канал, а код
    вокруг схем отсекают границы в правилах.
    """
    tree = HTMLParser(html)
    tree.strip_tags(["style"])
    return html_entities.unescape(tree.html or "")


def extract_handles(html: str) -> set[Handle]:
    """Каналы, названные ссылкой или URI-схемой.

    Ищем по всей разметке, а не по дереву ссылок: `tg://` и `whatsapp://`
    половина сайтов вешает на `onclick` или кладёт в JSON-LD, и обход
    только `a[href]` их не увидел бы.

    Пустой результат законен: на странице может не быть ни одного канала.
    """
    if not html:
        return set()
    return _collect(_markup(html), _EXPLICIT_RULES, Trust.EXPLICIT)


def extract_handle_guesses(html: str) -> set[Handle]:
    """Каналы, узнанные из текста: `@nick`, `Skype: login`, `WhatsApp: +7…`.

    Из результата вычитается всё, что уже нашлось ссылкой: иначе один и
    тот же ник вернулся бы дважды с разным доверием, и вызывающему
    пришлось бы сводить их самому.

    Приём шумный по своей природе — `@nick` в тексте бывает аккаунтом
    в X, а не телеграмом. Решение, писать ли по такому, за вызывающим.
    """
    if not html:
        return set()
    return _guesses_beyond(html, extract_handles(html))


def _guesses_beyond(html: str, explicit: set[Handle]) -> set[Handle]:
    """Догадки без того, что уже нашлось явно. Явные — доводом: разбор
    разметки не бесплатен, и `harvest_handles` не должен делать его дважды."""
    taken = {(handle.kind, handle.value) for handle in explicit}
    guesses = _collect(_visible_text(html), _GUESS_RULES, Trust.GUESSED)
    return {handle for handle in guesses if (handle.kind, handle.value) not in taken}


@dataclass(frozen=True, slots=True)
class FoundHandle:
    """Канал связи вместе с тем, откуда он взят.

    Отдельный тип, а не поля в `Handle`: извлечение отвечает на вопрос
    «что написано», а провенанс знает только тот, кто скачал страницу.
    Вид страницы сохраняется по той же причине, по какой он сохраняется
    у адреса: ник со страницы «advertise» ведёт к тому, кто называет
    цену, а из подвала — к кому попало.
    """

    kind: MessengerKind
    value: str
    trust: Trust
    page_kind: PageKind
    page_url: str | None = None


def harvest_handles(
    html: str, *, page_kind: PageKind, page_url: str | None = None
) -> set[FoundHandle]:
    """Снять со страницы все каналы связи и подписать их страницей.

    Догадки берутся наравне с явными ссылками — они отличимы по `trust`,
    и решение, писать ли по догадке, остаётся за тем, кто читает итог.
    Своего фильтра у каналов нет: у ника нет ни ролевой части, ни домена,
    по которым адрес отсеивают в `quality.py`, а проверить ник можно
    только попыткой написать.
    """
    explicit = extract_handles(html)
    found = explicit | (_guesses_beyond(html, explicit) if html else set())
    return {
        FoundHandle(
            kind=handle.kind,
            value=handle.value,
            trust=handle.trust,
            page_kind=page_kind,
            page_url=page_url,
        )
        for handle in found
    }
