"""Смета пересуда прогона до запуска: сайтов, к модели, токенов — и отказ словами.

Стоимость называется до того, как её потратят, — как у сметы прогона (`segment_estimate.py`):

- **сайтов** — снимком тем же правилом, что возьмёт пересуд (`segment_rejudge.snapshot`): не
  решённые человеком и уже судившиеся; решения человека и «ещё не судили» — числами отдельно;
- **незаконченный пересуд того же описания продолжается** (`segment_rejudge_report.left_of`): смета —
  на сайты его снимка, до которых он не дошёл, и словами «продолжить с места остановки»;
- **к модели** — без тех, кого решит правило до модели (стоп-лист, «кому не пишем» по имени,
  пустой текст и отказ доступа): они бесплатны;
- **токенов на сайт — по истории**: среднее прошлых вердиктов модели этой гипотезы (той же
  версии промпта), нет — всех гипотез, нет и их — предложение сметы прогона
  (`segment_estimate.TOKENS_PER_SITE`); выше остатка дневного потолка модели продаж — отказ.

Ничего не пишет и никуда не ходит, кроме базы.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import llm as llm_cfg
from backend.config import sales as sales_cfg
from backend.config.judge import JudgeMode
from backend.features.core.models.domain import DomainModel
from backend.features.sales import usage_cap
from backend.features.sales.segment_estimate import (
    CAP_UNREAD,
    MODEL_NOT_SET,
    SALES_OFF,
    TOKENS_PER_SITE,
    tokens_left,
)
from backend.features.sales.segment_judge import (
    PROMPT_VERSION,
    SiteText,
    by_host,
    by_text,
    segment_digest,
)
from backend.features.sales.segment_judging import stoplisted
from backend.features.sales.segment_models import (
    Decider,
    SalesSegmentCandidateModel,
    SalesSegmentRunModel,
    SalesSegmentVerdictModel,
)
from backend.features.sales.segment_rejudge import refusal_of, segment_of, snapshot
from backend.features.sales.segment_rejudge_report import block_of, left_of

Candidate = SalesSegmentCandidateModel
Verdict = SalesSegmentVerdictModel

NOTHING = (
    "пересуживать нечего: сайты прогона ещё не судили или все их решил человек — решения "
    "человека пересуд не трогает"
)


@dataclass(slots=True)
class RejudgeEstimate:
    """Смета пересуда: числа и слова, и отказ, если пересуживать нельзя."""

    #: Пересудить: не решённые человеком и уже судившиеся.
    sites: int = 0
    #: Из них к модели — без решённых правилом до модели.
    model: int = 0
    per_site: int = 0
    tokens: int = 0
    #: Остаток дневного потолка модели; `None` — потолка нет.
    tokens_left: int | None = None
    kept_human: int = 0
    #: С лидом: новая полоса только покажется.
    with_lead: int = 0
    #: Ещё не судили: их судит продолжение прогона.
    pending: int = 0
    #: Отпечаток описания, которое спросит пересуд: запуск — только по этой смете.
    digest: str = ""
    mode: str = ""
    #: Продолжение незаконченного пересуда того же описания — с его снимка; `done` — уже пройдено.
    resumes: bool = False
    done: int = 0
    lines: list[str] = field(default_factory=list)
    refusal: str | None = None


def _number(value: int) -> str:
    return f"{value:,}".replace(",", " ")


async def tokens_per_site(session: AsyncSession, hypothesis_id: int) -> int:
    """Токенов модели на сайт — по прошлым вердиктам: этой гипотезы, иначе всех, иначе
    предложение сметы прогона."""
    base = select(func.avg(Verdict.tokens)).where(
        Verdict.decided_by == Decider.MODEL,
        Verdict.prompt_version == PROMPT_VERSION,
        Verdict.tokens > 0,
    )
    for query in (base.where(Verdict.hypothesis_id == hypothesis_id), base):
        found = await session.scalar(query)
        if found:
            return round(float(found))
    return TOKENS_PER_SITE


async def _to_model(
    session: AsyncSession, ids: list[int], found: RejudgeEstimate, moment: datetime
) -> None:
    """Сколько сайтов дойдёт до модели и у скольких лид — по строкам кандидатов."""
    if not ids:
        return
    rows = await session.execute(
        select(Candidate, DomainModel.host)
        .join(DomainModel, DomainModel.id == Candidate.domain_id)
        .where(Candidate.id.in_(ids))
    )
    pairs = list(rows.tuples().all())
    closed = await stoplisted(session, [candidate.domain_id for candidate, _ in pairs], moment)
    for candidate, host in pairs:
        found.with_lead += int(candidate.lead_id is not None)
        if candidate.domain_id in closed or by_host(host) is not None:
            continue
        if not candidate.site_text:
            found.model += 1  # пересуд откроет главную — и спросит модель
            continue
        found.model += int(by_text(SiteText.parse(candidate.site_text)) is None)


def _refusal(found: RejudgeEstimate, run: SalesSegmentRunModel, segment: str) -> str | None:
    """Первый отказ по порядку: модуль, суть пересуда, ключ модели, потолок."""
    over = (
        f"смета пересуда выше остатка дневного потолка модели: ≈ {_number(found.tokens)} токенов "
        f"при остатке {_number(found.tokens_left or 0)} — пересудите завтра"
    )
    cap_unread = usage_cap.sales_cap().setting in llm_cfg.UNREAD_CAPS
    pointless = refusal_of(run, segment)
    checks: tuple[tuple[bool, str | None], ...] = (
        (not sales_cfg.ENABLED, SALES_OFF),
        (pointless is not None, pointless),
        (found.sites == 0, NOTHING),
        (found.model > 0 and not llm_cfg.API_KEY, MODEL_NOT_SET),
        (found.tokens > 0 and cap_unread, CAP_UNREAD),
        (found.tokens_left is not None and found.tokens > found.tokens_left, over),
    )
    return next((words for failed, words in checks if failed), None)


def _what(found: RejudgeEstimate) -> str:
    """Первая строка сметы: новый пересуд — или продолжение незаконченного с места остановки."""
    if found.resumes:
        return (
            f"Продолжить пересуд с места остановки: осталось {_number(found.sites)} из "
            f"{_number(found.sites + found.done)} — то же описание и снимок полос прошлого запуска: "
            "пересуженные сайты к модели второй раз не пойдут, «было» у них прежнее."
        )
    return (
        f"Пересудить сайтов: {_number(found.sites)} — по тексту, который видел прошлый суд, мимо "
        "кэша вердиктов; выдачу второй раз не покупаем."
    )


def _lines(found: RejudgeEstimate) -> list[str]:
    """Смета словами — строкой на статью."""
    left = (
        ""
        if found.tokens_left is None
        else f", остаток дневного потолка — {_number(found.tokens_left)}"
    )
    lines = [
        _what(found),
        f"К модели — {_number(found.model)}, токенов ≈ {_number(found.tokens)} (≈ "
        f"{_number(found.per_site)} на сайт по прошлым вердиктам){left}.",
    ]
    if found.sites > found.model:
        lines.append(f"Остальных — {_number(found.sites - found.model)} — решит правило до модели.")
    if found.with_lead:
        lines.append(
            f"С лидом — {_number(found.with_lead)}: их новую полосу только покажем («стало бы»), "
            "лид и полоса не меняются."
        )
    if found.kept_human:
        lines.append(f"Решения человека не пересуживаются: {_number(found.kept_human)}.")
    if found.pending:
        lines.append(f"Ещё не судили — {_number(found.pending)}: их судит продолжение прогона.")
    if found.mode == JudgeMode.SHADOW:
        lines.append("Судья в наблюдении: его «отказ» не отсекает сайт, а отдаёт человеку.")
    return lines


async def _start_point(
    session: AsyncSession, run: SalesSegmentRunModel, digest: str
) -> tuple[RejudgeEstimate, list[int]]:
    """С чего начнёт пересуд: незаконченный того же описания — с сайтов своего снимка, до которых
    не дошёл; иначе — новый снимок полос."""
    block = block_of(run.report)
    left = left_of(block, digest)
    if left is not None:
        done = len(block.get("sites") or {}) - len(left)
        kept, pending = int(block.get("kept_human", 0)), int(block.get("pending", 0))
        found = RejudgeEstimate(
            len(left), kept_human=kept, pending=pending, digest=digest, mode=run.mode, done=done
        )
        found.resumes = True
        return found, left
    snap = await snapshot(session, run.id)
    found = RejudgeEstimate(
        sites=len(snap["sites"]),
        kept_human=snap["kept_human"],
        pending=snap["pending"],
        digest=digest,
        mode=run.mode,
    )
    return found, [int(key) for key in snap["sites"]]


async def estimate(
    session: AsyncSession, run: SalesSegmentRunModel, *, now: datetime | None = None
) -> RejudgeEstimate:
    """Смета пересуда прогона. Ничего не пишет."""
    moment = now or datetime.now(UTC)
    segment = await segment_of(session, run)
    found, ids = await _start_point(session, run, segment_digest(segment))
    await _to_model(session, ids, found, moment)
    found.per_site = await tokens_per_site(session, run.hypothesis_id)
    found.tokens = found.model * found.per_site
    found.tokens_left = await tokens_left(session, now)
    found.refusal = _refusal(found, run, segment)
    found.lines = _lines(found)
    return found
