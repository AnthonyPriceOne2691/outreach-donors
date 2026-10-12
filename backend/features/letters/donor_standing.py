"""Можно ли донору письмо сейчас — проверка при отправке, а не только при сборке.

Между сборкой очереди и отправкой проходят часы, а у добивки — недели. За это
время человек мог передумать — принять, вернуть в «предложен», отклонить
(Anthony, 24.09.2026), — и без проверки письмо, собранное для принятого, ушло бы
отклонённому. А домен мог оказаться в «кому не пишем» ТЗ (решение владельца
11.10.2026, `runs.exclusions`): крупный сайт (DR > 80), крупное СМИ, соцсеть,
справочник — им не уходят ни первое письмо, ни добивка, даже принятым до правила.

Здесь — ответ причиной и словами; класс отказа по причине выбирает отправка
(`sending._DONOR_REFUSED`): отклонённому и «кому не пишем» — как стоп-лист, цепочка
добивок кончается; ждущему решения письмо ждёт, разговор не рвётся. Письмо «кому
не пишем» `check_donor` к тому же останавливает с причиной (`stop_for_good`): в очереди
оно ждало бы того, что не наступит; так же — письмо оффера с площадкой оттуда
(`offer_standing.check_offer`). Ответ в переписке «кому не пишем» не держит: донор
написал сам, и ответ ему — не рассылка.

Отдельно от `sending.py`: тот упёрся в потолок длины файла.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import MessageStatus
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.outreach import MessageModel
from backend.features.donors.standing import decided_in
from backend.features.letters import settle
from backend.features.letters.chain import ANSWER_STEP
from backend.features.runs.exclusions import not_writing_reason


class Refusal(StrEnum):
    """Почему донору сейчас не пишем."""

    REJECTED = "rejected"
    UNDECIDED = "undecided"
    NOT_WRITING = "not_writing"


@dataclass(frozen=True, slots=True)
class DonorRefusal:
    """Отказ донору при отправке: почему и словами для экрана."""

    reason: Refusal
    words: str


def _review_of(run: int | None) -> str:
    """Чьё рассмотрение назвать: прогона, где донор ждёт решения, — или где его искать."""
    return "прогона, где он нашёлся (экран «Прогон»)" if run is None else f"прогона №{run}"


async def check_donor(
    session: AsyncSession, message: MessageModel, *, host: str
) -> DonorRefusal | None:
    """Проверка отправки: `donor_refusal`, а письмо «кому не пишем» — остановлено своей
    фиксацией (`stop_for_good`)."""
    refused = await donor_refusal(session, message, host=host)
    if refused is not None and refused.reason is Refusal.NOT_WRITING:
        await stop_for_good(session, message, refused.words)
    return refused


async def donor_refusal(
    session: AsyncSession, message: MessageModel, *, host: str
) -> DonorRefusal | None:
    """Почему письмо донору не уходит сейчас; `None` — уходит."""
    review = await session.scalar(
        select(DonorModel.review).where(DonorModel.domain_id == message.domain_id)
    )
    if review == "rejected":
        return DonorRefusal(
            Refusal.REJECTED,
            f"Донора {host} отклонили после сборки письма №{message.id} — "
            "писать ему нельзя. Письмо стоит убрать из очереди",
        )
    off = (
        None
        if message.step >= ANSWER_STEP
        else await not_writing_reason(session, message.domain_id)
    )
    if off is not None:
        return DonorRefusal(
            Refusal.NOT_WRITING,
            f"Письмо №{message.id} остановлено: {off} ({host}). Добивки не уйдут",
        )
    if review == "accepted":
        return None
    # Где решать — номером прогона, а не письма (проверка QA 10.10.2026): у ответа в переписке
    # это письмо, созданное секундой раньше, и человеку его номер не говорит ничего.
    run = await decided_in(session, message.domain_id, None)
    return DonorRefusal(
        Refusal.UNDECIDED,
        f"Донор {host} снова ждёт решения; пока его не примут, письмо ждёт. "
        f"Решить — на рассмотрении {_review_of(run)}",
    )


async def stop_for_good(session: AsyncSession, message: MessageModel, words: str) -> None:
    """Письмо «кому не пишем» — донору или оффер с площадкой оттуда (`offer_standing.py`) —
    в «остановлено» с причиной `words`, как по стоп-листу. Из очереди — условием
    (`settle.leave`): письмо, которое секундой раньше взяла пачка, не трогается."""
    await settle.leave(
        session,
        message,
        was=MessageStatus.QUEUED,
        status=MessageStatus.STOPPED,
        next_action_at=None,
        failure_reason=words[:256],
    )
    await session.commit()
