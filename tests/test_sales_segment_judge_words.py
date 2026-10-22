"""Слова, время и решавший судьи у сайта сбора — свои колонки (`judge_reason`, `judged_at`,
`judged_by`): решение человека и его отмена их не теряют.

Тест продаж 22.10: сайт «на проверке» после «Отменить решение» — «Кто решил: модель» без даты и
«причина не сохранилась»; сайт, отсечённый человеком, — «Судья: причина не сохранилась», хотя до
решения у него было «цитаты нет в тексте сайта — …». Решение человека затирает причину, время и
«кто решил» своими, а слова судьи читались только из кэша вердиктов — вердикт без цитаты в тексте
туда не ложится; решавшего отмена угадывала. Теперь слова, время и решавшего пишет только судья —
проход и пересуд, — решение человека их не трогает, а «Судья: …» и отмена читают их первыми; кэш и
догадка — у строк, судимых до колонок; слов нет нигде — «причина не сохранилась», как было. На
настоящей базе, судит настоящий проход с подделкой модели:

- каждое место, где ложится вердикт, пишет слова, время и решавшего: проход (модель, правило по
  имени и по тексту, стоп-лист, модель не ответила) и пересуд (модель, стоп-лист); решение
  человека — и второе, после отмены, — их не меняет;
- вердикт без цитаты в тексте (в кэше его нет): после решения — «Судья: <слова сверки>», после
  отмены — те же слова и дата судьи; путём маршрутов — тем ответом сервера, что рисует экран;
- строка, судимая до колонок: решение переносит в них её причину, время и решавшего, отмена их
  возвращает — и вердикт языка, который сверкой уже не узнать; слова человека судьёй не становятся;
  решено до колонок — слова из кэша, нет и там — «причина не сохранилась»;
- вердикт языка после смены правила языка — снова «судья отказал бы», «язык» и слова, какими их
  сказал судья, а не «отсечён» правилом; и наоборот — решение правила не становится вердиктом
  языка, если новое правило языка узнало бы в тексте своё: решавший — из колонки, а не догадкой;
- пересуд не трогает решённый человеком сайт — ни решённый до него, ни посреди: «Судья: …» и
  отмена — слова и время вердикта, что лежит на сайте, а не свежая строка кэша.

Ниши и языки разные: пекарня и бухгалтерия (польский), стоматология (японский, без пробелов), юрист
и языковая школа (арабский, справа налево), справочник мастерских (немецкий), ветклиника
(польский), сантехник (испанский), йога (хинди), школы танцев и серфинга (португальский), курсы
(турецкий), велосипедные мастерские (украинский и русский — сверка языка). Слова сверки — словами
судьи (`segment_judge.made_up` с цитатой подделки), не копией: цитата не нашлась и во втором вопросе
судьи — подделка отвечает тем же, вызовов модели два. Домены выдуманы (`*.example.test`).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from backend.config.judge import JudgeMode
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.ops import UsageRecordModel
from backend.features.sales import segment_judge as sj
from backend.features.sales import segment_rejudge as sr
from backend.features.sales import segment_report, segment_sites, segment_window
from backend.features.sales import segment_undo as su
from backend.features.sales.models import SalesHypothesisModel, SalesStoplistModel
from backend.features.sales.segment_judging import STOPLIST_OWN
from backend.features.sales.segment_models import (
    Decider,
    Lane,
    SalesSegmentCandidateModel,
    SalesSegmentRunModel,
    SalesSegmentVerdictModel,
)
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.segment_facts_fakes import facts
from tests.test_sales_segment_rejudge import asked, deps, describe
from tests.test_sales_segment_run import Model, hypothesis, make_run
from tests.test_sales_segment_undo import (
    ANYWHERE,
    CLINIC,
    EMPTY,
    INTERMEDIARY,
    MAYBE_SCHOOL,
    SILENT,
    UNCLEAR,
    WORKSHOPS,
    Said,
    judged,
    undone,
    words,
)
from tests.test_sales_segment_would_reject import LANGUAGE_WORDS, _language_run

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]
Candidate = SalesSegmentCandidateModel
Verdict = SalesSegmentVerdictModel

SELLER = "seller@ours.example.test"
CANDIDATES = "/api/sales/segment/candidates"
RUNS = "/api/sales/segment/runs"
#: Проход судьи, решение человека и пересуд — каждый в своё время: чьё время где, видно по часам.
JUDGED = datetime(2026, 10, 22, 9, 15, tzinfo=UTC)
DECIDED = datetime(2026, 10, 22, 10, 40, tzinfo=UTC)
REJUDGED = datetime(2026, 10, 22, 11, 5, tzinfo=UTC)
#: Судили до колонок судьи — время прохода, как его оставила прежняя запись.
BEFORE = datetime(2026, 10, 20, 16, 2, tzinfo=UTC)
HUMAN_WORDS = {True: "принял человек", False: "отказал человек"}


def made_up(said: Said) -> str:
    """Слова сверки у сайта, чья цитата не нашлась ни в первом, ни во втором ответе (подделка
    отвечает тем же): словами судьи с цитатой подделки (`segment_judge.made_up`)."""
    assert isinstance(said.answer, dict)
    return sj.made_up(said.answer["quote"])


#: Пекарня (польский): цитата модели не из текста сайта — «цитаты нет», в кэш не ложится.
BAKERY = Said(
    "piekarnia-zakwas.example.test",
    "Piekarnia rzemieślnicza — chleb na zakwasie i drożdżówki",
    facts("yes", "najlepsze rogaliki w całej Europie", "własna piekarnia"),
)
#: Стоматология (японский, без пробелов): цитаты в тексте нет.
DENTIST = Said(
    "ekimae-shika.example.test",
    "駅前の歯科医院で痛みの少ない治療を",
    facts("yes", "日本一の矯正専門クリニック", "自社の歯科医院"),
)
#: Юрист (арабский, справа налево): цитаты в тексте нет.
LAWYER = Said(
    "mohami-usra.example.test",
    "مكتب محاماة متخصص في قضايا الأسرة والميراث",
    facts("yes", "أفضل محامي ضرائب في المنطقة كلها", "مكتب محاماة خاص"),
)
#: Платформа — «кому не пишем» по имени, до модели.
PLATFORM = Said("youtube.com", "Видео о пекарнях и хлебе на закваске")
#: Домен в стоп-листе продаж — отсечён до судьи.
CLOSED = Said(
    "piekarnia-osiedlowa.example.test",
    "Piekarnia osiedlowa — pieczywo codziennie od szóstej",
    facts("yes", "Piekarnia osiedlowa — pieczywo codziennie", "piekarnia"),
)
#: Справочник мастерских после правки описания: модель говорит «своё дело» — другие слова судьи.
WORKSHOPS_YES = Said(
    WORKSHOPS.host,
    WORKSHOPS.title,
    facts("yes", "Alle Möbeltischler in Ihrer Stadt", "verzeichnet Tischler und baut selbst"),
)
#: Языковая школа под новым описанием: тот же вердикт и та же цитата, другие слова — строка кэша
#: нового описания совпадает с вердиктом на сайте ключом (вердикт, цитата, кто решил).
SCHOOL_AGAIN = Said(
    MAYBE_SCHOOL.host,
    MAYBE_SCHOOL.title,
    facts("unsure", "تعلم اللغة الإنجليزية مع دروسنا", "ما زال غير واضح: مدرسة أم مدونة معلم"),
)
#: Описание после правки — пересуд спрашивает модель заново.
NEW_SEGMENT = "Собственные мастерские и школы: сами делают то, что продают, у себя."


@pytest.fixture
async def headers(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user(SELLER)
    return bearer(await sign_in(SELLER))


async def _decide(session: AsyncSession, site: Candidate, *, accept: bool) -> None:
    await segment_report.decide(session, site.id, accept=accept, author=SELLER, now=DECIDED)
    await session.commit()


async def _judge_says(session: AsyncSession, site: Candidate) -> str | None:
    """«Судья: …» строки списка сайтов — тем правилом, что видит экран (`site_row`)."""
    return (await segment_sites.site_row(session, site)).judge


async def _old_row(
    session: AsyncSession, run: SalesSegmentRunModel, host: str, **fields: Any
) -> Candidate:
    """Сайт, как его оставил проход до колонок судьи: слова — в причине, время — в «когда решено»,
    решавший — в «кто решил», колонки судьи пусты."""
    domain = DomainModel(host=host)
    session.add(domain)
    await session.flush()
    columns: dict[str, Any] = {
        "page_title": host,
        "model": "gpt-5-mini",
        "decided_by": Decider.MODEL,
        "decided_at": BEFORE,
    } | fields
    made = Candidate(run_id=run.id, hypothesis_id=run.hypothesis_id, domain_id=domain.id, **columns)
    session.add(made)
    await session.commit()
    assert (made.judge_reason, made.judged_at, made.judged_by) == (None, None, None)
    return made


async def _shadow_run(session: AsyncSession) -> SalesSegmentRunModel:
    return await make_run(session, await hypothesis(session), mode=JudgeMode.SHADOW)


# --- пишет только судья ---------------------------------------------------------------------------


async def test_every_verdict_of_the_pass_keeps_the_judges_words_and_time_and_a_human_changes_none(
    session: AsyncSession,
) -> None:
    session.add(SalesStoplistModel(host=CLOSED.host))
    await session.commit()
    _, made = await judged(
        session, BAKERY, WORKSHOPS, MAYBE_SCHOOL, EMPTY, SILENT, PLATFORM, CLOSED, now=JUDGED
    )

    # Судья пишет свои колонки тем же, что кладёт в причину, время и «кто решил»: у каждого вида
    # вердикта.
    for host, site in made.items():
        judge = (site.judge_reason, site.judged_at, site.judged_by)
        assert judge == (site.reason, site.decided_at, site.decided_by), host
    # Время — его прохода; модель не ответила — решения нет, и времени судьи тоже.
    times = {host: site.judged_at for host, site in made.items()}
    assert (times.pop(SILENT.host), set(times.values())) == (None, {JUDGED})
    platform = sj.by_host(PLATFORM.host)
    assert platform is not None
    said_by = [
        (made[said.host].judge_reason, made[said.host].judged_by)
        for said in (BAKERY, CLOSED, PLATFORM, EMPTY, SILENT)
    ]
    assert said_by[:3] == [
        (made_up(BAKERY), Decider.MODEL),
        (STOPLIST_OWN, Decider.RULE),
        (platform.reason, Decider.RULE),
    ]
    assert [by for _, by in said_by[3:]] == [Decider.RULE, None]
    waiting = [made[said.host] for said in (BAKERY, WORKSHOPS, MAYBE_SCHOOL, EMPTY, SILENT)]
    kept = {site.id: (site.judge_reason, site.judged_at, site.judged_by) for site in waiting}

    for number, site in enumerate(waiting):
        await _decide(session, site, accept=number % 2 == 0)

    assert {
        site.id: (site.judge_reason, site.judged_at, site.judged_by) for site in waiting
    } == kept
    assert {(site.decided_by, site.decided_at) for site in waiting} == {(Decider.HUMAN, DECIDED)}
    assert {site.reason for site in waiting} == set(HUMAN_WORDS.values())
    # Второе решение после отмены, когда причина уже не судьи («модель не ответила — спросит
    # снова»): колонки судьи — те же, слова отмены в них не переходят.
    silent = made[SILENT.host]
    await undone(session, silent)
    assert silent.reason == su.SILENT
    await _decide(session, silent, accept=True)
    assert (silent.judge_reason, silent.judged_at, silent.judged_by) == kept[silent.id]


async def test_the_rejudge_writes_the_judges_words_and_time_of_its_verdicts(
    session: AsyncSession,
) -> None:
    run, made = await judged(session, WORKSHOPS, MAYBE_SCHOOL, CLINIC, now=JUDGED)
    owner = await session.get(SalesHypothesisModel, run.hypothesis_id)
    assert owner is not None
    session.add(SalesStoplistModel(host=CLINIC.host))
    await describe(session, owner, NEW_SEGMENT)
    await asked(session, run)
    model = Model({WORKSHOPS.host: WORKSHOPS_YES.answer, MAYBE_SCHOOL.host: 503})

    await sr.rejudge(session, run.id, deps(model), now=REJUDGED)

    for site in made.values():
        await session.refresh(site)
    workshops, school, clinic = (made[said.host] for said in (WORKSHOPS, MAYBE_SCHOOL, CLINIC))
    assert (workshops.lane, workshops.reason) == (Lane.ACCEPT, words(ANYWHERE, WORKSHOPS_YES))
    assert (workshops.judge_reason, workshops.judged_at, workshops.judged_by) == (
        workshops.reason,
        REJUDGED,
        Decider.MODEL,
    )
    assert (clinic.lane, clinic.judge_reason, clinic.judged_at, clinic.judged_by) == (
        Lane.REJECT,
        STOPLIST_OWN,
        REJUDGED,
        Decider.RULE,
    )
    # Модель не ответила пересуду: вердикта нет — слова сбоя, ни времени, ни решавшего.
    assert school.unanswered
    assert (school.judge_reason, school.judged_at, school.judged_by) == (school.reason, None, None)


# --- вердикт без цитаты в тексте: на экране -------------------------------------------------------


@pytest.mark.parametrize(
    ("said", "accept"),
    [(BAKERY, False), (DENTIST, True), (LAWYER, False)],
    ids=["pl-bakery-reject", "ja-dentist-accept", "ar-lawyer-reject"],
)
async def test_a_verdict_without_its_quote_in_the_text_keeps_its_words_and_date_on_the_screen(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    said: Said,
    accept: bool,
) -> None:
    """Находка теста продаж 22.10: «Судья: причина не сохранилась» у решения человека и «модель» без
    даты и слов после отмены — у сайта, чей вердикт («цитаты нет в тексте сайта») не ложится в кэш.
    Ответы маршрутов — те, что рисует экран: «Судья: …» — `judge_reason`, дата под «Кто решил» —
    `decided_at`."""
    run, made = await judged(session, said, now=JUDGED)
    site = made[said.host]
    words_of = made_up(said)
    assert (site.lane, site.reason, site.decided_by) == (Lane.REVIEW, words_of, Decider.MODEL)
    # Второй шанс цитате: цитата не нашлась и во втором ответе — вызовов модели два, оба в расходе.
    spent = select(func.sum(UsageRecordModel.units)).where(
        UsageRecordModel.operation == sj.OPERATION
    )
    assert await session.scalar(spent) == 2 * Model({}).tokens
    cached = select(func.count()).select_from(Verdict).where(Verdict.domain_id == site.domain_id)
    assert await session.scalar(cached) == 0
    url = f"{CANDIDATES}/{site.id}/decision"

    decision = await client.post(url, json={"accept": accept}, headers=headers)
    listed = await client.get(f"{RUNS}/{run.id}/sites", headers=headers)
    back = await client.delete(url, headers=headers)

    assert decision.status_code == 200, decision.text
    row = decision.json()
    assert (row["reason"], row["judge_reason"]) == (HUMAN_WORDS[accept], words_of)
    assert [found["judge_reason"] for found in listed.json()["rows"]] == [words_of]
    assert back.status_code == 200, back.text
    row = back.json()
    assert (row["lane"], row["reason"], row["decided_by"], row["decided_at"]) == (
        "review",
        words_of,
        "model",
        "2026-10-22T09:15:00Z",
    )
    assert (row["decided_by_user"], row["judge_reason"]) == (None, None)


# --- строки, судимые до колонок судьи -----------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "lane", "verdict", "said", "by"),
    [
        ("fontanero-urgente.example.test", Lane.REVIEW, Lane.REVIEW, sj.MADE_UP, Decider.MODEL),
        (
            "yoga-kaksha.example.test",
            Lane.WOULD_REJECT,
            Lane.REJECT,
            f"{INTERMEDIARY}. योग कक्षाओं की सूची, खुद कक्षाएँ नहीं चलाता",
            Decider.MODEL,
        ),
        (
            "maisternia-velo.example.test",
            Lane.WOULD_REJECT,
            Lane.REJECT,
            LANGUAGE_WORDS,
            Decider.LANGUAGE,
        ),
    ],
    ids=["es-review-made-up", "hi-would-reject", "uk-language-would-reject"],
)
async def test_a_site_judged_before_the_columns_keeps_its_words_time_and_judge_through_a_decision(
    session: AsyncSession, host: str, lane: Lane, verdict: Lane, said: str, by: Decider
) -> None:
    """Сайты, судимые до колонок и ждущие решения: судья положил слова в причину, время — в «когда
    решено», себя — в «кто решил». Решение человека сначала переносит их в колонки судьи, потом
    пишет своё; отмена возвращает их. Вердикт языка у такой строки сверкой не узнать (текста нет или
    правило языка поменялось) — догадка по модели сочла бы его правилом и отсекла бы сайт; с
    перенесённым решавшим он снова «судья отказал бы» от языка."""
    model = None if by is Decider.LANGUAGE else "gpt-5-mini"
    old = await _old_row(
        session,
        await _shadow_run(session),
        host,
        lane=lane,
        verdict=verdict,
        reason=said,
        decided_by=by,
        model=model,
    )

    await _decide(session, old, accept=lane is Lane.WOULD_REJECT)

    assert (old.judge_reason, old.judged_at, old.judged_by, old.decided_at) == (
        said,
        BEFORE,
        by,
        DECIDED,
    )
    assert await _judge_says(session, old) == said
    await undone(session, old)
    await session.refresh(old)
    assert (old.lane, old.reason, old.decided_by, old.decided_at) == (lane, said, by, BEFORE)


async def test_the_words_of_a_human_are_not_taken_for_the_words_of_the_judge(
    session: AsyncSession,
) -> None:
    """Сайт ждёт человека, а последним решал человек: его «принял человек» в колонки судьи не
    переносится — «Судья: …» не назовёт решение человека словами судьи."""
    odd = await _old_row(
        session,
        await _shadow_run(session),
        "aulas-de-surf.example.test",
        lane=Lane.REVIEW,
        verdict=Lane.REVIEW,
        reason=HUMAN_WORDS[True],
        decided_by=Decider.HUMAN,
        decided_by_user=SELLER,
    )

    await _decide(session, odd, accept=False)

    assert (odd.judge_reason, odd.judged_at, odd.judged_by) == (None, None, None)
    assert await _judge_says(session, odd) == su.LOST


@pytest.mark.parametrize("cached", [True, False], ids=["words-in-the-cache", "words-nowhere"])
async def test_a_site_decided_before_the_columns_reads_the_cache_or_says_the_words_are_lost(
    session: AsyncSession, cached: bool
) -> None:
    """Решено до колонок: причину человек уже затёр. Слова — из кэша вердиктов, если вердикт туда
    лёг; нет и там — «причина не сохранилась», как было: восстанавливать не из чего. Отмена колонок
    судьи не пишет — их пишет только судья."""
    run = await _shadow_run(session)
    quote = "Escola de dança — aulas de salsa"
    said = f"{INTERMEDIARY}. Diretório de escolas de dança da cidade"
    old = await _old_row(
        session,
        run,
        "escola-de-danca.example.test",
        lane=Lane.REJECT,
        verdict=Lane.REJECT,
        quote=quote,
        reason=HUMAN_WORDS[False],
        decided_by=Decider.HUMAN,
        decided_at=DECIDED,
        decided_by_user=SELLER,
    )
    if cached:
        owner = await session.get(SalesHypothesisModel, run.hypothesis_id)
        assert owner is not None
        session.add(
            Verdict(
                domain_id=old.domain_id,
                hypothesis_id=owner.id,
                prompt_version=sj.PROMPT_VERSION,
                segment_digest=sj.segment_digest(owner.description or ""),
                verdict=Lane.REJECT,
                quote=quote,
                reason=said,
                model="gpt-5-mini",
                decided_by=Decider.MODEL,
            )
        )
        await session.commit()
    kept = said if cached else su.LOST

    assert await _judge_says(session, old) == kept
    await undone(session, old)
    await session.refresh(old)
    assert (old.lane, old.reason, old.decided_by, old.decided_at) == (
        Lane.WOULD_REJECT,
        kept,
        Decider.MODEL,
        None,
    )
    assert (old.judge_reason, old.judged_at, old.judged_by) == (None, None, None)


# --- вердикт языка ------------------------------------------------------------------------------


async def test_a_language_refusal_comes_back_as_language_after_the_language_rule_changed(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сайту отказала сверка языка («судья отказал бы» в наблюдении), человек его решил, а потом
    правило языка поменялось: по сохранённому тексту сверка больше не отказывает, и слова у неё
    другие. Отмена кладёт сайт туда, куда его положил судья: «судья отказал бы», «Кто решил: язык»,
    слова — какими их сказал судья, время — его. Прежняя догадка (повтор сверки) сочла бы вердикт
    правилом — «отсечён», «Кто решил: правило». Путём маршрутов — тем ответом, что рисует экран."""
    _, uk, _ = await _language_run(session, JudgeMode.SHADOW)
    assert (uk.lane, uk.decided_by, uk.judged_by, uk.judge_reason) == (
        Lane.WOULD_REJECT,
        Decider.LANGUAGE,
        Decider.LANGUAGE,
        LANGUAGE_WORDS,
    )
    then = "сайт на украинском, а письма прогона — на русском"  # слова сверки в день суда
    uk.reason = uk.judge_reason = then
    await session.commit()
    judged_at = uk.judged_at
    url = f"{CANDIDATES}/{uk.id}/decision"
    decision = await client.post(url, json={"accept": True}, headers=headers)
    monkeypatch.setattr(su, "by_language", lambda *_: None)  # правило языка поменялось

    back = await client.delete(url, headers=headers)

    assert decision.status_code == 200, decision.text
    assert decision.json()["judge_reason"] == then
    assert back.status_code == 200, back.text
    row = back.json()
    assert (row["lane"], row["decided_by"], row["reason"], row["verdict"]) == (
        "would_reject",
        "language",
        then,
        "reject",
    )
    assert datetime.fromisoformat(row["decided_at"]) == judged_at


async def test_the_judge_in_its_column_is_not_guessed_again_by_a_changed_language_rule(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Обратная сторона смены правила языка: сайт «на проверку» положило правило (вместо страницы —
    отказ доступа), а новое правило языка по сохранённому тексту назвало бы вердикт своим («не
    понять» — та же полоса). Решавший — из колонки судьи: отмена возвращает правило и его слова, а
    не сверку языка."""
    ruled = "вместо страницы пришёл отказ доступа — судить не по чему"
    owner = await hypothesis(session)
    run = await make_run(session, owner, mode=JudgeMode.SHADOW)
    domain = DomainModel(host="biuro-rachunkowe.example.test")
    session.add(domain)
    await session.flush()
    site = Candidate(
        run_id=run.id,
        hypothesis_id=owner.id,
        domain_id=domain.id,
        site_text="Заголовок в выдаче: Biuro rachunkowe — księgowość dla firm",
        lane=Lane.REVIEW,
        verdict=Lane.REVIEW,
        reason=ruled,
        decided_by=Decider.RULE,
        decided_at=JUDGED,
        judge_reason=ruled,
        judged_at=JUDGED,
        judged_by=Decider.RULE,
    )
    session.add(site)
    await session.commit()
    await _decide(session, site, accept=True)
    guess = sj.SegmentVerdict(Lane.REVIEW, "язык сайта не понять", decided_by=Decider.LANGUAGE)
    monkeypatch.setattr(su, "by_language", lambda *_: guess)  # правило языка поменялось

    assert await _judge_says(session, site) == ruled
    await undone(session, site)

    await session.refresh(site)
    assert (site.lane, site.decided_by, site.reason, site.decided_at) == (
        Lane.REVIEW,
        Decider.RULE,
        ruled,
        JUDGED,
    )


# --- пересуд и решение человека -------------------------------------------------------------------


async def test_a_rejudge_leaves_a_human_decision_with_the_words_and_time_of_the_judge_before_it(
    session: AsyncSession,
) -> None:
    """Пересуд решений человека не пересуживает и модель о них не спрашивает: полоса и причина —
    человека, «Судья: …» — слова последнего вердикта на сайте, и отмена возвращает его со
    временем."""
    run, made = await judged(session, WORKSHOPS, now=JUDGED)
    site = made[WORKSHOPS.host]
    then = words(INTERMEDIARY, WORKSHOPS)
    await _decide(session, site, accept=True)
    owner = await session.get(SalesHypothesisModel, run.hypothesis_id)
    assert owner is not None
    await describe(session, owner, NEW_SEGMENT)
    await asked(session, run)
    model = Model({WORKSHOPS.host: WORKSHOPS_YES.answer})

    outcome = await sr.rejudge(session, run.id, deps(model), now=REJUDGED)

    await session.refresh(site)
    assert (model.asked, outcome["kept_human"]) == ([], 1)
    assert (site.lane, site.reason, site.judge_reason, site.judged_at, site.judged_by) == (
        Lane.ACCEPT,
        HUMAN_WORDS[True],
        then,
        JUDGED,
        Decider.MODEL,
    )
    assert await _judge_says(session, site) == then
    await undone(session, site)
    await session.refresh(site)
    assert (site.lane, site.reason, site.decided_at, site.decided_by) == (
        Lane.WOULD_REJECT,
        then,
        JUDGED,
        Decider.MODEL,
    )


async def test_a_decision_in_the_middle_of_a_rejudge_keeps_the_words_of_the_verdict_on_the_site(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Решение пришло посреди пересуда: модель уже спрошена под новым описанием и её строка — в
    кэше, а полоса на сайт не ложится. «Судья: …» и отмена — слова и время вердикта, что лежит на
    сайте и что видел человек, а не свежая строка кэша с тем же вердиктом и цитатой."""
    run, made = await judged(session, MAYBE_SCHOOL, now=JUDGED)
    site = made[MAYBE_SCHOOL.host]
    then = words(UNCLEAR, MAYBE_SCHOOL)
    assert (site.lane, site.reason) == (Lane.REVIEW, then)
    owner = await session.get(SalesHypothesisModel, run.hypothesis_id)
    assert owner is not None
    await describe(session, owner, NEW_SEGMENT)
    await asked(session, run)
    real = segment_window._one

    async def meanwhile(*args: Any) -> Any:
        await segment_report.decide(session, site.id, accept=False, author=SELLER, now=DECIDED)
        await session.flush()
        return await real(*args)

    monkeypatch.setattr(segment_window, "_one", meanwhile)
    model = Model({MAYBE_SCHOOL.host: SCHOOL_AGAIN.answer})

    outcome = await sr.rejudge(session, run.id, deps(model), now=REJUDGED)

    assert (model.asked, outcome["human"]) == ([MAYBE_SCHOOL.host], 1)
    fresh = await session.scalar(
        select(Verdict.reason).where(
            Verdict.domain_id == site.domain_id,
            Verdict.segment_digest == sj.segment_digest(NEW_SEGMENT),
        )
    )
    assert fresh == words(UNCLEAR, SCHOOL_AGAIN) != then
    await session.refresh(site)
    assert (site.lane, site.judge_reason, site.judged_at, site.judged_by) == (
        Lane.REJECT,
        then,
        JUDGED,
        Decider.MODEL,
    )
    assert await _judge_says(session, site) == then
    await undone(session, site)
    await session.refresh(site)
    assert (site.lane, site.reason, site.decided_at, site.decided_by) == (
        Lane.REVIEW,
        then,
        JUDGED,
        Decider.MODEL,
    )
