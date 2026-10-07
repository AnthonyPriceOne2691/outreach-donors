"""Пробный рекламодатель: проверить оффер Этапа 2 на себе, не трогая настоящих.

Оффер пишется под найденную ссылку — площадку, страницу и анкор
(`letters/recipients.py`), и проверить его целиком — сборку, текст, отправку,
ответ-лид — можно только настоящим письмом. Настоящему рекламодателю
проверочный оффер не пошлёшь: письмо на сайт одно (`letters/attempts.py`),
и проверка заняла бы место настоящего письма.

**Домен и адрес — как у липового донора** (`donors/probe.py`): домен в зоне
`.invalid`, адрес — свой ящик. Предохранитель здесь обязателен: ящик стоит
в списке учётки Этапа 2, иначе отказ. Чистка `prune --probes` уносит пробного
целиком — с письмами и перепиской.

**Ссылка — настоящая**, из обхода названного донора: лучшая у его кандидатов,
а без кандидатов — ссылка обхода с анкором. Проверяется то самое письмо,
которое получил бы рекламодатель с этой страницы.

**Сборка должна взять его — и первым.** Условия те же, что у сборки
(`Recipients.advertiser_candidates`): свежая цена донора-площадки, адрес вне
стоп-листа, писем домену ещё не было. Рекламодателей сборка берёт по баллу,
и балл пробного — на единицу выше лучшего настоящего: сборка с лимитом 1
возьмёт его, а не настоящего. Последнее слово — за запросом самой сборки:
если первым она берёт не пробного, это отказ с причиной, а не пробный,
которому письмо молча не соберётся.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import filters as filters_cfg
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, ContactStatus, Stage
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.crawl import CrawlRunModel, OutLinkModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import MessageModel
from backend.features.donors.host import normalize_host
from backend.features.donors.probe import (
    ProbeError,
    checked_email,
    checked_host,
    fake_domain,
    own_address,
)
from backend.features.letters.recipients import Recipients
from backend.shared.net.url_parts import split_url

#: Откуда рекламодатель: ссылка на доноре — как у найденных обходом (`promote.py`).
LINKS = "links"


@dataclass(frozen=True, slots=True)
class DonorLink:
    """Ссылка из обхода донора, под которую пишется оффер."""

    crawl_id: int
    page_url: str
    anchor: str
    #: Чей сайт ссылка рекламирует на самом деле — для показа.
    target: str


@dataclass(frozen=True, slots=True)
class ProbeAdvertiser:
    advertiser_id: int
    domain_id: int
    host: str
    email: str
    donor_host: str
    link: DonorLink
    #: На единицу выше лучшего настоящего: сборка берёт пробного первым.
    points: int
    #: Заведён сейчас — или уже был, и повтор обновил ссылку, ничего не задвоив.
    created: bool


async def make_probe_advertiser(
    session: AsyncSession,
    *,
    host: str,
    email: str,
    donor: str,
    author: str,
    now: datetime | None = None,
) -> ProbeAdvertiser:
    """Завести пробного рекламодателя на ссылке донора. Без коммита.

    Отказ сборки — после того, как строки заведены: её запрос смотрит на них
    самих. Поэтому заведение идёт точкой сохранения, и отказ откатывает его —
    в базе не остаётся пробного, которого сборка не возьмёт.
    """
    host, email = checked_host(host), checked_email(email, Stage.ADVERTISERS, required=True)
    donor_host = await _priced_donor(session, donor, now or datetime.now(UTC))
    link = await donor_link(session, donor_host)
    async with session.begin_nested():
        domain, created = await fake_domain(session, host)
        advertiser = await _advertiser(session, domain.id, donor_host, link)
        await own_address(session, domain, email)
        await _taken_first(session, domain.id, email)
    await AccessRepository(session).record(
        AuditAction.PROBE_CREATED,
        target=f"advertiser:{advertiser.id}",
        details={
            "домен": host,
            "адрес": email,
            "донор": donor_host,
            "страница": link.page_url,
            "анкор": link.anchor,
            "обход": link.crawl_id,
            "кто": author,
        },
    )
    return ProbeAdvertiser(
        advertiser_id=advertiser.id,
        domain_id=domain.id,
        host=host,
        email=email,
        donor_host=donor_host,
        link=link,
        points=advertiser.points,
        created=created,
    )


def donor_host_of(raw: str) -> str:
    """Хост донора, как его пишет обход (`crawl/repository.queue_crawl`): без схемы,
    пути и `www.`; корень — по списку суффиксов, если зона в нём есть (как
    `crawl/links.py`): иначе выдуманные зоны тестов и `.local` теряли бы хост."""
    value = raw.strip().lower()
    split = split_url(value if "//" in value else f"//{value}")
    host = (split.hostname if split else None) or ""
    return normalize_host(host) or host.removeprefix("www.")


async def _priced_donor(session: AsyncSession, raw: str, moment: datetime) -> str:
    """Донор-площадка со свежей ценой — то, без чего сборка оффер не соберёт."""
    donor_host = donor_host_of(raw)
    if not donor_host:
        raise ProbeError(f"«{raw}» — не домен донора")
    row = (
        await session.execute(
            select(DonorModel.last_price, DonorModel.last_price_at)
            .join(DomainModel, DomainModel.id == DonorModel.domain_id)
            .where(DomainModel.host == donor_host)
        )
    ).first()
    if row is None:
        raise ProbeError(
            f"Донора {donor_host} в базе нет: ссылка пробному берётся из обхода донора — "
            "назовите донора, которого обходили"
        )
    ttl = filters_cfg.PRICE_TTL_DAYS
    if row.last_price is None or row.last_price_at is None:
        raise ProbeError(
            f"У донора {donor_host} цены нет: оффер собирается, только если цена площадки "
            f"известна и не старше {ttl} дней, — пробный в сборку не попал бы. Сначала "
            "цена донора (Этап 1)"
        )
    if row.last_price_at < moment - timedelta(days=ttl):
        raise ProbeError(
            f"Цена донора {donor_host} от {row.last_price_at:%d.%m.%Y} старше {ttl} дней: "
            "оффер на протухшей цене сборка не соберёт. Сначала перезапрос цены"
        )
    return donor_host


async def donor_link(session: AsyncSession, donor_host: str) -> DonorLink:
    """Ссылка оффера из обхода донора: лучшая у кандидатов, без них — ссылка
    обхода с анкором. Обхода нет или ссылок с анкором нет — отказ словами."""
    found = await _best_candidate(session, donor_host) or await _outlink(session, donor_host)
    if found is not None:
        return found
    crawled = await session.scalar(
        select(func.count(CrawlRunModel.id)).where(CrawlRunModel.host == donor_host)
    )
    if not crawled:
        raise ProbeError(
            f"Донора {donor_host} не обходили: ссылку пробному взять неоткуда. Сначала "
            "обход — «Рекламодатели» → «Обход доноров»"
        )
    raise ProbeError(
        f"Обход донора {donor_host} не нашёл ни одной ссылки с анкором: оффер не под что писать"
    )


async def _best_candidate(session: AsyncSession, donor_host: str) -> DonorLink | None:
    """Лучшая ссылка кандидата с последнего обхода, где кандидаты есть: по баллу —
    ровно её рекламодатель получил бы в `best_*` при переводе (`promote.py`)."""
    row = (
        await session.execute(
            select(
                CandidateModel.crawl_run_id,
                CandidateModel.best_page_url,
                CandidateModel.best_anchor,
                CandidateModel.target_root,
            )
            .join(CrawlRunModel, CrawlRunModel.id == CandidateModel.crawl_run_id)
            .where(
                CrawlRunModel.host == donor_host,
                func.trim(CandidateModel.best_page_url) != "",
                func.trim(CandidateModel.best_anchor) != "",
            )
            .order_by(
                CandidateModel.crawl_run_id.desc(), CandidateModel.points.desc(), CandidateModel.id
            )
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    crawl_id, page_url, anchor, target = row
    return DonorLink(crawl_id, (page_url or "").strip(), (anchor or "").strip(), target)


async def _outlink(session: AsyncSession, donor_host: str) -> DonorLink | None:
    """Кандидатов нет — ссылка обхода с анкором: из тела статьи первой."""
    row = (
        await session.execute(
            select(
                OutLinkModel.crawl_run_id,
                OutLinkModel.page_url,
                OutLinkModel.anchor,
                OutLinkModel.target_root,
            )
            .join(CrawlRunModel, CrawlRunModel.id == OutLinkModel.crawl_run_id)
            .where(CrawlRunModel.host == donor_host, func.trim(OutLinkModel.anchor) != "")
            .order_by(
                OutLinkModel.crawl_run_id.desc(), OutLinkModel.in_body.desc(), OutLinkModel.id
            )
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    crawl_id, page_url, anchor, target = row
    return DonorLink(crawl_id, page_url.strip(), anchor.strip(), target)


async def _advertiser(
    session: AsyncSession, domain_id: int, donor_host: str, link: DonorLink
) -> AdvertiserModel:
    """Строка рекламодателя с найденной ссылкой и баллом выше всех настоящих."""
    row = await session.scalar(
        select(AdvertiserModel).where(AdvertiserModel.domain_id == domain_id)
    )
    if row is None:
        row = AdvertiserModel(domain_id=domain_id)
        session.add(row)
    best = await session.scalar(
        select(func.max(AdvertiserModel.points)).where(AdvertiserModel.domain_id != domain_id)
    )
    row.points = (best or 0) + 1
    row.donors, row.links, row.source = 1, 1, LINKS
    row.best_donor_host, row.best_page_url, row.best_anchor = donor_host, link.page_url, link.anchor
    # Адрес вписан руками — «найден», как адрес с карточки (`contacts/manual.py`).
    row.contact_status = ContactStatus.FOUND
    await session.flush()
    return row


async def _taken_first(session: AsyncSession, domain_id: int, email: str) -> None:
    """Сборка офферов с лимитом 1 берёт пробного — проверено её же запросом."""
    picked = await Recipients(session).advertiser_candidates(limit=1)
    if picked and picked[0].domain_id == domain_id:
        return
    written = await session.scalar(
        select(func.count(MessageModel.id)).where(MessageModel.domain_id == domain_id)
    )
    if written:
        raise ProbeError(
            "Пробному уже писали — письмо на сайт одно, второе сборка не соберёт. Убрать "
            "его с перепиской (outreach prune --probes --yes) и завести заново"
        )
    stopped = await session.scalar(
        select(func.count(SuppressionModel.id)).where(
            or_(SuppressionModel.domain_id == domain_id, SuppressionModel.email == email),
            or_(SuppressionModel.stage.is_(None), SuppressionModel.stage == Stage.ADVERTISERS),
            SuppressionModel.in_force(datetime.now(UTC)),
        )
    )
    if stopped:
        raise ProbeError(
            f"Адрес {email} или домен пробного в стоп-листе — оффер на него сборка "
            "не соберёт. Снять — экран стоп-листа, с причиной"
        )
    raise ProbeError(
        "Сборка офферов первым берёт не пробного — смотреть воронку на экране "
        "«Письма → Рекламодателям»"
    )
