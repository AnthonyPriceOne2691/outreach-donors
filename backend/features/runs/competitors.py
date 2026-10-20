"""Кандидаты из ссылок конкурентов — третий вход прогона рядом со своими ключами и ключами
модели.

Слова оператора: «указываем сайты конкурентов, и оно парсит их ссылочную массу, отбирает
хорошие площадки». Сайт, который ссылается на конкурента, уже ставит ссылки сайтам этой
ниши — ровно тот, кому мы пишем. Путь:

    конкуренты → списки ссылающихся доменов Ahrefs (пороги DR и трафика — у Ahrefs)
               → нормализация и повторы → без самих конкурентов и уже принятых доноров
               → та же воронка, что у ключей: гейт исключений, свежесть, судья,
                 метрики и «Гео», пороги, рассмотрение человеком

Воронка не знает, откуда кандидаты: они приходят готовыми (`RunRequest.candidates`), как
выдача, сохранённая прошлой попыткой. Ключей у такого прогона нет, и тему письма, которую
у выдачи несут ключи (`found_by`), он не придумывает: кто на кого ссылается, лежит в журнале
отдельно (`linked_to`).

**Что выброшено и почему — числом в журнале прогона** (`Candidates.source`): строки без
домена, сами конкуренты, уже принятые доноры, DR вне порогов (страховка: Ahrefs отсёк их сам)
и повторы — поддомен того же сайта или сайт, ссылающийся на нескольких конкурентов. Отсев
гейта и свежести пишет воронка, как у выдачи.

Деньги и бюджет — в `runs/competitor_run.py`: здесь только что спросить и как разобрать.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.ahrefs import refdomains as links
from backend.features.ahrefs.client import AhrefsClient, AhrefsError
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.donors.collect import parse_domain_rating
from backend.features.donors.host import normalize_host
from backend.features.donors.standing import is_donor
from backend.features.donors.verdict import Thresholds
from backend.features.runs.competitor_hosts import ascii_host
from backend.features.runs.planning import Candidates

logger = logging.getLogger(__name__)

#: Глубина — сколько ссылающихся доменов взять у одного конкурента, сильнейшие первыми.
MIN_DEPTH = 10
MAX_DEPTH = links.MAX_ROWS
DEFAULT_DEPTH = 100
#: Метка журнала: кандидаты прогона — из ссылок конкурентов, а не из выдачи.
SOURCE_KIND = "competitors"


#: Отказ Ahrefs на запрос одного списка: после хотя бы одного полученного — пропуск этого
#: конкурента с причиной в журнале, а не остановка прогона с оплаченными списками на руках.
#: Ключ и доступ (401, 402, 403) останавливают всегда: они про все списки сразу.
SKIPPED_STATUSES = frozenset({400, 404, 422})


class CompetitorLinksError(RuntimeError):
    """Список конкурента не получен. `permanent` — от отказа Ahrefs: ключ, права, запрос —
    повтор не поможет; сеть, 5xx и частота — прогон продолжат (`runs/failures.py`).
    `status` — код ответа Ahrefs; `None` — кода не было."""

    def __init__(self, message: str, *, permanent: bool, status: int | None = None) -> None:
        super().__init__(message)
        self.permanent = permanent
        self.status = status


def links_filter(thresholds: Thresholds) -> links.RefdomainsFilter:
    """Фильтр списка — порогами версии настроек прогона: DR и органический трафик."""
    return links.RefdomainsFilter(
        min_dr=thresholds.min_dr,
        max_dr=thresholds.max_dr,
        min_traffic=thresholds.min_org_traffic or None,
    )


def row_price(thresholds: Thresholds) -> int:
    """Цена строки списка при этих порогах — моделью `ahrefs/refdomains.row_units`."""
    return links.row_units(links.SELECT, links_filter(thresholds).fields)


@dataclass(frozen=True, slots=True)
class Ask:
    """Что прогон спрашивает у Ahrefs: конкуренты и глубина — строка прогона `competitors`."""

    targets: tuple[str, ...]
    depth: int

    @property
    def rows_max(self) -> int:
        """Сколько строк придёт в худшем случае — каждый список до глубины."""
        return len(self.targets) * self.depth

    def as_dict(self) -> dict[str, Any]:
        return {"targets": list(self.targets), "depth": self.depth}

    @classmethod
    def of(cls, stored: Mapping[str, Any] | None) -> Ask | None:
        """Из строки прогона; пусто — прогон по ключам."""
        if not stored:
            return None
        return cls(targets=tuple(stored["targets"]), depth=int(stored["depth"]))


@dataclass(frozen=True, slots=True)
class Fetched:
    """Список одного конкурента, как его отдал Ahrefs: домен и DR строкой, во что обошёлся
    и цена строки по заголовку ответа (`UnitsCost.per_row`; `None` — Ahrefs не назвал)."""

    target: str
    rows: list[tuple[str, float | None]]
    units: int
    per_row: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "rows": [[domain, dr] for domain, dr in self.rows],
            "units": self.units,
            "per_row": self.per_row,
        }

    @classmethod
    def restored(cls, target: str, payload: Mapping[str, Any]) -> Fetched:
        rows = [(str(domain), None if dr is None else float(dr)) for domain, dr in payload["rows"]]
        return cls(target, rows, int(payload["units"]), payload.get("per_row"))


def refused(target: str, exc: AhrefsError) -> CompetitorLinksError:
    """Отказ Ahrefs на список — словами, что делать: по коду ответа, с текстом провайдера."""
    status = exc.status
    if status == 401:
        what = "Ahrefs не принял ключ — проверьте ключ API в настройках сервиса"
    elif status in {402, 403}:
        what = (
            "у ключа Ahrefs нет доступа к спискам ссылающихся доменов или кончилась оплата — "
            "это решается в самом Ahrefs"
        )
    elif status in SKIPPED_STATUSES:
        what = (
            f"Ahrefs не принял запрос списка (код {status}): сменились поля, фильтр или адрес "
            "метода, или цель ему не подходит — повтор не поможет"
        )
    elif status == 429:
        what = "Ahrefs ограничил частоту запросов или юниты ключа кончились — прогон продолжат"
    elif status is not None and status >= 500:
        what = "Ahrefs не ответил — прогон продолжат с этого конкурента"
    elif status is not None:
        # 3xx, 405, 409, 410 и прочие: адрес или метод у Ahrefs сменились — код называется.
        what = f"Ahrefs ответил кодом {status} — повтор не поможет, нужна правка запроса"
    else:
        what = "ответ Ahrefs не получен или не разобран"
    return CompetitorLinksError(
        f"Ссылки конкурента {target} не получены: {what}. Ahrefs: {exc}",
        permanent=exc.permanent,
        status=status,
    )


async def fetch_list(
    client: AhrefsClient, target: str, *, depth: int, where: links.RefdomainsFilter
) -> Fetched:
    """Список одного конкурента. Расход сообщает клиент (`on_usage`) — записывает вызывающий."""
    try:
        response = await client.refdomains(target, where=where, limit=depth)
    except AhrefsError as exc:
        raise refused(target, exc) from exc
    rows = [(str(row.get("domain") or ""), parse_domain_rating(row)) for row in response.rows]
    logger.info(
        "Ссылки конкурента %s: строк %s, юнитов %s (строка %s)",
        target,
        len(rows),
        response.cost.billable,
        response.cost.per_row,
    )
    return Fetched(target, rows, response.cost.billable, response.cost.per_row)


class DropReason(StrEnum):
    """Почему строка списка не стала кандидатом. Оператор читает подпись."""

    UNPARSED = "unparsed"
    COMPETITOR = "competitor"
    DR_OUTSIDE = "dr_outside"
    REPEAT = "repeat"
    DONOR = "donor"

    @property
    def caption(self) -> str:
        return _DROP_CAPTIONS[self]


_DROP_CAPTIONS = {
    DropReason.UNPARSED: "не домен",
    DropReason.COMPETITOR: "сам конкурент",
    DropReason.DR_OUTSIDE: "DR вне порогов",
    DropReason.REPEAT: "повтор: поддомен или ссылается на нескольких конкурентов",
    DropReason.DONOR: "уже донор",
}


@dataclass(slots=True)
class Journal:
    """Что списки дали прогону: кандидаты по порядку, кто на кого из конкурентов ссылается,
    сколько нового принёс каждый список и что выброшено — числом по причине."""

    hosts: list[str] = field(default_factory=list)
    linked_to: dict[str, list[str]] = field(default_factory=dict)
    kept: dict[str, int] = field(default_factory=dict)
    dropped: dict[str, int] = field(default_factory=dict)
    rows: int = 0

    def drop(self, reason: DropReason, count: int = 1) -> None:
        if count:
            self.dropped[reason.value] = self.dropped.get(reason.value, 0) + count

    def keep(self, host: str, target: str) -> None:
        """Строка списка — кандидат или повтор уже взятого."""
        targets = self.linked_to.get(host)
        if targets is None:
            self.linked_to[host] = [target]
            self.hosts.append(host)
            self.kept[target] = self.kept.get(target, 0) + 1
            return
        if target not in targets:
            targets.append(target)
        self.drop(DropReason.REPEAT)

    def without(self, donors: Collection[str]) -> None:
        """Уже принятые доноры — не кандидаты: их нашли раньше, и смотреть их второй раз
        и тем более докупать им метрики незачем."""
        known = [host for host in self.hosts if host in donors]
        for host in known:
            # Новое у списка — кандидаты: донор, которого он принёс первым, из счёта уходит.
            first = self.linked_to.pop(host)[0]
            self.kept[first] -= 1
        self.hosts = [host for host in self.hosts if host not in donors]
        self.drop(DropReason.DONOR, len(known))


def _drop_reason(
    host: str, dr: float | None, rivals: Collection[str], thresholds: Thresholds
) -> DropReason | None:
    if not host:
        return DropReason.UNPARSED
    if host in rivals:
        return DropReason.COMPETITOR
    if dr is not None and not thresholds.min_dr <= dr <= thresholds.max_dr:
        return DropReason.DR_OUTSIDE
    return None


def collapse(ask: Ask, lists: Sequence[Fetched], thresholds: Thresholds) -> Journal:
    """Списки конкурентов — в один список кандидатов: корневой домен, как у выдачи
    (`donors/host.normalize_host`), в ASCII-форме IDNA, первый встреченный — первым, без
    самих конкурентов и без DR вне порогов. Каждая строка либо кандидат, либо число
    в `dropped`."""
    rivals = set(ask.targets)
    journal = Journal(kept=dict.fromkeys(ask.targets, 0))
    for fetched in lists:
        for domain, dr in fetched.rows:
            journal.rows += 1
            # Одна форма имени — ASCII IDNA, как у конкурентов (`competitor_hosts`): сайт,
            # пришедший национальным именем и его ASCII-формой, — один кандидат.
            host = normalize_host(domain)
            host = ascii_host(host) if host else host
            reason = _drop_reason(host, dr, rivals, thresholds)
            if reason is None:
                journal.keep(host, fetched.target)
            else:
                journal.drop(reason)
    return journal


async def known_donors(session: AsyncSession, hosts: Sequence[str]) -> set[str]:
    """Какие из доменов уже доноры — приняты человеком (`donors/standing.is_donor`)."""
    if not hosts:
        return set()
    rows = await session.scalars(
        select(DomainModel.host)
        .join(DonorModel, DonorModel.domain_id == DomainModel.id)
        .where(DomainModel.host.in_(hosts), is_donor())
    )
    return set(rows.all())


def candidates_of(
    ask: Ask,
    lists: Sequence[Fetched],
    journal: Journal,
    *,
    row_units: int,
    skipped: Mapping[str, str] | None = None,
) -> Candidates:
    """Кандидаты воронки с журналом списков. `results` — строки списков, `dropped` — строки
    без домена: так у выдачи, и отчёт прогона читает их одинаково. `skipped` — конкуренты,
    чей список Ahrefs не принял, и почему (`SKIPPED_STATUSES`)."""
    return Candidates(
        hosts=list(journal.hosts),
        keywords=0,
        results=journal.rows,
        empty_keywords=[],
        dropped=journal.dropped.get(DropReason.UNPARSED.value, 0),
        source={
            "kind": SOURCE_KIND,
            "depth": ask.depth,
            # Цена строки по модели сметы — рядом с ценой из ответа (`per_row`): расхождение
            # видно в журнале прогона.
            "row_units": row_units,
            "targets": [
                {
                    "target": fetched.target,
                    "rows": len(fetched.rows),
                    "units": fetched.units,
                    "per_row": fetched.per_row,
                    "kept": journal.kept.get(fetched.target, 0),
                }
                for fetched in lists
            ],
            "dropped": dict(journal.dropped),
            "skipped": [
                {"target": target, "reason": reason} for target, reason in (skipped or {}).items()
            ],
            "linked_to": {host: list(targets) for host, targets in journal.linked_to.items()},
        },
    )
