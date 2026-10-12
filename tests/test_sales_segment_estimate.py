"""Смета прогона гипотезы до запуска (срез 6.1): выдача, модель, Hunter строкой и отказ словами.

Настройки — подменой атрибутов модулей конфига: смета не должна зависеть от `.env` машины.
Числа выдуманы и не круглы.
"""

from __future__ import annotations

import pytest
from backend.config import contacts as contacts_cfg
from backend.config import llm as llm_cfg
from backend.config import sales as sales_cfg
from backend.config import serp as serp_cfg
from backend.config.judge import JudgeMode
from backend.features.core.domain import UsageProvider
from backend.features.core.models.ops import UsageRecordModel
from backend.features.sales import segment_estimate as se
from backend.features.sales.intake import UnknownHypothesisError
from backend.features.sales.segment_estimate import Ask, estimate
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_segment_judge import MODEL_KEY
from tests.test_sales_segment_run import hypothesis

KEYWORDS = "купить горный велосипед\nвелосипед для города\nзапчасти для велосипеда"


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Продажи включены, выдача собрана, ключ модели есть, потолков нет, Hunter не настроен,
    платная ступень сбора выключена, проверка выдуманная."""
    monkeypatch.setattr(sales_cfg, "ENABLED", True)
    monkeypatch.setattr(sales_cfg, "SEGMENT_PAID_CONTACTS", False)
    for name, value in {
        "SANDBOX": False,
        "PROVIDER": "dataforseo",
        "LOGIN": "login-test",
        "PASSWORD": "password-test",  # pragma: allowlist secret
        "PRICE_PER_KEYWORD_USD": 0.0007,
        "MAX_KEYWORDS_PER_RUN": 100,
        "MAX_DEPTH_PAGES": 10,
    }.items():
        monkeypatch.setattr(serp_cfg, name, value)
    monkeypatch.setattr(llm_cfg, "API_KEY", MODEL_KEY)
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", 0)
    monkeypatch.setattr(llm_cfg, "SALES_DAILY_TOKEN_CAP", None)
    monkeypatch.setattr(contacts_cfg, "HUNTER_API_KEY", "")
    monkeypatch.setattr(sales_cfg, "VERIFIER_PROVIDER", "fixture")
    monkeypatch.setattr(sales_cfg, "SEGMENT_JUDGE_MODE", JudgeMode.SHADOW)


def ask(hypothesis_id: int, **changes: object) -> Ask:
    values: dict[str, object] = {
        "hypothesis_id": hypothesis_id,
        "keywords": KEYWORDS,
        "country": "us",
        "language": None,
        "depth_pages": 1,
    }
    values.update(changes)
    return Ask(**values)  # type: ignore[arg-type]


async def test_estimate_names_serp_model_and_hunter_in_words(session: AsyncSession) -> None:
    owner = await hypothesis(session)

    found = await estimate(session, ask(owner.id))

    assert found.refusal is None
    assert (found.results, found.sites, found.tokens, found.tokens_left) == (
        30,
        26,
        26 * 1500,
        None,
    )
    assert found.serp_usd == pytest.approx(3 * 0.0007)
    assert (found.country, found.language, found.mode) == ("us", "en", "shadow")
    assert found.lines == [
        "Выдача: ключей 3 × страниц 1 — результатов до 30, ≈ $0,0021.",
        "Судья сегмента: сайтов до 26, токенов модели ≈ 39 000.",
        "Судья в наблюдении: его «отказ» не отсекает сайт, а отдаёт человеку.",
        "Hunter: платный поиск адреса у сбора выключен или не настроен — только бесплатные "
        "ступени; проверка адреса выдуманная.",
    ]


async def test_a_paid_address_search_and_check_are_named_as_upper_bounds(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(contacts_cfg, "HUNTER_API_KEY", MODEL_KEY)
    monkeypatch.setattr(sales_cfg, "SEGMENT_PAID_CONTACTS", True)
    monkeypatch.setattr(sales_cfg, "VERIFIER_PROVIDER", "live")
    owner = await hypothesis(session)

    found = await estimate(session, ask(owner.id, depth_pages=2))

    assert (found.hunter_searches, found.hunter_checks) == (51, 51)
    assert found.lines[-1] == (
        "Hunter: поисков адреса до 51 — только тем, кому бесплатные ступени адреса не нашли; "
        "проверок адреса до 51."
    )


async def test_a_hunter_key_of_the_donors_does_not_turn_on_paid_search_for_collection(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ключ Hunter общий с донорами: без флага сбора платных поисков в смете нет."""
    monkeypatch.setattr(contacts_cfg, "HUNTER_API_KEY", MODEL_KEY)
    owner = await hypothesis(session)

    found = await estimate(session, ask(owner.id, depth_pages=2))

    assert found.hunter_searches == 0
    assert found.lines[-1].startswith("Hunter: платный поиск адреса у сбора выключен")


async def test_switched_off_sales_refuse_the_collection(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Модуль выключен — сбор не тратит ни выдачу, ни модель, ни поиск адресов."""
    monkeypatch.setattr(sales_cfg, "ENABLED", False)
    owner = await hypothesis(session)

    found = await estimate(session, ask(owner.id))

    assert found.refusal == se.SALES_OFF


async def test_estimate_above_the_model_cap_refuses_in_words(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:  # A4
    """Свой потолок продаж — как у остальных вызовов модели продаж, за вычетом потраченного."""
    monkeypatch.setattr(llm_cfg, "SALES_DAILY_TOKEN_CAP", 41113)
    session.add(
        UsageRecordModel(
            system="outreach-donors",
            provider=UsageProvider.LLM,
            operation="sales_reply_kind",
            units=2219,
        )
    )
    await session.flush()
    owner = await hypothesis(session)

    found = await estimate(session, ask(owner.id))

    assert found.tokens_left == 41113 - 2219
    assert found.refusal == (
        "смета модели выше остатка дневного потолка: ≈ 39 000 токенов при остатке 38 894 — "
        "уменьшите число ключей или глубину, или запустите завтра"
    )
    # Слова сметы остаются: видно, сколько просили.
    assert found.lines[1] == (
        "Судья сегмента: сайтов до 26, токенов модели ≈ 39 000, остаток дневного потолка — 38 894."
    )


async def test_the_general_daily_cap_counts_too(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", 9001)
    owner = await hypothesis(session)

    found = await estimate(session, ask(owner.id))

    # Свой потолок продаж не задан — доля общего (`SALES_CAP_SHARE`): меньший из двух.
    assert found.tokens_left == int(9001 * llm_cfg.SALES_CAP_SHARE)
    assert found.refusal is not None
    assert found.refusal.startswith("смета модели выше остатка дневного потолка")


async def test_keywords_go_through_the_hygiene_and_dedup_of_the_donor_pool(
    session: AsyncSession,
) -> None:
    owner = await hypothesis(session)
    text = (
        "Купить горный велосипед\n\nкупить  горный велосипед\nsite:example.com велосипед\n"
        "горный велосипед купить недорого\nвелосипед для города"
    )

    found = await estimate(session, ask(owner.id, keywords=text))

    assert found.keywords == ["купить горный велосипед", "велосипед для города"]
    assert found.rejected == {
        "site:example.com велосипед": "поисковый оператор: так пользователи не ищут",
        "горный велосипед купить недорого": "почти повтор другого ключа: выдача у них общая",
    }


@pytest.mark.parametrize(
    ("changes", "refusal"),
    [
        ({"keywords": " \n "}, se.NO_KEYWORDS),
        ({"country": "zz"}, "рынка «zz» нет в карте выдачи — выберите страну из списка"),
        ({"country": "de"}, "язык писем «de» — цепочка пишется на ru, en"),
        ({"depth_pages": 11}, "глубина выдачи — от 1 до 10 страниц по 10 результатов"),
    ],
    ids=["no-keywords", "unknown-market", "language-without-chain", "too-deep"],
)
async def test_a_request_the_run_cannot_take_is_refused_before_any_money(
    session: AsyncSession, changes: dict[str, object], refusal: str
) -> None:
    owner = await hypothesis(session)

    found = await estimate(session, ask(owner.id, **changes))

    assert found.refusal == refusal
    assert found.lines == []


async def test_a_market_of_another_language_writes_in_the_language_asked(
    session: AsyncSession,
) -> None:
    owner = await hypothesis(session)

    found = await estimate(session, ask(owner.id, country="ua", language="RU"))

    assert (found.language, found.refusal) == ("ru", None)


async def test_too_many_keywords_are_refused(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(serp_cfg, "MAX_KEYWORDS_PER_RUN", 2)
    owner = await hypothesis(session)

    found = await estimate(session, ask(owner.id))

    assert found.refusal == "ключей 3 — за прогон не больше 2: разделите набор"


@pytest.mark.parametrize(
    ("mode", "refusal"), [(JudgeMode.SHADOW, se.NO_SEGMENT), (JudgeMode.OFF, None)]
)
async def test_a_hypothesis_without_a_segment_cannot_be_judged(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, mode: JudgeMode, refusal: str | None
) -> None:
    monkeypatch.setattr(sales_cfg, "SEGMENT_JUDGE_MODE", mode)
    owner = await hypothesis(session, description="  ")

    found = await estimate(session, ask(owner.id))

    assert found.refusal == refusal


@pytest.mark.parametrize(
    ("setting", "value", "refusal"),
    [
        (serp_cfg, ("SANDBOX", True), se.SANDBOX),
        (serp_cfg, ("LOGIN", ""), se.SERP_NOT_SET),
        (llm_cfg, ("API_KEY", ""), se.MODEL_NOT_SET),
        (serp_cfg, ("PROVIDER", "ahrefs"), se.SERP_AHREFS),
    ],
    ids=["sandbox", "serp-not-set", "no-model-key", "ahrefs-units-untracked"],
)
async def test_settings_that_would_waste_the_run_refuse_it_in_words(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    setting: object,
    value: tuple[str, object],
    refusal: str,
) -> None:
    monkeypatch.setattr(setting, *value)
    owner = await hypothesis(session)

    found = await estimate(session, ask(owner.id))

    assert found.refusal == refusal


async def test_a_live_check_without_a_key_refuses_the_run(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sales_cfg, "VERIFIER_PROVIDER", "live")
    owner = await hypothesis(session)

    found = await estimate(session, ask(owner.id))

    assert found.refusal == se.VERIFIER_NOT_SET


async def test_the_switched_off_judge_costs_no_tokens(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sales_cfg, "SEGMENT_JUDGE_MODE", JudgeMode.OFF)
    monkeypatch.setattr(llm_cfg, "API_KEY", "")
    owner = await hypothesis(session)

    found = await estimate(session, ask(owner.id))

    assert (found.tokens, found.refusal) == (0, None)
    assert (
        found.lines[1]
        == "Судья сегмента выключен: модель не зовётся, сайтов до 26 — решает человек."
    )


async def test_unknown_hypothesis_is_refused(session: AsyncSession) -> None:
    with pytest.raises(UnknownHypothesisError, match="гипотезы №987654 нет"):
        await estimate(session, ask(987654))
