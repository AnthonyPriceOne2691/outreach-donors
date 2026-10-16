"""Ключи, снятые человеком в смете сбора, и запуск только по смете, которую он видел (волна 15).

Снятый ключ (`excluded` — номер строки, как вписана) не покупается: его нет ни в выдаче, ни в
числах сметы, ни в смете прогона; в ответе он отдельно от отсеянных, а «повтор строки N» от
снятия не сдвигается. Запуск сверяет отпечаток сметы: ключи, снятые, описание сегмента и режимы
— иначе «смета устарела»; числа сметы (остаток потолка модели) отпечаток не меняют.

Ключи — разных ниш и языков; настройки сметы — подменой атрибутов конфига (`_settings`), очередь
задач — подделка с правилами rq, выдача — подделка, которая записывает купленные ключи.
"""

from __future__ import annotations

import logging
import random
from typing import Any

import pytest
from backend.api.sales import segment as segment_api
from backend.api.sales.segment_schemas import RunSpend
from backend.config import llm as llm_cfg
from backend.config import sales as sales_cfg
from backend.config import serp as serp_cfg
from backend.config.judge import JudgeMode
from backend.features.core.domain import AuditAction, UsageProvider
from backend.features.core.models.access import AuditLogModel
from backend.features.core.models.ops import UsageRecordModel
from backend.features.keywords import hygiene
from backend.features.sales import segment_estimate_keys
from backend.features.sales.segment_estimate import ALL_EXCLUDED, TOKENS_PER_SITE, Ask, estimate
from backend.features.sales.segment_estimate_keys import chosen, keyword_lines, run_excluded
from backend.features.sales.segment_keywords import REPEAT, clean_keywords
from backend.features.sales.segment_models import SalesSegmentRunModel
from backend.features.serp.modes import SerpMode, serp_cost_usd
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_clean_api import _Jobs
from tests.test_sales_segment_api import SEGMENT_URL, headers, jobs, seen
from tests.test_sales_segment_estimate import _settings
from tests.test_sales_segment_run import Model, Serp, go, hypothesis

__all__ = ["_settings", "headers", "jobs"]  # фикстуры — отсюда их видит pytest

RUNS = "/api/sales/segment/runs"
#: Шесть строк: первое написание, его повтор, пустая строка, оператор поиска и два ключа.
TEXT = "\n".join(
    [
        "reparación de bicicletas",
        "Reparación de bicicletas",
        "",
        "site:bicis.example.test talleres",
        "taller de bicicletas madrid",
        "bicicletas eléctricas",
    ]
)


def ask(hypothesis_id: int, text: str = TEXT, **changes: Any) -> Ask:
    values: dict[str, Any] = {
        "hypothesis_id": hypothesis_id,
        "keywords": text,
        "country": "es",
        "language": "en",
        "depth_pages": 1,
    }
    values.update(changes)
    return Ask(**values)


def asked(hypothesis_id: int, **changes: Any) -> dict[str, Any]:
    """Тело сметы и запуска: немецкий набор ключей с повтором и оператором поиска."""
    keywords = "zahnarzt berlin\nzahnarzt notdienst berlin\nZahnarzt Berlin\nzahnarzt kosten"
    return {
        "hypothesis_id": hypothesis_id,
        "keywords": keywords,
        "country": "de",
        "language": "en",  # письма — на языке цепочки, выдача — немецкая
        **changes,
    }


# --- смета ---------------------------------------------------------------------------------------


async def test_an_excluded_keyword_is_not_bought_and_the_numbers_are_recounted(
    session: AsyncSession,
) -> None:
    owner = await hypothesis(session)

    cut = await estimate(session, ask(owner.id, excluded=(5,)))
    # Та же смета без строки 5 в тексте: сайты, токены и деньги — те же, что у снятого.
    alone = await estimate(
        session, ask(owner.id, text=TEXT.replace("taller de bicicletas madrid", ""))
    )

    assert cut.refusal is None
    assert cut.keywords == ["reparación de bicicletas", "bicicletas eléctricas"]
    assert cut.excluded == {"taller de bicicletas madrid": 5}
    assert cut.results == 2 * 10
    assert cut.serp_usd == pytest.approx(serp_cost_usd(2, 1, SerpMode(cut.serp_mode)))
    assert cut.tokens == cut.sites * TOKENS_PER_SITE
    numbers = ("results", "hosts", "sites", "serp_usd", "tokens", "tokens_usd")
    assert [getattr(cut, name) for name in numbers] == [getattr(alone, name) for name in numbers]
    assert cut.lines[0].startswith("Выдача — стандарт: ключей 2 × страниц 1")
    assert cut.as_dict()["keywords"] == 2


async def test_excluding_the_first_spelling_keeps_the_repeat_numbering(
    session: AsyncSession,
) -> None:
    """Снят ключ строки 1 — его повтор в строке 2 остаётся «повтором строки 1» и не занимает
    его место: снятие идёт после отсева, а не вычёркиванием строки из текста."""
    owner = await hypothesis(session)

    cut = await estimate(session, ask(owner.id, excluded=(1,)))

    assert cut.rejected["Reparación de bicicletas"] == REPEAT.format(line=1)
    assert cut.keywords == ["taller de bicicletas madrid", "bicicletas eléctricas"]
    assert cut.excluded == {"reparación de bicicletas": 1}


async def test_the_excluded_stand_apart_from_the_rejected_with_their_lines(
    session: AsyncSession,
) -> None:
    owner = await hypothesis(session)

    cut = await estimate(session, ask(owner.id, excluded=(6,)))

    assert set(cut.excluded) == {"bicicletas eléctricas"}
    assert "bicicletas eléctricas" not in cut.rejected
    assert set(cut.rejected) == {"Reparación de bicicletas", "site:bicis.example.test talleres"}
    # Номер строки, как вписана, — у каждого взятого: и покупаемого, и снятого.
    assert cut.keyword_lines == {
        "reparación de bicicletas": 1,
        "taller de bicicletas madrid": 5,
        "bicicletas eléctricas": 6,
    }


@pytest.mark.parametrize(
    "text",
    [
        TEXT,
        "доставка цветов\n\nДоставка цветов\nцветы на заказ\nдоставка  цветов\nцветы оптом",
        "straße reparatur\nstrasse reparatur\n  \nSTRASSE REPARATUR\nfahrrad werkstatt",
        "家具 注文\n家具　注文\n\n家具 注文 東京",
    ],
)
def test_line_numbers_agree_with_the_repeat_numbering_of_the_cleaning(text: str) -> None:
    """Номер строки взятого ключа — тот же, что называет «повтор строки N»: одна нумерация у
    снятия и у отсева, на любом языке и с пустыми строками."""
    words = clean_keywords(text)
    lines = keyword_lines(text, words.taken)

    assert any(why.startswith("повтор строки") for why in words.rejected.values()), words
    assert set(lines) == set(words.taken)
    for why in words.rejected.values():
        if why.startswith("повтор строки"):
            number = int(why.removeprefix("повтор строки ").split(":", 1)[0])
            assert number in lines.values(), (why, lines)


#: Слова наборов инварианта: разные языки и письменности, повтор в другом регистре, оператор
#: поиска и прошлый год (их отсеивает гигиена), пустые строки.
WORDS = (
    "доставка",
    "Доставка",
    "цветов",
    "fahrrad",
    "Werkstatt",
    "reparación",
    "bicis",
    "家具",
    "注文",
    "site:x.example.test",
    "2019",
    "straße",
    "STRASSE",
)


def test_exclusion_removes_exactly_the_excluded_and_keeps_the_cleaning() -> None:
    """Инвариант на трёхстах наборах (случайных, с зерном): снятие убирает из покупаемых ровно
    снятые ключи, порядок остальных — прежний, отсев и пометки — те же; номер строки взятого —
    строка, чья фраза после нормализации и есть этот ключ."""
    rng = random.Random(1510)
    for _ in range(300):
        rows = [
            " ".join(rng.sample(WORDS, rng.randint(1, 3))) if rng.random() > 0.15 else ""
            for _ in range(rng.randint(1, 12))
        ]
        text = "\n".join(rows)
        whole = clean_keywords(text)
        lines = keyword_lines(text, whole.taken)
        off = set(rng.sample(sorted(lines.values()), rng.randint(0, len(lines))))

        picked = chosen(text, whole.taken, off)

        assert picked.bought == [phrase for phrase in whole.taken if lines[phrase] not in off]
        assert picked.excluded == {phrase: n for phrase, n in lines.items() if n in off}
        assert picked.lines == lines
        assert clean_keywords(text) == whole  # отсев и пометки от снятия не зависят
        for phrase, number in lines.items():
            assert hygiene.normalize(text.splitlines()[number - 1]) == phrase


async def test_numbers_of_other_lines_exclude_nothing_and_are_logged(
    session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """Номер повтора, оператора, пустой строки или за концом набора ничего не снимает — и не
    тихо: экран шлёт только номера взятых ключей."""
    owner = await hypothesis(session)

    with caplog.at_level(logging.WARNING, logger=segment_estimate_keys.__name__):
        cut = await estimate(session, ask(owner.id, excluded=(2, 3, 4, 99)))

    assert cut.excluded == {}
    assert len(cut.keywords) == 3
    [record] = [r for r in caplog.records if "снятые номера строк" in r.getMessage()]
    assert record.__dict__["lines"] == [2, 3, 4, 99]


async def test_all_taken_excluded_refuses_in_words_and_keeps_the_keys(
    session: AsyncSession,
) -> None:
    owner = await hypothesis(session)

    cut = await estimate(session, ask(owner.id, excluded=(1, 5, 6)))

    assert cut.refusal == ALL_EXCLUDED
    assert cut.keywords == []
    # Галочки вернуть можно: снятые и их строки в ответе есть.
    assert cut.excluded == {
        "reparación de bicicletas": 1,
        "taller de bicicletas madrid": 5,
        "bicicletas eléctricas": 6,
    }


async def test_the_run_estimate_keeps_the_excluded_and_the_report_reads_them_back(
    session: AsyncSession,
) -> None:
    owner = await hypothesis(session)
    cut = await estimate(session, ask(owner.id, excluded=(6, 1)))
    run = SalesSegmentRunModel(
        hypothesis_id=owner.id,
        keywords=cut.keywords,
        country="es",
        language="en",
        depth_pages=1,
        mode="shadow",
        estimate=cut.as_dict(),
        report={},
    )

    assert cut.as_dict()["excluded"] == {"reparación de bicicletas": 1, "bicicletas eléctricas": 6}
    assert run_excluded(run) == ["reparación de bicicletas", "bicicletas eléctricas"]
    assert RunSpend.of(run).keywords.excluded == run_excluded(run)
    # Прогон старше снятия (или чужое поле) — пусто, а не падение отчёта.
    run.estimate = {"excluded": "не словарь"}
    assert run_excluded(run) == []


async def test_the_digest_follows_what_the_person_saw_not_the_numbers(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(llm_cfg, "SALES_DAILY_TOKEN_CAP", 9_000_000)
    owner = await hypothesis(session)
    first = await estimate(session, ask(owner.id, excluded=(5,)))
    session.add(
        UsageRecordModel(
            system="outreach-donors",
            provider=UsageProvider.LLM,
            operation="sales_reply_kind",
            units=2219,
        )
    )
    await session.flush()

    again = await estimate(session, ask(owner.id, excluded=(5,)))

    assert first.tokens_left is not None
    assert again.tokens_left == first.tokens_left - 2219
    assert again.digest == first.digest
    others = [
        ask(owner.id),
        ask(owner.id, excluded=(6,)),
        ask(owner.id, excluded=(5,), serp_mode="live"),
        ask(owner.id, excluded=(5,), country="mx"),
        ask(owner.id, excluded=(5,), language="ru"),
        ask(owner.id, excluded=(5,), depth_pages=2),
        ask(owner.id, text=TEXT + "\nbicicletas plegables", excluded=(5,)),
    ]
    digests = {(await estimate(session, other)).digest for other in others}
    assert first.digest not in digests
    assert len(digests) == len(others)
    # Режим судьи — настройкой сервиса: «режет» вместо «наблюдает» — другая смета.
    monkeypatch.setattr(sales_cfg, "SEGMENT_JUDGE_MODE", JudgeMode.ENFORCE)
    assert (await estimate(session, ask(owner.id, excluded=(5,)))).digest != first.digest
    monkeypatch.setattr(sales_cfg, "SEGMENT_JUDGE_MODE", JudgeMode.SHADOW)
    assert (await estimate(session, ask(owner.id, excluded=(5,)))).digest == first.digest
    # Описание сегмента поправили — другой вопрос судье, другая смета; пробелы — не правка.
    owner.description = f"  {owner.description}  ".replace(" ", "  ")
    await session.flush()
    assert (await estimate(session, ask(owner.id, excluded=(5,)))).digest == first.digest
    owner.description = "Tiendas de bicicletas con taller propio."
    await session.flush()
    assert (await estimate(session, ask(owner.id, excluded=(5,)))).digest != first.digest


# --- смета и запуск через маршруты ---------------------------------------------------------------


async def test_the_estimate_answers_the_excluded_apart_with_lines_and_a_digest(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    owner = await hypothesis(session)

    response = await client.post(
        f"{SEGMENT_URL}/estimate", json=asked(owner.id, excluded=[4]), headers=headers
    )

    assert response.status_code == 200, response.text
    found = response.json()
    assert (found["keywords"], found["excluded"]) == (
        ["zahnarzt berlin", "zahnarzt notdienst berlin"],
        ["zahnarzt kosten"],
    )
    assert found["keyword_lines"] == {
        "zahnarzt berlin": 1,
        "zahnarzt notdienst berlin": 2,
        "zahnarzt kosten": 4,
    }
    assert found["rejected"] == {"Zahnarzt Berlin": REPEAT.format(line=1)}
    assert found["segment"] == owner.description
    assert len(found["digest"]) == segment_estimate_keys.DIGEST_LENGTH


async def test_a_start_with_excluded_buys_no_serp_for_them(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str], jobs: _Jobs
) -> None:
    """Запуск со снятой строкой: прогон не несёт снятого ключа, смета прогона его называет, а
    проход задачи покупает выдачу только по взятым (подделка выдачи записывает ключи)."""
    owner = await hypothesis(session)

    response = await client.post(
        RUNS, json=await seen(client, headers, asked(owner.id, excluded=[2])), headers=headers
    )

    assert response.status_code == 202, response.text
    run = await session.scalar(select(SalesSegmentRunModel))
    assert run is not None
    assert run.keywords == ["zahnarzt berlin", "zahnarzt kosten"]
    assert run.estimate["excluded"] == {"zahnarzt notdienst berlin": 2}
    assert run.estimate["keywords"] == 2
    journal = await session.scalar(
        select(AuditLogModel).where(AuditLogModel.action == AuditAction.RUN_STARTED)
    )
    assert journal is not None
    assert journal.details is not None
    assert journal.details["снято ключей"] == "«zahnarzt notdienst berlin»"

    bought = Serp({})
    await go(session, run, bought, Model({}))

    assert bought.calls == [("zahnarzt berlin", "zahnarzt kosten")]


@pytest.mark.parametrize(
    "change", ["segment", "excluded", "excluded_only", "serp_mode", "judge_mode", "keywords"]
)
async def test_a_start_on_a_stale_estimate_is_refused_in_words(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    jobs: _Jobs,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    """Между сметой и запуском поменялось то, что видел человек: описание сегмента (правка в
    соседней вкладке), снятые ключи или ключи (тело запуска не то, что у сметы; и когда покупаемые
    те же, а снятого нет), режим выдачи по умолчанию или режим судьи (настройка сервиса) — 409
    «смета устарела», прогона и задачи нет."""
    owner = await hypothesis(session)
    sent = await seen(client, headers, asked(owner.id, excluded=[4]))
    if change == "segment":
        owner.description = "Zahnarztpraxen und Kieferorthopäden mit eigener Praxis."
        await session.flush()
    elif change == "excluded":
        sent["excluded"] = []
    elif change == "excluded_only":
        # Покупаемые те же, но снятого в запуске нет: строку убрали из текста, а не сняли.
        sent["keywords"] = sent["keywords"].replace("\nzahnarzt kosten", "")
        sent["excluded"] = []
    elif change == "keywords":
        sent["keywords"] += "\nzahnarzt notdienst"
    elif change == "serp_mode":
        monkeypatch.setattr(serp_cfg, "MODE", "live")
    else:
        monkeypatch.setattr(sales_cfg, "SEGMENT_JUDGE_MODE", JudgeMode.ENFORCE)

    response = await client.post(RUNS, json=sent, headers=headers)

    assert (response.status_code, response.json()["detail"]) == (409, segment_api.STALE)
    assert response.json()["detail"] == "Прогон не запущен: смета устарела — посчитайте заново"
    assert await session.scalar(select(func.count()).select_from(SalesSegmentRunModel)) == 0
    assert jobs.enqueued == []


async def test_a_start_without_a_digest_is_refused_in_words(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str], jobs: _Jobs
) -> None:
    owner = await hypothesis(session)

    response = await client.post(RUNS, json=asked(owner.id), headers=headers)

    assert (response.status_code, response.json()["detail"]) == (409, segment_api.UNSEEN)
    assert jobs.enqueued == []


async def test_all_taken_excluded_closes_the_start_in_words(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str], jobs: _Jobs
) -> None:
    owner = await hypothesis(session)

    response = await client.post(
        RUNS, json=await seen(client, headers, asked(owner.id, excluded=[1, 2, 4])), headers=headers
    )

    assert (response.status_code, response.json()["detail"]) == (
        409,
        f"Прогон не запущен: {ALL_EXCLUDED}",
    )
    assert await session.scalar(select(func.count()).select_from(SalesSegmentRunModel)) == 0
    assert jobs.enqueued == []


async def test_numbers_moving_after_the_estimate_do_not_stale_it(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    jobs: _Jobs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Остаток потолка модели сдвинули чужие вызовы — смета та же: запуск идёт."""
    monkeypatch.setattr(llm_cfg, "SALES_DAILY_TOKEN_CAP", 9_000_000)
    owner = await hypothesis(session)
    sent = await seen(client, headers, asked(owner.id))
    session.add(
        UsageRecordModel(
            system="outreach-donors",
            provider=UsageProvider.LLM,
            operation="sales_reply_kind",
            units=3301,
        )
    )
    await session.flush()

    response = await client.post(RUNS, json=sent, headers=headers)

    assert response.status_code == 202, response.text
    assert len(jobs.enqueued) == 1


@pytest.mark.parametrize("excluded", [[0], [-3], [20_001]])
async def test_line_numbers_out_of_bounds_are_refused_by_the_form(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str], excluded: list[int]
) -> None:
    owner = await hypothesis(session)

    response = await client.post(
        f"{SEGMENT_URL}/estimate", json=asked(owner.id, excluded=excluded), headers=headers
    )

    assert response.status_code == 422
