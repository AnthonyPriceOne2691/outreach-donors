"""Обход 24/7 на экране: строка состояния, «i», очереди, доли продающих страниц и глубина донора.

**На виду — одна строка** (`docs/UI_RULES.md`, как у автокруга): «идёт: новые — 3 · до 10 000 — 120
· дальше — 40; обходится сейчас 12», или почему включённый сейчас не идёт, или «на паузе». Всё
остальное — строками для «i»: порядок, порог, ночь, сколько разом, сколько пройдено и кто ждёт. Слова
считает сервер, экран их только называет; время — экран, по часам смотрящего.

**Доли продающих страниц** (`yields`) — у сайтов, прошедших первые 10 000, чью пометку решает порог:
по ним экран сам считает «при пороге X % пометку получат N сайтов», без запроса на каждую цифру.
Пометка руками от порога не зависит, и такие сайты в доли не входят.

**Глубина донора** (`depths`) — в строке вкладки «Обход доноров»: сколько страниц прошла цепочка,
частей, доля, пометка и кто её поставил, пройден ли сайт и идёт ли часть сейчас.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import crawl as crawl_cfg
from backend.config import crawl_always as cfg
from backend.features.core.models.crawl import (
    CrawlAlwaysSignalModel,
    CrawlDepthModel,
    CrawlRunModel,
)
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.crawl import always, always_depth, always_switch
from backend.features.crawl.always import Stage, Survey
from backend.features.crawl.always_switch import Switch
from backend.features.crawl.donor_page import ACTIVE
from backend.features.crawl.launch import crawl_host
from backend.features.crawl.units_stop import units_stop
from backend.features.donors.standing import is_donor
from backend.shared.numbers import spaced

#: Почему включённый обход 24/7 сейчас не идёт — словами экрана.
NIGHT_WAIT = "ночной обход — продолжится после него"
UNITS_WAIT = "юниты Ahrefs кончились — продолжится сам"
HUMANS_WAIT = "обходы людей в очереди загрузки — продолжится после них"
DONE_WAIT = "новых страниц нет — все сайты пройдены"
NO_DONORS = "доноров для обхода нет — нужны принятые доноры со свежей ценой"
#: Рубильник сервиса выключен — строка блока.
UNAVAILABLE = "выключен настройкой сервиса"


@dataclass(frozen=True, slots=True)
class DonorDepth:
    """Донор в обходе 24/7 — для его строки на вкладке: «платная ссылка на `selling_pages` из
    `pages` страниц»."""

    pages: int
    parts: int
    selling_pages: int | None
    yield_pct: float | None
    weak: bool
    #: `auto` — по порогу, `human` — руками (и пометил, и снял); пусто — порог не пометил.
    weak_by: str | None
    exhausted: bool
    #: Часть 24/7 этого донора идёт или ждёт обходчика.
    running: bool
    #: Строка для «i» донора, если есть что сказать: судья читает не всю цепочку.
    note: str | None = None


@dataclass(frozen=True, slots=True)
class View:
    """Обход 24/7 на экране — одним ответом."""

    #: Рубильник сервиса включён: без него кнопок нет.
    available: bool
    switch: Switch
    #: Почему включённый сейчас не идёт; пусто — идёт или на паузе.
    waiting_for: str | None
    counts: dict[str, int]
    #: Страниц, пройденных обходом 24/7, всего, и страниц в части после первой.
    pages: int
    part_pages: int
    words: str
    details: list[str]
    yields: list[float]
    #: Когда сказано «пройдено всё»; пусто — не говорили или с тех пор появилась работа.
    done_at: datetime | None


def depth_of(row: always_depth.Facts, threshold: float, *, running: bool) -> DonorDepth:
    weak, by = always_depth.weak_of(row, threshold)
    return DonorDepth(
        pages=row.pages,
        parts=row.parts,
        selling_pages=row.selling_pages,
        yield_pct=row.yield_pct,
        weak=weak,
        weak_by=by,
        exhausted=row.exhausted_at is not None,
        running=running,
        note=frozen_words(row.frozen_parts),
    )


async def depths(
    session: AsyncSession, hosts: list[str], *, threshold: float
) -> dict[str, DonorDepth]:
    """Глубина доноров страницы вкладки — по хосту обхода; у кого строки нет, того нет."""
    names = sorted({crawl_host(host) for host in hosts})
    if not names:
        return {}
    rows = await always_depth.facts(session, names)
    going = await session.scalars(
        select(CrawlRunModel.host).where(
            CrawlRunModel.host.in_(names),
            CrawlRunModel.depth_part.is_not(None),
            CrawlRunModel.status.in_(ACTIVE),
        )
    )
    running = set(going.all())
    return {host: depth_of(row, threshold, running=host in running) for host, row in rows.items()}


async def known_donor(session: AsyncSession, host: str) -> bool:
    """Донор ли это, принятый человеком, — хостом, как его пишет обход: пометку ставят донору."""
    key = func.regexp_replace(func.lower(DomainModel.host), r"^www\.", "")
    found = await session.scalar(
        select(DomainModel.id)
        .join(DonorModel, DonorModel.domain_id == DomainModel.id)
        .where(is_donor(), key == host)
        .limit(1)
    )
    return found is not None


def frozen_words(parts: int) -> str | None:
    """Судья читает не всю цепочку донора (`gate_union.py`) — словами «i»; читает всю — пусто."""
    if parts <= 0:
        return None
    return (
        f"судья смотрит последние {spaced(cfg.UNION_LINKS)} ссылок цепочки — находки старых "
        f"частей ({spaced(parts)}) заморожены и остаются, пока их не найдут заново"
    )


def _sites(count: int) -> str:
    """«у 1 сайта», «у 5 сайтов» — родительный падеж числом."""
    one = count % 10 == 1 and count % 100 != 11
    return f"{spaced(count)} {'сайта' if one else 'сайтов'}"


def percent(value: float) -> str:
    """Доля процентами по-русски: «0,1 %», «5 %»."""
    return f"{value:g}".replace(".", ",") + " %"


def _left(counts: dict[str, int]) -> str:
    """Что осталось обходить — частями строки; пустые очереди не называются."""
    step = spaced(always_depth.first_step())
    names = ((Stage.NEW, "новые"), (Stage.FIRST, f"до {step}"), (Stage.DEEPER, "дальше"))
    parts = [f"{name} — {spaced(counts[stage.value])}" for stage, name in names if counts[stage]]
    return " · ".join(parts) or "обходить некого"


def _words(available: bool, switch: Switch, waiting: str | None, counts: dict[str, int]) -> str:
    """Строка блока: идёт, ждёт, на паузе или пройдено всё."""
    if not available:
        return UNAVAILABLE
    if not switch.enabled:
        return f"на паузе; осталось: {_left(counts)}"
    if waiting == DONE_WAIT:
        return (
            f"пройдено всё: до конца — {spaced(counts[Stage.EXHAUSTED])} · "
            f"мало рекламодателей — {spaced(counts[Stage.WEAK])}"
        )
    if waiting is not None:
        return f"ждёт: {waiting}; осталось: {_left(counts)}"
    return f"идёт: {_left(counts)}; обходится сейчас {spaced(counts['running'])}"


def _rules(switch: Switch) -> list[str]:
    """Строки «i» о том, как обход 24/7 устроен: порядок, порог, ночь, пауза и сколько разом."""
    step = spaced(always_depth.first_step())
    weak = (
        f"Мало рекламодателей — меньше {percent(switch.threshold_pct)} страниц с платной ссылкой "
        f"на первых {step}: такие сайты дальше не обходятся"
    )
    if switch.recrawl_weak:
        weak += " — но переобход слабых включён: они идут после сильных"
    night = (
        "Ночью идёт ночной обход: новых частей нет, начатые останавливаются на границе страницы "
        "и продолжаются после него."
        if crawl_cfg.NIGHT_ROUND_ENABLED
        else "Ночного обхода нет — обход 24/7 идёт круглые сутки."
    )
    return [
        f"Сначала — сайты, которых ещё не обходили: до {spaced(crawl_cfg.MAX_PAGES_PER_DONOR)} "
        f"страниц каждый; потом — до {step} у каждого; дальше — ещё по {step}, сначала доходным.",
        weak + ".",
        night,
        f"Разом — не больше {spaced(cfg.SLOTS)} частей и только в свободные обходчики: обходы "
        "людей и очередь загрузки — первыми.",
        "Пауза останавливает только обход 24/7: идущие части кончаются на границе страницы, "
        "ночной обход и обходы людей идут как шли.",
    ]


def _facts(found: Survey, done_at: datetime | None) -> list[str]:
    """Строки «i» о том, что сейчас: кто ждёт после отказа, каталоги, итог. Сколько страниц
    пройдено, экран пишет сам — числом `pages`."""
    lines = []
    if found.resting():
        lines.append(
            f"Не пустили — {spaced(found.resting())}: ждут, пока сайт снова откроется ночному "
            "обходу или обходу человека."
        )
    frozen = sum(1 for donor in found.donors if donor.frozen)
    if frozen:
        lines.append(
            f"У {_sites(frozen)} судья смотрит последние {spaced(cfg.UNION_LINKS)} ссылок "
            "цепочки, старые находки заморожены."
        )
    if found.catalogs:
        lines.append(
            f"Доноров-каталогов — {spaced(found.catalogs)}: их обход 24/7, как и ночной, "
            "не обходит."
        )
    if done_at is not None:
        lines.append("Всё пройдено — об этом сказано в Telegram; новые части начнутся сами.")
    return lines


async def _waiting(
    session: AsyncSession, switch: Switch, found: Survey, now: datetime
) -> str | None:
    """Почему включённый обход 24/7 сейчас не идёт — по тем же правилам, что у выдачи."""
    if not cfg.ENABLED or not switch.enabled:
        return None
    if await always.at_night(session, now):
        return NIGHT_WAIT
    if await units_stop(session) is not None:
        return UNITS_WAIT
    return await _idle(session, found)


async def _idle(session: AsyncSession, found: Survey) -> str | None:
    """Окна выдачи открыты, а частей может не быть: некого обходить, всё пройдено, ждут люди."""
    if not found.donors:
        return NO_DONORS
    if not found.remaining() and found.running == 0:
        return DONE_WAIT
    return HUMANS_WAIT if await always.humans_waiting(session) else None


async def _done_at(session: AsyncSession) -> datetime | None:
    row = await session.scalar(
        select(CrawlAlwaysSignalModel).order_by(CrawlAlwaysSignalModel.id.desc()).limit(1)
    )
    return row.sent_at if row is not None and row.reopened_at is None else None


async def view(session: AsyncSession, *, now: datetime) -> View:
    """Обход 24/7 одним ответом для экрана."""
    switch = await always_switch.state(session)
    found = await always.survey(session, switch)
    waiting = await _waiting(session, switch, found, now)
    counts = found.counts()
    pages = int(
        await session.scalar(select(func.coalesce(func.sum(CrawlDepthModel.pages), 0))) or 0
    )
    done_at = await _done_at(session)
    return View(
        available=cfg.ENABLED,
        switch=switch,
        waiting_for=waiting,
        counts=counts,
        pages=pages,
        part_pages=always_depth.first_step(),
        words=_words(cfg.ENABLED, switch, waiting, counts),
        details=[*_rules(switch), *_facts(found, done_at)],
        yields=sorted(
            donor.yield_pct
            for donor in found.donors
            if donor.scored and donor.yield_pct is not None
        ),
        done_at=done_at,
    )
