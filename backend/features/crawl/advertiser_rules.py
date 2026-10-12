"""Кто рекламодатель — два условия, одни у списка, приёмки и ручной очереди форм.

**Кандидат, которому пишем** (`writes_to`): «куплено» скоринга или «пишем» человека,
без «не пишем» — условие перевода (`crawl/promote.py`): решение человека сильнее
вердикта скоринга в обе стороны. По нему приёмка считает рекламодателей донора, а
фильтр «Донор» списка ищет любого донора, который на рекламодателя ссылается.

**Рекламодатель, которому пишем** (`in_work`): строка `advertisers` по найденной
ссылке — всякая, бизнес ниши — после «пишем» человека (`niche.decide`). Бизнес без
решения ждёт его в своей очереди, а сказанное «не пишем» — не рекламодатель. То же
условие у поиска адресов (`crawl/contacts.py`).

Своим модулем, а не в списке или приёмке: оба читают оба условия, и импорт друг
у друга замкнул бы круг.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import ColumnElement

from backend.features.core.domain import Verdict
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.crawl.niche import NICHE


def writes_to(candidate: Any = CandidateModel) -> ColumnElement[bool]:
    """Кандидат, которому пишем: «куплено» или «пишем», без «не пишем».

    `candidate` — модель или её псевдоним, когда кандидат стоит в подзапросе.
    Условие перевода живёт в `crawl/promote.py`; расхождение ловит тест приёмки
    (`test_a_donor_is_found_by_the_promotion_rule`).
    """
    chosen = (candidate.verdict == Verdict.BOUGHT) | candidate.confirmed.is_(True)
    result: ColumnElement[bool] = chosen & candidate.confirmed.is_not(False)
    return result


def in_work() -> ColumnElement[bool]:
    """Рекламодатель, которому пишем или напишем: по ссылке — всякий, бизнес ниши —
    после «пишем» человека."""
    return (AdvertiserModel.source != NICHE) | AdvertiserModel.confirmed_by_human.is_(True)
