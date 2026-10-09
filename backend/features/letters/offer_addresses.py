"""Общее начало отбора адресатов оффера Этапа 2: рекламодатель и его свежий адрес.

Им пользуются оба отбора — по найденной ссылке (`recipients`) и бизнесов
ниши (`niche_recipients`): строка на адрес, с номером попытки.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, select

from backend.features.core.domain import Stage
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.letters import attempts


def advertiser_addresses(*columns: Any) -> Select[Any]:
    """Домен, адрес, номер попытки — и `columns` своего отбора."""
    return (
        select(
            DomainModel.id.label("domain_id"),
            DomainModel.host.label("host"),
            ContactModel.id.label("contact_id"),
            ContactModel.email.label("email"),
            attempts.attempt_number(DomainModel.id, Stage.ADVERTISERS).label("attempt"),
            *columns,
        )
        .join(AdvertiserModel, AdvertiserModel.domain_id == DomainModel.id)
        .join(ContactModel, ContactModel.domain_id == DomainModel.id)
        .where(attempts.fresh(ContactModel))
    )
