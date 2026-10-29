"""Отсрочка и отказ доставки письма — словами (`letters/deferrals.py`): у письма в переписке
(`api/threads`, `LetterCard`) и в блоке «Доставка откладывается» на экране писем
(`api/letters/deferred.py`).

Строку целиком собирает экран: время в ней — по часам браузера, а «14 попыток с 10:26» — числом
и временем отсюда. Вид причины и слова — сервер: таблица образцов одна (`delivery_trouble.py`),
и второй её копии у экрана нет.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from backend.features.core.domain import Stage
from backend.features.core.models.outreach import MessageModel
from backend.features.letters.deferrals import DeferredLetter, Waiting, deferral_of


class DeferralCard(BaseModel):
    """Последняя отсрочка доставки: платформа не отдала письмо серверу получателя и повторяет."""

    #: Вид причины (`letters/delivery_trouble.Trouble`): `unreachable`, `mailbox_full`, …
    kind: str
    #: Словами для человека, с коротким кодом в скобках: «сервер получателя не отвечает
    #: (порт 25, тайм-аут)»; вид не узнан — ответ почты, обрезанный.
    words: str
    #: Сколько попыток доставки — по счёту платформы.
    attempts: int
    #: С какого часа платформа повторяет — первая отсрочка.
    since: datetime
    #: Последняя попытка.
    last: datetime

    @classmethod
    def of(cls, message: MessageModel) -> DeferralCard | None:
        found = deferral_of(message)
        if found is None:
            return None
        return cls(
            kind=found.kind.value,
            words=found.words,
            attempts=found.attempts,
            since=found.since,
            last=found.last,
        )


class DeferredLetterCard(BaseModel):
    """Письмо, доставка которого откладывается, — с тем, по чему его узнают."""

    id: int
    host: str
    email: str | None
    campaign: str
    #: Переписка письма: там вся его история.
    thread_id: int | None
    deferral: DeferralCard

    @classmethod
    def of(cls, row: DeferredLetter) -> DeferredLetterCard | None:
        deferral = DeferralCard.of(row.message)
        if deferral is None:
            return None
        return cls(
            id=row.message.id,
            host=row.host,
            email=row.email,
            campaign=row.campaign,
            thread_id=row.message.thread_id,
            deferral=deferral,
        )


class DeferredLettersView(BaseModel):
    """Блок «Доставка откладывается» на экране писем: первые письма и сколько их всего."""

    stage: Stage
    letters: list[DeferredLetterCard]
    total: int

    @classmethod
    def of(cls, stage: Stage, found: Waiting) -> DeferredLettersView:
        cards = [DeferredLetterCard.of(row) for row in found.letters]
        return cls(stage=stage, letters=[card for card in cards if card], total=found.total)
