"""Лиды из кандидатов сбора: принятым с живым адресом на домене — лид гипотезы, затем
та же очистка, что у загрузки базы (`cleaning.clean_leads`).

**Адрес — лучший живой адрес домена** по общему порядку (`contacts/preference.preferred_first`:
с которого отвечали, вписанный человеком, по оценке проверки, старший); мёртвый (отказ
доставки) не берётся вовсе. Ссылка лида на общую строку адреса — та же, что у лида из
файла (`contact_id`, `SET NULL`), а сам адрес лид хранит у себя (решение 01.10).

**Лид — источник `collected`**, страна — рынок выдачи прогона, пояс — по стране, язык — язык писем
прогона, а если сайт уверенно на другом языке и без версии на языке писем (переключатель в меню или
страница выдачи на нём) — язык сайта (`segment_judge.site_language`: тем же решением и по тому же
тексту, что вердикт судьи «язык сайта — …, письма — …»; такой сайт стал принятым только словом
человека). Язык сайта буквами не назван — язык его письменности из таблицы (`Mismatch.then`:
латиница — английский; его же называют слова вердикта «…; примете — английский»), письменность без
строки — язык прогона. На языке без цепочки лид ждёт с причиной в отчёте очереди, а не уходит
письмом не на том языке. Имени, должности и компании сбор не знает: первое письмо и так спрашивает,
кто отвечает за SEO и маркетинг.

**Дубли, стоп-листы, отписки, чужое направление и мёртвые адреса** отсекает очистка — та же,
что у загрузки, без копии: адрес уже у лида продаж — «дубль», домен в работе у доноров или
рекламодателей — «домен в работе у другого направления». Журнал — запись загрузки базы:
по нему ищут, откуда взялся лид.

**Сайт, у домена которого уже есть живой лид продаж любой гипотезы, лида не даёт**
(решение владельца 14.10, «компания — в одной гипотезе»), даже если готовый адрес домена
лежит в `contacts`: у компании лид уже есть, а заведённый оплатил бы проверку адреса — и
своей гипотезе, где очистка его пропустила бы. Условие то же, что у очереди поиска адреса
(`company_hold.held`); итог такого сайта в отчёте — «компания уже в работе гипотезы …».

**«Принять» после отмены — тот же лид, а не второй** (`_undone`, тест продаж 21.10). Отмена
решения снимает лида сайта, пока письмо ему не ушло: «отклонён» с причиной «решение отменено»
(`segment_undo`). Нового лида с тем же адресом очистка отсекла бы дублем снятого («адрес уже у
лида №…»), и сайт ушёл бы из сбора насовсем. Поэтому принятому сайту возвращается снятый отменой
лид его гипотезы на том же домене — тем же видом, что новый (`_lead`): лучший живой адрес сейчас,
«новый» без прежнего вердикта, и очистка проверит его целиком. Его снятое письмо сборка пишет
заново на том же месте (`off_queue.again`): второго письма тому же человеку нет.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Exists, and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from backend.features.access.repository import AccessRepository
from backend.features.contacts.preference import DEAD, preferred_first
from backend.features.core.domain import AuditAction, Stage
from backend.features.core.models.donor import ContactModel
from backend.features.sales import clean_jobs, cleaning, company_hold, geo
from backend.features.sales.models import (
    LeadSource,
    LeadStatus,
    RejectionReason,
    SalesLeadModel,
)
from backend.features.sales.segment_judge import SiteText, site_language
from backend.features.sales.segment_models import (
    Lane,
    SalesSegmentCandidateModel,
    SalesSegmentRunModel,
)
from backend.features.sales.verifier import EmailVerifier

logger = logging.getLogger(__name__)

Candidate = SalesSegmentCandidateModel


def _alive() -> ColumnElement[bool]:
    return ContactModel.verification_status.is_distinct_from(DEAD)


def living_address(domain_id: ColumnElement[int] | InstrumentedAttribute[int]) -> Exists:
    """У домена есть живой адрес — строка `contacts` без отметки отказа доставки."""
    return select(ContactModel.id).where(ContactModel.domain_id == domain_id, _alive()).exists()


def lead_ready(run_id: int) -> ColumnElement[bool]:
    """Принятые кандидаты прогона без лида, чей домен даёт лида из живого адреса, — без чужого
    живого лида на домене (`company_hold.held`). Одно условие у этапа лидов и у числа «ждут» в
    отчёте прогона (`segment_report`)."""
    return and_(
        Candidate.run_id == run_id,
        Candidate.lane == Lane.ACCEPT,
        Candidate.lead_id.is_(None),
        living_address(Candidate.domain_id),
        ~company_hold.held(Candidate.domain_id),
    )


async def _ready(
    session: AsyncSession, run_id: int, since: datetime | None = None
) -> list[tuple[Candidate, int, str]]:
    """Принятые кандидаты прогона без лида — и лучший живой адрес их домена; домен с живым
    лидом продаж лида не даёт (`company_hold.held`). `since` — только те, кому поиск записал
    исход с этого момента: повтор по сроку заводит лиды лишь из найденного им самим.

    **Строки взятых — под замком до конца транзакции заведения** (`FOR UPDATE OF` кандидатов):
    человек может отменить решение «принят» посреди прохода (`segment_undo`). Без замка проход,
    прочитавший сайт принятым, заводил лида уже после отмены — сайт «на проверке», а лид есть.
    С замком отмена ждёт конца заведения, видит лида и снимает его, а заведение, пришедшее
    посреди отмены, ждёт её и перечитывает строку: сайт уже не принят — лида нет."""
    best = (
        select(ContactModel.domain_id, ContactModel.id, ContactModel.email)
        .distinct(ContactModel.domain_id)
        .where(_alive())
        .order_by(ContactModel.domain_id, *preferred_first())
        .subquery()
    )
    query = (
        select(Candidate, best.c.id, best.c.email)
        .join(best, best.c.domain_id == Candidate.domain_id)
        .where(lead_ready(run_id))
        .order_by(Candidate.id)
        .with_for_update(of=Candidate)
    )
    if since is not None:
        query = query.where(Candidate.contact_attempted_at >= since)
    return list((await session.execute(query)).tuples().all())


def lead_language(run: SalesSegmentRunModel, candidate: Candidate) -> str:
    """Язык писем лиду: сайт уверенно на другом языке — язык, названный буквами, а не назван — язык
    письменности сайта из таблицы (`Mismatch.then`: латиница — английский; тот же, что в словах
    вердикта «примете — …»); иначе (сайт на языке писем или у него есть версия на нём, не уверены,
    письменности нет в таблице) — язык прогона."""
    found = site_language(SiteText.parse(candidate.site_text or ""), run.language)
    if found is None:
        return run.language
    return found.code or found.then or run.language


def _fields(
    run: SalesSegmentRunModel, candidate: Candidate, contact_id: int, email: str
) -> dict[str, Any]:
    """Лид принятого сайта — новый или вернувшийся после отмены (`_lead`): одно правило."""
    return {
        "hypothesis_id": run.hypothesis_id,
        "domain_id": candidate.domain_id,
        "contact_id": contact_id,
        "email": email.strip().lower(),
        "country": run.country,
        "timezone": geo.timezone_for(run.country),
        "language": lead_language(run, candidate),
        "source": LeadSource.COLLECTED,
        "status": LeadStatus.NEW,
    }


#: Что снятому отменой лиду не возвращается: причина, слова очистки, вердикт и срок перепроверки.
_FORGOTTEN = dict.fromkeys(
    (
        "rejection_reason",
        "cleaning_note",
        "verification_status",
        "verification_score",
        "verified_at",
        "verify_since",
        "verify_again_at",
    )
)
#: Доменов в одном `IN`: параметров у Postgres не больше 32 767 на запрос.
CHUNK = 1000


async def _undone(
    session: AsyncSession, hypothesis_id: int, ready: list[tuple[Candidate, int, str]]
) -> dict[int, SalesLeadModel]:
    """Лиды сбора гипотезы на доменах взятых сайтов (`ready`), снятые отменой «Принять»
    (`segment_undo`), — по домену, под замком строк: ранний первым. У сайта такой лид один —
    второй завёлся бы, только если бы «Принять» снова не вернуло первого; ранний и есть оригинал
    своего адреса для правила дублей."""
    found: dict[int, SalesLeadModel] = {}
    ordered = sorted({candidate.domain_id for candidate, _, _ in ready})
    for start in range(0, len(ordered), CHUNK):
        rows = await session.scalars(
            select(SalesLeadModel)
            .where(
                SalesLeadModel.hypothesis_id == hypothesis_id,
                SalesLeadModel.domain_id.in_(ordered[start : start + CHUNK]),
                SalesLeadModel.source == LeadSource.COLLECTED,
                SalesLeadModel.status == LeadStatus.REJECTED,
                SalesLeadModel.rejection_reason == RejectionReason.UNDONE.value,
            )
            .order_by(SalesLeadModel.id)
            .with_for_update()
        )
        for lead in rows.all():
            found.setdefault(lead.domain_id, lead)
    return found


def _lead(
    ready: tuple[Candidate, int, str], run: SalesSegmentRunModel, undone: dict[int, SalesLeadModel]
) -> SalesLeadModel:
    """Лид принятого сайта: снятый отменой лид его домена — снова «новый» тем же видом, что новый
    (`_fields`: лучший живой адрес сейчас, страна, пояс и язык прогона), без прежнего вердикта и
    срока перепроверки — очистка проверит его целиком, и новый `unknown` не сойдёт за второй
    (`unconfirmed.judged`); имя, должность и компания, вписанные человеком, остаются. Иначе — новый."""
    candidate, contact_id, email = ready
    fields = _fields(run, candidate, contact_id, email)
    lead = undone.get(candidate.domain_id)
    if lead is None:
        return SalesLeadModel(**fields)
    for name, value in (fields | _FORGOTTEN).items():
        setattr(lead, name, value)
    return lead


async def make_leads(
    session: AsyncSession,
    run: SalesSegmentRunModel,
    verifier: EmailVerifier,
    *,
    since: datetime | None = None,
) -> dict[str, Any]:
    """Завести лидов принятым с адресом и очистить их; снятый отменой лид сайта возвращается, а не
    заводится второй (`_undone`). Коммит — за вызывающим: лиды, ссылки кандидатов, журнал, итог
    очистки и строка расхода проверки — одна транзакция. `since` — только из найденного поиском с
    этого момента (`_ready`)."""
    ready = await _ready(session, run.id, since)
    if not ready:
        return {"created": 0}
    undone = await _undone(session, run.hypothesis_id, ready)
    pairs = [(found[0], _lead(found, run, undone)) for found in ready]
    session.add_all([lead for _, lead in pairs])
    await session.flush()
    for candidate, lead in pairs:
        candidate.lead_id = lead.id
    await _journal(session, run, made=len(pairs), back=len(undone))
    found = await cleaning.clean_leads(session, [lead for _, lead in pairs], verifier)
    logger.info(
        "сбор продаж: лиды заведены",
        extra={"run": run.id, "leads": len(pairs), "back": len(undone)},
    )
    return {"created": len(pairs), "cleaning": clean_jobs.report_of(found)}


async def _journal(
    session: AsyncSession, run: SalesSegmentRunModel, *, made: int, back: int
) -> None:
    """Заведение — записью загрузки базы: по ней ищут, откуда взялся лид, которому ушло письмо.
    Вернувшиеся после отмены решения по сайту (`back`, из них же) — своим числом."""
    details: dict[str, Any] = {
        "источник": f"сбор по ключам, прогон №{run.id}",
        "загружено": made,
        "отклонено": 0,
        "запустил": run.created_by,
    }
    if back:
        details["вернулось после отмены решения по сайту"] = back
    await AccessRepository(session).record(
        AuditAction.SALES_LEADS_IMPORTED,
        target=f"sales_hypothesis:{run.hypothesis_id}",
        details=details,
        stage=Stage.SALES,
    )
