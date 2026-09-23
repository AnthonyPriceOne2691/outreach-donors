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
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.features.core.domain import MessageStatus
from backend.features.core.models.outreach import MessageModel
from backend.features.letters import compose, guards
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


def edit(
    message: MessageModel,
    *,
    host: str,
    subject: str,
    body: str,
    template: Template | None = None,
) -> Reviewed:
    """Заменить текст письма руками и пересчитать отличие."""
    _editable(message)

    text = body.strip()
    if not text:
        raise NotEditableError("Пустое письмо отправить нельзя")

    # Те же два запрета, что при сборке: метрики стоят ключа Ahrefs,
    # незаполненная подстановка — письма, подписанного никем.
    guards.assert_no_metrics(text)

    plain = compose.render(
        template or default(),
        compose.values_for(host=host, domain_id=message.domain_id),
    ).body
    uniqueness = difference(plain, text)

    message.subject = subject.strip()
    message.body = text
    message.uniqueness_pct = uniqueness
    return Reviewed(uniqueness=uniqueness, verdict=corridor_verdict(uniqueness))


def skip(message: MessageModel) -> None:
    """Не писать этому донору."""
    _editable(message)
    message.status = MessageStatus.STOPPED
