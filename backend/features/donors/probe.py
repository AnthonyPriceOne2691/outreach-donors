"""Липовый донор: проверить всю цепочку на живом сервисе, не трогая настоящих.

Письмо → отправка → ответ → привязка к переписке → разбор цены → цена в карточке
→ добивки — всё это проверяется только настоящей отправкой и настоящим ответом.
На настоящем доноре такая проверка оставила бы ему тестовую переписку и тестовую
цену, а удалить адрес с перепиской сервис не даёт — и правильно.

**Домен — в зоне `.invalid`.** Она зарезервирована (RFC 6761): настоящий сайт
так не назовут, выдача его не принесёт, и чистка (`outreach prune --probes`)
вправе убрать такой домен целиком — с письмами, перепиской и ответами.

**Адрес — свой ящик проверяющего.** Пока стоит предохранитель, только из списка
разрешённых: иначе письмо всё равно не уйдёт, и проверка кончится, не начавшись.

**К донору — проверочный прогон.** Письмо собирается штатной сборкой по прогону
(`letters-build --runs N`), и в рассылку не попадает никто, кроме липового.
Прогон «остановлен», а не «завершён»: смета берёт историю только завершённых,
и проверка не должна сдвигать её.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, delete, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import outreach as outreach_cfg
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import (
    AuditAction,
    ContactSource,
    DonorStatus,
    RunStatus,
    Stage,
)
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.core.models.run import RunCandidateModel, RunModel
from backend.features.letters.sendgrid import allowed_recipient
from backend.features.runs.repository import RunRepository
from backend.features.runs.thresholds import ThresholdsRepository

#: Зарезервированная зона: домен в ней не бывает настоящим сайтом.
PROBE_ZONE = ".invalid"
#: Ключ проверочного прогона — им же прогон узнаётся при повторе.
PROBE_KEYWORD = "проверка цепочки"
PROBE_REASON = "проверочный прогон: липовый донор для проверки цепочки (outreach probe-donor)"


class ProbeError(ValueError):
    """Липового донора завести нельзя. Сообщение говорит почему и что сделать."""


@dataclass(frozen=True, slots=True)
class Probe:
    run_id: int
    domain_id: int
    donor_id: int
    host: str
    email: str
    #: Заведён сейчас — или уже был, и повтор ничего не задвоил.
    created: bool


def is_probe_host(host: str) -> bool:
    return host.strip().lower().endswith(PROBE_ZONE)


def _checked(host: str, email: str) -> tuple[str, str]:
    host, email = host.strip().lower(), email.strip().lower()
    if not is_probe_host(host) or host == PROBE_ZONE.lstrip("."):
        raise ProbeError(
            f"«{host}» — не липовый домен: нужен домен в зоне {PROBE_ZONE}, например "
            f"probe{PROBE_ZONE}. Настоящий сайт так не назовут, и чистка уберёт его целиком"
        )
    if email.count("@") != 1 or not email.split("@")[0] or "." not in email.split("@")[1]:
        raise ProbeError(f"«{email}» — не адрес почты")
    account = outreach_cfg.mail_account(Stage.DONORS.value)
    if account.allowed_recipients and not allowed_recipient(email, account.allowed_recipients):
        raise ProbeError(
            f"Адреса {email} нет в списке разрешённых ({account.allowlist_setting}): "
            "пока стоит предохранитель, письмо на него не уйдёт. Взять свой ящик из списка"
        )
    return host, email


async def _domain(session: AsyncSession, host: str) -> tuple[DomainModel, bool]:
    found = await session.scalar(select(DomainModel).where(DomainModel.host == host))
    if found is not None:
        return found, False
    domain = DomainModel(host=host)
    session.add(domain)
    await session.flush()
    return domain, True


async def _donor(session: AsyncSession, domain: DomainModel, author: str) -> DonorModel:
    """Годный и принятый: письма собираются только таким. Метрик нет — они платные,
    а проверяется не отбор, а то, что после него."""
    donor = await session.scalar(select(DonorModel).where(DonorModel.domain_id == domain.id))
    if donor is None:
        donor = DonorModel(domain_id=domain.id)
        session.add(donor)
    donor.status = DonorStatus.SUITABLE
    if donor.review != "accepted":
        donor.review, donor.review_at, donor.review_by = "accepted", datetime.now(UTC), author
    await session.flush()
    return donor


async def _contact(session: AsyncSession, domain: DomainModel, email: str) -> None:
    exists = await session.scalar(
        select(ContactModel.id).where(
            ContactModel.domain_id == domain.id, ContactModel.email == email
        )
    )
    if exists is None:
        session.add(ContactModel(domain_id=domain.id, email=email, source=ContactSource.MANUAL))
        await session.flush()


async def _run(session: AsyncSession, domain: DomainModel, author: str) -> RunModel:
    """Проверочный прогон этого донора: прежний, если уже есть, — повтор не множит прогоны."""
    for run in await session.scalars(
        select(RunModel).where(RunModel.status == RunStatus.STOPPED).order_by(RunModel.id)
    ):
        if (run.candidates or {}).get("hosts") == [domain.host]:
            return run
    settings = await ThresholdsRepository(session).current()
    if settings is None:
        raise ProbeError(
            "Порогов ещё нет — прогону не на что сослаться. Сохранить пороги на экране "
            "настроек или запустить обычный прогон"
        )
    run = await RunRepository(session).create_run(
        stage=Stage.DONORS,
        settings_id=settings.id,
        keywords=[PROBE_KEYWORD],
        country="us",
        status=RunStatus.STOPPED,
    )
    run.candidates = {"hosts": [domain.host], "found_by": {domain.host: [PROBE_KEYWORD]}}
    run.stats = {"причина": PROBE_REASON}
    session.add(
        RunCandidateModel(
            run_id=run.id,
            domain_id=domain.id,
            status="accepted",
            decided_by=author,
            decided_at=datetime.now(UTC),
            note="липовый донор",
        )
    )
    await session.flush()
    return run


async def make_probe(session: AsyncSession, *, host: str, email: str, author: str) -> Probe:
    """Завести липового донора с адресом и проверочным прогоном. Без коммита."""
    host, email = _checked(host, email)
    domain, created = await _domain(session, host)
    donor = await _donor(session, domain, author)
    await _contact(session, domain, email)
    run = await _run(session, domain, author)
    await AccessRepository(session).record(
        AuditAction.PROBE_CREATED,
        target=f"donor:{donor.id}",
        details={"домен": host, "адрес": email, "прогон": run.id, "кто": author},
    )
    return Probe(
        run_id=run.id,
        domain_id=domain.id,
        donor_id=donor.id,
        host=host,
        email=email,
        created=created,
    )


def probe_domain() -> ColumnElement[bool]:
    """Домен липового донора — условием запроса: зона `.invalid`."""
    return DomainModel.host.like(f"%{PROBE_ZONE}")


@dataclass(slots=True)
class ProbeTrace:
    """Что оставили липовые доноры — уходит целиком при `prune --probes`."""

    domains: list[int] = field(default_factory=list)
    #: Проверочные прогоны: в выдаче только липовые домены.
    runs: list[int] = field(default_factory=list)
    letters: int = 0
    threads: int = 0
    replies: int = 0
    #: Рассылки, где только липовые письма и переписка: без них они пусты.
    campaigns: list[int] = field(default_factory=list)

    def as_details(self) -> dict[str, Any]:
        return {
            "липовых доноров": len(self.domains),
            "проверочные прогоны": self.runs,
            "писем": self.letters,
            "переписок": self.threads,
            "ответов": self.replies,
            "рассылки": self.campaigns,
        }


async def _count(session: AsyncSession, statement: Select[tuple[int]]) -> int:
    return int(await session.scalar(statement) or 0)


async def probe_trace(session: AsyncSession) -> ProbeTrace:
    """Липовые доноры и всё, что за ними тянется. Ничего не меняет."""
    trace = ProbeTrace()
    trace.domains = list(
        (await session.scalars(select(DomainModel.id).where(probe_domain()))).all()
    )
    for run in await session.scalars(select(RunModel).order_by(RunModel.id)):
        hosts = (run.candidates or {}).get("hosts") or []
        if hosts and all(is_probe_host(str(host)) for host in hosts):
            trace.runs.append(run.id)
    if not trace.domains:
        return trace
    ids = trace.domains
    trace.letters = await _count(
        session, select(func.count(MessageModel.id)).where(MessageModel.domain_id.in_(ids))
    )
    trace.threads = await _count(
        session, select(func.count(ThreadModel.id)).where(ThreadModel.domain_id.in_(ids))
    )
    trace.replies = await _count(
        session,
        select(func.count(ReplyModel.id))
        .join(ThreadModel, ThreadModel.id == ReplyModel.thread_id)
        .where(ThreadModel.domain_id.in_(ids)),
    )
    elsewhere = or_(
        exists().where(
            MessageModel.campaign_id == CampaignModel.id, MessageModel.domain_id.not_in(ids)
        ),
        exists().where(
            ThreadModel.campaign_id == CampaignModel.id, ThreadModel.domain_id.not_in(ids)
        ),
    )
    touched = exists().where(
        MessageModel.campaign_id == CampaignModel.id, MessageModel.domain_id.in_(ids)
    )
    trace.campaigns = list(
        (
            await session.scalars(
                select(CampaignModel.id).where(touched, ~elsewhere).order_by(CampaignModel.id)
            )
        ).all()
    )
    return trace


async def remove_probes(session: AsyncSession, trace: ProbeTrace) -> None:
    """Убрать липовых доноров с письмами и перепиской. Без коммита.

    Письма — первыми: на домен они ссылаются без каскада, и база не дала бы
    удалить домен с письмами. Переписка и ответы уходят с доменом каскадом.
    Зона проверяется и здесь, в самом запросе: настоящий домен этим путём
    не удаляется, что бы ни лежало в плане.
    """
    if not trace.domains:
        return
    probes = select(DomainModel.id).where(DomainModel.id.in_(trace.domains), probe_domain())
    await session.execute(delete(MessageModel).where(MessageModel.domain_id.in_(probes)))
    await session.execute(
        delete(DomainModel).where(DomainModel.id.in_(trace.domains), probe_domain())
    )
    if trace.campaigns:
        # Пустая — без писем и без переписки: переписка ушла бы с рассылкой каскадом.
        emptied = and_(
            ~exists().where(MessageModel.campaign_id == CampaignModel.id),
            ~exists().where(ThreadModel.campaign_id == CampaignModel.id),
        )
        await session.execute(
            delete(CampaignModel).where(CampaignModel.id.in_(trace.campaigns), emptied)
        )
