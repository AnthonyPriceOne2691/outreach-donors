"""Обход 24/7: рубильник и сколько его частей идёт разом (`crawl/always.py`, 17.11.2026).

Свой модуль, а не `config/crawl.py`: обход 24/7 читают проход выдачи, обходчик и экран «Обход
доноров», и опечатка здесь не должна ронять ни один из них (`_base.spared`).

**Рубильник, а не включатель** — как `ADVERTISERS_AUTO_ROUND`. Включён по умолчанию, но сам обход
24/7 этим не начинается: его запускает человек кнопкой на вкладке «Обход доноров». Выключенный
рубильник — новых частей нет, идущие останавливаются на границе страницы и помнят, откуда
продолжить, а кнопок на экране нет.

**Сколько частей разом** — 12 из 16 обходчиков: остальные — обходам, поставленным людьми, очереди
загрузки и глубоким обходам. Части идут только в свободные обходчики и только когда очередь
обходов пуста (`always.tick`): обход, поставленный человеком, не ждёт за ними.

**Сколько ссылок цепочки судья держит в памяти** (`CRAWL_ALWAYS_UNION_LINKS`, `gate_union.py`):
кандидаты донора считаются по последнему обходу и частям цепочки, новые первыми, пока ссылок не больше
потолка; находки старых частей заморожены. 600 000 ссылок — около полугигабайта у задачи пересчёта
с потолком памяти в полтора: на проде страница несёт около пяти ссылок (p90 — 10–13), а глубокий
обход нёс 307 тысяч на 10 000 страниц.

**Передышка сайту после его обычного обхода** (`CRAWL_ALWAYS_REST_MIN`, `always.plan`, 19.11.2026):
часть 24/7 не начинается на сайте, пока с конца его обычного обхода — ночного или поставленного
человеком — не прошло столько минут. Утром 19.11 все шесть частей, вставших по доле отказов,
начались в 07:03–07:36 на сайтах, которые ночной обход только что прошёл: сайт отсекает вторую
нагрузку подряд. 0 — без передышки.

Опечатка в значении (`CRAWL_ALWAYS=да`) не роняет процессы, которые грузят настройку: берётся
«выключен» (или умолчание числа), и при старте — громкая строка журнала с именем переменной.
"""

from __future__ import annotations

from pydantic import Field

from backend.config._base import DomainSettings, Fallback, spared

#: Частей 24/7 разом и ссылок цепочки в памяти судьи, пока не задано иначе.
DEFAULT_SLOTS = 12
DEFAULT_UNION_LINKS = 600_000
#: Минут передышки сайту после его обычного обхода, пока не задано иначе.
DEFAULT_REST_MIN = 120


class _Always(DomainSettings):
    enabled: bool = Field(default=True, validation_alias="CRAWL_ALWAYS")
    slots: int = Field(default=DEFAULT_SLOTS, gt=0, validation_alias="CRAWL_ALWAYS_SLOTS")
    union_links: int = Field(
        default=DEFAULT_UNION_LINKS, gt=0, validation_alias="CRAWL_ALWAYS_UNION_LINKS"
    )
    rest_min: int = Field(default=DEFAULT_REST_MIN, ge=0, validation_alias="CRAWL_ALWAYS_REST_MIN")


_s, _unread = spared(
    _Always,
    {
        "CRAWL_ALWAYS": Fallback(
            False,
            wants="true или false",
            means="обход 24/7 не идёт, пока настройку не поправят",
        ),
        "CRAWL_ALWAYS_SLOTS": Fallback(
            DEFAULT_SLOTS,
            wants="целое число больше нуля",
            means=f"не больше {DEFAULT_SLOTS} частей обхода 24/7 разом",
        ),
        "CRAWL_ALWAYS_UNION_LINKS": Fallback(
            DEFAULT_UNION_LINKS,
            wants="целое число больше нуля",
            means=f"судья держит не больше {DEFAULT_UNION_LINKS} ссылок цепочки донора",
        ),
        "CRAWL_ALWAYS_REST_MIN": Fallback(
            DEFAULT_REST_MIN,
            wants="целое число от нуля",
            means=f"часть 24/7 ждёт {DEFAULT_REST_MIN} минут после обычного обхода сайта",
        ),
    },
)

ENABLED: bool = _s.enabled
SLOTS: int = _s.slots
UNION_LINKS: int = _s.union_links
REST_MIN: int = _s.rest_min
