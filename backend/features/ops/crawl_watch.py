"""Тревоги сторожа об обходе — Этап 2.

Своим модулем от общего сторожа (`silence.py`): тревоги несут этап и считаются только у того,
кто его видит, — обход закрыт правом «Рекламодатели» (решение 02.11.2026), как тревоги о
почте этапа. Фоновый проход видит все этапы и говорит о них как раньше.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import Boolean, ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import CrawlOutcome, CrawlStatus, Stage, StopReason
from backend.features.core.models.crawl import CrawlRunModel
from backend.features.crawl.units_stop import units_stop
from backend.features.ops.alarms import Alarm, cause, no_cause
from backend.features.runs.spending import crawl_spent_this_month

logger = logging.getLogger(__name__)

#: За сколько последних обходов смотрим здоровье краула.
RUNS_WATCHED = 10

#: С какой доли последних обходов их проблемы — серия, а не случай (`blocked`).
SERIES_SHARE = 0.3

#: Меньше стольких обходов — не серия при любой доле: в свежей базе один обход — уже 100 %.
SERIES_MIN = 2


def _stuck() -> ColumnElement[bool]:
    """Обход упёрся в защиту: закрылся целиком или остановлен по доле отказов."""
    return or_(
        CrawlRunModel.outcome == CrawlOutcome.BLOCKED,
        CrawlRunModel.stop_reason == StopReason.UNHEALTHY,
    )


async def blocked(session: AsyncSession) -> Alarm | None:
    """Обход упирается в защиту сайтов — серией, а не одним сайтом.

    Требование про долю отказов выше 30 % — о страницах внутри одного обхода
    (`CRAWL_STOP_SHARE`): такой обход останавливает себя сам, и это видно в его отчёте.
    Тревога говорит о другом — о серии: `SERIES_SHARE` и выше среди последних обходов
    закрылись целиком или остановлены по доле отказов; обход, с которым случилось и то и
    другое, считается один раз.

    До 02.11.2026 тревогу поднимал один остановленный обход из десяти. За выходные она вышла
    четыре раза от одного-двух обходов, а такие обходы обычно собрали сотни страниц: человеку
    нечего было делать (слово Anthony: «шумная тревога, убираем»).

    Считается по последним обходам, а не за всё время: доля за всю
    историю прячет защиту, включившуюся вчера. Только законченным:
    у идущего обхода исхода ещё нет, и он разбавлял бы долю закрытых.

    Сайт, упёршийся и в прошлый свой обход, — не новость и в счёт не идёт (05.11.2026). Ночной
    круг ставит таких последними (`crawl/night.py`, ранги «частично» и «не начинался»), и хвост
    круга поднимал тревогу каждое утро: она стояла до следующей ночи, хотя доля за круг не
    менялась и человеку нечего было делать. Считаются упёршиеся впервые и те, кто в прошлый раз
    открылся: это и есть защита, включившаяся вчера.
    """
    before = func.lag(_stuck(), type_=Boolean).over(
        partition_by=CrawlRunModel.host, order_by=CrawlRunModel.id
    )
    done = (
        select(CrawlRunModel.id, before.label("before"))
        .where(CrawlRunModel.status == CrawlStatus.DONE)
        .subquery()
    )
    rows = (
        (
            await session.execute(
                select(CrawlRunModel)
                .join(done, done.c.id == CrawlRunModel.id)
                .where(done.c.before.is_not(True))
                .order_by(CrawlRunModel.id.desc())
                .limit(RUNS_WATCHED)
            )
        )
        .scalars()
        .all()
    )
    closed = sum(1 for row in rows if row.outcome is CrawlOutcome.BLOCKED)
    troubled = sum(
        1
        for row in rows
        if row.outcome is CrawlOutcome.BLOCKED or row.stop_reason is StopReason.UNHEALTHY
    )
    if troubled < SERIES_MIN or troubled / len(rows) < SERIES_SHARE:
        return None

    return Alarm(
        code="crawl-blocked",
        title="Обход упирается в защиту",
        detail=(
            f"Из последних {len(rows)} обходов, не считая сайтов, закрытых и в прошлый раз, "
            f"упёрлись в защиту {troubled}: закрылись целиком {closed}, остановлены по доле "
            f"отказов {troubled - closed}. Дальше это лечится "
            "не повтором, а следующим уровнем каскада: браузером (CRAWL_BROWSER_ENABLED) или "
            "резидентными прокси (CRAWL_PROXY_URLS)"
        ),
        stage=Stage.ADVERTISERS,
        concern=cause(
            "адреса закрытых сайтов не собираются",
            "включить следующий уровень обхода — браузер или резидентные прокси",
        ),
    )


def _setting_names(exc: ValueError) -> str:
    """Имена настроек из отказа pydantic — без значений; не pydantic — «CRAWL_*»."""
    errors = getattr(exc, "errors", None)
    found = (
        sorted({str(error["loc"][0]) for error in errors() if error.get("loc")})
        if callable(errors)
        else []
    )
    return ", ".join(found) or "CRAWL_*"


async def units_stopped(session: AsyncSession) -> Alarm | None:
    """Обходы стоят: юнитов Ahrefs не хватает на DR новых кандидатов (`crawl/units_stop.py`).
    Сторож ключа говорит о капе всех этапов, а эта тревога — о том, что встало здесь."""
    stop = await units_stop(session)
    if stop is None:
        return None
    return Alarm(
        code="crawl-no-units",
        title=stop.title,
        detail=stop.detail,
        stage=Stage.ADVERTISERS,
        concern=cause("новые обходы и ночной обход стоят, новых кандидатов нет", stop.todo),
    )


async def cap_reached(session: AsyncSession) -> Alarm | None:
    """Свой потолок Этапа 2 выбран: прокси и рендеринг не зовутся до первого числа, и обходы
    за защитой закрываются — не потому, что сломались (`crawl/spend.py`).

    Конфиг обхода грузится здесь, а не при импорте: в процессе сторожа идёт и повтор
    передачи лидов продаж, и опечатка в `CRAWL_*` роняла бы его целиком. Битый конфиг —
    своя тревога с именами настроек, без значений: в значении бывает пароль прокси."""
    try:
        from backend.config import crawl as crawl_cfg  # noqa: PLC0415 — см. выше
    except ValueError as exc:
        names = _setting_names(exc)
        logger.warning("сторож тишины: настройки обхода не читаются (%s)", names)
        return Alarm(
            code="crawl-config-broken",
            title="Настройки обхода не читаются",
            detail=(
                f"В .env ошибка в настройках обхода ({names}): обходчик не стартует, "
                "и свой потолок Этапа 2 не проверяется. Значение поправить и пересоздать контейнеры"
            ),
            stage=Stage.ADVERTISERS,
            concern=cause(
                "обходчик не стартует", "поправить настройку обхода и пересоздать контейнеры"
            ),
        )
    cap = crawl_cfg.MONTH_CAP_USD
    if cap is None:
        return None
    spent = await crawl_spent_this_month(session)
    if spent < Decimal(str(cap)):
        return None
    return Alarm(
        code="crawl-cap-reached",
        title="Потолок Этапа 2 на обход выбран",
        detail=(
            f"Прокси и рендеринг обошлись в {spent:.2f} $ при своём потолке {cap:.2f} $. "
            "До первого числа или до поднятия потолка платные уровни обхода не зовутся, "
            "и закрытые сайты остаются необойдёнными — это не поломка, но выглядит одинаково"
        ),
        stage=Stage.ADVERTISERS,
        # Потолок держит деньги, а обход идёт бесплатными уровнями: устройство, а не поломка.
        concern=no_cause(
            "потолок сработал, как задуман: платные уровни обхода ждут первого числа, "
            "бесплатные идут"
        ),
    )
