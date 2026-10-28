"""«Адреса нет» и «Формы» в ответах «Доноров»: причина, страница формы, место в очереди
«Форм», «Искать глубже».

Своим модулем, а не в схемах доноров (`api/donors/schemas.py` у потолка длины), по образцу
темы сайта (`api/topics/schemas.py`): строка и карточка доноров наследуют поля отсюда, а
значения карточки собирает одна функция (`reach_fields`).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from backend.api.contacts.schemas import DeeperCard
from backend.features.core.models.donor import DonorModel


class ReachRow(BaseModel):
    """«Искать глубже» в строке доноров."""

    #: Можно искать адрес глубже: кнопка в строке — только тогда (`contacts/deeper.py`).
    deeper: bool = False


class ReachCard(BaseModel):
    """Причина «адреса нет», страница формы, очередь «Форм» и «Искать глубже» — в карточке."""

    #: Почему «адреса нет», если сайт не прочитан вовсе: не пустил, проверка «вы не робот»,
    #: нет почты (`ladder._without_contact`); у «сайт не ответил» — причина молчания.
    contact_reason: str | None = None
    #: Страница с контактной формой — ссылка «Открыть форму» в карточке.
    form_url: str | None = None
    #: Стоит ли донор в очереди «Форм» — её условием (`forms.queued`): «стоит в очереди» карточка
    #: пишет только тогда, а не всякому принятому — донора, которому не пишем, очередь не берёт.
    in_forms: bool = False
    #: «Искать глубже»: почему сейчас нельзя (пусто — можно), когда искали и чем кончилось.
    deeper: DeeperCard = Field(default_factory=lambda: DeeperCard(refusal=None))


def reach_fields(
    donor: DonorModel, *, form_url: str | None, refusal: str | None, in_forms: bool
) -> dict[str, Any]:
    """Поля карточки: из донора и из того, что о нём собрала карточка (`browse.DonorCard`)."""
    return {
        "contact_reason": donor.contact_reason,
        "form_url": form_url,
        "in_forms": in_forms,
        "deeper": DeeperCard(
            refusal=refusal, at=donor.contact_deeper_at, note=donor.contact_deeper_note
        ),
    }
