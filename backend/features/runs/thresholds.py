"""Пороги: текущие, сравнение новых с действующими и новая версия.

Пороги версионируются намеренно: смена порога не должна переписывать
вердикты прошлых прогонов — иначе через полгода непонятно, почему домен
отсеялся. Поэтому «сохранить» здесь означает «завести новую версию»,
а не «поправить строчку».

**Где пороги действуют — только в прогоне.** Прогон, заводясь, берёт версию,
действующую сейчас (`in_force`), и судит ею домены, которые измеряет: новые и те,
у кого истёк срок метрик (`DonorRepository.fresh_hosts`). Вердикт ложится в базу
при замере (`donors.status`), и его берут как есть «Отбор», «Доноры», «Обзор»,
сборка писем и обход. Сохранение порогов базу не пересчитывает.

**Предпросмотр — эти пороги против действующих, одним правилом с обеих сторон**
(проверка прода 10.10.2026). Обе стороны считает `check_metrics` — то же правило,
что у прогона, — по метрикам доменов базы. До этого «было» брало сохранённый
вердикт, а «станет» — одни метрики; вердикт же ставят ещё регион и прошлые версии
порогов, и любая правка «возвращала в базу» домены, отсеянные регионом: ключи
300 → 301 — «подходит 130, будет 135, вернётся 5». Теперь при порогах, равных
действующим, перемен ноль; строже — никого не пропустят сверх действующих; мягче —
никого не отсекут.

**Показывается и то, что отсекут, и то, что пропустят.** Порог двигают в обе
стороны, и «отсекут 340» без «пропустят 12» — половина ответа. Отдельной
строкой — сколько среди отсечённых тех, у кого цена уже получена: за них
заплачено не только юнитами, но и письмом.

**Версия — это не только четыре порога** (сверка ТЗ 11.10.2026). Правило региона
и срок годности метрик лежат в той же строке (`Rules`), и прогон судит ими, а не
переменными окружения: до правки топ-N, доля и срок брались из конфига, а поле
`metrics_ttl_days` версии только записывалось. Новая версия переносит их из
действующей; конфиг — умолчания первой.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import filters as filters_cfg
from backend.features.core.domain import DonorStatus
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.run import RunSettingsModel
from backend.features.donors.geo import GeoRule
from backend.features.donors.verdict import Metrics, Thresholds, check_metrics


def _metrics_of(donor: DonorModel) -> Metrics:
    """Метрики из JSONB. Ключи те же, что пишет сборщик."""
    raw: dict[str, Any] = donor.metrics or {}
    return Metrics(
        dr=float(donor.dr) if donor.dr is not None else None,
        org_traffic=donor.org_traffic,
        refdomains=raw.get("refdomains"),
        org_keywords=raw.get("org_keywords"),
    )


def _passes(metrics: Metrics, thresholds: Thresholds) -> bool | None:
    """Пропускают ли пороги домен: да, нет — или `None`, если метрик для решения нет.

    Третий ответ — не «нет». Домен, отсеянный по DR, остальных метрик не покупал
    (`collect`, первая ступень): мягче DR пустил бы его дальше, а пройдёт ли он там,
    скажет только замер.
    """
    verdict = check_metrics(metrics, thresholds)
    if verdict is None:
        return True
    return None if verdict.status is DonorStatus.UNCHECKED else False


@dataclass(slots=True)
class Consequences:
    """Эти пороги против действующих — по метрикам доменов базы.

    Обе стороны считает одно правило (`_passes` → `check_metrics`): при порогах,
    равных действующим, `cut`, `admitted` и `undecided` — нули. Регион и решения
    человека в сравнении не считаются — их пороги не решают; они есть только в
    `suitable`, вердикте базы, который стоит рядом для сверки с «Обзором».
    """

    #: Домены с метриками: их и сравнивают.
    checked: int = 0
    #: Пропускают действующие пороги.
    passing_now: int = 0
    #: Пропустят эти.
    passing_after: int = 0
    #: Действующие пропускают, эти — нет.
    cut: int = 0
    #: Из них — с полученной ценой.
    cut_with_price: int = 0
    #: Эти пропускают, действующие — нет.
    admitted: int = 0
    #: Действующие отсекают, эти пустили бы дальше, но метрик для решения нет.
    undecided: int = 0
    #: Домены без метрик (DR нет): пороги их не судят. Число показывается
    #: отдельно — ступень, которая ничего не решает, иначе выглядит работающей.
    without_metrics: int = 0
    #: С вердиктом «подходит» в базе — по всем доменам, с заведёнными руками без
    #: метрик: то же правило, что «Прошли пороги» на «Обзоре» (`ops/overview.py`).
    #: Экран ставит его рядом со сравнением, и два экрана не расходятся числом
    #: (проверка прода 10.10.2026: «Пороги» — 130, «Обзор» — 131).
    suitable: int = 0

    def count(self, donor: DonorModel, candidate: Thresholds, in_force: Thresholds) -> None:
        """Учесть один домен — тем же правилом с обеих сторон."""
        self.suitable += int(donor.status is DonorStatus.SUITABLE)
        metrics = _metrics_of(donor)
        if metrics.dr is None:
            self.without_metrics += 1
            return
        now, after = _passes(metrics, in_force), _passes(metrics, candidate)
        self.checked += 1
        self.passing_now += int(now is True)
        self.passing_after += int(after is True)
        cut = now is True and after is False
        self.cut += int(cut)
        self.cut_with_price += int(cut and donor.last_price is not None)
        self.admitted += int(after is True and now is False)
        self.undecided += int(now is False and after is None)


def consequences(
    donors: Sequence[DonorModel], candidate: Thresholds, *, in_force: Thresholds
) -> Consequences:
    """Эти пороги против действующих — по метрикам доменов базы, ничего не меняя."""
    result = Consequences()
    for donor in donors:
        result.count(donor, candidate, in_force)
    return result


class ThresholdsRepository:
    """Версии порогов: прочитать текущую, завести новую."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def current(self) -> RunSettingsModel | None:
        """Последняя версия. `None` — порогов ещё не заводили, и тогда
        действуют умолчания конфига."""
        rows = await self._session.execute(
            select(RunSettingsModel).order_by(RunSettingsModel.version.desc()).limit(1)
        )
        return rows.scalar_one_or_none()

    async def history(self, *, limit: int = 20) -> Sequence[RunSettingsModel]:
        rows = await self._session.execute(
            select(RunSettingsModel).order_by(RunSettingsModel.version.desc()).limit(limit)
        )
        return rows.scalars().all()

    async def donors(self) -> Sequence[DonorModel]:
        """База целиком: предпросмотр отвечает на вопрос «что станет
        со всей базой», а не «что станет со страницей таблицы»."""
        rows = await self._session.execute(select(DonorModel))
        return rows.scalars().all()

    async def save(
        self, candidate: Thresholds, *, author: str, rules: Rules | None = None
    ) -> RunSettingsModel:
        """Завести новую версию порогов.

        Остальные настройки переносятся из текущей версии, а не берутся
        из конфига: конфиг — это умолчания для первой версии, и подмешать
        их в десятую значило бы молча откатить чужую правку. `rules` — правило
        региона и сроки этой версии, если их правили на экране.
        """
        previous = await self.current()
        version = 1 if previous is None else previous.version + 1
        carried = rules or (default_rules() if previous is None else rules_of(previous))

        settings = RunSettingsModel(
            version=version,
            created_by=author,
            min_dr=candidate.min_dr,
            min_org_traffic=candidate.min_org_traffic,
            min_refdomains=candidate.min_refdomains,
            min_keywords=candidate.min_keywords,
            max_dr=candidate.max_dr,
            **carried.columns(),
            units_cap=previous.units_cap if previous else 0,
        )
        self._session.add(settings)
        await self._session.flush()
        return settings


def defaults() -> Thresholds:
    """Умолчания конфига — то, что действует, пока порогов не заводили."""
    return Thresholds(
        min_dr=filters_cfg.MIN_DR,
        min_org_traffic=filters_cfg.MIN_ORG_TRAFFIC,
        min_refdomains=filters_cfg.MIN_REFDOMAINS,
        min_keywords=filters_cfg.MIN_KEYWORDS,
        max_dr=filters_cfg.MAX_DR,
    )


def thresholds_of(settings: RunSettingsModel) -> Thresholds:
    """Пороги версии настроек — те, с которыми прогон поставлен: по его `settings_id`
    вердикт объясним и через полгода."""
    return Thresholds(
        min_dr=settings.min_dr,
        min_org_traffic=settings.min_org_traffic,
        min_refdomains=settings.min_refdomains,
        min_keywords=settings.min_keywords,
        max_dr=settings.max_dr,
    )


@dataclass(frozen=True, slots=True)
class Rules:
    """Чем прогон судит домены — версия настроек целиком, кроме капа.

    Пороги, правило региона и срок годности метрик — из одной строки `run_settings`:
    прогон, поставленный с версией, судит ею, и по его `settings_id` вердикт
    объясним целиком, а не только порогами. Срок цены едет рядом — его переносит
    каждая новая версия.
    """

    thresholds: Thresholds
    geo: GeoRule = field(default_factory=GeoRule)
    metrics_ttl_days: int = filters_cfg.METRICS_TTL_DAYS
    price_ttl_days: int = filters_cfg.PRICE_TTL_DAYS

    def columns(self) -> dict[str, int | float]:
        """Поля строки настроек, кроме порогов и капа, — для новой версии."""
        return {
            "geo_top_n": self.geo.top_n,
            "geo_min_share": self.geo.min_share,
            "metrics_ttl_days": self.metrics_ttl_days,
            "price_ttl_days": self.price_ttl_days,
        }


def default_rules() -> Rules:
    """Умолчания конфига — первая версия, пока настроек не заводили."""
    return Rules(thresholds=defaults())


def rules_of(settings: RunSettingsModel) -> Rules:
    """Версия настроек целиком — та, с которой прогон поставлен."""
    return Rules(
        thresholds=thresholds_of(settings),
        geo=GeoRule(top_n=settings.geo_top_n, min_share=settings.geo_min_share),
        metrics_ttl_days=settings.metrics_ttl_days,
        price_ttl_days=settings.price_ttl_days,
    )


async def rules_in_force(session: AsyncSession) -> Rules:
    """Версия, действующая сейчас: последняя, а пока её нет — умолчания конфига.

    Ею заводится прогон — с кнопки и из консоли: новая строка настроек прогона
    переносит её целиком, а не берёт правило региона и срок из окружения.
    """
    current = await ThresholdsRepository(session).current()
    return default_rules() if current is None else rules_of(current)


async def in_force(session: AsyncSession) -> Thresholds:
    """Пороги, действующие сейчас: последняя версия, а пока её нет — умолчания конфига.

    Этим заводится прогон (аудит 10.10.2026). Запуск брал `defaults()`, и правка на экране
    «Пороги» до сбора не доходила; а строка настроек прогона — тоже версия, последняя, —
    после каждого запуска показывала на экране умолчания вместо сохранённого.
    """
    return (await rules_in_force(session)).thresholds
