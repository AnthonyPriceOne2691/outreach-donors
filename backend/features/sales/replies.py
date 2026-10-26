"""Ответ лида продаж: задача своей очереди (`sales`) разбирает вид и ведёт по пути.

Приём общий с донорами: вебхук, повтор, привязка, правила вида, последствия
(`replies/pipeline.py`). Ответ человека в треде продаж уходит сюда задачей
`queue.SALES_REPLY_JOB` — своей очередью и своим воркером, а не очередью
прогонов: часовой прогон доноров держал бы ответ лида до часа.

**Вид называет модель, путь решает код** (`reply_routes.py`): таблица путей и порог
уверенности (`SALES_REPLY_CONFIDENCE`) — ниже него ручная очередь продаж, какой бы вид ни
назвали.

**Ждёт ли ответ человека — производное, как у доноров.** Задача кладёт
в снимок ответа (`model_parse`) вид, путь, «ждёт ли» и причину словами;
диалоги читают их там (`replies.outcome.sales_review`), почта модуля продаж
не знает.

**Без модели — то, что вид уже решили правила приёма**: автоответ переносит
следующий шаг (`ooo.py`), отписка закрывает адрес во всех направлениях — а если
ответил не лид, и адрес лида (`unsubscribe.py`).

**«Хочет говорить» — передача лида телемаркетологу** (`handoff.start`, срез 5.3) —
только после записи ответа (`pass_on`, `reply_handoff.py`): передача коммитит сама и ставит
задачу, которая идёт в Kommo и Telegram. Её отказ разбор не роняет: вид уже записан, ответ
ждёт человека, а почему передачи нет — словами в снимке ответа.

**Новое письмо лида, уже переданного телемаркетологу**, — любое, кроме автоответа (отписка
словами — тоже: стоп-лист, как был, и телемаркетологу надо знать), — уходит той же передачей
тем же путём после записи ответа: примечание к той же сделке и сообщение «Лид написал снова»
(`reply_handoff.py`, `handoff_telegram.py`). Ответ с ожидания снимается словами. Потолок модели
письмо не держит: оно уходит до переноса вида на завтра (`forward_unread`).

**Вопрос и интерес — черновик агента продаж** (`DRAFTED`): задача ответа ставит его после
записи вида своей очередью (`workers/sales_jobs.py`), если агент продаж включён тумблером
(`SALES_AGENT_ENABLED`) и настроен. Иначе ответ ждёт человека, как ждал. Лиду, которому не
пишут (снят, у телемаркетолога, в стоп-листе — `mail.unwritable`, то же правило, что у
отправки), черновика нет: модель не платит за письмо, которое не уйдёт, а почему черновика
нет — словами в снимке ответа. Агент ведёт первые ответы лида в диалоге
(`SALES_AGENT_MAX_TURNS`, `agent_turns.py`): дальше черновика нет — отвечает человек.

**«Пишите другому»** заводит лида той же компании (`referral.py`) — с именем, если его
назвали, — и закрывает диалог; прошёл очистку — письмо ему собирает та же задача сборки,
только для него, сразу после записи ответа (`write_to_referred`). Проверка адресов не
настроена — лида заводит человек: вид уже назван и оплачен, а упавшая задача откатила бы
расход и позвала модель снова.

**Задача не верит, что её поставили по делу.** Задача живёт в очереди дольше
кода и может прийти ко второму ответу, к удалённому, к уже разобранному или
решённому человеком: каждый такой случай — итог словами, а не платный вызов.

**Продажи выключены (`SALES_ENABLED`) — ни модели, ни передачи** (решение владельца по
ревью стыков). Приём ответ принимает, как всегда; задача модель не зовёт — ни ради вида,
ни ради отписки словами (это тоже вид от модели), — и ответ ждёт человека словами
`SWITCHED_OFF`. Записка, а не снимок вида: задача, поставленная после включения, разберёт
ответ заново.
Что правила приёма решили без модели (автоответ, отписка правилами), идёт как шло.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol, assert_never

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import sales as cfg
from backend.features.core import usage
from backend.features.core.domain import ReplyKind, Stage, ThreadStatus
from backend.features.core.models.outreach import ReplyModel, ThreadModel
from backend.features.replies import outcome
from backend.features.replies.repository import ReplyRepository
from backend.features.sales import agent_turns, handoff, kb, ooo, queue_jobs, reply_handoff
from backend.features.sales.connection import MODULE_OFF
from backend.features.sales.dead_mailbox import KIND, colleague
from backend.features.sales.mail import linked, unwritable
from backend.features.sales.no_draft import absent as agent_absent
from backend.features.sales.referral import Referred, from_answer
from backend.features.sales.reply_handoff import Ask, HandOver
from backend.features.sales.reply_kind import (
    OPERATION,
    PROMPT_VERSION,
    KindFound,
    SalesKind,
    Unanswered,
    snapshot,
)
from backend.features.sales.reply_routes import DRAFTED, KIND_WORDS, Decision, Route, decide
from backend.features.sales.unsubscribe import close_address
from backend.features.sales.usage_cap import sales_cap
from backend.features.sales.verifier import EmailVerifier

logger = logging.getLogger(__name__)

#: Почему ответ ждёт человека, когда продажи выключены (`SALES_ENABLED`): модель не звалась.
#: Словами человека, без имени настройки — причину читают на экране диалога.
SWITCHED_OFF = f"{MODULE_OFF} — ответ ждёт человека"

#: Почему вид ответа не разобран сейчас: потолок расхода на модель — задача перенесена на завтра.
CAPPED = "потолок расхода на модель — вид ответа разберётся завтра"


class KindClassifier(Protocol):
    """Кто называет вид: вызов модели (`reply_kind.KindClient`) или подстава теста."""

    @property
    def model(self) -> str: ...

    async def classify(
        self, *, text: str, subject: str, services: Sequence[str] = ()
    ) -> KindFound | Unanswered: ...


#: Письмо одному лиду — та же задача сборки (`queue_jobs.build_for(hypothesis_id, lead_id)`).
BuildFor = Callable[[int, int], object]


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
    #: Лид уже передан: письмо уходит той же передачей (`reply_handoff.Ask.again`).
    again: bool = False
    #: Почему черновика агента нет, хотя путь его даёт: лиду не пишут (`mail.unwritable`) или
    #: агент уже вёл свои ответы в диалоге (`agent_turns.beyond`).
    no_draft: str | None = None
    #: Новый лид из «пишите другому», готовый к письму: (гипотеза, лид) — письмо ему собирает
    #: задача сборки после записи ответа (`write_to_referred`).
    referred: tuple[int, int] | None = None

    @property
    def drafted(self) -> bool:
        """Положен ли ответу черновик агента по его пути (`DRAFTED`) и лиду (`no_draft`); лиду,
        уже переданному телемаркетологу, — нет: письмо ушло ему (`again`). Включён ли агент
        продаж и настроен ли — решает шов (`drafting.wants_draft`)."""
        return self.route in DRAFTED and self.no_draft is None and not self.again

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
        build_for: BuildFor | None = None,
        verifier: Callable[[], EmailVerifier] | None = None,
        threshold: float | None = None,
        now: datetime | None = None,
        last_try: bool = False,
    ) -> None:
        self._session = session
        self._repo = ReplyRepository(session)
        self._classifier = classifier
        self._hand_over = hand_over
        #: Письмо новому лиду из «пишите другому» — задача сборки (`queue_jobs.build_for`).
        self._build_for = queue_jobs.build_for if build_for is None else build_for
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
        again = await self._handed(reply)
        done = await self._without_model(reply, again)
        if done is not None:
            return done

        await usage.ensure_llm_within_cap(self._session, own=sales_cap())
        services = await kb.service_titles(self._session)
        found = await self._classifier.classify(
            text=reply.raw_body, subject=reply.subject or "", services=services
        )
        if isinstance(found, Unanswered):
            return replace(self._unanswered(reply, found), **_forwarded(reply, again))
        if found.tokens:
            usage.record(self._session, operation=OPERATION, units=found.tokens)
        decision, referred = await self._follow(reply, found, decide(found, self._threshold))
        decision, no_draft = await self._no_draft(reply, found, decision)
        reply.model_parse = self._record(snapshot(found, model=self._classifier.model), decision)
        reply.confidence = found.confidence
        logger.info(
            "продажи: вид ответа разобран",
            extra={"reply": reply.id, "kind": found.kind.value, "route": decision.route.value},
        )
        handled = Handled(
            reply.id,
            kind=found.kind.value,
            route=decision.route.value,
            waits=decision.waits,
            reason=decision.reason,
            tokens=found.tokens,
            handoff_thread=reply.thread_id if decision.route is Route.HANDOFF else None,
            no_draft=no_draft,
            referred=referred,
        )
        return replace(handled, **_forwarded(reply, again))

    async def _handed(self, reply: ReplyModel) -> bool:
        """Лид диалога уже передан телемаркетологу — письмо уйдёт той же передачей. Продажи
        выключены — нет: передача стоит (`handoff.start`)."""
        return cfg.ENABLED and await reply_handoff.handed(self._session, reply.thread_id)

    async def _no_draft(
        self, reply: ReplyModel, found: KindFound, decision: Decision
    ) -> tuple[Decision, str | None]:
        """Путь даёт черновик агента, а лиду диалога не пишут — то же правило, что у отправки
        (`mail.unwritable`): черновика нет, почему — словами в причине ответа. Диалог без лида
        продаж правило не судит. Агент уже вёл свои ответы лида в диалоге (`agent_turns`) —
        черновика тоже нет: дальше отвечает человек. Шов черновик не поставит (диалог закрыт,
        агент продаж не настроен) — тоже словами (`no_draft.absent`)."""
        if decision.route not in DRAFTED:
            return decision, None
        dialog = await linked(self._session, reply.thread_id)
        why = None if dialog is None else await unwritable(self._session, dialog)
        if why is not None:
            reason = f"{KIND_WORDS[found.kind]}: {why}; черновика агента нет — решает человек"
            return replace(decision, reason=reason), why
        why = await agent_turns.beyond(self._session, reply)
        why = why or await agent_absent(self._session, reply)
        if why is None:
            return decision, None
        return replace(decision, reason=f"{KIND_WORDS[found.kind]}: {why}"), why

    async def pass_on(self, handled: Handled) -> Handled:
        """Передать лида, если ответ «хочет говорить», а письмо переданного лида — той же
        передачей (`handled.again`), — после коммита снимка ответа (`reply_handoff.pass_on`).

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
        said = await reply_handoff.pass_on(self._session, _ask(handled), self._hand_over)
        if said is None:
            return handled
        extra = {"reply": handled.reply_id, "thread_id": handled.handoff_thread}
        if said.handed and handled.again:
            logger.info("продажи: письмо переданного лида — телемаркетологу", extra=extra)
        elif said.handed:
            logger.info(
                "продажи: лид передан телемаркетологу — ответ человека не ждёт", extra=extra
            )
        return replace(handled, waits=said.waits, reason=said.reason)

    async def forward_unread(self, reply_id: int) -> None:
        """Потолок модели: вид ответа — завтра, а письмо переданного лида уходит телемаркетологу
        сейчас — записка вместо вида (`kind` пуст: завтра ответ разберут заново) и передача."""
        reply = await self._session.get(ReplyModel, reply_id)
        if reply is None or not cfg.ENABLED:
            return
        if not await reply_handoff.handed(self._session, reply.thread_id):
            return
        decision = Decision(Route.MANUAL, True, CAPPED)
        reply.model_parse = self._record({"stage": Stage.SALES.value, "kind": None}, decision)
        await self._session.commit()
        handled = Handled(reply.id, route=Route.MANUAL.value, waits=True, reason=CAPPED)
        await self.pass_on(replace(handled, **_forwarded(reply, again=True)))

    async def write_to_referred(self, handled: Handled) -> None:
        """Письмо новому лиду из «пишите другому» — сразу, той же задачей сборки, только ему.
        После записи ответа: задача должна видеть лида. Очередь не ответила — строка в журнал:
        лид `ready`, письмо соберёт сборка гипотезы с кнопки."""
        if handled.referred is None:
            return
        hypothesis_id, lead_id = handled.referred
        try:
            self._build_for(hypothesis_id, lead_id)
        except Exception as exc:  # noqa: BLE001 — лид записан, письмо соберёт кнопка
            logger.warning(
                "продажи: письмо новому лиду не поставлено — соберёт сборка гипотезы",
                extra={"reply": handled.reply_id, "lead_id": lead_id, "error": str(exc)},
            )
            return
        logger.info(
            "продажи: письмо новому лиду из «пишите другому» — в сборку",
            extra={"reply": handled.reply_id, "lead_id": lead_id},
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
        if not outcome.to_sales_queue(reply.kind, stage) and reply.kind is not ReplyKind.BOUNCE:
            return f"вид «{reply.kind.value}» решают правила приёма"
        return None

    async def _without_model(self, reply: ReplyModel, again: bool) -> Handled | None:
        """Что решается без модели: автоответ и отписка — как их узнали правила приёма
        (отписку переданного лида — ещё и телемаркетологу, `again`; автоответ — нет);
        выключенные продажи — ответ ждёт человека. `None` — вид называет модель."""
        match reply.kind:
            case ReplyKind.AUTO_REPLY:
                return await self._out_of_office(reply)
            case ReplyKind.UNSUBSCRIBE:
                closed = await close_address(self._session, reply)
                done = Handled(reply.id, kind=reply.kind.value, reason=closed.words)
                return replace(done, **_forwarded(reply, again)) if again else done
            case ReplyKind.BOUNCE:  # мёртвый ящик лида назвал адрес коллеги
                return await self._dead_mailbox(reply)
            case _:
                pass
        return None if cfg.ENABLED else self._switched_off(reply)

    def _switched_off(self, reply: ReplyModel) -> Handled:
        """Продажи выключены: модель не зовётся, ответ ждёт человека словами. Записка, а не
        снимок вида (`kind` пуст): задача, поставленная после включения, разберёт его заново."""
        decision = Decision(Route.MANUAL, True, SWITCHED_OFF)
        reply.model_parse = self._record({"stage": Stage.SALES.value, "kind": None}, decision)
        logger.info(
            "продажи: выключены — вид ответа не разобран, ждёт человека", extra={"reply": reply.id}
        )
        return Handled(reply.id, route=Route.MANUAL.value, waits=True, reason=SWITCHED_OFF)

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

    async def _follow(
        self, reply: ReplyModel, found: KindFound, decision: Decision
    ) -> tuple[Decision, tuple[int, int] | None]:
        """Что путь делает сразу — и что из этого вышло; вторым — новый лид, готовый к письму.
        Ждущие человека пути не пишут; передача лида — не здесь, а после записи ответа (`pass_on`)."""
        words = KIND_WORDS[found.kind]
        match decision.route:
            case Route.CLOSED:
                await self._close(reply.thread_id)
            case Route.REFERRAL:
                referred = await self._refer(reply)(found.contact, found.contact_name)
                said = Decision(Route.REFERRAL, referred.waits, f"{words}: {referred.words}")
                return said, referred.ready
            case Route.UNSUBSCRIBE:
                closed = await close_address(self._session, reply)
                said = Decision(Route.UNSUBSCRIBE, not closed.emails, f"{words}: {closed.words}")
                return said, None
            case Route.HANDOFF | Route.AGENT | Route.MANUAL:
                pass
            case _:
                assert_never(decision.route)
        return decision, None

    async def _dead_mailbox(self, reply: ReplyModel) -> Handled:
        """Мёртвый ящик лида назвал адрес коллеги — лид той же компании тем же путём, что «пишите
        другому» (`dead_mailbox.colleague`); снимок с видом — повтор задачи его не возьмёт."""
        said, ready = await colleague(self._session, reply, self._refer(reply))
        reply.model_parse = self._record({"stage": Stage.SALES.value, "kind": KIND}, said)
        found = Handled(reply.id, kind=reply.kind.value, route=said.route.value, referred=ready)
        return replace(found, waits=said.waits, reason=said.reason)

    def _refer(self, reply: ReplyModel) -> Callable[[str | None, str | None], Awaitable[Referred]]:
        """Лид из ответа на названный адрес (`referral.from_answer`)."""
        return lambda contact, name: from_answer(
            self._session, reply, contact, self._verifier, now=self._moment(), name=name
        )

    async def _close(self, thread_id: int | None) -> None:
        """Закрыть диалог: «не интересно» и «не сейчас» — без давления. Отписку
        закрытие не перебивает: она сильнее."""
        thread = await self._session.get(ThreadModel, thread_id) if thread_id else None
        if thread is not None and thread.status in (ThreadStatus.OPEN, ThreadStatus.REPLIED):
            thread.status = ThreadStatus.CLOSED


def _forwarded(reply: ReplyModel, again: bool) -> dict[str, Any]:
    """Поля итога, по которым письмо переданного лида уходит той же передачей (`pass_on`)."""
    if not again:
        return {}
    return {"handoff_thread": reply.thread_id, "again": True}


def _ask(handled: Handled) -> Ask:
    """Что передать по итогу разбора: первый раз — «хочет говорить», снова — письмо переданного
    лида с путём и причиной ответа."""
    assert handled.handoff_thread is not None  # зовут только с диалогом
    kinds = {kind.value: kind for kind in SalesKind}
    kind = kinds.get(handled.kind or "", SalesKind.WANTS_TO_TALK)
    route = Route(handled.route) if handled.route else None
    return Ask(
        reply_id=handled.reply_id,
        thread_id=handled.handoff_thread,
        words=KIND_WORDS[kind],
        again=handled.again,
        route=route,
        waits=handled.waits,
        reason=handled.reason or "",
        phone=route is Route.HANDOFF and kind is not SalesKind.WANTS_TO_TALK,
    )
