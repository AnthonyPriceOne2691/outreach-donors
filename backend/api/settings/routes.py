"""Пороги и расход.

Пороги правит оператор — это работа с базой, а не с деньгами; но правка
заводит **новую версию**, а не переписывает старую: вердикты прошлых
прогонов должны оставаться объяснимыми. Веса и пороги Этапа 2 здесь только
показываются (`/settings/advertisers`): они — настройки окружения.

Расход смотрят все: это то же содержимое базы, что и доноры. Расход продаж —
только с правом «Продажи» (решение Anthony 10.10.2026, П2б, `runs/spending.py`).
"""

from __future__ import annotations

import logging
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import actor, db_session, needs
from backend.api.settings.schemas import (
    LIMITS,
    RULE_LIMITS,
    AdvertiserSettingsView,
    ConsequencesView,
    SpendingView,
    ThresholdsBody,
    ThresholdsVersion,
    ThresholdsView,
    share_percent,
)
from backend.config import ahrefs as ahrefs_cfg
from backend.features.access.permissions import visible_stages
from backend.features.access.repository import AccessRepository
from backend.features.ahrefs.client import AhrefsClient, AhrefsError
from backend.features.ahrefs.units import Quota
from backend.features.core.domain import AuditAction, Permission, Stage
from backend.features.core.models.access import UserModel
from backend.features.crawl import selection_settings
from backend.features.donors.geo import assert_settings_allow_limited_fetch
from backend.features.runs.spending import SpendingRepository
from backend.features.runs.thresholds import (
    ThresholdsRepository,
    consequences,
    default_rules,
    defaults,
    in_force,
    rules_in_force,
)
from backend.features.serp.dataforseo import SerpError
from backend.features.serp.factory import UnknownProviderError, build_provider

logger = logging.getLogger(__name__)
router = APIRouter(tags=["настройки"])

_settler = Depends(needs(Permission.SETTINGS))
_viewer = Depends(needs(Permission.VIEW))


def _clamped(name: str, value: int) -> int:
    """Умолчание окружения — в границах поля: за ними экран «Пороги» не открылся бы вовсе
    (схема ответа отказала бы пятисоткой), а так он показывает ближайшее допустимое,
    и журнал называет переменную, которую поправить."""
    low, high = {**LIMITS, **RULE_LIMITS}[name]
    kept = min(max(value, low), high)
    if kept != value:
        logger.warning(
            "пороги: умолчание окружения для %s = %s вне границ %s…%s, на экране — %s",
            name,
            value,
            low,
            high,
            kept,
        )
    return kept


def _defaults_body() -> ThresholdsBody:
    values, rules = defaults(), default_rules()
    return ThresholdsBody(
        min_dr=_clamped("min_dr", values.min_dr),
        min_org_traffic=_clamped("min_org_traffic", values.min_org_traffic),
        min_refdomains=_clamped("min_refdomains", values.min_refdomains),
        min_keywords=_clamped("min_keywords", values.min_keywords),
        max_dr=_clamped("max_dr", values.max_dr),
        geo_top_n=_clamped("geo_top_n", rules.geo.top_n),
        geo_min_share_percent=_clamped("geo_min_share_percent", share_percent(rules.geo.min_share)),
        metrics_ttl_days=_clamped("metrics_ttl_days", rules.metrics_ttl_days),
    )


@router.get("/settings/thresholds", response_model=ThresholdsView, summary="Пороги и их версии")
async def current_thresholds(
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> ThresholdsView:
    repository = ThresholdsRepository(session)
    current = await repository.current()
    return ThresholdsView(
        current=None if current is None else ThresholdsVersion.of(current),
        defaults=_defaults_body(),
        history=[ThresholdsVersion.of(version) for version in await repository.history()],
    )


@router.post(
    "/settings/preview",
    response_model=ConsequencesView,
    summary="Эти пороги против действующих — до сохранения",
)
async def preview(
    body: ThresholdsBody,
    _: UserModel = _settler,
    session: AsyncSession = Depends(db_session),
) -> ConsequencesView:
    """Сравнить пороги с действующими по метрикам доменов базы, ничего не сохраняя.

    Обе стороны — тем же правилом, что у прогона: второй экземпляр правила
    разошёлся бы с настоящим при первой правке. Действующие — те, что возьмёт
    следующий прогон (`in_force`), а не сохранённые вердикты: те ставят ещё регион
    и прошлые версии порогов (проверка прода 10.10.2026).
    """
    donors = await ThresholdsRepository(session).donors()
    now = await in_force(session)
    return ConsequencesView.of(consequences(donors, body.to_thresholds(now.max_dr), in_force=now))


@router.post(
    "/settings/thresholds",
    response_model=ThresholdsVersion,
    summary="Новая версия порогов",
)
async def save_thresholds(
    body: ThresholdsBody,
    author: UserModel = _settler,
    session: AsyncSession = Depends(db_session),
) -> ThresholdsVersion:
    # Правило региона и срок метрик — части той же версии: не присланное берётся
    # из действующей, и несогласованное правило — отказ словами до записи (`geo.py`).
    rules = body.with_rules_of(await rules_in_force(session))
    assert_settings_allow_limited_fetch(rules.geo.top_n, rules.geo.min_share)
    repository = ThresholdsRepository(session)
    settings = await repository.save(rules.thresholds, author=author.email, rules=rules)
    await AccessRepository(session).record(
        AuditAction.THRESHOLDS_CHANGED,
        author_id=author.id,
        target=f"settings:{settings.id}",
        details={
            "версия": settings.version,
            "dr": body.min_dr,
            "трафик": body.min_org_traffic,
            "реф. домены": body.min_refdomains,
            "ключи": body.min_keywords,
            "крупный сайт, DR выше": rules.thresholds.max_dr,
            "топ стран": rules.geo.top_n,
            "доля страны, %": share_percent(rules.geo.min_share),
            "срок метрик, дней": rules.metrics_ttl_days,
        },
    )
    await session.commit()
    return ThresholdsVersion.of(settings)


@router.get(
    "/settings/advertisers",
    response_model=AdvertiserSettingsView,
    summary="Веса, пороги и «кому не пишем» Этапа 2 — что действует",
)
async def advertiser_settings(_: UserModel = _viewer) -> AdvertiserSettingsView:
    """Чем обход судит ссылки сейчас. Только чтение: значения Этапа 2 — настройками
    окружения (`config/advertisers.py`), а не версиями, как пороги Этапа 1: пересчёт
    кандидатов обязан переписать вердикт новыми весами, а не сохранить прежний."""
    return AdvertiserSettingsView.of(selection_settings.in_force())


@router.get("/usage", response_model=SpendingView, summary="Расход по статьям")
async def usage(
    user: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> SpendingView:
    """Расход с начала месяца и остаток у провайдера.

    Недоступный остаток — не повод прятать расход: своя таблица знает,
    на что мы потратили, и этот ответ не зависит от провайдера. Поэтому
    неудача запроса остатка отдаётся отдельным полем, а не пятисоткой.

    Без права «Продажи» — расход без операций продаж, весь: статьи, суммы и итог
    сходятся, а `sales_hidden` говорит экрану, что итог — без продаж (П2б).
    """
    stages = visible_stages(actor(user))
    spending = await SpendingRepository(session).since_month_start(stages=stages)
    serp_left, serp_error = await _serp_balance()

    # Спрашивается сырой остаток провайдера, а не бюджет прогона: бюджет —
    # это меньшее из остатка и капа, и сравнивать его с нашим расходом
    # бессмысленно (первая версия экрана так и показывала «израсходовано
    # 0» при шести тысячах потраченных).
    left: int | None = None
    error: str | None = None
    client = AhrefsClient()
    try:
        left = Quota.from_payload(await client.limits_and_usage()).available
    except (AhrefsError, OSError) as exc:
        logger.warning("расход: остаток у Ahrefs недоступен (%s)", exc)
        error = (
            "Не удалось узнать остаток у Ahrefs. Расход ниже — из своей таблицы, "
            "он от провайдера не зависит."
        )
    finally:
        await client.aclose()

    return SpendingView.of(
        spending,
        ahrefs_left=left,
        ahrefs_cap=ahrefs_cfg.UNITS_CAP,
        error=error,
        serp_left_usd=serp_left,
        serp_left_error=serp_error,
        sales_hidden=Stage.SALES not in stages,
    )


async def _serp_balance() -> tuple[Decimal | None, str | None]:
    """Остаток у источника выдачи. Недоступен — это отдельное поле,
    а не пятисотка: свой расход мы знаем и без провайдера.

    У запасного источника счёта в деньгах нет вовсе — он платит юнитами
    Ahrefs, и спрашивать его не о чем.
    """
    ahrefs = AhrefsClient()
    try:
        provider = build_provider(ahrefs)
    except UnknownProviderError as exc:
        # Не подключён — не авария: экран расхода обязан открываться и до
        # того, как на сервер положили ключи выдачи.
        await ahrefs.aclose()
        logger.warning("расход: источник выдачи не настроен (%s)", exc)
        return None, "Источник выдачи не подключён — остаток узнать не у кого."
    balance = getattr(provider, "balance", None)
    try:
        if balance is None:
            return None, "Выбран запасной источник выдачи — он платит юнитами, а не деньгами."
        return Decimal(str(await balance())), None
    except (SerpError, OSError) as exc:
        logger.warning("расход: остаток у источника выдачи недоступен (%s)", exc)
        return None, "Не удалось узнать остаток у источника выдачи."
    finally:
        await ahrefs.aclose()
        aclose = getattr(provider, "aclose", None)
        if aclose is not None:
            await aclose()
