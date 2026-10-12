"""Перезапрос цены: короткое письмо донору, чья цена старше срока свежести.

ТЗ, Этап 2: «свежесть цены 150 дней; старше — сначала перезапрос цены у донора». До
12.10.2026 перезапросить было нечем: обход такому донору отказывал словами
(`crawl/targets.explain`), а первое письмо ответившему донору не собирается
(`attempts.open_for_letter`).

**Уходит только нажатием человека** — на панели «Обход доноров» (по одному и пачкой)
и на карточке донора — и только донору с протухшей ценой. Право, этап и саму цену
проверяет сервер (`look`), а не экран: экран лишь показывает его слова до нажатия.

**Можно ли** — `recheck_rules.py`; **куда** — `recheck_target.py`, по фактам, прочитанным
разом (`recheck_facts.py`): список протухших стоит полтора десятка запросов, сколько бы
в нём ни было доноров. Донор отвечал — в его переписку, ответом с её ящика (шаг вне
цепочки `chain.RECHECK_STEP`, переписка снова ждёт донора); переписки нет — новым первым
письмом в рассылке перезапросов; писали, а донор молчит, — отказ. **Перезапрос одиночный
на обоих путях** (решение 12.10.2026): добивок нет (`settle._next_due`).

**Защиты — те же, что у любой отправки.** Письмо уходит через `Sending`: стоп-лист,
решение по донору, подпись, окно, ящик. Всё, на чём отправка откажет, проверяется ещё
до заведения письма, — отказ после оставил бы перезапрос в очереди. Свои — сверху:
одно письмо донору за раз, не чаще раза в `PRICE_RECHECK_DAYS` дней, лимиты ящика.

**Нажатия одного донора идут по одному** (проверка 12.10.2026): строка донора
захватывается до чтения фактов (`FOR UPDATE`) и держится до фиксации письма. Без этого
два одновременных нажатия оба видели «писем нет», считали разные номера ключа и уводили
донору два письма; теперь второе ждёт первое и видит его письмо.

**Перезапрос, не ушедший с первого раза** (почта отказала, исход неизвестен и возвращён
в очередь), уходит следующим нажатием тем же письмом, а не вторым — как ответ в переписке
(`answers.py`), и записью «только если ещё в очереди» (`settle.leave`). Первое письмо,
оставшееся в очереди, — в очереди писем и уходит оттуда.

**Ответ разбирается как обычно** (`replies/pipeline.py`): приходит в ту же переписку по
метке письма, цена из него — последнее слово донора (`donors/price.py`) и снова свежая,
и обход по донору открывается сам (`crawl/targets.choose`). Ответ без числа ждёт
человека, и у него — «Цена та же» (`recheck_answer.py`).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.outreach import CampaignModel, MessageModel
from backend.features.crawl import targets
from backend.features.donors.standing import UnknownDonorError
from backend.features.letters import compose, recheck_facts, recheck_rules, recheck_target, settle
from backend.features.letters import template as templates
from backend.features.letters.building import idempotency_key
from backend.features.letters.chain import FIRST_STEP, RECHECK_STEP
from backend.features.letters.recheck_facts import Facts
from backend.features.letters.recheck_rules import CAMPAIGN_TEMPLATE
from backend.features.letters.recheck_target import Plan, RecheckRefusedError, Way
from backend.features.letters.repository import LetterRepository
from backend.features.letters.sending import NotReadyError, SendError, Sending, check_ready
from backend.features.letters.transport import MaybeSentError
from backend.shared.database.ids import storable

logger = logging.getLogger(__name__)

#: Имя рассылки перезапросов новым первым письмом — для экрана писем; находит её отметка
#: (`recheck_rules.CAMPAIGN_TEMPLATE`), а не имя.
CAMPAIGN_NAME = "Перезапрос цены — новым письмом"

#: Тот же ключ письма у двух нажатий подряд — слова для второго.
RACED = "Его секундой раньше перезапросил другой запрос — обновите список."
#: Остальным донорам нажатия, когда перезапросы остановил обрыв связи или сбой.
STOPPED = "Не отправлен: перезапросы остановлены на предыдущем доноре — повторите позже."

#: Сколько доноров перезапрашивается одним нажатием. Письма уходят тут же, по одному, пока
#: человек ждёт ответа экрана: два десятка — секунды, и больше за раз осознанно не выбирают.
BATCH_MAX = 20

__all__ = [
    "BATCH_MAX",
    "Asked",
    "Recheck",
    "RecheckRefusedError",
    "Refused",
    "Stale",
    "Way",
    "ask",
    "ask_many",
    "look",
    "stale",
]


@dataclass(frozen=True, slots=True)
class Recheck:
    """Перезапрос цены донора — как его видит человек: можно ли, куда уйдёт и каким текстом."""

    donor_id: int
    domain_id: int
    host: str
    price: Decimal | None
    currency: str | None
    priced_at: datetime | None
    source: str | None
    #: Когда последний перезапрос ушёл донору. Пусто — не перезапрашивали.
    asked_at: datetime | None
    #: Почему перезапрос сейчас не уйдёт — словами. Пусто — уйдёт по `plan`.
    refusal: str | None = None
    plan: Plan | None = None
    subject: str | None = None
    body: str | None = None
    #: Перезапрос в переписку, собранный раньше и не ушедший: уйдёт он, а не второй.
    queued_id: int | None = None


@dataclass(frozen=True, slots=True)
class Asked:
    """Перезапрос ушёл: каким письмом, куда и с какого ящика."""

    donor_id: int
    host: str
    message_id: int
    thread_id: int | None
    way: Way
    sender_email: str
    #: Ушло ли письмо на самом деле. У нулевого транспорта — нет.
    real: bool


@dataclass(frozen=True, slots=True)
class Refused:
    """Перезапрос не ушёл — почему, словами."""

    donor_id: int
    host: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class Stale:
    """Доноры с протухшей ценой — с тем, можно ли перезапросить каждого."""

    rows: list[Recheck]
    #: Сколько их всего: строк бывает меньше — по потолку списка.
    total: int


async def look(
    session: AsyncSession,
    donor_id: int,
    *,
    now: datetime | None = None,
    sales_visible: bool = False,
) -> Recheck:
    """Можно ли перезапросить цену донора сейчас и куда уйдёт письмо — ничего не меняя.

    Одни слова у списка до нажатия и у отказа после: экран показывает ровно то, что
    скажет сервер. Донора нет — `UnknownDonorError`. `sales_visible` — смотрящий видит
    продажи: без права «Продажи» письма продаж в словах отказа не называются.
    """
    if not storable(donor_id):
        raise UnknownDonorError(f"Донора №{donor_id} нет")
    facts = await recheck_facts.load(session, [donor_id], now or datetime.now(UTC))
    return judge(facts, donor_id, sales_visible=sales_visible)


def judge(facts: Facts, donor_id: int, *, sales_visible: bool) -> Recheck:
    """Решение по одному донору — по фактам, без базы."""
    found = facts.donors.get(donor_id)
    if found is None:
        raise UnknownDonorError(f"Донора №{donor_id} нет")
    donor, host = found
    seen = Recheck(
        donor_id=donor.id,
        domain_id=donor.domain_id,
        host=host,
        price=donor.last_price,
        currency=donor.last_price_currency,
        priced_at=donor.last_price_at,
        source=donor.last_price_source,
        asked_at=facts.asked.get(donor.domain_id),
    )
    refusal = recheck_rules.refusal(facts, donor, host, sales_visible=sales_visible)
    if refusal is not None:
        return replace(seen, refusal=refusal)
    try:
        subject, body = _text(seen)
        plan = recheck_target.plan(facts, donor, host, subject, sales_visible=sales_visible)
    except RecheckRefusedError as exc:
        logger.debug("перезапрос цены: %s сейчас не уйдёт — %s", host, exc)
        return replace(seen, refusal=str(exc))
    queued = facts.queued.get(donor.domain_id)
    return replace(
        seen,
        plan=plan,
        subject=plan.subject or subject,
        body=body,
        queued_id=queued.id if queued is not None else None,
    )


async def ask(
    session: AsyncSession,
    sending: Sending,
    donor_id: int,
    *,
    author_id: int | None,
    now: datetime | None = None,
    sales_visible: bool = False,
) -> Asked:
    """Перезапросить цену донора — и отправить сразу: нажатие человека и есть согласование.

    Отказ — `RecheckRefusedError` словами `look`, до заведения письма; отказ отправки —
    её словами (`SendError`). Строка донора захвачена до чтения — до фиксации письма
    второе нажатие того же донора ждёт.
    """
    if storable(donor_id):
        await session.execute(
            select(DonorModel.id).where(DonorModel.id == donor_id).with_for_update()
        )
    found = await look(session, donor_id, now=now, sales_visible=sales_visible)
    plan = found.plan
    if plan is None or found.subject is None or found.body is None:
        raise RecheckRefusedError(found.refusal or f"Перезапрос цены {found.host} не собрался.")
    message = await _materialize(session, found, plan)
    await session.commit()
    outcome = await sending.send(
        message.id,
        author_id=author_id,
        from_sender_id=plan.sender_id,
        in_reply_to=plan.in_reply_to,
    )
    logger.info(
        "перезапрос цены: %s — письмо №%s ушло (%s)", found.host, message.id, plan.way.value
    )
    return Asked(
        donor_id=found.donor_id,
        host=found.host,
        message_id=message.id,
        thread_id=message.thread_id,
        way=plan.way,
        sender_email=outcome.sender_email,
        real=outcome.real,
    )


async def ask_many(
    session: AsyncSession,
    sending: Sending,
    donor_ids: Sequence[int],
    *,
    author_id: int | None,
    now: datetime | None = None,
    sales_visible: bool = False,
) -> list[Asked | Refused]:
    """Перезапросить цену у нескольких доноров — по одному, тем же путём, что одного.

    Отказ одного не останавливает остальных: он ложится в итог словами, а ушедшие письма
    уже записаны — каждое своей фиксацией. Обрыв связи с почтой и непредвиденный сбой
    останавливают перезапросы, как пачку писем (`batch.py`): исход письма неизвестен,
    и следующее упёрлось бы в то же.
    """
    done: list[Asked | Refused] = []
    queue = list(dict.fromkeys(donor_ids))
    for index, donor_id in enumerate(queue):
        try:
            done.append(
                await ask(
                    session,
                    sending,
                    donor_id,
                    author_id=author_id,
                    now=now,
                    sales_visible=sales_visible,
                )
            )
        except (SendError, UnknownDonorError) as exc:
            # Отказ — до записи или после фиксации отправки: сессия чиста, и фиксация лишь
            # отпускает строку донора (`ask`). Иначе захваты отказанных копились бы до конца
            # пачки, и две пачки с общими донорами в разном порядке ждали бы друг друга.
            await session.commit()
            logger.info("перезапрос цены: донор №%s не перезапрошен — %s", donor_id, exc)
            done.append(Refused(donor_id, await _host_of(session, donor_id), str(exc)))
        except IntegrityError:
            # Тот же ключ письма у двух нажатий: второе откатывается, первое ушло.
            await session.rollback()
            logger.warning(
                "перезапрос цены: донор №%s — ключ письма занят другим нажатием", donor_id
            )
            done.append(Refused(donor_id, await _host_of(session, donor_id), RACED))
        except Exception as exc:
            logger.exception("перезапрос цены: донор №%s — перезапросы остановлены", donor_id)
            done.append(Refused(donor_id, None, _broken(exc)))
            done.extend(Refused(rest, None, STOPPED) for rest in queue[index + 1 :])
            break
    return done


async def stale(
    session: AsyncSession,
    *,
    limit: int,
    now: datetime | None = None,
    sales_visible: bool = False,
) -> Stale:
    """Доноры с протухшей ценой — тем же отбором, что у обхода (`targets.choose`), — и
    можно ли перезапросить каждого. Факты — разом на весь список (`recheck_facts.load`)."""
    moment = now or datetime.now(UTC)
    chosen = await targets.choose(session, limit=0, now=moment)
    hosts = chosen.stale_price[:limit]
    ids = dict(
        (
            await session.execute(
                select(DomainModel.host, DonorModel.id)
                .join(DonorModel, DonorModel.domain_id == DomainModel.id)
                .where(DomainModel.host.in_(hosts))
            )
        )
        .tuples()
        .all()
    )
    facts = await recheck_facts.load(session, ids.values(), moment)
    rows = [judge(facts, ids[host], sales_visible=sales_visible) for host in hosts if host in ids]
    return Stale(rows=rows, total=len(chosen.stale_price))


def _text(seen: Recheck) -> tuple[str, str]:
    """Тема и текст перезапроса — шаблоном, без модели. Незаданная подпись или адрес —
    отказ до заведения письма: отправка отказала бы так же, но письмо осталось бы в очереди."""
    rendered = compose.render(
        templates.recheck(), compose.values_for(host=seen.host, domain_id=seen.domain_id)
    )
    try:
        check_ready(rendered.body, what=f"Перезапрос цены {seen.host}")
    except NotReadyError as exc:
        raise RecheckRefusedError(str(exc)) from exc
    return rendered.subject, rendered.body


async def _materialize(session: AsyncSession, found: Recheck, plan: Plan) -> MessageModel:
    """Строка письма — новая или собранная раньше и не ушедшая; адрес и текст — нынешние.

    Собранную раньше правит та же условная запись, что выводит письмо из очереди
    (`settle.leave`, как `review._while_queued`): её могла секундой раньше взять отправка.
    """
    if found.queued_id is not None:
        message = await session.get(MessageModel, found.queued_id)
        written = message is not None and await settle.leave(
            session,
            message,
            was=MessageStatus.QUEUED,
            contact_id=plan.contact_id,
            subject=found.subject,
            body=found.body,
        )
        if message is None or not written:
            raise RecheckRefusedError(
                f"Перезапрос №{found.queued_id} уже не в очереди: его секундой раньше взяла "
                "отправка — обновите список."
            )
        return message
    message = await _new_letter(session, found, plan)
    message.contact_id = plan.contact_id
    message.subject = found.subject
    message.body = found.body
    await session.flush()
    return message


async def _new_letter(session: AsyncSession, found: Recheck, plan: Plan) -> MessageModel:
    """Новая строка: в переписку — шагом перезапроса, иначе — первым письмом своей рассылки.

    Ключ первого письма — с номером попытки этапа, как у сборки (`attempts.attempt_number`,
    `building.idempotency_key`): добивки и попытки следующих адресов наследуют его, и с ключом
    прежней цепочки донора он не совпадёт.
    """
    if plan.thread_id is not None and plan.campaign_id is not None:
        asked = await _count(session, found.domain_id, MessageModel.step == RECHECK_STEP)
        message = MessageModel(
            campaign_id=plan.campaign_id,
            thread_id=plan.thread_id,
            domain_id=found.domain_id,
            step=RECHECK_STEP,
            status=MessageStatus.QUEUED,
            idempotency_key=f"{Stage.DONORS.value}:{found.host}:recheck:{asked + 1}",
        )
    else:
        campaign = await _campaign(session)
        thread = await LetterRepository(session).thread(
            domain_id=found.domain_id, campaign_id=campaign.id, contact_id=plan.contact_id
        )
        firsts = await _count(
            session,
            found.domain_id,
            (MessageModel.step == FIRST_STEP) & (CampaignModel.stage == Stage.DONORS),
        )
        message = MessageModel(
            campaign_id=campaign.id,
            thread_id=thread.id,
            domain_id=found.domain_id,
            step=FIRST_STEP,
            status=MessageStatus.QUEUED,
            idempotency_key=idempotency_key(
                stage=Stage.DONORS, host=found.host, step=FIRST_STEP, attempt=firsts + 1
            ),
        )
    session.add(message)
    return message


async def _campaign(session: AsyncSession) -> CampaignModel:
    """Рассылка перезапросов новым первым письмом — одна на всех, заводится первым."""
    found: CampaignModel | None = await session.scalar(
        select(CampaignModel)
        .where(CampaignModel.template_id == CAMPAIGN_TEMPLATE, CampaignModel.stage == Stage.DONORS)
        .order_by(CampaignModel.id)
        .limit(1)
    )
    if found is not None:
        return found
    created = CampaignModel(
        name=CAMPAIGN_NAME, stage=Stage.DONORS, status="draft", template_id=CAMPAIGN_TEMPLATE
    )
    session.add(created)
    await session.flush()
    return created


async def _count(session: AsyncSession, domain_id: int, condition: ColumnElement[bool]) -> int:
    """Сколько писем донора подходят под `condition` — для номера в ключе письма."""
    total = await session.scalar(
        select(func.count(MessageModel.id))
        .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
        .where(MessageModel.domain_id == domain_id, condition)
    )
    return int(total or 0)


async def _host_of(session: AsyncSession, donor_id: int) -> str | None:
    """Домен донора — для итога пачки; донора нет — пусто."""
    if not storable(donor_id):
        return None
    host: str | None = await session.scalar(
        select(DomainModel.host)
        .join(DonorModel, DonorModel.domain_id == DomainModel.id)
        .where(DonorModel.id == donor_id)
    )
    return host


def _broken(exc: Exception) -> str:
    """Почему перезапросы остановились — словами для итога."""
    if isinstance(exc, MaybeSentError):
        return (
            "Почта не ответила, и ушло ли письмо — неизвестно: второе не уходит, а исход "
            "решают на экране «Письма» через несколько минут."
        )
    return "Не ушёл: сбой сервиса — причина в его журнале."
