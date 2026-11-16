"""Переадресация по диалогам рекламодателей, уже ждущим человека: `outreach lead-redirect`.

Новые ответы «пишите на …» приём ставит на разбор сам (`replies/lead_redirect.note`), а ответы,
пришедшие раньше, стоят «лид — ждёт человека». Их берёт эта команда.

**По умолчанию — предпросмотр**: по каждому ждущему ответу — какие адреса в нём названы, перешлёт ли
и куда, а если нет — почему; что уйдёт письмом. Без модели, без записи и без отправки: модель и
почту зовёт только `--write`, и только для названных переписок (`--threads`) или для всех (`--all`).

    outreach lead-redirect                          предпросмотр по всем ждущим
    outreach lead-redirect --threads 12,15          предпросмотр этих переписок
    outreach lead-redirect --write --threads 12,15  разобрать моделью и отправить эти
    outreach lead-redirect --write --all            разобрать и отправить все ждущие

С `--write` письмо уходит по тем же правилам, что у прохода: рабочие часы получателя, лимиты и пауза
ящика переписки. Не сейчас — письмо ждёт в переписке, и его отправит проход переадресации (при
`OUTREACH_LEAD_REDIRECT=true`) или повтор команды. Команда работает и при выключенном рубильнике:
её зовёт человек.
"""

from __future__ import annotations

import argparse
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.core.domain import ReplyKind, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import CampaignModel, ReplyModel, ThreadModel
from backend.features.core.models.redirect import LeadRedirectModel
from backend.features.letters import redirect_send
from backend.features.letters.redirect_letter import (
    FIXED_LINE,
    NO_FIRST,
    Asker,
    address_refusal,
    colleague_of,
    talk_of,
)
from backend.features.letters.transport import Mail
from backend.features.letters.transport_factory import Transports, in_use
from backend.features.replies.lead_redirect import candidates, enlist, held, seen_for
from backend.features.replies.redirect_ask import RedirectClient
from backend.features.replies.redirect_shown import shown

EXIT_OK = 0
EXIT_MISUSED = 2

#: Сколько ответов показывает предпросмотр за раз.
PREVIEW_LIMIT = 200

#: Порядок расхода на один разбор моделью — для строки итога предпросмотра.
TOKENS_PER_REPLY = "≈1–2 тыс. токенов"


def add_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    command = sub.add_parser(
        "lead-redirect",
        help="переадресация по диалогам рекламодателей, ждущим человека: предпросмотр, "
        "с --write — разбор моделью и отправка",
    )
    command.add_argument("--threads", default="", help="номера переписок через запятую")
    command.add_argument("--all", action="store_true", help="все ждущие переписки (с --write)")
    command.add_argument(
        "--write",
        action="store_true",
        help="разобрать моделью и отправить; без него — только предпросмотр, ничего не пишет",
    )
    command.add_argument(
        "--limit", type=int, default=PREVIEW_LIMIT, help="сколько ответов взять за раз"
    )


@dataclass(frozen=True, slots=True)
class Waiting:
    """Ответ человека рекламодателю, ждущий человека, по которому переадресация не решалась."""

    reply: ReplyModel
    thread_id: int
    host: str


async def waiting(
    session: AsyncSession, thread_ids: Sequence[int] = (), *, limit: int = PREVIEW_LIMIT
) -> list[Waiting]:
    """Ждущие ответы — переписок `thread_ids` или все, по переписке и времени прихода."""
    statement = (
        select(ReplyModel, ThreadModel.id, DomainModel.host)
        .join(ThreadModel, ThreadModel.id == ReplyModel.thread_id)
        .join(CampaignModel, CampaignModel.id == ThreadModel.campaign_id)
        .join(DomainModel, DomainModel.id == ThreadModel.domain_id)
        .where(
            CampaignModel.stage == Stage.ADVERTISERS,
            ReplyModel.kind == ReplyKind.HUMAN,
            ReplyModel.reviewed_at.is_(None),
            ~exists().where(LeadRedirectModel.reply_id == ReplyModel.id),
        )
        .order_by(ThreadModel.id, ReplyModel.created_at, ReplyModel.id)
        .limit(limit)
    )
    if thread_ids:
        statement = statement.where(ThreadModel.id.in_(list(thread_ids)))
    rows = (await session.execute(statement)).tuples().all()
    return [Waiting(reply=reply, thread_id=thread, host=host) for reply, thread, host in rows]


@dataclass(frozen=True, slots=True)
class Look:
    """Что сделала бы команда с одним ответом — словами предпросмотра."""

    found: Waiting
    addresses: tuple[str, ...]
    verdict: str
    #: Пойдёт ли ответ к модели при `--write`.
    asks: bool = False


async def look(session: AsyncSession, found: Waiting) -> Look:
    """Предпросмотр одного ответа: адреса в нём, проверки адресов и переписки. Только чтение:
    ни модели, ни записи; DNS домена адреса — вопрос без денег."""
    reply = found.reply
    addresses = candidates(reply.raw_body, await seen_for(session, reply, found.thread_id))
    if not addresses:
        return Look(found, (), "в ответе нет адресов, кроме наших, отправителя и нашего письма")
    why = await held(session, found.thread_id)
    talk = await talk_of(session, reply.id)
    if why is not None or talk is None or talk.first is None:
        return Look(found, addresses, f"не перешлёт: {why or NO_FIRST}")
    refusals = {address: await address_refusal(session, address, talk) for address in addresses}
    passing = [address for address, refusal in refusals.items() if refusal is None]
    if not passing:
        said = "; ".join(f"{address} — {refusal}" for address, refusal in refusals.items())
        return Look(found, addresses, f"не перешлёт: {said}")
    line = FIXED_LINE.format(colleague=colleague_of(talk, None))
    verdict = (
        f"спросит модель, просят ли писать на {', '.join(passing)}; если да — уйдёт первое письмо "
        f"«{talk.first.subject or ''}» с ящика переписки, сверху: «{line}»"
    )
    return Look(found, addresses, verdict, asks=True)


async def preview(session: AsyncSession, found: Sequence[Waiting]) -> list[Look]:
    """Предпросмотр всех ждущих ответов — ничего не пишет."""
    return [await look(session, one) for one in found]


async def write(
    session: AsyncSession,
    asker: Asker,
    transports: Mail,
    found: Sequence[Waiting],
    *,
    now: datetime,
) -> dict[int, str]:
    """Поставить ответы на разбор и пройти по их перепискам: решения, письма. Номер переписки →
    что вышло словами (`redirect_shown`)."""
    for one in found:
        await enlist(session, one.reply, stage=Stage.ADVERTISERS, thread_id=one.thread_id)
    await session.commit()
    threads = sorted({one.thread_id for one in found})
    while (
        await redirect_send.run(session, asker, transports, now=now, thread_ids=threads)
    ).decided:
        pass  # проход берёт решения пачками (`PASS_LIMIT`): до последней ждущей
    return {thread: said.words for thread, said in (await shown(session, threads)).items()}


def _threads(raw: str) -> list[int]:
    return [int(part) for part in raw.replace(" ", "").split(",") if part]


def _print_looks(looks: Sequence[Look]) -> None:
    if not looks:
        print("Ждущих человека ответов рекламодателей, по которым переадресация не решалась, нет.")
        return
    for one in looks:
        reply = one.found.reply
        print(
            f"Переписка №{one.found.thread_id} · {one.found.host} · ответ №{reply.id} "
            f"от {reply.created_at:%d.%m.%Y} ({reply.from_email or 'адрес не записан'})"
        )
        if one.addresses:
            print(f"  адреса в ответе: {', '.join(one.addresses)}")
        print(f"  → {one.verdict}")
    asks = sorted({one.found.thread_id for one in looks if one.asks})
    print(
        f"\nОтветов: {len(looks)}, к модели пойдёт: {sum(one.asks for one in looks)} "
        f"({TOKENS_PER_REPLY} на ответ). Ничего не записано и не отправлено."
    )
    if asks:
        listed = ",".join(map(str, asks))
        print(f"Разобрать и отправить: outreach lead-redirect --write --threads {listed}")


@asynccontextmanager
async def _sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    check_storage()
    engine = create_async_engine(storage.DSN)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def cmd_lead_redirect(args: argparse.Namespace) -> int:
    """Предпросмотр по умолчанию; `--write` — с `--threads` или `--all`."""
    threads = _threads(args.threads)
    if args.write and not (threads or args.all):
        print("С --write назовите переписки (--threads 12,15) или возьмите все (--all).")
        return EXIT_MISUSED
    async with _sessions() as factory, factory() as session:
        found = await waiting(session, threads, limit=args.limit)
        if not args.write:
            _print_looks(await preview(session, found))
            return EXIT_OK
        client = RedirectClient()
        try:
            async with in_use(Transports()) as transports:
                done = await write(session, client, transports, found, now=datetime.now(UTC))
        finally:
            await client.aclose()
    for thread, words in done.items():
        print(f"Переписка №{thread}: {words}")
    print(f"Переписок с переадресацией: {len(done)} из {len({one.thread_id for one in found})}.")
    return EXIT_OK
