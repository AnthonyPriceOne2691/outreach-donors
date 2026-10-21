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
не вышло — лид ждёт следующей сборки, а строка письма называет отличие каждой попытки. Три
лида подряд вне коридора — сборка встаёт словами: не даёт шаблон (`corridor.STREAK`). Отличие
каждого письма и принятые зоны — в отчёте сборки (`queue_report.py`): на экране под строкой
задачи и в консоли. Меряется отличие по тексту шаблона без блока настроек: правило записи 4.6a
считает достижимость коридора по зонам.

**Добивки (шаги 2–3) с зонами `rewrite` — тоже при сборке** (`followup_texts.py`): когда
первое письмо легло в коридор и сходится с подписью, тот же писатель лида (`corridor.Writer`,
расход — одной строкой) готовит их тексты, каждый в своём коридоре, и они ложатся у переписки
лида; отправка добивки берёт их оттуда. Не легла — добивка уходит текстом шаблона, первое
письмо встаёт в очередь; потолок модели встал на добивках — лид ждёт следующей сборки.

**Повтор сборки не задваивает**: у лида один диалог, у письма — ключ с контактом. Письмо в
очереди, написанное не нынешней цепочкой, с прежними подписью и адресом или до правки лида,
собирается заново на том же месте вместе с добивками: сверка на отправке такое не пустила
бы, а второе письмо тому же лиду не дал бы ключ. Так же — на том же месте — пишется заново
первое письмо, снятое до отправки (`off_queue.again`): лида снимали, а он снова «готов», или
сайт был в работе у другого направления, а теперь свободен.

**Сайт в работе у другого направления — лид ждёт** (одна роль на сайт, `elsewhere.py`): письмо
ему отправка сняла бы, и модель его не пишет; причина — в отчёте.

**Лид, которому писать нельзя, вызова модели не стоит**: передан телемаркетологу, в
стоп-листе, адрес проверен выдуманным проверяльщиком при живом (`verifier.unproven`) или не
подтверждён (`unconfirmed.first_letter`), нет значения подстановки, нет цепочки на его языке —
причина в отчёте.
Лиды `referral` (коллега, которого назвали в ответе) получают тот же первый шаг: своего
письма для них пока нет — открытый вопрос владельцу.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, or_, select, true, update
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
from backend.features.sales import (
    chain,
    chain_text,
    connection,
    corridor,
    elsewhere,
    handoff,
    hypotheses,
    letter,
    off_queue,
)
from backend.features.sales.followup_texts import CAPPED, Built
from backend.features.sales.followup_texts import build as build_followups
from backend.features.sales.intake import UnknownHypothesisError
from backend.features.sales.kb_gaps import IN_LANGUAGE
from backend.features.sales.lead_edit import edited
from backend.features.sales.lead_values import language_base
from backend.features.sales.mail import stopped_by
from backend.features.sales.models import (
    LeadStatus,
    SalesHypothesisModel,
    SalesLeadModel,
    SalesThreadModel,
)
from backend.features.sales.not_writing import excluded
from backend.features.sales.queue_report import QueueReport
from backend.features.sales.sender import Sender
from backend.features.sales.unconfirmed import first_letter
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
#: Ключ сводки причин — без чисел: сто лидов вне коридора — одна строка; числа каждой попытки —
#: в строке письма (`corridor.waiting_reason`), в журнале и на экране.
OFF_CORRIDOR = "вне коридора отличия — ждёт следующей сборки"
SENT_MEANWHILE = "письмо ушло или снято, пока его собирали заново — оставлено как есть"


def campaign_name(hypothesis: SalesHypothesisModel) -> str:
    """Рассылка гипотезы: одна на гипотезу, сроки добивок — при её создании. Имя — правилом
    `hypotheses.campaign_title`: по нему же правка имени гипотезы переименовывает рассылку."""
    return hypotheses.campaign_title(hypothesis.name)


@dataclass(frozen=True, slots=True)
class _Lead:
    """Лид и то, что о нём уже есть: сайт компании, диалог и первое письмо — в очереди или
    снятое до отправки (`off_queue.again`)."""

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
    only: tuple[int, ...] = (),
) -> QueueReport:
    """Собрать очередь гипотезы: до `limit` писем, новых и собранных заново (`only` — лидам)."""
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
        rows = await _leads(session, hypothesis_id, after, only)
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


async def _leads(
    session: AsyncSession, hypothesis_id: int, after: int, only: tuple[int, ...] = ()
) -> list[_Lead]:
    """Лиды `ready` гипотезы, которым ещё не писали: без диалога, с письмом в очереди или с
    первым письмом, снятым до отправки (`off_queue.again`) — его сборка пишет заново."""
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
            SalesLeadModel.id.in_(only) if only else true(),
            or_(
                SalesThreadModel.thread_id.is_(None),
                MessageModel.status == MessageStatus.QUEUED,
                off_queue.again(MessageModel),
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
    """Почему этому лиду писать нельзя: передан, в стоп-листе, в «кому не пишем» — или ждёт,
    пока сайт в работе у другого направления (`elsewhere.py`)."""
    if await handoff.handed_off(session, row.lead.id):
        return HANDED_OFF
    if await stopped_by(session, row.lead, row.host) is not None:
        return STOPLIST
    off = excluded(row.lead.email, row.host)
    if off is not None:
        return off.waiting
    busy = await elsewhere.roles(session, [row.lead.domain_id])
    return elsewhere.waiting(busy[row.lead.domain_id]) if busy else None


async def _plan(session: AsyncSession, row: _Lead, hypothesis_id: int) -> _Plan | str:
    """Что писать лиду — или почему он ждёт (строкой отчёта). Без вызова модели."""
    if (why := await _blocked(session, row) or first_letter(row.lead)) is not None:
        return why
    # Язык без цепочки — причина в отчёте, не ошибка; язык — основой кода: `ru-RU` → `ru`.
    language = language_base(row.lead.language)
    if language not in chain_text.LANGUAGES:
        return NO_LANGUAGE
    found = await chain.resolve(session, hypothesis_id=hypothesis_id, language=language)
    if found.missing:
        # Язык словами («на русском»), а не кодом: причину читает человек на экране очереди.
        said = IN_LANGUAGE.get(language, f"на языке {language}")
        return f"цепочка {said} задана не целиком"
    values = letter.values_of(row.lead, row.host)
    absent = letter.lacking(found.steps.values(), values)
    if absent:
        return f"у лида нет значения для {', '.join(absent)}"
    key = letter.key(row.host, row.lead.email, FIRST_STEP)
    return LONG_KEY if len(key) > letter.KEY_LENGTH else _Plan(found, values, key)


def _current(row: _Lead, plan: _Plan, found: Sender) -> bool:
    """Письмо в очереди написано нынешней цепочкой, подписано нынешними настройками и после
    последней правки лида (`lead_edit.edited`). Снятое до отправки — не в очереди."""
    if row.queued is None or row.link is None or row.queued.status is not MessageStatus.QUEUED:
        return False
    same = (row.link.chain_hypothesis_id, row.link.chain_version) == (
        plan.chain.hypothesis_id,
        plan.chain.version,
    )
    fresh = same and not edited(row.lead, row.queued)
    return fresh and letter.problem(row.queued.body or "", found) is None


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
    по итоговому тексту, добивки (`followup_texts.py`), запись в очередь."""
    plan = await _plan(session, row, hypothesis_id)
    if isinstance(plan, str):
        report.waiting[plan] += 1
        return
    if _current(row, plan, found):
        report.waiting[UP_TO_DATE] += 1
        return
    writer = corridor.Writer(
        rewriter,
        Personalization(host=row.host, country=row.lead.country or "—"),
        row.lead.id,
        sales_cfg.REWRITE_ATTEMPTS,
        lambda: _within_cap(session, report),
        corridor.Spend(session),
    )
    first = plan.chain.steps[letter.template_step(FIRST_STEP)]
    tried = await writer.write(letter.rendered(first, plan.values))
    signed = _signed(tried, found)
    later = Built()
    if isinstance(signed, compose.Letter):
        later = await build_followups(writer, plan.chain, plan.values)
    report.tokens_spent += writer.spend.tokens
    report.notes.update([*tried.notes, *later.notes])
    why = (
        signed
        if isinstance(signed, str)
        else await _store(session, row, plan, signed, tried, later, campaign_id)
    )
    report.tally(row.lead.id, row.lead.name, row.queued is not None, tried, why, later.lines)


def _signed(tried: corridor.Tried, found: Sender) -> compose.Letter | str:
    """Письмо с подписью и адресом — или почему не в очередь, строкой отчёта: вне коридора
    после всех попыток, не сходится с подписью и адресом отправителя."""
    if tried.letter is None:
        return OFF_CORRIDOR
    body = letter.signed(tried.letter.body, found)
    problem = letter.problem(body, found)
    if problem is not None:
        return problem
    return compose.Letter(
        subject=tried.letter.subject, body=body, plain_body=tried.letter.plain_body
    )


async def _store(
    session: AsyncSession,
    row: _Lead,
    plan: _Plan,
    written: compose.Letter,
    tried: corridor.Tried,
    later: Built,
    campaign_id: int,
) -> str | None:
    """Письмо и тексты добивок в очередь — или почему нет, строкой отчёта: потолок модели
    встал на добивках, лида сняли (`off_queue.still_ready`) или письмо ушло, пока его собирали."""
    if later.capped:
        return CAPPED
    if (withdrawn := await off_queue.still_ready(session, row.lead.id)) is not None:
        return withdrawn
    texts = later.stored(plan.chain.version)
    if not await _write(session, row, plan, written, tried.difference, texts, campaign_id):
        return SENT_MEANWHILE
    return None


async def _write(
    session: AsyncSession,
    row: _Lead,
    plan: _Plan,
    written: compose.Letter,
    uniqueness: float,
    texts: dict[str, Any] | None,
    campaign_id: int,
) -> bool:
    """Письмо в очередь: новый диалог со связью — или то же место, собранное заново; тексты
    добивок — у связи (`texts`, пусто — добивки уйдут шаблоном шага).

    Место переписывается, только если письмо в базе всё ещё в очереди — или снято до отправки
    (`off_queue.again`): тогда оно снова в очереди, без прежней причины. Пачка — другая задача,
    и письмо, прочитанное сборкой, могло уйти, пока модель его переписывала. Ушедшее остаётся
    тем, что ушло (по его теме, версии цепочки и текстам добивок идут добивки): `False`."""
    if row.queued is not None and row.link is not None:
        place = or_(MessageModel.status == MessageStatus.QUEUED, off_queue.again(MessageModel))
        refreshed = await session.execute(
            update(MessageModel)
            .where(MessageModel.id == row.queued.id, place)
            .values(
                subject=written.subject,
                body=written.body,
                uniqueness_pct=uniqueness,
                status=MessageStatus.QUEUED,
                failure_reason=None,
            )
            .returning(MessageModel.id)
            .execution_options(synchronize_session="fetch")
        )
        if refreshed.first() is None:
            return False
        row.link.chain_hypothesis_id = plan.chain.hypothesis_id
        row.link.language, row.link.chain_version = plan.chain.language, plan.chain.version
        row.link.followups = texts
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
                followups=texts,
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
    #: Лидов `ready`, которым сборка напишет первое письмо сейчас (`elsewhere.writable`): письма
    #: нет или оно снято до отправки, сайт не в работе у другого направления.
    unwritten: int
    #: Первых писем гипотезы в очереди — ждут отправки (их же пересобирает сборка).
    queued: int
    #: Писем продаж в очереди — всех гипотез: пачка (`send_queue` этапа продаж) берёт
    #: очередь этапа целиком, и кнопка называет это число, а не число гипотезы. Как и у
    #: пачки, только первые письма: добивка в очереди ждёт свой ящик и проход добивок.
    stage_queued: int
    #: Те же лиды без письма — по языку письма (основой кода, как у сборки): плашка «отправлять
    #: нечего» называет неполную цепочку только того языка, лиды которого ждут.
    unwritten_by_language: dict[str, int] = field(default_factory=dict)


async def state(session: AsyncSession, hypothesis_id: int) -> QueueState:
    """Очередь гипотезы для экрана и консоли — без записи."""
    chains = [
        await chain.resolve(session, hypothesis_id=hypothesis_id, language=code)
        for code in chain_text.LANGUAGES
    ]
    # Язык — основой кода тем же правилом, что у сборки (`language_base`): разных записей языка
    # у гипотезы — единицы, группа по ним дешевле правила в SQL.
    written = await session.execute(
        select(SalesLeadModel.language, func.count())
        .select_from(SalesLeadModel)
        .outerjoin(SalesThreadModel, SalesThreadModel.lead_id == SalesLeadModel.id)
        .where(SalesLeadModel.hypothesis_id == hypothesis_id, elsewhere.writable())
        .group_by(SalesLeadModel.language)
    )
    by_language: Counter[str] = Counter()
    for language, count in written.tuples().all():
        by_language[language_base(language)] += count
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
        sum(by_language.values()),
        int(queued or 0),
        int(stage_queued or 0),
        # Лид без языка — в общем числе, а не по языкам: цепочки у него нет ни на одном.
        {code: count for code, count in by_language.items() if code},
    )
