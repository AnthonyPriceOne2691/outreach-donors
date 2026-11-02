"""Отказы отправки письма: каждый вид — своим классом.

Класс решает, что будет дальше: стоп-лист кончает цепочку, «не сейчас» возвращает срок добивки,
«писать некому» останавливает пачку (`letters/batch.py`, `letters/followups.py`). Имена доступны и
из `sending.py`: оттуда их берут все пути письма, модуль продаж в том числе.
"""

from __future__ import annotations

from backend.features.core import stages, window


class SendError(stages.MailRefusalError):
    """Письмо не отправлено. Сообщение называет причину и что делать."""


class NotQueuedError(SendError):
    """Письмо не в очереди: уже ушло, остановлено или отправляется."""


class SuppressedError(SendError):
    """Донор или адрес в стоп-листе."""


class RejectedDonorError(SuppressedError):
    """Донора отклонили после сборки письма. Для цепочки — как стоп-лист:
    писать ему больше не будем."""


class RemovedAdvertiserError(SuppressedError):
    """Рекламодателя сняли после сборки письма. Для цепочки — как стоп-лист."""


class WithdrawnLinkError(SuppressedError):
    """Оффер собран под ссылку, которой у рекламодателя больше нет: письмо остановлено."""


class StalePriceError(SendError):
    """Цена донора, под чью ссылку написан оффер, устарела или снята после сборки."""


class UndecidedDonorError(SendError):
    """Донора вернули в «предложен» или бизнес ниши снова ждёт решения после сборки письма:
    пока человек решает, писать нельзя, но и обрывать разговор из-за этого нельзя."""


class NotWritingError(SuppressedError):
    """Донор или площадка оффера — в «кому не пишем» ТЗ (`runs.exclusions`): как стоп-лист."""


class OtherRoleError(SuppressedError):
    """Сайт в работе у другого направления: первое письмо снято (`site_role.py`)."""


class NotReadyError(SendError):
    """В письме остались незаполненные обязательные места."""


class NoSenderError(SendError):
    """Сегодня писать некому: все ящики выключены или выбрали дневной лимит.
    У письма переписки — её ящик не пишет, и письмо ждёт его."""


class OwnPathError(SendError):
    """Добивка или ответ пришли не своим путём: без ящика переписки не уходят."""


class OutsideWindowError(window.DeferredError, SendError):
    """Вне окна получателя: первое письмо ждёт в очереди, добивка — открытия окна."""


class UnknownZoneError(window.DeferredError, SendError):
    """Окно этапа есть, а пояса получателя нет ни у лида, ни у страны, ни у гипотезы."""
