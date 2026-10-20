"""Домены конкурентов, которые вписал человек, — к одной строгой форме до первого юнита.

Строка поля уходит в Ahrefs целью запроса, и всё, что ушло, оплачено: список платформы — это
список «весь интернет», домен с подчёркиванием или пробелом Ahrefs не знает, а минимум за
запрос берёт и с пустого. Поэтому домен конкурента проверяется строже домена из выдачи:

* имя — по правилам DNS: метки из латиницы, цифр и дефиса, не с дефиса и не на дефис,
  до 63 знаков, имя целиком — до 253; национальное имя (`пример.рф`) — к ASCII-форме IDNA
  (`xn--e1afmkfd.xn--p1ai`): одна форма — один конкурент и один список;
* в строке — ни пробелов, ни невидимых знаков, ни `%`, `@` и обратной косой: адрес с ними
  значит не то, что видно (`example.com@other.example` ведёт на второй);
* корневой домен — по тому же списку суффиксов, что у выдачи (`donors/host.py`);
* платформы, соцсети, крупные СМИ, справочники и гос. зоны — отказ тем же правилом, что
  у гейта исключений (`runs/exclusions.by_name`): на них ссылаются все подряд, и их список —
  не площадки ниши.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence

import idna

from backend.features.donors.host import normalize_host
from backend.features.runs.exclusions import by_name
from backend.shared.net.url_parts import split_url

logger = logging.getLogger(__name__)

#: Конкурентов за прогон — не больше: каждый — свой список и до глубины новых сайтов
#: на метрики, и пять уже дают тысячи юнитов при средней глубине.
MAX_COMPETITORS = 5
#: Метка DNS: латиница, цифры и дефис внутри, до 63 знаков.
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
#: Имя целиком — не длиннее.
MAX_NAME = 253
#: Пробелы и невидимые знаки: мягкий перенос, нулевой ширины, направление письма, BOM.
_HIDDEN = re.compile(r"[\s\u00ad\u180e\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]")
#: Знаки в имени, с которыми адрес ведёт не туда, куда виден, или прячет буквы кодом;
#: обратная косая — и в пути: браузер читает её как прямую.
_MISLEADING = ("%", "@")
#: Пример в отказе — домен из RFC 2606: подсказка, а не чей-то сайт.
EXAMPLE = "example.com"
#: Чем имя не домен по правилам DNS — словами отказа.
NOT_DNS = (
    f"не домен по правилам имён: метки из букв, цифр и дефиса до 63 знаков, имя — до {MAX_NAME}"
)


def ascii_host(host: str) -> str:
    """Имя — в ASCII-форме IDNA, для сравнения; не переводится — как есть."""
    try:
        return idna.encode(host, uts46=True).decode("ascii")
    except idna.IDNAError:
        logger.debug("Имя %r не переводится в ASCII-форму IDNA — сравнивается как есть", host)
        return host


def _refusal(text: str, why: str) -> ValueError:
    return ValueError(f"«{text[:80]}» — {why}. Впишите домен конкурента, например {EXAMPLE}")


def _authority(text: str) -> str:
    """Часть адреса до пути: в пути `@` и `%` законны (`youtube.com/@канал`), в имени — нет."""
    rest = text.split("//", 1)[1] if "//" in text else text
    return re.split(r"[/?#]", rest, maxsplit=1)[0]


def _strict(text: str) -> str:
    """Один набранный домен — корневым доменом в ASCII-форме; отказ — словами."""
    if _HIDDEN.search(text):
        raise _refusal(text, "в нём пробел или невидимый знак")
    if "\\" in text or any(sign in _authority(text) for sign in _MISLEADING):
        raise _refusal(text, "в адресе знак, с которым он ведёт не туда, куда виден")
    split = split_url(text if "//" in text else f"//{text}")
    name = ((split.hostname if split else None) or "").rstrip(".")
    try:
        name = idna.encode(name, uts46=True).decode("ascii")
    except idna.IDNAError:
        raise _refusal(text, NOT_DNS) from None
    labels = name.split(".")
    if not name or len(name) > MAX_NAME or not all(_LABEL.fullmatch(part) for part in labels):
        raise _refusal(text, NOT_DNS)
    host = normalize_host(name)
    if not host:
        raise _refusal(text, "не домен")
    return host


def competitor_hosts(raw: Sequence[str]) -> list[str]:
    """Домены конкурентов — как их спросит Ahrefs: корневой домен в ASCII-форме, без
    протокола, `www` и пути, без повторов, в порядке ввода. Отказ — `ValueError` словами."""
    hosts: list[str] = []
    for typed in raw:
        text = typed.strip()
        if not text:
            continue
        host = _strict(text)
        if host not in hosts:
            hosts.append(host)
    if not hosts:
        raise ValueError("Конкурентов нет — одни пустые строки. Впишите домены конкурентов")
    if len(hosts) > MAX_COMPETITORS:
        raise ValueError(
            f"Конкурентов за прогон — не больше {MAX_COMPETITORS}, пришло {len(hosts)}. "
            "Разбейте их на несколько прогонов: так видна смета каждого"
        )
    refused = by_name(hosts)
    if refused:
        host, reason = next(iter(refused.items()))
        raise ValueError(
            f"«{host}» — {reason.caption}: на такие сайты ссылаются все подряд, и их список — "
            "не площадки ниши. Впишите сайт конкурента"
        )
    return hosts
