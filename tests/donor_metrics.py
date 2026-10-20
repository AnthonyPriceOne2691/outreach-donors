"""Годный по порогам донор в тестах — с метриками и странами, по которым он годен.

С 20.10.2026 прогон судит свои домены сам — своей страной и порогами, по метрикам и
странам, что лежат у домена (`donors/rejudge.py`). «Подходит» без метрик — вердикт,
которого прогону вынести нечем: такой домен он назовёт «не проверен» и в очередь не
положит. Тесту очереди нужен домен, который прогон признает годным, — с данными замера.
"""

from __future__ import annotations

from datetime import UTC, datetime

from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

#: Трафик, ключи и реф. домены выше умолчаний порогов (`config/filters.py`).
TRAFFIC = 9000
REFDOMAINS = 500
KEYWORDS = 2000


async def measured(session: AsyncSession, domain: DomainModel, *, country: str = "us") -> None:
    """Дать донору домена метрики и страны замера: DR — какой есть, трафик, ключи и
    реф. домены — проходят пороги, страна — первой в топ-5. Вердикт замера — «подходит»."""
    donor = await session.scalar(select(DonorModel).where(DonorModel.domain_id == domain.id))
    assert donor is not None, f"у {domain.host} нет строки донора"
    moment = datetime.now(UTC)
    donor.metrics = {
        "domain_rating": donor.dr,
        "org_traffic": TRAFFIC,
        "refdomains": REFDOMAINS,
        "org_keywords": KEYWORDS,
    }
    donor.org_traffic = TRAFFIC
    donor.metrics_refreshed_at = moment
    donor.geo = country
    donor.geo_breakdown = [{"country": country, "org_traffic": TRAFFIC, "share": 1.0}]
    donor.geo_top_share = 1.0
    donor.geo_refreshed_at = moment
    donor.measured_status, donor.measured_reason = "suitable", None
    await session.flush()
