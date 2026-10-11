"""Письмо доноров и рекламодателей в коридоре отличия — подбором принятых зон, без второго
вызова модели (`letters/building.py` поверх ядра `letters/selection.py`).

Живые прогоны 11.10.2026: просьбу «поменяй N% слов» при большой переписываемой доле модель
не исполняет — переписывает зоны почти целиком, и письмо уходит за верхний край коридора.
Сборка берёт из того же ответа набор принятых зон, с которым письмо в коридоре; зерно
выбора — номер домена адресата. Ни один набор не попал — письмо со всеми зонами встаёт
в очередь с пометкой, как раньше: очередь доноров смотрит человек, повторного вызова нет.

Модель заменена подделкой, у которой доля переписанных слов каждой зоны задана тестом
(`ZoneRewriter`); база настоящая. Номера доменов заданы там, где важно зерно: выбор с ним
повторим. Тексты и адреса выдуманы.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

import pytest
from backend.cli import letters_queue
from backend.features.core.domain import ContactSource, DonorStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.ops import UsageRecordModel
from backend.features.core.models.outreach import MessageModel
from backend.features.crawl import niche
from backend.features.letters import compose, selection
from backend.features.letters.building import BuildReport, BuildRequest, QueueBuilder
from backend.features.letters.compose import Rendered
from backend.features.letters.guards import ForbiddenContentError
from backend.features.letters.rewrite import Personalization, RewriteResult
from backend.features.letters.template import ZoneKind
from backend.features.letters.uniqueness import (
    above_corridor,
    corridor_verdict,
    difference,
    in_corridor,
)
from backend.workers import jobs
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.test_letters_advertisers import ANCHOR, DONOR, PAGE, make_advertiser, priced_donor
from tests.test_niche_letters import BOOKIE, _business

#: Токенов на вызов у подделки — число выдуманное.
TOKENS = 113
#: Метрика, дописанная «моделью»: правила Ahrefs запрещают её в письме (`guards.py`).
LEAK = " Your DR 46 site."
#: Вопрос о цене «переписан» на 40% слов, приветствие и вступление — целиком. У письма
#: донору всё принятое тогда выше коридора, а в коридоре — вопрос с приветствием или без;
#: наибольший набор — вопрос с приветствием, вступление остаётся шаблоном.
ASK = {"ask": 0.4}
#: Вступление и вопрос «переписаны» на 30% слов: всё письмо в коридоре (около 20%), как и
#: четыре меньших набора.
WHOLE = {"opening": 0.3, "ask": 0.3}
#: Номера доменов доноров — зерно подбора: с ними выбор повторим, и наборы у них разные.
SEEDED = tuple(range(7100001, 7100005))
#: Ядро как есть: тест спрашивает его сам, даже когда сборке подставлен шпион.
CORE = selection.select

_WORD = re.compile(r"\b[^\W\d_]\w*")


def _words(name: str, count: int) -> str:
    return " ".join(f"{name}{index}" for index in range(count))


#: Письмо донору из слов-меток — 100 слов с подписью и адресом (у адреса тестов 4 слова,
#: `conftest.FILLED`, — на столько короче условия). Целиком переписанные вступление (20%)
#: и вопрос (21%) — каждое в коридоре, любая пара — выше края: наибольших наборов два, по
#: одной зоне, выбор между ними — за зерном, и каждое письмо оставляет шаблоном две зоны.
TWO_SINGLES = (
    "subject: Made-up letter for {{host}}\n\n"
    f"[greeting] rewrite\n{_words('hello', 6)}\n\n"
    f"[opening] rewrite\n{_words('intro', 20)}\n\n"
    f"[offer] fixed\n{_words('offer', 25)}\n\n"
    f"[ask] rewrite\n{_words('question', 21)}\n\n"
    f"[terms] fixed\n{_words('terms', 20)}\n\n"
    "[signature] fixed\nBest regards,\n{{sender_name}}\n{{postal_address}}\n"
)


def _changed(text: str, share: float) -> str:
    """Первые `share` слов зоны — другими словами («Hi» → «reHi»), остальные — как были;
    номера пунктов списка вопросов остаются на месте."""
    count = round(len(_WORD.findall(text)) * share)
    done = 0

    def swap(word: re.Match[str]) -> str:
        nonlocal done
        done += 1
        return f"re{word.group(0)}" if done <= count else word.group(0)

    return _WORD.sub(swap, text)


class ZoneRewriter:
    """Подделка модели: зона `rewrite` переписана на заданную долю слов — по умолчанию
    целиком, как переписывала живая модель. Помнит, что видела и что вернула, по хосту.
    `silent` — хосты, у которых не принята ни одна зона."""

    def __init__(
        self,
        share: Mapping[str, float] | None = None,
        *,
        refuse: tuple[str, ...] = (),
        leak: tuple[str, ...] = (),
        silent: tuple[str, ...] = (),
    ) -> None:
        self.share = dict(share or {})
        self.refuse = refuse
        self.leak = leak
        self.silent = silent
        self.calls = 0
        self.rendered: dict[str, Rendered] = {}
        self.zones: dict[str, dict[str, str]] = {}

    async def rewrite(self, rendered: Rendered, about: Personalization) -> RewriteResult:
        self.calls += 1
        refused = (
            tuple(zone.name for zone in rendered.rewritable())
            if about.host in self.silent
            else self.refuse
        )
        zones = {
            zone.name: _changed(zone.text, self.share.get(zone.name, 1.0))
            + (LEAK if zone.name in self.leak else "")
            for zone in rendered.rewritable()
            if zone.name not in refused
        }
        self.rendered[about.host], self.zones[about.host] = rendered, zones
        # Отказ по зоне — нотой, как у настоящего переписчика (`RewriteClient._collect`).
        notes = [f"зона «{name}» осталась шаблонной" for name in refused]
        return RewriteResult(zones=zones, tokens_spent=TOKENS, notes=notes)

    async def aclose(self) -> None:
        """Задача сборки закрывает переписчик в конце (`jobs._build_letters`)."""

    def chosen(self, host: str, domain_id: int) -> selection.Selection | None:
        """Набор, который ядро выбирает этому адресату с зерном — номером его домена."""
        return CORE(self.rendered[host], self.zones[host], seed=domain_id)

    def assembled(self, host: str, zones: Mapping[str, str]) -> compose.Letter:
        return compose.assemble(self.rendered[host], dict(zones))

    def every_zone_above(self, host: str) -> bool:
        """Письмо со всеми принятыми зонами — за верхним краем: без подбора оно вне коридора."""
        full = self.assembled(host, self.zones[host])
        return above_corridor(difference(full.plain_body, full.body))


async def _build(session: AsyncSession, rewriter: ZoneRewriter, **request: object) -> BuildReport:
    values: dict[str, object] = {"campaign_name": "Подбор", "niche": ("home repair",)}
    values.update(request)
    report = await QueueBuilder(session, rewriter).build(BuildRequest(**values))  # type: ignore[arg-type]
    await session.flush()
    return report


async def _donor(session: AsyncSession, domain_id: int) -> DomainModel:
    """Принятый донор с адресом — с заданным номером домена, зерном подбора."""
    domain = DomainModel(id=domain_id, host=f"site{domain_id}.example.test")
    session.add(domain)
    await session.flush()
    session.add(
        DonorModel(domain_id=domain_id, status=DonorStatus.SUITABLE, dr=30, review="accepted")
    )
    session.add(
        ContactModel(domain_id=domain_id, email=f"editor@{domain.host}", source=ContactSource.PAGE)
    )
    await session.flush()
    return domain


async def _letters(session: AsyncSession) -> dict[int, MessageModel]:
    """Первые письма очереди — по номеру домена адресата."""
    rows = await session.scalars(select(MessageModel).where(MessageModel.step == 0))
    return {letter.domain_id: letter for letter in rows}


def _spy_seeds(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int]]:
    """Зёрна, с которыми сборка зовёт подбор, — рядом с темой письма (в ней хост адресата)."""
    seen: list[tuple[str, int]] = []

    def spy(
        rendered: Rendered, rewritten: Mapping[str, str], *, seed: int, limit: int = selection.LIMIT
    ) -> selection.Selection | None:
        seen.append((rendered.subject, seed))
        return CORE(rendered, rewritten, seed=seed, limit=limit)

    monkeypatch.setattr(selection, "select", spy)
    return seen


def _assert_selected(
    letter: MessageModel, rewriter: ZoneRewriter, host: str, chosen: selection.Selection
) -> None:
    """В очереди — письмо выбранного набора: его текст, отличие собранного письма в коридоре,
    неизменяемые зоны дословно."""
    rendered = rewriter.rendered[host]
    body = letter.body or ""
    assert body == compose.assemble(rendered, chosen.zones).body
    assert letter.uniqueness_pct is not None
    assert letter.uniqueness_pct == pytest.approx(difference(rendered.body, body))
    assert in_corridor(letter.uniqueness_pct)
    assert all(zone.text in body for zone in rendered.zones if zone.kind is ZoneKind.FIXED)


class TestDonorLetter:
    async def test_letter_above_the_edge_goes_into_the_corridor_by_selection(
        self, session: AsyncSession, filled_legal: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Всё принятое выше верхнего края — в очереди письмо набора, который ядро выбрало
        с зерном — номером домена донора: наибольшего в коридоре, без пометки. Снятая подбором
        зона — не отказ модели: нот нет, в отчёте — число писем и зон."""
        domains = [await _donor(session, domain_id) for domain_id in SEEDED]
        seen = _spy_seeds(monkeypatch)
        rewriter = ZoneRewriter(ASK)

        report = await _build(session, rewriter)

        assert sorted(seen) == sorted((f"Guest article on {d.host}", d.id) for d in domains)
        letters = await _letters(session)
        for domain in domains:
            chosen = rewriter.chosen(domain.host, domain.id)
            assert rewriter.every_zone_above(domain.host)
            assert chosen is not None
            assert (tuple(chosen.zones), chosen.dropped) == (("greeting", "ask"), ("opening",))
            _assert_selected(letters[domain.id], rewriter, domain.host, chosen)
        assert (report.prepared, report.off_corridor, report.notes) == (4, 0, {})
        assert (report.selected_letters, report.dropped_zones) == (4, 4)

    async def test_seed_from_the_domain_tells_equally_large_sets_apart(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Наибольших наборов в коридоре два, по одной зоне: какой войдёт в письмо, решает
        зерно — номер домена донора, и у доноров с разными номерами наборы разные."""
        domains = [await _donor(session, domain_id) for domain_id in SEEDED]
        rewriter = ZoneRewriter()

        report = await _build(session, rewriter, letter_template=TWO_SINGLES)

        letters = await _letters(session)
        picked = set()
        for domain in domains:
            chosen = rewriter.chosen(domain.host, domain.id)
            assert chosen is not None
            assert len(chosen.zones) == 1
            _assert_selected(letters[domain.id], rewriter, domain.host, chosen)
            picked.add(tuple(chosen.zones))
        assert picked == {("opening",), ("ask",)}
        assert (report.selected_letters, report.dropped_zones) == (4, 8)

    async def test_no_set_in_the_corridor_queues_the_letter_with_every_zone(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Подбирать нечего — письмо со всеми принятыми зонами встаёт в очередь с пометкой
        «выше коридора», как до подбора, и второго вызова модели нет."""
        domain = await _donor(session, SEEDED[0])
        rewriter = ZoneRewriter()

        report = await _build(session, rewriter)

        assert rewriter.chosen(domain.host, domain.id) is None
        letter = (await _letters(session))[domain.id]
        assert letter.body == rewriter.assembled(domain.host, rewriter.zones[domain.host]).body
        assert letter.uniqueness_pct is not None
        assert "выше коридора" in (corridor_verdict(letter.uniqueness_pct) or "")
        assert (report.prepared, report.off_corridor) == (1, 1)
        assert (report.selected_letters, report.dropped_zones) == (0, 0)
        assert (rewriter.calls, report.tokens_spent) == (1, TOKENS)

    async def test_tokens_per_letter_are_the_same_as_without_selection(
        self, session: AsyncSession, filled_legal: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Подбор — из того же ответа: вызов модели на письмо один, токены и строки расхода —
        те же, что у сборки без подбора."""
        for domain_id in SEEDED:
            await _donor(session, domain_id)
        chosen_by_core = ZoneRewriter(ASK)
        with_selection = await _build(session, chosen_by_core, campaign_name="С подбором", limit=2)
        monkeypatch.setattr(selection, "select", lambda *_args, **_kwargs: None)
        every_zone = ZoneRewriter(ASK)
        without = await _build(session, every_zone, campaign_name="Без подбора", limit=2)

        assert (with_selection.selected_letters, without.selected_letters) == (2, 0)
        assert (with_selection.off_corridor, without.off_corridor) == (0, 2)
        assert chosen_by_core.calls == every_zone.calls == 2
        assert with_selection.tokens_spent == without.tokens_spent == 2 * TOKENS
        units = await session.scalars(
            select(UsageRecordModel.units).where(UsageRecordModel.operation == "letter_rewrite")
        )
        assert list(units) == [TOKENS] * 4

    async def test_zone_the_model_refused_is_a_note_and_the_dropped_one_is_not(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Отказ модели по зоне — нота «осталась шаблонной», как раньше. Зона, которую снял
        подбор, — не отказ: ноты о ней нет, она в отчёте числом."""
        domain = await _donor(session, SEEDED[0])
        rewriter = ZoneRewriter(ASK, refuse=("greeting",))

        report = await _build(session, rewriter)

        chosen = rewriter.chosen(domain.host, domain.id)
        assert chosen is not None
        assert chosen.dropped == ("opening",)
        assert report.notes == {"зона «greeting» осталась шаблонной": 1}
        assert (report.selected_letters, report.dropped_zones, report.off_corridor) == (1, 1, 0)

    async def test_letter_wholly_in_the_corridor_keeps_every_zone(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Письмо, целиком лежащее в коридоре, уходит целиком — со вступлением под контент
        донора, хотя в коридоре и меньшие наборы. Подбором оно не считается: в
        `selected_letters` его нет, `dropped_zones` не растёт."""
        domains = [await _donor(session, domain_id) for domain_id in SEEDED]
        rewriter = ZoneRewriter(WHOLE)

        report = await _build(session, rewriter)

        letters = await _letters(session)
        for domain in domains:
            whole = rewriter.assembled(domain.host, rewriter.zones[domain.host])
            assert letters[domain.id].body == whole.body
            assert in_corridor(letters[domain.id].uniqueness_pct or 0.0)
        assert (report.prepared, report.off_corridor, report.notes) == (4, 0, {})
        assert (report.selected_letters, report.dropped_zones) == (0, 0)

    async def test_metrics_in_a_chosen_zone_stop_the_build(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Метрика в принятой зоне — отказ сборки: проверка стоит на собранном письме."""
        await _donor(session, SEEDED[0])

        with pytest.raises(ForbiddenContentError, match="DR 46"):
            await _build(session, ZoneRewriter(ASK, leak=("greeting", "opening", "ask")))

    async def test_metrics_in_a_dropped_zone_do_not_reach_the_letter(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Проверяется текст выбранного набора: метрика в зоне, которую подбор снял, до
        адресата не доходит и письма не держит."""
        domain = await _donor(session, SEEDED[0])
        rewriter = ZoneRewriter({**ASK, "opening": 0.5}, leak=("opening",))

        await _build(session, rewriter)

        chosen = rewriter.chosen(domain.host, domain.id)
        assert chosen is not None
        assert "opening" in chosen.dropped
        letter = (await _letters(session))[domain.id]
        _assert_selected(letter, rewriter, domain.host, chosen)
        assert "DR 46" not in (letter.body or "")


class TestOffers:
    """Коридор есть и у офферов Этапа 2 — по найденной ссылке и бизнесу ниши: подбор тот же,
    подстановки неизменяемых зон доходят дословно."""

    async def test_offer_keeps_the_found_link_word_for_word(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        await priced_donor(session)
        brand = await make_advertiser(session, "brand.example.test")
        rewriter = ZoneRewriter()

        report = await _build(session, rewriter, campaign_name="Офферы", stage=Stage.ADVERTISERS)

        chosen = rewriter.chosen(brand.host, brand.id)
        assert rewriter.every_zone_above(brand.host)
        assert chosen is not None
        letter = (await _letters(session))[brand.id]
        _assert_selected(letter, rewriter, brand.host, chosen)
        assert letter.subject == f"Your placement on {DONOR}"
        assert all(
            part in (letter.body or "") for part in (f"link on {DONOR}", PAGE, f'"{ANCHOR}"')
        )
        assert (report.off_corridor, report.notes) == (0, {})

    async def test_niche_offer_keeps_the_example_and_the_topic(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        await priced_donor(session, DONOR)
        await _business(session)
        bookie = await session.scalar(select(DomainModel).where(DomainModel.host == BOOKIE))
        assert bookie is not None
        rewriter = ZoneRewriter()

        report = await _build(
            session, rewriter, campaign_name="Ниша", stage=Stage.ADVERTISERS, audience=niche.NICHE
        )

        chosen = rewriter.chosen(BOOKIE, bookie.id)
        assert rewriter.every_zone_above(BOOKIE)
        assert chosen is not None
        letter = (await _letters(session))[bookie.id]
        _assert_selected(letter, rewriter, BOOKIE, chosen)
        assert all(part in (letter.body or "") for part in (DONOR, "sports betting", "Germany"))
        assert (report.off_corridor, report.notes) == (0, {})


class _Engine:
    """Движок задачи: тест отдаёт ей свою сессию, закрывать нечего."""

    async def dispose(self) -> None:
        return None


class TestBuildSummary:
    """Итог сборки: числа подбора — отдельно от нот, и у задачи, и в консоли."""

    async def test_job_report_carries_the_selection_numbers(
        self, session: AsyncSession, filled_legal: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Итог задачи сборки — тот, что лежит у задачи за экраном, — несёт письма,
        собранные подбором, и зоны, оставленные шаблоном, каждое своим полем. Шаблон — две
        одиночки в коридоре: каждое письмо подбора оставляет шаблоном две зоны."""
        domains = [await _donor(session, domain_id) for domain_id in SEEDED[:3]]
        rewriter = ZoneRewriter(silent=(domains[2].host,))
        monkeypatch.setattr(jobs, "RewriteClient", lambda: rewriter)
        monkeypatch.setattr(jobs, "create_async_engine", lambda dsn: _Engine())
        monkeypatch.setattr(
            jobs,
            "async_sessionmaker",
            lambda engine, **kw: async_sessionmaker(bind=session.bind, expire_on_commit=False),
        )

        report = await jobs._build_letters(
            BuildRequest(campaign_name="Задача", letter_template=TWO_SINGLES)
        )

        chosen = [rewriter.chosen(domain.host, domain.id) for domain in domains[:2]]
        dropped = sum(len(found.dropped) for found in chosen if found is not None)
        assert len({3, 2, dropped}) == 3, "числа различимы: перепутанное поле не пройдёт"
        summary = (report["prepared"], report["selected_letters"], report["dropped_zones"])
        assert summary == (3, 2, dropped)
        assert report["off_corridor"] == 1

    def test_console_names_the_selection_only_when_there_was_one(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Строка рядом с «Вне коридора отличия»: сколько писем собрал подбор и сколько
        переписанных зон осталось текстом шаблона; без подбора строки нет."""
        letters_queue._print_build(
            BuildReport(campaign_id=7, prepared=3, selected_letters=2, dropped_zones=5)
        )
        said = capsys.readouterr().out
        letters_queue._print_build(BuildReport(campaign_id=7, prepared=3))
        quiet = capsys.readouterr().out

        assert "Подбор под коридор: писем 2, шаблонными оставлено зон 5." in said
        assert "Подбор под коридор" not in quiet
