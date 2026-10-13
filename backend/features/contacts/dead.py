"""Мёртвый адрес: отказ доставки и автоответ «ящик больше не читается» хоронят адрес.

Отметка — `preference.DEAD` и оценка 0. Одной отметки мало, и до 28.09.2026 её одной
и не хватало: мёртвый адрес с оценкой 0 оставался лучшим, а не дошедшее письмо — «уже
писали». Следующий адрес открывает сборка писем: мёртвый адрес она не берёт, а донора,
у которого все письма не дошли и никто не ответил, снова собирает — на следующий адрес
(`letters/attempts.py`). Ставят отметку событие отказа доставки (`letters/events.py`)
и приём ответов (`replies/repository.py`); мягкий отказ — сервер занят, ящик переполнен —
адрес не хоронит.

**Хоронится адрес, который отказал.** У доноров и рекламодателей письмо уходит на свой
контакт — он и хоронится (`bury`). У письма продаж — нет: оно уходит адресу лида переписки
(`core/stages.recipient`), а контакт письма — тот, кто ответил: секретарь, второй ящик
компании; похоронить его значило бы закрыть живой адрес (кросс-ревью продаж, 10.10.2026).
Адрес лида при этом — та же строка `contacts`, что у доноров: лид сбора ссылается на лучший
адрес домена (`sales/segment_leads.py`), — и отказ её не хоронил, а доноры потом брали
мёртвый адрес первым (ревью стыков 12.10.2026). Поэтому у продаж хоронятся строки с тем
адресом, который отказал (`bury_address`), — все, без учёта регистра: ящика нет на любом
домене, где он записан.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.contacts.preference import DEAD
from backend.features.core.models.donor import ContactModel


def _buried(contact: ContactModel) -> None:
    contact.verification_status = DEAD
    contact.verification_score = 0


async def bury(session: AsyncSession, contact_id: int | None) -> None:
    """Похоронить контакт письма. Контакта нет — хоронить нечего."""
    if contact_id is None:
        return
    contact = await session.get(ContactModel, contact_id)
    if contact is not None:
        _buried(contact)


async def bury_address(session: AsyncSession, email: str | None) -> int:
    """Похоронить все строки `contacts` с адресом `email` — без учёта регистра и краевых
    пробелов. Сколько похоронено; пустой адрес — ни одной."""
    address = (email or "").strip().lower()
    if not address:
        return 0
    found = await session.scalars(
        select(ContactModel).where(func.lower(ContactModel.email) == address)
    )
    rows = found.all()
    for contact in rows:
        _buried(contact)
    return len(rows)
