"""Отмена решения человека по сайту сбора: сайт — туда, куда его кладёт судья, со словами судьи.

Тест продаж 20.10: «Принять» и «Отказ» по сайту «на проверку» есть, а вернуть ошибочное решение
нечем. Отмена — обратное решению (`segment_report.decide`):

- **Куда возвращается сайт — решает судья, а не прошитая полоса.** Решение человека затирает
  причину, время и «кто решил» своими («принял человек»), но вердикт, цитата и модель остаются у
  сайта, а слова, время и решавший судьи — в своих колонках (`judge_reason`, `judged_at`,
  `judged_by`: их пишет только судья, `segment_window.land`). Полоса и причина ложатся тем же
  правилом, что у прохода судьи (`segment_window.landing`: `segment_judge.effective` в режиме
  прогона): в наблюдении отказ модели или языка — «судья отказал бы» со словами судьи. Автор
  решения очищен, время — судьи: под «модель» снова дата его решения (тест продаж 22.10: было
  пусто).
- **Кто решал до человека — из колонки судьи** (`judged_by`: правило, модель, язык). У сайта,
  решённого человеком до колонки, — из строки кэша вердиктов под его вердикт (`_cached`: домен,
  гипотеза, вердикт и цитата; у вердикта без модели решавший в строке — правило или язык; свежая
  первой) — с её словами и временем. Стенд 22.10: правило языка поменялось, повтор сверки по
  сохранённому тексту вердикт языка больше не узнавал, и такой сайт выходил «Судья: причина не
  сохранилась», а отмена — «правило» без даты, хотя строка кэша со словами и решавшим «язык» есть.
  Нет и строки — догадка: повтор сверки языка (`_language`, `segment_judge.by_language`), иначе по
  модели вердикта (`_decider`). Вердикт языка ложится в кэш, только если модель спрашивали (язык
  «не понять» — с фактами); отказ языка до модели строки не оставляет — его узнаёт только догадка.
- **Слова судьи — из его колонки; кэш вердиктов — у сайтов, судимых до неё** (тест продаж 22.10:
  вердикт без цитаты в тексте в кэш не ложится, `SegmentVerdict.proven`, — и у решения человека
  выходило «Судья: причина не сохранилась»). Решено человеком до колонки, и в кэше слов нет —
  словами, что причина не сохранилась: восстанавливать не из чего. Модель не ответила — словами,
  что следующий проход спросит её снова.
- **Отменяется только решение человека**: решение правила или судья — отказ словами; сайт уже
  ждёт человека (двойной клик, повтор запроса) — не отказ и не вторая запись журнала.
- **Лид уже заведён, а письмо ему не ушло — лид снимается вместе с решением** (тест продаж 21.10:
  после «Принять» адрес находится и лид заводится почти сразу, и отказ «лид уже заведён» не давал
  отменить «Принять» вовсе). Лид — «отклонён» с причиной `RejectionReason.UNDONE` и словами
  `UNDONE_NOTE`, а не удалён: так его снимают и стоп-лист, и очистка. Его первое письмо в очереди —
  с очереди тем же местом (`off_queue.take_off`, слова — `off_queue.closed`), добивок без ушедшего
  первого не бывает, а отправка снятому лиду не пустит ничего (`mail.unwritable`). Ссылка сайта на
  лида гаснет: сайт — как не принятый, и числа прогона и воронки сходятся без поправок (лиды прогона
  считаются по ссылке сайта, воронка — по письмам). Снятый держатель отпускает компанию
  (`company_hold.freed`), как после стоп-листа. «Принять» снова возвращает того же лида
  (`segment_leads`: новый лид с тем же адресом очистка отсекла бы дублем снятого). Удаление строки
  потеряло бы оплаченную проверку адреса и оставило бы без хозяина ссылки на лида в словах чужих
  лидов («дубль: адрес уже у лида №…», держатель компании).
- **Письмо ушло, уходит или могло уйти — отказ словами** (`GONE`, правило — `gone_leads`): отменой
  письмо не отозвать. Остановить лида можно стоп-листом продаж; во вкладке «emails» остановки
  цепочки нет. Тем же правилом список сайтов говорит, есть ли кнопка (`undoable`).
- **Гонки — замками строк в одном порядке: сайт → лид → письма его диалога.** Проверка и снятие —
  одной транзакцией. Заведение лидов держит строки принятых до конца своей транзакции
  (`segment_leads._ready`): отмена, пришедшая посреди заведения, ждёт его и снимает заведённого
  лида; заведение, пришедшее посреди отмены, ждёт её и сайт без решения не берёт. Сборка письма
  берёт строку лида на запись письма (`off_queue.still_ready`): пришла посреди отмены — ждёт её и
  письма снятому лиду не пишет; отмена посреди записи письма ждёт её конца и снимает письмо.
  Отправка захватывает письмо условием «в очереди» (`sending._claim`), отмена держит строку письма:
  кто первым взял строку, тот и прав — письмо ушло, и отмена отказывает, или снято, и отправка его не
  берёт. Строку держат дольше предела ожидания — отказ словами «занято», а не ожидание без конца.

Слова судьи у решённого человеком сайта (`judged_before`) читает и список сайтов прогона: с чем
человек не согласился, видно рядом с его решением.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Collection, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Row, Select, or_, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config.judge import JudgeMode
from backend.features.core.domain import MessageStatus
from backend.features.core.models.outreach import MessageModel
from backend.features.letters.chain import FIRST_STEP
from backend.features.sales import clean_write, company_hold, off_queue
from backend.features.sales.clean_report import REASON_LABELS
from backend.features.sales.models import (
    LeadStatus,
    RejectionReason,
    SalesLeadModel,
    SalesThreadModel,
)
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
from backend.features.sales.verifier import WENT

logger = logging.getLogger(__name__)

Candidate = SalesSegmentCandidateModel
Verdict = SalesSegmentVerdictModel

#: Сколько ждать замок строки сайта, его лида и писем лида: заведение лидов держит строки принятых
#: до конца очистки адресов, сборка — строку лида до конца записи письма. Строкой целиком — значение
#: не из ввода; тест предела подменяет её короткой.
LOCK_WAIT = "SET LOCAL lock_timeout = '5s'"
#: SQLSTATE «замок не дождались» (`lock_not_available`).
LOCK_NOT_AVAILABLE = "55P03"

#: Письмо лиду ушло, уходит или могло уйти: отменой его не отозвать.
GONE = (
    "письмо лиду уже ушло — отменой его не отозвать; остановить лида можно только стоп-листом "
    "продаж: адрес или домен — на вкладке «Стоп-лист»"
)
#: Почему лид снят — словами у лида (`cleaning_note`) и, с началом `off_queue.LEAD_CLOSED`, у его
#: снятого письма.
UNDONE_NOTE = "решение человека по сайту сбора отменено — лид снят, письмо ему не уйдёт"
BUSY = (
    "по сайту сейчас заводится лид, пишется другое решение или собирается и уходит письмо — "
    "обновите список через минуту"
)
NOT_JUDGED = "сайт ещё не судили — отменять нечего"
#: Решение правила или судьи: отменяется только решение человека.
NOT_HUMAN = {
    Decider.RULE: "сайт решило правило, а не человек — отменяется только решение человека",
    Decider.MODEL: "сайт решил судья, а не человек — отменяется только решение человека",
    Decider.LANGUAGE: "сайт решила сверка языка, а не человек — отменяется только решение человека",
}
#: Слов судьи нет ни в его колонке, ни в кэше: человек решил до колонки, а вердикт без цитаты в
#: тексте или с неразобранным ответом в кэш не ложится.
LOST = "причина не сохранилась"
#: Модель не ответила: слова отказа провайдера не хранятся, а следующий проход спросит снова
#: (`segment_judging.waiting_judge`).
SILENT = "модель не ответила — следующий проход спросит её снова"


class UndoRefusedError(ValueError):
    """Решение не отменить: решил не человек, письмо лиду сайта ушло. Текст — почему."""


class UndoBusyError(UndoRefusedError):
    """Строку сайта, его лида или письма лида держит другая операция дольше `LOCK_WAIT`."""


@dataclass(frozen=True, slots=True)
class LeadOff:
    """Лид сайта, снятый отменой, — для журнала: номер, адрес, было ли его письмо в очереди и каким
    был лид (`_was`)."""

    lead_id: int
    email: str
    #: Первое письмо лида снято с очереди; ложь — в очереди письма не было.
    letter_off: bool
    #: Каким лид был до снятия — словами вкладки «emails»: «готов», «новый», «отклонён: дубль».
    was: str


#: Состояние лида словами — как на вкладке «emails».
STATE_WORDS = {LeadStatus.NEW: "новый", LeadStatus.READY: "готов", LeadStatus.REJECTED: "отклонён"}
#: Причина отказа словами по коду, как он записан у лида; незнакомый код — как записан.
_REASON_WORDS = {code.value: words for code, words in REASON_LABELS.items()}


def _was(lead: SalesLeadModel) -> str:
    """Каким лид был до снятия: отклонённому — и причина очистки словами, иначе она пропала бы —
    снятие пишет свою (`UNDONE`)."""
    said = STATE_WORDS[lead.status]
    reason = lead.rejection_reason
    if lead.status is LeadStatus.REJECTED and reason:
        said = f"{said}: {_REASON_WORDS.get(reason, reason)}"
    return said


@dataclass(frozen=True, slots=True)
class Undone:
    """Сайт после отмены, какое решение отменено и снятый лид сайта; `was` — `None`: повтор, сайт
    уже ждёт человека."""

    candidate: Candidate
    was: str | None = None
    lead: LeadOff | None = None

    @property
    def repeated(self) -> bool:
        return self.was is None


def undoable(candidate: Candidate, *, gone: bool = False) -> bool:
    """Отменить можно: решил человек, а письмо лиду сайта не ушло, не уходит и уйти не могло
    (`gone` — у лида сайта такое письмо есть, `gone_leads`)."""
    return candidate.decided_by == Decider.HUMAN and not gone


def _went() -> ColumnElement[bool]:
    """Письмо ушло, уходит или могло уйти: «отправляется», ушло, вернулось отказом доставки
    (`verifier.WENT`) — или у него наш `Message-ID` прежней попытки, которая могла дойти: такое
    не трогает и сборка (`off_queue.again`)."""
    return or_(MessageModel.status.in_(WENT), MessageModel.internet_message_id.is_not(None))


async def gone_leads(session: AsyncSession, lead_ids: Collection[int]) -> set[int]:
    """Лиды, чьё письмо ушло, уходит или могло уйти (`_went`): снять их отменой нельзя. Письма —
    все письма диалога лида: добивка и ответ бывают только после ушедшего первого. Одно правило у
    отказа отмены и у кнопки в списке сайтов (`undoable`)."""
    wanted = sorted(set(lead_ids))
    if not wanted:
        return set()
    rows = await session.scalars(
        select(SalesThreadModel.lead_id)
        .join(MessageModel, MessageModel.thread_id == SalesThreadModel.thread_id)
        .where(SalesThreadModel.lead_id.in_(wanted), _went())
        .distinct()
    )
    return set(rows.all())


@dataclass(frozen=True, slots=True)
class _Cached:
    """Строка кэша вердиктов, совпавшая с вердиктом сайта: слова, кто решал и когда (время строки —
    когда судья положил в неё эти слова: проход или пересуд)."""

    words: str
    by: Decider
    at: datetime


def _decider(candidate: Candidate, cached: _Cached | None = None) -> Decider:
    """Кто дал вердикт до человека — из колонки судьи (`judged_by`). Судили и решили до неё — из
    совпавшей строки кэша (`cached`); нет её — догадка: правило модель не зовёт, и модели у его
    вердикта нет."""
    if candidate.judged_by is not None:
        return Decider(candidate.judged_by)
    if cached is not None:
        return cached.by
    return Decider.RULE if candidate.model is None else Decider.MODEL


def _language(candidate: Candidate, letters: str | None) -> SegmentVerdict | None:
    """Вердикт языка сайта у сайта, судимого до колонки судьи (`judged_by` пуст) и без строки кэша,
    если его решала сверка языка: модели у такого вердикта нет, и по тексту, который видел судья,
    сверка даёт тот же вердикт (`segment_judge.by_language`) — пока правило языка не поменялось."""
    if candidate.judged_by is not None or candidate.model is not None or not candidate.site_text:
        return None
    found = by_language(SiteText.parse(candidate.site_text), letters)
    return found if found is not None and found.lane == candidate.verdict else None


def _verdict(
    candidate: Candidate, cached: _Cached | None, letters: str | None
) -> SegmentVerdict | None:
    """Вердикт судьи, как он лёг на сайт до решения человека; `None` — судья был выключен. Слова —
    судьи из его колонки (`judge_reason`); у сайта, судимого до неё, — из строки кэша (`cached`),
    нет и там — `LOST`. Кто решал — из колонки судьи; у сайта, решённого до неё, — из строки кэша,
    нет её — повтор сверки языка, иначе догадка по модели."""
    if candidate.unanswered:
        return SegmentVerdict(Lane.REVIEW, SILENT, model=candidate.model, unanswered=True)
    if candidate.verdict is None:
        return None
    if cached is None and (language := _language(candidate, letters)) is not None:
        return language
    return SegmentVerdict(
        Lane(candidate.verdict),
        _words(candidate, cached),
        candidate.quote,
        candidate.model,
        decided_by=_decider(candidate, cached),
    )


def _words(candidate: Candidate, cached: _Cached | None) -> str:
    """Слова судьи: из его колонки; у сайта, судимого до неё, — из строки кэша; нет и там —
    `LOST`."""
    remembered = cached.words if cached is not None else ""
    return candidate.judge_reason or remembered or LOST


def _deciders(candidate: Candidate) -> tuple[str, ...]:
    """Кем мог быть решавший в строке кэша: у вердикта с моделью — модель; без модели — правило
    или язык (вердикт языка в кэше — со своим решавшим, и угадывать его по тексту не нужно)."""
    if candidate.model is not None:
        return (Decider.MODEL.value,)
    return (Decider.RULE.value, Decider.LANGUAGE.value)


#: Строки кэша по вердикту сайта: домен, гипотеза, вердикт, цитата → (решавший, слова, время),
#: свежая первой.
_Rows = dict[tuple[int, int, str, str | None], list[tuple[str, str, datetime]]]


def _key(candidate: Candidate) -> tuple[int, int, str, str | None]:
    return (candidate.domain_id, candidate.hypothesis_id, candidate.verdict or "", candidate.quote)


def _fit(candidate: Candidate, rows: _Rows) -> _Cached | None:
    """Свежая строка под вердикт сайта, чей решавший возможен у такого вердикта (`_deciders`)."""
    for by, reason, at in rows.get(_key(candidate), []):
        if by in _deciders(candidate):
            return _Cached(reason, Decider(by), at)
    return None


async def _cached(session: AsyncSession, candidates: Sequence[Candidate]) -> dict[int, _Cached]:
    """Строка кэша вердиктов под вердикт сайта — по домену, гипотезе, вердикту и цитате, решавший —
    из возможных у такого вердикта (`_deciders`); свежая первой: пересуд под другим описанием
    кладёт тому же домену вторую строку."""
    asked = [candidate for candidate in candidates if candidate.verdict is not None]
    if not asked:
        return {}
    found = await session.execute(
        select(
            Verdict.domain_id,
            Verdict.hypothesis_id,
            Verdict.verdict,
            Verdict.quote,
            Verdict.decided_by,
            Verdict.reason,
            Verdict.updated_at,
        )
        .where(
            Verdict.domain_id.in_({candidate.domain_id for candidate in asked}),
            Verdict.hypothesis_id.in_({candidate.hypothesis_id for candidate in asked}),
        )
        .order_by(Verdict.updated_at.desc(), Verdict.id.desc())
    )
    rows: _Rows = {}
    for domain_id, hypothesis_id, verdict, quote, by, reason, at in found.tuples().all():
        rows.setdefault((domain_id, hypothesis_id, verdict, quote), []).append((by, reason, at))
    return {candidate.id: kept for candidate in asked if (kept := _fit(candidate, rows))}


async def _before(
    session: AsyncSession, candidates: Sequence[Candidate], mode: JudgeMode, letters: str | None
) -> dict[int, tuple[Landing, datetime | None]]:
    """Что лежало бы на решённых человеком сайтах без него и когда судья это решил: время — из
    колонки судьи (`judged_at`); у сайта без модели, решённого до колонок, — время строки кэша,
    по которой узнан решавший; иначе его нет."""
    human = [candidate for candidate in candidates if candidate.decided_by == Decider.HUMAN]
    cached = await _cached(session, [c for c in human if c.judge_reason is None])
    found: dict[int, tuple[Landing, datetime | None]] = {}
    for candidate in human:
        row = cached.get(candidate.id)
        verdict = _verdict(candidate, row, letters)
        at = candidate.judged_at
        if at is None and row is not None and candidate.model is None:
            at = row.at
        found[candidate.id] = (landing(Judged(verdict, SiteText(())), mode), at)
    return found


async def judged_before(
    session: AsyncSession, candidates: Sequence[Candidate], mode: JudgeMode, letters: str | None
) -> dict[int, Landing]:
    """Что лежало бы на решённых человеком сайтах без него — полоса и слова судьи тем же правилом,
    что у прохода (`segment_window.landing`). Сайты прогона: режим и язык писем — его (`run.mode`,
    `run.language`). Кэш вердиктов читается только для сайтов без слов судьи в его колонке."""
    found = await _before(session, candidates, mode, letters)
    return {candidate_id: said for candidate_id, (said, _) in found.items()}


def _sqlstate(exc: DBAPIError) -> str:
    """SQLSTATE отказа базы — как его читает общий обработчик (`api/errors._database_code`)."""
    orig = exc.orig
    return str(getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None) or "")


@asynccontextmanager
async def _waiting(session: AsyncSession, what: str) -> AsyncIterator[None]:
    """Шаг, который ждёт чужой замок строки, — с пределом ожидания (`LOCK_WAIT`, ставит `_locked`
    на всю транзакцию) и в точке сохранения: отказ по пределу — словами «занято», а не пятисотка,
    и транзакция вызывающего жива."""
    try:
        async with session.begin_nested():
            yield
    except DBAPIError as exc:
        if _sqlstate(exc) != LOCK_NOT_AVAILABLE:
            raise
        logger.warning("сбор продаж: строку держит другая операция", extra={"what": what})
        raise UndoBusyError(BUSY) from exc


async def _held(session: AsyncSession, query: Select[Any], what: str) -> list[Row[Any]]:
    """Строки запроса под замком (`_waiting`). Значения — из базы, а не из памяти сессии."""
    async with _waiting(session, what):
        rows = await session.execute(query)  # строки уже прочитаны: результат буферизован
    return list(rows.all())


async def _locked(
    session: AsyncSession, candidate_id: int
) -> tuple[Candidate, str, str | None] | None:
    """Строка сайта под `FOR UPDATE` (строка прогона — без замка: её пишет идущий проход), режим
    судьи и язык писем прогона (`_held`)."""
    await session.execute(text(LOCK_WAIT))
    query = (
        select(Candidate, SalesSegmentRunModel.mode, SalesSegmentRunModel.language)
        .join(SalesSegmentRunModel, SalesSegmentRunModel.id == Candidate.run_id)
        .where(Candidate.id == candidate_id)
        .with_for_update(of=Candidate)
        .execution_options(populate_existing=True)
    )
    found = await _held(session, query, f"сайт №{candidate_id}")
    return None if not found else (found[0][0], found[0][1], found[0][2])


async def _lead_off(session: AsyncSession, lead_id: int) -> LeadOff | None:
    """Снять лида сайта, пока его письмо не ушло. Строка лида и письма его диалога — под замком
    (`_held`); письмо ушло, уходит или могло уйти (`gone_leads`) — отказ словами `GONE`, ничего не
    тронуто. Иначе первое письмо в очереди — с очереди (`off_queue.take_off`), лид — «отклонён» с
    причиной `UNDONE` (время правки лида — прежнее, как у очистки: `clean_write`), а компания,
    которую он держал, свободна (`company_hold.freed`). `None` — лида уже нет."""
    lead_query = select(SalesLeadModel).where(SalesLeadModel.id == lead_id).with_for_update()
    leads = await _held(
        session, lead_query.execution_options(populate_existing=True), f"лид №{lead_id}"
    )
    if not leads:
        return None
    lead: SalesLeadModel = leads[0][0]
    letters_query = (
        select(MessageModel)
        .join(SalesThreadModel, SalesThreadModel.thread_id == MessageModel.thread_id)
        .where(SalesThreadModel.lead_id == lead_id)
        .order_by(MessageModel.id)
        .with_for_update(of=MessageModel)
        .execution_options(populate_existing=True)
    )
    letters = [row[0] for row in await _held(session, letters_query, f"письма лида №{lead_id}")]
    if await gone_leads(session, [lead_id]):
        raise UndoRefusedError(GONE)
    was = _was(lead)
    queued = [
        letter
        for letter in letters
        if letter.step == FIRST_STEP and letter.status is MessageStatus.QUEUED
    ]
    for letter in queued:  # строки письма под замком: отправка взять его не успеет
        await off_queue.take_off(session, letter, off_queue.closed(UNDONE_NOTE))
    await clean_write.write(session, [_rejected(lead_id)])
    async with _waiting(session, f"компания лида №{lead_id}"):  # лиды других гипотез
        await company_hold.freed(session, {lead.domain_id})
    return LeadOff(lead_id, lead.email, letter_off=bool(queued), was=was)


def _rejected(lead_id: int) -> dict[str, object]:
    """Исход снятого лида: «отклонён», причина — отмена решения; срок перепроверки адреса снят."""
    return {
        clean_write.LEAD_ID: lead_id,
        "status": LeadStatus.REJECTED,
        "rejection_reason": RejectionReason.UNDONE.value,
        "cleaning_note": UNDONE_NOTE,
        "verify_since": None,
        "verify_again_at": None,
    }


def _not_human(candidate: Candidate) -> Undone:
    """Сайт решил не человек: ждёт человека — повтор отмены; иначе — отказ словами."""
    if candidate.lane in AWAITS_HUMAN:
        return Undone(candidate)
    if candidate.decided_by is None:
        raise UndoRefusedError(NOT_JUDGED)
    raise UndoRefusedError(NOT_HUMAN[Decider(candidate.decided_by)])


async def undo(session: AsyncSession, candidate_id: int) -> Undone | None:
    """Отменить решение человека по сайту — и снять его лида, пока письмо лиду не ушло (`_lead_off`);
    `None` — сайта нет. Коммит — за вызывающим: отказ словами — до первой записи, «занято» на
    лидах других гипотез (`company_hold.freed`) — после снятия лида, и записанное откатывает он."""
    locked = await _locked(session, candidate_id)
    if locked is None:
        return None
    candidate, mode, letters = locked
    if candidate.decided_by != Decider.HUMAN:
        return _not_human(candidate)
    lead = None if candidate.lead_id is None else await _lead_off(session, candidate.lead_id)
    found, at = (await _before(session, [candidate], JudgeMode(mode), letters))[candidate.id]
    was = candidate.lane
    # Время решения — судьи, а не отмены; колонки судьи отмена не пишет — их пишет только судья.
    values = found.values(at) | {"decided_by_user": None, "lead_id": None}
    for name, value in values.items():
        setattr(candidate, name, value)
    logger.info(
        "сбор продаж: решение по сайту отменено",
        extra={"id": candidate_id, "was": was, "lead": None if lead is None else lead.lead_id},
    )
    return Undone(candidate, was, lead)
