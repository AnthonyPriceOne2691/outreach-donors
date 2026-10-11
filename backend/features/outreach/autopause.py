"""Ящик встал на паузу сам — одна пауза и одна тревога о ней (ТЗ: «ящик с ростом отказов сам
встаёт на паузу»).

Ставят её два правила: мягкие сигналы этапа с политикой (`health.py`, у продаж — по окну
последних писем) и прежнее правило парковки (`letters/events._park_if_burning`, у доноров и
рекламодателей — по доле отказов за жизнь ящика). До тревоги пауза оставляла след только в
журнале и в ответе вебхука: ящик вставал, а узнавали об этом по пустой очереди.

**Одна на паузу.** Паузу ставит тот, кто застал ящик пишущим под замком его строки
(`pause_once`): платформа шлёт пачки событий параллельно, и без замка две пачки с отказами
одного ящика поставили бы одну паузу дважды — и тревог было бы две. Следующие события по
стоящему ящику паузу не ставят и тревоги не шлют. Ящик включили и он снова встал — новая
пауза и новая тревога.

**Пауза человеком — без тревоги.** Её ставит экран «Домены рассылки» (`api/senders`), а не
правило: человек и так знает, что сделал.

**Тревога — после фиксации** (`tell` зовёт вебхук событий после `commit`): сказать о паузе,
которая откатится вместе с транзакцией, значит соврать. Общая для всех этапов, одним
каналом (`shared/alerts.py`): какой ящик, почему и с какими числами, что делать.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import Stage
from backend.features.core.models.outreach import SenderModel
from backend.features.outreach.senders import disable
from backend.shared.alerts import send_alert

#: Что делать, — одними словами для всех этапов: включение начинает разгон заново
#: (`senders.enable`), и это стоит сказать до нажатия.
WHAT_TO_DO = (
    "Что делать: разобрать причину — журнал платформы, жалобы, списки блокировок — и "
    "после разбора включить ящик на экране «Домены рассылки» кнопкой «Включить заново»: "
    "разгон начнётся заново"
)


@dataclass(frozen=True, slots=True)
class AutoPause:
    """Ящик, вставший на паузу сам: адрес, этап и почему — с числами."""

    email: str
    stage: Stage
    why: str

    @property
    def words(self) -> str:
        """Текст тревоги: какой ящик, почему, что делать."""
        return (
            f"ящик {self.email} (направление «{self.stage.value}») встал на паузу сам: "
            f"{self.why}. Новых писем с него нет, письма его переписок ждут. {WHAT_TO_DO}"
        )


async def pause_once(
    session: AsyncSession, sender: SenderModel, reason: str, *, now: datetime
) -> bool:
    """Поставить ящик на паузу, если он ещё пишет. `False` — его уже остановили: другая
    пачка событий рядом, человек. Строка ящика перечитывается под замком до решения: вторая
    пачка ждёт фиксации первой и видит ящик уже стоящим."""
    await session.refresh(sender, with_for_update=True)
    if not sender.enabled:
        return False
    disable(sender, reason, now=now)
    return True


async def tell(pauses: Sequence[AutoPause]) -> None:
    """Тревога о каждой паузе. Не бросает (`shared/alerts.py`): не принял Telegram — строка
    журнала «ТРЕВОГА НЕ ОТПРАВЛЕНА» с её текстом."""
    for pause in pauses:
        await send_alert(pause.words)
