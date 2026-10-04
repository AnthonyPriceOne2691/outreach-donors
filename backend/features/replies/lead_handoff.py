"""Передача лида дальше: подписанный вебхук и выгрузка файлом.

Лид — ответ человека на оффер рекламодателю (`ReplyRepository.take_lead`).
До 04.10.2026 он жил только в диалогах: куда его вести, решала другая сторона,
и кода передачи не было вовсе. Решение Anthony (04.10.2026): выгрузка CSV
с экрана и вебхук на настраиваемый адрес — CRM подключается настройкой.

**Вебхук — задачей в очереди, а не в запросе «Взять в работу».** Чужой сервер
отвечает секундами или не отвечает вовсе; кнопка человека от этого зависеть
не должна. Сеть и 5xx очередь повторяет (`shared.queue.RETRY_INTERVALS`), отказ
4xx — постоянный: тот же запрос он отвергнет и в третий раз (`runs.failures`).

**Подпись — HMAC-SHA256 от «время.тело».** Без времени подсмотренный запрос
можно было бы слать вечно; без подписи адрес приёма открыт любому. Секрет
общий с получателем; пусто — вебхук не уходит вовсе.

**Повтор безопасен у получателя:** в теле номер лида и номер события, по
ним он отбрасывает дубль, если наш повтор застал его уже принятым.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime

import httpx
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import outreach as cfg
from backend.features.core.domain import ReplyKind, Stage
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import CampaignModel, ReplyModel, ThreadModel

#: Сколько текста ответа уходит в карточку. Письмо целиком с цитатой нашего
#: оффера получателю не нужно, а в CSV длинная ячейка ломает просмотр.
TEXT_LIMIT = 4000

#: Потолок строк выгрузки за раз: файл для человека, а не дамп базы.
EXPORT_LIMIT = 5000

TIMEOUT_S = 20.0

SIGNATURE_HEADER = "X-Outreach-Signature"
EVENT = "lead_taken"


class LeadNotTakenError(ValueError):
    """Лид ещё не взят в работу: передавать нечего — ведущего нет."""

    permanent = True


class LeadWebhookOffError(ValueError):
    """Адрес вебхука не задан: передавать некуда (`OUTREACH_LEAD_WEBHOOK_URL`)."""

    permanent = True


class LeadWebhookRefusedError(RuntimeError):
    """Получатель отверг запрос (4xx). Повтор не поможет — исход задачи, а не сбой."""

    permanent = True


@dataclass(frozen=True, slots=True)
class LeadCard:
    """Что знаем о лиде. Одни и те же поля — в вебхуке и в строке CSV."""

    lead_id: int
    received_at: str
    advertiser: str
    from_email: str
    subject: str
    text: str
    taken_by: str
    taken_at: str
    donor_host: str
    page_url: str
    anchor: str
    campaign: str


def _iso(moment: datetime | None) -> str:
    return moment.isoformat() if moment is not None else ""


def _leads() -> Select[tuple[ReplyModel, str, str, str | None, str | None, str | None]]:
    return (
        select(
            ReplyModel,
            DomainModel.host,
            CampaignModel.name,
            AdvertiserModel.best_donor_host,
            AdvertiserModel.best_page_url,
            AdvertiserModel.best_anchor,
        )
        .join(ThreadModel, ThreadModel.id == ReplyModel.thread_id)
        .join(CampaignModel, CampaignModel.id == ThreadModel.campaign_id)
        .join(DomainModel, DomainModel.id == ThreadModel.domain_id)
        .outerjoin(AdvertiserModel, AdvertiserModel.domain_id == ThreadModel.domain_id)
        .where(CampaignModel.stage == Stage.ADVERTISERS, ReplyModel.kind == ReplyKind.HUMAN)
    )


def _card(row: tuple[ReplyModel, str, str, str | None, str | None, str | None]) -> LeadCard:
    reply, host, campaign, donor_host, page_url, anchor = row
    return LeadCard(
        lead_id=reply.id,
        received_at=_iso(reply.created_at),
        advertiser=host,
        from_email=reply.from_email or "",
        subject=reply.subject or "",
        text=(reply.raw_body or "").strip()[:TEXT_LIMIT],
        taken_by=reply.reviewed_by or "",
        taken_at=_iso(reply.reviewed_at),
        donor_host=donor_host or "",
        page_url=page_url or "",
        anchor=anchor or "",
        campaign=campaign,
    )


async def lead_card(session: AsyncSession, reply_id: int) -> LeadCard | None:
    """Карточка лида. `None` — это не лид (не ответ человека на оффер)."""
    row = (await session.execute(_leads().where(ReplyModel.id == reply_id))).first()
    return _card(row._tuple()) if row is not None else None


async def leads(
    session: AsyncSession,
    *,
    taken: bool | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = EXPORT_LIMIT,
) -> list[LeadCard]:
    """Лиды для выгрузки, новые первыми. `taken` — только взятые / только ждущие."""
    statement = _leads()
    if taken is not None:
        statement = statement.where(
            ReplyModel.reviewed_at.is_not(None) if taken else ReplyModel.reviewed_at.is_(None)
        )
    if since is not None:
        statement = statement.where(ReplyModel.created_at >= since)
    if until is not None:
        statement = statement.where(ReplyModel.created_at < until)
    statement = statement.order_by(ReplyModel.created_at.desc(), ReplyModel.id.desc()).limit(limit)
    return [_card(row._tuple()) for row in (await session.execute(statement)).all()]


def to_csv(cards: list[LeadCard]) -> str:
    """Выгрузка файлом. Заголовок — имена полей вебхука: одно описание на оба пути."""
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(LeadCard.__dataclass_fields__))
    writer.writeheader()
    for card in cards:
        writer.writerow(asdict(card))
    return out.getvalue()


def body_of(card: LeadCard, *, event_id: str) -> bytes:
    """Тело вебхука: событие, его номер для отбрасывания дублей и карточка."""
    payload = {"event": EVENT, "event_id": event_id, "lead": asdict(card)}
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def signature(body: bytes, *, secret: str, timestamp: int) -> str:
    """`t=<время>,v1=<hex HMAC-SHA256 от «время.тело»>` — как у Stripe и Slack."""
    signed = f"{timestamp}.".encode() + body
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


async def deliver(
    card: LeadCard, http: httpx.AsyncClient, *, event_id: str, now: float | None = None
) -> int:
    """Отправить карточку на адрес вебхука. Возвращает код ответа (2xx).

    Сеть и 5xx — исключение для повтора очередью; 4xx — `LeadWebhookRefusedError`:
    получатель отверг именно этот запрос, и повтор его не изменит.
    """
    if not cfg.LEAD_WEBHOOK_URL:
        raise LeadWebhookOffError(
            "адрес вебхука лидов не задан — заполнить OUTREACH_LEAD_WEBHOOK_URL"
        )
    if not cfg.LEAD_WEBHOOK_SECRET:
        raise LeadWebhookOffError(
            "секрет подписи вебхука лидов не задан — заполнить OUTREACH_LEAD_WEBHOOK_SECRET"
        )
    body = body_of(card, event_id=event_id)
    stamp = int(now if now is not None else time.time())
    response = await http.post(
        cfg.LEAD_WEBHOOK_URL,
        content=body,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            SIGNATURE_HEADER: signature(body, secret=cfg.LEAD_WEBHOOK_SECRET, timestamp=stamp),
        },
        timeout=TIMEOUT_S,
    )
    if 400 <= response.status_code < 500 and response.status_code not in (408, 429):
        raise LeadWebhookRefusedError(
            f"получатель вебхука отверг лид №{card.lead_id}: {response.status_code} "
            f"{response.text[:200]!r}"
        )
    response.raise_for_status()
    return response.status_code
