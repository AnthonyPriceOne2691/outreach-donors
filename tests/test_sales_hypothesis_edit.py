"""Правка гипотезы — `PATCH /api/sales/hypotheses/{N}`: имя и описание сегмента (волна 15).

Правило одно — `features/sales/hypotheses.edit`: имя — как у заведения (пробелы не в счёт, пустое
и длинное — 422 словами, занятое гипотезой или рассылкой продаж — 409 словами), описание — как у
правки на «Сборе базы»; каждое изменённое поле — своей записью журнала той же транзакцией. Право —
`sales`, как у заведения. **Рассылка гипотезы ищется по имени** («Продажи: <имя>»), поэтому правка
имени переименовывает и её: следующая сборка пишет в ту же рассылку, а не заводит вторую.

Строка гипотезы — под `FOR NO KEY UPDATE`: загрузку лидов правка не держит, а предел ожидания —
отказ словами, а не зависший запрос. Это проверяется на настоящих фиксациях (`committed_sessions`):
у фикстуры `session` второго соединения нет, и блокировки в ней не бывает. Имена разных
письменностей; домены и адреса выдуманы (`*.example.test`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from backend.features.access.permissions import no_right
from backend.features.core.domain import AuditAction, Permission, Stage
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.outreach import CampaignModel, MessageModel
from backend.features.sales import hypotheses, queue
from backend.features.sales.models import NAME_LENGTH, SalesHypothesisModel
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests import test_sales_send_world as w
from tests.conftest import bearer
from tests.test_send_race import MEETING, committed_sessions

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

SELLER = "seller@ours.example.test"
HYPOTHESES = "/api/sales/hypotheses"
NO_RIGHT = no_right(Permission.SALES)


@pytest.fixture
async def seller(make_user: MakeUser) -> UserModel:
    return await make_user(SELLER)


@pytest.fixture
async def headers(seller: UserModel, sign_in: SignIn) -> dict[str, str]:
    return bearer(await sign_in(SELLER))


async def _made(client: AsyncClient, headers: dict[str, str], name: str, **body: Any) -> int:
    response = await client.post(HYPOTHESES, json={"name": name, **body}, headers=headers)
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


async def _journal(session: AsyncSession, hypothesis_id: int) -> list[dict[str, Any]]:
    """Подробности записей о гипотезе после заведения — по порядку."""
    found = await session.scalars(
        select(AuditLogModel)
        .where(
            AuditLogModel.action == AuditAction.USER_UPDATED,
            AuditLogModel.target == f"sales_hypothesis:{hypothesis_id}",
        )
        .order_by(AuditLogModel.id)
    )
    return [dict(entry.details or {}) for entry in found.all()][1:]


async def test_a_new_name_is_written_journaled_and_comes_back(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str], seller: UserModel
) -> None:
    made = await _made(client, headers, "сайты EN", description="Блоги о путешествиях.")

    response = await client.patch(
        f"{HYPOTHESES}/{made}", json={"name": "  блоги   EN "}, headers=headers
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "hypothesis_id": made,
        "name": "блоги EN",
        "description": "Блоги о путешествиях.",
    }
    assert await _journal(session, made) == [
        {
            "этап": Stage.SALES.value,
            "действие": hypotheses.RENAMED,
            "было": "сайты EN",
            "стало": "блоги EN",
        }
    ]
    author = await session.scalar(
        select(AuditLogModel.user_id).where(AuditLogModel.target == f"sales_hypothesis:{made}")
    )
    assert author == seller.id


async def test_the_same_name_after_spaces_is_not_an_edit(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    made = await _made(client, headers, "сайты EN")

    response = await client.patch(
        f"{HYPOTHESES}/{made}", json={"name": " сайты  EN"}, headers=headers
    )

    assert (response.status_code, response.json()["name"]) == (200, "сайты EN")
    assert await _journal(session, made) == []


async def test_a_name_and_a_description_at_once_are_two_records_in_one_transaction(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    """Имя — первым: запись описания называет гипотезу уже новым именем."""
    made = await _made(client, headers, "Ελληνικά ξενοδοχεία")

    response = await client.patch(
        f"{HYPOTHESES}/{made}",
        json={"name": "Hôtels de montagne", "description": " Отели в горах: бронь у себя. "},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert [entry["действие"] for entry in await _journal(session, made)] == [
        hypotheses.RENAMED,
        f"{hypotheses.SEGMENT} задано",
    ]
    assert (await _journal(session, made))[1] == {
        "этап": Stage.SALES.value,
        "действие": f"{hypotheses.SEGMENT} задано",
        "гипотеза": "Hôtels de montagne",
        "было": None,
        "стало": "Отели в горах: бронь у себя.",
    }


@pytest.mark.parametrize(
    "names",
    [
        ("物流 日本", "物流 日本・関西"),
        ("مدارس اللغات", "مدارس اللغات في القاهرة"),
        ("Clínicas dentales MX", "Clínicas dentales CDMX"),
        ("Школы английского", "Школы английского для взрослых"),
    ],
)
async def test_names_of_any_script_are_renamed(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str], names: tuple[str, str]
) -> None:
    made = await _made(client, headers, names[0])

    response = await client.patch(f"{HYPOTHESES}/{made}", json={"name": names[1]}, headers=headers)

    assert response.json()["name"] == names[1]
    assert (
        await session.scalar(
            select(SalesHypothesisModel.name).where(SalesHypothesisModel.id == made)
        )
        == names[1]
    )


async def test_a_name_of_another_hypothesis_is_409_in_words_and_nothing_changes(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    taken = await _made(client, headers, "сайты EN")
    made = await _made(client, headers, "сайты DE")

    response = await client.patch(
        f"{HYPOTHESES}/{made}",
        json={"name": "сайты  EN", "description": "Не должно записаться."},
        headers=headers,
    )

    assert (response.status_code, response.json()["detail"]) == (
        409,
        f"имя «сайты EN» уже у гипотезы №{taken}",
    )
    session.expire_all()
    found = await session.get(SalesHypothesisModel, made)
    assert found is not None
    assert (found.name, found.description) == ("сайты DE", None)
    assert await _journal(session, made) == []


@pytest.mark.parametrize(
    ("name", "words"),
    [
        ("   ", "нет имени (передано '   ') — по нему гипотезу выбирают при загрузке базы"),
        ("я" * (NAME_LENGTH + 1), f"имя длиннее {NAME_LENGTH} знаков (129) — нужно короткое"),
    ],
)
async def test_a_bad_name_is_422_in_words(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str], name: str, words: str
) -> None:
    made = await _made(client, headers, "сайты EN")

    response = await client.patch(f"{HYPOTHESES}/{made}", json={"name": name}, headers=headers)

    assert (response.status_code, response.json()["detail"]) == (422, words)
    assert await _journal(session, made) == []


async def test_the_longest_allowed_name_is_taken(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    made = await _made(client, headers, "сайты EN")
    longest = "я" * NAME_LENGTH

    response = await client.patch(f"{HYPOTHESES}/{made}", json={"name": longest}, headers=headers)

    assert (response.status_code, response.json()["name"]) == (200, longest)


async def test_an_edit_without_fields_is_422_in_words(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    made = await _made(client, headers, "сайты EN")

    empty = await client.patch(f"{HYPOTHESES}/{made}", json={}, headers=headers)
    nulls = await client.patch(
        f"{HYPOTHESES}/{made}", json={"name": None, "description": None}, headers=headers
    )
    typo = await client.patch(f"{HYPOTHESES}/{made}", json={"title": "x"}, headers=headers)

    words = "нечего править: дайте имя или описание сегмента"
    assert (empty.status_code, empty.json()["detail"]) == (422, words)
    assert (nulls.status_code, nulls.json()["detail"]) == (422, words)
    # Опечатка в имени поля — отказ схемы, а не молчаливое «ничего не поменялось».
    assert typo.status_code == 422
    assert typo.json()["detail"][0]["loc"] == ["body", "title"]


async def test_the_description_has_one_limit_on_creation_and_on_edit(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    longest, longer = "д" * 4000, "д" * 4001
    made = await _made(client, headers, "сайты EN", description=longest)

    created = await client.post(
        HYPOTHESES, json={"name": "сайты DE", "description": longer}, headers=headers
    )
    edited = await client.patch(
        f"{HYPOTHESES}/{made}", json={"description": longer}, headers=headers
    )

    assert (created.status_code, edited.status_code) == (422, 422)


async def test_an_unknown_hypothesis_is_404_in_words(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    response = await client.patch(
        f"{HYPOTHESES}/987654", json={"name": "сайты EN"}, headers=headers
    )

    assert (response.status_code, response.json()["detail"]) == (
        404,
        "гипотезы №987654 нет — обновите список гипотез",
    )


async def test_without_the_sales_right_an_edit_is_refused_and_nothing_changes(
    session: AsyncSession, client: AsyncClient, make_user: MakeUser, sign_in: SignIn
) -> None:
    owner = (await hypotheses.add(session, "сайты EN", None)).id
    await session.commit()
    await make_user("outsider@team.example.test", permissions={"sales": False})
    headers = bearer(await sign_in("outsider@team.example.test"))

    response = await client.patch(
        f"{HYPOTHESES}/{owner}", json={"name": "чужое имя"}, headers=headers
    )

    assert (response.status_code, response.json()["detail"]) == (403, NO_RIGHT)
    session.expire_all()
    found = await session.get(SalesHypothesisModel, owner)
    assert found is not None
    assert found.name == "сайты EN"
    assert await _journal(session, owner) == []


# --- рассылка гипотезы ищется по имени ---------------------------------------------------------


async def _campaigns(session: AsyncSession) -> list[str]:
    found = await session.scalars(
        select(CampaignModel.name)
        .where(CampaignModel.stage == Stage.SALES)
        .order_by(CampaignModel.id)
    )
    return list(found.all())


async def test_the_campaign_is_renamed_with_the_hypothesis_and_the_next_build_keeps_it(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Без переименования рассылки следующая сборка завела бы вторую — «Продажи: <новое>»."""
    world = await w.world(session, monkeypatch)
    await w.lead(session, world.hypothesis_id, "jane@acme.example.test")
    await queue.build(session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=5)
    await session.commit()

    response = await client.patch(
        f"{HYPOTHESES}/{world.hypothesis_id}", json={"name": "Гипотеза EU"}, headers=headers
    )
    await w.lead(session, world.hypothesis_id, "john@beta.example.test")
    again = await queue.build(
        session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=5
    )

    assert response.status_code == 200, response.text
    assert await _campaigns(session) == ["Продажи: Гипотеза EU"]
    letters = await session.scalars(select(MessageModel.campaign_id))
    assert set(letters.all()) == {again.campaign_id}
    assert (await _journal(session, world.hypothesis_id))[-1]["рассылка"] == (
        "«Продажи: Выдуманная гипотеза» → «Продажи: Гипотеза EU»"
    )


async def test_a_sales_campaign_with_the_new_name_refuses_the_rename_in_words(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    """Остаток прежней гипотезы с тем же именем или чужая рассылка: имя рассылки в базе не
    уникально, и сборка взяла бы первую попавшуюся — письма двух гипотез в одной."""
    made = await _made(client, headers, "сайты EN")
    left = CampaignModel(stage=Stage.SALES, name="Продажи: сайты DE")
    donors = CampaignModel(stage=Stage.DONORS, name="Продажи: сайты FR")
    session.add_all([left, donors])
    await session.commit()

    refused = await client.patch(f"{HYPOTHESES}/{made}", json={"name": "сайты DE"}, headers=headers)
    # Рассылка другого этапа сборке продаж не встретится: имя свободно.
    taken = await client.patch(f"{HYPOTHESES}/{made}", json={"name": "сайты FR"}, headers=headers)

    assert (refused.status_code, refused.json()["detail"]) == (
        409,
        f"рассылка «Продажи: сайты DE» уже есть (№{left.id}, писем — 0) — письма двух гипотез "
        "легли бы в одну рассылку: выберите другое имя",
    )
    assert (taken.status_code, taken.json()["name"]) == (200, "сайты FR")


async def test_a_new_hypothesis_named_as_a_sales_campaign_is_refused_by_the_same_rule(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    left = CampaignModel(stage=Stage.SALES, name="Продажи: сайты DE")
    session.add(left)
    await session.commit()

    response = await client.post(HYPOTHESES, json={"name": "сайты DE"}, headers=headers)

    assert response.status_code == 409
    assert response.json()["detail"].startswith("рассылка «Продажи: сайты DE» уже есть")
    count = await session.scalar(select(func.count()).select_from(SalesHypothesisModel))
    assert count == 0


# --- блокировка строки: на настоящих фиксациях --------------------------------------------------


async def _committed_hypothesis(factory: async_sessionmaker[AsyncSession], name: str) -> int:
    async with factory() as session:
        made = await hypotheses.add(session, name, None)
        await w.domain(session, "acme.example.test")
        await session.commit()
        return made.id


async def _waits_on_a_lock(factory: async_sessionmaker[AsyncSession], pid: int) -> bool:
    """Ждёт ли соединение `pid` блокировку — по `pg_stat_activity`, до `MEETING` секунд."""
    async with factory() as watcher:
        for _ in range(MEETING * 20):
            state = await watcher.scalar(
                text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
                {"pid": pid},
            )
            if state == "Lock":
                return True
            await asyncio.sleep(0.05)
    return False


async def test_a_description_edit_does_not_wait_for_a_lead_being_loaded() -> None:
    """Загрузка лидов держит строку гипотезы ключом (`FOR KEY SHARE`), пока не зафиксирована;
    правка описания под `FOR NO KEY UPDATE` с ней совместима и проходит сразу."""
    async with committed_sessions() as factory:
        made = await _committed_hypothesis(factory, "сайты EN")
        async with factory() as loading:
            await w.lead(loading, made, "jane@acme.example.test")  # не зафиксирован
            async with factory() as editing:
                found = await asyncio.wait_for(
                    hypotheses.edit(editing, made, description="Блоги.", author_id=None),
                    MEETING,
                )
                await editing.commit()
            await loading.commit()

        assert found.description == "Блоги."


async def test_a_rename_waits_for_a_lead_being_loaded_no_longer_than_the_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Имя — под уникальным индексом: правка имени в Postgres — правка ключа, и она ждёт
    незафиксированную загрузку лидов. Ждёт не дольше предела и отказывает словами — не 500
    посреди записи (замер стенда 15.10: без блокировки нужной силы ожидание шло на записи)."""
    monkeypatch.setattr(hypotheses, "LOCK_WAIT", "SET LOCAL lock_timeout = '200ms'")
    async with committed_sessions() as factory:
        made = await _committed_hypothesis(factory, "сайты EN")
        async with factory() as loading:
            await w.lead(loading, made, "jane@acme.example.test")  # не зафиксирован
            async with factory() as editing:
                with pytest.raises(hypotheses.BusyError) as refused:
                    await asyncio.wait_for(
                        hypotheses.edit(editing, made, name="сайты EU", author_id=None), MEETING
                    )
            await loading.commit()
        async with factory() as editing:
            # Загрузка зафиксирована — переименование проходит.
            again = await hypotheses.edit(editing, made, name="сайты EU", author_id=None)
            await editing.commit()

        assert (str(refused.value), again.name) == (hypotheses.BUSY, "сайты EU")


async def test_a_row_held_longer_than_the_limit_is_refused_in_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Удаление держит строку `FOR UPDATE` дольше предела: правка не висит, а отказывает словами."""
    monkeypatch.setattr(hypotheses, "LOCK_WAIT", "SET LOCAL lock_timeout = '200ms'")
    async with committed_sessions() as factory:
        made = await _committed_hypothesis(factory, "сайты EN")
        async with factory() as deleting:
            await deleting.execute(
                text("SELECT id FROM sales_hypotheses WHERE id = :id FOR UPDATE"), {"id": made}
            )
            async with factory() as editing:
                with pytest.raises(hypotheses.BusyError) as refused:
                    await asyncio.wait_for(
                        hypotheses.edit(editing, made, name="сайты EU", author_id=None), MEETING
                    )
                # Транзакция правки жива: отказ — в точке сохранения.
                assert await editing.scalar(text("SELECT 1")) == 1
            await deleting.rollback()

        assert str(refused.value) == hypotheses.BUSY


async def test_two_renames_to_one_name_at_once_end_in_one_refusal_in_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Обе правки прошли проверку имени раньше, чем первая записала его: уникальность базы —
    последний рубеж, и её отказ — словами, а не 500."""
    async with committed_sessions() as factory:
        first = await _committed_hypothesis(factory, "сайты DE")
        async with factory() as session:
            second = (await hypotheses.add(session, "сайты FR", None)).id
            await session.commit()
        barrier = asyncio.Barrier(2)
        free = hypotheses._free

        async def meet(*args: Any, **kwargs: Any) -> None:
            await free(*args, **kwargs)
            await asyncio.wait_for(barrier.wait(), timeout=MEETING)

        monkeypatch.setattr(hypotheses, "_free", meet)

        async def rename(hypothesis_id: int) -> str:
            async with factory() as session:
                found = await hypotheses.edit(
                    session, hypothesis_id, name="сайты EN", author_id=None
                )
                await session.commit()
                return found.name

        results = await asyncio.gather(rename(first), rename(second), return_exceptions=True)

        refused = [item for item in results if isinstance(item, hypotheses.NameTakenError)]
        assert sorted(map(str, results)) == sorted(
            ["сайты EN", "имя «сайты EN» только что заняла другая гипотеза"]
        )
        assert len(refused) == 1
