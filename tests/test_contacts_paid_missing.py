"""Платной ступени нет на этот проход — исход называет причину, а не «адреса нет» на полгода.

Находка продаж 20.10.2026: `search.paid_step` возвращал «ступени нет» и тогда, когда
квота кончилась или её остаток не узнать, — лестница видела это как «ключа нет», и сайт
без адреса на страницах получал «адреса нет» на `CONTACTS_TTL_DAYS`, хотя квота
возвращается в начале месяца. Теперь причина едет в лестницу (`PaidUnavailable`) — через
отчёт прохода (`SearchReport.paid_missing`), а ответ `paid_step` по-прежнему только «готова
ли ступень»: продажи спрашивают `paid_step(...) is not None` и подменяют её в своих тестах.

Правило (ревью 20.10.2026):

- отказ остатка разбирается, как отказ посреди прохода: квота — `no_quota`, закрытая
  учётка и непринятый ключ — `blocked`, сеть и 5xx — `error`;
- «недоспрошен» повторяется через сутки (`attempts.PAID_RETRY_DELAY`), а не каждым
  проходом: автопоиск после каждого «принять» брал бы те же домены снова;
- сайт с формой — «только форма» сразу: иначе он не попал бы в «Формы» до квоты.

Ключа нет, платный поиск выключен — рабочий режим: «адреса нет», как и было. Сеть
подменена: сайт — заглушка, платный сервис — заглушка, DNS не трогается.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from backend.config import contacts as contacts_cfg
from backend.features.contacts import attempts, mx, search
from backend.features.contacts.ladder import ContactLadder, LadderResult
from backend.features.contacts.provider import HunterProvider, PaidUnavailable, ProviderQuotaError
from backend.features.contacts.quality import Candidate
from backend.features.contacts.repository import ContactRepository, refusal_of
from backend.features.contacts.search import SearchReport, search_contacts
from backend.features.core.domain import ContactStatus, DonorStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

HOST = "plain.example.test"
EMPTY = "<html><body><p>Пекарня у дома</p></body></html>"
WITH_FORM = (
    '<html><body><form><input name="email"><textarea name="m"></textarea></form></body></html>'
)
NO_QUOTA = PaidUnavailable(ContactStatus.NO_QUOTA, "квота платного сервиса исчерпана")
UNKNOWN = PaidUnavailable(ContactStatus.ERROR, "остаток платного сервиса не узнать")
CLOSED = PaidUnavailable(ContactStatus.BLOCKED, "учётка закрыта провайдером")
ZERO = {"data": {"requests": {"searches": {"used": 100, "available": 100}}}}


@pytest.fixture(autouse=True)
def mail(monkeypatch: pytest.MonkeyPatch) -> Callable[[mx.MailRoute], None]:
    """Почта у домена есть; тест, которому нужен нулевой MX, ставит его сам."""
    state = {"route": mx.MailRoute.MX}

    async def route(_host: str, **_kwargs: object) -> mx.MailRoute:
        return state["route"]

    monkeypatch.setattr("backend.features.contacts.ladder.mail_route", route)
    return lambda value: state.update(route=value)


def _site(home: str) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in ("", "/"):
            return httpx.Response(200, html=home, request=request)
        return httpx.Response(404, request=request)

    return httpx.MockTransport(handler)


async def _find(home: str, **ladder: object) -> LadderResult:
    async with httpx.AsyncClient(transport=_site(home)) as http:
        return await ContactLadder(http, **ladder).find(HOST)  # type: ignore[arg-type]


class _QuotaGone:
    """Платный сервис, у которого квота кончилась посреди прохода."""

    name = "stub"

    async def find_emails(self, host: str) -> list[Candidate]:
        raise ProviderQuotaError(f"квота исчерпана на {host}")


class TestTheLadder:
    async def test_exhausted_quota_leaves_the_domain_unasked(self) -> None:
        result = await _find(EMPTY, provider=NO_QUOTA)

        assert result.status is ContactStatus.NO_QUOTA

    async def test_unknown_balance_is_an_error_to_repeat(self) -> None:
        result = await _find(EMPTY, provider=UNKNOWN)

        assert result.status is ContactStatus.ERROR

    async def test_closed_account_is_named_as_mid_pass(self) -> None:
        result = await _find(EMPTY, provider=CLOSED)

        assert result.status is ContactStatus.BLOCKED

    async def test_without_a_key_the_site_has_no_address_as_before(self) -> None:
        result = await _find(EMPTY, provider=None)

        assert result.status is ContactStatus.NOT_FOUND

    async def test_null_mx_does_not_wait_for_the_quota(
        self, mail: Callable[[mx.MailRoute], None]
    ) -> None:
        """Нулевой MX — платить за домен незачем и после пополнения: «адреса нет»."""
        mail(mx.MailRoute.NULL_MX)

        result = await _find(EMPTY, provider=NO_QUOTA)

        assert result.status is ContactStatus.NOT_FOUND

    async def test_silent_site_stays_no_answer(self) -> None:
        result = await _find(EMPTY, provider=NO_QUOTA, silence=lambda: "нет ответа: таймаут")

        assert result.status is ContactStatus.NO_ANSWER
        assert result.reason == "нет ответа: таймаут"

    @pytest.mark.parametrize(
        "missing", [NO_QUOTA, UNKNOWN, CLOSED], ids=["квота", "сбой", "учётка"]
    )
    async def test_site_with_a_form_goes_to_the_forms_now(self, missing: PaidUnavailable) -> None:
        """Форма есть — «только форма» сразу, а не когда вернётся квота (ревью 20.10.2026)."""
        result = await _find(WITH_FORM, provider=missing)

        assert result.status is ContactStatus.FORM_ONLY
        assert result.has_form

    async def test_quota_gone_mid_pass_sends_the_form_too(self) -> None:
        """Квота кончилась посреди прохода — то же правило, что и до него."""
        result = await _find(WITH_FORM, provider=_QuotaGone())

        assert result.status is ContactStatus.FORM_ONLY

    async def test_address_on_the_pages_does_not_need_the_quota(self) -> None:
        home = f'<html><body><a href="mailto:info@{HOST}">почта</a></body></html>'
        result = await _find(home, provider=NO_QUOTA)

        assert result.status is ContactStatus.FOUND


class Queue:
    """Очередь из одного домена: проверяется, что лестница отдала очереди."""

    stage = Stage.DONORS

    def __init__(self) -> None:
        self.saved: list[LadderResult] = []

    async def pending_hosts(self, *, limit: int = 100) -> list[str]:
        return [HOST][:limit]

    async def save(self, results: Sequence[LadderResult]) -> int:
        self.saved.extend(results)
        return 0

    async def last_tries(self, hosts: Sequence[str]) -> set[str]:
        return set()


def _hunter(account: httpx.Response | Exception) -> Callable[..., httpx.AsyncClient]:
    """Сеть прохода: сайт без адреса и платный сервис, чей остаток отвечает `account`."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.hunter.io":
            if isinstance(account, Exception):
                raise account
            return account
        if request.url.path in ("", "/"):
            return httpx.Response(200, html=EMPTY, request=request)
        return httpx.Response(404, request=request)

    return lambda **_kw: httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _refusal(marker: str, code: int) -> httpx.Response:
    return httpx.Response(code, json={"errors": [{"id": marker, "details": "отказ"}]})


#: Ответ остатка → исход домена без адреса на страницах: как отказ посреди прохода.
REFUSALS = pytest.mark.parametrize(
    ("account", "status"),
    [
        (httpx.Response(200, json=ZERO), ContactStatus.NO_QUOTA),
        (_refusal("usage_exceeded", 429), ContactStatus.NO_QUOTA),
        (_refusal("restricted_account", 403), ContactStatus.BLOCKED),
        (_refusal("restricted_account", 429), ContactStatus.BLOCKED),
        (_refusal("authentication_failed", 401), ContactStatus.BLOCKED),
        (httpx.Response(401, text="<html>unauthorized</html>"), ContactStatus.BLOCKED),
        (httpx.Response(500, json={"errors": [{"details": "сбой"}]}), ContactStatus.ERROR),
        (httpx.ConnectError("нет сети"), ContactStatus.ERROR),
    ],
    ids=[
        "остаток ноль",
        "квота на /account",
        "закрытая учётка 403",
        "закрытая учётка 429",
        "ключ не принят",
        "401 без JSON",
        "5xx",
        "сеть",
    ],
)


class _Ready:
    """Подделка платного сервиса, какой её ставят тесты продаж: готов и ничего не знает."""

    name = "stub"

    async def find_emails(self, _host: str) -> list[Candidate]:
        return []


class TestTheSearchPass:
    @pytest.fixture(autouse=True)
    def _paid_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(contacts_cfg, "PAID_SEARCH_ENABLED", True)
        monkeypatch.setattr(contacts_cfg, "HUNTER_API_KEY", "k-test")

    @REFUSALS
    async def test_paid_step_answers_only_whether_the_step_is_ready(
        self, account: httpx.Response | Exception, status: ContactStatus
    ) -> None:
        """Контракт продаж (`paid_step(...) is not None` — «ступень готова»): на отказе
        остатка ответ — `None`, а причина для лестницы — в отчёте прохода."""
        report = SearchReport()
        async with _hunter(account)() as http:
            answer = await search.paid_step(http, report)

        assert answer is None
        assert report.paid_missing is not None
        assert report.paid_missing.status is status

    async def test_paid_step_with_balance_left_is_the_provider(self) -> None:
        left = {"data": {"requests": {"searches": {"used": 10, "available": 100}}}}
        report = SearchReport()
        async with _hunter(httpx.Response(200, json=left))() as http:
            answer = await search.paid_step(http, report)

        assert isinstance(answer, HunterProvider)
        assert report.paid_missing is None

    async def test_substitute_answering_none_means_no_step_as_before(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Подделка `paid_step` из тестов продаж, ответившая `None`, — «ступени нет»: сайт
        без адреса — «адреса нет», как до правки, хотя настоящий остаток был бы нулём."""

        async def no_step(_http: httpx.AsyncClient, _report: SearchReport) -> None:
            return None

        monkeypatch.setattr(search, "paid_step", no_step)
        monkeypatch.setattr(
            "backend.features.contacts.search.guarded_client",
            _hunter(httpx.Response(200, json=ZERO)),
        )
        queue = Queue()

        await search_contacts(session, limit=1, queue=queue)

        assert [result.status for result in queue.saved] == [ContactStatus.NOT_FOUND]

    async def test_substitute_answering_a_provider_is_asked(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def ready(_http: httpx.AsyncClient, _report: SearchReport) -> _Ready:
            return _Ready()

        monkeypatch.setattr(search, "paid_step", ready)
        monkeypatch.setattr(
            "backend.features.contacts.search.guarded_client",
            _hunter(httpx.Response(200, json=ZERO)),
        )
        queue = Queue()

        report = await search_contacts(session, limit=1, queue=queue)

        assert [result.status for result in queue.saved] == [ContactStatus.NOT_FOUND]
        assert report.counters["provider_entered"] == 1

    @REFUSALS
    async def test_balance_refusal_is_named_as_mid_pass(
        self,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        account: httpx.Response | Exception,
        status: ContactStatus,
    ) -> None:
        monkeypatch.setattr("backend.features.contacts.search.guarded_client", _hunter(account))
        queue = Queue()

        report = await search_contacts(session, limit=1, queue=queue)

        assert [result.status for result in queue.saved] == [status]
        # Причина отказа — словами в отчёте прохода (`PaidUnavailable.reason`).
        assert any(note.startswith("Платная ступень не зовётся:") for note in report.notes)

    async def test_switched_off_paid_search_is_the_working_mode(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Флаг выключен (решение 12.10.2026) — «адреса нет», как и было: это не сбой."""
        monkeypatch.setattr(contacts_cfg, "PAID_SEARCH_ENABLED", False)
        monkeypatch.setattr(
            "backend.features.contacts.search.guarded_client",
            _hunter(httpx.Response(500, json={})),
        )
        queue = Queue()

        await search_contacts(session, limit=1, queue=queue)

        assert [result.status for result in queue.saved] == [ContactStatus.NOT_FOUND]

    async def test_no_key_is_the_working_mode(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(contacts_cfg, "HUNTER_API_KEY", "")
        monkeypatch.setattr(
            "backend.features.contacts.search.guarded_client",
            _hunter(httpx.Response(500, json={})),
        )
        queue = Queue()

        await search_contacts(session, limit=1, queue=queue)

        assert [result.status for result in queue.saved] == [ContactStatus.NOT_FOUND]


async def _accepted_donor(session: AsyncSession, host: str) -> DonorModel:
    domain = DomainModel(host=host)
    session.add(domain)
    await session.flush()
    donor = DonorModel(domain_id=domain.id, status=DonorStatus.SUITABLE, dr=40, review="accepted")
    session.add(donor)
    await session.flush()
    return donor


class TestRetryWindow:
    """«Недоспрошен» повторяется через сутки, а не каждым проходом (ревью 20.10.2026)."""

    @pytest.fixture(autouse=True)
    def _empty_quota(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(contacts_cfg, "PAID_SEARCH_ENABLED", True)
        monkeypatch.setattr(contacts_cfg, "HUNTER_API_KEY", "k-test")
        monkeypatch.setattr(
            "backend.features.contacts.search.guarded_client",
            _hunter(httpx.Response(200, json=ZERO)),
        )

    async def test_second_pass_in_a_row_does_not_take_the_domain(
        self, session: AsyncSession
    ) -> None:
        donor = await _accepted_donor(session, HOST)

        first = await search_contacts(session, limit=50)
        second = await search_contacts(session, limit=50)

        assert (first.pending, second.pending) == (1, 0)
        await session.refresh(donor)
        assert donor.contact_status is ContactStatus.NO_QUOTA
        tomorrow = datetime.now(UTC) + attempts.PAID_RETRY_DELAY + timedelta(minutes=1)
        assert await ContactRepository(session).pending_hosts(now=tomorrow) == [HOST]

    async def test_card_says_when_the_search_comes_back(self, session: AsyncSession) -> None:
        """Карточка не обещает поиск, который сервер отказал бы, и называет срок."""
        donor = await _accepted_donor(session, HOST)
        await search_contacts(session, limit=50)
        await session.refresh(donor)

        refusal = refusal_of(donor, now=datetime.now(UTC))

        assert refusal is not None
        assert refusal.startswith("Платная ступень в прошлый раз не спросила (кончилась квота)")
        assert (
            await session.scalar(select(DonorModel.contact_tries).where(DonorModel.id == donor.id))
            == 0
        )

    @pytest.mark.parametrize(
        ("status", "waits"),
        [
            (ContactStatus.NO_QUOTA, False),
            (ContactStatus.ERROR, False),
            (ContactStatus.RATE_LIMITED, True),
        ],
    )
    async def test_rule_reads_the_same_in_query_and_in_the_row(
        self, session: AsyncSession, status: ContactStatus, waits: bool
    ) -> None:
        """Частота — следующим проходом: он встаёт сам и застаёт лимит снятым; квота и
        сбой — через сутки. Запрос очереди и правило строки не расходятся."""
        now = datetime.now(UTC)
        donor = await _accepted_donor(session, HOST)
        donor.contact_status, donor.contact_attempted_at = status, now - timedelta(hours=2)
        await session.flush()

        ttl, unasked = contacts_cfg.CONTACT_TTL_DAYS, attempts.general_queue()
        assert attempts.waits(donor, now=now, ttl_days=ttl, unasked=unasked) is waits
        hosts = await ContactRepository(session).pending_hosts(now=now)
        assert hosts == ([HOST] if waits else [])

    async def test_queue_without_its_own_term_repeats_on_the_next_pass(
        self, session: AsyncSession
    ) -> None:
        """Срок — довод очереди: без него (очередь продаж ведёт свои сроки сама) квота
        повторяется следующим проходом, как и раньше."""
        now = datetime.now(UTC)
        donor = await _accepted_donor(session, HOST)
        donor.contact_status = ContactStatus.NO_QUOTA
        donor.contact_attempted_at = now - timedelta(hours=2)
        await session.flush()

        rule = attempts.waiting(DonorModel, now=now, ttl_days=contacts_cfg.CONTACT_TTL_DAYS)
        taken = await session.scalar(select(DonorModel.id).where(DonorModel.id == donor.id, rule))

        assert taken == donor.id
        assert attempts.waits(donor, now=now, ttl_days=contacts_cfg.CONTACT_TTL_DAYS)
