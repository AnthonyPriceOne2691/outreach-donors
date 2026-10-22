"""Смета прогона гипотезы до запуска (срез 6.1): выдача, модель, Hunter строкой и отказ словами.

Настройки — подменой атрибутов модулей конфига: смета не должна зависеть от `.env` машины.
Числа выдуманы и не круглы. Режим выдачи по умолчанию здесь — стандарт (`SERP_MODE`): цены
режимов разные, и смета называет цену страницы своего.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence

import pytest
from backend.config import contacts as contacts_cfg
from backend.config import llm as llm_cfg
from backend.config import sales as sales_cfg
from backend.config import serp as serp_cfg
from backend.config.judge import JudgeMode
from backend.features.core import usage
from backend.features.core.domain import UsageProvider
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.ops import UsageRecordModel
from backend.features.sales import segment_estimate as se
from backend.features.sales import segment_history
from backend.features.sales.intake import UnknownHypothesisError
from backend.features.sales.models import SalesHypothesisModel
from backend.features.sales.segment_estimate import Ask, estimate
from backend.features.sales.segment_keywords import REPEAT
from backend.features.sales.segment_models import (
    Lane,
    SalesSegmentCandidateModel,
    SalesSegmentRunModel,
)
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_segment_judge import MODEL_KEY
from tests.test_sales_segment_run import hypothesis

KEYWORDS = "купить горный велосипед\nвелосипед для города\nзапчасти для велосипеда"


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Продажи включены, выдача собрана (стандарт по умолчанию), ключ модели есть, потолков нет,
    Hunter не настроен, платная ступень сбора выключена, проверка выдуманная; цена токена судьи —
    $0,25 и $2,00 за миллион входа и вывода."""
    monkeypatch.setattr(sales_cfg, "ENABLED", True)
    monkeypatch.setattr(sales_cfg, "SEGMENT_PAID_CONTACTS", False)
    monkeypatch.setattr(sales_cfg, "SEGMENT_INPUT_USD_PER_1M_TOKENS", 0.25)
    monkeypatch.setattr(sales_cfg, "SEGMENT_OUTPUT_USD_PER_1M_TOKENS", 2.0)
    for name, value in {
        "SANDBOX": False,
        "PROVIDER": "dataforseo",
        "LOGIN": "login-test",
        "PASSWORD": "password-test",  # pragma: allowlist secret
        "MODE": "standard",
        "PRICE_PER_KEYWORD_USD": 0.0007,
        "PRICE_PRIORITY_USD": 0.0013,
        "PRICE_LIVE_USD": 0.0021,
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
        26 * se.TOKENS_PER_SITE,
        None,
    )
    assert found.serp_usd == pytest.approx(3 * 0.0007)
    # Деньги токенов: 70% входа по $0,25 и 30% вывода по $2,00 — $0,775 за миллион.
    assert found.tokens_usd == pytest.approx(round(26 * se.TOKENS_PER_SITE * 0.775 / 1_000_000, 4))
    assert (found.country, found.language, found.mode) == ("us", "en", "shadow")
    assert found.lines == [
        "Выдача — стандарт: ключей 3 × страниц 1 — результатов до 30, по $0,0007 за страницу "
        "≈ $0,0021; ждать ≈ 5 мин.",
        "Сайтов ≈ 26 — 85% результатов: прогонов сбора с выдачей ещё нет, доля — худшая из "
        "замеренных у выдачи, смета скорее завышена.",
        "Судья сегмента: сайтов ≈ 26, токенов модели ≈ 88\u00a0400 (по 3\u00a0400 на сайт — "
        "запасное число: прогонов сбора с судьёй ещё нет) — ≈ $0,0685; "
        "доля вывода — запасная, замеров ещё нет: 30% токенов.",
        "Судья в наблюдении: его «отказ» не отсекает сайт — сайт ляжет в «Судья отказал бы»; "
        "решать по нему не обязательно, лид — только после «Принять» человека.",
        "Hunter: платный поиск адреса у сбора выключен или не настроен — только бесплатные "
        "ступени; проверка адреса выдуманная.",
        "Деньгами: ≈ $0,0706 — выдача и модель; Hunter не тратится.",
    ]


async def test_a_paid_address_search_and_check_are_named_as_upper_bounds(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(contacts_cfg, "HUNTER_API_KEY", MODEL_KEY)
    monkeypatch.setattr(sales_cfg, "SEGMENT_PAID_CONTACTS", True)
    monkeypatch.setattr(sales_cfg, "VERIFIER_PROVIDER", "live")
    monkeypatch.setattr(sales_cfg, "VERIFIER_MONTH_CAP", "1500")
    owner = await hypothesis(session)
    usage.record(session, operation="sales_verify", units=120)  # уже в этом месяце
    await session.flush()

    found = await estimate(session, ask(owner.id, depth_pages=2))

    assert (found.hunter_searches, found.hunter_checks) == (51, 51)
    assert found.lines[-2:] == [
        "Hunter: поисков адреса до 51 — только тем, кому бесплатные ступени адреса не нашли; "
        "проверок адреса до 51, в этом месяце 120 из 1500.",
        "Деньгами: ≈ $0,1386 — выдача и модель; Hunter платится запросами своего тарифа и в "
        "сумму не входит.",
    ]
    assert (found.refusal, found.as_dict()["month"]) == (None, "в этом месяце 120 из 1500")


async def test_a_live_check_without_a_month_cap_refuses_the_run(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Живая проверка без месячного потолка: лиды сбора встали бы на очистке — прогон не
    запускается, отказ называет настройку (`verify_cap`)."""
    monkeypatch.setattr(contacts_cfg, "HUNTER_API_KEY", MODEL_KEY)
    monkeypatch.setattr(sales_cfg, "VERIFIER_PROVIDER", "live")
    monkeypatch.setattr(sales_cfg, "VERIFIER_MONTH_CAP", "")
    owner = await hypothesis(session)

    found = await estimate(session, ask(owner.id))

    assert found.refusal == (
        "потолок проверки адресов на месяц не задан — задайте SALES_VERIFIER_MONTH_CAP"
    )


async def test_a_hunter_key_of_the_donors_does_not_turn_on_paid_search_for_collection(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ключ Hunter общий с донорами: без флага сбора платных поисков в смете нет."""
    monkeypatch.setattr(contacts_cfg, "HUNTER_API_KEY", MODEL_KEY)
    owner = await hypothesis(session)

    found = await estimate(session, ask(owner.id, depth_pages=2))

    assert found.hunter_searches == 0
    assert found.lines[-2].startswith("Hunter: платный поиск адреса у сбора выключен")


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
    monkeypatch.setattr(llm_cfg, "SALES_DAILY_TOKEN_CAP", 29113)
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

    assert found.tokens_left == 29113 - 2219
    assert found.refusal == (
        "смета модели выше остатка дневного потолка: ≈ 88\u00a0400 токенов при остатке 26\u00a0894 — "
        "уменьшите число ключей или глубину, или запустите завтра"
    )
    # Слова сметы остаются: видно, сколько просили.
    assert found.lines[2] == (
        "Судья сегмента: сайтов ≈ 26, токенов модели ≈ 88\u00a0400 (по 3\u00a0400 на сайт — "
        "запасное число: прогонов сбора с судьёй ещё нет) — ≈ $0,0685, "
        "остаток дневного потолка — 26\u00a0894; доля вывода — запасная, замеров ещё нет: 30% токенов."
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


async def test_keywords_lose_only_garbage_and_exact_repeats_near_ones_stay_noted(
    session: AsyncSession,
) -> None:
    """Проверка прода 14.10: общий ключ отсеял все свои «… + город». Теперь отсев — гигиена и
    точный повтор, почти повтор взят с пометкой, какой ключ похож (`segment_keywords`)."""
    owner = await hypothesis(session)
    text = (
        "Купить горный велосипед\n\nкупить  горный велосипед\nsite:example.com велосипед\n"
        "горный велосипед купить недорого\nвелосипед для города"
    )

    found = await estimate(session, ask(owner.id, keywords=text))

    assert found.keywords == [
        "купить горный велосипед",
        "горный велосипед купить недорого",
        "велосипед для города",
    ]
    assert found.rejected == {
        "site:example.com велосипед": "поисковый оператор: так пользователи не ищут",
        # Повтор — фразой, как вписана, и строкой взятой: не тем же ключом, что во «взятых».
        "купить  горный велосипед": REPEAT.format(line=1),
    }
    assert found.notes == {
        "горный велосипед купить недорого": "похож на «купить горный велосипед» — общих слов 3 "
        "из 4: выдача может частично совпасть, ключ взят",
    }
    assert (found.as_dict()["rejected"], found.as_dict()["notes"]) == (found.rejected, found.notes)


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

    assert (found.tokens, found.tokens_usd, found.refusal) == (0, 0.0, None)
    assert found.lines[2] == (
        "Судья сегмента выключен: модель не зовётся, сайтов ≈ 26 — решает человек."
    )
    assert found.lines[-1] == "Деньгами: ≈ $0,0021 — выдача; Hunter не тратится."


async def test_unknown_hypothesis_is_refused(session: AsyncSession) -> None:
    with pytest.raises(UnknownHypothesisError, match="гипотезы №987654 нет"):
        await estimate(session, ask(987654))


# --- сайты по истории сбора, уже собранные гипотезой, деньги, режим выдачи ----------------------


async def past_run(
    session: AsyncSession,
    owner_id: int,
    *,
    keywords: Sequence[str] = ("купить горный велосипед",),
    depth: int = 1,
    hosts: int | None = None,
    answered: int | None = None,
    results: int | None = None,
    country: str = "us",
) -> SalesSegmentRunModel:
    """Прошлый прогон сбора; `hosts` — выдача куплена и дала столько сайтов, `answered` —
    сколько ключей провайдер отдал (остальные — пропали), `results` — сколько строк пришло."""
    serp = {
        "keywords": len(keywords) if answered is None else answered,
        "results": results,
        "hosts": hosts,
        "lost_keywords": list(keywords[answered:]) if answered is not None else [],
    }
    run = SalesSegmentRunModel(
        hypothesis_id=owner_id,
        keywords=list(keywords),
        country=country,
        language="en",
        depth_pages=depth,
        mode="shadow",
        estimate={"lines": ["смета"]},
        report={} if hosts is None else {"serp": serp},
    )
    session.add(run)
    await session.flush()
    return run


# Номер сайта сквозной, а не с нуля у вызова: иначе хосты двух ключей одного прогона совпадут.
_SITE_NUMBERS = itertools.count()


async def collected(
    session: AsyncSession, run: SalesSegmentRunModel, keyword: str | None, count: int
) -> None:
    """Сайты, которые прогон уже завёл в гипотезу, — найденные этим ключом."""
    for _ in range(count):
        domain = DomainModel(host=f"site-{run.id}-{next(_SITE_NUMBERS)}.example.test")
        session.add(domain)
        await session.flush()
        session.add(
            SalesSegmentCandidateModel(
                run_id=run.id,
                hypothesis_id=run.hypothesis_id,
                domain_id=domain.id,
                keyword=keyword,
                lane=Lane.ACCEPT,
            )
        )
    await session.flush()


async def test_the_share_of_sites_comes_from_the_history_of_collection_runs(
    session: AsyncSession,
) -> None:
    """Сайтов на запрошенный результат по прошлым прогонам сбора — а не общая 0,85: 17 сайтов
    на 40 результатов и 21 на 40 (третий ключ пропал у провайдера — его результатов не просили
    в счёт) — 38 из 80. Пришло строк меньше запрошенного (33 и 31), но смета умножает долю на
    запрошенные результаты — и делит на них же. Прогон без выдачи историю не меняет."""
    owner = await hypothesis(session)
    keys = ("велосипед для города", "запчасти для велосипеда", "детский велосипед", "самокат")
    await past_run(session, owner.id, keywords=keys, hosts=17, results=33)
    await past_run(session, owner.id, keywords=keys[:3], depth=2, hosts=21, answered=2, results=31)
    await past_run(session, owner.id, keywords=keys[:1])  # поставлен, выдачи ещё нет

    found = await estimate(session, ask(owner.id))

    assert (found.share, found.share_runs) == (pytest.approx(38 / 80), 2)
    assert (found.results, found.hosts, found.known, found.sites) == (30, 14, 0, 14)
    assert found.tokens == 14 * se.TOKENS_PER_SITE
    assert found.lines[1] == (
        "Сайтов ≈ 14 — 48% результатов, как в прошлых прогонах сбора (с выдачей — 2): выдача "
        "отдаёт не все результаты и повторяет сайты."
    )
    assert (found.as_dict()["share"], found.as_dict()["hosts"]) == (pytest.approx(0.475), 14)


async def test_a_handful_of_history_is_not_a_share_the_spare_one_is_named(
    session: AsyncSession,
) -> None:
    owner = await hypothesis(session)
    await past_run(session, owner.id, hosts=9)  # десять результатов — шум, а не доля

    found = await estimate(session, ask(owner.id))

    assert (found.share, found.share_runs, found.hosts) == (0.85, 0, 26)
    assert "прогонов сбора с выдачей ещё нет" in found.lines[1]


async def test_the_share_looks_at_the_latest_runs_only(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(segment_history, "HISTORY_RUNS", 2)
    owner = await hypothesis(session)
    keys = ("велосипед для города", "запчасти для велосипеда")
    await past_run(session, owner.id, keywords=keys, hosts=20)  # старый: всё уникально
    await past_run(session, owner.id, keywords=keys, hosts=7)
    await past_run(session, owner.id, keywords=keys, hosts=9)

    found = await estimate(session, ask(owner.id))

    assert (found.share, found.share_runs) == (pytest.approx(16 / 40), 2)


async def test_sites_the_hypothesis_already_collected_by_the_same_keys_are_subtracted(
    session: AsyncSession,
) -> None:
    """Их второй раз не заводят — не судят и адрес не ищут. Тот же ключ на другом рынке и сайты
    другой гипотезы — не в счёт; сайт, найденный другим ключом, — тоже."""
    owner = await hypothesis(session)
    other = SalesHypothesisModel(name="сайты RU", description="Магазины самокатов.")
    session.add(other)
    await session.flush()
    before = await past_run(session, owner.id)
    await collected(session, before, "купить горный велосипед", 5)
    await collected(session, before, "велосипед для города", 2)
    await collected(session, before, "горный велосипед недорого", 4)
    await collected(
        session, await past_run(session, owner.id, country="gb"), "купить горный велосипед", 6
    )
    await collected(session, await past_run(session, other.id), "велосипед для города", 3)

    found = await estimate(session, ask(owner.id))

    assert (found.hosts, found.known, found.sites) == (26, 7, 19)
    assert (found.tokens, found.as_dict()["known"]) == (19 * se.TOKENS_PER_SITE, 7)
    assert found.lines[1].endswith(
        " Уже в сборе гипотезы ≈ 7 — их не судят и адрес им не ищут: новых ≈ 19."
    )
    assert found.lines[2].startswith("Судья сегмента: сайтов ≈ 19, токенов модели ≈ 64\u00a0600")


async def test_a_deeper_past_run_counts_no_more_than_this_serp_gives_per_key(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Прошлый прогон шёл глубже и собрал по ключу 30 сайтов; топ-10 даст по нему около 8,5 —
    вычесть больше нельзя: новый ключ рядом свои сайты даст. А когда собраны все сайты всех
    ключей, судье и Hunter работы нет."""
    monkeypatch.setattr(contacts_cfg, "HUNTER_API_KEY", MODEL_KEY)
    monkeypatch.setattr(sales_cfg, "SEGMENT_PAID_CONTACTS", True)
    owner = await hypothesis(session)
    await collected(
        session, await past_run(session, owner.id, depth=5), "купить горный велосипед", 30
    )

    both = await estimate(
        session, ask(owner.id, keywords="купить горный велосипед\nвелосипед для города")
    )
    alone = await estimate(session, ask(owner.id, keywords="купить горный велосипед"))

    assert (both.hosts, both.known, both.sites) == (17, 8, 9)
    assert (alone.hosts, alone.known, alone.sites, alone.tokens) == (8, 8, 0, 0)
    assert alone.hunter_searches == 0


async def test_tokens_are_named_in_money_by_the_price_of_the_model(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Цена — из настроек (вход и вывод модели), смешанная долей вывода: другая цена — другие
    деньги, а токены те же."""
    monkeypatch.setattr(sales_cfg, "SEGMENT_INPUT_USD_PER_1M_TOKENS", 0.5)
    monkeypatch.setattr(sales_cfg, "SEGMENT_OUTPUT_USD_PER_1M_TOKENS", 4.0)
    owner = await hypothesis(session)

    found = await estimate(session, ask(owner.id))

    assert found.usd_per_1m_tokens == pytest.approx(0.7 * 0.5 + 0.3 * 4.0)
    assert (found.tokens, found.tokens_usd) == (26 * se.TOKENS_PER_SITE, pytest.approx(0.137))
    assert found.lines[2] == (
        "Судья сегмента: сайтов ≈ 26, токенов модели ≈ 88\u00a0400 (по 3\u00a0400 на сайт — "
        "запасное число: прогонов сбора с судьёй ещё нет) — ≈ $0,1370; "
        "доля вывода — запасная, замеров ещё нет: 30% токенов."
    )
    assert found.lines[-1] == "Деньгами: ≈ $0,1391 — выдача и модель; Hunter не тратится."
    assert (found.as_dict()["tokens_usd"], found.as_dict()["usd_per_1m_tokens"]) == (
        pytest.approx(0.137),
        pytest.approx(1.55),
    )


@pytest.mark.parametrize(
    ("asked", "mode", "line"),
    [
        (
            "live",
            "live",
            "Выдача — быстрая (Live): ключей 3 × страниц 1 — результатов до 30, по $0,0021 за "
            "страницу ≈ $0,0063; ждать ≈ 10 с.",
        ),
        (
            "Standard",
            "standard",
            "Выдача — стандарт: ключей 3 × страниц 1 — результатов до 30, по $0,0007 за страницу "
            "≈ $0,0021; ждать ≈ 5 мин.",
        ),
        (
            "priority",
            "priority",
            "Выдача — приоритетная: ключей 3 × страниц 1 — результатов до 30, по $0,0013 за "
            "страницу ≈ $0,0039; ждать ≈ 1 мин.",
        ),
        (
            None,
            "standard",
            "Выдача — стандарт: ключей 3 × страниц 1 — результатов до 30, по $0,0007 за страницу "
            "≈ $0,0021; ждать ≈ 5 мин.",
        ),
    ],
    ids=["live", "standard", "priority", "service-default"],
)
async def test_the_serp_mode_names_its_page_price_and_wait_and_goes_to_the_run(
    session: AsyncSession, asked: str | None, mode: str, line: str
) -> None:
    owner = await hypothesis(session)

    found = await estimate(session, ask(owner.id, serp_mode=asked))

    assert found.refusal is None
    assert (found.serp_mode, found.lines[0]) == (mode, line)
    saved = found.as_dict()
    assert saved["serp_mode"] == mode
    assert (saved["serp_page_usd"], saved["serp_wait_s"]) == (
        found.serp_page_usd,
        found.serp_wait_s,
    )


async def test_an_unknown_serp_mode_is_refused_in_words_before_any_money(
    session: AsyncSession,
) -> None:
    owner = await hypothesis(session)

    found = await estimate(session, ask(owner.id, serp_mode="turbo"))

    assert found.refusal == "режима выдачи «turbo» нет; известные: standard, priority, live"
    assert (found.lines, found.serp_usd) == ([], 0.0)
