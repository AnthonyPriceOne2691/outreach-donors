"""Этап продаж в общей почте: отказ там, где почта его не ведёт, и мост к модулю продаж.

Этап продаж заведён раньше, чем почта научилась его вести: почти каждая ветка
почты была устроена как «рекламодатели, иначе доноры», и новый этап в ней
молча становился донором — очередь из принятых доноров, вопрос о цене, цена из
ответа лида в карточке донора. Поэтому ветка, где этап решает путь, разбирает
его целиком — `match` с `assert_never` — и продажам либо отказывает этим
исключением, либо ведёт их своим путём. Текст отказа один на всю почту: человек
читает его на экране, в консоли и в итоге задачи.

**Где почта продажи не ведёт** — сборка очереди доноров, шаблоны файлами, отбор:
отказ (`SalesNotConnectedError`). Где этапу нужен только допуск — сборка, — стоит
шлюз `mail_stage`: дальше идёт `MailStage`.

**Где ведёт** — отправка письма из очереди, проход добивок (срез 4.6b) и ответ лиду
в переписке: письмо продаж собирает модуль продаж из лидов и цепочки в базе, ответ
пишет человек или агент, а уходят они общей отправкой — теми же стоп-листами,
ящиками этапа и предохранителем. Почта (`letters/`) модуль продаж не импортирует
(контракт `mail-does-not-know-sales` в `.importlinter`): о письме продаж она
спрашивает этот мост (`SalesMail`). Ответ в переписке — до заведения письма
(`answer_text`): отказ модуля (не подключены — чего не хватает, у переписки нет
лида, лиду писать нельзя) не оставляет ответа в очереди, а текст ответа модуль
отдаёт с подписью и физическим адресом из настроек отправителя.

**Модуль продаж подключает себя сам** (`register_sales` — при загрузке пакета
продаж): мост знает только, подключён ли он. Не подключён — отказ 1.1b словами, а
проход добивок сроки продаж не берёт (срок цел). Регистрация хранит загрузчик, а не
модуль: модуль продаж стоит на почте и при загрузке тянет её саму — загрузка при
регистрации замкнула бы круг, поэтому загрузчик зовётся при первом письме продаж.

Отправка спрашивает мост до выбора учётки этапа (`transport.of_stage`): пока
продажи не подключены — нет своей учётки, отправителя, цепочки, — письмо продаж
учётку не трогает, и отказ называет, чего не хватает.

**От модуля почта получает только отказ словами** (`MailRefusalError`): проход добивок,
пачка и кнопка — общие, и поломка модуля продаж не должна их ронять. Любую другую
ошибку модуля мост переводит в «не подключены» с причиной и пишет в журнал: срок
добивки возвращается, пачка встаёт словами, кнопка получает 409; «подключены ли» с
ошибкой — «нет», и проход идёт без продаж. Модуль отвечает в своей точке сохранения:
упавший запрос модуля не ломает транзакцию почты, а сбой своих несохранённых изменений
почты — её ошибка, а не модуля.

**Политика этапа** (`mail_policy`, Ф4) — что почта делает с письмами этапа сверх
общих правил: окно получателя (4.3). У доноров и рекламодателей — нынешнее
поведение (`CURRENT`), у продаж — ответ модуля продаж тем же мостом
(`SalesMail.policy`), а не вторым реестром; поломка модуля и здесь — «не подключены».
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, assert_never

from backend.features.core.domain import Stage

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.features.core.models.outreach import MessageModel
    from backend.features.core.window import SendWindow
    from backend.features.outreach.health import SoftSignals

logger = logging.getLogger(__name__)

#: Почему почта не делает того, о чём просят, для этапа продаж.
SALES_NOT_CONNECTED = "продажи к почте ещё не подключены"

#: Почему этот путь почты продажи не ведёт, даже подключённые: их письма — свои.
SALES_ELSEWHERE = (
    "письма продаж собирает модуль продаж из лидов и цепочки в базе, "
    "а не этот путь почты доноров и рекламодателей"
)

#: Этапы, которые ведёт сборка очереди почты. Шире — только вместе с ветками,
#: которые разбирают этот тип.
MailStage = Literal[Stage.DONORS, Stage.ADVERTISERS]


class MailRefusalError(RuntimeError):
    """Отказ почты словами: письмо не ушло, сообщение называет причину. Такие отказы почта
    разбирает сама — стоп-лист, «не готово», «некому писать», «не подключены»
    (`letters.sending.SendError` и `SalesNotConnectedError` — его наследники)."""


class SalesNotConnectedError(MailRefusalError):
    """Почта не ведёт продажи этим путём или продажи не подключены: `what` — что не
    сделано, `why` — чего не хватает (у отправки и прохода добивок — всё сразу)."""

    #: Повтор задачи это не исправит (`runs/failures.py`).
    permanent = True

    def __init__(
        self, what: str, why: str | None = None, *, words: str = SALES_NOT_CONNECTED
    ) -> None:
        super().__init__(f"{what}: {words}" + (f" — {why}" if why else ""))


def mail_stage(stage: Stage, what: str) -> MailStage:
    """Этап, если почта его ведёт; продажам — отказ (`what` — что не сделано)."""
    match stage:
        case Stage.DONORS | Stage.ADVERTISERS:
            return stage
        case Stage.SALES:
            raise SalesNotConnectedError(what)
        case _:
            assert_never(stage)


async def check_connected(session: AsyncSession, stage: Stage, what: str) -> None:
    """Мост подключения этапа (спрашивает отправка очереди пачкой): почта ведёт
    этап сейчас — или отказ словами. У продаж — ответ модуля продаж
    (`SalesMail.connected`, читает базу): модуль не подключён к мосту или говорит
    «нет» — отказ; спрашивающие не меняются. Не `mail_stage`: тот отказывает
    продажам там, где их не будет и подключённых (очередь из доноров, шаблоны)."""
    match stage:
        case Stage.DONORS | Stage.ADVERTISERS:
            return
        case Stage.SALES:
            if not await sales_connected(session):
                raise SalesNotConnectedError(what)
        case _:
            assert_never(stage)


def donor_path(stage: Literal[Stage.DONORS]) -> None:
    """Пометка в ветке по этапу: остальные этапы разобраны выше, дальше — путь доноров.

    На исполнении ничего не делает — проверяет mypy. Этап, добавленный в `Stage`
    без своей ветки выше вызова, сюда не пройдёт по типу, и путь доноров не станет
    его путём молча.
    """


@dataclass(frozen=True, slots=True)
class Recipient:
    """Кому письмо и от чьего имени — ответ этапа отправке (`sending._target`)."""

    stage: Stage
    #: Адрес получателя: у доноров и рекламодателей — строка `contacts`, у продаж — адрес
    #: лида (его называет модуль продаж). Пусто — писать некому.
    email: str | None
    #: Имя в From. Пусто — общее (`compose.values_for`); у продаж — из «Отправителя».
    sender_name: str | None = None
    #: Пояса получателя по порядку: лида, его страны, гипотезы — окно считает первый
    #: известный (`window.zone_of`). У доноров и рекламодателей пусто: окна у них нет.
    zones: tuple[str | None, ...] = ()

    def from_name(self, shared: str) -> str:
        """Имя в From: своё у этапа (продажи) или общее `shared`."""
        return self.sender_name or shared


@dataclass(frozen=True, slots=True)
class MailPolicy:
    """Что почта делает с письмами этапа сверх общих правил (Ф4); пустая — как было."""

    #: Окно получателя (`window.py`): письмо уходит в его рабочие часы. Пусто — в любой час.
    window: SendWindow | None = None
    #: Мягкие сигналы ящика (`outreach/health.py`). Пусто — прежнее правило парковки.
    soft: SoftSignals | None = None
    #: Сторож почты этапа (`ops/mail_watch.py`): ящик молчит, отправить некому, все на паузе.
    watch: bool = False


#: Политика доноров и рекламодателей: всё как было до политик этапа.
CURRENT = MailPolicy()


@dataclass(frozen=True, slots=True)
class SalesFollowup:
    """Текст добивки продаж — шаг цепочки из базы с подписью и адресом из настроек."""

    body: str


class SalesMail(Protocol):
    """Что почта спрашивает у модуля продаж о письме продаж (модуль `sales/mail.py`).
    Отказ — только словами почты; прочие ошибки мост переводит в «не подключены»."""

    async def recipient(self, session: AsyncSession, message: MessageModel, what: str) -> Recipient:
        """Кому письмо и от чьего имени — или отказ словами (`MailRefusalError`), до выбора
        учётки этапа."""

    async def check(self, session: AsyncSession, message: MessageModel) -> None:
        """Перед отправкой: лиду ещё можно писать, письмо цело — или отказ словами
        (`MailRefusalError`: стоп-лист кончает цепочку, «не готово» — письмо ждёт)."""

    async def connected(self, session: AsyncSession) -> bool:
        """Подключены ли продажи: проход добивок берёт их сроки только тогда."""

    async def answer(self, session: AsyncSession, thread_id: int, body: str, what: str) -> str:
        """Ответ лиду в переписке `thread_id` до заведения письма: текст, каким его увидит
        лид, — или отказ словами (`MailRefusalError`): не подключены (чего не хватает), у
        переписки нет лида, лиду писать нельзя."""

    async def followup(
        self, session: AsyncSession, thread_id: int | None, step: int
    ) -> SalesFollowup:
        """Текст добивки шага `step` (шаг письма, с нуля) в переписке `thread_id` — или «пока
        нельзя» (`SalesNotConnectedError`): письма ещё нет, и срок только возвращается."""

    async def policy(self, session: AsyncSession) -> MailPolicy:
        """Политика почты для писем продаж (`mail_policy`): окно получателя."""


@dataclass(slots=True)
class _Registry:
    """Загрузчик модуля продаж. `None` — продажи к почте не подключены."""

    load: Callable[[], SalesMail] | None = None


_SALES = _Registry()


def register_sales(load: Callable[[], SalesMail]) -> None:
    """Подключить модуль продаж к почте: `load` отдаёт его ответы о письме продаж."""
    _SALES.load = load


def sales_registered() -> bool:
    """Подключён ли модуль продаж к мосту — а не «продажи подключены» (`sales_connected`)."""
    return _SALES.load is not None


async def _asked[T](
    session: AsyncSession,
    what: str,
    ask: Callable[[SalesMail], Awaitable[T]],
    passes: type[MailRefusalError] = MailRefusalError,
) -> T:
    """Ответ модуля продаж — или отказ: модуль не подключён — 1.1b словами; отказ модуля
    словами (`passes`) — как есть; любая другая ошибка модуля — «не подключены» с причиной
    и записью в журнал. Модуль отвечает в своей точке сохранения (`begin_nested`).

    Точка сохранения сначала сбрасывает в базу несохранённое вызывающего — поэтому мост
    сбрасывает его сам до вопроса: сбой своих изменений почты всплывает как есть, а не
    как ошибка модуля продаж, и модуль тогда не спрошен."""
    load = _SALES.load
    if load is None:
        raise SalesNotConnectedError(what)
    await session.flush()
    try:
        async with session.begin_nested():
            return await ask(load())
    except passes:
        raise
    except Exception as exc:
        logger.warning(
            "мост продаж: %s — ошибка модуля продаж: %s",
            what,
            exc,
            exc_info=True,
            extra={"why": str(exc) or type(exc).__name__},
        )
        raise SalesNotConnectedError(what, str(exc) or type(exc).__name__) from exc


async def recipient(
    session: AsyncSession, message: MessageModel, stage: Stage, email: str | None, what: str
) -> Recipient:
    """Кому письмо этапа. Продажам — ответ модуля продаж (адрес лида и имя отправителя),
    если он подключён; нет — отказ словами (до выбора учётки этапа)."""
    match stage:
        case Stage.DONORS | Stage.ADVERTISERS:
            return Recipient(stage, email)
        case Stage.SALES:
            return await _asked(session, what, lambda mail: mail.recipient(session, message, what))
        case _:
            assert_never(stage)


async def answer_text(
    session: AsyncSession, stage: Stage, thread_id: int, body: str, what: str
) -> str:
    """Текст ответа собеседнику в переписке этапа — до заведения письма ответа. Доноры и
    рекламодатели — как написан. Продажи — ответ модуля продаж (`SalesMail.answer`): текст с
    подписью и адресом из настроек — или отказ словами (не подключены — чего не хватает, у
    переписки нет лида, лиду писать нельзя), и ответ в очереди не остаётся."""
    match stage:
        case Stage.DONORS | Stage.ADVERTISERS:
            return body
        case Stage.SALES:
            return await _asked(
                session, what, lambda mail: mail.answer(session, thread_id, body, what)
            )
        case _:
            assert_never(stage)


async def check_sales(session: AsyncSession, message: MessageModel) -> None:
    """Ветка продаж в проверке перед отправкой: лиду ещё можно писать, письмо цело."""
    await _asked(session, f"Письмо №{message.id}", lambda mail: mail.check(session, message))


async def sales_connected(session: AsyncSession) -> bool:
    """Подключены ли продажи: проход добивок берёт их сроки только тогда. Не бросает на
    ошибках модуля: ошибка модуля — «нет» с причиной в журнале (`_asked`), проход идёт без
    продаж, доноры уходят, пачка продаж встаёт словами. Ошибка собственных несохранённых
    изменений вызывающего всплывает как своя."""
    if _SALES.load is None:
        return False
    try:
        return await _asked(
            session,
            "Подключены ли продажи",
            lambda mail: mail.connected(session),
            SalesNotConnectedError,
        )
    except SalesNotConnectedError as exc:
        logger.info("мост продаж: проход без продаж — %s", exc, extra={"why": str(exc)})
        return False


async def sales_followup(session: AsyncSession, thread_id: int | None, step: int) -> SalesFollowup:
    """Текст добивки продаж шага `step` (шаг письма, с нуля) в переписке `thread_id`.
    Письма ещё нет: любой отказ, кроме «не подключены», здесь — тоже «не подключены»,
    и проход добивок возвращает срок (стоп-лист решает отправка, когда письмо уже есть)."""
    what = f"Добивка шага {step} в переписке №{thread_id}"
    return await _asked(
        session,
        what,
        lambda mail: mail.followup(session, thread_id, step),
        SalesNotConnectedError,
    )


async def mail_policy(session: AsyncSession, stage: Stage, what: str) -> MailPolicy:
    """Политика этапа. Продажи — ответ модуля продаж тем же мостом (`_asked`): не подключён —
    писем продаж нет, и политика нынешняя (отправка откажет им раньше, `recipient`). Политика —
    об этапе, а не о письме: как «подключены ли», пропускает только «не подключены»."""
    match stage:
        case Stage.DONORS | Stage.ADVERTISERS:
            return CURRENT
        case Stage.SALES:
            if _SALES.load is None:
                return CURRENT
            passes = SalesNotConnectedError  # стоп-лист из политики — тоже «не подключены»
            return await _asked(session, what, lambda mail: mail.policy(session), passes)
        case _:
            assert_never(stage)
