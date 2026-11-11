"""Проверка адреса перед первым письмом донору и рекламодателю (11.11.2026).

Найденный адрес шёл в письмо сразу. Первая же большая пачка первых писем вернула жёсткие
отказы «такого ящика нет»: это свойство списка, а бьёт оно по ящику и домену рассылки —
ящик встал на автопаузу по доле отказов. У продаж адрес лида проверяется до письма давно
(`sales/verifier.py`); здесь — то же для адресов доноров и рекламодателей, общим клиентом
(`verifier.py`). Зовёт проверку сборка очереди — до вызова модели (`letters/address_gate.py`).

**Что делаем со словом Hunter** — по образцу продаж (`sales/verifier.DELIVERABLE_BY_STATUS`),
с одним отличием:

- `valid` — ящик существует: отметка и балл у адреса, письмо собирается;
- `invalid`, `disposable` — ящика нет или он одноразовый: адрес хоронится тем же способом, что
  отказ доставки (`dead.bury`), письмо не собирается, сборка берёт следующий адрес домена;
- `accept_all`, `webmail` — сервер принимает всё или это публичная почта, где Hunter ящик не
  спрашивает: проверить нельзя, письмо собирается (у продаж так же — «не подтверждён, но и не
  отвергнут»);
- `unknown` — Hunter не дозвался сервера: **проверить нельзя, письмо собирается**. У продаж
  иначе: лид с `unknown` ждёт перепроверки через неделю и без неё не пишется (решение владельца
  13.10.2026) — там холодная база на тысячи лидов, и отложить одного ничего не стоит. Здесь
  адресат — донор, принятый человеком, или рекламодатель с найденной ссылкой; замены ему нет,
  жёсткий отказ «ящика нет» — это `invalid`, а от остального ящик держит автопауза. Сколько
  таких — видно числом на экране писем («не проверить»); правило — одна константа
  (`UNVERIFIABLE`);
- сервис не работает с доменом адреса (`blacklisted_domain`) — тоже «проверить нельзя»: это
  ответ про домен у Hunter, а не про ящик, и единицы он не стоит.

**Проверка не состоялась — письма нет.** Сеть, 5xx, непонятный ответ, «ещё проверяет» — этому
адресу письмо сейчас не собирается, причина словами в итоге сборки, следующая сборка спросит
снова. Квота, закрытая учётка, непринятый ключ, частота после повторов, ключа нет, месячный
потолок выбран или не задан (`STOPS`, `_allowed`) — проверка встаёт до конца сборки: следующий
адрес получил бы то же. Молча пропустить проверку нельзя: письмо без неё — то, от чего уходим.

**Чему верим без вызова** (`kind_of`, `passing`): адресу от самого адресата — вписан человеком
(`manual.BY_HAND`), назван автоответом, с него ответили (источник `manual`); и отметке проверки
моложе `CONTACTS_VERIFY_TTL_DAYS` — её ставит эта проверка и платный поиск Hunter, когда отдаёт
адрес уже проверенным (`repository.save_addresses`). Срок — 90 дней: ящики умирают не за недели,
а отметка нужна только до первого письма — дальше об адресе говорит доставка.

**Потолок — месячный потолок проверок Hunter с экрана «Расход»** (`core/hunter_cap.py`), общий с
продажами: число одно, счёт — сумма проверок продаж и этих (`VERIFY_OPERATIONS`). Место считается
до запросов пачки; параллельная очистка продаж может перебрать не больше своей пачки. Расход —
строкой журнала `contacts_verify` на этап адресата, единица — проверка (полкредита учётки).

**Вердикт — чекпоинт.** Отметка и строка расхода ложатся одной фиксацией сразу после ответов
пачки: оборванная сборка не спрашивает адрес второй раз и не пишет расход дважды.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import contacts as cfg
from backend.features.contacts import dead
from backend.features.contacts.preference import DEAD
from backend.features.contacts.provider import (
    ProviderBlockedError,
    ProviderDomainRefusedError,
    ProviderError,
    ProviderQuotaError,
    ProviderRateLimitError,
)
from backend.features.contacts.quality import Candidate
from backend.features.contacts.verifier import (
    ACCEPT_ALL,
    DISPOSABLE,
    INVALID,
    UNKNOWN,
    VALID,
    WEBMAIL,
    EmailVerifier,
    NoKeyError,
    StillCheckingError,
    Verdict,
)
from backend.features.core import usage
from backend.features.core.domain import ContactSource, Stage
from backend.features.core.hunter_cap import (
    OUTREACH_VERIFY_OPERATION,
    VERIFY_OPERATIONS,
    verify_month_cap,
)
from backend.features.core.models.donor import ContactModel
from backend.features.runs.spending import units_this_month
from backend.shared.numbers import spaced

logger = logging.getLogger(__name__)

#: Операция журнала расхода: проверка адреса перед первым письмом, единица — проверка.
OPERATION = OUTREACH_VERIFY_OPERATION

#: Слова Hunter «проверить нельзя»: письмо собирается, число таких — на экране писем.
UNVERIFIABLE = frozenset({ACCEPT_ALL, WEBMAIL, UNKNOWN})
#: Слова Hunter «писать некуда»: адрес хоронится, как при отказе доставки.
GONE = frozenset({INVALID, DISPOSABLE})
#: Отметки проверки в `verification_status`, с которыми письмо собирается, пока срок не вышел.
MARKS = frozenset({VALID, *UNVERIFIABLE})

#: Слова проверки, с которыми платный поиск отдаёт адрес и которым верим без вызова.
FOUND_MARKS = frozenset({VALID, ACCEPT_ALL})

#: Отказы, после которых проверка встаёт до конца сборки: следующий адрес получил бы то же.
STOPS: tuple[type[ProviderError], ...] = (
    ProviderQuotaError,
    ProviderBlockedError,
    ProviderRateLimitError,
    NoKeyError,
)

STOPPED_BY_HAND = "проверки адресов остановлены на экране «Расход» (потолок 0)"
DELETED = "адрес удалён после отбора"


def reached(cap: int) -> str:
    return (
        f"потолок проверки адресов на месяц выбран ({spaced(cap)}) — поднять его можно на экране "
        "«Расход», иначе продолжит следующий месяц"
    )


def enabled(stage: Stage) -> bool:
    """Проверяется ли адрес перед первым письмом этапа — рубильник настроек. У продаж проверка
    своя (`sales/verifier.py`), и этого рубильника у них нет."""
    return cfg.VERIFY_BEFORE_LETTER.get(stage.value, False)


class Kind(StrEnum):
    """Чем кончилась проверка адреса для письма."""

    #: Отметка годна — вызова не было.
    TRUSTED = "trusted"
    #: Hunter: ящик существует.
    VALID = "valid"
    #: Проверить нельзя — письмо собирается.
    UNVERIFIABLE = "unverifiable"
    #: Ящика нет или он одноразовый — адрес похоронен.
    GONE = "gone"
    #: Проверка не состоялась — письма этому адресу сейчас нет.
    HELD = "held"


@dataclass(frozen=True, slots=True)
class Checked:
    """Исход проверки одного адреса."""

    kind: Kind
    #: Почему проверка не состоялась — словами (`Kind.HELD`).
    said: str = ""
    #: Проверка не состоялась у одного этого адреса, а не у сервиса: «ещё проверяет» (202).
    #: Следующий адрес спрашивать можно — пачка отправки из-за него не встаёт.
    lone: bool = False

    @property
    def passes(self) -> bool:
        """Собирается ли письмо на этот адрес."""
        return self.kind in (Kind.TRUSTED, Kind.VALID, Kind.UNVERIFIABLE)


def _in_term(contact: Any, now: datetime) -> ColumnElement[bool]:
    return and_(
        contact.verified_at.is_not(None),
        contact.verified_at >= now - timedelta(days=cfg.VERIFY_TTL_DAYS),
    )


def unverifiable(contact: Any, now: datetime) -> ColumnElement[bool]:
    """У адреса отметка «проверить нельзя» в сроке давности — число «не проверить» на экране."""
    return and_(
        contact.source != ContactSource.MANUAL,
        contact.verification_status.in_(sorted(UNVERIFIABLE)),
        _in_term(contact, now),
    )


def passing(contact: Any, now: datetime) -> ColumnElement[bool]:
    """Письмо на адрес соберётся без вызова: он от самого адресата или его отметка проверки
    в сроке давности. То же правило, что `kind_of`, запросом — для чисел экрана."""
    marked = and_(contact.verification_status.in_(sorted(MARKS)), _in_term(contact, now))
    return and_(
        contact.verification_status.is_distinct_from(DEAD),
        or_(contact.source == ContactSource.MANUAL, marked),
    )


def kind_of(contact: ContactModel, now: datetime) -> Kind | None:
    """Что отметка адреса говорит без вызова; `None` — адрес надо проверить."""
    if contact.verification_status == DEAD:
        return Kind.GONE
    if contact.source is ContactSource.MANUAL:
        return Kind.TRUSTED
    at = contact.verified_at
    fresh = at is not None and at >= now - timedelta(days=cfg.VERIFY_TTL_DAYS)
    return Kind.TRUSTED if fresh and contact.verification_status in MARKS else None


def found_mark(found: Candidate) -> dict[str, Any]:
    """Отметка адреса, который платный поиск отдал уже проверенным, — колонками `contacts`
    (`repository.save_addresses`). Верим двум словам: «существует» и «принимает всё» — второе
    проверка перед письмом услышала бы снова, за те же полкредита. Прочее — без отметки: адрес
    спросят перед письмом. День — день проверки у Hunter: старше срока давности — спросят снова.
    Балл не пишется: порядок адресов домена (`preference.py`) от находки не меняется."""
    word, on = found.verified or ("", None)
    if on is None or word not in FOUND_MARKS:
        return {"verification_status": None, "verified_at": None}
    at = datetime.combine(on, time.min, tzinfo=UTC)
    return {"verification_status": word, "verified_at": at}


async def month_spent(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Проверок адреса с первого числа — продаж и этих вместе: потолок у них один."""
    spent = 0
    for operation in VERIFY_OPERATIONS:
        spent += await units_this_month(session, operation, now=now)
    return spent


class Checker:
    """Проверка адресов пачкой — под месячным потолком, с отметкой и расходом у каждого."""

    def __init__(
        self,
        session: AsyncSession,
        verifier: EmailVerifier,
        *,
        stage: Stage,
        now: datetime | None = None,
    ) -> None:
        self._session = session
        self._verifier = verifier
        self._stage = stage
        self._now = now
        #: Почему проверка встала до конца сборки — словами; `None` — идёт.
        self.stopped: str | None = None

    async def check(self, contact_ids: Sequence[int]) -> dict[int, Checked]:
        """Исход по каждому адресу пачки. Запросы к Hunter идут разом, отметки и расход
        пишутся после них по одному и фиксируются: вердикт пачки не теряется."""
        moment = self._now or datetime.now(UTC)
        rows = await self._session.scalars(
            select(ContactModel).where(ContactModel.id.in_(list(contact_ids)))
        )
        contacts = {contact.id: contact for contact in rows}
        done: dict[int, Checked] = {}
        asking: list[ContactModel] = []
        for contact_id in contact_ids:
            contact = contacts.get(contact_id)
            known = Kind.HELD if contact is None else kind_of(contact, moment)
            if contact is not None and known is None:
                asking.append(contact)
            elif known is not None:
                done[contact_id] = Checked(known, DELETED if contact is None else "")
        allowed = await self._allowed(asking, done, moment)
        answers = await asyncio.gather(
            *(self._verifier.verify(contact.email) for contact in allowed),
            return_exceptions=True,
        )
        failure: BaseException | None = None
        for contact, answer in zip(allowed, answers, strict=True):
            if isinstance(answer, Verdict):
                usage.record(
                    self._session, operation=OPERATION, units=answer.units, stage=self._stage
                )
                done[contact.id] = await self._marked(contact, answer.status, answer.score, moment)
            elif isinstance(answer, ProviderDomainRefusedError):
                # Не про ящик: сервис не работает с доменом — проверить нельзя, единицы нет.
                done[contact.id] = await self._marked(contact, UNKNOWN, None, moment)
            elif isinstance(answer, ProviderError):
                done[contact.id] = self._refused(contact, answer)
            else:
                failure = failure or answer
        # Чекпоинт: вердикты пачки — в базе до писем и до следующей пачки.
        await self._session.commit()
        if failure is not None:
            raise failure
        return done

    async def _allowed(
        self, asking: list[ContactModel], done: dict[int, Checked], moment: datetime
    ) -> list[ContactModel]:
        """Кого из пачки спросим: сколько осталось до месячного потолка. Остальным — причина
        словами; потолок выбран, не задан или ноль — проверка встаёт до конца сборки."""
        if not asking:
            return []
        left = 0
        if self.stopped is None:
            cap = await verify_month_cap(self._session)
            if isinstance(cap, str):
                self.stopped = cap
            elif cap == 0:
                self.stopped = STOPPED_BY_HAND
            else:
                left = max(0, cap - await month_spent(self._session, now=moment))
                if left < len(asking):
                    self.stopped = reached(cap)
        allowed = asking if self.stopped is None else asking[:left]
        for contact in asking[len(allowed) :]:
            done[contact.id] = Checked(Kind.HELD, self.stopped or "")
        return allowed

    async def _marked(
        self, contact: ContactModel, status: str, score: int | None, moment: datetime
    ) -> Checked:
        """Слово Hunter — отметкой адреса. «Писать некуда» хоронит адрес, как отказ доставки."""
        contact.verified_at = moment
        if status in GONE:
            await dead.bury(self._session, contact.id)
            logger.info("проверка адреса: адрес №%s — %s, похоронен", contact.id, status)
            return Checked(Kind.GONE)
        contact.verification_status = status
        contact.verification_score = score
        return Checked(Kind.VALID if status == VALID else Kind.UNVERIFIABLE)

    def _refused(self, contact: ContactModel, exc: ProviderError) -> Checked:
        """Вердикта нет: письма этому адресу сейчас не будет, а отказ учётке или частоте
        останавливает проверку до конца сборки."""
        said = str(exc)
        if isinstance(exc, STOPS) and self.stopped is None:
            self.stopped = said
        logger.warning("проверка адреса: адрес №%s не проверен — %s", contact.id, said)
        return Checked(Kind.HELD, said, lone=isinstance(exc, StillCheckingError))
