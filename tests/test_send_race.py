"""Двойная отправка: два одновременных запроса на одно письмо (ревью 28.09.2026).

Проверка «письмо в очереди» и перевод в «отправляется» шли чтением и
записью: два нажатия подряд — двойной щелчок, экран и консоль — оба
читали «в очереди» и оба отдавали письмо почте. Донор получал два
одинаковых письма и справедливо жаловался на спам.

**Гонка здесь настоящая, а не изображённая.** Обычная фикстура `session`
держит всё во внешней транзакции одного соединения: второй запрос в ней
не встретил бы блокировки строки вовсе. Поэтому данные фиксируются
по-настоящему, у каждого запроса — своя сессия и своё соединение, а после
теста таблицы вычищаются. Оба запроса сводятся в одну точку
(`asyncio.Barrier`) после того, как прочли письмо и выбрали ящик, — так
гонка воспроизводится каждый раз, а не когда повезёт с таймингом.
Без захвата оба теста первого раздела падают: почта получает письмо
дважды (проверено порчей).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from backend.api import deps
from backend.api.app import create_app
from backend.features.access.attempts import LoginAttempts
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, MessageStatus, Stage, UserRole
from backend.features.core.models.access import AuditLogModel
from backend.features.core.models.ops import UsageRecordModel
from backend.features.core.models.outreach import MessageModel
from backend.features.letters.building import BuildRequest, QueueBuilder
from backend.features.letters.followups import Chain, Claimed, send_due
from backend.features.letters.rewrite import RewriteResult
from backend.features.letters.sending import NotQueuedError, Sending
from backend.features.letters.transport import Outgoing
from backend.features.outreach import senders as sender_rules
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from tests.conftest import TEST_DSN, bearer, make_donor, make_sender

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
PASSWORD = "пароль-для-гонки"

#: Сколько ждать второго участника гонки. Не дождались — значит, один
#: запрос упал раньше встречи, и тест должен сказать это, а не висеть.
MEETING = 5


class CountingTransport:
    """Почта, которая считает, сколько раз ей отдали письмо."""

    name = "counting"
    real = False

    def __init__(self) -> None:
        self.handed: list[int] = []

    async def send(self, outgoing: Outgoing) -> str:
        self.handed.append(outgoing.message_id)
        return f"provider-{len(self.handed)}"


class TemplateOnly:
    """Модель, которая ничего не переписывает: текст письма здесь не важен."""

    async def rewrite(self, rendered: object, about: object) -> RewriteResult:
        return RewriteResult(notes=["модель в тесте не участвует"])


@pytest.fixture
async def committed() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Сессии на настоящих соединениях с настоящими фиксациями.

    После теста база вычищается целиком: остальные тесты ждут пустые
    таблицы, а зафиксированное здесь внешняя транзакция не откатит.
    """
    engine = create_async_engine(TEST_DSN)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        async with engine.begin() as conn:
            tables = (
                await conn.execute(
                    text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                    )
                )
            ).scalars()
            listed = ", ".join(f'"{table}"' for table in tables)
            await conn.execute(text(f"TRUNCATE {listed} RESTART IDENTITY CASCADE"))
        await engine.dispose()


async def _one_letter(factory: async_sessionmaker[AsyncSession]) -> int:
    """Донор с адресом, ящик и собранное письмо к нему — зафиксированы."""
    async with factory() as session:
        await make_donor(session, "race.example.test", email="editor@race.example.test")
        await make_sender(session, "outreach1@mail.example.test")
        await QueueBuilder(session, TemplateOnly()).build(  # type: ignore[arg-type]
            BuildRequest(campaign_name="Гонка", niche=("home repair",), followup_days=(3, 7))
        )
        await session.commit()
        return int(await session.scalar(select(MessageModel.id)))


def _meet_after_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    """Оба запроса прочли письмо и выбрали ящик — только теперь идут дальше.

    Точка встречи — сразу перед переводом в «отправляется»: здесь старый
    код уже решил, что письмо в очереди, и дальше только записывал.
    """
    barrier = asyncio.Barrier(2)
    pick = Sending._pick_sender

    async def meet(self: Sending, stage: Stage) -> sender_rules.Availability:
        spot = await pick(self, stage)
        await asyncio.wait_for(barrier.wait(), timeout=MEETING)
        return spot

    monkeypatch.setattr(Sending, "_pick_sender", meet)


async def _count(factory: async_sessionmaker[AsyncSession], statement: object) -> int:
    async with factory() as session:
        return int(await session.scalar(statement) or 0)  # type: ignore[call-overload]


class TestTwoRequestsOneLetter:
    async def test_two_parallel_sends_hand_the_letter_to_mail_once(
        self,
        committed: async_sessionmaker[AsyncSession],
        filled_legal: None,
        jwt_secret: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Два запроса `POST /send` на одно письмо — одно письмо почте.

        Путь целиком, как у экрана: вход, два запроса, у каждого своя сессия
        на запрос. Второй получает отказ словами, а не второе письмо.
        """
        letter_id = await _one_letter(committed)
        async with committed() as session:
            user = await AccessRepository(session).create(
                email="гонка@site.com", password=PASSWORD, role=UserRole.ADMIN
            )
            # Новой учётке положено сменить разовый пароль, и до смены
            # её не пускают никуда — так же снимает и фикстура `make_user`.
            user.must_change_password = False
            await session.commit()

        transport = CountingTransport()
        monkeypatch.setattr("backend.api.letters.routes.build_transport", lambda: transport)
        monkeypatch.setattr(deps, "attempts", LoginAttempts(limit=5))
        _meet_after_reading(monkeypatch)

        async def per_request() -> AsyncIterator[AsyncSession]:
            async with committed() as session:
                yield session

        app = create_app()
        app.dependency_overrides[deps.db_session] = per_request
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post(
                "/api/auth/login", json={"email": "гонка@site.com", "password": PASSWORD}
            )
            assert login.status_code == 200, login.text
            headers = bearer(login.json()["token"])

            answers = await asyncio.gather(
                client.post(f"/api/letters/{letter_id}/send", headers=headers),
                client.post(f"/api/letters/{letter_id}/send", headers=headers),
            )

        assert transport.handed == [letter_id]
        assert sorted(answer.status_code for answer in answers) == [200, 409]
        refused = next(answer for answer in answers if answer.status_code == 409)
        assert "уже не в очереди" in refused.json()["detail"]
        # След одной отправки, а не двух: статус, журнал и расход.
        async with committed() as session:
            letter = await session.get(MessageModel, letter_id)
            assert letter is not None
            assert letter.status is MessageStatus.SENT
        assert (
            await _count(
                committed,
                select(func.count())
                .select_from(AuditLogModel)
                .where(AuditLogModel.action == AuditAction.LETTER_SENT),
            )
            == 1
        )
        assert (
            await _count(
                committed,
                select(func.count())
                .select_from(UsageRecordModel)
                .where(UsageRecordModel.operation == "letter_send"),
            )
            == 1
        )

    async def test_losing_side_leaves_the_winner_untouched(
        self,
        committed: async_sessionmaker[AsyncSession],
        filled_legal: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Проигравший не пишет ничего: ни свой ящик, ни свой Message-ID
        поверх тех, с которыми письмо на самом деле ушло."""
        letter_id = await _one_letter(committed)
        transport = CountingTransport()
        _meet_after_reading(monkeypatch)

        async def send_once() -> str | None:
            async with committed() as session:
                await Sending(session, transport, now=NOW).send(letter_id)
                letter = await session.get(MessageModel, letter_id)
                assert letter is not None
                return letter.internet_message_id

        # Отказ проигравшего — значением, а не исключением: какой из двух
        # проиграет, решает база, и проверяется, что ровно один.
        outcomes = await asyncio.gather(send_once(), send_once(), return_exceptions=True)

        assert transport.handed == [letter_id]
        refusals = [outcome for outcome in outcomes if isinstance(outcome, NotQueuedError)]
        winners = [outcome for outcome in outcomes if isinstance(outcome, str)]
        assert len(refusals) == 1, outcomes
        assert len(winners) == 1, outcomes
        async with committed() as session:
            letter = await session.get(MessageModel, letter_id)
            assert letter is not None
            assert letter.internet_message_id == winners[0]


class TestTwoSweepsOneFollowup:
    async def test_parallel_passes_send_one_followup(
        self,
        committed: async_sessionmaker[AsyncSession],
        filled_legal: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Два прохода добивок одновременно — одна добивка.

        Другой путь отправки: цепочку забирает захват строки со сроком
        (`Chain.claim`, `FOR UPDATE SKIP LOCKED`), а строку добивки шлёт тот
        же `Sending.send`. Проходы встречаются сразу после захвата, пока
        первый ещё держит строку незафиксированной: исправный захват второго
        её пропускает и приходит на встречу ни с чем. Без блокировки второй
        упёрся бы в строку первого и до встречи не дошёл — тест падает
        по времени встречи (проверено порчей).
        """
        letter_id = await _one_letter(committed)
        transport = CountingTransport()
        async with committed() as session:
            await Sending(session, transport, now=NOW).send(letter_id)
        assert transport.handed == [letter_id]

        barrier = asyncio.Barrier(2)
        claim = Chain.claim
        met = 0

        async def claim_then_meet(self: Chain) -> Claimed | None:
            nonlocal met
            claimed = await claim(self)
            met += 1
            if met <= 2:
                await asyncio.wait_for(barrier.wait(), timeout=MEETING)
            return claimed

        monkeypatch.setattr(Chain, "claim", claim_then_meet)
        due = NOW + timedelta(days=4)

        async def one_pass() -> int:
            async with committed() as session:
                report = await send_due(session, transport=transport, limit=5, now=due)
                return report.sent

        sent = await asyncio.gather(one_pass(), one_pass())

        assert sorted(sent) == [0, 1]
        followups = [handed for handed in transport.handed if handed != letter_id]
        assert len(followups) == 1
        assert (
            await _count(
                committed,
                select(func.count()).select_from(MessageModel).where(MessageModel.step == 1),
            )
            == 1
        )
