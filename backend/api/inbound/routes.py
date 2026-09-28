"""Приём ответов от почтовой платформы.

**Единственная ручка наружу без пропуска** — и потому единственная,
где защита описана отдельно (`docs/SECURITY.md`):

* секрет передаётся заголовком, а не в пути: в пути он утекает
  в журналы прокси и браузера. Годятся два заголовка: свой
  `X-Inbound-Secret` и `Authorization: Basic` с секретом паролем —
  второй нужен платформе, которая своих заголовков не шлёт (SendGrid
  Inbound Parse): адрес вебхука `https://inbound:СЕКРЕТ@домен/api/inbound/replies`
  клиент превращает в заголовок, и в строку запроса секрет не попадает;
* сравнение секрета постоянное по времени;
* тело ограничено по размеру **до** разбора, а не после, — и тогда, когда
  размер не объявлен: читается не больше потолка;
* частота ограничена.

**Форма читается по точным байтам, а не разбором веб-фреймворка**
(`features/replies/form_data.py`): тот читает поля как UTF-8, не зная
кодировки письма, и отказывает полю больше мегабайта — а на отказ
платформа отвечает повторами и в конце концов бросает письмо.

**Отвечаем 200 всему, что приняли,** — включая непонятое. Платформа
повторяет доставку на любой не-2xx, и отказ на письме, которое мы всё
равно не сможем разобрать, превращается в бесконечный поток повторов.
Не-2xx остаётся для двух случаев, и в обоих повтор действительно нужен:
у нас не получилось сохранить — или не встал в очередь разбор цены (ниже).

**Разбор цены здесь не делается.** Он идёт задачей очереди: вызов модели
занимает секунды, платформа повторяет вебхук по таймауту, и платный
разбор внутри запроса означал бы повторные списания там, где сеть
подтормозила.

**Разбор, не вставший в очередь, — это 503, а не молчание.** Ответ к тому
времени уже сохранён, и повтор вебхука его не задвоит, — зато повтор
поставит разбор снова (`Accepted.to_parse`). Раньше недоступная очередь
роняла ручку пятисоткой, а повтор видел «уже принято» и не ставил ничего:
ответ навсегда оставался «ждёт разбора». Постановка идемпотентна — номер
задачи от номера ответа (`queue.parse_job_id`), и два повтора подряд
не дают двух платных разборов.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import logging

from fastapi import APIRouter, Depends, Header, Request, Response, status
from redis.exceptions import RedisError
from rq.exceptions import DuplicateJobError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session
from backend.api.inbound.schemas import Taken
from backend.config import outreach as cfg
from backend.features.replies.form_data import FormError, read_form
from backend.features.replies.inbound import MAX_BODY_CHARS, Incoming, masked_for_log
from backend.features.replies.mime import incoming_from
from backend.features.replies.pipeline import Inbox
from backend.shared.queue import PARSE_JOB, parse_job_id, runs_queue, with_retries
from backend.shared.sliding_window import SlidingWindow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/inbound", tags=["приём ответов"])


#: Писем в минуту с одного адреса. Щедро для платформы и тесно для потока:
#: на двадцати доменах по двадцать писем в день столько ответов за минуту
#: не приходит никогда.
RATE_PER_MINUTE = 120

#: Тело запроса целиком — предел письма у платформы приёма. Письмо режется
#: отдельно и позже; это потолок на всё вместе, включая вложения в форме.
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


def basic_password(authorization: str | None) -> str | None:
    """Пароль из `Authorization: Basic`. Имя пользователя не проверяется:
    секрет один, и имя в адресе вебхука — только форма."""
    scheme, _, encoded = (authorization or "").partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return None
    try:
        decoded = base64.b64decode(encoded.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        logger.warning("приём: заголовок Basic не разобрать (%s) — считаю, что секрета нет", exc)
        return None
    _, sep, password = decoded.partition(":")
    return password if sep else None


def given_secret(header: str | None, authorization: str | None) -> str | None:
    """Секрет из своего заголовка, а нет его — паролем из Basic."""
    return header or basic_password(authorization)


def _check_rate(client: str) -> None:
    if _throttle.count(client) >= RATE_PER_MINUTE:
        raise InboundRefusedError(f"Слишком часто: больше {RATE_PER_MINUTE} писем в минуту")
    _throttle.record(client)


async def _body_within(request: Request, limit: int) -> bytes | None:
    """Тело целиком, но не больше потолка. `None` — тело больше потолка.

    Объявленный размер проверяется до чтения, но объявить его отправитель
    не обязан: без этой проверки тело без `Content-Length` читалось бы
    в память целиком, сколько бы его ни прислали.
    """
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            return None
    return bytes(body)


def _too_large(response: Response, size: str) -> Taken:
    logger.warning("приём: тело %s байт больше потолка — отказ до разбора", size)
    # Прежнее имя кода (`…_REQUEST_ENTITY_TOO_LARGE`) Starlette объявила
    # устаревшим: ветка отказа, не проверявшаяся ни одним тестом, упала бы
    # с его удалением пятисоткой — и платформа повторяла бы письмо без конца.
    response.status_code = status.HTTP_413_CONTENT_TOO_LARGE
    return Taken(accepted=False, reason="Письмо больше допустимого размера")


def _queue_parse(reply_id: int | None, message_id: str, response: Response) -> str | None:
    """Поставить разбор цены — одну задачу на ответ. Причина — если не вышло.

    Разбор цены — задача очереди: платный вызов модели внутри вебхука
    означал бы повторные списания при таймауте платформы. Уже стоящая задача
    с тем же номером — не ошибка: это повтор вебхука застал разбор в очереди,
    и второй платный вызов модели не нужен. Не вставшая — ответ не 2xx,
    чтобы платформа повторила письмо: сохранённый ответ повтор не задвоит,
    а разбор поставит.
    """
    if reply_id is None:
        return None
    try:
        runs_queue().enqueue(
            PARSE_JOB,
            reply_id,
            job_id=parse_job_id(reply_id, message_id),
            unique=True,
            **with_retries(),
        )
    except DuplicateJobError:
        logger.info("приём: разбор ответа №%s уже в очереди — второй не ставлю", reply_id)
    except RedisError as exc:
        logger.warning(
            "приём: разбор ответа №%s не поставлен — очередь недоступна (%s). "
            "Ответ сохранён; платформа повторит письмо, и повтор поставит разбор",
            reply_id,
            exc,
        )
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return (
            f"Ответ №{reply_id} сохранён, но разбор цены не поставлен: очередь недоступна. "
            "Повторите доставку — разбор встанет при повторе"
        )
    return None


async def _letter_from(request: Request, response: Response) -> Incoming | Taken:
    """Письмо из тела запроса — или отказ, если читать нечего.

    Размер проверяется до чтения: объявленный — сразу, необъявленный — по ходу.
    """
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_REQUEST_BYTES:
        return _too_large(response, declared)
    body = await _body_within(request, MAX_REQUEST_BYTES)
    if body is None:
        return _too_large(response, f"больше {MAX_REQUEST_BYTES} (размер не объявлен)")
    try:
        form = read_form(body, request.headers.get("content-type", ""))
    except FormError as exc:
        # Не форма — второй раз придёт то же самое: повтор не поможет.
        logger.warning("приём: %s (%s байт)", exc, len(body))
        return Taken(accepted=False, reason=str(exc))
    return incoming_from(form)


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
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(db_session),
) -> Taken:
    """Принять одно входящее письмо."""
    client = request.client.host if request.client else "неизвестно"
    try:
        _check_secret(given_secret(secret, authorization))
        _check_rate(client)
    except InboundRefusedError as exc:
        # Отказ в доступе — это не «повторите», это «не ходите сюда».
        logger.warning("приём: отказано %s — %s", client, exc)
        response.status_code = status.HTTP_403_FORBIDDEN
        return Taken(accepted=False, reason=str(exc))

    incoming = await _letter_from(request, response)
    if isinstance(incoming, Taken):
        return incoming

    if not incoming.from_email:
        # Письмо без отправителя разобрать нельзя, но и повторять его
        # платформе незачем: второй раз придёт то же самое.
        logger.warning("приём: письмо без отправителя, тема «%s»", incoming.subject[:80])
        return Taken(accepted=False, reason="В письме нет отправителя")

    outcome = await Inbox(session).accept(incoming)
    await session.commit()

    refused = _queue_parse(outcome.to_parse, incoming.message_id, response)

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
        reason=refused or outcome.review_reason,
    )


#: Потолок на текст письма живёт в ядре и здесь только упоминается:
#: два разных потолка на одно и то же расходятся при первой правке.
TEXT_LIMIT = MAX_BODY_CHARS
