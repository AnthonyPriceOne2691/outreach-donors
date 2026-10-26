"""Передача лида телемаркетологу — что отдают кнопка «Передать телемаркетологу» и выдача
переписки продаж (`ThreadView.handoff`). Слова состояния — модуля продаж
(`features/sales/handoff_manual.py`): экран их показывает как есть."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from backend.features.sales import handoff_manual
from backend.features.sales.models import HandoffKommo, HandoffTelegram, SalesHandoffModel


class HandoffCard(BaseModel):
    """Передача лида — как её знает строка `sales_handoffs` сейчас. Задача Kommo и Telegram
    асинхронная: сразу после нажатия обе стороны ждут задачи, итог приносит перечитанная
    переписка."""

    id: int
    #: Когда лид передан — заведена передача.
    at: datetime
    #: Код состояния Kommo — экрану, чтобы решить, ждать ли итога и красить ли строку; человеку —
    #: `kommo_words`: сделка, только ссылка на диалог или отказ.
    kommo: HandoffKommo
    kommo_words: str
    #: Сообщение телемаркетологу: код и слова — отправлено или отказ.
    telegram: HandoffTelegram
    telegram_words: str

    @classmethod
    def of(cls, row: SalesHandoffModel) -> HandoffCard:
        return cls(
            id=row.id,
            at=row.created_at,
            kommo=row.kommo,
            kommo_words=handoff_manual.kommo_said(row),
            telegram=row.telegram,
            telegram_words=handoff_manual.telegram_said(row),
        )


class HandedOverView(BaseModel):
    """Итог нажатия: передача, встала ли её задача сейчас и какие ответы сняты с ожидания —
    и всё это словами для строки под кнопкой и уведомления."""

    handoff: HandoffCard
    #: Задача Kommo и Telegram встала в очередь сейчас; нет — её возьмёт проход по расписанию.
    queued: bool
    #: Ответы диалога, снятые с ожидания человека.
    released: list[int]
    said: str

    @classmethod
    def of(cls, done: handoff_manual.HandedOver) -> HandedOverView:
        return cls(
            handoff=HandoffCard.of(done.row),
            queued=done.queued,
            released=list(done.released),
            said=handoff_manual.said(done),
        )
