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
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, assert_never

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import sales as cfg
from backend.config.startup_checks import ConfigError
from backend.features.core import usage
from backend.features.core.domain import ReplyKind, Stage, ThreadStatus
from backend.features.core.models.outreach import ReplyModel, ThreadModel
from backend.features.replies import outcome
from backend.features.replies.repository import ReplyRepository
from backend.features.sales import ooo
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


#: Передача лида по треду (вход среза передачи: `handoff.start(session, thread_id)`).
HandOver = Callable[[AsyncSession, int], Awaitable[None]]


async def mark_for_handoff(_session: AsyncSession, thread_id: int) -> None:
    """Точка передачи лида: сюда при сборке встанет `handoff.start(session, thread_id)`.

    Пока передачи в дереве нет, пометка — путь `handoff` в снимке ответа,
    и ответ ждёт человека. Сессия — та же, что у задачи: передача должна лечь
    в одну транзакцию со снимком.
    """
    logger.info(
        "продажи: лид хочет говорить — передача ждёт своего среза", extra={"thread_id": thread_id}
    )


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
        hand_over: HandOver = mark_for_handoff,
        verifier: Callable[[], EmailVerifier] | None = None,
        threshold: float | None = None,
        now: datetime | None = None,
    ) -> None:
        self._session = session
        self._repo = ReplyRepository(session)
        self._classifier = classifier
        self._hand_over = hand_over
        self._verifier = verifier
        self._threshold = cfg.REPLY_CONFIDENCE if threshold is None else threshold
        self._now = now

    def _moment(self) -> datetime:
        return self._now or datetime.now(UTC)

    async def handle(self, reply_id: int) -> Handled:
        """Разобрать вид ответа и повести его по пути. Потолок модели — исключение
        `LlmCapExceededError`: задача ставит себя на завтра (`workers/sales_jobs.py`)."""
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

        await usage.ensure_llm_within_cap(self._session)
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
        )

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
        повтор задачи или ручная постановка — разберёт ответ заново.
        """
        then = "разберите вручную" if missing.permanent else "задача попробует ещё раз"
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
        """Что путь делает сразу — и что из этого вышло. Ждущие человека пути не пишут."""
        words = KIND_WORDS[found.kind]
        match decision.route:
            case Route.HANDOFF:
                if reply.thread_id is not None:
                    await self._hand_over(self._session, reply.thread_id)
            case Route.CLOSED:
                await self._close(reply.thread_id)
            case Route.REFERRAL:
                referred = await self._refer(reply, found.contact)
                return Decision(Route.REFERRAL, referred.waits, f"{words}: {referred.words}")
            case Route.UNSUBSCRIBE:
                closed = await close_address(self._session, reply)
                return Decision(Route.UNSUBSCRIBE, closed.email is None, f"{words}: {closed.words}")
            case Route.AGENT | Route.MANUAL:
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
