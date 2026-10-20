"""Тема сайта в базе и в прогоне — на настоящей базе, модель подставная.

Проверяется то, что должно уцелеть при сбое и не стоить лишнего: тема ложится на домен
пачкой рядом с вердиктом судьи и тем же текстом, свежая тема не спрашивается второй раз
(«не ясна» — свежа месяц, а не год), токены — строкой журнала `site_topic` на каждый ответ и
в потолок модели, а «модель не ответила» и ответ не по форме — своим числом с причиной в
отчёте прогона, без пустой темы на домене и не затирая прежнюю.

Домены прогона — `example.com` и `example.org`: прогон сводит адрес к домену по списку
публичных суффиксов, и выдуманная зона (`.test`) до суда не дошла бы. Хранение — на `.test`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from backend.config import topics as topics_cfg
from backend.config.judge import JudgeMode
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.ops import UsageRecordModel
from backend.features.core.models.run import RunModel
from backend.features.core.usage import LlmCapExceededError
from backend.features.donors import judging
from backend.features.donors.home_signals import HomeSignals
from backend.features.donors.publisher_judge import Intent, Judgement, Recommendation
from backend.features.runs.pipeline import RunRequest, execute_run
from backend.features.runs.repository import RunRepository
from backend.features.serp.protocol import SerpResult
from backend.features.topics.classifier import TOPIC_VERSION, SiteText, TopicVerdict
from backend.features.topics.store import TopicRecord, TopicStore, topic_counts, topic_is
from backend.features.topics.taxonomy import NOT_DEFINED
from sqlalchemy import func, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import Update
from tests import test_execute_run as base
from tests.migration_helpers import columns_down_and_up

# --- хранение ----------------------------------------------------------------------

FINANCE = TopicRecord(
    "finance", "business", "ставки по кредитам", "gpt-5-mini", TOPIC_VERSION, "serp"
)


async def _domain(session: AsyncSession, host: str, **values: Any) -> DomainModel:
    domain = DomainModel(host=host, **values)
    session.add(domain)
    await session.flush()
    return domain


class TestStore:
    async def test_saved_topic_is_fresh_and_a_new_version_is_not(
        self, session: AsyncSession
    ) -> None:
        store = TopicStore(session)
        await store.save({"money.test": FINANCE, "garden.test": FINANCE})

        assert (await store.known(["money.test"], version=TOPIC_VERSION)).fresh == {"money.test"}
        known = await store.known(["money.test"], version="topic-v2")
        assert known.fresh == frozenset(), "новая версия промпта — тема несвежая"

    async def test_old_topic_is_not_fresh(self, session: AsyncSession) -> None:
        store = TopicStore(session)
        old = datetime.now(UTC) - timedelta(days=topics_cfg.TTL_DAYS + 1)
        await store.save({"money.test": FINANCE}, now=old)

        assert (await store.known(["money.test"], version=TOPIC_VERSION)).fresh == frozenset()

    async def test_unclear_is_fresh_for_a_month_and_a_topic_for_a_year(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """«Не понять» чаще говорит о бедном тексте, чем о сайте: через месяц спросим снова,
        а названная тема того же возраста — свежая."""
        monkeypatch.setattr(topics_cfg, "UNCLEAR_TTL_DAYS", 30)
        unclear = TopicRecord(None, None, "одно название", "gpt-5-mini", TOPIC_VERSION, "home")
        store = TopicStore(session)
        month_ago = datetime.now(UTC) - timedelta(days=31)
        await store.save({"bare.test": unclear, "money.test": FINANCE}, now=month_ago)
        await store.save({"recent.test": unclear}, now=month_ago + timedelta(days=2))

        known = await store.known(["bare.test", "money.test", "recent.test"], version=TOPIC_VERSION)

        assert known.fresh == {"money.test", "recent.test"}

    async def test_save_writes_in_host_order(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Два прогона с общими доменами пишут их в одном порядке — по имени: ждать друг
        друга по кругу им не из-за чего (#415)."""
        updated: list[str] = []
        execute = session.execute

        async def seen(statement: Any, *args: Any, **kwargs: Any) -> Any:
            if isinstance(statement, Update):
                updated.append(statement.whereclause.right.value)  # type: ignore[union-attr]
            return await execute(statement, *args, **kwargs)

        monkeypatch.setattr(session, "execute", seen)

        await TopicStore(session).save(dict.fromkeys(("c.test", "a.test", "b.test"), FINANCE))

        assert updated == ["a.test", "b.test", "c.test"]

    async def test_save_creates_the_domain_and_keeps_every_field(
        self, session: AsyncSession
    ) -> None:
        """Судья по сайту не ответил и домен не записал — тема не теряется."""
        await TopicStore(session).save({"new.test": FINANCE})

        domain = (
            await session.execute(select(DomainModel).where(DomainModel.host == "new.test"))
        ).scalar_one()
        assert (domain.site_topic, domain.site_topic_secondary) == ("finance", "business")
        assert (domain.topic_reason, domain.topic_model) == ("ставки по кредитам", "gpt-5-mini")
        assert (domain.topic_version, domain.topic_source) == (TOPIC_VERSION, "serp")
        assert domain.topic_at is not None

    async def test_unclear_topic_is_stored_as_knowledge(self, session: AsyncSession) -> None:
        """«Не ясна» — модель прочла и темы не назвала: это знание, второй раз не платим."""
        unclear = TopicRecord(None, None, "страница входа", "gpt-5-mini", TOPIC_VERSION, "home")
        store = TopicStore(session)
        await store.save({"login.test": unclear})

        assert (await store.known(["login.test"], version=TOPIC_VERSION)).fresh == {"login.test"}

    async def test_stored_home_title_is_known(self, session: AsyncSession) -> None:
        await _domain(
            session, "judged.test", judge_home={"reached": True, "title": "Garden Weekly"}
        )
        await _domain(session, "bare.test")

        known = await TopicStore(session).known(
            ["judged.test", "bare.test", "absent.test"], version=TOPIC_VERSION
        )

        assert known.home_titles == {"judged.test": "Garden Weekly"}
        assert known.fresh == frozenset()

    async def test_nothing_asked_nothing_read(self, session: AsyncSession) -> None:
        store = TopicStore(session)
        assert (await store.known([], version=TOPIC_VERSION)).fresh == frozenset()
        assert await store.save({}) == 0


def _screen(*columns: Any) -> Any:
    """Основа «экрана» для счётчиков: домены с донором, как у таблицы доноров."""
    return (
        select(*columns)
        .select_from(DonorModel)
        .join(DomainModel, DomainModel.id == DonorModel.domain_id)
    )


class TestFilterAndCounts:
    async def test_filter_and_counts_use_one_rule(self, session: AsyncSession) -> None:
        """Тема — основная или вторая; пункт фильтра считает то, что фильтр найдёт."""
        rows = {
            "ledger.test": ("news", "finance"),
            "money.test": ("finance", None),
            "trip.test": ("travel", None),
            "blank.test": (None, None),
        }
        for host, (topic, second) in rows.items():
            domain = await _domain(session, host, site_topic=topic, site_topic_secondary=second)
            session.add(DonorModel(domain_id=domain.id))
        await session.flush()

        counts = await topic_counts(session, _screen)

        assert counts == {"news": 1, "finance": 2, "travel": 1, NOT_DEFINED: 1}
        for code, count in counts.items():
            found = await session.scalar(_screen(func.count(DonorModel.id)).where(topic_is(code)))
            assert found == count, code


# --- в прогоне ---------------------------------------------------------------------


class Titled(base.FakeSerp):
    """Выдача с заголовками: без текста определять тему не по чему."""

    async def search(
        self, keywords: Sequence[str], country: str, *, depth_pages: int = 1
    ) -> dict[str, list[SerpResult]]:
        return {
            kw: [SerpResult(i + 1, u, title=f"Заголовок {u}") for i, u in enumerate(self._urls)]
            for kw in keywords
        }


@pytest.fixture
def judge(monkeypatch: pytest.MonkeyPatch) -> None:
    """Судья — подставной и без главных; тематика включена."""

    async def fake(http: object, **kwargs: object) -> Judgement:
        return Judgement(
            Intent.EDITORIAL_ADS, Recommendation.ACCEPT, "Заголовок", "издание", "m", 400
        )

    monkeypatch.setattr("backend.features.donors.judging.judge_host", fake)
    monkeypatch.setattr("backend.config.judge.HOME_CHECK", False)
    monkeypatch.setattr("backend.config.judge.MODE", JudgeMode.SHADOW)
    monkeypatch.setattr(topics_cfg, "MODE", topics_cfg.TopicMode.ON)


def _model(monkeypatch: pytest.MonkeyPatch, answers: dict[str, TopicVerdict]) -> list[SiteText]:
    """Модель тематики без сети: ответ по домену; тексты, которые она прочла, — в список."""
    read: list[SiteText] = []

    async def fake(http: httpx.AsyncClient, *, host: str, text: SiteText) -> TopicVerdict:
        read.append(text)
        return answers[host]

    monkeypatch.setattr("backend.features.topics.step.classify", fake)
    return read


def _topic(code: str | None, tokens: int = 300) -> TopicVerdict:
    return TopicVerdict(code, None, "по заголовку", "gpt-5-mini", tokens, 200, tokens - 200, "serp")


async def _run(session: AsyncSession, hosts: list[str]) -> Any:
    serp = Titled([f"https://{host}/a" for host in hosts])
    client = base._ahrefs(dict.fromkeys(hosts, base.GOOD))
    return await execute_run(
        await base._deps(session, serp, client),
        RunRequest(
            keywords=["k"],
            country="us",
            thresholds=base.T,
            settings_id=await base._settings_id(session),
        ),
    )


async def _topic_tokens(session: AsyncSession) -> int | None:
    return await session.scalar(
        select(func.sum(UsageRecordModel.units)).where(UsageRecordModel.operation == "site_topic")
    )


@pytest.mark.usefixtures("judge")
class TestInTheRun:
    async def test_topics_land_with_their_tokens(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        read = _model(
            monkeypatch,
            {"example.com": _topic("finance"), "example.org": _topic("travel", 500)},
        )

        report = await _run(session, ["example.com", "example.org"])

        topics = dict(
            (await session.execute(select(DomainModel.host, DomainModel.site_topic))).tuples().all()
        )
        assert topics == {"example.com": "finance", "example.org": "travel"}
        assert {text.text for text in read} == {
            "Страница из выдачи: Заголовок https://example.com/a",
            "Страница из выдачи: Заголовок https://example.org/a",
        }, "тот же текст выдачи, что у судьи, без захода на сайт"
        assert await _topic_tokens(session) == 800
        lines = await session.scalar(
            select(func.count()).where(UsageRecordModel.operation == "site_topic")
        )
        assert lines == 2, "строка журнала — на каждый ответ, сразу"
        assert report.topics is not None
        assert report.topics.classified == 2
        stats = (await session.execute(select(RunModel.stats))).scalar_one()
        assert stats["topics"]["classified"] == 2
        assert stats["topics"]["by_topic"] == {"finance": 1, "travel": 1}
        assert stats["topics"]["tokens"] == 800

    async def test_fresh_topic_is_not_asked_again(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await TopicStore(session).save({"example.com": FINANCE})
        read = _model(monkeypatch, {"example.org": _topic("news")})

        report = await _run(session, ["example.com", "example.org"])

        assert len(read) == 1
        assert report.topics.from_cache == 1
        assert report.topics.classified == 1

    async def test_a_silent_model_is_counted_with_its_reason(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ключ не годен — «модель не ответила» числом и причиной; тема на домен не легла,
        и следующий прогон спросит снова. Ноль определённых без этого читался бы «тем нет»."""
        silent = TopicVerdict(None, None, "модель, отказ (чинить): HTTP 401", unanswered=True)
        _model(monkeypatch, {"example.com": silent})

        report = await _run(session, ["example.com"])

        assert (report.topics.unanswered, report.topics.classified) == (1, 0)
        stats = (await session.execute(select(RunModel.stats))).scalar_one()
        assert stats["topics"]["unanswered"] == 1
        assert "HTTP 401" in stats["topics"]["unanswered_reason"]
        domain = (
            await session.execute(select(DomainModel).where(DomainModel.host == "example.com"))
        ).scalar_one()
        assert (domain.site_topic, domain.topic_at) == (None, None)
        assert await _topic_tokens(session) is None

    async def test_an_answer_out_of_form_keeps_the_old_topic_and_its_tokens_count(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Тема устарела, а модель ответила не по форме: прежняя тема цела, токены — в журнале,
        сбой — своим числом и причиной, а не «не ясна»."""
        old = datetime.now(UTC) - timedelta(days=topics_cfg.TTL_DAYS + 1)
        await TopicStore(session).save({"example.com": FINANCE}, now=old)
        broken = TopicVerdict(
            None, None, "модель назвала тему не из списка: «finance, business»", "gpt-5-mini",
            700, 500, 200, "serp", unanswered=True, unreadable=True,
        )  # fmt: skip
        _model(monkeypatch, {"example.com": broken})

        report = await _run(session, ["example.com"])

        assert (report.topics.unreadable, report.topics.unclear, report.topics.unanswered) == (
            1,
            0,
            0,
        )
        stats = (await session.execute(select(RunModel.stats))).scalar_one()
        assert "не из списка" in stats["topics"]["unreadable_reason"]
        domain = (
            await session.execute(select(DomainModel).where(DomainModel.host == "example.com"))
        ).scalar_one()
        assert (domain.site_topic, domain.topic_at) == ("finance", old)
        assert await _topic_tokens(session) == 700

    async def test_cap_reached_before_topics_stops_the_run_and_keeps_the_verdicts(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Потолок модели — и перед темами: прогон останавливается причиной, а вердикты
        судьи пачки уже в базе."""
        checks = {"n": 0}

        async def cap(self: RunRepository, *, run_id: int | None) -> None:
            checks["n"] += 1
            if checks["n"] == 2:
                raise LlmCapExceededError("потолок модели на сегодня исчерпан")

        monkeypatch.setattr(RunRepository, "ensure_llm_within_cap", cap)
        read = _model(monkeypatch, {"example.com": _topic("finance")})

        with pytest.raises(LlmCapExceededError):
            await _run(session, ["example.com"])

        assert read == [], "модель тематики не звали"
        domain = (
            await session.execute(select(DomainModel).where(DomainModel.host == "example.com"))
        ).scalar_one()
        assert domain.judged_at is not None
        assert domain.site_topic is None

    async def test_mode_off_asks_nothing_and_reports_no_topics(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Тематика выключена — шага нет: в отчёте ни ветки, ни нулей «определено 0»."""
        monkeypatch.setattr(topics_cfg, "MODE", topics_cfg.TopicMode.OFF)
        read = _model(monkeypatch, {})

        report = await _run(session, ["example.com"])

        assert read == []
        assert report.topics is None
        stats = (await session.execute(select(RunModel.stats))).scalar_one()
        assert "topics" not in stats
        assert await _topic_tokens(session) is None


async def test_the_judges_home_is_read_by_topics(monkeypatch: pytest.MonkeyPatch) -> None:
    """Главная, открытая судьёй, — в тексте темы: второй раз за ней не ходят."""
    home = HomeSignals(reached=True, title="Daily Ledger", description="City news")

    async def fake_judge(http: object, **kwargs: object) -> Judgement:
        return Judgement(Intent.EDITORIAL_ADS, Recommendation.ACCEPT, "x", "издание", "m", 10)

    async def opened(http: object, home_client: object, **kwargs: Any) -> Any:
        return kwargs["verdict"], home

    monkeypatch.setattr(judging, "judge_host", fake_judge)
    monkeypatch.setattr(judging, "second_opinion", opened)

    passed = await judging.judge_candidates(
        object(),  # type: ignore[arg-type]
        ["ledger.test"],
        {},
        home_client=None,
    )

    assert passed.homes == {"ledger.test": home}


# --- ревизия --------------------------------------------------------------------------

MIGRATION = "b14a5e5368a1_site_topic.py"
TOPIC_COLUMNS = {
    "site_topic",
    "site_topic_secondary",
    "topic_reason",
    "topic_model",
    "topic_version",
    "topic_source",
    "topic_at",
}


def _topic_indexes(connection: Connection) -> list[str]:
    found = connection.execute(
        text(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'domains' "
            "AND indexdef ILIKE '%topic%'"
        )
    )
    return list(found.scalars())


def _down_and_up(connection: Connection) -> tuple[set[str], set[str], list[str]]:
    down, up = columns_down_and_up(connection, MIGRATION, "domains", TOPIC_COLUMNS)
    return down, up, _topic_indexes(connection)


async def test_revision_goes_down_and_up(session: AsyncSession) -> None:
    """Ревизия исполняется на базе теста: вниз снимает колонки, вверх — ставит. Индекса по
    теме нет намеренно: фильтр — ИЛИ по двум колонкам, и план его не выбирал, а постройка
    индекса на выкатке держала бы запись в `domains`."""
    connection = await session.connection()

    down, up, indexes = await connection.run_sync(_down_and_up)

    assert down == set()
    assert up == TOPIC_COLUMNS
    assert indexes == []
