"""Заведение домена рассылки и его ящика из консоли.

**Ящики заводятся здесь, а не в интерфейсе, и это решение.** Строка
отправителя существует только вместе с настроенным DNS: SPF, DKIM
и DMARC на домене и Domain Authentication у платформы. Кнопка в вебе
позволила бы завести ящик раньше, чем он способен отправлять, —
и первые письма ушли бы с домена без подписи, то есть прямо в спам,
сжигая репутацию, ради которой всё и затевалось.

Разгон начинается с момента заведения: дневной потолок поднимается
ступенями от даты старта, а не хранится числом (`features/outreach/senders.py`).

    outreach sender-add --email outreach@mail-a.example
    outreach sender-add --email a@mail-b.example --daily-cap 30 --stage advertisers
    outreach senders

Один домен — один ящик по умолчанию: домен берётся из адреса. Так
заведена первая двадцатка; несколько ящиков на домене платформа
позволяет, и тогда домен задаётся явно.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import outreach as cfg
from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.core.domain import SenderStatus, Stage
from backend.features.core.models.outreach import SenderModel
from backend.features.outreach.senders import warmup_state

EXIT_OK = 0
EXIT_TAKEN = 3
EXIT_BAD_ADDRESS = 4


async def cmd_sender_add(args: argparse.Namespace) -> int:
    """Завести ящик рассылки."""
    check_storage()

    email = args.email.strip().lower()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        print(f"«{args.email}» не похоже на адрес почты")
        return EXIT_BAD_ADDRESS
    domain = (args.domain or email.rsplit("@", 1)[1]).strip().lower()

    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            found = await session.execute(select(SenderModel).where(SenderModel.email == email))
            if found.scalars().first() is not None:
                print(f"Ящик {email} уже заведён")
                return EXIT_TAKEN

            sender = SenderModel(
                domain=domain,
                email=email,
                stage=Stage(args.stage),
                daily_cap=args.daily_cap,
                status=SenderStatus.FREE,
                enabled=True,
                # Разгон с этой минуты: домен, заведённый сегодня, сегодня
                # же и начинает с первой ступени, а не с полного капа.
                warmup_started_at=datetime.now(UTC),
            )
            session.add(sender)
            await session.commit()

            warmup = warmup_state(sender)
            print(f"Заведён {email} (домен {domain}, этап {sender.stage.value})")
            print(f"Дневной потолок {sender.daily_cap}, сегодня по разгону — {warmup.allowance}")
            print("DNS и Domain Authentication должны быть настроены ДО первой отправки.")
    finally:
        await engine.dispose()

    return EXIT_OK


async def cmd_senders(_: argparse.Namespace) -> int:
    """Показать заведённые ящики и их состояние."""
    check_storage()

    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            rows = await session.execute(select(SenderModel).order_by(SenderModel.id))
            senders = rows.scalars().all()
            if not senders:
                print("Ящиков нет. Завести: outreach sender-add --email …")
                return EXIT_OK

            print(f"{'адрес':40} {'этап':12} {'кап':>5} {'сегодня':>8}  состояние")
            for sender in senders:
                warmup = warmup_state(sender)
                state = "выключен" if not sender.enabled else sender.status.value
                if sender.pause_reason:
                    state = f"{state} ({sender.pause_reason})"
                print(
                    f"{sender.email:40} {sender.stage.value:12} {sender.daily_cap:5} "
                    f"{warmup.allowance:8}  {state}"
                )
            print(f"\nТранспорт: {cfg.TRANSPORT}")
    finally:
        await engine.dispose()

    return EXIT_OK


def add_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    add = sub.add_parser("sender-add", help="завести домен рассылки и его ящик")
    add.add_argument("--email", required=True, help="адрес ящика, например outreach@mail-a.example")
    add.add_argument("--domain", help="домен рассылки; по умолчанию — домен адреса")
    add.add_argument(
        "--daily-cap",
        type=int,
        default=cfg.DAILY_CAP_PER_SENDER,
        help="дневной потолок писем на ящик после разгона",
    )
    add.add_argument(
        "--stage",
        choices=[stage.value for stage in Stage],
        default=Stage.DONORS.value,
        help="этап: у второго домены отправки отдельные",
    )

    sub.add_parser("senders", help="ящики рассылки и их состояние")
