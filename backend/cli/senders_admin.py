"""Заведение домена рассылки и его ящика из консоли.

**Ящики заводятся здесь, а не в интерфейсе, и это решение.** Строка
отправителя существует только вместе с настроенным DNS: SPF, DKIM
и DMARC на домене и Domain Authentication у платформы. Кнопка в вебе
позволила бы завести ящик раньше, чем он способен отправлять, —
и первые письма ушли бы с домена без подписи, то есть прямо в спам,
сжигая репутацию, ради которой всё и затевалось.

Старт разгона — минута заведения, а ступени дневного потолка считаются
от первого письма ящика после неё, а не хранятся числом
(`features/outreach/senders.py`): ящик, заведённый за неделю до первого
письма, начинает с первой ступени.

    outreach sender-add --email outreach@mail-a.example
    outreach sender-add --email a@mail-b.example --daily-cap 30 --stage advertisers
    outreach senders
    outreach sender-resume --email a@mail-b.example          # показать
    outreach sender-resume --email a@mail-b.example --yes    # снять паузу, разгон на месте
    outreach sender-cap --stage sales --daily-cap 25         # показать новый дневной потолок
    outreach sender-cap --stage sales --daily-cap 25 --yes   # записать

**Дневной потолок ящика меняется только здесь** (27.11.2026): экрана для него нет, а правка базы
руками в обход штатной команды на проде не делается. Потолок держит и разгон: ступени
`OUTREACH_WARMUP_CAPS` кончаются своим числом, и выше последней ступени ящик не пойдёт, сколько
бы ни стояло в потолке, — команда об этом предупреждает. Добивки считаются своим потолком
(`OUTREACH_FOLLOWUP_PER_SENDER_PER_DAY`), команда его только называет.

Один домен — один ящик по умолчанию: домен берётся из адреса. Так
заведена первая двадцатка; несколько ящиков на домене платформа
позволяет, и тогда домен задаётся явно.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import outreach as cfg
from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, SenderStatus, Stage
from backend.features.core.models.outreach import SenderModel
from backend.features.outreach.senders import WARMUP_STEPS, resume, warmup_state

EXIT_OK = 0
EXIT_TAKEN = 3
EXIT_BAD_ADDRESS = 4
EXIT_NOT_FOUND = 5
EXIT_BAD_CAP = 6

#: Выше — опечатка, а не решение для консоли: подъём потолка идёт шагами по 5 (27.11.2026).
MAX_DAILY_CAP = 50


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
                # Старт разгона — эта минута; ступень — от первого письма после
                # неё: до него ящик на первой ступени, а не на полном капе.
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


async def cmd_sender_resume(args: argparse.Namespace) -> int:
    """Снять паузу с ящиков, не трогая разгон; без `--yes` — только показать. Ящик вне паузы не
    трогается, незнакомый адрес останавливает всю команду до записи."""
    check_storage()
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            boxes = await _named_boxes(session, args.email)
            if boxes is None:
                return EXIT_NOT_FOUND
            for box in boxes:
                await _resume_one(session, box, write=args.yes)
            await session.commit()
    finally:
        await engine.dispose()
    if not args.yes:
        print("Только показ. Снять паузу: добавить --yes")
    return EXIT_OK


async def _resume_one(session: AsyncSession, box: SenderModel, *, write: bool) -> None:
    """Один ящик: сказать, что с ним, и снять паузу, если велено. В журнал — как включение
    кнопкой, но со своими словами: разгон на месте."""
    if box.enabled:
        print(f"{box.email}: не на паузе, не трогаю")
        return
    print(f"{box.email}: пауза «{box.pause_reason}», разгон с {box.warmup_started_at:%d.%m.%Y}")
    if not write:
        return
    resume(box)
    await AccessRepository(session).record(
        AuditAction.USER_UPDATED,
        author_id=None,
        target=f"sender:{box.id}",
        details={"действие": "пауза снята из консоли", "домен": box.domain, "разгон": "на месте"},
        stage=box.stage,
    )
    print(f"{box.email}: пауза снята, сегодня по разгону — {warmup_state(box).allowance}")


async def cmd_sender_cap(args: argparse.Namespace) -> int:
    """Сменить дневной потолок первых писем у ящиков этапа или у названных; без `--yes` — только
    показать. Незнакомый адрес останавливает всю команду до записи."""
    check_storage()
    cap = int(args.daily_cap)
    if not 1 <= cap <= MAX_DAILY_CAP:
        print(f"Потолок {cap} вне 1–{MAX_DAILY_CAP}. Ничего не менял")
        return EXIT_BAD_CAP
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            boxes = await _boxes_for_cap(session, args)
            if boxes is None:
                return EXIT_NOT_FOUND
            for box in boxes:
                await _cap_one(session, box, cap, write=args.yes)
            if args.yes:
                await session.commit()
    finally:
        await engine.dispose()
    if cap > WARMUP_STEPS[-1]:
        print(
            f"Разгон кончается ступенью {WARMUP_STEPS[-1]} (OUTREACH_WARMUP_CAPS): выше неё ящик "
            "с разгоном не пойдёт — сначала добавить ступени"
        )
    print(f"Добивки — свой потолок: {cfg.FOLLOWUP_PER_SENDER_PER_DAY} в сутки с ящика (.env)")
    if not args.yes:
        print("Только показ. Записать: добавить --yes")
    return EXIT_OK


async def _boxes_for_cap(
    session: AsyncSession, args: argparse.Namespace
) -> list[SenderModel] | None:
    """Ящики команды: этапа целиком или названные по адресу. `None` — адреса нет в базе."""
    if args.stage:
        found = await session.scalars(
            select(SenderModel)
            .where(SenderModel.stage == Stage(args.stage))
            .order_by(SenderModel.id)
        )
        return list(found.all())
    return await _named_boxes(session, args.email)


async def _named_boxes(session: AsyncSession, emails: list[str]) -> list[SenderModel] | None:
    """Ящики по адресам — без учёта регистра и краевых пробелов, без повторов, в порядке
    названного. `None` — какого-то адреса нет: команда останавливается до записи."""
    wanted = list(dict.fromkeys(email.strip().lower() for email in emails))
    found = await session.scalars(select(SenderModel).where(SenderModel.email.in_(wanted)))
    boxes = {box.email: box for box in found.all()}
    missing = [email for email in wanted if email not in boxes]
    if missing:
        print(f"Таких ящиков нет: {', '.join(missing)}. Ничего не менял")
        return None
    return [boxes[email] for email in wanted]


async def _cap_one(session: AsyncSession, box: SenderModel, cap: int, *, write: bool) -> None:
    """Один ящик: был и станет, сегодня по разгону; записать и в журнал, если велено."""
    was = box.daily_cap
    if was == cap:
        print(f"{box.email}: потолок уже {cap}, не трогаю")
        return
    box.daily_cap = cap
    today = warmup_state(box).allowance
    print(f"{box.email}: потолок {was} → {cap}, сегодня по разгону — {today}")
    if not write:
        box.daily_cap = was
        return
    await AccessRepository(session).record(
        AuditAction.USER_UPDATED,
        author_id=None,
        target=f"sender:{box.id}",
        details={"действие": "дневной потолок из консоли", "было": was, "стало": cap},
        stage=box.stage,
    )


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

    back = sub.add_parser("sender-resume", help="снять паузу с ящика, разгон остаётся на месте")
    back.add_argument(
        "--email", action="append", required=True, help="адрес ящика; можно несколько"
    )
    back.add_argument("--yes", action="store_true", help="записать; без него — только показать")

    cap = sub.add_parser("sender-cap", help="сменить дневной потолок первых писем ящиков")
    which = cap.add_mutually_exclusive_group(required=True)
    which.add_argument("--stage", choices=[stage.value for stage in Stage], help="все ящики этапа")
    which.add_argument("--email", action="append", help="адрес ящика; можно несколько")
    cap.add_argument(
        "--daily-cap", type=int, required=True, help=f"новый потолок, 1–{MAX_DAILY_CAP}"
    )
    cap.add_argument("--yes", action="store_true", help="записать; без него — только показать")
