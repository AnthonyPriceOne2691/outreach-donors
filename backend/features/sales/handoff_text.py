"""Тексты передачи лида: сообщение телемаркетологу и примечание к сделке в Kommo.

**Сообщение — ровно три строки** в формате, который просили:
«Новый лид / Эмейл Рассылка / Ссылка на сделку в коммо: …». Ссылки на сделку нет —
Kommo не подключён, не ответил или ответ потерян — третья строка несёт ссылку на
диалог в сервисе и пометку, что со сделкой: телемаркетолог не остаётся в тишине.

**Примечание — что есть в данных:** последнее письмо (только написанное человеком,
без цитаты нашего), сводка переписки числами, компания и сайт, гипотеза, ГЕО и язык,
ссылка на диалог. Услуг и пересказа переписки в данных нет: их принесёт агент
продаж (Ф3) — выдумывать их здесь нельзя.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backend.config import sales as cfg
from backend.features.sales.kommo import NewLead
from backend.features.sales.models import HandoffKommo

#: Первые две строки сообщения — дословно, третья — ссылка.
HEAD = ("Новый лид", "Эмейл Рассылка")

#: Сколько текста письма уходит в примечание: письмо целиком с цитатой не нужно.
TEXT_LIMIT = 3000

#: Что со сделкой, когда вместо неё — ссылка на диалог.
_WHY_NO_DEAL = {
    HandoffKommo.OFF: "",
    HandoffKommo.PENDING: " — сделка в Kommo не создана, повторяем",
    HandoffKommo.RETRY: " — сделка в Kommo не создана, повторяем",
    HandoffKommo.UNCONFIRMED: " — сделка в Kommo не подтверждена, проверяем",
    HandoffKommo.FAILED: " — сделка в Kommo не создана, разбираемся",
    HandoffKommo.DONE: "",
}


@dataclass(frozen=True, slots=True)
class Card:
    """Что известно о лиде и диалоге в момент передачи."""

    thread_id: int
    lead_id: int
    email: str
    name: str
    company: str
    site: str
    hypothesis: str
    country: str
    language: str
    #: Последний ответ человека в диалоге; `None` — ответа нет (передачу позвали руками).
    reply_id: int | None
    reply_at: datetime | None
    reply_subject: str
    reply_text: str
    #: Сводка переписки: писем от нас ушло, ответов человека пришло, когда ушло первое.
    sent: int
    replies: int
    first_sent_at: datetime | None

    @property
    def new_lead(self) -> NewLead:
        return NewLead(
            email=self.email,
            site=self.site,
            hypothesis=self.hypothesis,
            company=self.company,
            name=self.name,
        )


def dialog_link(thread_id: int) -> str:
    """Ссылка на диалог в сервисе; без адреса сервиса — номер и имя настройки."""
    if cfg.APP_URL:
        return f"{cfg.APP_URL}/threads/{thread_id}"
    return f"диалог №{thread_id} (SALES_APP_URL не задан)"


def message(deal_url: str | None, thread_id: int, kommo: HandoffKommo) -> str:
    """Три строки телемаркетологу: ссылка на сделку, а без неё — на диалог с пометкой."""
    if deal_url:
        link = f"Ссылка на сделку в коммо: {deal_url}"
    else:
        link = f"Ссылка на диалог: {dialog_link(thread_id)}{_WHY_NO_DEAL[kommo]}"
    return "\n".join((*HEAD, link))


def note(card: Card) -> str:
    """Примечание к сделке: последнее письмо и всё, что о лиде известно."""
    when = f" — {card.reply_at:%d.%m.%Y %H:%M} UTC" if card.reply_at else ""
    first = f"{card.first_sent_at:%d.%m.%Y}" if card.first_sent_at else "—"
    lines = [
        f"Ответ из рассылки{when}",
        f"От: {card.email}",
        f"Тема: {card.reply_subject or '—'}",
        "",
        card.reply_text[:TEXT_LIMIT] or "(текста ответа нет)",
        "",
        f"Переписка: писем от нас — {card.sent}, ответов — {card.replies}; первое письмо — {first}",
        f"Компания: {card.company or '—'} · {card.site}",
        f"Гипотеза: {card.hypothesis}",
        f"ГЕО: {card.country or '—'}; язык: {card.language or '—'}",
        f"Диалог в сервисе: {dialog_link(card.thread_id)}",
    ]
    return "\n".join(lines)
