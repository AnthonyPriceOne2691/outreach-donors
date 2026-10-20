"""Ссылающиеся домены цели — `site-explorer/refdomains`: что спрашиваем и почём.

Источник кандидатов «Конкуренты» (`runs/competitors.py`): сайт, который ссылается на
конкурента, уже ставит ссылки сайтам этой ниши. Запрос — один на конкурента.

**Отсев — на стороне Ahrefs, до оплаты.** Ahrefs берёт за строки ответа, поэтому пороги
прогона, которые видны по строке списка, уходят в `where`: DR от нижнего порога до границы
крупного сайта, органический трафик сайта не ниже порога, ссылка без nofollow, домен не спам.
Строки вне порогов не приходят и не оплачиваются, и глубина «до N сайтов» значит N сайтов,
уже прошедших два порога из четырёх. Реф. домены, ключи и регион строке списка не по силам —
их проверяет воронка пакетом метрик, как у выдачи.

**Цена строки — единица плюс цена каждого поля запроса, и `select`, и `where`; ключевое поле
(`domain`) бесплатно.** Модель сходится с тремя замерами живым ключом: наш `linkeddomains`
22.09.2026 — четыре поля, одно из них ключевое, 4 юнита строка (okf/advertiser-discovery.md),
и два замера соседней системы на этом эндпоинте — семь полей в `select` и одно только
в `where` дали 8, то же с `traffic_domain` — 18. Цены полей — по замеру той же системы
заголовком `x-api-units-cost-row`: DR 1, трафик 10. Незнакомое поле — 10, дороже любого
замеренного: занизить цену хуже, чем завысить. Сколько списано на самом деле, называет ответ
(`UnitsCost.per_row`), и журнал прогона кладёт это рядом со сметой — расхождение значит,
что таблицу пора перемерить.

**Больше тысячи строк за запрос Ahrefs не отдаёт, `offset` он молча не читает** (замер
соседней системы) — глубже листают по DR. Прогону это не нужно: тысяча новых сайтов на
одного конкурента — это десятки тысяч юнитов на одни метрики.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any

from backend.features.ahrefs.units import MIN_REQUEST_UNITS

PATH = "/v3/site-explorer/refdomains"
#: Имя операции в журнале расхода (`core/usage.OPERATION_PROVIDERS`).
OPERATION = "refdomains"
#: Под каким ключом ответ кладёт строки — берётся явно (`client._rows`).
ROWS_KEY = "refdomains"
#: Больше строк в один ответ Ahrefs не отдаёт.
MAX_ROWS = 1000
#: Что считать целью: `subdomains` — домен со всеми поддоменами, `domain` — только он,
#: `prefix` и `exact` — адрес страницы. Литералы Ahrefs.
MODES = frozenset({"subdomains", "domain", "prefix", "exact"})

#: Что берём о сайте: имя и DR. DR — для журнала и страховкой на случай, если Ahrefs
#: округлит границу фильтра; трафик в `select` не нужен — пакет метрик воронки его купит.
SELECT = ("domain", "domain_rating")
#: С верхнего DR вниз: при глубине меньше списка первыми приходят самые сильные сайты.
ORDER_BY = "domain_rating:desc"

#: Ключевое поле строки — бесплатно, как `date` у истории метрик.
KEY_FIELD = "domain"
#: Надбавка за саму строку поверх полей.
ROW_UNITS = 1
#: Цена поля в строке — замеренные. По единице — DR, флаги, даты и счётчики ссылок: так
#: сходятся замеры 4 и 8 юнитов на строку; трафик — 10. Счётчики `linkeddomains` — из его
#: замера: модель цены строки у списков Site Explorer одна.
FIELD_UNITS = {
    "domain_rating": 1,
    "is_dofollow": 1,
    "is_spam": 1,
    "first_seen": 1,
    "last_seen": 1,
    "links_to_target": 1,
    "dofollow_links": 1,
    "links_from_target": 1,
    "linked_pages": 1,
    "traffic_domain": 10,
}
#: Цена незнакомого поля: его не мерили, и дороже любого замеренного — нарочно.
UNKNOWN_FIELD_UNITS = 10


@dataclass(frozen=True, slots=True)
class RefdomainsFilter:
    """Какие ссылающиеся домены нужны — то, что Ahrefs проверит по строке до оплаты.

    Границы DR включительные, как у вердикта (`donors/verdict.check_dr`): ниже нижнего
    порога — «не подходит», выше границы крупного сайта — «кому не пишем».
    """

    min_dr: int
    max_dr: int
    #: Органический трафик сайта не ниже; пусто или ноль — без порога.
    min_traffic: int | None = None
    #: Хотя бы одна ссылка на цель без nofollow: такие площадки и ставят ссылки.
    dofollow: bool = True
    #: Без доменов, которые Ahrefs считает спамом.
    no_spam: bool = True

    def __post_init__(self) -> None:
        if self.min_dr > self.max_dr:
            raise ValueError(
                f"Нижний порог DR {self.min_dr} выше границы крупного сайта {self.max_dr}: "
                "список был бы пуст, а запрос всё равно стоит минимума"
            )

    def nodes(self) -> list[dict[str, Any]]:
        """Условия фильтра — в порядке, в каком уходят в запрос."""
        parts: list[dict[str, Any]] = []
        if self.dofollow:
            parts.append({"field": "is_dofollow", "is": ["eq", True]})
        if self.no_spam:
            parts.append({"field": "is_spam", "is": ["eq", False]})
        parts.append({"field": "domain_rating", "is": ["gte", self.min_dr]})
        parts.append({"field": "domain_rating", "is": ["lte", self.max_dr]})
        if self.min_traffic:
            parts.append({"field": "traffic_domain", "is": ["gte", self.min_traffic]})
        return parts

    def where(self) -> str:
        """Фильтр строкой запроса — JSON-деревом, как его читает Ahrefs: строку вида SQL
        он не принимает (замер соседней системы)."""
        return json.dumps({"and": self.nodes()}, ensure_ascii=False, separators=(",", ":"))

    @property
    def fields(self) -> frozenset[str]:
        """Поля, по которым фильтр судит, — их Ahrefs тоже считает в цене строки."""
        return frozenset(str(node["field"]) for node in self.nodes())


def row_units(select: Sequence[str], where_fields: Collection[str]) -> int:
    """Цена строки ответа: единица плюс каждое поле запроса один раз, ключевое — даром."""
    fields = (set(select) | set(where_fields)) - {KEY_FIELD}
    return ROW_UNITS + sum(FIELD_UNITS.get(field, UNKNOWN_FIELD_UNITS) for field in fields)


def list_units(rows: int, per_row: int) -> int:
    """Цена одного списка: строки по цене строки, но не меньше минимума на запрос. Смета
    кладёт минимум и пустому ответу: берут ли его там, не мерили, а смета — верхняя граница."""
    return max(MIN_REQUEST_UNITS, max(0, rows) * per_row)
