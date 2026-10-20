"""Отмена решения человека по сайту сбора: сайт — туда, куда его кладёт судья, со словами судьи.

Тест продаж 20.10: «Принять» и «Отказ» по сайту «на проверку» есть, а вернуть ошибочное решение
нечем. Отмена — обратное решению (`segment_report.decide`):

- **Куда возвращается сайт — решает судья, а не прошитая полоса.** Решение человека затирает
  причину словами «принял человек», но вердикт, цитата и модель остаются у сайта, а слова вердикта —
  в кэше вердиктов (`sales_segment_verdicts`). Полоса и причина ложатся тем же правилом, что у
  прохода судьи (`segment_window.landing`: `segment_judge.effective` в режиме прогона): в
  наблюдении отказ модели — «судья отказал бы» со словами судьи. Кто и когда решил — очищено:
  решения нет.
- **Вердикт языка сайта** (`Decider.LANGUAGE`) в кэш не ложится и модели у него нет — как у
  правила. Он повторяется по сохранённому тексту сайта и языку писем прогона
  (`segment_judge.by_language`): совпал с вердиктом сайта — сайт вернётся как от языка («судья
  отказал бы» в наблюдении) и с его словами, а не отсечённым правилом.
- **Слов нет в кэше** — модель не дала цитаты или ответ не разобран (такой вердикт не кэшируется,
  `segment_judge.proven_again`), модель не ответила: причина — словами, что она не сохранилась.
- **Отменяется только решение человека**: решение правила или судья — отказ словами; сайт уже
  ждёт человека (двойной клик, повтор запроса) — не отказ и не вторая запись журнала.
- **Лид уже заведён — отказ словами**: письма лиду отменой не отозвать, его отклоняют во вкладке
  «emails». Проверка и отмена — одной транзакцией под замком строки сайта; заведение лидов держит
  строки принятых тем же замком до конца своей транзакции (`segment_leads._ready`). Отмена, пришедшая
  посреди заведения, ждёт его конца и видит лид — или, дольше предела ожидания, отказывает словами
  «занято», а не висит; заведение, пришедшее посреди отмены, ждёт её и сайт без решения не берёт.

Слова судьи у решённого человеком сайта (`judged_before`) читает и список сайтов прогона: с чем
человек не согласился, видно рядом с его решением.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config.judge import JudgeMode
from backend.features.sales.segment_judge import SegmentVerdict, SiteText, by_language
from backend.features.sales.segment_models import (
    AWAITS_HUMAN,
    Decider,
    Lane,
    SalesSegmentCandidateModel,
    SalesSegmentRunModel,
    SalesSegmentVerdictModel,
)
from backend.features.sales.segment_window import Judged, Landing, landing

logger = logging.getLogger(__name__)

Candidate = SalesSegmentCandidateModel
Verdict = SalesSegmentVerdictModel

#: Сколько ждать замок строки сайта: заведение лидов держит строки принятых до конца очистки
#: адресов. Строкой целиком — значение не из ввода; тест предела подменяет её короткой.
LOCK_WAIT = "SET LOCAL lock_timeout = '5s'"
#: SQLSTATE «замок не дождались» (`lock_not_available`).
LOCK_NOT_AVAILABLE = "55P03"

LEAD_MADE = "лид уже заведён — отклоните его во вкладке «emails»"
BUSY = "по сайту сейчас заводится лид или пишется другое решение — обновите список через минуту"
NOT_JUDGED = "сайт ещё не судили — отменять нечего"
#: Решение правила или судьи: отменяется только решение человека.
NOT_HUMAN = {
    Decider.RULE: "сайт решило правило, а не человек — отменяется только решение человека",
    Decider.MODEL: "сайт решил судья, а не человек — отменяется только решение человека",
    Decider.LANGUAGE: "сайт решила сверка языка, а не человек — отменяется только решение человека",
}
#: Слов вердикта нет в кэше: модель не дала цитаты или ответ не разобран.
LOST = "причина не сохранилась"
#: Модель не ответила: слова отказа провайдера не хранятся, а следующий проход спросит снова
#: (`segment_judging.waiting_judge`).
SILENT = "модель не ответила — следующий проход спросит её снова"


class UndoRefusedError(ValueError):
    """Решение не отменить: решил не человек, лид уже заведён. Текст — почему."""


class UndoBusyError(UndoRefusedError):
    """Строку сайта держит другая операция дольше `LOCK_WAIT`."""


@dataclass(frozen=True, slots=True)
class Undone:
    """Сайт после отмены и какое решение отменено; `None` — повтор: сайт уже ждёт человека."""

    candidate: Candidate
    was: str | None = None

    @property
    def repeated(self) -> bool:
        return self.was is None


def undoable(candidate: Candidate) -> bool:
    """Отменить можно: решил человек, и лида из сайта нет."""
    return candidate.decided_by == Decider.HUMAN and candidate.lead_id is None


def _decider(candidate: Candidate) -> Decider:
    """Кто дал вердикт до человека: правило модель не зовёт, и модели у его вердикта нет."""
    return Decider.RULE if candidate.model is None else Decider.MODEL


def _language(candidate: Candidate, letters: str | None) -> SegmentVerdict | None:
    """Вердикт языка сайта, если сайт решала сверка языка: модели у такого вердикта нет, и по
    тексту, который видел судья, сверка даёт тот же вердикт (`segment_judge.by_language`)."""
    if candidate.model is not None or not candidate.site_text:
        return None
    found = by_language(SiteText.parse(candidate.site_text), letters)
    return found if found is not None and found.lane == candidate.verdict else None


def _verdict(candidate: Candidate, words: str | None, letters: str | None) -> SegmentVerdict | None:
    """Вердикт судьи, как он лёг на сайт до решения человека; `None` — судья был выключен."""
    if candidate.unanswered:
        return SegmentVerdict(Lane.REVIEW, SILENT, model=candidate.model, unanswered=True)
    if candidate.verdict is None:
        return None
    if (language := _language(candidate, letters)) is not None:
        return language
    return SegmentVerdict(
        Lane(candidate.verdict),
        words or LOST,
        candidate.quote,
        candidate.model,
        decided_by=_decider(candidate),
    )


async def _cached_words(session: AsyncSession, candidates: Sequence[Candidate]) -> dict[int, str]:
    """Слова вердикта из кэша — по домену, гипотезе, вердикту, цитате и тому, кто решил; свежий
    первым: пересуд под другим описанием кладёт тому же домену вторую строку."""
    asked = [candidate for candidate in candidates if candidate.verdict is not None]
    if not asked:
        return {}
    rows = await session.execute(
        select(
            Verdict.domain_id,
            Verdict.hypothesis_id,
            Verdict.verdict,
            Verdict.quote,
            Verdict.decided_by,
            Verdict.reason,
        )
        .where(
            Verdict.domain_id.in_({candidate.domain_id for candidate in asked}),
            Verdict.hypothesis_id.in_({candidate.hypothesis_id for candidate in asked}),
        )
        .order_by(Verdict.updated_at.desc(), Verdict.id.desc())
    )
    found: dict[tuple[int, int, str, str | None, str], str] = {}
    for domain_id, hypothesis_id, verdict, quote, decided_by, reason in rows.tuples().all():
        found.setdefault((domain_id, hypothesis_id, verdict, quote, decided_by), reason)
    return {
        candidate.id: words
        for candidate in asked
        if (words := found.get(_key(candidate))) is not None
    }


def _key(candidate: Candidate) -> tuple[int, int, str, str | None, str]:
    return (
        candidate.domain_id,
        candidate.hypothesis_id,
        candidate.verdict or "",
        candidate.quote,
        _decider(candidate).value,
    )


async def judged_before(
    session: AsyncSession, candidates: Sequence[Candidate], mode: JudgeMode, letters: str | None
) -> dict[int, Landing]:
    """Что лежало бы на решённых человеком сайтах без него — полоса и слова судьи тем же правилом,
    что у прохода (`segment_window.landing`). Сайты прогона: режим и язык писем — его (`run.mode`,
    `run.language`)."""
    human = [candidate for candidate in candidates if candidate.decided_by == Decider.HUMAN]
    words = await _cached_words(session, human)
    return {
        candidate.id: landing(
            Judged(_verdict(candidate, words.get(candidate.id), letters), SiteText(())), mode
        )
        for candidate in human
    }


def _sqlstate(exc: DBAPIError) -> str:
    """SQLSTATE отказа базы — как его читает общий обработчик (`api/errors._database_code`)."""
    orig = exc.orig
    return str(getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None) or "")


async def _locked(
    session: AsyncSession, candidate_id: int
) -> tuple[Candidate, str, str | None] | None:
    """Строка сайта под `FOR UPDATE` (строка прогона — без замка: её пишет идущий проход), режим
    судьи и язык писем прогона — с пределом ожидания и в точке сохранения: отказ по пределу не
    ломает транзакцию вызывающего. Значения — из базы, а не из памяти сессии."""
    await session.execute(text(LOCK_WAIT))
    query = (
        select(Candidate, SalesSegmentRunModel.mode, SalesSegmentRunModel.language)
        .join(SalesSegmentRunModel, SalesSegmentRunModel.id == Candidate.run_id)
        .where(Candidate.id == candidate_id)
        .with_for_update(of=Candidate)
        .execution_options(populate_existing=True)
    )
    try:
        async with session.begin_nested():
            found = (await session.execute(query)).tuples().first()
    except DBAPIError as exc:
        if _sqlstate(exc) != LOCK_NOT_AVAILABLE:
            raise
        logger.warning(
            "сбор продаж: строку сайта держит другая операция", extra={"id": candidate_id}
        )
        raise UndoBusyError(BUSY) from exc
    return None if found is None else (found[0], found[1], found[2])


def _not_human(candidate: Candidate) -> Undone:
    """Сайт решил не человек: ждёт человека — повтор отмены; иначе — отказ словами."""
    if candidate.lane in AWAITS_HUMAN:
        return Undone(candidate)
    if candidate.decided_by is None:
        raise UndoRefusedError(NOT_JUDGED)
    raise UndoRefusedError(NOT_HUMAN[Decider(candidate.decided_by)])


async def undo(session: AsyncSession, candidate_id: int) -> Undone | None:
    """Отменить решение человека по сайту; `None` — сайта нет. Коммит — за вызывающим."""
    locked = await _locked(session, candidate_id)
    if locked is None:
        return None
    candidate, mode, letters = locked
    if candidate.decided_by != Decider.HUMAN:
        return _not_human(candidate)
    if candidate.lead_id is not None:
        raise UndoRefusedError(LEAD_MADE)
    found = (await judged_before(session, [candidate], JudgeMode(mode), letters))[candidate.id]
    was = candidate.lane
    values = found.values(datetime.now(UTC)) | {"decided_at": None, "decided_by_user": None}
    for name, value in values.items():
        setattr(candidate, name, value)
    logger.info("сбор продаж: решение по сайту отменено", extra={"id": candidate_id, "was": was})
    return Undone(candidate, was)
