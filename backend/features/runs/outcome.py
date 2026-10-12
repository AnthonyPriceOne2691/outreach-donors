"""Отчёт прогона на экране: сколько проверено, сколько прошло, по какому порогу
отсеяно и во что обошлось.

Требование ТЗ, одно для всех вариантов сервиса: «после прогона — отчёт: сколько
доменов проверено, сколько прошло фильтр, сколько отсеяно и по какому порогу,
сколько потрачено юнитов и денег». Отчёт записывался (`report.run_stats`), а экран
показывал из него только домены выдачи, исключённые, смету и факт (сверка ТЗ
11.10.2026).

**Числа доменов — из отчёта прогона, расход — из журнала расхода.** Отчёт пишется
в конце попытки и знает вердикты. Журнал пишется на каждом чекпоинте и знает всё,
что прогон потратил: выдачу в деньгах и токены судьи, которых в отчёте нет, и траты
прежних попыток, которые отчёт следующей попытки затирает. Расход из отчёта показал
бы идущему прогону ноль, а продолженному — одну последнюю попытку.

**Порог — по первому слову причины.** Отчёт хранит причину отказа её первым словом
(`RunReport.record`): «DR», «органический», «ключей», «реф.», а у региона — код
страны прогона. Перевод в порог — здесь, одним местом. Связь со словами вердикта
держит тест (`tests/test_run_outcome.py`): поменяют слова в `donors/verdict.py` или
`donors/geo.py` — покраснеет он, а не экран тихо назовёт отсев по DR «другой причиной».
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config.judge import JudgeMode
from backend.features.core.domain import DonorStatus, Stage
from backend.features.core.models.run import RunSettingsModel
from backend.features.runs.browse import RunBrowser, RunRow
from backend.features.runs.repository import RESUMES_KEY, RunRepository
from backend.features.runs.spending import Article, SpendingRepository


class Threshold(StrEnum):
    """Порог, по которому домен отсеян. Значения уходят на экран кодом, слова — там."""

    DR = "dr"
    TRAFFIC = "org_traffic"
    KEYWORDS = "keywords"
    REFDOMAINS = "refdomains"
    GEO = "geo"
    #: Причина, которой нет среди порогов: записана до сегодняшних слов вердикта или
    #: чем-то новым. Показывается как записана — не пропадает и не подменяется.
    OTHER = "other"


#: Первое слово причины отказа (`donors/verdict.py`) → порог. Регион — кодом страны.
FIRST_WORDS: dict[str, Threshold] = {
    "DR": Threshold.DR,
    "органический": Threshold.TRAFFIC,
    "ключей": Threshold.KEYWORDS,
    "реф.": Threshold.REFDOMAINS,
}

#: Пороги на экране — в порядке проверок (`donors/collect.py`): DR первой ступенью,
#: трафик, ключи и реф. домены второй, регион последним.
ORDER: tuple[Threshold, ...] = (
    Threshold.DR,
    Threshold.TRAFFIC,
    Threshold.KEYWORDS,
    Threshold.REFDOMAINS,
    Threshold.GEO,
)


def threshold_of(word: str, country: str) -> Threshold:
    """Порог по первому слову причины. Регион вердикт пишет кодом страны прогона:
    «US не входит в топ-5…» (`donors/geo.check_geo`)."""
    if word in FIRST_WORDS:
        return FIRST_WORDS[word]
    if country and word.upper() == country.upper():
        return Threshold.GEO
    return Threshold.OTHER


@dataclass(frozen=True, slots=True)
class Rejection:
    """Сколько доменов отсеял порог."""

    threshold: Threshold
    count: int
    #: Причина, как записана в отчёте, — только у `OTHER`: её экран показывает как есть.
    said: str | None = None


def rejections(reasons: Mapping[str, Any], country: str) -> list[Rejection]:
    """Отсев по каждому порогу — все пять, и с нулём: «реф. домены не отсеяли никого»
    тоже ответ на «по какому порогу». Незнакомая причина — своей строкой, а не в сумму:
    сложенные вместе, они спрятали бы, что слова вердикта разошлись с экраном."""
    counted = dict.fromkeys(ORDER, 0)
    other: list[Rejection] = []
    for word, count in reasons.items():
        threshold = threshold_of(str(word), country)
        if threshold is Threshold.OTHER:
            other.append(Rejection(threshold, _int(count), said=str(word)))
        else:
            counted[threshold] += _int(count)
    return [Rejection(threshold, count) for threshold, count in counted.items()] + other


@dataclass(frozen=True, slots=True)
class Domains:
    """Домены прогона по шагам: из выдачи — в проверку Ahrefs — в вердикт."""

    #: Уникальных доменов выдачи. `None` — отчёт записан до этого числа.
    found: int | None
    #: Отсёк гейт исключений: стоп-лист, свои, уже отклонённые.
    excluded: int
    #: Метрики в сроке — проверены раньше, платить не пришлось.
    fresh: int
    #: Новые — те, за которые прогон собирался платить Ahrefs.
    to_check: int
    suitable: int
    unsuitable: int
    unchecked: int
    #: Судья режет (`JudgeMode.ENFORCE`): отрезанные им до Ahrefs не доходят.
    judge_enforced: bool
    #: Сколько раз прогон продолжали после сбоя. Числа доменов — последней попытки:
    #: проверенное до сбоя она застала свежим, и оно — в `fresh`.
    resumes: int

    @property
    def checked(self) -> int:
        """Сколько прошло проверку Ahrefs — с любым вердиктом."""
        return self.suitable + self.unsuitable + self.unchecked

    @property
    def not_reached(self) -> int:
        """Новых, до Ahrefs не дошедших: отрезал судья или прогон остановился раньше."""
        return max(0, self.to_check - self.checked)


def domains_of(stats: Mapping[str, Any]) -> Domains | None:
    """Числа доменов из отчёта. `None` — итога нет: прогон в очереди, считает смету или
    идёт первой попыткой (отчёт пишется в конце попытки), либо остановлен до сметы."""
    if "checked_now" not in stats:
        return None
    by_status = stats.get("by_status") or {}
    judge = stats.get("judge") or {}
    return Domains(
        found=None if stats.get("unique_hosts") is None else _int(stats.get("unique_hosts")),
        excluded=_int(stats.get("excluded")),
        fresh=_int(stats.get("already_fresh")),
        to_check=_int(stats.get("checked_now")),
        suitable=_int(by_status.get(DonorStatus.SUITABLE.value)),
        unsuitable=_int(by_status.get(DonorStatus.UNSUITABLE.value)),
        unchecked=_int(by_status.get(DonorStatus.UNCHECKED.value)),
        judge_enforced=judge.get("mode") == JudgeMode.ENFORCE.value,
        resumes=_int(stats.get(RESUMES_KEY)),
    )


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """Отчёт прогона целиком: строка истории, пороги, домены, отсев и расход."""

    row: RunRow
    #: Пороги, с которыми прогон запущен: отсев называется вместе с ними.
    settings: RunSettingsModel
    domains: Domains | None
    rejected: list[Rejection]
    #: Расход по журналу — всё, что записано за этим прогоном.
    spending: list[Article]
    #: Сколько запросов к Ahrefs обслужил кэш провайдера — бесплатно.
    free_requests: int


async def outcome(session: AsyncSession, run_id: int, *, stages: Collection[Stage]) -> RunOutcome:
    """Отчёт прогона. Нет прогона — `UnknownRunError` (`runs/browse.py`)."""
    row = await RunBrowser(session).one(run_id)
    stats: Mapping[str, Any] = row.run.stats or {}
    domains = domains_of(stats)
    reasons = stats.get("reject_reasons") or {}
    return RunOutcome(
        row=row,
        settings=await RunRepository(session).settings_of(row.run),
        domains=domains,
        rejected=[] if domains is None else rejections(reasons, row.run.country),
        spending=await SpendingRepository(session).of_run(run_id, stages=stages),
        free_requests=sum(_int(count) for count in (stats.get("free_requests") or {}).values()),
    )


def _int(value: object) -> int:
    """Число из отчёта. Отчёт — JSON прошлых версий: чего нет — ноль, а не падение."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return int(value)
