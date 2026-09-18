"""Перечисления домена.

Правило: значения добавляются, ничего не ломая. Код не должен падать на
незнакомом значении — только явно обрабатывать известные.
"""

from __future__ import annotations

from enum import StrEnum


class DonorStatus(StrEnum):
    """Вердикт по донору.

    UNCHECKED — не «не подходит»: Ahrefs не вернул данных, и это
    повод добрать позже, а не закрыть домен.
    """

    SUITABLE = "suitable"
    UNSUITABLE = "unsuitable"
    UNCHECKED = "unchecked"


class ContactSource(StrEnum):
    """Откуда взят адрес. Порядок = порядок удешевления."""

    PAGE = "page"  # страница контактов или about
    WHOIS = "whois"
    PROVIDER = "provider"  # платный сервис — только для остатка
    MANUAL = "manual"  # форма заполнена руками


class PageKind(StrEnum):
    """Что за страница отдала адрес. Определяет его вес.

    Адрес со страницы «write for us» или «advertise» ведёт к тому, кто
    называет цену; общий `info@` попадает в поддержку и умирает там
    (okf/contact-ladder.md). Порядок значений — порядок убывания веса.
    """

    MONEY = "money"  # write-for-us, advertise, guest-post
    CONTACT = "contact"  # contact, contact-us, press, support
    ABOUT = "about"  # about, team, imprint, masthead
    LEGAL = "legal"  # terms, privacy: там указан оператор сайта
    HOME = "home"  # главная и всё прочее


class ContactStatus(StrEnum):
    """Итог поиска контакта. Хранится с отметкой времени попытки —
    вместе они дают идемпотентность: повторно платный сервис не дёргаем."""

    FOUND = "found"
    NOT_FOUND = "not_found"
    FORM_ONLY = "form_only"
    NO_QUOTA = "no_quota"
    RATE_LIMITED = "rate_limited"
    ERROR = "error"


class SenderStatus(StrEnum):
    FREE = "free"
    WORKING = "working"
    PAUSED = "paused"


class Stage(StrEnum):
    """Этап продукта. У этапов разные домены отправки."""

    DONORS = "donors"  # Этап 1: запрос цены у донора
    ADVERTISERS = "advertisers"  # Этап 2: оффер рекламодателю


class RunStatus(StrEnum):
    ESTIMATING = "estimating"
    RUNNING = "running"
    DONE = "done"
    STOPPED = "stopped"


class MessageStatus(StrEnum):
    QUEUED = "queued"
    SENDING = "sending"
    SENT = "sent"
    DELIVERED = "delivered"
    BOUNCED = "bounced"
    STOPPED = "stopped"


class ThreadStatus(StrEnum):
    OPEN = "open"
    REPLIED = "replied"
    UNSUBSCRIBED = "unsubscribed"
    CLOSED = "closed"


class ReplyKind(StrEnum):
    """Вид входящего.

    Цепочку добивок останавливает только HUMAN и UNSUBSCRIBE. Автоответчик
    об отпуске ответом не считается — иначе половина цепочек оборвётся
    на «я в отпуске до понедельника».
    """

    HUMAN = "human"
    AUTO_REPLY = "auto_reply"
    BOUNCE = "bounce"
    UNSUBSCRIBE = "unsubscribe"


class SuppressionReason(StrEnum):
    UNSUBSCRIBED = "unsubscribed"
    COMPLAINED = "complained"
    SUPPLIER = "supplier"  # поставщик агентства
    MANUAL = "manual"


class UsageProvider(StrEnum):
    AHREFS = "ahrefs"
    SERP = "serp"
    LLM = "llm"
    EMAIL = "email"
