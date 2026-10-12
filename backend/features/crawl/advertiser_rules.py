"""Кто рекламодатель — два условия, одни у списка, приёмки и ручной очереди форм.

**Кандидат, которому пишем** (`writes_to`): «куплено» скоринга или «пишем» человека,
без «не пишем»: решение человека сильнее вердикта скоринга в обе стороны. Правило
одно у перевода (`crawl/promote.py`), списка, приёмки и проверки оффера при отправке
(`letters/offer_standing.py`): по нему приёмка считает рекламодателей донора, а
фильтр «Донор» списка ищет любого донора, который на рекламодателя ссылается. До
12.10.2026 у перевода была своя копия условия (`promote.eligible_of`) — копии
расходятся при первой правке.

**Рекламодатель, которому пишем** (`in_work`): строка `advertisers` по найденной
ссылке — всякая, бизнес ниши — после «пишем» человека (`niche.decide`). Бизнес без
решения ждёт его в своей очереди, а сказанное «не пишем» — не рекламодатель. То же
условие у поиска адресов (`crawl/contacts.py`).

Своим модулем, а не в списке или приёмке: оба читают оба условия, и импорт друг
у друга замкнул бы круг.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement
from sqlalchemy.orm.util import AliasedClass

from backend.features.core.domain import Verdict
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.crawl.niche import NICHE


def writes_to(
    candidate: type[CandidateModel] | AliasedClass[CandidateModel] = CandidateModel,
) -> ColumnElement[bool]:
    """Кандидат, которому пишем: «куплено» или «пишем», без «не пишем». Отклонённый
    человеком дальше не идёт, сколько бы баллов ему ни насчитали.

    `candidate` — модель или её псевдоним, когда кандидат стоит в подзапросе
    («у домена есть подходящий»).
    """
    chosen = (candidate.verdict == Verdict.BOUGHT) | candidate.confirmed.is_(True)
    result: ColumnElement[bool] = chosen & candidate.confirmed.is_not(False)
    return result


def in_work() -> ColumnElement[bool]:
    """Рекламодатель, которому пишем или напишем: по ссылке — всякий, бизнес ниши —
    после «пишем» человека."""
    return (AdvertiserModel.source != NICHE) | AdvertiserModel.confirmed_by_human.is_(True)
