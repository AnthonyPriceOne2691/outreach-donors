"""Судья рекламодателей: ядро — один спорный кандидат от текста до совета (проект автоматизации
спорных, PR 5). Прохода здесь нет: очередь, смета, потолок, проверка модели до траты и чекпоинты —
следующим PR; совет здесь никуда не применяется.

Путь случая: страница донора и главная домена (`judge_text.gather`) → вопрос модели о фактах
(`judge_client`) → сверка формы и цитат кодом (`judge_facts`) → совет таблицей правил
(`judge_rules`) → строка суда (`judge_store`). Кэш — по паре, контексту и когорте: тот же текст той
же подсказкой модель второй раз не спрашивают.

**Исход называется всегда** (`Outcome`), и не-вердикт не прячется за вердиктом (урок #91:
молчание модели легло «посмотри» на полгода): судить нечем (`blind`), заглушка защиты (`denied`),
модель не ответила (`unanswered`, с видом отказа), упёрлась в потолок вывода (`cut_off`), ответ не
строгой формы (`form_broken`), цитата не нашлась и после второго вопроса (`quote_missing`). Ни у
одного из них нет строки суда и кэша: следующий проход спросит снова. Из исходов и токенов проход
сложит свой отчёт.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

import httpx
from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.crawl import OutLinkModel
from backend.features.core.models.domain import DomainModel
from backend.features.crawl.judge_client import JudgeClient, Link, Said, user_message
from backend.features.crawl.judge_facts import LinkFacts
from backend.features.crawl.judge_rules import RULES_VERSION, Advice, advise
from backend.features.crawl.judge_store import Row, cohort_of, known, relabel, remember
from backend.features.crawl.judge_text import JudgeText, Seen, fingerprint, gather

logger = logging.getLogger(__name__)


class Outcome(StrEnum):
    """Чем кончился суд одного случая."""

    JUDGED = "judged"
    BLIND = "blind"
    DENIED = "denied"
    UNANSWERED = "unanswered"
    CUT_OFF = "cut_off"
    FORM_BROKEN = "form_broken"
    QUOTE_MISSING = "quote_missing"


@dataclass(frozen=True, slots=True)
class Case:
    """Спорный кандидат глазами судьи: пара «донор + домен», лучшая страница и ссылка на ней."""

    donor_host: str
    page_url: str
    link: Link
    #: Ниша донора (`domains.site_topic`); пусто — неизвестна.
    donor_niche: str | None = None


@dataclass(frozen=True, slots=True)
class Judged:
    """Исход суда: совет и правило — только у `judged`; `words` — слова человеку: правило совета
    или почему совета нет. `refusal` — вид отказа модели у `unanswered`."""

    outcome: Outcome
    advice: Advice | None = None
    rule: str = ""
    words: str = ""
    facts: LinkFacts | None = None
    tokens: int = 0
    refusal: str | None = None
    no_credits: bool = False
    retried: bool = False
    retry_held: bool = False
    cached: bool = False


def judged_of(said: Said) -> Judged:
    """Ответ модели → исход: факты — совет таблицей правил; иначе — почему нет."""
    if said.facts is not None:
        ruled = advise(said.facts)
        return Judged(
            Outcome.JUDGED,
            ruled.advice,
            ruled.rule,
            ruled.words,
            said.facts,
            tokens=said.tokens,
            no_credits=said.no_credits,
            retried=said.retried,
            retry_held=said.retry_held,
        )
    refusal: str | None = None
    if said.cut_off:
        outcome = Outcome.CUT_OFF
    elif said.refusal is not None or said.empty:
        outcome = Outcome.UNANSWERED
        refusal = said.refusal.kind.value if said.refusal is not None else "пустой ответ"
    elif said.broken:
        outcome = Outcome.FORM_BROKEN
    else:
        outcome = Outcome.QUOTE_MISSING
    return Judged(
        outcome,
        words=said.failure,
        tokens=said.tokens,
        refusal=refusal,
        no_credits=said.no_credits,
        retried=said.retried,
    )


async def decide(client: JudgeClient, link: Link, text: JudgeText) -> Judged:
    """Суд по готовому тексту — без базы и без кэша: так его меряет eval на наборе."""
    said = await client.judge(user=user_message(link, text), text=text, host=link.target)
    return judged_of(said)


async def judge_seen(
    session: AsyncSession,
    case_: Case,
    seen: Seen,
    *,
    client: JudgeClient,
    now: datetime | None = None,
) -> Judged:
    """Суд по тому, что судья увидел: факты из кэша — совет заново таблицей; нет — модель."""
    moment = now or datetime.now(UTC)
    user = user_message(case_.link, seen.text)
    context = fingerprint(user)
    cohort = cohort_of(client.model)
    pair = {"donor_host": case_.donor_host, "target_root": case_.link.target}
    found = await known(session, **pair, context_print=context, cohort=cohort, now=moment)
    if found is not None:
        if found.rules_version != RULES_VERSION:
            await relabel(session, ids=[found.id])
        ruled = advise(found.facts)
        return Judged(
            Outcome.JUDGED, ruled.advice, ruled.rule, ruled.words, found.facts, cached=True
        )
    said = await client.judge(user=user, text=seen.text, host=case_.link.target)
    result = judged_of(said)
    if result.facts is not None:
        row = Row(
            result.facts,
            seen.text.for_model(),
            seen.language,
            case_.donor_niche,
            result.tokens,
            result.retried,
        )
        await remember(
            session, **pair, context_print=context, cohort=cohort, row=row, moment=moment
        )
    return result


async def judge_case(
    session: AsyncSession,
    case_: Case,
    *,
    client: JudgeClient,
    http: httpx.AsyncClient,
    now: datetime | None = None,
) -> Judged:
    """Один спорный кандидат: дочитать страницу и главную, спросить модель, поставить совет."""
    seen = await gather(
        http, page_url=case_.page_url, target_root=case_.link.target, anchor=case_.link.anchor
    )
    if seen.denied:
        return Judged(Outcome.DENIED, words=seen.denied)
    if seen.blind:
        return Judged(Outcome.BLIND, words=seen.blind)
    return await judge_seen(session, case_, seen, client=client, now=now)


async def case_of(session: AsyncSession, candidate: CandidateModel) -> Case | None:
    """Кандидат → случай судьи: лучшая страница, ссылка на ней с тем же анкором (иначе первая) и
    ниша донора. Лучшей страницы нет — судить нечего."""
    if not candidate.best_page_url:
        return None
    anchor = candidate.best_anchor or ""
    outlink = await session.scalar(
        select(OutLinkModel)
        .where(
            OutLinkModel.crawl_run_id == candidate.crawl_run_id,
            OutLinkModel.target_root == candidate.target_root,
            OutLinkModel.page_url == candidate.best_page_url,
        )
        .order_by(case((OutLinkModel.anchor == anchor, 0), else_=1), OutLinkModel.id)
        .limit(1)
    )
    niche = await session.scalar(
        select(DomainModel.site_topic).where(DomainModel.host == candidate.donor_host)
    )
    link = Link(target=candidate.target_root, anchor=anchor)
    if outlink is not None:
        link = Link(
            target=candidate.target_root,
            anchor=anchor or outlink.anchor,
            sponsored=outlink.sponsored,
            nofollow=outlink.nofollow,
            ugc=outlink.ugc,
            in_body=outlink.in_body,
            page_label=outlink.page_label,
            published=outlink.page_published,
        )
    return Case(candidate.donor_host, candidate.best_page_url, link, niche)
