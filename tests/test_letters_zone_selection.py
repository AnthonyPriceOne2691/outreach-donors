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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_letters_advertisers import ANCHOR, DONOR, PAGE, make_advertiser, priced_donor
from tests.test_niche_letters import BOOKIE, _business

#: Токенов на вызов у подделки — число выдуманное.
TOKENS = 113
#: Метрика, дописанная «моделью»: правила Ahrefs запрещают её в письме (`guards.py`).
LEAK = " Your DR 46 site."
#: Вопрос о цене «переписан» на 40% слов, приветствие и вступление — целиком. У письма
#: донору всё принятое тогда выше коридора, а в коридоре — вопрос с приветствием или без.
ASK = {"ask": 0.4}
#: Номера доменов доноров — зерно подбора: с ними выбор повторим, и наборы у них разные.
SEEDED = tuple(range(7100001, 7100005))
#: Ядро как есть: тест спрашивает его сам, даже когда сборке подставлен шпион.
CORE = selection.select

_WORD = re.compile(r"\b[^\W\d_]\w*")


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
    целиком, как переписывала живая модель. Помнит, что видела и что вернула, по хосту."""

    def __init__(
        self,
        share: Mapping[str, float] | None = None,
        *,
        refuse: tuple[str, ...] = (),
        leak: tuple[str, ...] = (),
    ) -> None:
        self.share = dict(share or {})
        self.refuse = refuse
        self.leak = leak
        self.calls = 0
        self.rendered: dict[str, Rendered] = {}
        self.zones: dict[str, dict[str, str]] = {}

    async def rewrite(self, rendered: Rendered, about: Personalization) -> RewriteResult:
        self.calls += 1
        zones = {
            zone.name: _changed(zone.text, self.share.get(zone.name, 1.0))
            + (LEAK if zone.name in self.leak else "")
            for zone in rendered.rewritable()
            if zone.name not in self.refuse
        }
        self.rendered[about.host], self.zones[about.host] = rendered, zones
        # Отказ по зоне — нотой, как у настоящего переписчика (`RewriteClient._collect`).
        notes = [f"зона «{name}» осталась шаблонной" for name in self.refuse]
        return RewriteResult(zones=zones, tokens_spent=TOKENS, notes=notes)

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
        с зерном — номером домена донора: в коридоре, без пометки. У доноров с разными
        номерами наборы разные. Снятая подбором зона — не отказ модели: нот нет, в отчёте —
        число писем и зон."""
        domains = [await _donor(session, domain_id) for domain_id in SEEDED]
        seen = _spy_seeds(monkeypatch)
        rewriter = ZoneRewriter(ASK)

        report = await _build(session, rewriter)

        assert sorted(seen) == sorted((f"Guest article on {d.host}", d.id) for d in domains)
        letters = await _letters(session)
        picked, dropped = set(), 0
        for domain in domains:
            chosen = rewriter.chosen(domain.host, domain.id)
            assert rewriter.every_zone_above(domain.host)
            assert chosen is not None
            assert chosen.dropped
            _assert_selected(letters[domain.id], rewriter, domain.host, chosen)
            picked.add(tuple(chosen.zones))
            dropped += len(chosen.dropped)
        assert len(picked) > 1, "у доноров с разными номерами домена — разные наборы"
        assert (report.prepared, report.off_corridor, report.notes) == (4, 0, {})
        assert (report.selected_letters, report.dropped_zones) == (4, dropped)

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

    async def test_all_accepted_in_the_corridor_is_not_counted_as_selection(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """В коридоре всё принятое — письмо целиком, и подбором оно не считается: снимать
        было нечего."""
        domain = await _donor(session, SEEDED[0])
        rewriter = ZoneRewriter(ASK, refuse=("greeting", "opening"))

        report = await _build(session, rewriter)

        chosen = rewriter.chosen(domain.host, domain.id)
        assert chosen is not None
        assert (list(chosen.zones), chosen.dropped) == (["ask"], ())
        _assert_selected((await _letters(session))[domain.id], rewriter, domain.host, chosen)
        assert (report.selected_letters, report.dropped_zones, report.off_corridor) == (0, 0, 0)

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


def test_console_report_names_letters_put_into_the_corridor_by_selection(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Итог сборки в консоли — отдельной строкой: сколько писем собрал подбор и сколько
    переписанных зон осталось текстом шаблона; без подбора строки нет."""
    letters_queue._print_build(
        BuildReport(campaign_id=7, prepared=3, selected_letters=2, dropped_zones=3)
    )
    said = capsys.readouterr().out
    letters_queue._print_build(BuildReport(campaign_id=7, prepared=3))
    quiet = capsys.readouterr().out

    assert "Подбор зон под коридор: писем 2, переписанных зон оставлено текстом шаблона 3" in said
    assert "Подбор зон" not in quiet
