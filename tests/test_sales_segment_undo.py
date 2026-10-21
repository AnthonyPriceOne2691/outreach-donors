"""Отмена решения человека по сайту сбора — `segment_undo` на настоящей базе.

Тест продаж 20.10: «Принять» и «Отказ» по сайту «на проверку» есть, вернуть решение нечем. Сайты
судит настоящий проход судьи (`judge_run`) с подделкой модели — полоса, слова и кэш вердиктов те
же, что у прогона. Правило одно для любого сайта, ниши и языка: ниши разные, а слова судьи (не
только цитаты) — в четырёх письменностях: латиница, японский без пробелов, арабский справа налево,
кириллица; слова не разбираются. Проверяется:

- отмена «Принять» и «Отказ» — сайт туда и с теми словами, куда и с какими его положил судья (в
  наблюдении отказ модели — «судья отказал бы» со словами судьи), кто и когда решил — очищено;
- куда — решает правило судьи (`segment_window.landing`), а не прошитая «на проверке»;
- слов нет в кэше — словами, что не сохранились; модель не ответила — следующий проход судит снова;
- решение правила или судьи, «ещё не судили» — отказ словами; повтор — не отказ; заведённый лид без
  письма снимается вместе с решением (письма, сборка, отправка и «Принять» снова —
  `test_sales_segment_undo_lead.py`);
- путь целиком: отменённое «принят» продолжение прогона в лиды не берёт;
- **гонка с заведением лида — на настоящих фиксациях** (две сессии своего движка, у фикстуры
  `session` второго соединения нет): отмена посреди заведения ждёт его и снимает заведённого лида,
  заведение посреди отмены ждёт её и лида не заводит; строка, занятая дольше предела, — отказ
  словами.

Домены и тексты выдуманы (`*.example.test`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from backend.config.judge import JudgeMode
from backend.features.core.domain import ContactSource
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.sales import segment_judge, segment_leads, segment_report, segment_window
from backend.features.sales import segment_undo as su
from backend.features.sales.models import (
    LeadSource,
    LeadStatus,
    RejectionReason,
    SalesHypothesisModel,
    SalesLeadModel,
)
from backend.features.sales.segment_judging import judge_run
from backend.features.sales.segment_models import (
    Decider,
    Lane,
    SalesSegmentCandidateModel,
    SalesSegmentRunModel,
)
from backend.features.sales.segment_rules import KIND_WORDS
from backend.features.sales.verifier import FixtureVerifier
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.segment_facts_fakes import facts
from tests.test_sales_segment_judge import MODEL_KEY
from tests.test_sales_segment_run import (
    ANSWERS,
    NEWS,
    NEWS_WORDS,
    SHOP,
    Model,
    Sites,
    _network,
    go,
    hypothesis,
    make_run,
    serp,
)
from tests.test_send_race import MEETING, committed_sessions

__all__ = ["_network"]  # сеть прогона (DNS, платная ступень) — отсюда фикстуру видит pytest

Candidate = SalesSegmentCandidateModel
SELLER = "seller@ours.example.test"
#: Колонки решения: что ложится на сайт от судьи и от человека.
FIELDS = ("lane", "reason", "verdict", "quote", "decided_by", "model", "unanswered")


@dataclass(frozen=True, slots=True)
class Said:
    """Сайт и что о нём скажет модель: ответ строгой формой; цитата — из заголовка выдачи."""

    host: str
    title: str | None
    answer: dict[str, Any] | int | None = None


#: Справочник мастерских мебели: модель — посредник, слова судьи — латиницей (немецкий).
WORKSHOPS = Said(
    "moebel-verzeichnis.example.test",
    "Alle Möbeltischler in Ihrer Stadt finden und vergleichen",
    facts(
        "no",
        "Alle Möbeltischler in Ihrer Stadt",
        "Branchenverzeichnis für Tischler — baut selbst keine Möbel",
        kind="intermediary",
    ),
)
#: Портал языковых школ: модель — посредник, слова судьи — японским письмом без пробелов.
PORTAL = Said(
    "eikaiwa-portal.example.test",
    "全国の英会話教室を比較して口コミで選べるポータル",
    facts(
        "no",
        "全国の英会話教室を比較して口コミで選べる",
        "英会話教室の比較ポータルで、自社のレッスンはない",
        kind="intermediary",
    ),
)
#: Школа или блог — не понять: модель — дело не видно, слова судьи — арабским, справа налево.
MAYBE_SCHOOL = Said(
    "madrasat-lughat.example.test",
    "تعلم اللغة الإنجليزية مع دروسنا المجانية على الإنترنت",
    facts(
        "unsure", "تعلم اللغة الإنجليزية مع دروسنا", "غير واضح من النص: مدرسة لغات أم مدونة معلم"
    ),
)
#: Ветклиника: модель — своё дело того вида, слова судьи — латиницей (польский).
CLINIC = Said(
    "weterynarz-krakow.example.test",
    "Przychodnia weterynaryjna — umów wizytę online",
    facts("yes", "Przychodnia weterynaryjna — umów wizytę", "własna przychodnia weterynaryjna"),
)
#: Слова судьи v4: правило таблицы и пояснение модели.
INTERMEDIARY = f"{KIND_WORDS['intermediary']}: сегмент таких сайтов не просит"
UNCLEAR = "по тексту не видно, чем занят сайт, — решает человек"
ANYWHERE = "дело сайта — то, что ищет сегмент; места сегмент не называет"


def words(rule: str, said: Said) -> str:
    """Слова судьи у сайта: правило таблицы и пояснение модели вторым предложением, с заглавной
    (`segment_judge._verdict`)."""
    assert isinstance(said.answer, dict)
    why = said.answer["why"]
    return f"{rule}. {why[:1].upper()}{why[1:]}"


#: Выдача ничего не дала: правило — человеку.
EMPTY = Said("pusto.example.test", None)
#: Модель не дала цитаты: «на проверку», в кэш не ложится.
NO_QUOTE = Said(
    "kurs-sample.example.test",
    "Курсы испанского языка в мини-группах",
    facts("no", why="похоже на агрегатор"),
)
#: Модель не ответила.
SILENT = Said("sessiz.example.test", "Türkçe kursları ve özel dersler", 503)


async def judged(
    session: AsyncSession, *said: Said, mode: JudgeMode = JudgeMode.SHADOW
) -> tuple[SalesSegmentRunModel, dict[str, Candidate]]:
    """Прогон гипотезы, его сайты из выдачи и проход судьи с подделкой модели."""
    owner = await hypothesis(session)
    run = await make_run(session, owner, mode=mode)
    made: dict[str, Candidate] = {}
    for site in said:
        domain = DomainModel(host=site.host)
        session.add(domain)
        await session.flush()
        made[site.host] = Candidate(
            run_id=run.id,
            hypothesis_id=owner.id,
            domain_id=domain.id,
            page_title=site.title,
            lane=Lane.PENDING,
        )
        session.add(made[site.host])
    await session.commit()
    await judge_run(
        session,
        run_id=run.id,
        segment=owner.description or "",
        mode=mode,
        client=_client(Model({site.host: site.answer for site in said})),
        home=None,
    )
    for candidate in made.values():
        await session.refresh(candidate)
    return run, made


def _client(model: Model) -> segment_judge.SegmentClient:
    return segment_judge.SegmentClient(
        httpx.AsyncClient(transport=httpx.MockTransport(model)),
        model="gpt-5-mini",
        api_key=MODEL_KEY,
    )


def landed(candidate: Candidate) -> dict[str, Any]:
    return {name: getattr(candidate, name) for name in FIELDS}


async def decided(session: AsyncSession, candidate: Candidate, *, accept: bool) -> None:
    await segment_report.decide(session, candidate.id, accept=accept, author=SELLER)
    await session.commit()
    assert (candidate.decided_by, candidate.decided_by_user) == (Decider.HUMAN, SELLER)


async def undone(session: AsyncSession, candidate: Candidate) -> su.Undone:
    found = await su.undo(session, candidate.id)
    assert found is not None
    await session.commit()
    return found


# --- куда и с какими словами ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("said", "mode", "lane", "words"),
    [
        (WORKSHOPS, JudgeMode.SHADOW, Lane.WOULD_REJECT, words(INTERMEDIARY, WORKSHOPS)),
        (PORTAL, JudgeMode.SHADOW, Lane.WOULD_REJECT, words(INTERMEDIARY, PORTAL)),
        (MAYBE_SCHOOL, JudgeMode.ENFORCE, Lane.REVIEW, words(UNCLEAR, MAYBE_SCHOOL)),
        (
            EMPTY,
            JudgeMode.SHADOW,
            Lane.REVIEW,
            "выдача и главная не дали текста — судить не по чему",
        ),
    ],
    ids=["shadow-cut-latin", "shadow-cut-japanese", "unsure-arabic", "rule-empty-cyrillic"],
)
@pytest.mark.parametrize("accept", [True, False], ids=["accept", "reject"])
async def test_an_undone_decision_puts_the_site_where_the_judge_put_it_with_its_words(
    session: AsyncSession, said: Said, mode: JudgeMode, lane: Lane, words: str, accept: bool
) -> None:
    _, made = await judged(session, said, mode=mode)
    site = made[said.host]
    before = landed(site)
    assert (site.lane, site.reason) == (lane, words)
    await decided(session, site, accept=accept)
    assert site.reason == ("принял человек" if accept else "отказал человек")

    found = await undone(session, site)

    assert (found.was, found.repeated) == (Lane.ACCEPT if accept else Lane.REJECT, False)
    await session.refresh(site)
    assert landed(site) == before
    assert (site.decided_at, site.decided_by_user) == (None, None)
    assert site.decided_by == (Decider.RULE if said is EMPTY else Decider.MODEL)


async def test_the_lane_after_an_undo_is_the_judges_rule_not_a_hardwired_review(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Куда класть отказ модели, решает одно место (`segment_judge.effective`): судья завёл для
    отказов в наблюдении свою полосу («судья отказал бы») — отмена пошла туда же без правки
    отмены; заведёт другую — пойдёт и туда."""
    _, made = await judged(session, WORKSHOPS)
    site = made[WORKSHOPS.host]
    await decided(session, site, accept=True)

    def elsewhere(verdict: segment_judge.SegmentVerdict, mode: JudgeMode) -> tuple[Lane, str]:
        return Lane.REJECT, f"в другой полосе: {verdict.reason}"

    monkeypatch.setattr(segment_window, "effective", elsewhere)
    await undone(session, site)

    await session.refresh(site)
    assert (site.lane, site.reason, site.decided_by) == (
        Lane.REJECT,
        f"в другой полосе: {words(INTERMEDIARY, WORKSHOPS)}",
        Decider.MODEL,
    )


async def test_words_missing_from_the_cache_are_named_and_a_silent_model_is_asked_again(
    session: AsyncSession,
) -> None:
    run, made = await judged(session, NO_QUOTE, SILENT)
    quiet, silent = made[NO_QUOTE.host], made[SILENT.host]
    assert (quiet.reason, quiet.verdict) == (segment_judge.NO_QUOTE, Lane.REVIEW)
    assert (silent.unanswered, silent.verdict) == (True, None)
    for site in (quiet, silent):
        await decided(session, site, accept=False)
        await undone(session, site)
        await session.refresh(site)

    # Модель не дала цитаты — вердикта в кэше нет: слова «не сохранились», решала модель.
    assert (quiet.lane, quiet.reason, quiet.decided_by) == (Lane.REVIEW, su.LOST, Decider.MODEL)
    # Модель не ответила — сайт снова ждёт судью: следующий проход его судит.
    assert (silent.lane, silent.reason, silent.decided_by) == (Lane.REVIEW, su.SILENT, None)
    said = facts("yes", "Türkçe kursları ve özel", "kendi dil kursları")
    model = Model({SILENT.host: said})
    await judge_run(
        session, run_id=run.id, segment="курсы", mode=JudgeMode.SHADOW, client=_client(model),
        home=None,
    )  # fmt: skip
    await session.refresh(silent)
    assert (model.asked, silent.lane, silent.unanswered) == ([SILENT.host], Lane.ACCEPT, False)


async def test_a_site_the_judge_did_not_see_goes_back_with_the_words_of_the_judge_off(
    session: AsyncSession,
) -> None:
    """Источник вердикта любой — и его нет вовсе: судья был выключен. Отмена кладёт сайт туда же, со
    словами выключенного судьи, а «кто решил» снова никто."""
    _, made = await judged(
        session,
        Said("kursy-bez-sudi.example.test", "Kursy hiszpańskiego w małych grupach"),
        mode=JudgeMode.OFF,
    )
    site = made["kursy-bez-sudi.example.test"]
    assert (site.lane, site.reason, site.verdict) == (Lane.REVIEW, segment_judge.JUDGE_OFF, None)
    before = landed(site)
    await decided(session, site, accept=True)

    await undone(session, site)

    await session.refresh(site)
    assert landed(site) == before
    assert (site.decided_by, site.decided_at, site.decided_by_user) == (None, None, None)


# --- кому отмена отказывает ----------------------------------------------------------------------


async def test_only_a_human_decision_is_undone_and_the_refusals_are_in_words(
    session: AsyncSession,
) -> None:
    _, made = await judged(
        session, CLINIC, Said("youtube.com", "Видео о ветклиниках"), mode=JudgeMode.ENFORCE
    )
    owner = await session.get(SalesHypothesisModel, made[CLINIC.host].hypothesis_id)
    assert owner is not None
    run = await make_run(session, owner)
    domain = DomainModel(host="ozhidanie.example.test")
    session.add(domain)
    await session.flush()
    waiting = Candidate(
        run_id=run.id, hypothesis_id=owner.id, domain_id=domain.id, lane=Lane.PENDING
    )
    session.add(waiting)
    await session.commit()

    refused = {}
    sites = {"model": made[CLINIC.host], "rule": made["youtube.com"], "pending": waiting}
    for name, site in sites.items():
        before = landed(site)
        with pytest.raises(su.UndoRefusedError) as caught:
            await su.undo(session, site.id)
        refused[name] = str(caught.value)
        assert landed(site) == before

    assert refused == {
        "model": su.NOT_HUMAN[Decider.MODEL],
        "rule": su.NOT_HUMAN[Decider.RULE],
        "pending": su.NOT_JUDGED,
    }
    assert await su.undo(session, 987654) is None


async def test_a_repeated_undo_is_not_refused_and_changes_nothing(session: AsyncSession) -> None:
    _, made = await judged(session, PORTAL)
    site = made[PORTAL.host]
    await decided(session, site, accept=False)
    await undone(session, site)
    after = landed(site)

    again = await undone(session, site)

    assert (again.repeated, again.was) == (True, None)
    await session.refresh(site)
    assert landed(site) == after


async def test_a_made_lead_without_a_letter_is_taken_off_with_the_decision(
    session: AsyncSession,
) -> None:
    """Тест продаж 21.10: лид заводится почти сразу после «Принять», и отказ «лид уже заведён» не
    давал отменить «Принять» вовсе. Письма лиду нет — отмена снимает его вместе с решением:
    «отклонён» с причиной «решение отменено», ссылка сайта гаснет."""
    _, made = await judged(session, WORKSHOPS)
    site = made[WORKSHOPS.host]
    before = landed(site)
    await decided(session, site, accept=True)
    lead = SalesLeadModel(
        hypothesis_id=site.hypothesis_id,
        domain_id=site.domain_id,
        email="info@moebel-verzeichnis.example.test",
        source=LeadSource.COLLECTED,
        status=LeadStatus.READY,
    )
    session.add(lead)
    await session.flush()
    site.lead_id = lead.id
    await session.commit()
    assert su.undoable(site)

    found = await undone(session, site)

    await session.refresh(site)
    await session.refresh(lead)
    assert landed(site) == before
    assert site.lead_id is None
    assert (lead.status, lead.rejection_reason, lead.cleaning_note) == (
        LeadStatus.REJECTED,
        RejectionReason.UNDONE.value,
        su.UNDONE_NOTE,
    )
    assert found.lead == su.LeadOff(lead.id, lead.email, letter_off=False, was="готов")


# --- путь целиком --------------------------------------------------------------------------------


async def test_an_undone_accept_is_not_taken_into_leads_by_the_resumed_run(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Принятый человеком сайт с адресом на главной; решение отменено до «Найти адреса принятым»:
    продолжение прогона не ищет ему адрес и не заводит лида, судья его не пересуживает."""
    mail = f"editor@{NEWS}"
    found = Sites({NEWS: f'<html><body><a href="mailto:{mail}">{mail}</a></body></html>'})
    monkeypatch.setattr(
        "backend.features.contacts.search.guarded_client",
        lambda **_kw: httpx.AsyncClient(transport=httpx.MockTransport(found)),
    )
    owner = await hypothesis(session)
    run = await make_run(session, owner, mode=JudgeMode.SHADOW)
    hits = serp(SHOP, NEWS)
    await go(session, run, hits, Model(ANSWERS))
    news = await session.scalar(
        select(Candidate).join(DomainModel, DomainModel.id == Candidate.domain_id)
        .where(DomainModel.host == NEWS)
    )  # fmt: skip
    assert news is not None
    assert (news.lane, news.reason) == (Lane.WOULD_REJECT, NEWS_WORDS)
    await decided(session, news, accept=True)
    await undone(session, news)

    model = Model(ANSWERS)
    await go(session, run, hits, model)

    assert model.asked == []
    await session.refresh(news)
    assert (news.lane, news.reason, news.lead_id) == (
        Lane.WOULD_REJECT,
        NEWS_WORDS,
        None,
    )
    assert not [hit for hit in found.hits if hit.startswith(NEWS)]


# --- гонка с заведением лида: на настоящих фиксациях ------------------------------------------------


async def _accepted_with_address(factory: async_sessionmaker[AsyncSession]) -> tuple[int, int]:
    """Сайт, принятый человеком, и живой адрес его домена — зафиксированы. Номера сайта и прогона."""
    async with factory() as session:
        run, made = await judged(session, WORKSHOPS)
        site = made[WORKSHOPS.host]
        await decided(session, site, accept=True)
        session.add(
            ContactModel(
                domain_id=site.domain_id,
                email="werkstatt@moebel-verzeichnis.example.test",
                source=ContactSource.PAGE,
            )
        )
        await session.commit()
        return site.id, run.id


async def _making(factory: async_sessionmaker[AsyncSession], run_id: int) -> dict[str, Any]:
    async with factory() as session:
        run = await session.get(SalesSegmentRunModel, run_id)
        assert run is not None
        made = await segment_leads.make_leads(session, run, FixtureVerifier())
        await session.commit()
        return made


async def _state(
    factory: async_sessionmaker[AsyncSession], site_id: int
) -> tuple[str, int | None, int]:
    async with factory() as session:
        site = await session.get(Candidate, site_id)
        assert site is not None
        leads = await session.scalar(select(func.count()).select_from(SalesLeadModel))
        return site.lane, site.lead_id, int(leads or 0)


async def _paused_after(
    monkeypatch: pytest.MonkeyPatch, name: str, module: Any = segment_leads
) -> tuple[asyncio.Event, asyncio.Event]:
    """Функция модуля доходит до конца и ждёт отмашки: (дошла, отмашка)."""
    reached, release = asyncio.Event(), asyncio.Event()
    original: Callable[..., Awaitable[Any]] = getattr(module, name)

    async def paused(*args: Any, **kwargs: Any) -> Any:
        found = await original(*args, **kwargs)
        reached.set()
        await asyncio.wait_for(release.wait(), MEETING)
        return found

    monkeypatch.setattr(module, name, paused)
    return reached, release


async def test_an_undo_in_the_middle_of_lead_making_waits_for_it_and_takes_the_lead_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Заведение прочло сайт принятым и стоит до записи лида; отмена приходит сейчас. Старый проход
    строку не держал: отмена проходила, а лид потом ложился на сайт «на проверке». Теперь отмена
    ждёт конца заведения, видит лида и снимает его вместе с решением: письма ему ещё нет."""
    async with committed_sessions() as factory:
        site_id, run_id = await _accepted_with_address(factory)
        selected, release = await _paused_after(monkeypatch, "_ready")

        async def undoing() -> su.Undone | None:
            await asyncio.wait_for(selected.wait(), MEETING)
            async with factory() as session:
                task = asyncio.ensure_future(su.undo(session, site_id))
                await asyncio.sleep(0.3)  # отмене — время упереться в замок
                release.set()
                found = await asyncio.wait_for(task, MEETING)
                await session.commit()
                return found

        made, found = await asyncio.gather(
            _making(factory, run_id), undoing(), return_exceptions=True
        )

        assert isinstance(made, dict), made
        assert made["created"] == 1
        assert isinstance(found, su.Undone), found
        assert found.lead is not None
        assert not found.lead.letter_off
        lane, lead_id, leads = await _state(factory, site_id)
        assert (lane, lead_id, leads) == (Lane.WOULD_REJECT, None, 1)
        async with factory() as session:
            lead = await session.get(SalesLeadModel, found.lead.lead_id)
            assert lead is not None
            assert (lead.status, lead.rejection_reason) == (
                LeadStatus.REJECTED,
                RejectionReason.UNDONE.value,
            )


async def test_lead_making_in_the_middle_of_an_undo_waits_for_it_and_makes_no_lead() -> None:
    """Отмена взяла строку и ещё не зафиксирована; проход заведения лидов приходит сейчас. Старый
    проход читал сайт принятым мимо замка и записывал лида на сайт, решение которого отменено."""
    async with committed_sessions() as factory:
        site_id, run_id = await _accepted_with_address(factory)
        async with factory() as undoing:
            found = await su.undo(undoing, site_id)
            assert found is not None
            assert not found.repeated
            making = asyncio.ensure_future(_making(factory, run_id))
            await asyncio.sleep(0.3)  # заведению — время упереться в замок
            await undoing.commit()
        made = await asyncio.wait_for(making, MEETING)

        assert made["created"] == 0
        lane, lead_id, leads = await _state(factory, site_id)
        assert (lane, lead_id, leads) == (Lane.WOULD_REJECT, None, 0)


async def test_a_row_held_longer_than_the_limit_is_refused_in_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Строку держат дольше предела (заведение лидов чистит адреса): отмена не висит, а отказывает
    словами; транзакция вызывающего жива — отказ в точке сохранения."""
    monkeypatch.setattr(su, "LOCK_WAIT", "SET LOCAL lock_timeout = '200ms'")
    async with committed_sessions() as factory:
        site_id, _ = await _accepted_with_address(factory)
        async with factory() as holding:
            await holding.execute(
                select(Candidate.id).where(Candidate.id == site_id).with_for_update()
            )
            async with factory() as session:
                with pytest.raises(su.UndoBusyError) as refused:
                    await asyncio.wait_for(su.undo(session, site_id), MEETING)
                assert await session.scalar(select(func.count()).select_from(Candidate)) == 1
            await holding.rollback()

        assert str(refused.value) == su.BUSY
        lane, _, _ = await _state(factory, site_id)
        assert lane == Lane.ACCEPT
