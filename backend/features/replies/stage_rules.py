"""Правила этапа для записей человека по ответу: подтвердить цену, взять лид.

Ответ лида продаж приходит в тот же тред и в те же записи, что ответы доноров и
рекламодателей (`ReplyRepository.confirm`, `ReplyRepository.take_lead`). Его вид и
путь разбирает модуль продаж (`features/sales/replies.py`), а не разбор цены: цена
из ответа лида не ложится ни в ответ, ни в карточку донора, а лидом рекламодателя
он не становится. Отказ — словами, до любой записи.

Этап здесь разбирается целиком (`match` с `assert_never`): следующий новый этап —
ошибка mypy в этом файле, а не тихий путь доноров. Своим модулем, а не в
`replies/repository.py`: тот у предела длины файла.
"""

from __future__ import annotations

from typing import Literal, assert_never

from backend.features.core.domain import Stage
from backend.features.core.stages import SalesNotConnectedError


def price_confirmable(stage: Stage | None, reply_id: int) -> bool:
    """Ложится ли подтверждённая цена ответа в карточку донора.

    Донор и ответ без рассылки — да. Рекламодатель — нет: в его ответе цены
    площадки нет, и отказ словами даёт `ReplyRepository.confirm`. Продажам — отказ
    здесь же, до записи.
    """
    match stage:
        case Stage.DONORS | None:
            return True
        case Stage.ADVERTISERS:
            return False
        case Stage.SALES:
            raise SalesNotConnectedError(f"Цена из ответа №{reply_id} не подтверждена")
        case _:
            assert_never(stage)


def lead_stage(
    stage: Stage | None, reply_id: int
) -> Literal[Stage.DONORS, Stage.ADVERTISERS] | None:
    """Этап ответа, который берут лидом; продажам — отказ словами.

    Лид — ответ человека на оффер рекламодателю, остальное судит
    `ReplyRepository.take_lead` своими словами. Ответ лида продаж — не лид
    рекламодателя: его слова «не лид, ответ донора» были бы неправдой.
    """
    match stage:
        case Stage.SALES:
            raise SalesNotConnectedError(f"Ответ №{reply_id} лидом не взят")
        case Stage.DONORS | Stage.ADVERTISERS | None:
            return stage
        case _:
            assert_never(stage)
