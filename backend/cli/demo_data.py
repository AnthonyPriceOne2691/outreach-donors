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

from backend.cli.demo_content import (
    CONVERSATIONS,
    LETTER_BODY,
    QUEUE,
    REPLIES,
    SENDER_DOMAINS,
)
from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.core.domain import (
    ContactSource,
    DonorStatus,
    MessageStatus,
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
from backend.features.letters import compose
from backend.features.letters import template as letters_template
from backend.features.letters.uniqueness import difference
from backend.features.outreach.senders import warmup_state
from backend.shared import demo

#: Признак выдуманного домена один на весь сервис (`backend/shared/demo.py`):
#: по нему же нулевой транспорт решает, можно ли отправлять. Вторая копия
#: строки означала бы, что однажды заглушка сочтёт боевой домен выдуманным.
DEMO_SUFFIX = demo.SUFFIX
DEMO_CAMPAIGN = "Демонстрация"
#: Отдельная кампания под письма, ушедшие сегодня: по ней видно дневной
#: расход ящиков, и убирается она вместе с остальной демонстрацией.
DEMO_CAMPAIGN_TODAY = "Демонстрация: сегодня"
#: Кампания под очередь: письма, которые ещё ждут решения человека.
DEMO_CAMPAIGN_QUEUE = "Демонстрация: очередь"
DEMO_CAMPAIGNS = (DEMO_CAMPAIGN, DEMO_CAMPAIGN_TODAY, DEMO_CAMPAIGN_QUEUE)

EXIT_OK = 0


async def _clear(session: AsyncSession) -> int:
    """Убрать только демонстрационные записи."""
    campaigns = (
        (
            await session.execute(
                select(CampaignModel.id).where(CampaignModel.name.in_(DEMO_CAMPAIGNS))
            )
        )
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


#: Сколько писем ушло сегодня с каждого включённого ящика. Число скромное
#: и упирается в потолок разгона: ящик на первом дне с шестью письмами
#: при потолке пять — это «6 из 5» на экране, то есть данные, которых
#: не бывает.
SENT_TODAY_PER_BOX = 3


async def _seed_senders(session: AsyncSession, now: datetime) -> list[SenderModel]:
    """Ящики рассылки. Возвращает созданные: по ним потом раздаются письма."""
    made: list[SenderModel] = []
    for domain, boxes, cap, warmup_day, enabled, reason in SENDER_DOMAINS:
        for index in range(boxes):
            started = None if warmup_day is None else now - timedelta(days=warmup_day - 1)
            sender = SenderModel(
                domain=domain,
                email=f"outreach{index + 1}@{domain}",
                stage=Stage.DONORS,
                daily_cap=cap,
                status=SenderStatus.FREE if enabled else SenderStatus.PAUSED,
                enabled=enabled,
                warmup_started_at=started,
                paused_at=None if enabled else now - timedelta(days=2),
                pause_reason=reason,
            )
            session.add(sender)
            made.append(sender)
    await session.flush()
    return made


async def _seed_sent_today(session: AsyncSession, senders: list[SenderModel], now: datetime) -> int:
    """Письма, ушедшие сегодня.

    Нужны ради экрана доменов рассылки: дневной расход считается
    по письмам, а не хранится полем — хранимое поле никто не обнулял бы,
    и ящик упирался бы в кап навсегда. Без этих писем на экране у каждого
    ящика стоял бы ноль, и «отправлено 3 из 20» нечем было бы проверить.
    """
    campaign = CampaignModel(stage=Stage.DONORS, name=DEMO_CAMPAIGN_TODAY, status="running")
    session.add(campaign)
    await session.flush()

    made = 0
    for sender in senders:
        if not sender.enabled:
            continue
        allowance = warmup_state(sender, now=now).allowance
        for number in range(min(SENT_TODAY_PER_BOX, allowance)):
            host = f"sent-{sender.id}-{number + 1}{DEMO_SUFFIX}"
            domain = DomainModel(host=host)
            session.add(domain)
            await session.flush()

            session.add(
                DonorModel(
                    domain_id=domain.id,
                    status=DonorStatus.SUITABLE,
                    dr=22 + number,
                    org_traffic=800 + number * 250,
                    metrics_refreshed_at=now,
                )
            )
            contact = ContactModel(
                domain_id=domain.id, email=f"info@{host}", source=ContactSource.PAGE
            )
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

            session.add(
                MessageModel(
                    campaign_id=campaign.id,
                    thread_id=thread.id,
                    domain_id=domain.id,
                    contact_id=contact.id,
                    sender_id=sender.id,
                    step=0,
                    status=MessageStatus.SENT,
                    subject=f"Advertising rates for {host}",
                    body=LETTER_BODY,
                    # Отличие не назначается: текст собран не из шаблона,
                    # мерить его не от чего (см. `_seed_threads`).
                    uniqueness_pct=None,
                    sent_at=now - timedelta(hours=number + 1),
                    idempotency_key=f"{Stage.DONORS.value}:{host}:0",
                )
            )
            made += 1
    return made


async def _seed_queue(session: AsyncSession, now: datetime) -> int:
    """Письма, ждущие решения человека.

    Собраны настоящим кодом сборки письма — шаблоном и подстановками,
    а не строкой в этом файле: экран, которому подсунули выдуманный
    текст, показывает макет, а не сервис. Юридический блок при этом
    остаётся незаполненным, если он не заполнен: экран обязан показывать
    состояние сервиса, а не приукрашивать его.
    """
    campaign = CampaignModel(stage=Stage.DONORS, name=DEMO_CAMPAIGN_QUEUE, status="draft")
    session.add(campaign)
    await session.flush()

    template = letters_template.default()
    made = 0
    for order, (name, rewrites) in enumerate(QUEUE):
        host = f"{name}{DEMO_SUFFIX}"
        domain = DomainModel(host=host)
        session.add(domain)
        await session.flush()

        session.add(
            DonorModel(
                domain_id=domain.id,
                status=DonorStatus.SUITABLE,
                dr=31 + order * 4,
                org_traffic=2400 + order * 1100,
                metrics_refreshed_at=now - timedelta(days=order),
            )
        )
        contact = ContactModel(
            domain_id=domain.id, email=f"editor@{host}", source=ContactSource.PAGE
        )
        session.add(contact)
        await session.flush()

        letter = compose.assemble(
            compose.render(template, compose.values_for(host=host, domain_id=domain.id)),
            {zone: text.replace("{{host}}", host) for zone, text in rewrites.items()},
        )
        # Число считается по тексту, а не назначается ему.
        uniqueness = difference(letter.plain_body, letter.body)
        thread = ThreadModel(
            domain_id=domain.id,
            campaign_id=campaign.id,
            contact_id=contact.id,
            status=ThreadStatus.OPEN,
        )
        session.add(thread)
        await session.flush()

        session.add(
            MessageModel(
                campaign_id=campaign.id,
                thread_id=thread.id,
                domain_id=domain.id,
                contact_id=contact.id,
                step=0,
                status=MessageStatus.QUEUED,
                subject=letter.subject,
                body=letter.body,
                uniqueness_pct=uniqueness,
                idempotency_key=f"{Stage.DONORS.value}:{host}:0",
            )
        )
        made += 1
    return made


def _add_reply(
    session: AsyncSession,
    thread_id: int,
    *,
    host: str,
    email: str,
    outcome: str,
    white: Decimal | None,
    grey: Decimal | None,
    order: int,
) -> None:
    """Входящий ответ демонстрации, если разговор им кончился.

    Вынесено из сеятеля диалогов: там росло ветвление, а «чем кончился
    разговор» — отдельная тема от «как завести диалог».
    """
    if outcome not in REPLIES:
        return

    kind, template = REPLIES[outcome]
    session.add(
        ReplyModel(
            thread_id=thread_id,
            kind=kind,
            raw_body=template.format(white=white, grey=grey) if white else template,
            # Отправитель и тема заполняются как у настоящего входящего:
            # без них карточка показывает «—» там, где в бою стоит адрес
            # ответившего.
            from_email=email,
            subject=f"Re: Advertising rates for {host}",
            inbound_message_id=f"demo-{order}@{host}",
            price_white=white,
            price_grey=grey,
            currency="EUR" if white else None,
            payment_methods=["счёт", "карта"] if white else None,
            # Один ответ нарочно с низкой уверенностью: без него
            # на экране не видно очереди разбора.
            confidence=0.93 if white else (0.42 if outcome == "ответ" else None),
        )
    )


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
                # Отличия нет: текст написан здесь, а не собран из шаблона, и
                # мерить его не от чего. Раньше стояло «18.0 + номер» — число,
                # назначенное рядом с текстом (третий раз того же урока),
                # да ещё в процентах там, где поле хранит долю: карточка
                # диалога, написанная под эти данные, печатала «0%» у каждого
                # настоящего письма с отличием 19% (25.09.2026).
                uniqueness_pct=None,
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
                    # У добивки коридора нет вовсе (`letters/template.py`).
                    uniqueness_pct=None,
                    sent_at=sent_at + timedelta(days=7),
                    idempotency_key=f"demo:{host}:1",
                )
            )

        _add_reply(
            session,
            thread.id,
            host=host,
            email=email,
            outcome=outcome,
            white=white,
            grey=grey,
            order=order,
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
            sent_today = await _seed_sent_today(session, senders, now)
            queued = await _seed_queue(session, now)
            threads = await _seed_threads(session, now)
            await session.commit()
    finally:
        await engine.dispose()

    print(
        f"Заведено: ящиков рассылки {len(senders)}, писем за сегодня {sent_today}, "
        f"в очереди {queued}, диалогов {threads}."
    )
    print(f"Все домены оканчиваются на {DEMO_SUFFIX} — писем туда не уходит.")
    print("Убрать: outreach demo-seed --clear")
    return EXIT_OK
