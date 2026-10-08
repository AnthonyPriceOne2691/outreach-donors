"""Ответ лида продаж: задача своей очереди (`sales`) разбирает вид и ведёт по пути.

Приём общий с донорами: вебхук, повтор, привязка, правила вида, последствия
(`replies/pipeline.py`). Ответ человека в треде продаж уходит сюда задачей
`queue.SALES_REPLY_JOB` — своей очередью и своим воркером, а не очередью
прогонов: часовой прогон доноров держал бы ответ лида до часа.

**Вид называет модель, путь решает код** (урок соседней системы: «нужен ли
ответ» решает код). Таблица путей — `ROUTES`; ниже порога уверенности
(`SALES_REPLY_CONFIDENCE`) — ручная очередь продаж, какой бы вид ни назвали.

**Ждёт ли ответ человека — производное, как у доноров.** Задача кладёт
в снимок ответа (`model_parse`) вид, путь, «ждёт ли» и причину словами;
диалоги читают их там (`replies.outcome.sales_review`), почта модуля продаж
не знает.

**Без модели — то, что вид уже решили правила приёма**: автоответ переносит
следующий шаг (`ooo.py`), отписка закрывает адрес во всех направлениях
(`unsubscribe.py`).

**«Хочет говорить» — передача лида телемаркетологу** (`handoff.start`, срез 5.3) —
только после записи ответа (`pass_on`): передача коммитит сама и ставит задачу,
которая идёт в Kommo и Telegram. Её отказ разбор не роняет: вид уже записан, ответ
ждёт человека, а почему передачи нет — словами в снимке ответа.

**Вопрос и интерес — черновик агента продаж** (`DRAFTED`): задача ответа ставит его после
записи вида своей очередью (`workers/sales_jobs.py`), если агент продаж включён тумблером
(`SALES_AGENT_ENABLED`) и настроен. Иначе ответ ждёт человека, как ждал.

**«Пишите другому»** заводит лида той же компании (`referral.py`) и закрывает
диалог. Проверка адресов не настроена — лида заводит человек: вид уже назван
и оплачен, а упавшая задача откатила бы расход и позвала модель снова.

**Задача не верит, что её поставили по делу.** Задача живёт в очереди дольше
кода и может прийти ко второму ответу, к удалённому, к уже разобранному или
решённому человеком: каждый такой случай — итог словами, а не платный вызов.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, assert_never

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import sales as cfg
from backend.config.startup_checks import ConfigError
from backend.features.core import usage
from backend.features.core.domain import ReplyKind, Stage, ThreadStatus
from backend.features.core.models.outreach import ReplyModel, ThreadModel
from backend.features.replies import outcome
from backend.features.replies.repository import ReplyRepository
from backend.features.sales import handoff, ooo
from backend.features.sales.referral import Referred, refer
from backend.features.sales.reply_kind import (
    OPERATION,
    PROMPT_VERSION,
    KindFound,
    SalesKind,
    Unanswered,
    snapshot,
)
from backend.features.sales.unsubscribe import close_address
from backend.features.sales.usage_cap import sales_cap
from backend.features.sales.verifier import EmailVerifier

logger = logging.getLogger(__name__)


class Route(StrEnum):
    """Куда ответ уходит после вида."""

    HANDOFF = "handoff"  # хочет говорить — передача лида на созвон
    AGENT = "agent"  # вопрос, интерес — ответит агент; пока — человек
    REFERRAL = "referral"  # назвал другого — новый лид
    CLOSED = "closed"  # не интересно, не сейчас — диалог закрыт
    UNSUBSCRIBE = "unsubscribe"  # просит не писать — адрес закрыт
    MANUAL = "manual"  # вид не разобран, ниже порога, модель не ответила


#: Путь вида при уверенности не ниже порога.
ROUTES: dict[SalesKind, Route] = {
    SalesKind.WANTS_TO_TALK: Route.HANDOFF,
    SalesKind.QUESTION: Route.AGENT,
    SalesKind.INTERESTED: Route.AGENT,
    SalesKind.REFERRAL: Route.REFERRAL,
    SalesKind.NOT_INTERESTED: Route.CLOSED,
    SalesKind.NOT_NOW: Route.CLOSED,
    SalesKind.UNSUBSCRIBE: Route.UNSUBSCRIBE,
    SalesKind.PARSE_FAILED: Route.MANUAL,
}

#: Пути, после которых ответ человека не ждёт (если путь удался — `_follow`).
SETTLED = frozenset({Route.CLOSED, Route.REFERRAL, Route.UNSUBSCRIBE})

#: Пути, после которых агент продаж готовит черновик ответа (`workers/sales_jobs.py`), если
#: он включён тумблером `SALES_AGENT_ENABLED` и настроен: «ответит агент» — вопрос и интерес.
#: «Хочет говорить» — без черновика: лида передают телемаркетологу (`pass_on`); решение ждёт
#: подтверждения владельца, и черновик там — `Route.HANDOFF` в этот набор, одной строкой.
DRAFTED = frozenset({Route.AGENT})

#: Вид словами — для причины в карточке.
KIND_WORDS: dict[SalesKind, str] = {
    SalesKind.WANTS_TO_TALK: "хочет говорить",
    SalesKind.QUESTION: "задал вопрос",
    SalesKind.INTERESTED: "интересуется",
    SalesKind.REFERRAL: "назвал другого человека",
    SalesKind.NOT_INTERESTED: "не интересно",
    SalesKind.NOT_NOW: "не сейчас",
    SalesKind.UNSUBSCRIBE: "просит не писать",
    SalesKind.PARSE_FAILED: "вид ответа не разобран",
}

#: Чем кончился путь «хочет говорить», когда передача заведена, — словами.
HANDED_OVER = "передан телемаркетологу"
#: И когда передачи нет — дальше в причине словами, почему (`_not_handed_over`).
NOT_HANDED_OVER = "передать лида не вышло"

#: Что дальше по пути — словами.
ROUTE_WORDS: dict[Route, str] = {
    Route.HANDOFF: "передать лида на созвон; пока — человек",
    Route.AGENT: "ответит агент; пока — человек",
    Route.REFERRAL: "новый лид на названный адрес",
    Route.CLOSED: "диалог закрыт",
    Route.UNSUBSCRIBE: "адрес закрыт во всех направлениях",
    Route.MANUAL: "решает человек",
}


@dataclass(frozen=True, slots=True)
class Decision:
    """Путь ответа, ждёт ли он человека и почему — словами."""

    route: Route
    waits: bool
    reason: str


def _percent(share: float) -> int:
    return round(share * 100)


def decide(found: KindFound, threshold: float) -> Decision:
    """Путь по виду — решение кода, без базы. Ниже порога — человек."""
    words = KIND_WORDS[found.kind]
    notes = "".join(f"; {note}" for note in found.notes)
    if found.kind is SalesKind.PARSE_FAILED:
        return Decision(Route.MANUAL, True, f"{words}{notes} — {ROUTE_WORDS[Route.MANUAL]}")
    if found.confidence < threshold:
        level = f"уверенность {_percent(found.confidence)}% ниже порога {_percent(threshold)}%"
        return Decision(Route.MANUAL, True, f"модель: {words}, {level}{notes} — решает человек")
    route = ROUTES[found.kind]
    return Decision(route, route not in SETTLED, f"{words}: {ROUTE_WORDS[route]}")


class KindClassifier(Protocol):
    """Кто называет вид: вызов модели (`reply_kind.KindClient`) или подстава теста."""

    @property
    def model(self) -> str: ...

    async def classify(self, *, text: str, subject: str) -> KindFound | Unanswered: ...


#: Передача лида по треду — `handoff.start(session, thread_id)`: коммитит сессию сама
#: и ставит задачу передачи (Kommo, Telegram), поэтому зовётся после записи ответа.
HandOver = Callable[[AsyncSession, int], Awaitable[object]]


@dataclass(frozen=True, slots=True)
class Handled:
    """Чем кончилась задача одного ответа."""

    reply_id: int
    #: Почему задача ответ не взяла — словами. Пусто — взяла.
    skipped: str | None = None
    kind: str | None = None
    route: str | None = None
    #: Ждёт ли ответ человека и почему.
    waits: bool = False
    reason: str | None = None
    tokens: int = 0
    #: Модель не ответила — вида нет. Пусто — ответила.
    unanswered: Unanswered | None = None
    #: Диалог, лида которого передать телемаркетологу после записи ответа (`pass_on`).
    handoff_thread: int | None = None

    @property
    def drafted(self) -> bool:
        """Положен ли ответу черновик агента по его пути (`DRAFTED`). Включён ли агент
        продаж и настроен ли — решает шов (`drafting.wants_draft`)."""
        return self.route in DRAFTED

    @property
    def as_report(self) -> dict[str, object]:
        """Итог задачи для очереди и журнала."""
        if self.skipped is not None:
            return {"reply": self.reply_id, "skipped": self.skipped}
        return {
            "reply": self.reply_id,
            "kind": self.kind,
            "route": self.route,
            "waits": self.waits,
            "reason": self.reason,
            "tokens": self.tokens,
        }


class SalesReplies:
    """Ответы продаж, взятые задачей очереди."""

    def __init__(
        self,
        session: AsyncSession,
        classifier: KindClassifier,
        *,
        hand_over: HandOver = handoff.start,
        verifier: Callable[[], EmailVerifier] | None = None,
        threshold: float | None = None,
        now: datetime | None = None,
        last_try: bool = False,
    ) -> None:
        self._session = session
        self._repo = ReplyRepository(session)
        self._classifier = classifier
        self._hand_over = hand_over
        self._verifier = verifier
        self._threshold = cfg.REPLY_CONFIDENCE if threshold is None else threshold
        self._now = now
        #: Последняя попытка задачи очереди: повтора после временного отказа модели не будет.
        self._last_try = last_try

    def _moment(self) -> datetime:
        return self._now or datetime.now(UTC)

    async def handle(self, reply_id: int) -> Handled:
        """Разобрать вид ответа и повести его по пути. Потолок модели — общий или свой у
        продаж (`usage_cap.sales_cap`) — исключение `LlmCapExceededError`: задача ставит себя
        на завтра (`workers/sales_jobs.py`)."""
        reply = await self._session.get(ReplyModel, reply_id)
        if reply is None:
            logger.warning("продажи: ответа нет — задаче нечего делать", extra={"reply": reply_id})
            return Handled(reply_id, skipped="ответа нет: удалён до разбора")
        skipped = await self._not_ours(reply)
        if skipped is not None:
            logger.info("продажи: ответ не взят", extra={"reply": reply_id, "why": skipped})
            return Handled(reply_id, skipped=skipped)
        match reply.kind:
            case ReplyKind.AUTO_REPLY:
                return await self._out_of_office(reply)
            case ReplyKind.UNSUBSCRIBE:
                closed = await close_address(self._session, reply)
                return Handled(reply.id, kind=reply.kind.value, reason=closed.words)
            case _:
                pass

        await usage.ensure_llm_within_cap(self._session, own=sales_cap())
        found = await self._classifier.classify(text=reply.raw_body, subject=reply.subject or "")
        if isinstance(found, Unanswered):
            return self._unanswered(reply, found)
        if found.tokens:
            usage.record(self._session, operation=OPERATION, units=found.tokens)
        decision = await self._follow(reply, found, decide(found, self._threshold))
        reply.model_parse = self._record(snapshot(found, model=self._classifier.model), decision)
        reply.confidence = found.confidence
        logger.info(
            "продажи: вид ответа разобран",
            extra={"reply": reply.id, "kind": found.kind.value, "route": decision.route.value},
        )
        return Handled(
            reply.id,
            kind=found.kind.value,
            route=decision.route.value,
            waits=decision.waits,
            reason=decision.reason,
            tokens=found.tokens,
            handoff_thread=reply.thread_id if decision.route is Route.HANDOFF else None,
        )

    async def pass_on(self, handled: Handled) -> Handled:
        """Передать лида, если ответ «хочет говорить», — после коммита снимка ответа.

        Внешнее — только после записи ответа: передача коммитит сессию сама и ставит
        задачу, которая идёт в Kommo и Telegram. Отказ передачи разбор не роняет —
        вид уже записан: заведённую строку передачи повторит её проход по расписанию,
        а без строки (нет лида у диалога) ответ ждёт человека по снимку. Сессию после
        отказа задача только закрывает — открытая транзакция откатится с ней.

        Удачная передача снимает с ответа ожидание человека тем же снимком, по которому
        его ждут (`waits` и причина словами — `outcome.sales_review`): иначе ответ висел
        бы в «ждут человека», и человек написал бы лиду, которому уже звонят. Поля
        решения человека (`reviewed_*`) не трогаются: решала не она.
        """
        if handled.handoff_thread is None:
            return handled
        try:
            await self._hand_over(self._session, handled.handoff_thread)
        except Exception as exc:
            logger.exception(
                "продажи: передача лида не заведена — ответ разобран и ждёт человека",
                extra={"reply": handled.reply_id, "thread_id": handled.handoff_thread},
            )
            return await self._not_handed_over(handled, exc)
        settled = f"{KIND_WORDS[SalesKind.WANTS_TO_TALK]}: {HANDED_OVER}"
        reply = await self._session.get(ReplyModel, handled.reply_id)
        if reply is not None:
            reply.model_parse = {**(reply.model_parse or {}), "waits": False, "reason": settled}
            await self._session.commit()
        logger.info(
            "продажи: лид передан телемаркетологу — ответ человека не ждёт",
            extra={"reply": handled.reply_id, "thread_id": handled.handoff_thread},
        )
        return replace(handled, waits=False, reason=settled)

    async def _not_handed_over(self, handled: Handled, exc: Exception) -> Handled:
        """Передачи нет: ответ ждёт человека, а почему — словами в снимке ответа, а не только
        в журнале (ревью стыков, B5): на экране видно, что чинить — разобрать лидов-дублей,
        завести лида. Отказ передачи (`HandoffError`) говорит сам; чужой сбой — тип и журнал.
        Сессия после сбоя базы не годится и для этой записи — тогда причина только в журнале.
        """
        why = (
            str(exc)
            if isinstance(exc, handoff.HandoffError)
            else f"сбой ({type(exc).__name__}), подробности — в журнале задачи"
        )
        words = KIND_WORDS[SalesKind.WANTS_TO_TALK]
        reason = f"{words}: {NOT_HANDED_OVER} — {why}; решает человек"
        try:
            reply = await self._session.get(ReplyModel, handled.reply_id)
            if reply is None:
                return handled
            reply.model_parse = {
                **(reply.model_parse or {}),
                "reason": reason,
                "handoff_error": why,
            }
            await self._session.commit()
        except SQLAlchemyError:
            logger.exception(
                "продажи: почему передачи нет — в снимок ответа не записать, причина в журнале",
                extra={"reply": handled.reply_id},
            )
            return handled
        return replace(handled, reason=reason)

    async def _out_of_office(self, reply: ReplyModel) -> Handled:
        """Автоответ: цепочка идёт, следующий шаг — не раньше возвращения."""
        moved = await ooo.postpone(
            self._session, reply, delay_days=cfg.OOO_DELAY_DAYS, now=self._moment()
        )
        logger.info(
            "продажи: автоответ — следующий шаг перенесён",
            extra={"reply": reply.id, "until": moved.until.isoformat(), "moved": moved.moved},
        )
        return Handled(reply.id, kind=reply.kind.value, reason=moved.words)

    async def _not_ours(self, reply: ReplyModel) -> str | None:
        """Почему задаче этот ответ не брать. `None` — брать.

        Записка об отказе модели (`kind` пуст) — не вид: такой ответ берётся снова.
        """
        if reply.reviewed_at is not None:
            return "решён человеком"
        if (reply.model_parse or {}).get("kind"):
            return "уже разобран"
        stage = await self._repo.stage_of(reply)
        if stage is not Stage.SALES:
            return "не ответ продаж"
        if not outcome.to_sales_queue(reply.kind, stage):
            return f"вид «{reply.kind.value}» решают правила приёма"
        return None

    def _unanswered(self, reply: ReplyModel, missing: Unanswered) -> Handled:
        """Модель не ответила: вида нет, ответ ждёт человека с причиной.

        Это записка, а не снимок вида: `kind` пуст, и следующая попытка —
        повтор задачи или ручная постановка — разберёт ответ заново. Повтор
        записка обещает, только если он будет: на последней попытке задачи —
        «повторы кончились».
        """
        if missing.permanent:
            then = "разберите вручную"
        elif self._last_try:
            then = "повторы кончились — разберите вручную"
        else:
            then = "задача попробует ещё раз"
        reason = f"{missing.reason} — {then}"
        decision = Decision(Route.MANUAL, True, reason)
        note = {"kind": None, "refusal": missing.reason, "prompt_version": PROMPT_VERSION}
        reply.model_parse = self._record(
            {"stage": Stage.SALES.value, **note, "model": self._classifier.model}, decision
        )
        logger.warning(
            "продажи: модель не ответила — вид не записан",
            extra={"reply": reply.id, "permanent": missing.permanent},
        )
        return Handled(
            reply.id, route=Route.MANUAL.value, waits=True, reason=reason, unanswered=missing
        )

    @staticmethod
    def _record(base: dict[str, Any], decision: Decision) -> dict[str, Any]:
        return {
            **base,
            "route": decision.route.value,
            "waits": decision.waits,
            "reason": decision.reason,
        }

    async def _follow(self, reply: ReplyModel, found: KindFound, decision: Decision) -> Decision:
        """Что путь делает сразу — и что из этого вышло. Ждущие человека пути не пишут;
        передача лида — не здесь, а после записи ответа (`pass_on`)."""
        words = KIND_WORDS[found.kind]
        match decision.route:
            case Route.CLOSED:
                await self._close(reply.thread_id)
            case Route.REFERRAL:
                referred = await self._refer(reply, found.contact)
                return Decision(Route.REFERRAL, referred.waits, f"{words}: {referred.words}")
            case Route.UNSUBSCRIBE:
                closed = await close_address(self._session, reply)
                return Decision(Route.UNSUBSCRIBE, closed.email is None, f"{words}: {closed.words}")
            case Route.HANDOFF | Route.AGENT | Route.MANUAL:
                pass
            case _:
                assert_never(decision.route)
        return decision

    async def _refer(self, reply: ReplyModel, contact: str | None) -> Referred:
        """Лид из «пишите другому». Неверная настройка проверки адресов всплывает здесь,
        уже после вызова модели, — поэтому не роняет задачу: откат стёр бы расход, а повтор
        очереди позвал бы модель снова. Лида заводит человек."""
        if self._verifier is None:
            return Referred(None, "проверка адресов задаче не дана — завести лида руками")
        try:
            verifier = self._verifier()
        except ConfigError:
            logger.exception(
                "продажи: проверка адресов не настроена — лида из ответа заведёт человек",
                extra={"reply": reply.id},
            )
            return Referred(None, "проверка адресов не настроена — завести лида руками")
        return await refer(self._session, reply.thread_id, contact, verifier, now=self._moment())

    async def _close(self, thread_id: int | None) -> None:
        """Закрыть диалог: «не интересно» и «не сейчас» — без давления. Отписку
        закрытие не перебивает: она сильнее."""
        thread = await self._session.get(ThreadModel, thread_id) if thread_id else None
        if thread is not None and thread.status in (ThreadStatus.OPEN, ThreadStatus.REPLIED):
            thread.status = ThreadStatus.CLOSED
