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
from datetime import UTC, datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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


class UnknownZoneError(ValueError):
    """Часовой пояс выгрузки не знаком базе поясов."""


class LeadNotTakenError(ValueError):
    """Лид ещё не взят в работу: передавать нечего — ведущего нет."""

    permanent = True


class LeadWebhookOffError(ValueError):
    """Адрес вебхука не задан: передавать некуда (`OUTREACH_LEAD_WEBHOOK_URL`)."""

    permanent = True


class LeadWebhookRefusedError(RuntimeError):
    """Получатель отверг запрос (4xx) или настроен неверно (3xx, не https).
    Повтор не поможет — исход задачи, а не сбой."""

    permanent = True


class LeadWebhookUnavailableError(RuntimeError):
    """Получатель не ответил или ответил 5xx/408/429 — повтор очереди поможет.

    **Адреса вебхука в тексте нет.** У многих CRM входящий вебхук несёт токен
    в пути (Bitrix24) или в параметрах (Make, Zapier), а текст ошибки задачи
    уходит в журнал и на экран задач (ревью «Продаж» #160). `httpx` кладёт
    адрес в текст своих ошибок, поэтому они сюда не пробрасываются.
    """


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


#: С чего начинается формула в Excel и Google Sheets (OWASP «CSV Injection»).
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _inert(value: object) -> object:
    """Ячейка, которую таблица не исполнит: текст из чужого письма вида
    `=HYPERLINK(...)` при открытии выгрузки вытащил бы соседние ячейки —
    адреса других лидов (ревью «Продаж» #160). Ведущий апостроф — по OWASP."""
    if isinstance(value, str) and value.startswith(_FORMULA_START):
        return f"'{value}"
    return value


#: Заголовки файла — словами, по полю карточки. Файл открывает человек (лид программе
#: передаёт вебхук, `body_of`), и `taken_by` или `page_url` ему не говорят ничего. Поле
#: без слова уходит своим именем, а тест выгрузки краснеет: заголовок не теряется молча.
CSV_TITLES: dict[str, str] = {
    "lead_id": "номер лида",
    "received_at": "получен",
    "advertiser": "рекламодатель",
    "from_email": "от кого",
    "subject": "тема",
    "text": "текст ответа",
    "taken_by": "кто ведёт",
    "taken_at": "взят в работу",
    "donor_host": "донор",
    "page_url": "страница донора",
    "anchor": "анкор",
    "campaign": "кампания",
}


#: Поля карточки со временем. В вебхуке — ISO с поясом: его читает программа.
#: В файле — как на экране (`format.formatDateTime`): «07.10.2026 14:01» в поясе,
#: в котором экран пишет время, — поясе браузера выгрузившего. До 10.10.2026 файл
#: нёс «2026-10-07T11:01:49.964379+00:00» — машинный вид и UTC, а экран — время
#: браузера (проверка прода 10.10.2026).
_MOMENTS = frozenset({"received_at", "taken_at"})


def zone_named(name: str | None) -> tzinfo:
    """Пояс времени в файле по имени из базы поясов (`Europe/Moscow`) — так его
    называет браузер. Пусто — UTC; незнакомое имя — отказ словами."""
    if not name:
        return UTC
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise UnknownZoneError(
            f"Часовой пояс «{name[:64]}» не знаком: время в файле пишется в поясе браузера, "
            "его имя — из базы поясов, например Europe/Moscow"
        ) from exc


def _local(iso: str, zone: tzinfo) -> str:
    """Время карточки словами экрана, в поясе файла. Пусто — пусто: лид не взят."""
    return datetime.fromisoformat(iso).astimezone(zone).strftime("%d.%m.%Y %H:%M") if iso else ""


def to_csv(cards: list[LeadCard], zone: tzinfo = UTC) -> bytes:
    """Выгрузка файлом — для Excel: заголовки словами, точка с запятой, UTF-8 с меткой.

    Те же уступки Excel, что у выгрузки доноров (`donors/export.py`): с запятой
    русский Excel раскладывает строку в одну колонку, без метки — показывает
    кракозябры. До 10.10.2026 файл шёл с именами полей вебхука, запятыми и без метки,
    и русский текст ответа в Excel не читался (проверка QA 10.10.2026). Поля и их
    порядок — те же, что в вебхуке: одно описание лида на оба пути; время — словами
    экрана в поясе `zone` (`_MOMENTS`).
    """
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    writer.writerow([CSV_TITLES.get(name, name) for name in LeadCard.__dataclass_fields__])
    for card in cards:
        writer.writerow(
            [
                _inert(_local(value, zone) if name in _MOMENTS else value)
                for name, value in asdict(card).items()
            ]
        )
    return out.getvalue().encode("utf-8-sig")


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
    if not cfg.LEAD_WEBHOOK_URL.lower().startswith("https://"):
        # Уходит переписка с персональными данными — только шифрованным каналом.
        raise LeadWebhookRefusedError(
            "адрес вебхука лидов не https — OUTREACH_LEAD_WEBHOOK_URL должен начинаться с https://"
        )
    body = body_of(card, event_id=event_id)
    stamp = int(now if now is not None else time.time())
    try:
        response = await http.post(
            cfg.LEAD_WEBHOOK_URL,
            content=body,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                SIGNATURE_HEADER: signature(body, secret=cfg.LEAD_WEBHOOK_SECRET, timestamp=stamp),
            },
            timeout=TIMEOUT_S,
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        # Текст ошибки httpx содержит адрес — с токеном CRM. Только тип.
        raise LeadWebhookUnavailableError(
            f"получатель вебхука не ответил ({type(exc).__name__}) — повтор очереди"
        ) from None
    return _judge(response.status_code, lead_id=card.lead_id)


def _judge(code: int, *, lead_id: int) -> int:
    """Код ответа получателя → исход. Адреса в словах нет (см. `LeadWebhookUnavailableError`)."""
    if 200 <= code < 300 or code == 409:
        # 409 — получатель уже принял это событие: наш повтор застал его принятым.
        return code
    if code in (408, 429) or code >= 500:
        raise LeadWebhookUnavailableError(
            f"получатель вебхука ответил {code} на лид №{lead_id} — повтор очереди"
        )
    if 300 <= code < 400:
        # По перенаправлению не идём: подпись ушла бы на чужой хост.
        raise LeadWebhookRefusedError(
            f"адрес вебхука перенаправляет ({code}) — указать в OUTREACH_LEAD_WEBHOOK_URL "
            "конечный адрес"
        )
    raise LeadWebhookRefusedError(f"получатель вебхука отверг лид №{lead_id}: {code}")
