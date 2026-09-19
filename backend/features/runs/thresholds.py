"""Пороги: текущие, предпросмотр последствий и новая версия.

Пороги версионируются намеренно: смена порога не должна переписывать
вердикты прошлых прогонов — иначе через полгода непонятно, почему домен
отсеялся. Поэтому «сохранить» здесь означает «завести новую версию»,
а не «поправить строчку».

**Предпросмотр считается тем же правилом, что и сам отбор.** Второй
экземпляр правила в предпросмотре разошёлся бы с настоящим при первой
же правке, и экран показывал бы последствия, которых не будет.

**Показывается и то, что выпадет, и то, что вернётся.** Порог двигают
в обе стороны, и «из базы выпадет 340» без «вернётся 12» — половина
ответа. Отдельной строкой — сколько среди выпавших тех, у кого цена уже
получена: за них заплачено не только юнитами, но и письмом.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import filters as filters_cfg
from backend.features.core.domain import DonorStatus
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.run import RunSettingsModel
from backend.features.donors.verdict import Metrics, Thresholds, check_metrics


@dataclass(frozen=True, slots=True)
class Consequences:
    """Что станет с базой, если применить новые пороги."""

    checked: int
    suitable_now: int
    suitable_after: int
    falls_out: int
    falls_out_with_price: int
    comes_back: int
    #: Домены без метрик: их вердикт не изменится, потому что его нет.
    #: Число показывается отдельно — ступень, которая ничего не решает,
    #: иначе выглядит работающей.
    unchecked: int


def _metrics_of(donor: DonorModel) -> Metrics:
    """Метрики из JSONB. Ключи те же, что пишет сборщик."""
    raw: dict[str, Any] = donor.metrics or {}
    return Metrics(
        dr=float(donor.dr) if donor.dr is not None else None,
        org_traffic=donor.org_traffic,
        refdomains=raw.get("refdomains"),
        org_keywords=raw.get("org_keywords"),
    )


def consequences(donors: Sequence[DonorModel], candidate: Thresholds) -> Consequences:
    """Пересчитать вердикты тем же правилом, что и отбор."""
    checked = suitable_now = suitable_after = 0
    falls_out = falls_out_with_price = comes_back = unchecked = 0

    for donor in donors:
        metrics = _metrics_of(donor)
        if metrics.dr is None:
            unchecked += 1
            continue

        checked += 1
        was = donor.status is DonorStatus.SUITABLE
        # `check_metrics` возвращает вердикт при отказе и `None`, если
        # домен прошёл все пороги.
        will = check_metrics(metrics, candidate) is None

        suitable_now += int(was)
        suitable_after += int(will)
        if was and not will:
            falls_out += 1
            if donor.last_price_usd is not None:
                falls_out_with_price += 1
        if will and not was:
            comes_back += 1

    return Consequences(
        checked=checked,
        suitable_now=suitable_now,
        suitable_after=suitable_after,
        falls_out=falls_out,
        falls_out_with_price=falls_out_with_price,
        comes_back=comes_back,
        unchecked=unchecked,
    )


class ThresholdsRepository:
    """Версии порогов: прочитать текущую, завести новую."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def current(self) -> RunSettingsModel | None:
        """Последняя версия. `None` — порогов ещё не заводили, и тогда
        действуют умолчания конфига."""
        rows = await self._session.execute(
            select(RunSettingsModel).order_by(RunSettingsModel.version.desc()).limit(1)
        )
        return rows.scalar_one_or_none()

    async def history(self, *, limit: int = 20) -> Sequence[RunSettingsModel]:
        rows = await self._session.execute(
            select(RunSettingsModel).order_by(RunSettingsModel.version.desc()).limit(limit)
        )
        return rows.scalars().all()

    async def donors(self) -> Sequence[DonorModel]:
        """База целиком: предпросмотр отвечает на вопрос «что станет
        со всей базой», а не «что станет со страницей таблицы»."""
        rows = await self._session.execute(select(DonorModel))
        return rows.scalars().all()

    async def save(self, candidate: Thresholds, *, author: str) -> RunSettingsModel:
        """Завести новую версию порогов.

        Остальные настройки переносятся из текущей версии, а не берутся
        из конфига: конфиг — это умолчания для первой версии, и подмешать
        их в десятую значило бы молча откатить чужую правку.
        """
        previous = await self.current()
        version = 1 if previous is None else previous.version + 1

        settings = RunSettingsModel(
            version=version,
            created_by=author,
            min_dr=candidate.min_dr,
            min_org_traffic=candidate.min_org_traffic,
            min_refdomains=candidate.min_refdomains,
            min_keywords=candidate.min_keywords,
            geo_top_n=previous.geo_top_n if previous else filters_cfg.GEO_TOP_N,
            geo_min_share=previous.geo_min_share if previous else filters_cfg.GEO_MIN_SHARE,
            metrics_ttl_days=(
                previous.metrics_ttl_days if previous else filters_cfg.METRICS_TTL_DAYS
            ),
            price_ttl_days=previous.price_ttl_days if previous else filters_cfg.PRICE_TTL_DAYS,
            units_cap=previous.units_cap if previous else 0,
        )
        self._session.add(settings)
        await self._session.flush()
        return settings


def defaults() -> Thresholds:
    """Умолчания конфига — то, что действует, пока порогов не заводили."""
    return Thresholds(
        min_dr=filters_cfg.MIN_DR,
        min_org_traffic=filters_cfg.MIN_ORG_TRAFFIC,
        min_refdomains=filters_cfg.MIN_REFDOMAINS,
        min_keywords=filters_cfg.MIN_KEYWORDS,
    )
