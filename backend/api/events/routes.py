"""События доставки от почтовой платформы.

Третья и последняя ручка наружу без пропуска — и единственная, где
чужой запрос может **остановить нашу рассылку**: событие о недоставке
двигает статус письма, а серия таких паркует домен. Поэтому проверка
здесь строже, чем у приёма ответов: подпись платформы, а не секрет.

**Отвечаем 200 всему, что приняли.** Платформа повторяет доставку
на любой не-2xx, и отказ на событии, которое мы всё равно не разберём,
превращается в бесконечный поток повторов. Не-2xx остаётся для двух
случаев: подпись не сошлась (это не наш корреспондент) и не смогли
сохранить (повтор действительно нужен).

**Тело читается сырым.** Подпись считается по байтам запроса, и разбор
в модель до проверки означал бы проверку не того, что подписано.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session
from backend.api.events.schemas import Taken
from backend.config import outreach as cfg
from backend.features.letters.events import DeliveryEvent, apply_events
from backend.shared.webhook_signature import SignatureError, verify

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/events", tags=["события доставки"])

#: Потолок тела: платформа шлёт события пачками до тысячи штук.
MAX_BODY_BYTES = 5 * 1024 * 1024

#: Мягкий отказ — адрес живой, ящик переполнен или сервер занят.
#: Хоронить контакт по нему нельзя: завтра письмо уйдёт.
SOFT_BOUNCE = "blocked"


@router.post("/delivery", response_model=Taken, summary="События доставки от платформы")
async def take_events(
    request: Request,
    response: Response,
    signature: str | None = Header(default=None, alias="X-Twilio-Email-Event-Webhook-Signature"),
    timestamp: str | None = Header(default=None, alias="X-Twilio-Email-Event-Webhook-Timestamp"),
    session: AsyncSession = Depends(db_session),
) -> Taken:
    """Принять пачку событий."""
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        response.status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
        return Taken(accepted=False, reason="Пачка событий больше допустимого размера")

    try:
        verify(
            payload=body,
            timestamp=timestamp or "",
            signature=signature or "",
            public_key=cfg.EVENTS_PUBLIC_KEY,
        )
    except SignatureError as exc:
        # Не «повторите позже»: это не наш корреспондент.
        logger.warning("события доставки: отказано — %s", exc)
        response.status_code = status.HTTP_403_FORBIDDEN
        return Taken(accepted=False, reason=str(exc))

    try:
        payload = json.loads(body)
    except ValueError:
        logger.warning("события доставки: тело не разобрано как JSON")
        return Taken(accepted=False, reason="Тело не разобрано как JSON")

    events = _events_of(payload)
    report = await apply_events(session, events)
    await session.commit()

    return Taken(
        accepted=True,
        delivered=report.delivered,
        bounced=report.bounced,
        complained=report.complained,
        unknown=report.unknown,
        paused=report.paused_domains,
    )


def _events_of(payload: Any) -> list[DeliveryEvent]:
    """Разбор пачки. Непонятное событие пропускается молча — платформа
    добавляет свои типы, и падать на незнакомом значит терять пачку
    целиком из-за одной строки в ней."""
    if not isinstance(payload, list):
        return []
    return [event for row in payload if (event := _one(row)) is not None]


def _one(row: Any) -> DeliveryEvent | None:
    if not isinstance(row, dict):
        return None
    kind = str(row.get("event") or "").strip().lower()
    if not kind:
        return None
    return DeliveryEvent(
        kind=kind,
        message_id=_number(row.get("message_id")),
        email=str(row.get("email") or ""),
        reason=_reason(row),
        soft=str(row.get("type") or "").strip().lower() == SOFT_BOUNCE,
    )


def _number(raw: Any) -> int | None:
    """Наш номер письма из `custom_args`. Платформа возвращает его
    строкой на верхнем уровне события.

    Разбор без исключения намеренно: гейт `silent-except` прав —
    «не число» здесь не ошибка, а обычное дело (события приходят
    и по письмам, которых мы не отправляли), и прятать это в `except`
    значит однажды спрятать там же настоящую поломку.
    """
    text = str(raw if raw is not None else "").strip()
    return int(text) if text.isdigit() else None


def _reason(row: dict[str, Any]) -> str | None:
    reason = row.get("reason") or row.get("response") or row.get("status")
    return str(reason) if reason else None
