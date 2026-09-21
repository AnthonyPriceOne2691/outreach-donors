"""Подпись вебхука: проверка того, что событие прислала платформа.

**Секрета в заголовке здесь быть не может.** Адрес вебхука известен
платформе, а не нам, и придумать свой заголовок она не даст: события
подписываются её ключом, а мы проверяем подпись открытым.

Подписывается склейка «время + тело», и время проверяется отдельно:
без него чужой мог бы взять когда-то подсмотренное событие и слать
его повторно хоть год — подпись у него настоящая.

Ключ живёт в настройках и берётся из кабинета платформы. Пусто —
проверка отказывает всем: ручка, которая паркует домены рассылки
и пишет в стоп-лист, без подписи открыта любому.
"""

from __future__ import annotations

import base64
import logging
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_der_public_key

logger = logging.getLogger(__name__)

#: Насколько старое событие ещё принимаем. Платформа повторяет доставку
#: часами, поэтому окно щедрое; смысл его не в строгости, а в том,
#: чтобы подсмотренное событие нельзя было слать вечно.
MAX_AGE_SECONDS = 24 * 60 * 60


class SignatureError(ValueError):
    """Событие не принято: подпись не сошлась или её нечем проверить."""


def verify(
    *,
    payload: bytes,
    timestamp: str,
    signature: str,
    public_key: str,
    now: float | None = None,
) -> None:
    """Проверить подпись события. Молчание — значит сошлась."""
    if not public_key:
        raise SignatureError(
            "OUTREACH_EVENTS_PUBLIC_KEY не задан — проверить подпись события нечем. "
            "Ключ берётся в кабинете платформы, там же, где включается подписывание"
        )
    _check_age(timestamp, now)

    try:
        key = load_der_public_key(base64.b64decode(public_key))
    except (ValueError, TypeError) as exc:
        raise SignatureError(f"Открытый ключ не разобран: {exc}") from exc
    if not isinstance(key, ec.EllipticCurvePublicKey):
        raise SignatureError("Ожидался ключ на эллиптической кривой — платформа подписывает им")

    try:
        raw = base64.b64decode(signature)
    except (ValueError, TypeError) as exc:
        raise SignatureError("Подпись не разобрана как base64") from exc

    try:
        key.verify(raw, timestamp.encode("utf-8") + payload, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as exc:
        raise SignatureError("Подпись не сошлась с телом события") from exc


def _check_age(timestamp: str, now: float | None) -> None:
    try:
        sent_at = float(timestamp)
    except (TypeError, ValueError) as exc:
        raise SignatureError(f"Время события не разобрано: {timestamp!r}") from exc

    age = (now if now is not None else time.time()) - sent_at
    if age > MAX_AGE_SECONDS:
        raise SignatureError(
            f"Событие старше суток ({int(age)} c) — подпись настоящая, но повтор такого "
            "возраста означает, что его прислали не сейчас"
        )
