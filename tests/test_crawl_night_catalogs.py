"""Ночной круг без доноров-каталогов (`crawl/night.py`, `crawl/targets.py`).

Решение владельца 11.11.2026: карточки донора-каталога — не рекламодатели, судья им не пишет, и
обходить такого донора каждую ночь незачем. Каталог — признак по судам самого донора
(`judge_store.catalog_donors`), а не список доменов. Откладывает его только ночной круг: кнопка
человека и загрузки берут каталог как раньше.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from backend.config import llm as llm_cfg
from backend.features.core.models.advertiser import AdvertiserJudgementModel
from backend.features.core.models.crawl import CrawlNightModel
from backend.features.crawl import backlog, night, targets
from backend.features.crawl.judge_rules import CATALOG_MIN_PAIRS, RULES_VERSION
from backend.features.crawl.judge_store import cohort_of
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_donor
from tests.test_advertiser_judge import facts_body
from tests.test_crawl_night import (  # noqa: F401 — `alerts` и `operator` — фикстуры круга
    MIDNIGHT,
    NIGHT,
    WHO,
    _feed,
    _priced,
    _rounds,
    _tick,
    _waiting,
    alerts,
    operator,
)
from tests.test_send_race import committed_sessions

CATALOG = "schools-catalog.example.test"
PAPER = "city-paper.example.test"
YOUNG = "young-catalog.example.test"


def _judged(donor: str, pairs: int, *, card: bool = True) -> list[AdvertiserJudgementModel]:
    """Столько рассуженных пар донора в нынешней когорте судьи: карточки каталога — или рекламные
    статьи (`card=False`)."""
    cohort = cohort_of(llm_cfg.ADVERTISER_JUDGE_MODEL)
    facts = facts_body(directory_entry=card, money_anchor=not card, calls_to_buy=not card)
    return [
        AdvertiserJudgementModel(
            donor_host=donor,
            target_root=f"school{number}.example.test",
            context_print="c" * 64,
            prompt_version=cohort.prompt_version,
            prompt_print=cohort.prompt_print,
            examples_print=cohort.examples_print,
            model=cohort.model,
            rules_version=RULES_VERSION,
            facts=facts,
            advice="skip" if card else "write",
            rule="free_directory" if card else "promoted",
            seen="Абзац со ссылкой: …",
            tokens=100,
            retried=False,
        )
        for number in range(pairs)
    ]


async def test_the_round_leaves_a_catalog_donor_out_and_counts_it(session: AsyncSession) -> None:
    """Каталог в круг не идёт и назван в расчёте числом; обычный донор и донор, чьих пар рассужено
    мало, идут как все."""
    for host in (CATALOG, PAPER, YOUNG):
        await _priced(session, host)
    session.add_all(
        [
            *_judged(CATALOG, CATALOG_MIN_PAIRS),
            *_judged(PAPER, CATALOG_MIN_PAIRS, card=False),
            *_judged(YOUNG, CATALOG_MIN_PAIRS - 1),
        ]
    )
    await session.flush()

    found = await night.plan(session, workers=16, per_minute=2, now=MIDNIGHT)

    assert sorted(found.hosts) == [PAPER, YOUNG]
    assert (found.facts["catalogs"], found.facts["donors"], found.facts["rested"]) == (1, 2, 0)


@pytest.mark.parametrize(
    ("in_domains", "in_judgements"),
    [
        (f"www.{CATALOG}", CATALOG),
        ("Schools-Catalog.Example.Test", CATALOG),
        (CATALOG, f"www.{CATALOG}"),
    ],
)
async def test_a_catalog_is_known_however_its_host_is_written(
    session: AsyncSession, in_domains: str, in_judgements: str
) -> None:
    """Суд хранит донора именем обхода (строчными, без «www.»), отбор читает хост из таблицы
    доменов: каталог узнаётся при любой записи, и в круг идёт имя обхода."""
    await _priced(session, in_domains)
    await _priced(session, f"www.{PAPER}")
    session.add_all(_judged(in_judgements, CATALOG_MIN_PAIRS))
    await session.flush()

    chosen = await targets.choose(session, limit=None, skip_catalogs=True)
    found = await night.plan(session, workers=16, per_minute=2, now=MIDNIGHT)

    assert (chosen.hosts, chosen.catalog) == ([f"www.{PAPER}"], [in_domains])
    assert (found.hosts, found.facts["catalogs"]) == ([PAPER], 1)


async def test_a_catalog_without_a_price_keeps_its_first_reason(session: AsyncSession) -> None:
    """Причины отбора — прежние и первыми: каталог без цены — «цены нет», а не «каталог»."""
    await make_donor(session, CATALOG)
    session.add_all(_judged(CATALOG, CATALOG_MIN_PAIRS))
    await session.flush()

    chosen = await targets.choose(session, limit=None, skip_catalogs=True)

    assert (chosen.no_price, chosen.catalog, chosen.hosts) == ([CATALOG], [], [])


async def test_the_button_and_a_load_still_take_a_catalog_donor() -> None:
    """Откладывает каталог только ночной круг: отбор кнопки его берёт, и строку загрузки,
    поставленную человеком, очередь выдаёт обходчику."""
    async with committed_sessions() as factory:
        async with factory() as session:
            await _priced(session, CATALOG)
            session.add_all(_judged(CATALOG, CATALOG_MIN_PAIRS))
            await session.commit()
            pressed = await targets.choose(session, limit=None)

        await _tick(factory)
        [found] = await _rounds(factory)
        left_by_the_round = await _waiting(factory)

        async with factory() as session:
            await backlog.add(session, [CATALOG], by=WHO, author_id=None)
            await session.commit()
        fed = await _feed(factory, MIDNIGHT + timedelta(minutes=1), workers=1)

    assert (pressed.hosts, pressed.catalog) == ([CATALOG], [])
    assert left_by_the_round == []
    assert found.plan is not None
    assert (found.taken, found.plan["catalogs"]) == (0, 1)
    assert list(fed.queued) == [CATALOG]


@pytest.mark.parametrize(("planned", "shown"), [({"workers": 16, "catalogs": 3}, 3), ({}, None)])
async def test_the_tab_gets_how_many_catalogs_the_round_left_out(
    client: AsyncClient,
    session: AsyncSession,
    operator: str,  # noqa: F811 — фикстура круга
    planned: dict[str, int],
    shown: int | None,
) -> None:
    """Число отложенных каталогов едет на вкладку вместе с расчётом (12.11.2026): до этого оно
    лежало в строке круга, а экран его не получал. У круга, посчитанного до 11.11, числа нет —
    пусто, а не ноль."""
    session.add(
        CrawlNightModel(
            night=NIGHT,
            status=night.NightStatus.DONE.value,
            started_at=MIDNIGHT,
            taken=2,
            given=2,
            plan=planned,
        )
    )
    await session.commit()

    answer = await client.get("/api/crawls/night", headers=bearer(operator))

    assert answer.json()["last"]["catalogs"] == shown
