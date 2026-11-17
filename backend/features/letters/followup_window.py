"""Окно получателя у добивок: добивка уходит в его рабочие часы и ждёт их до сборки текста.

Слово владельца 16.11.2026. Окна у доноров и рекламодателей не было (`core/stages.CURRENT`), и их
добивки уходили в любой день и час — в субботу днём рекламодателям ушла пачка добивок. А первые
письма тех же адресатов, когда очередь уходит сама, окно уже держали (`autosend_window.py`).

**Окно и пояс доноров и рекламодателей — те же, что у первых писем автоотправки**:
`autosend_window.frame` — пн–пт 09:00–18:00 по часам получателя (`OUTREACH_AUTOSEND_DAYS`,
`OUTREACH_AUTOSEND_HOURS`), — и правило страны `autosend_window.zones_of`; страна неизвестна —
`OUTREACH_AUTOSEND_ZONE`. Письма одной переписки уходят в одни часы, а добивка не ждёт вечно из-за
неизвестной страны.

**Страна — данными цепочки** (`_zone`): домен адресата и рассылка — те же, что у первого письма
переписки. Донору — страна его трафика, иначе рынок прогона рассылки; рекламодателю по ссылке —
страна аудитории донора его ссылки (та же, что во фразе письма про аудиторию); бизнесу ниши — рынок
прогона, где его нашли. Само правило — одно, в `autosend_window.py`; здесь — только кому письмо.

**Вне окна добивка не уходит и не теряется**: срок возвращается на открытие окна плюс случайный
сдвиг (`window.defer_until`, `SendWindow.spread` — полчаса от открытия), как у добивки продаж.
Отложенные за выходные к утру понедельника не уходят одной минутой: сдвиг разносит их по получасу,
а дальше по дню — пауза ящика (`followup_pace.py`). Окно судится до потолков ящика: добивка вне
окна не тратит ни часового, ни суточного запаса.

**У продаж окно — политика этапа** (`stages.mail_policy`), пояс — ответ модуля продаж о получателе
(`stages.recipient`: лида, его страны) — тот же, что судит отправка (`sending._check_window`). До
17.11.2026 его судила только отправка — после сборки текста и заведения строки добивки, и ночью
проход собирал каждую добивку вне окна, чтобы отложить её: 250 таких добивок откладывались по 20 за
проход больше десяти минут, и сторож назвал молчащими все ящики продаж. Теперь окно продаж судится
здесь, до текста, по письму, после которого пришёл срок (адресат у переписки один); отправка судит
его, как прежде. Мост не ответил или пояса нет — здесь не судится: отказ скажет отправка своими
словами. Ответ в переписке окна не ждёт — он и не идёт проходом добивок.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core import stages, window
from backend.features.core.domain import Stage
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.outreach import MessageModel
from backend.features.crawl.niche import NICHE
from backend.features.letters import autosend_window
from backend.features.letters.followup_report import PassReport
from backend.features.letters.offer_addresses import donor_geo_of

if TYPE_CHECKING:
    from backend.features.letters.followups import Chain, Claimed

logger = logging.getLogger(__name__)

#: Этапы, чьё окно — окно первых писем автоотправки; у продаж — окно политики этапа.
STAGES = frozenset({Stage.DONORS, Stage.ADVERTISERS})


async def holds(session: AsyncSession, chain: Chain, claimed: Claimed, report: PassReport) -> bool:
    """Добивка вне рабочих часов получателя — срок на открытие окна плюс сдвиг, строка журнала —
    почему и когда уйдёт, и `True`. В окне или окна здесь не судить — `False`."""
    stage, audience = await chain.stage_and_audience(claimed.campaign_id)
    hours = await _hours(session, claimed, stage, audience)
    if hours is None:
        return False
    (frame, zone), now = hours, chain.moment()
    until = window.defer_until(frame, zone, now)
    if until is None:
        return False
    await chain.restore(claimed, delay=until - now)
    await session.commit()
    report.postponed += 1
    report.outside += 1
    logger.info(
        "добивки: %s — отложена: вне окна получателя (%s по его часам), уйдёт не раньше %s",
        claimed.host,
        frame.words,
        window.local_words(until, zone),
    )
    return True


async def _hours(
    session: AsyncSession, claimed: Claimed, stage: Stage, audience: str
) -> tuple[window.SendWindow, ZoneInfo] | None:
    """Окно и пояс получателя добивки; `None` — окна у этапа нет или судить его здесь нечем."""
    if stage in STAGES:
        return autosend_window.frame(), await _zone(session, claimed, stage, audience)
    return await _policy_hours(session, claimed, stage)


async def _policy_hours(
    session: AsyncSession, claimed: Claimed, stage: Stage
) -> tuple[window.SendWindow, ZoneInfo] | None:
    """Окно политики этапа и пояс получателя — ответом моста о письме, после которого пришёл срок:
    адресат у добивки тот же. Мост отказал или пояса нет — `None`, судит отправка."""
    what = f"Добивка шага {claimed.step} в переписке №{claimed.thread_id}"
    previous = await session.get(MessageModel, claimed.previous_id)
    if previous is None:
        return None
    try:
        frame = (await stages.mail_policy(session, stage, what)).window
        if frame is None:
            return None
        to = await stages.recipient(session, previous, stage, None, what)
    except stages.MailRefusalError as exc:
        # Отказ скажет отправка своими словами и строкой журнала прохода — здесь не дублируем.
        logger.debug("добивки: %s — окно здесь не судится: %s", claimed.host, exc)
        return None
    zone = window.zone_of(to.zones)
    return None if zone is None else (frame, zone)


async def _zone(session: AsyncSession, claimed: Claimed, stage: Stage, audience: str) -> ZoneInfo:
    """Пояс адресата добивки — правилом страны первых писем; у рекламодателя по ссылке страна —
    у донора его нынешней лучшей ссылки, той же, что у первого письма."""
    link_geo = None
    if stage is Stage.ADVERTISERS and audience != NICHE:
        link_geo = await session.scalar(
            select(donor_geo_of(AdvertiserModel.best_donor_host)).where(
                AdvertiserModel.domain_id == claimed.domain_id
            )
        )
    whom = autosend_window.Addressee(
        claimed.previous_id, stage, audience, claimed.domain_id, claimed.campaign_id, link_geo
    )
    return (await autosend_window.zones_of(session, [whom]))[whom.letter_id]
