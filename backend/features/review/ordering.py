"""Порядок очереди рассмотрения: ярус, полка прежних крупных, продающие размещение.

Отдельно от решений (`candidates.py`): порядок — это набор SQL-выражений,
которыми пользуются и страница очереди, и счётчики, и экран; держать его
рядом с записью решений значило бы растить один файл двумя темами.

Порядок показа внутри статуса:

1. ярус по совету о сайте — «вероятно донор», «посмотреть», «сомнительно»;
2. полка крупных сайтов (DR > 80) — в конце яруса; с 11.10.2026 только строки,
   положенные раньше: новые прогоны крупных не кладут (`runs.exclusions`);
3. продающие размещение у себя — первыми внутри яруса и полки
   (`sells_placement`);
4. DR по убыванию, потом имя.
"""

from __future__ import annotations

from enum import StrEnum
from operator import itemgetter
from typing import Any

from sqlalchemy import ColumnElement, case, func, literal, null

from backend.features.core.models.domain import DomainModel
from backend.features.donors.selection import HumanIntent, site_advice
from backend.features.runs.exclusions import big_site


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


def shelf() -> ColumnElement[Any]:
    """Полка крупных сайтов (DR > 80) — в конце яруса; только у строк до 11.10.2026.

    До решения владельца «кому не пишем — по ТЗ» крупный сайт был не отказом,
    а полкой: судья честно зовёт forbes.com и reuters.com изданиями, но гостевой
    пост им не продашь, и решал человек. Теперь DR > 80 — «не подходит»
    (`verdict.check_dr`), крупное СМИ отсекается до Ahrefs, и новые прогоны таких
    не кладут (`candidates.queue_run`). Полка держит строки, положенные раньше:
    без неё ждущие крупные встали бы первыми — порядок внутри яруса по DR.
    Граница — та же, что у отказа: DR 80 — ещё не крупный.
    """
    return case((big_site(), 1), else_=0)


class SellsBy(StrEnum):
    """Откуда известно, что сайт продаёт размещение, — чей это голос.

    Экран показывает признак один раз, в колонке его источника: ответ
    сайта — в «Донор ответил», тип сайта по судье — у судьи. До 25.09.2026
    строка печатала «продаёт размещение» трижды: значком у домена,
    пояснением «судья: продаёт размещение у себя» рядом и ярлыком судьи.
    """

    ANSWER = "answer"  # сайт сам ответил на письмо
    HUMAN = "human"  # человек назвал тип сайта на «Отборе»
    JUDGE = "judge"  # тип сайта по судье
    DOOR = "door"  # страница для авторов или пункт меню главной


def _sells_branches() -> list[tuple[ColumnElement[bool], Any, Any]]:
    """Условие → пояснение → источник. Одна таблица на оба выражения:
    порядок и подпись не могут разойтись с источником.

    Порядок — порядок силы. Ответ самого сайта сильнее всех: «не продаём»
    гасит любые догадки. Мнение человека о типе сайта сильнее судьи: если
    человек назвал сайт чем-то другим, ни судья, ни дверь его не поднимут.
    Дверь — страница для авторов или пункт меню «Advertise» — последней:
    она про то, что сайт зовёт, а не про то, что он точно продаёт.

    Посредника дверь не поднимает: биржа со страницей «пишите для нас»
    зовёт к чужим площадкам (`author_door.opens` решает так же). Очередь
    №21: adsy.com и vefogix.com стояли первыми в «посмотреть».
    """
    answer = literal(SellsBy.ANSWER.value)
    human = literal(SellsBy.HUMAN.value)
    judge = literal(SellsBy.JUDGE.value)
    door = literal(SellsBy.DOOR.value)
    return [
        (DomainModel.seller_answer == "declines", null(), null()),
        (DomainModel.seller_answer == "sells", literal("сам сказал: продаёт размещение"), answer),
        (
            DomainModel.seller_answer == "free",
            literal("сам сказал: берёт статьи бесплатно"),
            answer,
        ),
        # Тип сайта человек называет словами «Отбора» (`HumanIntent`). До 11.10.2026
        # здесь ждали тип судьи `sells_placement`, которого человек не говорит, —
        # и ветка не загоралась ни разу.
        (
            DomainModel.human_intent == HumanIntent.PUBLISHER.value,
            literal("человек: продаёт размещение у себя"),
            human,
        ),
        (DomainModel.human_intent.is_not(None), null(), null()),
        (
            DomainModel.site_intent == "sells_placement",
            literal("судья: продаёт размещение у себя"),
            judge,
        ),
        (DomainModel.site_intent == "link_vendor", null(), null()),
        (func.coalesce(DomainModel.site_door, "") != "", DomainModel.site_door, door),
    ]


def sells_placement() -> ColumnElement[Any]:
    """Продаёт ли сайт размещение у себя — словами, почему; NULL — не видно.
    Из таблицы берутся условие и пояснение."""
    return case(*map(itemgetter(0, 1), _sells_branches()), else_=null())


def sells_source() -> ColumnElement[Any]:
    """Чей голос сказал «продаёт» (`SellsBy`); NULL — признака нет.
    Из той же таблицы — условие и источник."""
    return case(*map(itemgetter(0, 2), _sells_branches()), else_=null())


def tier_order() -> ColumnElement[Any]:
    return case(
        (tier() == Tier.LIKELY.value, 0),
        (tier() == Tier.OPEN.value, 1),
        else_=2,
    )
