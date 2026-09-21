"""Приём ответов от почтовой платформы.

**Единственная ручка наружу без пропуска** — и потому единственная,
где защита описана отдельно (`docs/SECURITY.md`):

* секрет передаётся заголовком, а не в адресе: в адресе он утекает
  в журналы прокси и браузера;
* сравнение секрета постоянное по времени;
* тело ограничено по размеру **до** разбора, а не после;
* частота ограничена.

**Отвечаем 200 всему, что приняли,** — включая непонятое. Платформа
повторяет доставку на любой не-2xx, и отказ на письме, которое мы всё
равно не сможем разобрать, превращается в бесконечный поток повторов.
Не-2xx остаётся ровно для одного случая: у нас не получилось сохранить,
и повтор действительно нужен.

**Разбор цены здесь не делается.** Он идёт задачей очереди: вызов модели
занимает секунды, платформа повторяет вебхук по таймауту, и платный
разбор внутри запроса означал бы повторные списания там, где сеть
подтормозила.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Depends, Header, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session
from backend.api.inbound.schemas import Taken
from backend.config import outreach as cfg
from backend.features.replies.inbound import MAX_BODY_CHARS, masked_for_log
from backend.features.replies.mime import from_form
from backend.features.replies.pipeline import Inbox
from backend.shared.queue import PARSE_JOB, runs_queue
from backend.shared.sliding_window import SlidingWindow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/inbound", tags=["приём ответов"])


#: Писем в минуту с одного адреса. Щедро для платформы и тесно для потока:
#: на двадцати доменах по двадцать писем в день столько ответов за минуту
#: не приходит никогда.
RATE_PER_MINUTE = 120

#: Тело запроса целиком. Письмо режется отдельно и позже; это потолок
#: на всё вместе, включая вложения в форме.
MAX_REQUEST_BYTES = 30 * 1024 * 1024

_throttle = SlidingWindow()


class InboundRefusedError(Exception):
    """Письмо не принято. Сообщение говорит, почему."""


def _check_secret(given: str | None) -> None:
    """Секрет платформы. Сравнение постоянное по времени."""
    expected = cfg.INBOUND_SECRET
    if not expected:
        raise InboundRefusedError(
            "OUTREACH_INBOUND_SECRET не задан — принимать ответы нельзя: "
            "без секрета ручку наружу может дёрнуть кто угодно"
        )
    if not expected.isascii():
        # Заголовки HTTP бывают только ASCII: с таким секретом платформа
        # не сможет прислать верный заголовок никогда, и отказ выглядел бы
        # как «секрет не совпал» на каждом письме.
        raise InboundRefusedError(
            "OUTREACH_INBOUND_SECRET содержит символы вне латиницы — "
            "в заголовке HTTP такой секрет не передаётся. "
            "Сгенерировать годный: openssl rand -hex 32"
        )
    if not given or not hmac.compare_digest(given, expected):
        raise InboundRefusedError("Секрет не совпал")


def _check_rate(client: str) -> None:
    if _throttle.count(client) >= RATE_PER_MINUTE:
        raise InboundRefusedError(f"Слишком часто: больше {RATE_PER_MINUTE} писем в минуту")
    _throttle.record(client)


@router.post(
    "/replies",
    response_model=Taken,
    summary="Входящий ответ от почтовой платформы",
    include_in_schema=True,
)
async def take_reply(
    request: Request,
    response: Response,
    secret: str | None = Header(default=None, alias="X-Inbound-Secret"),
    session: AsyncSession = Depends(db_session),
) -> Taken:
    """Принять одно входящее письмо."""
    client = request.client.host if request.client else "неизвестно"
    try:
        _check_secret(secret)
        _check_rate(client)
    except InboundRefusedError as exc:
        # Отказ в доступе — это не «повторите», это «не ходите сюда».
        logger.warning("приём: отказано %s — %s", client, exc)
        response.status_code = status.HTTP_403_FORBIDDEN
        return Taken(accepted=False, reason=str(exc))

    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_REQUEST_BYTES:
        logger.warning("приём: тело %s байт больше потолка — отказ до разбора", declared)
        response.status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
        return Taken(accepted=False, reason="Письмо больше допустимого размера")

    form = await request.form(max_files=50, max_fields=200)
    incoming = from_form({key: value for key, value in form.items() if isinstance(value, str)})

    if not incoming.from_email:
        # Письмо без отправителя разобрать нельзя, но и повторять его
        # платформе незачем: второй раз придёт то же самое.
        logger.warning("приём: письмо без отправителя, тема «%s»", incoming.subject[:80])
        return Taken(accepted=False, reason="В письме нет отправителя")

    outcome = await Inbox(session).accept(incoming)
    await session.commit()

    if outcome.parse_pending and outcome.reply_id is not None:
        # Разбор цены — задача очереди: платный вызов модели внутри
        # вебхука означал бы повторные списания при таймауте платформы.
        runs_queue().enqueue(PARSE_JOB, outcome.reply_id)

    logger.info(
        "приём: письмо от %s — %s",
        masked_for_log(incoming.from_email),
        outcome.as_report,
    )
    return Taken(
        accepted=not outcome.duplicate,
        reply_id=outcome.reply_id,
        kind=outcome.kind.value if outcome.kind else None,
        bound=outcome.bound,
        duplicate=outcome.duplicate,
        needs_review=outcome.needs_review,
        reason=outcome.review_reason,
    )


#: Потолок на текст письма живёт в ядре и здесь только упоминается:
#: два разных потолка на одно и то же расходятся при первой правке.
TEXT_LIMIT = MAX_BODY_CHARS
