"""Передача лида продаж телемаркетологу: Kommo → Telegram, запасной путь, повторы (срез 5.3).

**Одна точка входа — `start(session, thread_id)`.** Её позовёт триггер: вид ответа
«хочет пообщаться» (Ф2) или ситуация агента (Ф3). Она находит лида продаж диалога
(`lead_of`), заводит передачу — одну на диалог — и ставит задачу в очередь. Сама
в Kommo и Telegram не ходит: чужие сервисы отвечают секундами, а вызывающий ждать не
должен. Повтор без нового ответа — ничего; новый ответ — примечание к той же сделке,
а не вторая сделка (A2). `start` коммитит сессию: задача должна видеть строку.

**Задача — `process`.** Сначала Kommo — сделка (`create_complex_lead`) или примечание
к уже заведённой; номер сделки пишется в базу сразу, до следующего запроса. Потом
Telegram: ссылка на сделку нужна в сообщении. Сообщение уходит, когда у передачи
появилась ссылка, которой телемаркетолог ещё не получал: первая — всегда, ссылка
на сделку после ссылки на диалог — тоже; повтор той же ссылки — нет.

**Исходы Kommo — по классу отказа клиента (`kommo_types.py`).**
- Не ответил после своих повторов → `retry`: телемаркетологу всё равно уходит ссылка
  на диалог с пометкой «повторяем», владельцу — тревога, проход по расписанию повторит
  (A3). Молча лид не теряется.
- Запись ушла, ответ потерян (`KommoUnconfirmedError`, ответ без номера) — повтора
  вслепую нет: защиты от дублей у Kommo нет. Решает поиск контакта по почте: не было
  ни до записи, ни после — запись не дошла, это `retry`; иначе — `unconfirmed`,
  тревога и проверка руками. Неподтверждённое примечание не повторяется тоже.
- Отказал (ключ, права, оплата, форма ответа) → `failed`, тревога; следующий ответ
  человека — новая попытка: ключ к тому времени могли починить.
- Kommo не подключён (`Deps.kommo is None`: на проде `fixture`) → `off`: тот же путь,
  но вместо ссылки на сделку — ссылка на диалог (A6).

**Telegram — бизнес-событие** (`telegram.py`): три попытки, затем `undelivered`
и тревога эксплуатации другим ботом (A4). Копия в группу — после личного сообщения,
если включена настройкой (A5); её недоставка — тревога, а не «не доставлено».

**Две задачи на одну передачу разом не работают:** задача захватывает строку
(`claimed_at`); вторая получает `HandoffBusyError`, и очередь повторит её позже.
Захват задачи, умершей посреди работы, переходит к следующей через
`HANDOFF_CLAIM_SEC`.

**Цепочка писем этому человеку** — шов `handed_off(session, lead_id)`: его спрашивают
сборка очереди продаж и отправка (4.6b) — переданному лиду письма и добивки не идут.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import ColumnElement, Exists, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import sales as cfg
from backend.features.core.domain import ReplyKind
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import MessageModel, ReplyModel, ThreadModel
from backend.features.replies.quoting import written_by_hand
from backend.features.runs.failures import described
from backend.features.sales import handoff_kommo
from backend.features.sales import handoff_text as wording
from backend.features.sales.handoff_text import Card
from backend.features.sales.kommo import KommoClient
from backend.features.sales.models import (
    HandoffKommo,
    HandoffTelegram,
    LeadStatus,
    SalesHandoffModel,
    SalesHypothesisModel,
    SalesLeadModel,
    SalesThreadModel,
)
from backend.features.sales.telegram import SalesBot, TelegramError
from backend.shared.alerts import send_alert
from backend.shared.queue import sales_queue, with_retries

logger = logging.getLogger(__name__)

#: Путь задачи строкой: очередь импортирует её в воркере (`shared/queue.py`).
HANDOFF_JOB = "backend.features.sales.handoff_jobs.hand_off_lead"

#: Сколько передач проход берёт за круг: лидов единицы в день, круг короткий.
PASS_LIMIT = 50

#: Kommo ждёт записи — задаче есть что делать.
_KOMMO_WORK = handoff_kommo.WORK
#: Новый ответ снова открывает запись: примечание, а у `off` и `failed` — и сделку.
#: `unconfirmed` не открывает: сделка, может быть, есть, и вторую заводить нельзя.
_REOPENED_BY_ANSWER = (HandoffKommo.DONE, HandoffKommo.OFF, HandoffKommo.FAILED)

Clock = Callable[[], datetime]
Enqueue = Callable[[int], object]


class HandoffError(RuntimeError):
    """Передавать нечего или некого: нет диалога, лида или передачи. Повтор не поможет."""

    permanent = True


class HandoffBusyError(RuntimeError):
    """Передачу держит другая задача — очередь повторит эту позже."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class Deps:
    """С кем говорит задача. `kommo=None` — Kommo не подключён: ссылка на диалог (A6)."""

    kommo: KommoClient | None
    bot: SalesBot
    alert: handoff_kommo.Alert = send_alert
    now: Clock = _utcnow


def enqueue_handoff(handoff_id: int) -> None:
    """Поставить задачу передачи в очередь продаж (`worker-sales`); сеть и база — повтор очереди.

    Не в общую: общий воркер держит прогон доноров до часа, а лид, который хочет говорить, —
    самое срочное у продаж; и сеть Kommo и Telegram — не в процессе доноров.
    """
    sales_queue().enqueue(HANDOFF_JOB, handoff_id, **with_retries())


async def start(
    session: AsyncSession,
    thread_id: int,
    *,
    enqueue: Enqueue = enqueue_handoff,
    now: Clock = _utcnow,
) -> SalesHandoffModel:
    """Передать лида диалога телемаркетологу. Идемпотентно; коммитит сессию.

    Задача не встала — не отказ, чем бы ни отказала очередь (Redis лежит, адрес очереди
    не разобран): строка уже закоммичена и ждёт прохода по расписанию (`due`), срок
    которого ставится здесь же. Исключение вызывающему значило бы «передачи нет», и разбор
    ответа оставил бы ответ ждать человека при живой передаче (ревью стыков, B5).
    """
    lead = await lead_of(session, thread_id)
    row = await _handoff_of(session, thread_id, lead.id)
    latest = await _latest_reply(session, thread_id)
    if not _reopen(row, latest.id if latest else None):
        return row
    row.due_at = now() + timedelta(seconds=cfg.HANDOFF_RETRY_SEC)
    await session.commit()
    try:
        enqueue(row.id)
    except Exception as exc:  # noqa: BLE001 — строка закоммичена, её возьмёт проход повторов
        logger.error(  # noqa: TRY400 — трассировка очереди ничего не добавит к причине
            "продажи: задача передачи лида не поставлена — её возьмёт проход по расписанию",
            extra={"handoff_id": row.id, "thread_id": thread_id, "error": described(exc)},
        )
    return row


async def lead_of(session: AsyncSession, thread_id: int) -> SalesLeadModel:
    """Лид продаж диалога: по явной связи, а без неё — тот же домен и тот же человек.

    Диалог, начатый сборкой продаж, знает своего лида явно (`sales_threads`, 4.6b): адреса
    `contacts` у него нет, и два лида одной компании — два диалога одного домена. Лиду по
    связи писали, даже если позже его отсеяли, — передаётся он. Диалог без связи (начатый
    до сборки продаж) — по домену и адресу: ссылкой на `contacts` или почтой; отсеянные
    не в счёт — им не писали. Ноль или больше одного — громкий отказ с номерами:
    угадывать, кому передавать, нельзя.
    """
    linked = await session.scalar(
        select(SalesLeadModel)
        .join(SalesThreadModel, SalesThreadModel.lead_id == SalesLeadModel.id)
        .where(SalesThreadModel.thread_id == thread_id)
    )
    if linked is not None:
        return linked
    thread = await session.get(ThreadModel, thread_id)
    if thread is None:
        raise HandoffError(f"диалога №{thread_id} нет — передавать нечего")
    same_person = []
    if thread.contact_id is not None:
        same_person.append(SalesLeadModel.contact_id == thread.contact_id)
        address = await session.scalar(
            select(ContactModel.email).where(ContactModel.id == thread.contact_id)
        )
        if address:
            same_person.append(func.lower(SalesLeadModel.email) == address.strip().lower())
    if not same_person:
        raise HandoffError(f"у диалога №{thread_id} нет адреса собеседника — лида по нему не найти")
    leads = list(
        await session.scalars(
            select(SalesLeadModel)
            .where(
                SalesLeadModel.domain_id == thread.domain_id,
                SalesLeadModel.status != LeadStatus.REJECTED,
                or_(*same_person),
            )
            .order_by(SalesLeadModel.id)
        )
    )
    if not leads:
        raise HandoffError(
            f"у диалога №{thread_id} нет лида продаж: ни ссылкой на адрес, ни почтой на его домене"
        )
    if len(leads) > 1:
        numbers = ", ".join(str(lead.id) for lead in leads)
        raise HandoffError(
            f"у диалога №{thread_id} лидов продаж несколько ({numbers}) — не угадываю: "
            "разобрать дубли руками"
        )
    return leads[0]


def handed_off_rule(lead_id: ColumnElement[int] | int) -> Exists:
    """Передан ли лид — условием запроса: передача заведена. Одно правило у шва цепочки
    (`handed_off`) и у воронки (`funnel.py`): переданный, которому не пишут, — он же «передан»."""
    return exists().where(SalesHandoffModel.lead_id == lead_id)


async def handed_off(session: AsyncSession, lead_id: int) -> bool:
    """Передан ли лид телемаркетологу. Шов цепочки продаж (4.6b): переданному — не писать."""
    return bool(await session.scalar(select(handed_off_rule(lead_id))))


async def _handoff_of(session: AsyncSession, thread_id: int, lead_id: int) -> SalesHandoffModel:
    """Передача диалога: заводится один раз, гонку двух вызовов держит ключ базы."""
    await session.execute(
        insert(SalesHandoffModel)
        .values(
            thread_id=thread_id,
            lead_id=lead_id,
            kommo=HandoffKommo.PENDING,
            telegram=HandoffTelegram.PENDING,
            attempts=0,
        )
        .on_conflict_do_nothing(constraint="uq_sales_handoffs_thread")
    )
    row: SalesHandoffModel = (
        await session.scalars(
            select(SalesHandoffModel)
            .where(SalesHandoffModel.thread_id == thread_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).one()
    return row


def _reopen(row: SalesHandoffModel, latest_reply_id: int | None) -> bool:
    """Есть ли задаче работа. Новый ответ снова открывает запись в Kommo."""
    fresh = latest_reply_id is not None and latest_reply_id != row.noted_reply_id
    if fresh and row.kommo in _REOPENED_BY_ANSWER:
        row.kommo = HandoffKommo.PENDING
    return row.kommo in _KOMMO_WORK or row.telegram is HandoffTelegram.PENDING


async def _latest_reply(session: AsyncSession, thread_id: int) -> ReplyModel | None:
    found: ReplyModel | None = await session.scalar(
        select(ReplyModel)
        .where(ReplyModel.thread_id == thread_id, ReplyModel.kind == ReplyKind.HUMAN)
        .order_by(ReplyModel.created_at.desc(), ReplyModel.id.desc())
        .limit(1)
    )
    return found


async def process(session: AsyncSession, handoff_id: int, deps: Deps) -> dict[str, object]:
    """Задача передачи: Kommo, затем Telegram. Внешние отказы — состояния и тревоги,
    а не исключения; исключение — только чужой сбой (база, ошибка кода): захват
    снимается, и очередь повторит задачу."""
    row = await _claim(session, handoff_id, deps.now())
    try:
        card = await _card(session, row)
        # Работу задача выводит сама из последнего ответа, а не только из состояния:
        # ответ, пришедший во время прошлой задачи, её итог мог перезаписать.
        _reopen(row, card.reply_id)
        await handoff_kommo.write(session, row, card, deps.kommo, deps.alert)
        await _telegram_step(row, card, deps)
        meanwhile = await _answered_meanwhile(session, row, card.reply_id)
    except Exception:
        await _release_after_failure(session, handoff_id)
        raise
    row.due_at = _due_after(row, deps.now(), meanwhile=meanwhile)
    row.claimed_at = None
    await session.commit()
    outcome: dict[str, object] = {
        "handoff": row.id,
        "kommo": row.kommo.value,
        "telegram": row.telegram.value,
        "kommo_lead_id": row.kommo_lead_id,
    }
    logger.info("продажи: передача лида", extra=outcome)
    return outcome


async def _answered_meanwhile(
    session: AsyncSession, row: SalesHandoffModel, seen: int | None
) -> bool:
    """Ответ новее того, с которым задача начала, — пришёл, пока она шла. Его задачу триггер
    мог не поставить (очередь лежала — «два отказа разом», находка 5.3), а итог этой задачи
    затёр бы срок прохода, и ответ ждал бы следующего ответа лида. Запись снова открыта."""
    latest = await _latest_reply(session, row.thread_id)
    return latest is not None and latest.id != seen and _reopen(row, latest.id)


def _due_after(row: SalesHandoffModel, now: datetime, *, meanwhile: bool) -> datetime | None:
    """Срок прохода после задачи: Kommo не ответил — через паузу повтора; пришёл ответ во
    время задачи — сразу; иначе работы нет."""
    if row.kommo is HandoffKommo.RETRY:
        return now + timedelta(seconds=cfg.HANDOFF_RETRY_SEC)
    return now if meanwhile else None


async def due(session: AsyncSession, *, now: datetime, limit: int = PASS_LIMIT) -> list[int]:
    """Передачи, которым пора повторить: Kommo не ответил, или задача потерялась.

    Срок взятых сдвигается сразу: следующий круг их не возьмёт, пока поставленная
    задача не отработала, а потерянная — возьмёт снова через `HANDOFF_RETRY_SEC`.
    """
    stale = now - timedelta(seconds=cfg.HANDOFF_CLAIM_SEC)
    ids = list(
        await session.scalars(
            select(SalesHandoffModel.id)
            .where(
                or_(
                    SalesHandoffModel.kommo.in_(_KOMMO_WORK),
                    SalesHandoffModel.telegram == HandoffTelegram.PENDING,
                ),
                SalesHandoffModel.due_at <= now,
                or_(SalesHandoffModel.claimed_at.is_(None), SalesHandoffModel.claimed_at < stale),
            )
            .order_by(SalesHandoffModel.due_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    if ids:
        await session.execute(
            update(SalesHandoffModel)
            .where(SalesHandoffModel.id.in_(ids))
            .values(due_at=now + timedelta(seconds=cfg.HANDOFF_RETRY_SEC))
        )
    await session.commit()
    return ids


async def _claim(session: AsyncSession, handoff_id: int, now: datetime) -> SalesHandoffModel:
    """Захватить передачу. Занята живой задачей — `HandoffBusyError`; нет — `HandoffError`."""
    stale = now - timedelta(seconds=cfg.HANDOFF_CLAIM_SEC)
    taken = await session.scalar(
        update(SalesHandoffModel)
        .where(
            SalesHandoffModel.id == handoff_id,
            or_(SalesHandoffModel.claimed_at.is_(None), SalesHandoffModel.claimed_at < stale),
        )
        .values(claimed_at=now)
        .returning(SalesHandoffModel.id)
    )
    await session.commit()
    row = await session.get(SalesHandoffModel, handoff_id, populate_existing=True)
    if row is None:
        raise HandoffError(f"передачи №{handoff_id} нет — делать нечего")
    if taken is None:
        raise HandoffBusyError(f"передачу №{handoff_id} держит другая задача — повторим позже")
    return row


async def _release_after_failure(session: AsyncSession, handoff_id: int) -> None:
    """Снять захват после чужого сбоя; не вышло — строка в журнал, исходная причина
    всё равно поднимается выше, а захват истечёт сам."""
    try:
        if not session.is_active:
            await session.rollback()
        await session.execute(
            update(SalesHandoffModel)
            .where(SalesHandoffModel.id == handoff_id)
            .values(claimed_at=None)
        )
        await session.commit()
    except SQLAlchemyError as exc:
        logger.error(  # noqa: TRY400 — исходную трассировку поднимет вызывающий
            "продажи: захват передачи не снят — истечёт сам",
            extra={"handoff_id": handoff_id, "error": str(exc)},
        )


async def _card(session: AsyncSession, row: SalesHandoffModel) -> Card:
    """Что известно о лиде и диалоге: лид, гипотеза, сайт, последний ответ, сводка."""
    found = await session.execute(
        select(SalesLeadModel, SalesHypothesisModel.name, DomainModel.host)
        .join(SalesHypothesisModel, SalesHypothesisModel.id == SalesLeadModel.hypothesis_id)
        .join(DomainModel, DomainModel.id == SalesLeadModel.domain_id)
        .where(SalesLeadModel.id == row.lead_id)
        .execution_options(populate_existing=True)
    )
    lead, hypothesis, host = found.one()._tuple()
    reply = await _latest_reply(session, row.thread_id)
    sent, first = (
        await session.execute(
            select(func.count(), func.min(MessageModel.sent_at)).where(
                MessageModel.thread_id == row.thread_id, MessageModel.sent_at.is_not(None)
            )
        )
    ).one()
    replies = await session.scalar(
        select(func.count())
        .select_from(ReplyModel)
        .where(ReplyModel.thread_id == row.thread_id, ReplyModel.kind == ReplyKind.HUMAN)
    )
    return Card(
        thread_id=row.thread_id,
        lead_id=lead.id,
        email=lead.email,
        name=lead.name or "",
        company=lead.company or "",
        site=host,
        hypothesis=hypothesis,
        country=lead.country or "",
        language=lead.language or "",
        reply_id=reply.id if reply else None,
        reply_at=reply.created_at if reply else None,
        reply_subject=(reply.subject or "") if reply else "",
        reply_text=written_by_hand(reply.raw_body) if reply else "",
        sent=int(sent),
        replies=int(replies or 0),
        first_sent_at=first,
    )


async def _telegram_step(row: SalesHandoffModel, card: Card, deps: Deps) -> None:
    """Сообщение телемаркетологу — когда появилась ссылка, которой он ещё не получал."""
    deal = None
    if deps.kommo is not None and row.kommo_lead_id is not None:
        deal = deps.kommo.lead_url(row.kommo_lead_id)
    link = deal or wording.dialog_link(card.thread_id)
    if row.notified_link == link:
        return
    words = wording.message(deal, card.thread_id, row.kommo)
    before = row.telegram
    try:
        await deps.bot.send(cfg.TELEGRAM_CHAT_ID, words)
    except TelegramError as exc:
        row.telegram, row.last_error = HandoffTelegram.UNDELIVERED, f"Telegram: {exc}"
        logger.warning(
            "продажи: сообщение телемаркетологу не доставлено",
            extra={"handoff_id": row.id, "error": str(exc)},
        )
        if before is not HandoffTelegram.UNDELIVERED:
            await deps.alert(
                f"{handoff_kommo.headline(row)}: сообщение телемаркетологу не доставлено — {exc}"
            )
        return
    row.telegram, row.notified_link, row.notified_at = HandoffTelegram.SENT, link, deps.now()
    await _group_copy(row, words, deps)


async def _group_copy(row: SalesHandoffModel, words: str, deps: Deps) -> None:
    """Копия в группу продаж (A5). Недоставка — тревога, а не «не доставлено»:
    телемаркетолог сообщение получил."""
    if not cfg.TELEGRAM_GROUP_COPY:
        return
    if not cfg.TELEGRAM_GROUP_CHAT_ID:
        row.last_error = (
            "копия в группу включена (SALES_TELEGRAM_GROUP_COPY), а "
            "SALES_TELEGRAM_GROUP_CHAT_ID пуст — копия не ушла"
        )
        logger.warning("продажи: копия в группу не ушла — чат группы не задан")
        return
    try:
        await deps.bot.send(cfg.TELEGRAM_GROUP_CHAT_ID, words)
    except TelegramError as exc:
        row.last_error = f"Telegram, копия в группу: {exc}"
        logger.warning(
            "продажи: копия в группу не доставлена",
            extra={"handoff_id": row.id, "error": str(exc)},
        )
        await deps.alert(
            f"{handoff_kommo.headline(row)}: копия в группу продаж не доставлена — {exc}"
        )
