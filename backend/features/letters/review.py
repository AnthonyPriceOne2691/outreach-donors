"""Что человек делает с письмом до отправки: поправить или не писать.

Третье действие — отправить — живёт в `sending.py`: оно единственное
выходит наружу.

**Правка проходит те же проверки, что и сборка.** Соблазн пропустить их
здесь силён — текст ведь написал человек, — и он же самый опасный:
запрет на метрики Ahrefs нарушается не злым умыслом, а желанием
объяснить донору, чем он нам приглянулся. Правило, которое соблюдает
только машина, не правило.

**Процент отличия пересчитывается от текущего шаблона.** Не от того,
каким он был при сборке: «отличие от шаблона» — это про тот шаблон,
который лежит сейчас, и если он изменился, числа обязаны поехать.
Поехавшие числа — повод пересобрать очередь, и это видно.

**«Не писать» — решение по донору, а не по письму.** Письмо уходит
в остановленные и больше в очереди не появляется, донор считается
написанным. Иначе следующая сборка предложила бы его снова, и человеку
пришлось бы отказываться от него каждую неделю.

**Решение пишется, только если письмо всё ещё в очереди** (аудит 10.10.2026) —
условием в самом `UPDATE`, как захват отправки (`Sending._claim`). Пачка — другая
задача: между чтением письма и записью решения она успевает перевести его
в «отправляется». Запись поверх такого письма врала бы о нём: «не писать» на
ушедшем письме оставляло его без времени ухода, без добивок и мимо дневного счёта
ящика (`settle.record_sent` ждёт «отправляется»), правка — оставляла в базе текст,
которого адресат не получал.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import MessageStatus
from backend.features.core.models.outreach import MessageModel
from backend.features.letters import compose, guards, settle
from backend.features.letters.template import Template, default
from backend.features.letters.uniqueness import corridor_verdict, difference


class NotEditableError(ValueError):
    """Править можно только то, что ещё не ушло."""


@dataclass(frozen=True, slots=True)
class Reviewed:
    """Письмо после правки и то, что о нём теперь известно."""

    uniqueness: float
    verdict: str | None


def _editable(message: MessageModel) -> None:
    if message.status is not MessageStatus.QUEUED:
        raise NotEditableError(
            f"Письмо №{message.id} в состоянии «{message.status.value}», а не в очереди. "
            "Править можно только то, что ещё ждёт отправки"
        )


async def _while_queued(
    session: AsyncSession, message: MessageModel, *, lost: str, **values: Any
) -> None:
    """Записать `values`, только если письмо всё ещё в очереди. `lost` — что не записано.

    Тот же условный `UPDATE`, что выводит письмо из состояния (`settle.leave`): правка
    статус не меняет, но пишется тем же условием — проверка `_editable` смотрит
    на письмо, прочитанное до решения, и могла устареть.
    """
    if not await settle.leave(session, message, was=MessageStatus.QUEUED, **values):
        raise NotEditableError(
            f"Письмо №{message.id} уже не в очереди: секундой раньше его взяла отправка "
            f"или оно остановлено, и {lost}. Что с ним сейчас, видно в очереди писем"
        )


async def edit(
    session: AsyncSession,
    message: MessageModel,
    *,
    host: str,
    subject: str,
    body: str,
    template: Template | None = None,
    link: compose.FoundLink | None = None,
    niche: compose.NicheOffer | None = None,
) -> Reviewed:
    """Заменить текст письма руками и пересчитать отличие.

    `template` — текст рассылки письма, `link` — ссылка рекламодателя:
    отличие оффера, отмеренное от вопроса донору о цене, было бы числом
    ни о чём.
    """
    _editable(message)

    text = body.strip()
    if not text:
        raise NotEditableError("Пустое письмо отправить нельзя")

    # Те же два запрета, что при сборке: метрики стоят ключа Ahrefs,
    # незаполненная подстановка — письма, подписанного никем.
    guards.assert_no_metrics(text)

    plain = compose.render(
        template or default(),
        compose.values_for(host=host, domain_id=message.domain_id, link=link, niche=niche),
    ).body
    uniqueness = difference(plain, text)

    await _while_queued(
        session,
        message,
        lost="правка не записана",
        subject=subject.strip(),
        body=text,
        uniqueness_pct=uniqueness,
    )
    return Reviewed(uniqueness=uniqueness, verdict=corridor_verdict(uniqueness))


async def skip(session: AsyncSession, message: MessageModel) -> None:
    """Не писать этому донору."""
    _editable(message)
    await _while_queued(
        session,
        message,
        lost="«не писать» не записано",
        status=MessageStatus.STOPPED,
    )
