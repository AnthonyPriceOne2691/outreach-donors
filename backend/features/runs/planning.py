"""Что пойдёт в прогон и во что это станет — до первой траты.

Отдельно от исполнения намеренно. Здесь живут два отсева и смета,
и именно здесь ошибка стоит денег: домен, пропущенный сюда зря,
оплачивается по всем трём ступеням, а отсечённый зря — теряется молча.

    выдача → нормализация → дедупликация
           → гейт исключений (бесплатен)
           → отсев уже проверенных (бесплатен)
           → смета и проверка капа

**Кап проверяется до первого платного запроса.** Узнать о превышении
на середине прогона — значит уже потратить половину.

**Оба отсева идут до сметы.** Показывать в цене прогона домены, за
которые платить не придётся, было бы обманом — и кэш перестал бы
быть виден человеку.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from backend.features.ahrefs.units import RunEstimate, estimate_run
from backend.features.core.domain import Stage
from backend.features.donors.host import normalize_host
from backend.features.runs.budget import CapExceededError
from backend.features.runs.exclusions import ExclusionReason, counts
from backend.features.serp.protocol import SerpProvider


class FreshnessSource(Protocol):
    """Кто знает, по каким доменам данные ещё годны."""

    async def fresh_hosts(self, hosts: Sequence[str]) -> set[str]:
        """Домены с непросроченными метриками — за них уже заплачено."""
        ...


class ExclusionSource(Protocol):
    """Кто знает, каких доменов нам не надо вовсе.

    Отдельный протокол, а не метод предыдущего: свежесть спрашивают
    у доноров, исключения — у стоп-листа, поставщиков и писем. Сведя
    их в один источник, мы получили бы репозиторий, знающий про всё.
    """

    async def excluded_hosts(
        self, hosts: Sequence[str], *, stage: Stage = ..., now: datetime | None = ...
    ) -> dict[str, ExclusionReason]:
        """Домены, которые в прогон не идут, и почему по каждому."""
        ...


@dataclass(frozen=True, slots=True)
class Candidates:
    """Что дала выдача после нормализации и дедупликации."""

    hosts: list[str]
    keywords: int
    results: int
    empty_keywords: list[str]
    dropped: int
    """Строк выдачи, из которых не удалось получить домен."""

    cost_usd: float = 0.0
    """Во что обошлась эта выдача. Ноль у восстановленной: за неё уже
    заплачено и уже записано в журнал прошлой попыткой, а вторая строка
    расхода превратила бы продолжение прогона в удвоение счёта."""

    @property
    def duplicates(self) -> int:
        """Сколько адресов схлопнулось в уже известные домены. Это и есть
        экономия дедупликации: каждый схлопнутый — непотраченные юниты."""
        return self.results - self.dropped - len(self.hosts)

    def as_dict(self) -> dict[str, Any]:
        """Для хранения в строке прогона. Поля перечислены руками: молча
        уехавшее поле — это молча потерянная выдача, за которую платили."""
        return {
            "hosts": list(self.hosts),
            "keywords": self.keywords,
            "results": self.results,
            "empty_keywords": list(self.empty_keywords),
            "dropped": self.dropped,
            # Цена сохраняется ради отчёта, а не ради повторной записи:
            # `restored()` намеренно возвращает ноль.
            "cost_usd": self.cost_usd,
        }

    @classmethod
    def restored(cls, payload: dict[str, Any]) -> Candidates:
        """Выдача, сохранённая прошлой попыткой того же прогона.

        **Цена сознательно не восстанавливается.** Продолжение прогона
        ничего у провайдера не покупает, и строка расхода на ту же
        выдачу второй раз означала бы счёт вдвое больше настоящего.
        """
        return cls(
            hosts=list(payload["hosts"]),
            keywords=int(payload["keywords"]),
            results=int(payload["results"]),
            empty_keywords=list(payload.get("empty_keywords", ())),
            dropped=int(payload.get("dropped", 0)),
        )


@dataclass(frozen=True, slots=True)
class RunPlan:
    """Смета прогона до его запуска."""

    candidates: Candidates
    fresh: list[str]
    new: list[str]
    estimate: RunEstimate
    #: Домены, отсечённые гейтом, и причина по каждому. Они не входят
    #: ни в смету, ни в `fresh`: за них не платили и платить не будут.
    excluded: dict[str, ExclusionReason] = field(default_factory=dict)

    @property
    def considered(self) -> int:
        """Сколько доменов дошло до вопроса о цене — после гейта."""
        return len(self.fresh) + len(self.new)

    @property
    def savings_from_cache(self) -> int:
        """Во что обошёлся бы прогон, если бы срока годности не было."""
        return estimate_run(self.considered).total - self.estimate.total

    @property
    def savings_from_gate(self) -> int:
        """Во что обошлись бы домены, отсечённые гейтом.

        Считается отдельно от кэша намеренно. Сложив их, мы получили бы
        одно число «сэкономлено» и потеряли бы единственный способ
        увидеть, что стоп-лист работает: он экономит ровно столько.
        """
        return estimate_run(len(self.candidates.hosts)).total - estimate_run(self.considered).total

    @property
    def excluded_by_reason(self) -> dict[str, int]:
        return counts(self.excluded)


async def gather_candidates(
    provider: SerpProvider,
    keywords: Sequence[str],
    country: str,
    *,
    depth_pages: int = 1,
) -> Candidates:
    """Выдача по ключам → уникальные корневые домены.

    Порядок сохраняется: первым идёт домен, встреченный выше в выдаче.
    На отладке это удобнее случайного порядка множества.
    """
    before = getattr(provider, "spent", 0.0)
    answer = await provider.search(keywords, country, depth_pages=depth_pages)
    # Цена берётся разницей, а не полем: один адаптер живёт дольше одного
    # прогона, и его накопленный расход — это расход всех прогонов сразу.
    cost = max(0.0, getattr(provider, "spent", 0.0) - before)

    seen: dict[str, None] = {}
    results = 0
    dropped = 0
    empty: list[str] = []

    for keyword, rows in answer.items():
        if not rows:
            empty.append(keyword)
        for row in rows:
            results += 1
            host = normalize_host(row.url)
            if not host:
                dropped += 1
                continue
            seen.setdefault(host, None)

    return Candidates(
        hosts=list(seen),
        keywords=len(answer),
        results=results,
        empty_keywords=empty,
        dropped=dropped,
        cost_usd=cost,
    )


async def plan_run(
    candidates: Candidates,
    freshness: FreshnessSource,
    *,
    units_left: int,
    exclusions: ExclusionSource | None = None,
    stage: Stage = Stage.DONORS,
) -> RunPlan:
    """Смета и проверка капа. Бросает `CapExceededError`, если не помещаемся.

    Два отсева, и оба до сметы. Сначала гейт: домен из стоп-листа,
    поставщик агентства и тот, кто промолчал на письмо, до провайдера
    не доходят вовсе. Потом свежесть: за оставшихся уже заплачено,
    и показывать их в цене прогона было бы обманом.

    Порядок именно такой, потому что гейт бесплатен, а его ответ
    окончателен: спрашивать свежесть у домена, которому мы всё равно
    не напишем, незачем.

    Источник исключений необязателен: без него остаётся прежнее
    поведение. Умолчание здесь — уступка вызывающим из тестов,
    а не режим работы; оба боевых вызова передают его всегда.
    """
    excluded: dict[str, ExclusionReason] = {}
    if exclusions is not None:
        excluded = await exclusions.excluded_hosts(candidates.hosts, stage=stage)

    wanted = [h for h in candidates.hosts if h not in excluded]
    fresh = await freshness.fresh_hosts(wanted)
    new = [h for h in wanted if h not in fresh]
    estimate = estimate_run(len(new))

    if estimate.total > units_left:
        raise CapExceededError(
            f"Прогон обойдётся в {estimate.total} юнитов, доступно {units_left}. "
            f"Новых доменов {len(new)} из {len(candidates.hosts)}; "
            f"сократите список ключей или поднимите кап."
        )

    return RunPlan(
        candidates=candidates,
        fresh=sorted(fresh),
        new=new,
        estimate=estimate,
        excluded=excluded,
    )
