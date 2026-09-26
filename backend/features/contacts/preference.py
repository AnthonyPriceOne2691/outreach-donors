"""Какой адрес донора лучший — одно правило для писем и для карточки.

Порядок: сначала адрес, с которого уже отвечали (дальше пишем тому, кто
отвечает, а не в ящик, где письмо пролежало неделю), потом по оценке
проверки адреса, потом старшая запись. По нему сборка писем берёт один
адрес на донора (`letters.recipients`), а карточка донора показывает
адреса в том же порядке и отмечает, на какой уйдёт письмо.

До 26.09.2026 порядок был записан в сборке писем дважды (доноры
и рекламодатели), а карточка сортировала адреса по-своему — без оценки
проверки. Вписанный руками адрес (оценка 100) стоял бы в карточке ниже
найденного, а письмо ушло бы на него.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import UnaryExpression

from backend.features.core.models.donor import ContactModel


def preferred_first() -> tuple[UnaryExpression[Any] | InstrumentedAttribute[Any], ...]:
    """Порядок адресов домена — лучший первым."""
    return (
        ContactModel.last_replied_at.desc().nullslast(),
        ContactModel.verification_score.desc().nullslast(),
        ContactModel.id,
    )
