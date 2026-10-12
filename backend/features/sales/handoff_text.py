"""Тексты передачи лида: сообщение телемаркетологу и примечание к сделке в Kommo.

**Сообщение — ровно три строки** в формате, который просили:
«Новый лид / Эмейл Рассылка / Ссылка на сделку в коммо: …». Ссылки на сделку нет —
Kommo не подключён, не ответил или ответ потерян — третья строка несёт ссылку на
диалог в сервисе и пометку, что со сделкой: телемаркетолог не остаётся в тишине.
Формат «Новый лид» — только для нового лида: о новом письме лида, уже переданного,
— те же три строки с первой «Лид написал снова» (`AGAIN`).

**Примечание — что есть в данных:** последнее письмо (только написанное человеком,
без цитаты нашего), сводка переписки числами, компания и сайт, проект и услуги — что
сказал о них разбор ответов дословно из писем и по базе знаний (`handoff_card.told`), —
гипотеза, ГЕО и язык, ссылка на диалог. Не названо — так и написано: выдумывать нельзя.

**«От:» — адрес, с которого письмо пришло.** Первое письмо спрашивает, кто у компании отвечает
за маркетинг, и ответить может коллега лида со своего адреса: «От: <адрес лида>» отправил бы
телемаркетолога не к тому человеку (ревью стыков, круг 2). Не адрес лида — так и сказано.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backend.config import sales as cfg
from backend.features.sales.kommo import NewLead
from backend.features.sales.models import HandoffKommo

#: Первые две строки сообщения — дословно, третья — ссылка.
HEAD = ("Новый лид", "Эмейл Рассылка")
#: Те же две строки о новом письме лида, уже переданного телемаркетологу.
AGAIN = ("Лид написал снова", "Эмейл Рассылка")

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
    #: О чём проект лида — его словами из письма; услуги — по базе знаний. Пусто — не названы.
    project: str = ""
    services: tuple[str, ...] = ()
    #: С какого адреса пришло последнее письмо; пусто — не известно (тогда «От:» — адрес лида).
    reply_from: str = ""

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


def message(
    deal_url: str | None, thread_id: int, kommo: HandoffKommo, *, again: bool = False
) -> str:
    """Три строки телемаркетологу: ссылка на сделку, а без неё — на диалог с пометкой.
    `again` — о новом письме лида, уже переданного: первая строка «Лид написал снова»."""
    if deal_url:
        link = f"Ссылка на сделку в коммо: {deal_url}"
    else:
        link = f"Ссылка на диалог: {dialog_link(thread_id)}{_WHY_NO_DEAL[kommo]}"
    return "\n".join((*(AGAIN if again else HEAD), link))


def note(card: Card) -> str:
    """Примечание к сделке: последнее письмо и всё, что о лиде известно."""
    when = f" — {card.reply_at:%d.%m.%Y %H:%M} UTC" if card.reply_at else ""
    first = f"{card.first_sent_at:%d.%m.%Y}" if card.first_sent_at else "—"
    lines = [
        f"Ответ из рассылки{when}",
        _sender(card),
        f"Тема: {card.reply_subject or '—'}",
        "",
        card.reply_text[:TEXT_LIMIT] or "(текста ответа нет)",
        "",
        f"Переписка: писем от нас — {card.sent}, ответов — {card.replies}; первое письмо — {first}",
        f"Компания: {card.company or '—'} · {card.site}",
        *_told(card),
        f"Гипотеза: {card.hypothesis}",
        f"ГЕО: {card.country or '—'}; язык: {card.language or '—'}",
        f"Диалог в сервисе: {dialog_link(card.thread_id)}",
    ]
    return "\n".join(lines)


def _sender(card: Card) -> str:
    """Кто написал: адрес, с которого письмо пришло; не адрес лида — так и сказано."""
    sender = card.reply_from.strip()
    if not sender or sender.lower() == card.email.strip().lower():
        return f"От: {card.email}"
    return f"От: {sender} (не адрес лида {card.email})"


def _told(card: Card) -> tuple[str, str]:
    """Проект и услуги лида — что сказал разбор ответов; не названо — так и написано."""
    return (
        f"Проект: {card.project or 'не описан'}",
        f"Услуги: {', '.join(card.services) or 'не названы'}",
    )
