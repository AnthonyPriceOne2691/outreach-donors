"""Порядок очереди рассмотрения: ярус, полка крупных, продающие размещение.

Отдельно от решений (`candidates.py`): порядок — это набор SQL-выражений,
которыми пользуются и страница очереди, и счётчики, и экран; держать его
рядом с записью решений значило бы растить один файл двумя темами.

Порядок показа внутри статуса:

1. ярус по совету о сайте — «вероятно донор», «посмотреть», «сомнительно»;
2. полка крупных сайтов (DR 80+) — в конце яруса;
3. продающие размещение у себя — первыми внутри яруса и полки
   (`sells_placement`);
4. DR по убыванию, потом имя.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from sqlalchemy import ColumnElement, case, func, literal, null

from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.donors.selection import site_advice


class Tier(StrEnum):
    """Ярус очереди по совету о сайте. Порядок — порядок показа."""

    LIKELY = "likely"  # советуют принять
    OPEN = "open"  # «посмотри» или совета нет
    DOUBTFUL = "doubtful"  # советуют отказ — скрыт под фильтром


def tier() -> ColumnElement[Any]:
    advice = site_advice()
    return case(
        (advice == "accept", Tier.LIKELY.value),
        (advice == "reject", Tier.DOUBTFUL.value),
        else_=Tier.OPEN.value,
    )


#: Полка крупных сайтов: внутри яруса домены с DR не ниже этого — в конце.
#: Строка «кому не пишем» требований называет «домены DR > 80»; судья
#: честно зовёт forbes.com и reuters.com изданиями, но гостевой пост
#: им не продашь, и первыми в очереди стоять они не должны. Полка, а не
#: отказ: решает человек.
BIG_SITE_DR = 80


def shelf() -> ColumnElement[Any]:
    return case((DonorModel.dr >= BIG_SITE_DR, 1), else_=0)


def sells_placement() -> ColumnElement[Any]:
    """Продаёт ли сайт размещение у себя — словами, почему; NULL — не видно.

    Порядок — порядок силы. Ответ самого сайта сильнее всех: «не продаём»
    гасит любые догадки. Мнение человека о типе сайта сильнее судьи: если
    человек назвал сайт чем-то другим, ни судья, ни дверь его не поднимут.
    Дверь — страница для авторов или пункт меню «Advertise» — последней:
    она про то, что сайт зовёт, а не про то, что он точно продаёт.

    Посредника дверь не поднимает: биржа со страницей «пишите для нас»
    зовёт к чужим площадкам (`author_door.opens` решает так же). Очередь
    №21: adsy.com и vefogix.com стояли первыми в «посмотреть».
    """
    return case(
        (DomainModel.seller_answer == "declines", null()),
        (DomainModel.seller_answer == "sells", literal("сам сказал: продаёт размещение")),
        (DomainModel.seller_answer == "free", literal("сам сказал: берёт статьи бесплатно")),
        (
            DomainModel.human_intent == "sells_placement",
            literal("человек: продаёт размещение у себя"),
        ),
        (DomainModel.human_intent.is_not(None), null()),
        (DomainModel.site_intent == "sells_placement", literal("судья: продаёт размещение у себя")),
        (DomainModel.site_intent == "link_vendor", null()),
        (func.coalesce(DomainModel.site_door, "") != "", DomainModel.site_door),
        else_=null(),
    )


def tier_order() -> ColumnElement[Any]:
    return case(
        (tier() == Tier.LIKELY.value, 0),
        (tier() == Tier.OPEN.value, 1),
        else_=2,
    )
