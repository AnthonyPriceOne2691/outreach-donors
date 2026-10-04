"""Главная по индексу поиска — когда сам сайт закрылся.

**Зачем.** Судья смотрит главную как вторую сторону, но у каждого десятого
домена она закрыта защитой от ботов: «Just a moment», «DDoS-Guard»,
«Human Verification». Настоящий браузер не помогает — 23.09 он открыл
5 из 16 закрытых главных и ни одной из тех, на которых судья ошибся.
Все четыре ошибки пяти рынков — магазины и обменники за закрытой главной,
принятые по статье из выдачи.

**Индекс видит их всё равно.** Поисковик обошёл сайт раньше нас, и запрос
`site:домен` отдаёт заголовок и описание корня и других страниц:
«P2P-exchange Bitpapa: Buy Bitcoin», каталог с ценами «Rp 229.000». Это
тот же довод, по которому судья вообще читает сниппет, а не сайт.

**Структуры в индексе нет** — ни корзины, ни разметки. Поэтому по правилу
доказательства отказ арбитра по индексу идёт человеку, а не в отказ:
закрытая главная превращает ложный приём в «посмотри», и только.
"""

from __future__ import annotations

from collections.abc import Sequence

from backend.features.donors.home_signals import HomeSignals
from backend.features.serp.protocol import SerpProvider, SerpResult
from backend.shared.net.url_parts import parse_url

#: Сколько заголовков других страниц сайта уходит арбитру вместо меню.
MAX_PAGES = 8

#: Страна запроса `site:` роли почти не играет: спрашиваем про один домен.
INDEX_COUNTRY = "us"


def _readable(rows: Sequence[SerpResult]) -> list[SerpResult]:
    """Строки выдачи с разбираемым адресом. Битый адрес — не страница сайта:
    ни корнем, ни заголовком для арбитра он не годится (ревью #148)."""
    return [row for row in rows if parse_url(row.url) is not None]


def _is_home(url: str) -> bool:
    """Корень сайта — адрес без пути. Битый адрес из выдачи корнем не считается."""
    parsed = parse_url(url)
    return parsed is not None and not parsed.path.strip("/")


async def index_homes(
    provider: SerpProvider, hosts: Sequence[str]
) -> tuple[dict[str, HomeSignals], float]:
    """Образы главных по индексу одним пакетом. Возвращает и цену запроса."""
    if not hosts:
        return {}, 0.0
    before = getattr(provider, "spent", 0.0)
    answers = await provider.search([f"site:{host}" for host in hosts], INDEX_COUNTRY)
    found: dict[str, HomeSignals] = {}
    for host in hosts:
        rows = _readable(answers.get(f"site:{host}", []))
        if not rows:
            continue
        root = next((row for row in rows if _is_home(row.url)), None)
        first = root or rows[0]
        pages = tuple(
            (row.title or "").strip()
            for row in rows
            if row is not first and (row.title or "").strip()
        )[:MAX_PAGES]
        found[host] = HomeSignals(
            reached=True,
            title=(first.title or "").strip(),
            description=(first.description or "").strip(),
            nav=pages,
            via="index",
        )
    return found, getattr(provider, "spent", 0.0) - before
