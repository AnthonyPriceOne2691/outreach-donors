"""«Платный поиск выключен — не спросили» (`paid_off`) — исход прохода без платной ступени.

Обещано продажам 20.10.2026: проход, вызванный без платной (`no_paid` — очередь сбора
базы продаж без своего флага, консольные `--no-paid`), отдавал сайту без адреса на
страницах «адреса нет», как если бы платный сервис спросили и он не знал. Теперь —
`paid_off`, а остальные исходы прежние: нулевой MX — «адреса нет», молчание — «не
ответил», форма — «только форма»; ключа нет или у доноров выключен флаг — «адреса нет»,
как и было (`test_contacts_paid_missing.py`). Общая очередь держит `paid_off` как «адреса
нет», сроком `CONTACTS_TTL_DAYS`; брать раньше будет очередь продаж — это её код.

Поиск глубже (`deeper.py`) зовётся без платной по устройству: его исход — «адреса нет».
Сеть подменена: сайт — заглушка, DNS не трогается.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.config import contacts as contacts_cfg
from backend.features.contacts import attempts, mx
from backend.features.contacts.ladder import ContactLadder, LadderResult
from backend.features.contacts.repository import ContactRepository
from backend.features.contacts.search import PAID_OFF, search_contacts
from backend.features.core.domain import ContactStatus, DonorStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.donors.wording import CONTACT_STATUS_TITLES
from sqlalchemy import select, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from tests.migration_helpers import load_migration

pytestmark = pytest.mark.asyncio

HOST = "plain.example.test"
EMPTY = "<html><body><p>Пекарня у дома</p></body></html>"
WITH_FORM = (
    '<html><body><form><input name="email"><textarea name="m"></textarea></form></body></html>'
)
REVISION = "1f5d08fe920b_contact_paid_off.py"


@pytest.fixture(autouse=True)
def mail(monkeypatch: pytest.MonkeyPatch) -> Callable[[mx.MailRoute], None]:
    state = {"route": mx.MailRoute.MX}

    async def route(_host: str, **_kwargs: object) -> mx.MailRoute:
        return state["route"]

    monkeypatch.setattr("backend.features.contacts.ladder.mail_route", route)
    return lambda value: state.update(route=value)


def _site(home: str, status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in ("", "/"):
            return httpx.Response(status, html=home, request=request)
        return httpx.Response(404, request=request)

    return httpx.MockTransport(handler)


async def _find(
    home: str, status: int = 200, **ladder: object
) -> tuple[LadderResult, ContactLadder]:
    async with httpx.AsyncClient(transport=_site(home, status)) as http:
        stairs = ContactLadder(http, provider=PAID_OFF, **ladder)  # type: ignore[arg-type]
        return await stairs.find(HOST), stairs


class TestTheLadder:
    async def test_site_without_an_address_was_not_asked(self) -> None:
        result, ladder = await _find(EMPTY)

        assert result.status is ContactStatus.PAID_OFF
        assert result.reason == ""
        assert ladder.counters.paid_off == 1
        assert ladder.counters.not_found == 0

    async def test_unread_site_keeps_its_reason(self) -> None:
        """Сайт не пустил — причина та же, что у «адреса нет» (`Collected.unread_reason`)."""
        result, _ = await _find("<p>Forbidden</p>", status=403)

        assert result.status is ContactStatus.PAID_OFF
        assert result.reason == "сайт не пустил нас (403)"

    async def test_null_mx_has_no_address_as_before(
        self, mail: Callable[[mx.MailRoute], None]
    ) -> None:
        """Платить за домен с нулевым MX незачем и с включённым поиском: «адреса нет»."""
        mail(mx.MailRoute.NULL_MX)

        result, _ = await _find(EMPTY)

        assert result.status is ContactStatus.NOT_FOUND

    async def test_silent_site_did_not_answer(self) -> None:
        result, _ = await _find(EMPTY, silence=lambda: "нет ответа: таймаут")

        assert result.status is ContactStatus.NO_ANSWER

    async def test_site_with_a_form_goes_to_the_forms(self) -> None:
        result, _ = await _find(WITH_FORM)

        assert result.status is ContactStatus.FORM_ONLY

    async def test_address_on_the_pages_is_found(self) -> None:
        result, _ = await _find(f'<html><body><a href="mailto:info@{HOST}">почта</a></body></html>')

        assert result.status is ContactStatus.FOUND


class Queue:
    """Очередь из одного домена: что лестница отдала очереди."""

    stage = Stage.SALES

    def __init__(self, *, deep: bool | None = None) -> None:
        self.saved: list[LadderResult] = []
        if deep is not None:
            self.deep_search = deep

    async def pending_hosts(self, *, limit: int = 100) -> list[str]:
        return [HOST][:limit]

    async def save(self, results: Sequence[LadderResult]) -> int:
        self.saved.extend(results)
        return 0

    async def last_tries(self, hosts: Sequence[str]) -> set[str]:
        return set()


class TestTheSearchPass:
    @pytest.fixture(autouse=True)
    def _site_without_address(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Ключ есть и флаг включён: «не спросили» — от `no_paid`, а не от настроек.
        monkeypatch.setattr(contacts_cfg, "HUNTER_API_KEY", "k-test")
        monkeypatch.setattr(contacts_cfg, "PAID_SEARCH_ENABLED", True)
        monkeypatch.setattr(
            "backend.features.contacts.search.guarded_client",
            lambda **_kw: httpx.AsyncClient(transport=_site(EMPTY)),
        )

    async def test_pass_without_the_paid_step_says_it_did_not_ask(
        self, session: AsyncSession
    ) -> None:
        queue = Queue()

        report = await search_contacts(session, limit=1, queue=queue, no_paid=True)

        assert [result.status for result in queue.saved] == [ContactStatus.PAID_OFF]
        assert report.counters["paid_off"] == 1

    async def test_deeper_search_has_no_address_rather_than_not_asked(
        self, session: AsyncSession
    ) -> None:
        """Глубже платная не зовётся по устройству — исход «адреса нет», как у поиска,
        который глубже перепроверяет."""
        queue = Queue(deep=True)

        await search_contacts(session, limit=1, queue=queue, no_paid=True)

        assert [result.status for result in queue.saved] == [ContactStatus.NOT_FOUND]

    async def test_queue_is_deep_by_the_value_of_its_mark(self, session: AsyncSession) -> None:
        """Признак глубины читается значением: `deep_search = False` — обычная очередь."""
        queue = Queue(deep=False)

        await search_contacts(session, limit=1, queue=queue, no_paid=True)

        assert [result.status for result in queue.saved] == [ContactStatus.PAID_OFF]


async def _donor(session: AsyncSession, host: str, attempted: datetime) -> DonorModel:
    domain = DomainModel(host=host)
    session.add(domain)
    await session.flush()
    donor = DonorModel(
        domain_id=domain.id,
        status=DonorStatus.SUITABLE,
        dr=40,
        review="accepted",
        contact_status=ContactStatus.PAID_OFF,
        contact_attempted_at=attempted,
    )
    session.add(donor)
    await session.flush()
    return donor


class TestTheGeneralQueue:
    async def test_paid_off_is_held_like_no_address(self, session: AsyncSession) -> None:
        now = datetime.now(UTC)
        fresh = await _donor(session, "fresh.example.test", now - timedelta(days=5))
        old = await _donor(
            session, "old.example.test", now - timedelta(days=contacts_cfg.CONTACT_TTL_DAYS + 1)
        )

        hosts = await ContactRepository(session).pending_hosts(now=now)

        assert hosts == ["old.example.test"]
        ttl = contacts_cfg.CONTACT_TTL_DAYS
        assert not attempts.waits(fresh, now=now, ttl_days=ttl)
        assert attempts.waits(old, now=now, ttl_days=ttl)
        assert attempts.again_at(fresh, ttl_days=ttl) == now - timedelta(days=5 - ttl)

    async def test_words_are_the_promised_ones(self) -> None:
        assert (
            CONTACT_STATUS_TITLES[ContactStatus.PAID_OFF] == "платный поиск выключен — не спросили"
        )


def _labels(connection: Connection) -> list[str]:
    rows = connection.execute(text("SELECT unnest(enum_range(NULL::contactstatus))::text"))
    return list(rows.scalars())


def _downgrade(connection: Connection) -> None:
    with Operations.context(MigrationContext.configure(connection)):
        load_migration(REVISION).downgrade()


def _upgrade_on_its_own(connection: Connection) -> list[str]:
    """Подъём ревизии своей транзакцией, как у команды выкатки (`migrations/env.py`):
    значение добавляется вне неё (`autocommit_block`)."""
    context = MigrationContext.configure(connection)
    with Operations.context(context), context.begin_transaction():
        load_migration(REVISION).upgrade()
    return _labels(connection)


class TestTheMigration:
    async def test_value_is_there_once_after_the_deploy_command(
        self, session: AsyncSession
    ) -> None:
        """Схему тестов поднимает `alembic upgrade head` — та же команда, что у выкатки."""
        labels = await (await session.connection()).run_sync(_labels)

        assert labels.count("paid_off") == 1

    async def test_rerun_is_harmless(self, engine: AsyncEngine) -> None:
        async with engine.connect() as connection:
            labels = await connection.run_sync(_upgrade_on_its_own)

        assert labels.count("paid_off") == 1

    async def test_downgrade_turns_rows_back_into_no_address(self, session: AsyncSession) -> None:
        """Значение у типа остаётся, строки с ним — «адреса нет»: прежний код их прочтёт."""
        donor = await _donor(session, "rollback.example.test", datetime.now(UTC))
        connection = await session.connection()

        await connection.run_sync(_downgrade)

        status = await session.scalar(
            select(DonorModel.contact_status)
            .where(DonorModel.id == donor.id)
            .execution_options(populate_existing=True)
        )
        assert status is ContactStatus.NOT_FOUND
        assert (await connection.run_sync(_labels)).count("paid_off") == 1
