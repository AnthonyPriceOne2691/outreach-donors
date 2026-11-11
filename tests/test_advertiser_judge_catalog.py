"""Донор-каталог у судьи рекламодателей: признак по сохранённым судам донора, пересчёт и новый суд.

Решение владельца 11.11.2026: карточки донора-каталога — не рекламодатели, и спор «таблица — пишем,
модель — не пишем» у такого донора — «не пишем». Каталог — не список доменов, а признак, который код
считает по судам донора: не меньше двадцати рассуженных пар когорты и не меньше 70 % карточек
каталога среди них. Модель здесь — подставной транспорт: пересчёт её не зовёт вовсе.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from backend.features.core.models.advertiser import AdvertiserJudgementModel
from backend.features.crawl import judge_store
from backend.features.crawl.judge import Case, judge_seen
from backend.features.crawl.judge_rules import CATALOG_RULE, RULES_VERSION, Advice
from backend.features.crawl.judge_store import (
    Catalogs,
    Cohort,
    catalog_donors,
    cohort_of,
    relabel,
    relabel_moves,
)
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_advertiser_judge import LINK, SEEN, Model, client_of, facts_answer, facts_body

MODEL = "gpt-5-mini"
CATALOG = "schools-catalog.example.test"
PAPER = "city-paper.example.test"
NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
V5 = "advertiser-rules-v5"

#: Факты строк суда — ответом модели, как их пишет проход. Карточка каталога: и таблица, и модель —
#: «не пишем».
CARD: dict[str, Any] = {
    "money_anchor": False,
    "calls_to_buy": False,
    "directory_entry": True,
    "visit_button": True,
    "verdict": "skip",
}
#: Страница карточки, прочитанная статьёй о домене: таблица — «пишем», модель — «не пишем».
DISPUTE: dict[str, Any] = {
    "money_anchor": False,
    "calls_to_buy": False,
    "article_about_target": True,
    "visit_button": True,
    "verdict": "skip",
}
#: Карточка в подборке «лучшие …»: модель — «пишем».
RANKED: dict[str, Any] = {
    "money_anchor": False,
    "calls_to_buy": False,
    "directory_entry": True,
    "recommends": True,
    "verdict": "write",
}
#: Рекламная статья: таблица и модель согласны — «пишем».
AD: dict[str, Any] = {}


def _row(
    donor: str,
    target: str,
    facts: dict[str, Any],
    *,
    said: tuple[str, str] = ("skip", "free_directory"),
    table: str = RULES_VERSION,
    cohort: Cohort | None = None,
    context: str = "c",
    at: datetime = NOW,
) -> AdvertiserJudgementModel:
    """Строка суда пары: факты, совет с правилом (`said`) и какой таблицей он поставлен."""
    of = cohort or cohort_of(MODEL)
    advice, rule = said
    return AdvertiserJudgementModel(
        donor_host=donor,
        target_root=target,
        context_print=context * 64,
        prompt_version=of.prompt_version,
        prompt_print=of.prompt_print,
        examples_print=of.examples_print,
        model=of.model,
        rules_version=table,
        facts=facts_body(**facts),
        advice=advice,
        rule=rule,
        seen="Абзац со ссылкой: …",
        tokens=100,
        retried=False,
        updated_at=at,
    )


def _pairs(
    donor: str, facts: dict[str, Any], count: int, *, name: str = "card", **row: Any
) -> list[AdvertiserJudgementModel]:
    """Столько пар донора с одними фактами: домены `<name>N.example.test`."""
    said = row.pop("said", ("skip", "free_directory") if facts is CARD else ("write", "promoted"))
    return [
        _row(donor, f"{name}{number}.example.test", facts, said=said, **row)
        for number in range(count)
    ]


async def _stamps(session: AsyncSession, donor: str, target: str) -> tuple[str, str, str]:
    """Совет, правило и версия таблицы строки суда пары — как они лежат в базе."""
    found = await session.execute(
        select(
            AdvertiserJudgementModel.advice,
            AdvertiserJudgementModel.rule,
            AdvertiserJudgementModel.rules_version,
        ).where(
            AdvertiserJudgementModel.donor_host == donor,
            AdvertiserJudgementModel.target_root == target,
        )
    )
    advice, rule, table = found.one()
    return advice, rule, table


# --- признак по судам донора -----------------------------------------------------------------------


async def test_a_catalog_is_a_donor_with_twenty_pairs_and_seven_cards_in_ten(
    session: AsyncSession,
) -> None:
    session.add_all(
        [
            # Ровно двадцать пар и ровно 70 % карточек — каталог.
            *_pairs("twenty.example.test", CARD, 14),
            *_pairs("twenty.example.test", AD, 6, name="ad"),
            # Девятнадцать пар — ещё нет, хотя карточки все.
            *_pairs("nineteen.example.test", CARD, 19),
            # Двадцать пар и 65 % — нет.
            *_pairs("under.example.test", CARD, 13),
            *_pairs("under.example.test", AD, 7, name="ad"),
            # Сто пар и 69 % — нет.
            *_pairs("almost.example.test", CARD, 69),
            *_pairs("almost.example.test", AD, 31, name="ad"),
            # Обычное издание: пар хватает, карточек нет.
            *_pairs(PAPER, AD, 25, name="ad"),
        ]
    )
    await session.flush()

    assert await catalog_donors(session, cohort_of(MODEL)) == {"twenty.example.test"}


async def test_the_sign_counts_each_pair_once_by_its_latest_judgement(
    session: AsyncSession,
) -> None:
    """Страница пары изменилась — у пары вторая строка суда: пара считается один раз и по последней."""
    earlier = NOW - timedelta(days=3)
    session.add_all(
        [
            # Девятнадцать пар, у одной две строки: строк двадцать, пар — девятнадцать.
            *_pairs("relisted.example.test", CARD, 19),
            _row("relisted.example.test", "card0.example.test", CARD, context="a", at=earlier),
            # По последним судам карточек 13 из 20; карточкой была и пара, ставшая рекламой.
            *_pairs("changed.example.test", CARD, 13),
            *_pairs("changed.example.test", AD, 7, name="ad"),
            _row("changed.example.test", "ad0.example.test", CARD, context="a", at=earlier),
            # По последним судам карточек 14 из 20, хотя одна из них раньше читалась рекламой.
            *_pairs("became.example.test", CARD, 14),
            *_pairs("became.example.test", AD, 6, name="ad"),
            _row("became.example.test", "card0.example.test", AD, context="a", at=earlier),
        ]
    )
    await session.flush()

    assert await catalog_donors(session, cohort_of(MODEL)) == {"became.example.test"}


async def test_the_sign_is_counted_inside_one_cohort(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Когорты не смешиваются: суды другой модели или подсказки — другие факты, и пары по ним
    считаются отдельно. Строке суда признак — по её же когорте, запросом на когорту, а не на строку."""
    ours, other = cohort_of(MODEL), cohort_of("gpt-5")
    old_prompt = Cohort("advertiser-judge-v0", "0" * 16, "", MODEL)
    mine = _pairs(CATALOG, CARD, 20)
    theirs = _pairs(CATALOG, CARD, 5, cohort=other)
    # Двадцать пар набирается только вместе с чужой когортой — каталога нет ни в одной.
    split = [
        *_pairs("split.example.test", CARD, 10),
        *_pairs("split.example.test", CARD, 10, name="more", cohort=old_prompt),
    ]
    session.add_all([*mine, *theirs, *split])
    await session.flush()

    assert await catalog_donors(session, ours) == {CATALOG}
    assert await catalog_donors(session, other) == set()
    assert await catalog_donors(session, old_prompt) == set()

    asked: list[Cohort] = []
    real = judge_store.catalog_donors

    async def counted(session_: AsyncSession, cohort: Cohort) -> frozenset[str]:
        asked.append(cohort)
        return await real(session_, cohort)

    monkeypatch.setattr(judge_store, "catalog_donors", counted)
    catalogs = Catalogs(session)
    assert [await catalogs.has(row) for row in mine] == [True] * 20
    assert [await catalogs.has(row) for row in theirs] == [False] * 5
    assert [await catalogs.has(row) for row in split] == [False] * 20
    assert Counter(asked) == {ours: 1, other: 1, old_prompt: 1}


# --- пересчёт ---------------------------------------------------------------------------------------


async def test_the_relabel_moves_rows_of_the_old_table_by_the_donors_sign(
    session: AsyncSession,
) -> None:
    """Суды прежней таблицы — в новую без модели: спор у каталога и подборка уходят из «не понять»
    в «не пишем», согласное «пишем» каталога остаётся, спор обычного издания — человеку."""
    stamped = NOW - timedelta(days=5)
    old: dict[str, Any] = {"table": V5, "at": stamped}
    session.add_all(
        [
            *_pairs(CATALOG, CARD, 17, **old),
            _row(CATALOG, "disputed.example.test", DISPUTE, said=("unclear", "disagree"), **old),
            _row(
                CATALOG, "ranked.example.test", RANKED, said=("unclear", "ranked_directory"), **old
            ),
            _row(CATALOG, "advert.example.test", AD, said=("write", "promoted"), **old),
            _row(PAPER, "disputed.example.test", DISPUTE, said=("unclear", "disagree"), **old),
            _row(PAPER, "ranked.example.test", RANKED, said=("unclear", "ranked_directory"), **old),
            _row(PAPER, "advert.example.test", AD, said=("write", "promoted"), **old),
        ]
    )
    await session.flush()

    moves = await relabel_moves(session)

    assert moves == {
        ("skip", "skip"): 17,
        ("unclear", "skip"): 3,  # спор каталога и обе подборки
        ("unclear", "unclear"): 1,  # спор обычного издания
        ("write", "write"): 2,
    }
    assert await _stamps(session, CATALOG, "disputed.example.test") == ("unclear", "disagree", V5)

    changed = await relabel(session)

    assert changed == 23
    assert await _stamps(session, CATALOG, "disputed.example.test") == (
        "skip",
        CATALOG_RULE,
        RULES_VERSION,
    )
    for donor in (CATALOG, PAPER):
        assert await _stamps(session, donor, "ranked.example.test") == (
            "skip",
            "ranked_directory",
            RULES_VERSION,
        )
        assert await _stamps(session, donor, "advert.example.test") == (
            "write",
            "promoted",
            RULES_VERSION,
        )
    assert await _stamps(session, PAPER, "disputed.example.test") == (
        "unclear",
        "disagree",
        RULES_VERSION,
    )
    # Срок фактов — их, а не пересчёта; второй пересчёт переписывать нечего.
    ages = await session.scalars(select(AdvertiserJudgementModel.updated_at).distinct())
    assert ages.all() == [stamped]
    assert await relabel_moves(session) == {}
    assert await relabel(session) == 0


async def test_the_relabel_follows_the_sign_on_rows_of_this_table(session: AsyncSession) -> None:
    """Признак живой: донор набрал пары и стал каталогом — совет, поставленный до этого нынешней же
    таблицей, пересчёт ставит заново; перестал им быть — спор возвращается человеку."""
    session.add_all(
        [
            *_pairs(CATALOG, CARD, 18),
            _row(CATALOG, "disputed.example.test", DISPUTE, said=("unclear", "disagree")),
            _row(PAPER, "disputed.example.test", DISPUTE, said=("unclear", "disagree")),
        ]
    )
    await session.flush()
    # Девятнадцать пар — признака ещё нет: строки нынешней таблицы на месте.
    assert await relabel_moves(session) == {}
    assert await relabel(session) == 0

    session.add(_row(CATALOG, "twentieth.example.test", CARD))
    await session.flush()

    assert await relabel_moves(session) == {("unclear", "skip"): 1}
    assert await relabel(session) == 1
    assert await _stamps(session, CATALOG, "disputed.example.test") == (
        "skip",
        CATALOG_RULE,
        RULES_VERSION,
    )
    assert await _stamps(session, PAPER, "disputed.example.test") == (
        "unclear",
        "disagree",
        RULES_VERSION,
    )
    assert await relabel(session) == 0

    # Карточек стало меньше 70 %: 19 из 28.
    session.add_all(_pairs(CATALOG, AD, 8, name="ad"))
    await session.flush()

    assert await relabel_moves(session) == {("skip", "unclear"): 1}
    assert await relabel(session) == 1
    assert await _stamps(session, CATALOG, "disputed.example.test") == (
        "unclear",
        "disagree",
        RULES_VERSION,
    )


async def test_the_relabel_of_named_rows_counts_the_sign_over_all_the_donors_rows(
    session: AsyncSession,
) -> None:
    """Пересчёт по номерам строк переписывает только их, а признак донора считает по всем его судам."""
    disputed = _row(
        CATALOG, "disputed.example.test", DISPUTE, said=("unclear", "disagree"), table=V5
    )
    session.add_all([*_pairs(CATALOG, CARD, 19, table=V5), disputed])
    await session.flush()

    changed = await relabel(session, ids=[disputed.id])

    assert changed == 1
    assert await _stamps(session, CATALOG, "disputed.example.test") == (
        "skip",
        CATALOG_RULE,
        RULES_VERSION,
    )
    tables = await session.execute(
        select(AdvertiserJudgementModel.rules_version).where(
            AdvertiserJudgementModel.id != disputed.id
        )
    )
    assert Counter(tables.scalars().all()) == {V5: 19}


# --- новый суд --------------------------------------------------------------------------------------


def _case(donor: str) -> Case:
    return Case(donor, f"https://{donor}/school/1", LINK)


async def test_a_new_judgement_at_a_catalog_donor_gets_the_advice_by_the_sign(
    session: AsyncSession,
) -> None:
    """Признак — по уже сохранённым судам донора: новая карточка каталога сразу «не пишем»."""
    session.add_all(_pairs(CATALOG, CARD, 20))
    await session.flush()
    model = Model(facts_answer(**DISPUTE))

    judged = await judge_seen(session, _case(CATALOG), SEEN, client=client_of(model))

    assert (judged.advice, judged.rule) == (Advice.SKIP, CATALOG_RULE)
    assert judged.words.startswith("донор-каталог, его карточки не рекламодатели")
    assert await _stamps(session, CATALOG, LINK.target) == ("skip", CATALOG_RULE, RULES_VERSION)


@pytest.mark.parametrize("answer", [AD, RANKED])
async def test_a_catalog_donor_keeps_an_agreed_write_and_skips_a_ranked_card(
    session: AsyncSession, answer: dict[str, Any]
) -> None:
    """Рекламная статья бывает и у каталога: согласное «пишем» остаётся. Подборка — «не пишем»."""
    session.add_all(_pairs(CATALOG, CARD, 20))
    await session.flush()

    judged = await judge_seen(
        session, _case(CATALOG), SEEN, client=client_of(Model(facts_answer(**answer)))
    )

    expected = (Advice.WRITE, "promoted") if answer is AD else (Advice.SKIP, "ranked_directory")
    assert (judged.advice, judged.rule) == expected


async def test_a_donor_without_enough_judgements_has_no_sign_yet(session: AsyncSession) -> None:
    """У нового донора и у донора с девятнадцатью парами признака нет: спор — человеку. Сторона
    безопасная: «не пишем» раньше времени теряло бы рекламодателя."""
    session.add_all(_pairs(CATALOG, CARD, 19))
    await session.flush()
    model = Model(facts_answer(**DISPUTE))

    for donor in (CATALOG, "new-donor.example.test"):
        judged = await judge_seen(session, _case(donor), SEEN, client=client_of(model))

        assert (judged.advice, judged.rule) == (Advice.UNCLEAR, "disagree")
        assert await _stamps(session, donor, LINK.target) == ("unclear", "disagree", RULES_VERSION)


async def test_the_sign_given_by_the_pass_is_not_asked_again(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Проход считает доноров-каталоги разом и отдаёт признак случаю: запроса на пару нет."""

    async def never(_session: AsyncSession, _cohort: Cohort) -> frozenset[str]:
        raise AssertionError("признак дан проходом — считать его на пару незачем")

    monkeypatch.setattr("backend.features.crawl.judge.catalog_donors", never)
    model = Model(facts_answer(**DISPUTE))

    judged = await judge_seen(
        session, _case(CATALOG), SEEN, client=client_of(model), catalog_donor=True
    )
    plain = await judge_seen(
        session, _case(PAPER), SEEN, client=client_of(model), catalog_donor=False
    )

    assert (judged.advice, judged.rule) == (Advice.SKIP, CATALOG_RULE)
    assert (plain.advice, plain.rule) == (Advice.UNCLEAR, "disagree")


async def test_cached_facts_follow_the_sign_and_the_row_is_restamped(session: AsyncSession) -> None:
    """Суд пары оплачен до того, как донор стал каталогом: повторный заход модель не зовёт, совет
    ставит по нынешнему признаку и поправляет строку — и обратно, когда признак ушёл."""
    session.add_all(_pairs(CATALOG, CARD, 19))
    await session.flush()
    model = Model(facts_answer(**DISPUTE))
    client = client_of(model)
    first = await judge_seen(session, _case(CATALOG), SEEN, client=client)
    assert (first.advice, first.rule, first.cached) == (Advice.UNCLEAR, "disagree", False)

    # Пара самой карточки — двадцатая у донора: теперь он каталог.
    again = await judge_seen(session, _case(CATALOG), SEEN, client=client)

    assert (again.advice, again.rule, again.cached) == (Advice.SKIP, CATALOG_RULE, True)
    assert await _stamps(session, CATALOG, LINK.target) == ("skip", CATALOG_RULE, RULES_VERSION)

    await session.execute(
        delete(AdvertiserJudgementModel).where(
            AdvertiserJudgementModel.target_root.in_(["card0.example.test", "card1.example.test"])
        )
    )
    back = await judge_seen(session, _case(CATALOG), SEEN, client=client)

    assert (back.advice, back.rule, back.cached) == (Advice.UNCLEAR, "disagree", True)
    assert await _stamps(session, CATALOG, LINK.target) == ("unclear", "disagree", RULES_VERSION)
    assert len(model.requests) == 1
