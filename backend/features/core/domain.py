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
    BLOCKED = "blocked"  # учётку у провайдера закрыли: повтор не поможет
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
    """Состояние прогона. `QUEUED` — не украшение списка: строка прогона
    заводится нажатием, а не задачей, иначе между нажатием и первым
    платным запросом сервис не показывает ничего и выглядит сломанным."""

    QUEUED = "queued"
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


class UserRole(StrEnum):
    """Роль сотрудника. Ролей две, и этого хватает.

    Права проверяются не по роли, а по именованному действию: роль —
    это набор действий по умолчанию, а не место в коде, где стоит
    проверка. Появится третья роль — добавится строкой в матрицу.
    """

    ADMIN = "admin"  # всё, включая учётки и права
    OPERATOR = "operator"  # работа с базой и прогонами


class Permission(StrEnum):
    """Именованные действия. Проверка прав ссылается на них, а не на роль.

    Иначе через полгода на вопрос «что может оператор» отвечают чтением
    всех обработчиков подряд.
    """

    VIEW = "view"  # смотреть базу, прогоны, переписку
    RUN = "run"  # запускать прогон — это трата юнитов
    SETTINGS = "settings"  # править пороги
    PRICES = "prices"  # подтверждать и править разобранные цены
    SEND = "send"  # отправлять письма — отдельное право, см. permissions.py
    SENDERS = "senders"  # домены и ящики рассылки: включать и выключать
    USERS = "users"  # заводить учётки и выдавать права


class AuditAction(StrEnum):
    """Что попадает в журнал: вход и всё, что меняет состояние или тратит
    деньги. Чтение не пишется — оно утопило бы журнал."""

    LOGIN = "login"
    LOGIN_FAILED = "login_failed"
    PASSWORD_CHANGED = "password_changed"  # noqa: S105 — это название события, не пароль
    USER_CREATED = "user_created"
    USER_UPDATED = "user_updated"
    RUN_STARTED = "run_started"
    THRESHOLDS_CHANGED = "thresholds_changed"
    LETTER_SENT = "letter_sent"
    PRICE_REVIEWED = "price_reviewed"
    # Адрес, внесённый человеком из ручной очереди форм или с карточки
    # донора, или закрытие донора без адреса: все решения принимает
    # человек, и все меняют, кому уйдёт письмо.
    CONTACT_ADDED = "contact_added"
    # Адрес донора удалён человеком с карточки. Удаляется только адрес,
    # которому не писали, но и он менял, кому уйдёт письмо.
    CONTACT_REMOVED = "contact_removed"
    # Решение человека по пограничному кандидату в рекламодатели:
    # оно меняет, кому уйдёт письмо, и именно по нему потом считается,
    # как часто ошибается скоринг.
    ADVERTISER_REVIEWED = "advertiser_reviewed"
    # Решение человека о типе сайта на экране отбора: оно меняет, кому
    # уйдёт письмо, и по нему считается, как часто ошибается судья.
    SITE_REVIEWED = "site_reviewed"
    SUPPRESSION_ADDED = "suppression_added"
    # Снятие записи об отписке — единственное действие, у которого причина
    # обязательна: это разрешение написать тому, кто просил не писать.
    SUPPRESSION_REMOVED = "suppression_removed"
    # Ответ рекламодателя взят в работу: лид перестаёт ждать человека,
    # и по журналу видно, кто его ведёт.
    LEAD_TAKEN = "lead_taken"


class UsageProvider(StrEnum):
    AHREFS = "ahrefs"
    SERP = "serp"
    LLM = "llm"
    EMAIL = "email"


class CrawlOutcome(StrEnum):
    """Чем кончился обход донора. Пять исходов — `docs/CRAWL.md`."""

    OK = "ok"  # взяли всё, что просили: список кончился или кончился потолок
    PARTIAL = "partial"  # что-то взяли, но упёрлись в срок, попытки или здоровье
    FORBIDDEN = "forbidden"  # robots.txt запрещает: откладываем человеку
    BLOCKED = "blocked"  # сайт закрылся: нужен следующий уровень каскада
    FAILED = "failed"  # не смогли начать: главная или robots не дались


class StopReason(StrEnum):
    """Почему обход закончился. Отличает «всё обошли» от «упёрлись»."""

    EXHAUSTED = "exhausted"  # страницы кончились — обошли всё, что было
    MAX_PAGES = "max_pages"
    MAX_ATTEMPTS = "max_attempts"
    TIMEOUT = "timeout"
    UNHEALTHY = "unhealthy"  # доля отказов выше потолка
    ROBOTS = "robots"
    NO_START = "no_start"  # главная не открылась ни в одном виде


class Verdict(StrEnum):
    """Что делать с кандидатом в рекламодатели. Четыре исхода, а не два.

    `PENDING` существует потому, что допуск по ложным рекламодателям —
    десять процентов, и пограничные случаи смотрит человек, а не порог.
    `BLOCKED` отделён от `SKIPPED`: первое — решение списка «кому
    не пишем», второе — решение баллов, и спорить с порогом там,
    где спорить не о чем, значит терять время на каждом разборе.
    """

    BOUGHT = "bought"
    PENDING = "pending"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
