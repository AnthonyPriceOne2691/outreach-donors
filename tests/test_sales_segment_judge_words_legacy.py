"""Сайт сбора, который человек решил до колонок судьи: решавший, слова и время — из строки кэша
вердиктов, а не догадкой.

Стенд 22.10 (после колонок судьи и смены правила языка): у сайта, решённого человеком до выкатки,
с вердиктом языка «Судья: …» выходило «причина не сохранилась», а отмена — «на проверке · правило ·
без даты». Колонки судьи у такой строки пусты навсегда, модели у вердикта языка нет; отмена
угадывала: повтор сверки языка по сохранённому тексту — новое правило его больше не узнаёт, — затем
«правило» по отсутствию модели, а ключ кэша с решавшим «правило» не находил строку, где лежат и
слова, и решавший «язык». Теперь у строки без колонки решавшего и без модели строка кэша под её
вердикт (домен, гипотеза, вердикт, цитата; решавший — правило или язык; свежая первой) даёт слова,
решавшего и время. Нет строки — прежняя догадка; сайт с моделью — как был (строку кэша языка он не
берёт).

Правило языка в тестах подменено (`segment_undo.by_language`): «не узнаёт» — как после смены
правила, «узнаёт» — как прежде. Ниши и языки разные: туристическое агентство (казахский латиницей),
пекарня (украинский), фотостудия (вьетнамский), стоматология (японский, без пробелов), курсы
(иврит, справа налево). Домены выдуманы (`*.example.test`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from backend.config.judge import JudgeMode
from backend.features.core.models.domain import DomainModel
from backend.features.sales import segment_judge as sj
from backend.features.sales import segment_sites
from backend.features.sales import segment_undo as su
from backend.features.sales.models import SalesHypothesisModel
from backend.features.sales.segment_models import (
    Decider,
    Lane,
    SalesSegmentCandidateModel,
    SalesSegmentRunModel,
    SalesSegmentVerdictModel,
)
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_segment_run import hypothesis, make_run
from tests.test_sales_segment_undo import undone

Candidate = SalesSegmentCandidateModel
Verdict = SalesSegmentVerdictModel

SELLER = "seller@ours.example.test"
#: Человек решил до выкатки колонок; строка кэша — время, когда судья положил в неё эти слова.
DECIDED = datetime(2026, 10, 21, 15, 40, tzinfo=UTC)
CACHED_AT = datetime(2026, 10, 21, 9, 12, tzinfo=UTC)
#: Вердикт языка «не понять» — «на проверку», и отказ языка — «судья отказал бы» в наблюдении.
UNCLEAR = "язык сайта — не понять по буквам (латиница), письма — русский"
UKRAINIAN = "язык сайта — украинский, письма — русский"
NO_TEXT = "выдача и главная не дали текста — судить не по чему"


async def _run(session: AsyncSession) -> SalesSegmentRunModel:
    run = await make_run(session, await hypothesis(session), mode=JudgeMode.SHADOW)
    run.language = "ru"
    await session.commit()
    return run


async def _decided_before(
    session: AsyncSession, run: SalesSegmentRunModel, host: str, **fields: Any
) -> Candidate:
    """Сайт, который человек решил до колонок судьи: причина, время и «кто решил» — его, колонки
    судьи пусты; вердикт, цитата и модель — судьи."""
    domain = DomainModel(host=host)
    session.add(domain)
    await session.flush()
    columns: dict[str, Any] = {
        "page_title": host,
        "site_text": f"Заголовок в выдаче: {host}",
        "lane": Lane.ACCEPT,
        "reason": "принял человек",
        "decided_by": Decider.HUMAN,
        "decided_at": DECIDED,
        "decided_by_user": SELLER,
    } | fields
    made = Candidate(run_id=run.id, hypothesis_id=run.hypothesis_id, domain_id=domain.id, **columns)
    session.add(made)
    await session.commit()
    assert (made.judge_reason, made.judged_at, made.judged_by) == (None, None, None)
    return made


async def _remembered(
    session: AsyncSession,
    site: Candidate,
    *,
    by: Decider,
    words: str,
    at: datetime = CACHED_AT,
    model: str | None = None,
    digest: str = "",
) -> None:
    """Строка кэша вердиктов под вердикт сайта: так её положил прежний проход."""
    owner = await session.get(SalesHypothesisModel, site.hypothesis_id)
    assert owner is not None
    session.add(
        Verdict(
            domain_id=site.domain_id,
            hypothesis_id=site.hypothesis_id,
            prompt_version=sj.PROMPT_VERSION,
            segment_digest=digest or sj.segment_digest(owner.description or ""),
            verdict=site.verdict,
            quote=site.quote,
            reason=words,
            model=model,
            decided_by=by,
            updated_at=at,
        )
    )
    await session.commit()


def _language_says(found: sj.SegmentVerdict | None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Правило языка по сохранённому тексту: `None` — «не узнаёт» (как после смены правила)."""
    monkeypatch.setattr(su, "by_language", lambda *_: found)


async def _judge_says(session: AsyncSession, site: Candidate) -> str | None:
    return (await segment_sites.site_row(session, site)).judge


def _after(site: Candidate) -> tuple[Any, ...]:
    return (site.lane, site.decided_by, site.reason, site.decided_at, site.judged_by)


@pytest.mark.parametrize(
    ("host", "verdict", "words", "back"),
    [
        ("tur-agenttik.example.test", Lane.REVIEW, UNCLEAR, Lane.REVIEW),
        ("pekarnia-kyiv.example.test", Lane.REJECT, UKRAINIAN, Lane.WOULD_REJECT),
    ],
    ids=["unclear-review", "refusal-would-reject"],
)
async def test_a_language_verdict_decided_before_the_columns_comes_back_from_its_cache_row(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    host: str,
    verdict: Lane,
    words: str,
    back: Lane,
) -> None:
    """Находка стенда 22.10: правило языка сменилось и текст больше не узнаёт — а строка кэша с
    решавшим «язык» есть. «Судья: …» — её слова; отмена — полоса, как её кладёт язык в наблюдении,
    «язык», слова и время строки кэша. Колонки судьи отмена не пишет."""
    site = await _decided_before(session, await _run(session), host, verdict=verdict)
    await _remembered(session, site, by=Decider.LANGUAGE, words=words)
    _language_says(None, monkeypatch)

    assert await _judge_says(session, site) == words
    await undone(session, site)

    await session.refresh(site)
    assert _after(site) == (back, Decider.LANGUAGE, words, CACHED_AT, None)
    assert (site.judge_reason, site.judged_at) == (None, None)


async def test_a_rule_verdict_decided_before_the_columns_comes_back_as_the_rule(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Строка кэша с решавшим «правило» — сайт возвращается от правила, с его словами и временем, а
    не вердиктом языка, даже если правило языка узнало бы в тексте своё."""
    site = await _decided_before(
        session, await _run(session), "foto-studio-hanoi.example.test", verdict=Lane.REVIEW
    )
    await _remembered(session, site, by=Decider.RULE, words=NO_TEXT)
    _language_says(
        sj.SegmentVerdict(Lane.REVIEW, UNCLEAR, decided_by=Decider.LANGUAGE), monkeypatch
    )

    assert await _judge_says(session, site) == NO_TEXT
    await undone(session, site)

    await session.refresh(site)
    assert _after(site) == (Lane.REVIEW, Decider.RULE, NO_TEXT, CACHED_AT, None)


@pytest.mark.parametrize(
    ("fresher", "words"),
    [(Decider.LANGUAGE, UNCLEAR), (Decider.RULE, NO_TEXT)],
    ids=["language-fresher", "rule-fresher"],
)
async def test_the_freshest_cache_row_of_the_rule_or_the_language_decides(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, fresher: Decider, words: str
) -> None:
    """У домена две строки под тот же вердикт и цитату — правила и языка (прогоны под разными
    описаниями): решает свежая, со своими словами и временем."""
    site = await _decided_before(
        session, await _run(session), "kursy-ivrit.example.test", verdict=Lane.REVIEW
    )
    older = CACHED_AT - timedelta(days=2)
    stale = Decider.RULE if fresher is Decider.LANGUAGE else Decider.LANGUAGE
    await _remembered(session, site, by=stale, words="прежние слова", at=older, digest="old0digest")
    await _remembered(session, site, by=fresher, words=words)
    _language_says(None, monkeypatch)

    await undone(session, site)

    await session.refresh(site)
    assert _after(site) == (Lane.REVIEW, fresher, words, CACHED_AT, None)


@pytest.mark.parametrize(
    ("recognised", "after"),
    [
        (True, (Lane.REVIEW, Decider.LANGUAGE, UNCLEAR, None, None)),
        (False, (Lane.REVIEW, Decider.RULE, su.LOST, None, None)),
    ],
    ids=["language-recognises", "nothing-recognises"],
)
async def test_without_a_cache_row_the_old_guess_stays(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    recognised: bool,
    after: tuple[Any, ...],
) -> None:
    """Строки кэша нет (отказ языка до модели её не оставляет): прежняя догадка — повтор сверки
    языка, иначе «правило»; слов нет — «причина не сохранилась»; времени судьи нет, как было."""
    site = await _decided_before(
        session, await _run(session), "shika-kyoto.example.test", verdict=Lane.REVIEW
    )
    guess = sj.SegmentVerdict(Lane.REVIEW, UNCLEAR, decided_by=Decider.LANGUAGE)
    _language_says(guess if recognised else None, monkeypatch)

    await undone(session, site)

    await session.refresh(site)
    assert _after(site) == after


#: Слова модели в кэше — у сайта с моделью и цитатой в тексте.
MODEL_WORDS = "по тексту не видно, чем занят сайт, — решает человек. לא ברור אם זה בית ספר או בלוג"


@pytest.mark.parametrize(
    ("by", "words", "said"),
    [
        (Decider.LANGUAGE, UNCLEAR, su.LOST),
        (Decider.MODEL, MODEL_WORDS, MODEL_WORDS),
    ],
    ids=["language-row-is-not-its", "model-row-words-without-time"],
)
async def test_a_site_the_model_judged_stays_as_it_was(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    by: Decider,
    words: str,
    said: str,
) -> None:
    """Сайт с моделью — как был: решавший — модель, строка кэша — только модели. Строка языка с тем
    же вердиктом и цитатой (у обоих цитаты нет) не его — «причина не сохранилась»; строка модели —
    её слова, а времени судьи, как и было, нет."""
    site = await _decided_before(
        session,
        await _run(session),
        "kursy-ivrit-online.example.test",
        verdict=Lane.REVIEW,
        model="gpt-5-mini",
    )
    # Строку языка проход пишет без модели: вердикт языка решает код.
    model = "gpt-5-mini" if by is Decider.MODEL else None
    await _remembered(session, site, by=by, words=words, model=model)
    _language_says(None, monkeypatch)

    assert await _judge_says(session, site) == said
    await undone(session, site)

    await session.refresh(site)
    assert _after(site) == (Lane.REVIEW, Decider.MODEL, said, None, None)
