"""Адрес, вписанный человеком, — с карточки донора и из очереди форм.

До 26.09.2026 вписать адрес можно было только в очереди форм, и только
донору, у которого лестница нашла форму. Замечание: «проверь, чтобы
в карточке донора была возможность заполнить контакты, и не один ящик,
а несколько». Правило одно на оба места — здесь.

**Проверка — та же, что у лестницы и у сборки писем** (`quality`):
адрес, который сборка потом молча пропустит, не записывается, а отказ
называет причину словами. Дубликат — тоже словами, а не отказом
уникальности базы.

**Вписанный адрес — «адрес найден», но не «искали».** Исход поиска
становится `found` — по нему считают фильтр «с адресом» и плитку главной;
отметка попытки не трогается: карточка не пишет «Искали <дата>», если
поиска не было. Общий поиск такого донора не берёт
(`repository.manual_address`).

**Удаляется только адрес, которому не писали.** У диалогов и писем ссылка
на адрес при удалении обнуляется (`ON DELETE SET NULL`): удалив адрес, по
которому шла переписка, мы стёрли бы, с кем она шла. Такой адрес не
удаляется, и почему — сказано до нажатия.

**Удалили последний адрес — исход честный.** «Адрес найден» без адресов —
неправда: исход становится «адреса нет», если лестница по донору ходила,
и «не искали», если нет. Другой исход, чем «найден», удаление не трогает.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.contacts.quality import NOT_AN_ADDRESS, rejection_reason
from backend.features.core.domain import ContactSource, ContactStatus
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.outreach import MessageModel, ThreadModel
from backend.shared.database.ids import storable

logger = logging.getLogger(__name__)

#: Длина колонки `contacts.email`.
MAX_LENGTH = 255

#: Оценка проверки у вписанного руками адреса: он пришёл от человека,
#: проверять его нечем и незачем. По ней же адрес встаёт первым в порядке
#: писем (`preference.preferred_first`), пока с другого не ответили.
MANUAL_SCORE = 100

#: Почему адрес с перепиской не удаляется.
HAS_LETTERS = "По этому адресу уже есть письма — удалить нельзя: переписка потеряла бы адресата."


class AddressInvalidError(ValueError):
    """Вписанное — не адрес или адрес, которым нельзя пользоваться."""


class AddressConflictError(ValueError):
    """Адрес уже есть у донора или его нельзя удалить. Текст говорит почему."""


class UnknownAddressError(ValueError):
    """Такого адреса у донора нет."""


@dataclass(frozen=True, slots=True)
class Removed:
    """Что удалили — для журнала."""

    email: str
    #: Исход поиска после удаления: меняется, только если адресов не осталось.
    contact_status: ContactStatus | None


def _checked(email: str) -> str:
    value = email.strip().lower()
    if not value:
        raise AddressInvalidError("Впишите адрес почты.")
    if len(value) > MAX_LENGTH:
        raise AddressInvalidError(f"Адрес длиннее {MAX_LENGTH} знаков — такого не бывает.")
    reason = rejection_reason(value)
    if reason == NOT_AN_ADDRESS:
        raise AddressInvalidError(f"«{value}» не похож на адрес почты.")
    if reason is not None:
        # Причина называет правило и сам адрес: «заглушка вместо адреса: you@x.com».
        raise AddressInvalidError(f"Такой адрес не записываем — {reason}.")
    return value


async def add(session: AsyncSession, donor: DonorModel, email: str) -> ContactModel:
    """Записать адрес донору. Дальше он — адрес для писем (если донора примут)."""
    value = _checked(email)
    taken = await session.scalar(
        select(ContactModel.id)
        .where(ContactModel.domain_id == donor.domain_id)
        .where(ContactModel.email == value)
    )
    if taken is not None:
        raise AddressConflictError(f"Адрес {value} у донора уже есть.")
    contact = ContactModel(
        domain_id=donor.domain_id,
        email=value,
        source=ContactSource.MANUAL,
        verification_status="manual",
        verification_score=MANUAL_SCORE,
    )
    session.add(contact)
    donor.contact_status = ContactStatus.FOUND
    await session.flush()
    logger.info("адреса: донору №%s вписан адрес руками", donor.id)
    return contact


async def removal_refusals(
    session: AsyncSession, contacts: Sequence[ContactModel]
) -> dict[int, str]:
    """Какие адреса нельзя удалить и почему: номер адреса → причина.

    Письмо или диалог на адрес — переписка, даже если письмо ещё в очереди:
    обнулённая ссылка оставила бы его без адресата. Отметки «писали»
    и «отвечали» — тот же след, и они проверяются отдельно: диалог мог
    перейти на другой адрес, а ответ — остаться на этом.
    """
    ids = [contact.id for contact in contacts]
    if not ids:
        return {}
    referenced = set(
        (
            await session.execute(
                select(MessageModel.contact_id)
                .where(MessageModel.contact_id.in_(ids))
                .union(select(ThreadModel.contact_id).where(ThreadModel.contact_id.in_(ids)))
            )
        )
        .scalars()
        .all()
    )
    return {
        contact.id: HAS_LETTERS
        for contact in contacts
        if contact.id in referenced
        or contact.last_contacted_at is not None
        or contact.last_replied_at is not None
    }


async def remove(session: AsyncSession, donor: DonorModel, contact_id: int) -> Removed:
    """Удалить адрес донора, которому ещё не писали."""
    contact = await session.get(ContactModel, contact_id) if storable(contact_id) else None
    if contact is None or contact.domain_id != donor.domain_id:
        raise UnknownAddressError(f"Адреса №{contact_id} у донора №{donor.id} нет.")
    refusal = (await removal_refusals(session, [contact])).get(contact.id)
    if refusal is not None:
        raise AddressConflictError(refusal)
    email = contact.email
    await session.delete(contact)
    await session.flush()
    left = await session.scalar(
        select(func.count(ContactModel.id)).where(ContactModel.domain_id == donor.domain_id)
    )
    if not left and donor.contact_status is ContactStatus.FOUND:
        donor.contact_status = (
            None if donor.contact_attempted_at is None else ContactStatus.NOT_FOUND
        )
        await session.flush()
    logger.info("адреса: у донора №%s удалён адрес, осталось %s", donor.id, left)
    return Removed(email=email, contact_status=donor.contact_status)


async def host_of(session: AsyncSession, donor: DonorModel) -> str:
    """Домен донора — для журнала."""
    return str(
        await session.scalar(select(DomainModel.host).where(DomainModel.id == donor.domain_id))
    )
