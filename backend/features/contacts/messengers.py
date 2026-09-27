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
_TELEGRAM_RESERVED = frozenset(
    {
        "joinchat", "share", "socks", "proxy", "addstickers", "addtheme",
        "addemoji", "setlanguage", "confirmphone", "login", "iv", "bg",
        "telegram", "telegramtips", "durov",
    }
)  # fmt: skip

# Служебные пути vk.com: кнопки «поделиться» и виджеты стоят на каждой
# второй странице, и без этого списка ими заполнилась бы вся выборка.
_VK_RESERVED = frozenset(
    {
        "share", "share.php", "widget", "widget_comments.php", "widget_community.php",
        "js", "away.php", "feed", "wall", "video", "video_ext.php", "im", "login",
        "dev", "about", "blog", "press", "images", "doc",
    }
)  # fmt: skip

# Номер в ссылке. `%2B` и `%20` — это закодированные плюс и пробел: в href
# они встречаются чаще незакодированных, и без них половина ссылок WhatsApp
# прошла бы мимо. Разбирает их `_number`, здесь важно лишь не оборвать совпадение.
_NUMBER = r"((?:\+|%2B)?\d(?:[\d\s\-()]|%20){6,19})"

# Ссылки и схемы. `(?<![\w.-])` перед доменом обязателен: без него `t\.me`
# находится внутри `client.metrics`, и страница с аналитикой отдаёт
# «телеграм-ник» trics. Плюс `+хэш`-приглашения не проходят по классу
# символов — это тоже намеренно.
_EXPLICIT_RULES: tuple[tuple[re.Pattern[str], MessengerKind], ...] = (
    (re.compile(r"(?<![\w.-])t\.me/(?:s/)?(@?[A-Za-z0-9_]{4,32})", re.I), MessengerKind.TELEGRAM),
    (re.compile(r"(?<![\w.-])telegram\.me/(@?[A-Za-z0-9_]{4,32})", re.I), MessengerKind.TELEGRAM),
    (re.compile(r"(?<![\w.-])tlgrm\.ru/(@?[A-Za-z0-9_]{4,32})", re.I), MessengerKind.TELEGRAM),
    (re.compile(r"tg://resolve\?domain=([A-Za-z0-9_]{4,32})", re.I), MessengerKind.TELEGRAM),
    (re.compile(r"skype:([A-Za-z0-9._\-:]{3,64})", re.I), MessengerKind.SKYPE),
    (
        re.compile(r"(?<![\w.-])join\.skype\.com/(?:invite/)?([A-Za-z0-9]{4,64})", re.I),
        MessengerKind.SKYPE,
    ),
    (re.compile(rf"(?<![\w.-])wa\.me/{_NUMBER}", re.I), MessengerKind.WHATSAPP),
    (
        re.compile(rf"(?:api|web|chat)\.whatsapp\.com/send/?\?phone={_NUMBER}", re.I),
        MessengerKind.WHATSAPP,
    ),
    (re.compile(rf"whatsapp://send\?phone={_NUMBER}", re.I), MessengerKind.WHATSAPP),
    (
        re.compile(rf"viber://(?:chat|add|contact)\?number={_NUMBER}", re.I),
        MessengerKind.VIBER,
    ),
    (re.compile(r"(?<![\w.-])vk\.com/([A-Za-z0-9_.]{2,64})", re.I), MessengerKind.VK),
    (re.compile(rf"tel:{_NUMBER}", re.I), MessengerKind.PHONE),
)

# Канал, названный словами. `(?<![\w.@/\-])` перед `@` отсекает главный
# ложный источник — локальную часть адреса: в `info@site.com` собака
# стоит после буквы, и ником это не считается.
_GUESS_RULES: tuple[tuple[re.Pattern[str], MessengerKind], ...] = (
    (
        re.compile(r"(?<![\w.@/\-])@([A-Za-z][A-Za-z0-9_]{3,31})(?![\w.@\-])"),
        MessengerKind.TELEGRAM,
    ),
    (
        re.compile(
            r"(?:telegram|telegramm|tg|телеграм{1,2}|телега|тг)\s*[:：\-—–]?\s*"
            r"@?([A-Za-z][A-Za-z0-9_]{3,31})(?![\w.@\-])",
            re.I,
        ),
        MessengerKind.TELEGRAM,
    ),
    (
        re.compile(r"(?:skype|скайп)\s*[:：\-—–]?\s*([A-Za-z0-9._\-:]{3,64})", re.I),
        MessengerKind.SKYPE,
    ),
    # `live:.cid.xxx` — форма нового идентификатора Skype. Встречается и
    # без слова «skype» рядом, но сама по себе достаточно характерна.
    (re.compile(r"\b(live:[A-Za-z0-9._\-]{3,64})", re.I), MessengerKind.SKYPE),
    (
        re.compile(
            r"(?:whatsapp|whats app|watsapp|вотсап|ватсап|ватсапп)\s*[:：\-—–]?\s*"
            rf"{_NUMBER}",
            re.I,
        ),
        MessengerKind.WHATSAPP,
    ),
)


def _nick(value: str) -> str:
    return value.lstrip("@").strip().lower()


def _number(value: str) -> str:
    """Цифры номера.

    Процентные последовательности убираются ДО цифр, и порядок здесь не
    косметика: в `%2B79161234567` наивное «оставить цифры» превратило бы
    плюс в двойку, и номер уехал бы в таблицу на одну цифру длиннее.
    """
    return re.sub(r"\D", "", re.sub(r"%[0-9A-Fa-f]{2}", "", value))


def _skype_id(value: str) -> str:
    """Логин из `skype:login?chat`: хвост с действием отрезаем."""
    return value.split("?", maxsplit=1)[0].strip(" .,;:").lower()


_NORMALIZE: dict[MessengerKind, Callable[[str], str]] = {
    MessengerKind.TELEGRAM: _nick,
    MessengerKind.SKYPE: _skype_id,
    MessengerKind.WHATSAPP: _number,
    MessengerKind.VIBER: _number,
    MessengerKind.VK: _nick,
    MessengerKind.PHONE: _number,
}


def _valid_telegram(value: str) -> bool:
    return bool(_TELEGRAM_NICK_RE.fullmatch(value)) and value not in _TELEGRAM_RESERVED


def _valid_skype(value: str) -> bool:
    # Односложные слова вроде «me» или «chat» — не логины, а обрывки фразы.
    return len(value) >= 3 and not value.isdigit()


def _valid_number(value: str) -> bool:
    """Номер телефона, а не год, цена или идентификатор заказа.

    Семь цифр — нижняя граница местного номера, пятнадцать — верхняя
    по E.164. Всё, что короче, на странице чаще оказывается ценой.
    """
    return 7 <= len(value) <= 15


def _valid_vk(value: str) -> bool:
    return value not in _VK_RESERVED and not value.endswith(".php")


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


def _visible_text(html: str) -> str:
    return html_entities.unescape(HTMLParser(html).text(separator=" "))


def extract_handles(html: str) -> set[Handle]:
    """Каналы, названные ссылкой или URI-схемой.

    Ищем по всей разметке, а не по дереву ссылок: `tg://` и `whatsapp://`
    половина сайтов вешает на `onclick` или кладёт в JSON-LD, и обход
    только `a[href]` их не увидел бы.

    Пустой результат законен: на странице может не быть ни одного канала.
    """
    if not html:
        return set()
    return _collect(html_entities.unescape(html), _EXPLICIT_RULES, Trust.EXPLICIT)


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

    explicit = {(handle.kind, handle.value) for handle in extract_handles(html)}
    guesses = _collect(_visible_text(html), _GUESS_RULES, Trust.GUESSED)
    return {handle for handle in guesses if (handle.kind, handle.value) not in explicit}
