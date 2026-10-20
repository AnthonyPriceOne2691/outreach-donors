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

**Порог — по ключу причины** (`runs/refusals.py`): «DR», «DR > 80», «органический»,
«ключей», «реф.», у региона — код страны прогона. Связь со словами вердикта держит тест
(`tests/test_run_outcome.py`): поменяют слова в `donors/verdict.py` или `donors/geo.py` —
покраснеет он, а не экран тихо назовёт отсев по DR «другой причиной».

**Каждая причина «не предложен» — словами** (аудит 20.10.2026): крупный сайт — «кому не
пишем», а не «другая причина»; исключённые гейтом — по причине, а не одним числом;
«не проверен» — почему. Свежие домены прогон судит сам (`runs/verdicts.py`), и их
вердикты стоят в тех же числах — по ним прогон и собирал очередь.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config.judge import JudgeMode
from backend.features.core.domain import DonorStatus, Stage
from backend.features.core.models.run import RunSettingsModel
from backend.features.runs.browse import RunBrowser, RunRow
from backend.features.runs.exclusions import NOT_WRITING_WORDS, ExclusionReason
from backend.features.runs.refusals import ORDER, Threshold, threshold_of
from backend.features.runs.report import FRESH_KEY
from backend.features.runs.repository import RESUMES_KEY, RunRepository
from backend.features.runs.spending import Article, SpendingRepository

#: Причина исключения гейтом по её подписи в отчёте (`exclusions.counts`).
_BY_CAPTION: dict[str, ExclusionReason] = {reason.caption: reason for reason in ExclusionReason}


@dataclass(frozen=True, slots=True)
class Rejection:
    """Сколько доменов отсеял порог."""

    threshold: Threshold
    count: int
    #: Причина, как записана в отчёте, — только у `OTHER`: её экран показывает как есть.
    said: str | None = None


def rejections(reasons: Mapping[str, Any], country: str) -> list[Rejection]:
    """Отсев по каждому порогу — все, и с нулём: «реф. домены не отсеяли никого»
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
class Named:
    """Сколько доменов не предложено по причине, названной словами."""

    said: str
    count: int
    #: «Кому не пишем» ТЗ: взять такой домен нельзя по правилу, а не по числам.
    not_writing: bool = False


def exclusion_words(reason: ExclusionReason) -> str:
    """Причина гейта словами отчёта: «кому не пишем» — так и сказано."""
    if reason in NOT_WRITING_WORDS:
        return f"Кому не пишем: {reason.caption}"
    return reason.caption[:1].upper() + reason.caption[1:]


def excluded_named(stats: Mapping[str, Any]) -> list[Named]:
    """Исключённые гейтом — по причине (`excluded_by_reason` — подписью). Незнакомая
    подпись — как записана: причину не теряем."""
    named: list[Named] = []
    for caption, count in (stats.get("excluded_by_reason") or {}).items():
        reason = _BY_CAPTION.get(str(caption))
        if reason is None:
            named.append(Named(str(caption), _int(count)))
        else:
            named.append(
                Named(exclusion_words(reason), _int(count), not_writing=reason in NOT_WRITING_WORDS)
            )
    return sorted(named, key=lambda one: (not one.not_writing, -one.count, one.said))


def unchecked_named(stats: Mapping[str, Any]) -> list[Named]:
    """«Не проверен» — почему, словами вердикта: купленные сейчас и свежие вместе."""
    tally: dict[str, int] = {}
    for reasons in _verdict_parts(stats, "unchecked_reasons"):
        for reason, count in reasons.items():
            tally[str(reason)] = tally.get(str(reason), 0) + _int(count)
    return [
        Named(f"Не проверен: {reason}", count)
        for reason, count in sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def _verdict_parts(stats: Mapping[str, Any], key: str) -> Iterable[Mapping[str, Any]]:
    """Счёт вердиктов купленных сейчас и свежих (`FRESH_KEY`) — по одному ключу."""
    yield stats.get(key) or {}
    fresh = stats.get(FRESH_KEY)
    if isinstance(fresh, Mapping):
        yield fresh.get(key) or {}


def _summed(stats: Mapping[str, Any], key: str) -> dict[str, int]:
    tally: dict[str, int] = {}
    for part in _verdict_parts(stats, key):
        for word, count in part.items():
            tally[str(word)] = tally.get(str(word), 0) + _int(count)
    return tally


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
    #: Сколько новых прошло проверку Ahrefs — с любым вердиктом.
    checked: int
    #: Вердикты прогона: купленные сейчас и — если прогон их судил (`fresh_judged`) —
    #: свежие, по сохранённым метрикам. По ним прогон собирал очередь.
    suitable: int
    unsuitable: int
    unchecked: int
    #: Судья режет (`JudgeMode.ENFORCE`): отрезанные им до Ahrefs не доходят.
    judge_enforced: bool
    #: Сколько раз прогон продолжали после сбоя. Числа доменов — последней попытки:
    #: проверенное до сбоя она застала свежим, и оно — в `fresh`.
    resumes: int
    #: Свежие судил сам прогон (с 20.10.2026): их вердикты — в числах выше. Прогон
    #: раньше брал им вердикт последнего замера и в отчёт их не считал.
    fresh_judged: bool = False

    @property
    def not_reached(self) -> int:
        """Новых, до Ahrefs не дошедших: отрезал судья или прогон остановился раньше."""
        return max(0, self.to_check - self.checked)


def domains_of(stats: Mapping[str, Any]) -> Domains | None:
    """Числа доменов из отчёта. `None` — итога нет: прогон в очереди, считает смету или
    идёт первой попыткой (отчёт пишется в конце попытки), либо остановлен до сметы."""
    if "checked_now" not in stats:
        return None
    measured = stats.get("by_status") or {}
    by_status = _summed(stats, "by_status")
    judge = stats.get("judge") or {}
    return Domains(
        found=None if stats.get("unique_hosts") is None else _int(stats.get("unique_hosts")),
        excluded=_int(stats.get("excluded")),
        fresh=_int(stats.get("already_fresh")),
        to_check=_int(stats.get("checked_now")),
        checked=sum(_int(count) for count in measured.values()),
        suitable=by_status.get(DonorStatus.SUITABLE.value, 0),
        unsuitable=by_status.get(DonorStatus.UNSUITABLE.value, 0),
        unchecked=by_status.get(DonorStatus.UNCHECKED.value, 0),
        judge_enforced=judge.get("mode") == JudgeMode.ENFORCE.value,
        resumes=_int(stats.get(RESUMES_KEY)),
        fresh_judged=isinstance(stats.get(FRESH_KEY), Mapping),
    )


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """Отчёт прогона целиком: строка истории, пороги, домены, отсев и расход."""

    row: RunRow
    #: Пороги, с которыми прогон запущен: отсев называется вместе с ними.
    settings: RunSettingsModel
    domains: Domains | None
    #: Расход по журналу — всё, что записано за этим прогоном.
    spending: list[Article]
    #: Сколько запросов к Ahrefs обслужил кэш провайдера — бесплатно.
    free_requests: int
    rejected: list[Rejection] = field(default_factory=list)
    #: Исключённые гейтом до Ahrefs — по причине, словами.
    excluded: tuple[Named, ...] = ()
    #: «Не проверен» — почему, словами вердикта.
    unchecked: tuple[Named, ...] = ()


async def outcome(session: AsyncSession, run_id: int, *, stages: Collection[Stage]) -> RunOutcome:
    """Отчёт прогона. Нет прогона — `UnknownRunError` (`runs/browse.py`)."""
    row = await RunBrowser(session).one(run_id)
    stats: Mapping[str, Any] = row.run.stats or {}
    domains = domains_of(stats)
    # Итога нет — нет и причин: у идущего прогона их скажет конец попытки.
    said = {} if domains is None else _reasons(stats, row.run.country)
    return RunOutcome(
        row=row,
        settings=await RunRepository(session).settings_of(row.run),
        domains=domains,
        spending=await SpendingRepository(session).of_run(run_id, stages=stages),
        free_requests=sum(_int(count) for count in (stats.get("free_requests") or {}).values()),
        **said,
    )


def _reasons(stats: Mapping[str, Any], country: str) -> dict[str, Any]:
    """Почему не предложены: пороги, исключённые гейтом, «не проверен»."""
    return {
        "rejected": rejections(_summed(stats, "reject_reasons"), country),
        "excluded": tuple(excluded_named(stats)),
        "unchecked": tuple(unchecked_named(stats)),
    }


def _int(value: object) -> int:
    """Число из отчёта. Отчёт — JSON прошлых версий: чего нет — ноль, а не падение."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return int(value)
