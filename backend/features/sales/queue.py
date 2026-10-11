"""Очередь писем продаж: первые письма цепочки лидам `ready` гипотезы, по одному на лида.

**Сборка ничего не отправляет** — как у доноров (`letters/building.py`): готовит текст и
ставит письма в очередь. Уходят они общей отправкой — пачкой (`send_queue` этапа продаж,
кнопка «Отправить очередь») или по одному; добивки — общим проходом.

**Письмо лида.** Язык — лида; цепочка — гипотезы на этом языке (своя целиком или общая,
`chain.resolve`) и полная — иначе лид ждёт с причиной в отчёте. Шаблон первого письма —
с подстановками лида (`{{name}}`, `{{company}}`, `{{site}}`); зоны `rewrite` переписывает
модель — общим клиентом доноров (`RewriteClient`), а какие из переписанных принять, подбирает
сборка (`corridor.py`): в отдельном письме зона может остаться текстом шаблона; зоны `fixed`
уходят как есть; подпись и физический адрес — блоком из «Отправителя» (`letter.signed`).
Тема — шаблона, без «Re:» (правило записи 4.6a). Адрес получателя — лида
(`sales_leads.email`), а не `contacts` (решение 01.10); связь диалога с лидом — явная
(`sales_threads`).

**Коридор отличия — тот же, что у доноров (15–25%), но письмо вне коридора в очередь не
встаёт.** У доноров его видит человек и решает сам; очередь продаж уходит пачкой, без глаз
на каждом письме. Поэтому сборка подбирает, какие переписанные зоны принять, чтобы письмо
легло в коридор; ни один набор не лёг — модель переписывает заново с подсказкой, куда и на
сколько промахнулась прошлая попытка, до `SALES_REWRITE_ATTEMPTS` вызовов (`corridor.py`);
не вышло — лид ждёт следующей сборки с отличием каждой попытки в причине. Отличие каждого
письма и принятые зоны — в отчёте сборки (`QueueReport.letters`): на экране под строкой задачи
и в консоли. Меряется отличие по тексту шаблона без блока настроек: правило записи 4.6a
считает достижимость коридора по зонам.

**Повтор сборки не задваивает**: у лида один диалог, у письма — ключ с контактом. Письмо,
которое ещё в очереди, но написано не нынешней цепочкой или с прежними подписью и адресом,
собирается заново на том же месте: сверка на отправке такое не пустила бы, а второе
письмо тому же лиду не дал бы ключ.

**Лид, которому писать нельзя, вызова модели не стоит**: передан телемаркетологу, в
стоп-листе, нет значения подстановки, нет цепочки на его языке — причина в отчёте.
Лиды `referral` (коллега, которого назвали в ответе) получают тот же первый шаг: своего
письма для них пока нет — открытый вопрос владельцу.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import sales as sales_cfg
from backend.features.core import usage
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import CampaignModel, MessageModel, ThreadModel
from backend.features.letters import compose
from backend.features.letters.chain import FIRST_STEP
from backend.features.letters.repository import LetterRepository
from backend.features.letters.rewrite import Personalization, RewriteClient
from backend.features.sales import chain, chain_text, connection, corridor, handoff, letter
from backend.features.sales.intake import UnknownHypothesisError
from backend.features.sales.mail import stopped_by
from backend.features.sales.models import (
    LeadStatus,
    SalesHypothesisModel,
    SalesLeadModel,
    SalesThreadModel,
)
from backend.features.sales.sender import Sender
from backend.features.sales.usage_cap import sales_cap

logger = logging.getLogger(__name__)

#: Сроки добивок продаж: через 3 дня после первого письма и через 5 после второго.
FOLLOWUP_DAYS = (3, 5)
#: Сколько лидов читать за раз: очередь собирается по одному письму, база — пачками.
CHUNK = 200
WHAT = "Очередь продаж не собрана"

#: Почему лид ждёт — словами отчёта; одинаковые причины складываются.
NO_LANGUAGE = "язык лида не задан или цепочки на нём нет (ru, en)"
HANDED_OFF = "передан телемаркетологу"
STOPLIST = "стоп-лист"
LONG_KEY = "адрес и домен длиннее ключа письма"
UP_TO_DATE = "письмо уже в очереди"
SENT_MEANWHILE = "письмо ушло или снято, пока его собирали заново — оставлено как есть"


def campaign_name(hypothesis: SalesHypothesisModel) -> str:
    """Рассылка гипотезы: одна на гипотезу, сроки добивок — при её создании."""
    return f"Продажи: {hypothesis.name}"


@dataclass
class QueueReport:
    """Что получилось — числами и словами, для консоли и строки задачи."""

    campaign_id: int
    prepared: int = 0
    #: Письма в очереди, собранные заново: цепочка сменилась или подпись, адрес.
    refreshed: int = 0
    tokens_spent: int = 0
    off_corridor: int = 0
    #: Почему лиды ждут: причина → сколько.
    waiting: Counter[str] = field(default_factory=Counter)
    #: Сборка остановлена потолком расхода на модель: причина словами.
    stopped: str | None = None
    #: Почему зоны остались шаблонными: причина → сколько раз (как у сборки доноров).
    notes: Counter[str] = field(default_factory=Counter)
    #: Письма, которые писала модель, — по порядку сборки.
    letters: list[corridor.LetterLine] = field(default_factory=list)

    def totals(self) -> dict[str, Any]:
        """Числа и причины без писем по одному — так сборку пишет журнал: имена лидов ему
        не нужны."""
        return {
            "campaign_id": self.campaign_id,
            "prepared": self.prepared,
            "refreshed": self.refreshed,
            "tokens_spent": self.tokens_spent,
            "off_corridor": self.off_corridor,
            "waiting": dict(self.waiting),
            "stopped": self.stopped,
            "notes": dict(self.notes),
        }

    def as_dict(self) -> dict[str, Any]:
        """Итог задачи для экрана: числа, причины и письма по одному."""
        return {**self.totals(), "letters": [line.as_dict() for line in self.letters]}


@dataclass(frozen=True, slots=True)
class _Lead:
    """Лид и то, что о нём уже есть: сайт компании, диалог и письмо в очереди."""

    lead: SalesLeadModel
    host: str
    link: SalesThreadModel | None
    queued: MessageModel | None


@dataclass(frozen=True, slots=True)
class _Plan:
    """Что пишем лиду: цепочка его языка и значения подстановок."""

    chain: chain.Chain
    values: dict[str, str]
    key: str


async def build(
    session: AsyncSession,
    rewriter: RewriteClient,
    *,
    hypothesis_id: int,
    limit: int,
    followup_days: tuple[int, ...] = FOLLOWUP_DAYS,
) -> QueueReport:
    """Собрать очередь гипотезы: до `limit` писем, новых и собранных заново."""
    hypothesis = await session.get(SalesHypothesisModel, hypothesis_id)
    if hypothesis is None:
        raise UnknownHypothesisError(f"гипотезы №{hypothesis_id} нет — обновите список гипотез")
    found = await connection.check(session, WHAT)
    campaign = await LetterRepository(session).campaign(
        name=campaign_name(hypothesis), stage=Stage.SALES, followup_days=followup_days
    )
    # Чекпоинт, как у доноров: дальше каждое письмо фиксируется отдельно.
    await session.commit()
    report = QueueReport(campaign_id=campaign.id)
    after = 0
    while report.prepared + report.refreshed < limit and report.stopped is None:
        rows = await _leads(session, hypothesis_id, after)
        if not rows:
            break
        for row in rows:
            after = row.lead.id
            if not await _room(session, report, limit):
                break
            await _prepare(session, rewriter, row, campaign.id, hypothesis_id, found, report)
            await session.commit()
    logger.info(
        "продажи: очередь собрана", extra={"hypothesis_id": hypothesis_id, **report.totals()}
    )
    return report


async def _leads(session: AsyncSession, hypothesis_id: int, after: int) -> list[_Lead]:
    """Лиды `ready` гипотезы, которым ещё не писали: без диалога или с письмом в очереди."""
    first = (MessageModel.thread_id == SalesThreadModel.thread_id) & (
        MessageModel.step == FIRST_STEP
    )
    rows = await session.execute(
        select(SalesLeadModel, DomainModel.host, SalesThreadModel, MessageModel)
        .join(DomainModel, DomainModel.id == SalesLeadModel.domain_id)
        .outerjoin(SalesThreadModel, SalesThreadModel.lead_id == SalesLeadModel.id)
        .outerjoin(MessageModel, first)
        .where(
            SalesLeadModel.hypothesis_id == hypothesis_id,
            SalesLeadModel.status == LeadStatus.READY,
            SalesLeadModel.id > after,
            or_(
                SalesThreadModel.thread_id.is_(None),
                MessageModel.status == MessageStatus.QUEUED,
            ),
        )
        .order_by(SalesLeadModel.id)
        .limit(CHUNK)
    )
    return [_Lead(*row._tuple()) for row in rows.all()]


async def _room(session: AsyncSession, report: QueueReport, limit: int) -> bool:
    """Есть ли место следующему письму: предел сборки не набран, сборка не встала — потолок
    модели мог встать и посреди попыток прошлого письма, — и потолок не достигнут сейчас."""
    if report.prepared + report.refreshed >= limit or report.stopped is not None:
        return False
    return await _within_cap(session, report)


async def _within_cap(session: AsyncSession, report: QueueReport) -> bool:
    """Потолок расхода на модель — до каждого письма, как у доноров, и до каждой следующей
    попытки письма (`corridor.write`); кроме общего — свой у вызовов модели продаж
    (`usage_cap.sales_cap`)."""
    try:
        await usage.ensure_llm_within_cap(session, run_id=None, own=sales_cap())
    except usage.LlmCapExceededError as exc:
        report.stopped = str(exc)
        logger.warning("продажи: сборка остановлена", extra={"reason": str(exc)})
        return False
    return True


async def _blocked(session: AsyncSession, row: _Lead) -> str | None:
    """Почему этому лиду писать нельзя совсем: передан или в стоп-листе."""
    if await handoff.handed_off(session, row.lead.id):
        return HANDED_OFF
    if await stopped_by(session, row.lead, row.host) is not None:
        return STOPLIST
    return None


async def _plan(session: AsyncSession, row: _Lead, hypothesis_id: int) -> _Plan | str:
    """Что писать лиду — или почему он ждёт (строкой отчёта). Без вызова модели."""
    if (why := await _blocked(session, row)) is not None:
        return why
    # Язык лида без цепочки — обычный случай очереди, а не ошибка: причина в отчёте.
    language = (row.lead.language or "").strip().lower()
    if language not in chain_text.LANGUAGES:
        return NO_LANGUAGE
    found = await chain.resolve(session, hypothesis_id=hypothesis_id, language=language)
    if found.missing:
        return f"цепочка на языке {language} задана не целиком"
    values = letter.values_of(row.lead, row.host)
    absent = letter.lacking(found.steps.values(), values)
    if absent:
        return f"у лида нет значения для {', '.join(absent)}"
    key = letter.key(row.host, row.lead.email, FIRST_STEP)
    return LONG_KEY if len(key) > letter.KEY_LENGTH else _Plan(found, values, key)


def _current(row: _Lead, plan: _Plan, found: Sender) -> bool:
    """Письмо в очереди написано нынешней цепочкой и подписано нынешними настройками."""
    if row.queued is None or row.link is None:
        return False
    same = (row.link.chain_hypothesis_id, row.link.chain_version) == (
        plan.chain.hypothesis_id,
        plan.chain.version,
    )
    return same and letter.problem(row.queued.body or "", found) is None


async def _prepare(
    session: AsyncSession,
    rewriter: RewriteClient,
    row: _Lead,
    campaign_id: int,
    hypothesis_id: int,
    found: Sender,
    report: QueueReport,
) -> None:
    """Одно письмо: план, текст в коридоре (подбор зон и попытки — `corridor.py`), сверка
    по итоговому тексту, запись в очередь."""
    plan = await _plan(session, row, hypothesis_id)
    if isinstance(plan, str):
        report.waiting[plan] += 1
        return
    if _current(row, plan, found):
        report.waiting[UP_TO_DATE] += 1
        return
    rendered = compose.render(
        plan.chain.steps[letter.template_step(FIRST_STEP)].letter, plan.values
    )
    tried = await corridor.write(
        session,
        rewriter,
        rendered,
        Personalization(host=row.host, country=row.lead.country or "—"),
        lead_id=row.lead.id,
        attempts=sales_cfg.REWRITE_ATTEMPTS,
        within_cap=lambda: _within_cap(session, report),
    )
    report.tokens_spent += tried.tokens
    report.notes.update(tried.notes)
    why = await _store(session, row, plan, tried, found, campaign_id)
    report.letters.append(corridor.line(row.lead.id, row.lead.name, tried, queued=why is None))
    if why is not None:
        report.waiting[why] += 1
        if tried.letter is None:
            report.off_corridor += 1
    elif row.queued is None:
        report.prepared += 1
    else:
        report.refreshed += 1


async def _store(
    session: AsyncSession,
    row: _Lead,
    plan: _Plan,
    tried: corridor.Tried,
    found: Sender,
    campaign_id: int,
) -> str | None:
    """Письмо в очередь — или почему нет, строкой отчёта: вне коридора после всех попыток,
    не сходится с подписью и адресом отправителя, ушло, пока его собирали заново."""
    if tried.letter is None:
        return corridor.waiting_reason(tried.differences)
    body = letter.signed(tried.letter.body, found)
    problem = letter.problem(body, found)
    if problem is not None:
        return problem
    written = compose.Letter(
        subject=tried.letter.subject, body=body, plain_body=tried.letter.plain_body
    )
    if not await _write(session, row, plan, written, tried.difference, campaign_id):
        return SENT_MEANWHILE
    return None


async def _write(
    session: AsyncSession,
    row: _Lead,
    plan: _Plan,
    written: compose.Letter,
    uniqueness: float,
    campaign_id: int,
) -> bool:
    """Письмо в очередь: новый диалог со связью — или то же место, собранное заново.

    Место переписывается, только если письмо в базе всё ещё в очереди: пачка — другая задача,
    и письмо, прочитанное сборкой, могло уйти, пока модель его переписывала. Ушедшее остаётся
    тем, что ушло (по его теме и версии цепочки идут добивки): `False`."""
    if row.queued is not None and row.link is not None:
        refreshed = await session.execute(
            update(MessageModel)
            .where(MessageModel.id == row.queued.id, MessageModel.status == MessageStatus.QUEUED)
            .values(subject=written.subject, body=written.body, uniqueness_pct=uniqueness)
            .returning(MessageModel.id)
            .execution_options(synchronize_session="fetch")
        )
        if refreshed.first() is None:
            return False
        row.link.chain_hypothesis_id = plan.chain.hypothesis_id
        row.link.language, row.link.chain_version = plan.chain.language, plan.chain.version
        await session.flush()
        return True
    thread = ThreadModel(domain_id=row.lead.domain_id, campaign_id=campaign_id, contact_id=None)
    session.add(thread)
    await session.flush()
    session.add_all(
        [
            SalesThreadModel(
                thread_id=thread.id,
                lead_id=row.lead.id,
                chain_hypothesis_id=plan.chain.hypothesis_id,
                language=plan.chain.language,
                chain_version=plan.chain.version,
            ),
            MessageModel(
                campaign_id=campaign_id,
                thread_id=thread.id,
                domain_id=row.lead.domain_id,
                contact_id=None,
                step=FIRST_STEP,
                status=MessageStatus.QUEUED,
                subject=written.subject,
                body=written.body,
                uniqueness_pct=uniqueness,
                idempotency_key=plan.key,
            ),
        ]
    )
    await session.flush()
    return True


@dataclass(frozen=True, slots=True)
class QueueState:
    """Что видно на экране очереди гипотезы: подключены ли продажи, цепочки, сколько ждёт."""

    #: Чего не хватает продажам (`connection.missing`). Пусто — подключены.
    missing: list[str]
    #: Цепочка гипотезы на каждом языке: своя или общая, полна ли.
    chains: list[chain.Chain]
    #: Лидов `ready` без диалога — им ещё не собрано письмо.
    unwritten: int
    #: Первых писем гипотезы в очереди — ждут отправки (их же пересобирает сборка).
    queued: int
    #: Писем продаж в очереди — всех гипотез: пачка (`send_queue` этапа продаж) берёт
    #: очередь этапа целиком, и кнопка называет это число, а не число гипотезы. Как и у
    #: пачки, только первые письма: добивка в очереди ждёт свой ящик и проход добивок.
    stage_queued: int


async def state(session: AsyncSession, hypothesis_id: int) -> QueueState:
    """Очередь гипотезы для экрана и консоли — без записи."""
    chains = [
        await chain.resolve(session, hypothesis_id=hypothesis_id, language=code)
        for code in chain_text.LANGUAGES
    ]
    unwritten = await session.scalar(
        select(func.count())
        .select_from(SalesLeadModel)
        .outerjoin(SalesThreadModel, SalesThreadModel.lead_id == SalesLeadModel.id)
        .where(
            SalesLeadModel.hypothesis_id == hypothesis_id,
            SalesLeadModel.status == LeadStatus.READY,
            SalesThreadModel.thread_id.is_(None),
        )
    )
    queued = await session.scalar(
        select(func.count())
        .select_from(MessageModel)
        .join(SalesThreadModel, SalesThreadModel.thread_id == MessageModel.thread_id)
        .join(SalesLeadModel, SalesLeadModel.id == SalesThreadModel.lead_id)
        .where(
            SalesLeadModel.hypothesis_id == hypothesis_id,
            MessageModel.status == MessageStatus.QUEUED,
            MessageModel.step == FIRST_STEP,
        )
    )
    stage_queued = await session.scalar(
        select(func.count())
        .select_from(MessageModel)
        .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
        .where(
            CampaignModel.stage == Stage.SALES,
            MessageModel.status == MessageStatus.QUEUED,
            MessageModel.step == FIRST_STEP,
        )
    )
    return QueueState(
        await connection.missing(session),
        chains,
        int(unwritten or 0),
        int(queued or 0),
        int(stage_queued or 0),
    )
