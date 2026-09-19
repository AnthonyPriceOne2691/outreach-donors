"""Демонстрационные данные: домены рассылки, доноры, переписка.

Зачем это в репозитории. Экраны рассылки и диалогов рисуются раньше,
чем появятся почтовые домены и уйдёт первое письмо, — иначе их пришлось
бы рисовать «вслепую» и переделывать после первого же взгляда на живые
данные. Пустой экран не показывает ни одной из проблем, ради которых
экран и делается: как выглядит домен на третьем дне разгона, как —
диалог, где ответ пришёл с другого адреса, и что видно, когда цена
названа в теле письма.

**Всё выдуманное помечено.** Домены оканчиваются на `.example.test` —
зона, зарезервированная под примеры, писем туда не уходит; кампания
называется «Демонстрация». Команда `--clear` убирает ровно эти записи
и не трогает остальные: перепутать демонстрацию с боевыми данными
нельзя ни на экране, ни в базе.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.core.domain import (
    ContactSource,
    DonorStatus,
    MessageStatus,
    ReplyKind,
    SenderStatus,
    Stage,
    ThreadStatus,
)
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    SenderModel,
    ThreadModel,
)

DEMO_SUFFIX = ".example.test"
DEMO_CAMPAIGN = "Демонстрация"

EXIT_OK = 0

#: Домены рассылки: разные состояния нарочно — свежий в разгоне, зрелый,
#: выключенный по отказам. Экран, на котором все домены одинаковы,
#: не показывает ничего.
SENDER_DOMAINS: list[tuple[str, int, int, int | None, bool, str | None]] = [
    # домен, ящиков, дневной кап, день разгона (None — разгон закончен), включён, причина паузы
    ("mail-alpha" + DEMO_SUFFIX, 2, 20, None, True, None),
    ("mail-beta" + DEMO_SUFFIX, 2, 20, 3, True, None),
    ("mail-gamma" + DEMO_SUFFIX, 1, 20, 1, True, None),
    ("mail-delta" + DEMO_SUFFIX, 1, 20, None, False, "доля отказов 7% — парковка"),
]

#: Доноры и то, чем закончился разговор с каждым. Набор подобран так,
#: чтобы на экране встретились все состояния диалога.
CONVERSATIONS: list[tuple[str, str, str, Decimal | None, Decimal | None]] = [
    # донор, адрес, чем кончилось, цена белая, цена серая
    (
        "digest-weekly" + DEMO_SUFFIX,
        "editor@digest-weekly" + DEMO_SUFFIX,
        "цена",
        Decimal("250"),
        Decimal("180"),
    ),
    ("city-news" + DEMO_SUFFIX, "info@city-news" + DEMO_SUFFIX, "цена", Decimal("400"), None),
    ("tech-review" + DEMO_SUFFIX, "ads@tech-review" + DEMO_SUFFIX, "ответ", None, None),
    ("green-blog" + DEMO_SUFFIX, "hello@green-blog" + DEMO_SUFFIX, "автоответ", None, None),
    ("travel-mag" + DEMO_SUFFIX, "editor@travel-mag" + DEMO_SUFFIX, "ждём", None, None),
    ("home-guide" + DEMO_SUFFIX, "contact@home-guide" + DEMO_SUFFIX, "ждём", None, None),
    ("food-diary" + DEMO_SUFFIX, "team@food-diary" + DEMO_SUFFIX, "отказ доставки", None, None),
    ("auto-parts" + DEMO_SUFFIX, "sales@auto-parts" + DEMO_SUFFIX, "отписка", None, None),
]

LETTER_BODY = (
    "Здравствуйте!\n\nПишу по поводу размещения статьи на вашем сайте. "
    "Подскажите, пожалуйста, стоимость размещения и есть ли условия "
    "по тематике.\n\nС уважением,\nотдел контента"
)

REPLIES = {
    "цена": (
        ReplyKind.HUMAN,
        "Здравствуйте!\n\nРазмещение статьи — {white} EUR, с пометкой «партнёрский "
        "материал» — {grey} EUR. Оплата по счёту или картой. Размещаем в течение "
        "трёх рабочих дней.\n\nС уважением,\nредакция",
    ),
    "ответ": (
        ReplyKind.HUMAN,
        "Добрый день! Прайс уточняю у главного редактора, вернусь с ответом на следующей неделе.",
    ),
    "автоответ": (
        ReplyKind.AUTO_REPLY,
        "Я в отпуске до понедельника. По срочным вопросам пишите коллеге.",
    ),
    "отказ доставки": (
        ReplyKind.BOUNCE,
        "Delivery has failed to these recipients: mailbox unavailable (550 5.1.1).",
    ),
    "отписка": (
        ReplyKind.UNSUBSCRIBE,
        "Просьба больше не писать на этот адрес.",
    ),
}


async def _clear(session: AsyncSession) -> int:
    """Убрать только демонстрационные записи."""
    campaigns = (
        (await session.execute(select(CampaignModel.id).where(CampaignModel.name == DEMO_CAMPAIGN)))
        .scalars()
        .all()
    )
    if campaigns:
        await session.execute(delete(MessageModel).where(MessageModel.campaign_id.in_(campaigns)))
        threads = (
            (
                await session.execute(
                    select(ThreadModel.id).where(ThreadModel.campaign_id.in_(campaigns))
                )
            )
            .scalars()
            .all()
        )
        if threads:
            await session.execute(delete(ReplyModel).where(ReplyModel.thread_id.in_(threads)))
            await session.execute(delete(ThreadModel).where(ThreadModel.id.in_(threads)))
        await session.execute(delete(CampaignModel).where(CampaignModel.id.in_(campaigns)))

    await session.execute(delete(SenderModel).where(SenderModel.domain.like(f"%{DEMO_SUFFIX}")))
    hosts = (
        (
            await session.execute(
                select(DomainModel.id).where(DomainModel.host.like(f"%{DEMO_SUFFIX}"))
            )
        )
        .scalars()
        .all()
    )
    if hosts:
        await session.execute(delete(ContactModel).where(ContactModel.domain_id.in_(hosts)))
        await session.execute(delete(DonorModel).where(DonorModel.domain_id.in_(hosts)))
        await session.execute(delete(DomainModel).where(DomainModel.id.in_(hosts)))
    await session.commit()
    return len(hosts)


async def _seed_senders(session: AsyncSession, now: datetime) -> int:
    made = 0
    for domain, boxes, cap, warmup_day, enabled, reason in SENDER_DOMAINS:
        for index in range(boxes):
            started = None if warmup_day is None else now - timedelta(days=warmup_day - 1)
            # Отправлено не больше сегодняшнего потолка: домен на первом дне
            # разгона с шестью письмами при потолке пять — это «6 из 5»
            # на экране, то есть данные, которых не бывает.
            allowance = cap if warmup_day is None else min(cap, 5 * warmup_day)
            sender = SenderModel(
                domain=domain,
                email=f"outreach{index + 1}@{domain}",
                stage=Stage.DONORS,
                daily_cap=cap,
                sent_today=min(cap // 3, allowance) if enabled else 0,
                status=SenderStatus.FREE if enabled else SenderStatus.PAUSED,
                enabled=enabled,
                warmup_started_at=started,
                paused_at=None if enabled else now - timedelta(days=2),
                pause_reason=reason,
            )
            session.add(sender)
            made += 1
    return made


async def _seed_threads(session: AsyncSession, now: datetime) -> int:
    campaign = CampaignModel(stage=Stage.DONORS, name=DEMO_CAMPAIGN, status="running")
    session.add(campaign)
    await session.flush()

    made = 0
    for order, (host, email, outcome, white, grey) in enumerate(CONVERSATIONS):
        domain = DomainModel(host=host)
        session.add(domain)
        await session.flush()

        session.add(
            DonorModel(
                domain_id=domain.id,
                status=DonorStatus.SUITABLE,
                dr=25 + order * 3,
                org_traffic=1200 + order * 900,
                metrics_refreshed_at=now - timedelta(days=order),
            )
        )
        contact = ContactModel(domain_id=domain.id, email=email, source=ContactSource.PAGE)
        session.add(contact)
        await session.flush()

        thread = ThreadModel(
            domain_id=domain.id,
            campaign_id=campaign.id,
            contact_id=contact.id,
            status=ThreadStatus.OPEN,
        )
        session.add(thread)
        await session.flush()

        # Отсчёт от двух недель назад: добивка уходит через семь дней после
        # первого письма, и при более близкой дате она оказывалась бы в
        # будущем — на экране это выглядит как сломанные часы.
        sent_at = now - timedelta(days=14 - order % 5, hours=order)
        session.add(
            MessageModel(
                campaign_id=campaign.id,
                thread_id=thread.id,
                domain_id=domain.id,
                contact_id=contact.id,
                step=0,
                status=(
                    MessageStatus.BOUNCED
                    if outcome == "отказ доставки"
                    else MessageStatus.DELIVERED
                ),
                subject="Стоимость размещения статьи",
                body=LETTER_BODY,
                uniqueness_pct=18.0 + order,
                sent_at=sent_at,
                idempotency_key=f"demo:{host}:0",
            )
        )
        if outcome == "ждём" and order % 2 == 0:
            session.add(
                MessageModel(
                    campaign_id=campaign.id,
                    thread_id=thread.id,
                    domain_id=domain.id,
                    contact_id=contact.id,
                    step=1,
                    status=MessageStatus.SENT,
                    subject="Re: Стоимость размещения статьи",
                    body="Добрый день! Поднимаю письмо — возможно, оно потерялось.",
                    uniqueness_pct=22.0,
                    sent_at=sent_at + timedelta(days=7),
                    idempotency_key=f"demo:{host}:1",
                )
            )

        if outcome in REPLIES:
            kind, template = REPLIES[outcome]
            body = template.format(white=white, grey=grey) if white else template
            session.add(
                ReplyModel(
                    thread_id=thread.id,
                    kind=kind,
                    raw_body=body,
                    price_white=white,
                    price_grey=grey,
                    currency="EUR" if white else None,
                    payment_methods=["счёт", "карта"] if white else None,
                    confidence=0.93 if white else None,
                )
            )
        made += 1
    return made


async def cmd_demo_seed(args: argparse.Namespace) -> int:
    """Набить базу выдуманными данными для показа экранов."""
    check_storage()
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    try:
        async with factory() as session:
            removed = await _clear(session)
            if args.clear:
                print(f"Демонстрационные данные убраны: доменов {removed}.")
                return EXIT_OK

            senders = await _seed_senders(session, now)
            threads = await _seed_threads(session, now)
            await session.commit()
    finally:
        await engine.dispose()

    print(f"Заведено: ящиков рассылки {senders}, диалогов {threads}.")
    print(f"Все домены оканчиваются на {DEMO_SUFFIX} — писем туда не уходит.")
    print("Убрать: outreach demo-seed --clear")
    return EXIT_OK
