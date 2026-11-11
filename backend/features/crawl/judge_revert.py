"""Откат партии советов судьи: решения партии — обратно в очередь проверки.

Партия находится по метке автора (`judge_mark.marker_of`), а не по номерам строк: строки кандидатов
новый обход заводит заново, а метка едет с решением (`CARRIED`, `crawl/gate.py`).

**Что откат не трогает.** Решение, которое человек переиграл: у него другой автор, и метки партии на
нём уже нет. И «пишем», по которому письмо ушло: домену уже написали, и вернуть пару в очередь
значило бы снять рекламодателя посреди переписки, — такая пара остаётся решённой и называется
числом; человек снимает её сам обычным «Не пишем».

**«Не пишем» возвращается записью.** Спорная пара без решения рекламодателя не заводит, и снимать у
неё нечего. Чего откат не возвращает: рекламодателя, которого «не пишем» сняло, потому что других
подходящих ссылок у домена не было (`promote.withdraw`), — подходящих ссылок у него нет и после
отката; его число названо при применении и стоит в журнале.

**«Пишем» возвращается вместе с рекламодателем.** Пара перестала быть подходящей — рекламодатель её
домена пересобирается из оставшихся подходящих ссылок, а нет их — снимается вместе с письмами в
очереди, тем же кодом, что у «не пишем» (`promote.withdraw`). Это его штатный смысл: он смотрит не на
решение, а на то, какие подходящие ссылки у домена остались. Снятое письмо ложится с причиной «ссылку
сняли» и следующую сборку не держит (`letters/attempts.py`): пара решится снова — письмо соберётся.

**Память обходов чистится тем же откатом** (`CandidateMemoryModel`). Пересчёт пишет решение в память
донора и читает её раньше строк, а строку без решения поверх памяти не кладёт (`gate._Previous.take`):
без чистки следующий же ночной обход вернул бы откатанной паре решение судьи. Решение пары, которой
последний обход не нашёл, живёт только в памяти — оно снимается там же, и пара вернётся нерешённой.
"""

from __future__ import annotations

import logging
from collections.abc import Collection
from dataclasses import dataclass

from sqlalchemy import or_, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import GONE_STATUSES, MessageStatus, Stage
from backend.features.core.models.advertiser import CandidateMemoryModel, CandidateModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import CampaignModel, MessageModel
from backend.features.crawl.advertiser_rules import writes_to
from backend.features.crawl.judge_apply import chunked
from backend.features.crawl.judge_mark import marker_of
from backend.features.crawl.niche import LINKS
from backend.features.crawl.promote import withdraw
from backend.features.crawl.suppliers import supplier_hosts
from backend.shared.numbers import spaced

logger = logging.getLogger(__name__)

Memory = CandidateMemoryModel

#: Письмо ушло или уходит: «отправляется» — исход ещё неизвестен, и письмо могло дойти.
OUT = (MessageStatus.SENDING, *GONE_STATUSES)

Pair = tuple[str, str]


class NothingBackError(ValueError):
    """Откату нечего вернуть: решений партии нет, или остались только те, по которым письмо ушло.
    Слова — человеку у кнопки; отказ — до единой записи."""


def _nothing_words(kept: int) -> str:
    if kept:
        return (
            f"Откатывать нечего: по оставшимся решениям партии письмо уже ушло ({spaced(kept)}) — "
            "они остаются в силе, снять такую пару можно обычным «Не пишем»"
        )
    return "Откатывать нечего: решения партии уже возвращены в очередь или переиграны человеком"


@dataclass(slots=True)
class Reverted:
    """Итог отката — числами: что вернулось, что осталось и почему."""

    batch: str
    #: Пар вернулось в очередь проверки.
    returned: int = 0
    #: «Пишем» осталось в силе: письмо домену уже ушло.
    kept: int = 0
    #: Пары, которых последний обход не нашёл: решение снято из памяти, вернутся нерешёнными.
    forgotten: int = 0
    #: Рекламодателей снято и писем снято из очереди — у откатанного «пишем» (`promote.Withdrawn`).
    removed: int = 0
    stopped: int = 0


async def _written_to(session: AsyncSession, hosts: Collection[str]) -> set[str]:
    """Домены, которым оффер по найденной ссылке ушёл, уходит или мог уйти: у письма, возвращённого в
    очередь после неизвестного исхода, остаётся наш `Message-ID` (`crawl/offer_stop.py`)."""
    found: set[str] = set()
    for chunk in chunked(sorted(hosts)):
        written = await session.scalars(
            select(DomainModel.host)
            .join(MessageModel, MessageModel.domain_id == DomainModel.id)
            .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
            .where(
                DomainModel.host.in_(chunk),
                CampaignModel.stage == Stage.ADVERTISERS,
                CampaignModel.audience == LINKS,
                or_(MessageModel.status.in_(OUT), MessageModel.internet_message_id.is_not(None)),
            )
            .distinct()
        )
        found.update(written.all())
    return found


async def _held(session: AsyncSession, hosts: Collection[str], marker: str) -> set[str]:
    """Домены, чьё «пишем» партии не откатывается: письмо ушло, а кроме пар партии подходящих ссылок
    у домена нет — откат снял бы рекламодателя, которому уже написали. Домен, которому пишут и по
    ссылке другого донора, не держится: рекламодатель останется и без пар партии."""
    written = await _written_to(session, hosts)
    if not written:
        return set()
    suppliers = await supplier_hosts(session)
    stays: set[str] = set()
    for chunk in chunked(sorted(written)):
        others = await session.execute(
            select(CandidateModel.target_root, CandidateModel.donor_host).where(
                CandidateModel.target_root.in_(chunk),
                writes_to(),
                or_(CandidateModel.decided_by.is_(None), CandidateModel.decided_by != marker),
            )
        )
        stays.update(root for root, donor in others.tuples() if donor.lower() not in suppliers)
    return written - stays


async def _forget(session: AsyncSession, marker: str, pairs: Collection[Pair]) -> None:
    """Решения партии у этих пар — из памяти обходов. DR в памяти остаётся: за него заплачено, и к
    решению он не относится."""
    for chunk in chunked(sorted(pairs)):
        await session.execute(
            update(Memory)
            .where(
                Memory.decided_by == marker,
                tuple_(Memory.donor_host, Memory.target_root).in_(chunk),
            )
            .values(confirmed=None, decided_by=None, decided_at=None)
            .execution_options(synchronize_session=False)
        )


async def _only_remembered(session: AsyncSession, pairs: Collection[Pair]) -> int:
    """Сколько из пар живёт только в памяти: строки кандидата у последнего обхода нет."""
    present: set[Pair] = set()
    for chunk in chunked(sorted(pairs)):
        rows = await session.execute(
            select(CandidateModel.donor_host, CandidateModel.target_root).where(
                tuple_(CandidateModel.donor_host, CandidateModel.target_root).in_(chunk)
            )
        )
        present.update((donor, target) for donor, target in rows.tuples())
    return len(set(pairs) - present)


async def revert(session: AsyncSession, batch: str) -> Reverted:
    """Вернуть в очередь решения партии, которые с тех пор не менял человек. Без фиксации: её
    делает вызывающий вместе с записью журнала. Вернуть нечего — отказ до единой записи."""
    marker = marker_of(batch)
    rows = list(
        (
            await session.scalars(
                select(CandidateModel)
                .where(CandidateModel.decided_by == marker)
                .order_by(CandidateModel.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    remembered = set(
        (
            await session.execute(
                select(Memory.donor_host, Memory.target_root).where(Memory.decided_by == marker)
            )
        )
        .tuples()
        .all()
    )
    held = await _held(session, {row.target_root for row in rows if row.confirmed}, marker)
    kept = [row for row in rows if row.confirmed and row.target_root in held]
    back = [row for row in rows if not (row.confirmed and row.target_root in held)]
    in_rows = {(row.donor_host, row.target_root) for row in rows}
    forgotten = await _only_remembered(session, remembered - in_rows)
    if not back and not forgotten:
        raise NothingBackError(_nothing_words(len(kept)))

    # Домены откатанного «пишем» — до сброса решения: после него строка их уже не называет.
    rebuilt = {row.target_root: row for row in back if row.confirmed}
    for row in back:
        row.confirmed = None
        row.decided_by = None
        row.decided_at = None
    await session.flush()

    result = Reverted(batch, returned=len(back), kept=len(kept), forgotten=forgotten)
    for row in rebuilt.values():
        taken = await withdraw(session, row)
        result.removed += int(taken.removed)
        result.stopped += taken.stopped
    await _forget(session, marker, remembered - {(row.donor_host, row.target_root) for row in kept})
    logger.info(
        "судья рекламодателей: партия откатана",
        extra={
            "batch": batch,
            "returned": result.returned,
            "kept": result.kept,
            "forgotten": result.forgotten,
            "removed": result.removed,
            "stopped": result.stopped,
        },
    )
    return result
