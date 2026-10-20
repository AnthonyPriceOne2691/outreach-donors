"""Кому пора искать адрес и что записать по исходу — одно правило на все очереди.

Очередей у поиска несколько: доноры, рекламодатели, дальше — кандидаты
продаж. Лестница у них одна, и правило «кому пора» тоже должно быть одно:
две копии разъехались бы на первой правке, и одна очередь повторяла бы
то, что другая считает решённым. Поэтому здесь всё, что про исход попытки,
параметризовано моделью (колонки `ContactAttemptMixin`), а не этапом.

**Сайт, который не ответил, — не «адреса нет».** Обрыв, таймаут, 5xx или
429 до первой открытой страницы записывались как `not_found`, и донор,
недоступный в момент сбоя, ждал повтора 180 дней. Теперь у такого прохода
свой исход `no_answer` с причиной, и повтор идёт по сроку: следующий
прогон, через день, через неделю. Четвёртый проход без ответа — `not_found`
на обычный срок. Платная ступень в проходе без ответа не зовётся — кроме
последнего: платному сервису живой сайт не нужен, а «не ответил» часто
значит, что сайт молча режет адрес нашего сервера (решения Anthony
01.10.2026).

**Платная ступень не спросила — у общей очереди повтор со сроком, а не каждым
проходом.** Квота кончилась или сервис не ответил — домен «недоспрошен», но без срока
каждый проход — автопоиск после каждого «принять» берёт всех ждущих, ручной поиск —
верхних — снова брал бы те же домены и упирался в ту же стену (ревью 20.10.2026). Квота
возвращается в начале месяца, сбой сервиса проходит за часы: доноры и рекламодатели
ждут сутки (`PAID_RETRY_DELAY`, `general_queue`). Так же — «не спросили» (`paid_off`),
если платный поиск включён: тогда его и спросят; выключен — «не спросили» держится, как
«адреса нет». Срок — довод очереди (`Unasked`), как срок годности: очередь сбора базы
продаж ведёт свои сроки и «Повторить сейчас» сама (`sales/segment_queue.py`) и зовёт
правило без него — у неё «недоспрошен» повторяется следующим проходом, как и раньше.
Частота — без срока у всех: проход после неё встаёт сам (`ladder.halt`), а следующий
застаёт лимит снятым.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import contacts as cfg
from backend.features.core.domain import ContactStatus
from backend.features.core.models._mixins import ContactAttemptMixin
from backend.features.core.models.domain import DomainModel

#: Исходы, которые повторяются: мы не спросили, а не узнали, что контакта нет.
RETRIABLE = frozenset({ContactStatus.NO_QUOTA, ContactStatus.RATE_LIMITED, ContactStatus.ERROR})

#: Из них — повтор по сроку очереди (`Unasked`): квота платной ступени и её сбой.
#: Частота повторяется следующим проходом (см. модуль).
PAID_DELAYED = frozenset({ContactStatus.NO_QUOTA, ContactStatus.ERROR})

#: Через сколько общая очередь — доноры и рекламодатели — повторяет домен, которому
#: платная ступень не ответила или которого не спросила (см. модуль).
PAID_RETRY_DELAY = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class Unasked:
    """Как очередь повторяет домены, которым платная ступень не ответила: через сколько и
    кого. Без своего срока (`NEXT_PASS`) — следующим проходом, как у очереди продаж: она ведёт
    свои сроки сама (`sales/segment_queue.py`)."""

    delay: timedelta = timedelta(0)
    statuses: frozenset[ContactStatus] = PAID_DELAYED


NEXT_PASS = Unasked()


def general_queue() -> Unasked:
    """Общая очередь — доноры и рекламодатели: квота и сбой — через сутки, а «не спросили»
    (`paid_off`) — тоже через сутки, если платный поиск включён и ключ есть: тогда его и
    спросят. Выключен — «не спросили» держится, как «адреса нет», своим сроком годности."""
    paid_on = cfg.PAID_SEARCH_ENABLED and bool(cfg.HUNTER_API_KEY)
    unasked = PAID_DELAYED | {ContactStatus.PAID_OFF} if paid_on else PAID_DELAYED
    return Unasked(PAID_RETRY_DELAY, unasked)


#: Сколько ждать повтора после первой, второй и третьей неудачи подряд.
RETRY_DELAYS = (timedelta(0), timedelta(days=1), timedelta(days=7))

#: Столько повторов после первой неудачи; следующий проход без ответа —
#: последний, и после него домен записывается «адреса нет».
MAX_RETRIES = len(RETRY_DELAYS)


@dataclass(frozen=True, slots=True)
class Outcome:
    """Исход прохода по домену, как его видит очередь."""

    host: str
    status: ContactStatus
    #: Почему сайт не ответил — только у `no_answer`.
    reason: str = ""


def delay_after(tries: int) -> timedelta:
    """Сколько ждать повтора после `tries` проходов без ответа подряд."""
    return RETRY_DELAYS[min(max(tries, 1), MAX_RETRIES) - 1]


def _tries_are(model: type[ContactAttemptMixin], tries: int) -> ColumnElement[bool]:
    """Ступень расписания: первая берёт и ноль, последняя — всё, что выше."""
    if tries == 1:
        return model.contact_tries <= tries
    if tries == MAX_RETRIES:
        return model.contact_tries >= tries
    return model.contact_tries == tries


def waiting(
    model: type[ContactAttemptMixin],
    *,
    now: datetime,
    ttl_days: int,
    unasked: Unasked = NEXT_PASS,
) -> ColumnElement[bool]:
    """Пора ли искать снова — по исходу прошлой попытки.

    Остальные условия очереди (принят ли донор, нет ли уже адреса на
    домене) — у самой очереди: они про роль, а это — про попытку. `unasked` — как очередь
    повторяет домены, которым платная ступень не ответила (см. модуль).
    """
    border = now - timedelta(days=ttl_days)
    due = or_(
        *(
            and_(_tries_are(model, tries), model.contact_attempted_at <= now - delay)
            for tries, delay in enumerate(RETRY_DELAYS, start=1)
        )
    )
    return or_(
        model.contact_attempted_at.is_(None),
        model.contact_attempted_at < border,
        model.contact_status.in_(tuple(RETRIABLE - PAID_DELAYED)),
        and_(
            model.contact_status.in_(tuple(unasked.statuses)),
            model.contact_attempted_at <= now - unasked.delay,
        ),
        and_(model.contact_status == ContactStatus.NO_ANSWER, due),
    )


def waits(
    row: ContactAttemptMixin, *, now: datetime, ttl_days: int, unasked: Unasked = NEXT_PASS
) -> bool:
    """`waiting`, прочитанный по одной строке: экран решает по нему, показать
    ли поиск, и не должен разойтись с запросом (тест гоняет обе стороны)."""
    attempted = row.contact_attempted_at
    if attempted is None or attempted < now - timedelta(days=ttl_days):
        return True
    again = _retry_at(row, unasked)
    return row.contact_status in RETRIABLE if again is None else again <= now


def _retry_at(row: ContactAttemptMixin, unasked: Unasked) -> datetime | None:
    """Когда повтор по своему сроку — у «не ответил» и у платной ступени; `None` — срока нет."""
    attempted = row.contact_attempted_at
    if attempted is None:
        return None
    if row.contact_status is ContactStatus.NO_ANSWER:
        return attempted + delay_after(row.contact_tries)
    if row.contact_status in unasked.statuses:
        return attempted + unasked.delay
    return None


def again_at(
    row: ContactAttemptMixin, *, ttl_days: int, unasked: Unasked = NEXT_PASS
) -> datetime | None:
    """Когда домен снова встанет в очередь. `None` — исхода ещё нет."""
    attempted = row.contact_attempted_at
    if attempted is None:
        return None
    return _retry_at(row, unasked) or attempted + timedelta(days=ttl_days)


def on_last_try(model: type[ContactAttemptMixin]) -> ColumnElement[bool]:
    """Следующий проход без ответа будет последним: в нём платят."""
    return and_(model.contact_status == ContactStatus.NO_ANSWER, model.contact_tries >= MAX_RETRIES)


def values(outcome: Outcome, *, tries_before: int, moment: datetime) -> dict[str, object]:
    """Что записать в очередь по исходу прохода.

    - сайт не ответил — `no_answer` и счёт +1; сверх предела — `not_found`
      на обычный срок, счёт с нуля, причина остаётся словами;
    - квота, частота, поломка платной ступени — «не спросили»: счёт и
      причина не трогаются; частота — в следующий прогон, квота и поломка — по сроку
      очереди (`Unasked`);
    - иначе сайт ответил: счёт с нуля. Причина — только у «адреса нет» сайта,
      который так и не прочитан: не пустил, показал проверку «вы не робот», закрыт
      для страны сервера, не принимает почту (`ladder._without_contact`). Без неё
      такой исход читался как «адреса на сайте нет» (живой замер 20.10.2026).
    """
    if outcome.status is ContactStatus.NO_ANSWER:
        tries = tries_before + 1
        if tries > MAX_RETRIES:
            return {
                "contact_status": ContactStatus.NOT_FOUND,
                "contact_attempted_at": moment,
                "contact_tries": 0,
                "contact_reason": f"сдались после {tries} проходов без ответа: {outcome.reason}",
            }
        return {
            "contact_status": ContactStatus.NO_ANSWER,
            "contact_attempted_at": moment,
            "contact_tries": tries,
            "contact_reason": outcome.reason,
        }
    if outcome.status in RETRIABLE:
        return {"contact_status": outcome.status, "contact_attempted_at": moment}
    return {
        "contact_status": outcome.status,
        "contact_attempted_at": moment,
        "contact_tries": 0,
        "contact_reason": outcome.reason or None,
    }


async def last_tries(
    session: AsyncSession, model: type[ContactAttemptMixin], hosts: Sequence[str]
) -> set[str]:
    """Хосты, для которых этот проход — последний шанс сайту ответить."""
    if not hosts:
        return set()
    owner = model.domain_id
    rows = await session.execute(
        select(DomainModel.host)
        .join(model, owner == DomainModel.id)
        .where(DomainModel.host.in_(list(hosts)), on_last_try(model))
    )
    return set(rows.scalars().all())


async def record(
    session: AsyncSession,
    model: type[ContactAttemptMixin],
    outcomes: Sequence[Outcome],
    ids: dict[str, int],
    *,
    moment: datetime,
    keep: ColumnElement[bool] | None = None,
) -> None:
    """Записать исходы пачки. `keep` — условие, при котором строку не трогают
    (у доноров — адрес, вписанный человеком: исход прохода его не перетирает).
    """
    owner = model.domain_id
    known = [ids[o.host] for o in outcomes if o.host in ids]
    found = await session.execute(select(owner, model.contact_tries).where(owner.in_(known)))
    tries = dict(found.tuples().all())
    for outcome in outcomes:
        domain_id = ids.get(outcome.host)
        if domain_id is None:
            continue
        written = values(outcome, tries_before=tries.get(domain_id, 0), moment=moment)
        statement = update(model).where(owner == domain_id).values(**written)
        await session.execute(statement if keep is None else statement.where(~keep))
