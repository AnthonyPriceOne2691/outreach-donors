"""Что отдают маршруты писем."""

from __future__ import annotations

from pydantic import BaseModel

from backend.config import outreach as cfg
from backend.features.core.domain import MessageStatus
from backend.features.letters.repository import QueuedLetter
from backend.features.letters.uniqueness import corridor_verdict


class QueuedLetterCard(BaseModel):
    """Одно письмо в очереди — целиком, вместе с текстом.

    Имя длиннее соседних нарочно: `LetterCard` уже занят письмом
    в переписке (`api/threads`), и две одноимённые схемы в одном описании
    API разъезжаются молча — клиент собирает типы по именам.

    Текст приходит сразу, а не отдельным запросом на каждое выбранное
    письмо: экран для того и сделан, чтобы человек читал письма подряд,
    а очередь редко длиннее сотни. Второй запрос на каждый щелчок сделал
    бы чтение рваным ради экономии, которой не видно.
    """

    id: int
    host: str
    email: str | None
    campaign: str
    status: MessageStatus
    subject: str | None
    body: str | None
    #: Доля изменённых слов относительно шаблона, 0–1.
    uniqueness: float | None
    #: Что не так с этим числом. Пусто — в коридоре.
    verdict: str | None

    @classmethod
    def of(cls, row: QueuedLetter) -> QueuedLetterCard:
        share = row.message.uniqueness_pct
        return cls(
            id=row.message.id,
            host=row.host,
            email=row.email,
            campaign=row.campaign,
            status=row.message.status,
            subject=row.message.subject,
            body=row.message.body,
            uniqueness=share,
            verdict=corridor_verdict(share) if share is not None else None,
        )


class Corridor(BaseModel):
    """Границы коридора отличия. Отдаёт сервер, а не хранит фронт: второй
    экземпляр чисел разъехался бы с настройкой при первой её правке,
    и экран показывал бы «в коридоре» там, где его уже нет."""

    min: float = cfg.UNIQUENESS_TARGET_MIN
    max: float = cfg.UNIQUENESS_TARGET_MAX


class Transport(BaseModel):
    """Чем отправляем и отправляем ли на самом деле."""

    name: str
    #: Ложь здесь дороже всего: письмо, помеченное отправленным и никуда
    #: не ушедшее, выглядит как работа.
    real: bool
    #: Почему транспорт не собрался, если не собрался.
    problem: str | None = None


class LettersView(BaseModel):
    """Экран писем целиком.

    Кроме самих писем отдаётся то, без чего очередь читается неверно:
    чем заблокирована отправка, настоящий ли транспорт и где кончились
    доноры. Пустая очередь при «всем написали» и при «ни у кого нет
    адреса» выглядит одинаково.
    """

    letters: list[QueuedLetterCard]
    #: Настройки, из-за которых отправить нельзя ни одно письмо.
    blocked_by: list[str]
    transport: Transport
    corridor: Corridor
    funnel: dict[str, int]


class BuildRequestBody(BaseModel):
    campaign: str
    country: str = "us"
    #: Ключи прогона: ниша, по которой донор нашёлся.
    niche: list[str] = []
    limit: int = 50


class BuildQueued(BaseModel):
    """Сборка ушла в очередь задач: она идёт минутами."""

    job_id: str


class EditRequestBody(BaseModel):
    subject: str
    body: str


class SendResult(BaseModel):
    """Чем кончилась отправка одного письма."""

    id: int
    sender_email: str
    #: Ушло ли письмо на самом деле. У нулевого транспорта — нет.
    real: bool
