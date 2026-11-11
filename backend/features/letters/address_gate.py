"""Проверка адреса при сборке очереди — до вызова модели (11.11.2026).

Сборка «Собрать очередь» стоит вызова модели на письмо, и тратить его на адрес, которого
нет, незачем: между отбором адресатов и моделью сборка спрашивает, существует ли адрес
(`contacts/address_check.py` — что делаем с каким ответом и чему верим без вызова).

**Почему здесь, а не шагом после поиска адреса.** Поиск находит адреса всем, кому они нужны,
а письмо получают не все: донора ещё не приняли, у рекламодателя нет свежей цены донора, сайт в
стоп-листе. Здесь проверяется ровно тот адрес, на который письмо сейчас соберётся, — и в ту же
минуту: проверка не стареет в ожидании письма. Сборка — уже фоновая задача с повтором и
чекпоинтом на письмо; новой задачи, очереди и расписания проверка не заводит. И лестница поиска
общая со сбором базы продаж, а у них проверка своя.

**Порядок** (`Picker`): пачка отбора → потолок модели → проверка адресов пачки → модель пишет
письма тем, чей адрес прошёл. Адреса нет — он похоронен, и следующий круг отбора отдаёт
следующий адрес домена по порядку предпочтения (`contacts/preference.py`), его проверяют так
же; кругов — не больше потолка адресов сайта (`attempts.MAX_ADDRESSES`). Адресов не осталось —
домен выпадает из отбора и виден числом «адрес не существует» в воронке. Сборка добирает до
своего числа писем следующими адресатами отбора.

**Проверка не состоялась — письма нет**, причина словами в итоге сборки (`BuildReport.unchecked`).
Адресаты с годной отметкой письма получают и тогда.

**Повтор безопасен.** Вердикт — отметка адреса, зафиксированная до письма: оборванная сборка
продолжает с места, проверенный адрес второй раз не спрашивается, расход дважды не пишется.

**Письмо, уже стоящее в очереди, проверяется перед отправкой** (`queued_refusal`): очередь,
собранную до проверки, и письмо, чья отметка успела устареть, пачка и проход автоотправки
спрашивают тем же правилом — по письму, прямо перед ним. Ящика нет — письмо снимается как не
уходившее (`attempts.ADDRESS_GONE`), и следующая сборка напишет сайту на следующий адрес;
проверка не состоялась — письмо ждёт в очереди, причина — в итоге пачки. Адрес, на который
письмо уже уходило, не спрашивается: проверка — до первого письма. Кнопка «Отправить» у одного
письма проверку не зовёт: его прочёл и отправляет человек.

**Рубильник** (`CONTACTS_VERIFY_DONORS`, `CONTACTS_VERIFY_ADVERTISERS`, включены): выключен —
сборка идёт, как до проверки, чисел проверки в воронке нет.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime
from types import TracebackType
from typing import TYPE_CHECKING, Any, Self

import httpx
from sqlalchemy import ScalarSelect, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from backend.features.contacts import address_check
from backend.features.contacts.address_check import Checked, Checker, Kind
from backend.features.contacts.preference import DEAD
from backend.features.contacts.verifier import EmailVerifier, HunterVerifier
from backend.features.core.domain import Stage
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.outreach import MessageModel
from backend.features.crawl.advertiser_rules import in_work
from backend.features.letters import attempts
from backend.features.letters.donor_standing import stop_for_good

if TYPE_CHECKING:
    from backend.features.letters.building import BuildReport, BuildRequest
    from backend.features.letters.recipients import Candidate
    from backend.features.letters.repository import LetterRepository

#: Числа проверки в воронке экрана писем — словами экрана (`frontend/src/letters/addressCheck.ts`).
CHECKED_STEP = "адрес проверен"
UNVERIFIABLE_NOTE = "не проверить"
GONE_NOTE = "адрес не существует"
WAITING_NOTE = "ждут проверки адреса"

#: Исход проверки адреса → строка итога сборки (`BuildReport.address_check`).
REPORT_WORDS: dict[Kind, str] = {
    Kind.TRUSTED: "проверены раньше",
    Kind.VALID: "существует",
    Kind.UNVERIFIABLE: "проверить нельзя",
    Kind.GONE: "не существует",
    Kind.HELD: "не проверено",
}


#: Почему письмо очереди не ушло из-за адреса — словами итога пачки (`batch.BatchReport`).
UNCHECKED = "адрес не проверен"


@asynccontextmanager
async def checker_of(
    session: AsyncSession, stage: Stage, verifier: EmailVerifier | None = None
) -> AsyncIterator[Checker | None]:
    """Проверка адресов этапа на время сборки или пачки; `None` — рубильник этапа выключен.
    Без своего проверяльщика — Hunter по настройкам, со своим клиентом на это время."""
    if not address_check.enabled(stage):
        yield None
    elif verifier is not None:
        yield Checker(session, verifier, stage=stage)
    else:
        async with httpx.AsyncClient() as http:
            yield Checker(session, HunterVerifier(http), stage=stage)


async def queued_refusal(session: AsyncSession, checker: Checker, letter_id: int) -> str | None:
    """Почему письмо очереди сейчас не уйдёт из-за адреса; `None` — адрес не мешает.

    Спрашивается адрес, на который писем ещё не уходило и у которого нет годной отметки: очередь,
    собранная до проверки. Ящика нет — письмо снято как не уходившее, следующая сборка возьмёт
    следующий адрес сайта."""
    letter = await session.get(MessageModel, letter_id)
    contact = (
        None
        if letter is None or letter.contact_id is None
        else await session.get(ContactModel, letter.contact_id)
    )
    if letter is None or contact is None or contact.last_contacted_at is not None:
        return None
    done = (await checker.check([contact.id]))[contact.id]
    if done.passes:
        return None
    if done.kind is Kind.GONE:
        await stop_for_good(session, letter, attempts.ADDRESS_GONE)
        return GONE_NOTE
    # Сервис отказал (сеть, 5xx, непонятный ответ) — остальные письма этой пачки уже не
    # спрашиваем: проход автоотправки идёт каждую минуту, и сбой сервиса иначе получал бы запрос
    # на каждое письмо очереди. «Ещё проверяет» — про один адрес (`Checked.lone`): его письмо
    # ждёт, следующее спрашивается — иначе вечно проверяемый адрес держал бы за собой очередь.
    if not done.lone:
        checker.stopped = checker.stopped or done.said
    return f"{UNCHECKED} — {done.said}"


class Picker:
    """Адресаты сборки пачками — только те, чей адрес прошёл проверку (шапка модуля).

    Проверка этапа выключена — пачки те же, что до неё: один отбор, без кругов и вызовов.
    `bad` — перепроверка адреса фильтром качества (`QueueBuilder._bad_address`), `go` — потолок
    модели перед пачкой: не прошёл — сборка кончена, адреса пачки не спрашиваются.
    """

    def __init__(
        self,
        session: AsyncSession,
        repository: LetterRepository,
        request: BuildRequest,
        report: BuildReport,
        *,
        size: int,
        bad: Callable[[Candidate, BuildReport], bool],
        go: Callable[[], Awaitable[bool]],
        verifier: EmailVerifier | None = None,
    ) -> None:
        self._session = session
        self._repository = repository
        self._request = request
        self._report = report
        self._size = size
        self._bad = bad
        self._go = go
        self._verifier = verifier
        self._service = AsyncExitStack()
        self._checker: Checker | None = None
        #: Адресаты круга, ещё не отданные сборке.
        self._todo: list[Candidate] = []
        #: Домены, решённые этой сборкой: письмо собрано или сейчас не соберётся.
        self._skip: set[int] = set()
        self._round = 0
        self._buried = 0
        self._left = request.limit
        self._over = False
        #: Сколько адресатов отбор отдал сборке — для строки журнала.
        self.seen = 0

    async def __aenter__(self) -> Self:
        self._checker = await self._service.enter_async_context(
            checker_of(self._session, self._request.stage, self._verifier)
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._service.aclose()

    async def next_pack(self) -> list[Candidate]:
        """Следующая пачка адресатов с проверенным адресом; пусто — собирать больше некого."""
        while True:
            if not self._todo and not await self._next_round():
                return []
            pack, self._todo = self._todo[: self._size], self._todo[self._size :]
            if not await self._go():
                self._todo, self._over = [], True
                return []
            passed = await self._check(pack)
            if passed:
                return passed

    async def _next_round(self) -> bool:
        """Новый отбор: первый — всегда; следующий — когда прошлый похоронил адрес (у домена
        может быть следующий) и сборка не добрала своего числа писем."""
        rounds = attempts.MAX_ADDRESSES if self._checker is not None else 1
        again = self._round == 0 or (self._buried > 0 and self._left > 0)
        if self._over or self._round >= rounds or not again:
            return False
        self._round += 1
        self._buried = 0
        request = self._request
        found = await self._repository.candidates(
            request.stage, limit=request.limit, run_ids=request.run_ids, audience=request.audience
        )
        for candidate in found:
            if candidate.domain_id in self._skip:
                continue
            self.seen += 1
            if self._bad(candidate, self._report):
                self._skip.add(candidate.domain_id)
            else:
                self._todo.append(candidate)
        self._todo = self._todo[: self._left]
        return bool(self._todo)

    async def _check(self, pack: list[Candidate]) -> list[Candidate]:
        """Кому из пачки письмо собирается. Похороненный адрес домен не закрывает: следующий
        круг отдаст его следующий адрес."""
        if self._checker is None:
            return pack
        outcomes = await self._checker.check([candidate.contact_id for candidate in pack])
        passed: list[Candidate] = []
        for candidate in pack:
            done = outcomes[candidate.contact_id]
            self._count(done)
            if done.kind is Kind.GONE:
                self._buried += 1
                continue
            self._skip.add(candidate.domain_id)
            if done.passes:
                passed.append(candidate)
        self._left -= len(passed)
        return passed

    def _count(self, done: Checked) -> None:
        counts = self._report.address_check
        words = REPORT_WORDS[done.kind]
        counts[words] = counts.get(words, 0) + 1
        if done.kind is Kind.HELD and self._report.unchecked is None:
            self._report.unchecked = done.said


def _gone_domains(stage: Stage) -> ScalarSelect[int]:
    """Сколько адресатов этапа выпало до первого письма, потому что проверка похоронила адрес,
    а другого у домена нет: отметка мёртвого с часом проверки, писем на адрес не уходило."""
    contact, letter = aliased(ContactModel), aliased(MessageModel)
    untried = ~(
        select(letter.id)
        .where(letter.contact_id == contact.id, ~attempts.withdrawn(letter))
        .exists()
    )
    role: Select[Any]
    if stage is Stage.DONORS:
        role = select(DonorModel.id).where(
            DonorModel.domain_id == contact.domain_id, DonorModel.review == "accepted"
        )
    else:
        role = select(AdvertiserModel.id).where(
            AdvertiserModel.domain_id == contact.domain_id, in_work()
        )
    return (
        select(func.count(func.distinct(contact.domain_id)))
        .where(
            contact.verification_status == DEAD,
            contact.verified_at.is_not(None),
            untried,
            role.exists(),
            ~attempts.fresh_address(contact.domain_id),
            ~attempts.written_before(contact.domain_id),
        )
        .scalar_subquery()
    )


async def funnel_counts(
    session: AsyncSession, stage: Stage, picks: Select[Any], *, now: datetime | None = None
) -> dict[str, int]:
    """Числа проверки адресов для воронки экрана писем. `picks` — адрес первого письма на
    каждого, кому сборка может писать (`Recipients.picks`): у скольких он проверен, у скольких
    из них проверить нельзя и сколько ждут проверки — она идёт при сборке. Проверка этапа
    выключена — пусто: воронка та же, что до неё."""
    if not address_check.enabled(stage):
        return {}
    moment = now or datetime.now(UTC)
    picked = picks.subquery()
    contact = aliased(ContactModel)
    row = (
        await session.execute(
            select(
                func.count().label("everyone"),
                func.count().filter(address_check.passing(contact, moment)).label("checked"),
                func.count()
                .filter(address_check.unverifiable(contact, moment))
                .label("unverifiable"),
                _gone_domains(stage).label("gone"),
            )
            .select_from(picked)
            .join(contact, contact.id == picked.c.contact_id)
        )
    ).one()
    counts = {CHECKED_STEP: int(row.checked)}
    notes = {
        UNVERIFIABLE_NOTE: int(row.unverifiable),
        GONE_NOTE: int(row.gone or 0),
        WAITING_NOTE: int(row.everyone) - int(row.checked),
    }
    return counts | {name: count for name, count in notes.items() if count > 0}
