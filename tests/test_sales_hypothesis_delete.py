"""Удаление гипотезы — `DELETE /api/sales/hypotheses/{N}` и `GET …/{N}/holds` (волна 15).

Удаляется только пустая — «на случай, если завели по ошибке»: держат её лиды, диалоги и письма,
прогоны сбора с сайтами и вердиктами судьи, свой набор цепочки (`hypothesis_holds.HOLDERS`).
Непустая — 409 словами с числом каждого вида и подсказкой переименовать; ничего не удалено и не
записано. Пустая — 204, запись журнала с автором и её пустая рассылка «Продажи: <имя>» вместе с
ней. Право и журнал — как у заведения (`sales`, «правка» со словами «действия»).

**Каждый вид держащего — своим тестом**, а не одним «живым миром»: мир, где держат все разом,
не заметил бы, что какой-то вид выпал из счёта. **Реестр сверяется с метаданными**: ключ на
гипотезу без строки в реестре — красный тест здесь, а не удаление, упавшее у человека.

**Гонки — на настоящих фиксациях** (`committed_sessions`, у каждого участника своё соединение):
у фикстуры `session` второго соединения нет, и блокировки строки в ней не бывает. Кто кого ждёт —
по `pg_stat_activity`, а не по таймингу. Порча старым кодом (без `FOR UPDATE`, без предела
ожидания) роняет эти тесты. Домены и адреса выдуманы (`*.example.test`).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import backend.features.core.models  # noqa: F401 — реестр моделей целиком, для метаданных
import pytest
from backend.features.access.permissions import no_right
from backend.features.core.domain import AuditAction, Permission, Stage
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.outreach import CampaignModel, ThreadModel
from backend.features.sales import chain, chain_text, hypotheses, hypothesis_holds, queue
from backend.features.sales.models import (
    SalesHypothesisModel,
    SalesLeadModel,
    SalesThreadModel,
)
from backend.features.sales.segment_judge import PROMPT_VERSION, segment_digest
from backend.features.sales.segment_models import (
    Decider,
    Lane,
    SalesSegmentCandidateModel,
    SalesSegmentVerdictModel,
)
from backend.shared.database.base import Base
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests import test_sales_send_world as w
from tests.conftest import bearer
from tests.test_sales_hypothesis_edit import _committed_hypothesis, _waits_on_a_lock
from tests.test_sales_segment_run import make_run
from tests.test_send_race import MEETING, committed_sessions

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

SELLER = "seller@ours.example.test"
HYPOTHESES = "/api/sales/hypotheses"
NO_RIGHT = no_right(Permission.SALES)
SEGMENT = "Языковые школы: курсы для взрослых, запись на сайте."


@pytest.fixture
async def seller(make_user: MakeUser) -> UserModel:
    return await make_user(SELLER)


@pytest.fixture
async def headers(seller: UserModel, sign_in: SignIn) -> dict[str, str]:
    return bearer(await sign_in(SELLER))


async def _hypothesis(session: AsyncSession, name: str = "школы EN") -> SalesHypothesisModel:
    made = await hypotheses.add(session, name, SEGMENT)
    await session.commit()
    return made


async def _removed(session: AsyncSession, hypothesis_id: int) -> list[dict[str, Any]]:
    found = await session.scalars(
        select(AuditLogModel.details).where(
            AuditLogModel.action == AuditAction.USER_UPDATED,
            AuditLogModel.target == f"sales_hypothesis:{hypothesis_id}",
            AuditLogModel.details["действие"].astext == hypotheses.REMOVED,
        )
    )
    return [dict(details or {}) for details in found.all()]


async def _exists(session: AsyncSession, hypothesis_id: int) -> bool:
    found = await session.scalar(
        select(func.count()).where(SalesHypothesisModel.id == hypothesis_id)
    )
    return bool(found)


# --- пустая уходит -------------------------------------------------------------------------------


async def test_an_empty_hypothesis_is_deleted_204_and_journaled_with_its_author(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str], seller: UserModel
) -> None:
    owner = await _hypothesis(session)

    response = await client.delete(f"{HYPOTHESES}/{owner.id}", headers=headers)

    assert (response.status_code, response.content) == (204, b"")
    assert not await _exists(session, owner.id)
    assert await _removed(session, owner.id) == [
        {
            "этап": Stage.SALES.value,
            "действие": hypotheses.REMOVED,
            "имя": "школы EN",
            "описание сегмента": SEGMENT,
        }
    ]
    author = await session.scalar(
        select(AuditLogModel.user_id).where(
            AuditLogModel.details["действие"].astext == hypotheses.REMOVED
        )
    )
    assert author == seller.id


async def test_the_empty_campaign_goes_with_it_and_other_campaigns_stay(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    """Пустую рассылку заводит сборка, нажатая без лидов: рассылка — до поиска лидов. Рассылка
    другого этапа и рассылка с перепиской — не её и не пустые: остаются."""
    owner = await _hypothesis(session)
    domain = await w.domain(session, "acme.example.test")
    empty = CampaignModel(stage=Stage.SALES, name="Продажи: школы EN")
    donors = CampaignModel(stage=Stage.DONORS, name="Продажи: школы EN")
    busy = CampaignModel(stage=Stage.SALES, name="Продажи: школы EN")
    session.add_all([empty, donors, busy])
    await session.flush()
    session.add(ThreadModel(domain_id=domain.id, campaign_id=busy.id, contact_id=None))
    await session.commit()
    kept = {donors.id, busy.id}

    response = await client.delete(f"{HYPOTHESES}/{owner.id}", headers=headers)

    assert response.status_code == 204, response.text
    left = await session.scalars(select(CampaignModel.id))
    assert set(left.all()) == kept
    [journaled] = await _removed(session, owner.id)
    assert journaled["рассылка"] == "«Продажи: школы EN» — пустая, удалена"


# --- непустая держится: каждый вид своим тестом ----------------------------------------------------


async def _leads(session: AsyncSession, owner: SalesHypothesisModel) -> str:
    await w.lead(session, owner.id, "jane@acme.example.test")
    await w.lead(session, owner.id, "john@acme.example.test")
    return "лиды — 2"


async def _dialog_of_its_chain(session: AsyncSession, owner: SalesHypothesisModel) -> str:
    """Диалог, начатый набором цепочки гипотезы, когда лид — в другой: держит своим ключом
    (`sales_threads.chain_hypothesis_id`), а не через лида."""
    await w.settings(session)
    await w.chain_of(session)
    await w.mailbox(session)
    other = await hypotheses.add(session, "школы DE", None)
    lead = await w.lead(session, other.id, "jane@acme.example.test")
    await queue.build(session, w.CorridorRewriter(), hypothesis_id=other.id, limit=5)
    link = await session.scalar(select(SalesThreadModel).where(SalesThreadModel.lead_id == lead.id))
    assert link is not None
    link.chain_hypothesis_id = owner.id
    return "диалоги — 1, письма — 1"


async def _runs(session: AsyncSession, owner: SalesHypothesisModel) -> str:
    await make_run(session, owner)
    return "прогоны сбора — 1"


async def _sites(session: AsyncSession, owner: SalesHypothesisModel) -> str:
    run = await make_run(session, owner)
    domain = await w.domain(session, "lingua-sample.example.test")
    session.add(
        SalesSegmentCandidateModel(
            run_id=run.id, hypothesis_id=owner.id, domain_id=domain.id, lane=Lane.REVIEW
        )
    )
    return "прогоны сбора — 1, сайты сбора — 1"


async def _verdicts(session: AsyncSession, owner: SalesHypothesisModel) -> str:
    """Вердикт без прогона: кэш судьи держит гипотезу и сам — ключом своей колонки."""
    domain = await w.domain(session, "lingua-verdict.example.test")
    session.add(
        SalesSegmentVerdictModel(
            domain_id=domain.id,
            hypothesis_id=owner.id,
            prompt_version=PROMPT_VERSION,
            segment_digest=segment_digest(SEGMENT),
            verdict=Lane.ACCEPT,
            quote="курсы для взрослых",
            reason="языковая школа",
            model="model-sample",
            decided_by=Decider.MODEL,
        )
    )
    return "вердикты судьи сегмента — 1"


async def _own_chain(session: AsyncSession, owner: SalesHypothesisModel) -> str:
    """Свой набор цепочки держит гипотезу и выключенным: шаблон не удаляется (решение 15.10)."""
    await w.settings(session)
    step = chain_text.step_template(step=2, language="en", body=w.FOLLOW_BODY[2], active=False)
    await chain.save(session, step, hypothesis_id=owner.id, author="тест", author_id=None)
    return "шаблоны своей цепочки — 1"


Make = Callable[[AsyncSession, SalesHypothesisModel], Awaitable[str]]


@pytest.mark.parametrize(
    "make",
    [_leads, _dialog_of_its_chain, _runs, _sites, _verdicts, _own_chain],
    ids=["лиды", "диалог её цепочки", "прогон", "сайт", "вердикт", "свой набор"],
)
async def test_each_kind_holds_the_hypothesis_and_is_named_with_its_number(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    make: Make,
) -> None:
    w.connect(monkeypatch)
    owner = await _hypothesis(session)
    held = await make(session, owner)
    await session.commit()

    read = await client.get(f"{HYPOTHESES}/{owner.id}/holds", headers=headers)
    response = await client.delete(f"{HYPOTHESES}/{owner.id}", headers=headers)

    assert (response.status_code, response.json()["detail"]) == (
        409,
        f"Гипотеза «школы EN» не удалена — её держат: {held}. {hypotheses.ONLY_EMPTY}",
    )
    assert read.json()["held"] == f"Удалить нельзя — её держат: {held}. {hypotheses.ONLY_EMPTY}"
    assert await _exists(session, owner.id)
    assert await _removed(session, owner.id) == []


async def test_a_live_hypothesis_names_every_kind_in_one_order(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = await w.world(session, monkeypatch)
    owner = await session.get(SalesHypothesisModel, world.hypothesis_id)
    assert owner is not None
    await w.lead(session, owner.id, "jane@acme.example.test")
    await queue.build(session, w.CorridorRewriter(), hypothesis_id=owner.id, limit=5)
    await _sites(session, owner)
    await _verdicts(session, owner)
    await _own_chain(session, owner)
    await session.commit()

    read = await client.get(f"{HYPOTHESES}/{owner.id}/holds", headers=headers)

    assert read.json()["holds"] == {
        "leads": 1,
        "dialogs": 1,
        "letters": 1,
        "runs": 1,
        "sites": 1,
        "verdicts": 1,
        "chain": 1,
    }
    assert hypothesis_holds.words(read.json()["holds"]) == (
        "лиды — 1, диалоги — 1, письма — 1, прогоны сбора — 1, сайты сбора — 1, "
        "вердикты судьи сегмента — 1, шаблоны своей цепочки — 1"
    )


def test_numbers_in_the_words_are_grouped() -> None:
    assert (
        hypothesis_holds.words({"leads": 12_345, "letters": 7}) == "лиды — 12\u00a0345, письма — 7"
    )
    assert hypothesis_holds.words({}) is None


def test_the_registry_names_every_key_to_a_hypothesis() -> None:
    """Таблица, которая заведёт ключ на гипотезу, обязана встать в реестр держащих: иначе
    счёт назовёт гипотезу пустой, а удаление упадёт на ключе базы. Метаданные — целиком: реестр
    моделей (`core/models`) импортирует все модели, и продаж тоже."""
    keys = {
        (fk.parent.table.name, fk.parent.name)
        for table in Base.metadata.tables.values()
        for fk in table.foreign_keys
        if fk.column.table.name == SalesHypothesisModel.__tablename__
    }
    named = {
        (holder.column.property.columns[0].table.name, holder.column.key)
        for holder in hypothesis_holds.HOLDERS
    }
    assert keys == named


async def test_the_window_reads_an_empty_hypothesis_as_removable(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    owner = await _hypothesis(session)

    read = await client.get(f"{HYPOTHESES}/{owner.id}/holds", headers=headers)

    assert read.json() == {"hypothesis_id": owner.id, "holds": {}, "held": None}


# --- отказы словами -----------------------------------------------------------------------------


async def test_an_unknown_hypothesis_is_404_in_words(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    deleted = await client.delete(f"{HYPOTHESES}/987654", headers=headers)
    read = await client.get(f"{HYPOTHESES}/987654/holds", headers=headers)

    words = "гипотезы №987654 нет — обновите список гипотез"
    assert (deleted.status_code, deleted.json()["detail"]) == (404, words)
    assert (read.status_code, read.json()["detail"]) == (404, words)


async def test_without_the_sales_right_nothing_is_read_or_deleted(
    session: AsyncSession, client: AsyncClient, make_user: MakeUser, sign_in: SignIn
) -> None:
    owner = (await _hypothesis(session)).id
    await make_user("outsider@team.example.test", permissions={"sales": False})
    headers = bearer(await sign_in("outsider@team.example.test"))

    deleted = await client.delete(f"{HYPOTHESES}/{owner}", headers=headers)
    read = await client.get(f"{HYPOTHESES}/{owner}/holds", headers=headers)

    assert (deleted.status_code, deleted.json()["detail"]) == (403, NO_RIGHT)
    assert (read.status_code, read.json()["detail"]) == (403, NO_RIGHT)
    assert await _exists(session, owner)
    assert await _removed(session, owner) == []


async def test_a_busy_row_is_409_in_words(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await _hypothesis(session)

    async def busy(*_: Any, **__: Any) -> hypotheses.Removed:
        raise hypotheses.BusyError(hypotheses.BUSY)

    monkeypatch.setattr(hypotheses, "remove", busy)

    response = await client.delete(f"{HYPOTHESES}/{owner.id}", headers=headers)

    assert (response.status_code, response.json()["detail"]) == (409, hypotheses.BUSY)


async def test_a_holder_the_registry_does_not_know_is_refused_in_words_and_loudly(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Счёт не видит держащего (так было бы с ключом вне реестра): удаление упирается в ключ
    базы, отказ — словами, а в лог — ошибкой. Транзакция вызывающего жива: удаление — в точке
    сохранения."""
    owner = await _hypothesis(session)
    await w.lead(session, owner.id, "jane@acme.example.test")
    await session.commit()

    async def blind(*_: Any) -> dict[str, int]:
        return {}

    monkeypatch.setattr(hypothesis_holds, "count", blind)

    with caplog.at_level(logging.ERROR), pytest.raises(hypotheses.HeldError) as refused:
        await hypotheses.remove(session, owner.id, author_id=None)

    assert str(refused.value) == (
        "Гипотеза «школы EN» не удалена — её держит запись, которой проверка не знает; "
        "сообщите разработчику: реестр держащих (hypothesis_holds) её не называет"
    )
    assert "гипотезу держит ссылка вне реестра держащих" in caplog.text
    assert await _exists(session, owner.id)


async def test_a_holder_seen_only_by_the_key_is_named_by_a_second_count(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Счёт до удаления ничего не нашёл, ключ базы — нашёл: слова — по второму счёту."""
    owner = await _hypothesis(session)
    await w.lead(session, owner.id, "jane@acme.example.test")
    await session.commit()
    real = hypothesis_holds.count
    calls: list[int] = []

    async def late(session: AsyncSession, hypothesis_id: int) -> dict[str, int]:
        calls.append(hypothesis_id)
        return {} if len(calls) == 1 else await real(session, hypothesis_id)

    monkeypatch.setattr(hypothesis_holds, "count", late)

    with pytest.raises(hypotheses.HeldError) as refused:
        await hypotheses.remove(session, owner.id, author_id=None)

    assert "её держат: лиды — 1." in str(refused.value)


# --- гонки: на настоящих фиксациях ----------------------------------------------------------------


async def _lead_count(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as session:
        return int(await session.scalar(select(func.count()).select_from(SalesLeadModel)) or 0)


async def test_a_lead_saved_before_the_delete_holds_the_hypothesis() -> None:
    """Лид заводится и ещё не зафиксирован, когда удаление берёт строку: удаление ждёт его,
    видит и отказывает словами; лид цел."""
    async with committed_sessions() as factory:
        made = await _committed_hypothesis(factory, "школы EN")
        inserted, deleter = asyncio.Event(), asyncio.get_running_loop().create_future()

        async def load() -> None:
            async with factory() as loading:
                await w.lead(loading, made, "jane@acme.example.test")
                inserted.set()
                pid = await asyncio.wait_for(deleter, MEETING)
                assert await _waits_on_a_lock(factory, pid)
                await loading.commit()

        async def delete() -> hypotheses.Removed:
            await asyncio.wait_for(inserted.wait(), MEETING)
            async with factory() as deleting:
                deleter.set_result(await deleting.scalar(text("SELECT pg_backend_pid()")))
                return await hypotheses.remove(deleting, made, author_id=None)

        loaded, removed = await asyncio.gather(load(), delete(), return_exceptions=True)

        assert loaded is None
        assert isinstance(removed, hypotheses.HeldError)
        assert "её держат: лиды — 1." in str(removed)
        assert await _lead_count(factory) == 1


async def test_a_lead_saved_after_the_lock_waits_and_the_empty_hypothesis_goes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Удаление взяло строку и посчитало ноль; лид, которого заводят в эту секунду, ждёт
    блокировку и после удаления получает отказ ключа. Висячего лида нет. Без `FOR UPDATE`
    лид прошёл бы сразу, а удаление упало бы на ключе — тест краснеет."""
    async with committed_sessions() as factory:
        made = await _committed_hypothesis(factory, "школы EN")
        counted, loader = asyncio.Event(), asyncio.get_running_loop().create_future()
        real = hypothesis_holds.count

        async def counted_then_meet(session: AsyncSession, hypothesis_id: int) -> dict[str, int]:
            found = await real(session, hypothesis_id)
            if not counted.is_set():
                counted.set()
                await _waits_on_a_lock(factory, await asyncio.wait_for(loader, MEETING))
            return found

        monkeypatch.setattr(hypothesis_holds, "count", counted_then_meet)

        async def load() -> None:
            async with factory() as loading:
                loader.set_result(await loading.scalar(text("SELECT pg_backend_pid()")))
                await asyncio.wait_for(counted.wait(), MEETING)
                await w.lead(loading, made, "jane@acme.example.test")
                await loading.commit()

        async def delete() -> hypotheses.Removed:
            async with factory() as deleting:
                removed = await hypotheses.remove(deleting, made, author_id=None)
                await deleting.commit()
                return removed

        loaded, removed = await asyncio.gather(load(), delete(), return_exceptions=True)

        assert isinstance(removed, hypotheses.Removed), removed
        assert isinstance(loaded, IntegrityError), loaded
        async with factory() as session:
            assert not await _exists(session, made)
        assert await _lead_count(factory) == 0


async def test_a_lead_held_longer_than_the_limit_is_refused_in_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Загрузка держит строку дольше предела: удаление не висит, а отказывает словами."""
    monkeypatch.setattr(hypotheses, "LOCK_WAIT", "SET LOCAL lock_timeout = '200ms'")
    async with committed_sessions() as factory:
        made = await _committed_hypothesis(factory, "школы EN")
        async with factory() as loading:
            await w.lead(loading, made, "jane@acme.example.test")  # не зафиксирован
            async with factory() as deleting:
                with pytest.raises(hypotheses.BusyError) as refused:
                    # Без предела удаление ждало бы загрузку вечно, а она — его: тест не висит.
                    await asyncio.wait_for(
                        hypotheses.remove(deleting, made, author_id=None), MEETING
                    )
            await loading.rollback()
        async with factory() as session:
            assert await _exists(session, made)

        assert str(refused.value) == hypotheses.BUSY
