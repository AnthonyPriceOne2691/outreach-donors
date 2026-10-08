"""Отправка очереди этапа одной кнопкой: «Отправить очередь · N».

До 06.10.2026 письма уходили только по одному, и это было решение экрана:
спорное решение видит человек (`docs/WEB_LAYER.md`). Для боевого запуска
Anthony выбрал пачку — «вся очередь» (06.10.2026): письма переписаны моделью
с проверкой отличия от шаблона, а отправка по одному на десятках писем
в день стала узким местом запуска.

**Каждое письмо пачки идёт тем же путём, что одно** (`Sending.send`):
стоп-листы, решение по донору или рекламодателю, предохранитель, дневной
лимит разгона ящика. Пачка — цикл поверх одной отправки, а не своя: две
отправки разошлись бы на первой правке. Этап — параметр: продажи встают
сюда же своим этапом. В пачке только первые письма (`LetterRepository.queued`):
добивка и ответ уходят своим путём и с ящика своей переписки (`mailbox.py`).

**Когда пачка останавливается.** Ящики выбрали дневной лимит — остальное
ждёт завтра в очереди. Связь с почтой оборвалась — ушло ли письмо,
неизвестно, и слать дальше вслепую нельзя: письмо остаётся «отправляется»
(`Sending._hand_over`), и его исход решает событие платформы или человек
в блоке «Исход неизвестен» (`unknown_outcome.py`). Отказ по одному письму
(стоп-лист, предохранитель, донор отклонён после сборки) пачку
не останавливает: он считается и называется в итоге.

**Две пачки разом не отправят письмо дважды**: захват письма в `Sending`
отдаёт второй отказ «уже не в очереди», и он попадает в итог словами.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import Stage
from backend.features.core.stages import SalesNotConnectedError
from backend.features.letters.repository import LetterRepository
from backend.features.letters.sending import (
    NoSenderError,
    NotQueuedError,
    NotReadyError,
    OutsideWindowError,
    RejectedDonorError,
    RemovedAdvertiserError,
    SendError,
    Sending,
    SuppressedError,
    UndecidedDonorError,
    UnknownZoneError,
)
from backend.features.letters.transport import Mail
from backend.features.letters.unknown_outcome import STUCK_MINUTES

logger = logging.getLogger(__name__)

#: Потолок одной пачки — столько же, сколько очередь показывает на экране
#: (`LetterRepository.queued`): уходит ровно то, что человек мог увидеть.
BATCH_MAX = 200

#: Отказ — словами, одинаковыми для похожих: текст отказа называет адрес
#: или донора, и по нему итог развалился бы на строку на каждое письмо.
#: Подклассы — раньше родителей: донор, отклонённый после сборки, — тоже
#: «стоп-лист» по классу, но человеку важнее первое.
_WHY: tuple[tuple[type[SendError], str], ...] = (
    (RejectedDonorError, "донор отклонён после сборки"),
    (RemovedAdvertiserError, "рекламодатель снят после сборки"),
    (SuppressedError, "стоп-лист"),
    (UndecidedDonorError, "донор не принят человеком"),
    (NotReadyError, "письмо не готово к отправке"),
    (NotQueuedError, "уже не в очереди"),
    # Окно получателя (`core/window.py`): письмо ждёт в очереди его рабочих часов.
    (OutsideWindowError, "вне окна получателя"),
    (UnknownZoneError, "пояс получателя неизвестен"),
)
#: Отказ предохранителя (`sendgrid.py`): пока список своих адресов не пуст,
#: письма уходят только на них.
_ALLOWLIST_MARK = "не в списке разрешённых получателей"


@dataclass(slots=True)
class BatchReport:
    """Итог пачки — числами и словами, для строки задачи на экране."""

    sent: int = 0
    refused: Counter[str] = field(default_factory=Counter)
    #: Почему пачка остановилась раньше конца очереди; `None` — дошла до конца.
    stopped: str | None = None
    #: Сколько писем этапа осталось в очереди — всех, без потолка пачки.
    left: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "sent": self.sent,
            "refused": dict(self.refused),
            "stopped": self.stopped,
            "left": self.left,
        }


def why(exc: SendError) -> str:
    """Почему письмо не ушло — словами, одинаковыми для похожих отказов."""
    for kind, words in _WHY:
        if isinstance(exc, kind):
            return words
    if _ALLOWLIST_MARK in str(exc):
        return "предохранитель: адрес не из своих"
    return "почта отказала"


async def send_queue(
    session: AsyncSession,
    transports: Mail,
    *,
    stage: Stage,
    author_id: int | None = None,
    limit: int = BATCH_MAX,
) -> BatchReport:
    """Отправить очередь этапа по одному письму, пока у ящиков есть лимит."""
    repository = LetterRepository(session)
    ids = [row.message.id for row in await repository.queued(stage=stage, limit=limit)]
    sending = Sending(session, transports)
    report = BatchReport()
    for letter_id in ids:
        stop = await _send_one(sending, letter_id, author_id, report)
        if stop is not None:
            report.stopped = stop
            break
    # Вся очередь этапа, а не её первые `limit`: остальное возьмёт следующая пачка.
    report.left = await repository.queued_count(stage=stage)
    logger.info(
        "письма: пачка этапа %s — ушло %s, не ушло %s, осталось %s",
        stage.value,
        report.sent,
        sum(report.refused.values()),
        report.left,
    )
    return report


async def _send_one(
    sending: Sending, letter_id: int, author_id: int | None, report: BatchReport
) -> str | None:
    """Одно письмо пачки. Возвращает причину остановки пачки или `None`."""
    try:
        await sending.send(letter_id, author_id=author_id)
    except (NoSenderError, SalesNotConnectedError) as exc:
        # Лимит ящиков или продажи, не подключённые к почте (ответ моста `core/stages`):
        # следующее письмо упрётся в то же — пачка встаёт с причиной словами, а не
        # «связь с почтой оборвалась».
        logger.info("письма: пачка встала на письме №%s — %s", letter_id, exc)
        return str(exc)
    except SendError as exc:
        logger.info("письма: письмо №%s пачки не ушло — %s", letter_id, exc)
        report.refused[why(exc)] += 1
        return None
    except Exception:
        logger.exception("письма: пачка остановлена на письме №%s — связь с почтой", letter_id)
        return (
            f"связь с почтой оборвалась на письме №{letter_id}: ушло ли оно, неизвестно, "
            "и дальше пачка не шла. Сообщит о нём платформа — исход решится сам; нет — "
            f"через {STUCK_MINUTES} минут письмо встанет в блок «Исход неизвестен» "
            "на экране писем: там его отмечают ушедшим или возвращают в очередь"
        )
    report.sent += 1
    return None
