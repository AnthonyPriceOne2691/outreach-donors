"""Что уходит и приходит по маршрутам порогов и расхода."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from backend.api.settings.cap_schemas import AhrefsKeyCard
from backend.features.core.domain import Stage, UsageProvider
from backend.features.core.models.run import RunSettingsModel
from backend.features.crawl.selection_settings import InForce, Number
from backend.features.donors.geo import COUNTRY_ROWS, GeoRule
from backend.features.donors.verdict import Thresholds
from backend.features.runs.ahrefs_cap import CapChange, KeyCap
from backend.features.runs.spending import Article, Spending, StageSpending
from backend.features.runs.thresholds import Consequences, Rules

#: Допустимые пороги — целые от и до, включительно. Одно место на схему и
#: экран: схема ниже отказывает по этим числам, а экран узнаёт их из ответа
#: (`ThresholdsView.limits`) и отказывает до нажатия тем же правилом
#: (замечание 28.09.2026: «валидация на допустимые значения и значки
#: подсказок, в каких диапазонах»). До этого экран знал только верх DR,
#: а трафик в сто миллионов уходил на сервер и возвращался отказом
#: по-английски.
#:
#: Границы стоят не для красоты: DR выше 90 отсекает всё, кроме десятка
#: сайтов мира, и такой прогон стоит юнитов, а даёт ноль.
LIMITS: dict[str, tuple[int, int]] = {
    "min_dr": (0, 90),
    "min_org_traffic": (0, 10_000_000),
    "min_refdomains": (0, 1_000_000),
    "min_keywords": (0, 1_000_000),
    # Крупный сайт: DR выше — «не подходит», писем нет (12.10.2026 — порог версии).
    "max_dr": (1, 100),
}


def _within(name: str) -> Any:
    low, high = LIMITS[name]
    return Field(ge=low, le=high)


#: Правило региона и срок метрик версии — целые от и до, как пороги (сверка ТЗ
#: 11.10.2026: «все пороги — в настройках прогона»). Топ — не шире пяти стран, что
#: спрашиваем у Ahrefs (`geo.COUNTRY_ROWS`); доля — процентами; срок — днями, до года.
RULE_LIMITS: dict[str, tuple[int, int]] = {
    "geo_top_n": (1, COUNTRY_ROWS),
    "geo_min_share_percent": (1, 100),
    "metrics_ttl_days": (1, 365),
}


def _rule(name: str) -> Any:
    low, high = RULE_LIMITS[name]
    return Field(default=None, ge=low, le=high)


class ThresholdsBody(BaseModel):
    """Пороги отбора — в границах `LIMITS`; правило региона и срок метрик — в `RULE_LIMITS`.

    Правило и срок необязательны: пусто — как у действующей версии. Их согласованность
    (`топ × доля ≥ 1`) проверяет сохранение — вместе с тем, что берётся из действующей.
    """

    min_dr: int = _within("min_dr")
    min_org_traffic: int = _within("min_org_traffic")
    min_refdomains: int = _within("min_refdomains")
    min_keywords: int = _within("min_keywords")
    #: Порог крупного сайта; пусто — как у действующей версии.
    max_dr: int | None = Field(default=None, ge=LIMITS["max_dr"][0], le=LIMITS["max_dr"][1])
    geo_top_n: int | None = _rule("geo_top_n")
    geo_min_share_percent: int | None = _rule("geo_min_share_percent")
    metrics_ttl_days: int | None = _rule("metrics_ttl_days")

    def with_rules_of(self, in_force: Rules) -> Rules:
        """Версия целиком: эти пороги, правило и срок — присланные, иначе действующие."""
        share, term = self.geo_min_share_percent, self.metrics_ttl_days
        return Rules(
            thresholds=self.to_thresholds(in_force.thresholds.max_dr),
            geo=GeoRule(
                top_n=in_force.geo.top_n if self.geo_top_n is None else self.geo_top_n,
                min_share=in_force.geo.min_share if share is None else share / 100,
            ),
            metrics_ttl_days=in_force.metrics_ttl_days if term is None else term,
            price_ttl_days=in_force.price_ttl_days,
        )

    def to_thresholds(self, max_dr_in_force: int) -> Thresholds:
        """Пороги тела; порог крупного сайта не прислан — действующий."""
        return Thresholds(
            min_dr=self.min_dr,
            min_org_traffic=self.min_org_traffic,
            min_refdomains=self.min_refdomains,
            min_keywords=self.min_keywords,
            max_dr=max_dr_in_force if self.max_dr is None else self.max_dr,
        )


def share_percent(share: float) -> int:
    """Доля версии процентами, как её правит экран: 0.2 → 20."""
    return round(share * 100)


class ThresholdsVersion(BaseModel):
    """Версия порогов: что стояло, кто поставил и когда — с правилом региона и сроком."""

    version: int
    created_by: str | None
    created_at: datetime
    min_dr: int
    min_org_traffic: int
    min_refdomains: int
    min_keywords: int
    max_dr: int
    geo_top_n: int
    geo_min_share_percent: int
    metrics_ttl_days: int

    @classmethod
    def of(cls, settings: RunSettingsModel) -> ThresholdsVersion:
        return cls(
            version=settings.version,
            created_by=settings.created_by,
            created_at=settings.created_at,
            min_dr=settings.min_dr,
            min_org_traffic=settings.min_org_traffic,
            min_refdomains=settings.min_refdomains,
            min_keywords=settings.min_keywords,
            max_dr=settings.max_dr,
            geo_top_n=settings.geo_top_n,
            geo_min_share_percent=share_percent(settings.geo_min_share),
            metrics_ttl_days=settings.metrics_ttl_days,
        )


class Range(BaseModel):
    """Допустимые значения порога: целые от `min` до `max` включительно."""

    min: int
    max: int


def _limits() -> dict[str, Range]:
    return {name: Range(min=low, max=high) for name, (low, high) in LIMITS.items()}


def _rule_limits() -> dict[str, Range]:
    return {name: Range(min=low, max=high) for name, (low, high) in RULE_LIMITS.items()}


class ThresholdsView(BaseModel):
    """Текущие пороги и история версий.

    `current` пусто, если порогов ещё не заводили: тогда действуют
    умолчания, и они же приходят в `defaults` — чтобы экран не выдумывал
    их на своей стороне. Так же и границы (`limits`): экран проверяет
    поле ими, а не своей копией чисел.
    """

    current: ThresholdsVersion | None
    defaults: ThresholdsBody
    history: list[ThresholdsVersion]
    limits: dict[str, Range] = Field(default_factory=_limits)
    #: Границы правила региона и срока метрик — экран проверяет поля ими.
    rule_limits: dict[str, Range] = Field(default_factory=_rule_limits)


class ConsequencesView(BaseModel):
    """Эти пороги против действующих — по метрикам доменов базы.

    Показываются обе стороны: и кого эти пороги отсекут, и кого пропустят сверх
    действующих. Порог двигают в обе стороны, и «отсекут 340» без «пропустят 12» —
    половина ответа. Вердикты в базе сохранение не переписывает, и имена полей
    не обещают перемен в ней: до 10.10.2026 они звались «выпадет из базы» и
    «вернётся в базу» (проверка прода 10.10.2026).
    """

    #: Домены с метриками: их и сравнивают.
    checked: int
    #: Пропускают действующие пороги.
    passing_now: int
    #: Пропустят эти.
    passing_after: int
    #: Действующие пропускают, эти — нет.
    cut: int
    #: Из них — с полученной ценой.
    cut_with_price: int
    #: Эти пропускают, действующие — нет.
    admitted: int
    #: Действующие отсекают, эти пустили бы дальше, но метрик для решения нет.
    undecided: int
    #: Домены без метрик: пороги их не судят.
    without_metrics: int
    #: С вердиктом «подходит» в базе — то же число, что «Прошли пороги» на «Обзоре».
    suitable: int

    @classmethod
    def of(cls, data: Consequences) -> ConsequencesView:
        return cls(
            checked=data.checked,
            passing_now=data.passing_now,
            passing_after=data.passing_after,
            cut=data.cut,
            cut_with_price=data.cut_with_price,
            admitted=data.admitted,
            undecided=data.undecided,
            without_metrics=data.without_metrics,
            suitable=data.suitable,
        )


class ArticleCard(BaseModel):
    """Статья расхода. `stage` пусто — трата общая на все этапы или этап не назван."""

    provider: UsageProvider
    operation: str
    units: int
    amount_usd: Decimal
    calls: int
    stage: Stage | None = None

    @classmethod
    def of(cls, article: Article) -> ArticleCard:
        return cls(
            provider=article.provider,
            operation=article.operation,
            units=article.units,
            amount_usd=article.amount_usd,
            calls=article.calls,
            stage=article.stage,
        )


class StageCard(BaseModel):
    """Расход этапа: единицы по провайдерам — каждый в своей валюте — и деньги."""

    stage: Stage | None
    units_by_provider: dict[str, int]
    amount_usd: Decimal
    calls: int

    @classmethod
    def of(cls, spent: StageSpending) -> StageCard:
        return cls(
            stage=spent.stage,
            units_by_provider={
                provider.value: units for provider, units in spent.units_by_provider.items()
            },
            amount_usd=spent.amount_usd,
            calls=spent.calls,
        )


class CrawlSpendCard(BaseModel):
    """Платный обход Этапа 2 — прокси и рендеринг — против своего потолка.

    `cap_usd` — потолок за месяц, `None` — своего потолка нет (настройка пуста).
    `spent_usd` — то же число, с которым потолок сравнивает сам обход
    (`runs/spending.crawl_spent_this_month`).
    """

    cap_usd: Decimal | None = None
    spent_usd: Decimal = Decimal(0)


class SpendingView(BaseModel):
    """Расход с начала месяца плюс остаток у провайдера и кап Ahrefs в цикле ключа.

    **Два периода, и путать их нельзя** (16.10.2026). Статьи, плитки и разбивка по этапам —
    с первого числа (`since`): так считают месяц Hunter и «Обзор». Кап Ahrefs — в цикле
    ключа, от его обнуления (`ahrefs_key`): у ключа своё обнуление, и с первого числа кап
    показывал почти выбранным, когда у ключа оставалась большая часть лимита.

    Остаток и наш расход — тоже разные числа — первая версия экрана путала их, показывая
    «израсходовано 0» при шести тысячах потраченных юнитов:

    * `ahrefs_left` — остаток **у провайдера** прямо сейчас. Ключ общий с соседней
      системой, поэтому её траты тоже уменьшают это число;
    * `ahrefs_key.spent_by_us` — сколько потратили мы за цикл, по своей таблице. Именно
      это число вычитают из капа; остаток провайдера для этого не годится, потому что
      включает чужой расход.
    """

    since: datetime
    articles: list[ArticleCard]
    units_by_provider: dict[str, int]
    amount_by_provider: dict[str, Decimal]
    total_units: int
    total_amount: Decimal
    ahrefs_left: int | None
    #: Ключ Ahrefs и наш кап в цикле ключа — размер, цикл, выбрано, осталось, смены.
    ahrefs_key: AhrefsKeyCard
    ahrefs_left_error: str | None = None
    #: Остаток денег у источника выдачи. Ключ там свой, не общий,
    #: поэтому вычитать чужое не из чего.
    serp_left_usd: Decimal | None = None
    serp_spent_by_us: Decimal = Decimal(0)
    serp_left_error: str | None = None
    #: Расход продаж не показан — у учётки нет права «Продажи» (решение Anthony 10.10.2026,
    #: П2б): статьи, суммы по провайдерам и итог — без него и сходятся между собой. Экран
    #: говорит это словами: итог без продаж — не весь счёт.
    sales_hidden: bool = False
    #: Разбивка по этапам (требование ТЗ): этапы с тратами, трата без этапа — последней.
    stages: list[StageCard] = Field(default_factory=list)
    crawl: CrawlSpendCard = Field(default_factory=CrawlSpendCard)

    @classmethod
    def of(
        cls,
        spending: Spending,
        *,
        ahrefs_balance: tuple[int | None, str | None],
        cap: KeyCap,
        cap_changes: list[CapChange],
        serp_balance: tuple[Decimal | None, str | None] = (None, None),
        sales_hidden: bool = False,
        crawl: CrawlSpendCard | None = None,
    ) -> SpendingView:
        """Остатки у провайдеров — парами «число или почему его нет»: ответ провайдера
        приходит одним из двух, и пара не даёт передать оба или ни одного по ошибке."""
        ahrefs_left, ahrefs_left_error = ahrefs_balance
        serp_left_usd, serp_left_error = serp_balance
        return cls(
            ahrefs_key=AhrefsKeyCard.of(cap, cap_changes),
            since=spending.since,
            articles=[ArticleCard.of(article) for article in spending.articles],
            stages=[StageCard.of(spent) for spent in spending.by_stage],
            crawl=crawl or CrawlSpendCard(),
            units_by_provider={
                provider.value: units for provider, units in spending.units_by_provider.items()
            },
            amount_by_provider={
                provider.value: amount for provider, amount in spending.amount_by_provider.items()
            },
            total_units=spending.total_units,
            total_amount=spending.total_amount,
            ahrefs_left=ahrefs_left,
            ahrefs_left_error=ahrefs_left_error,
            serp_left_usd=serp_left_usd,
            serp_spent_by_us=spending.amount_by_provider.get(UsageProvider.SERP, Decimal(0)),
            serp_left_error=serp_left_error,
            sales_hidden=sales_hidden,
        )


class SettingNumber(BaseModel):
    """Вес или порог Этапа 2: что значит, сколько и какой настройкой меняется."""

    label: str
    value: int
    setting: str


class SettingList(BaseModel):
    """Список «кому не пишем» Этапа 2 и настройка, которой он меняется."""

    label: str
    values: list[str]
    setting: str


class AdvertiserSettingsView(BaseModel):
    """Чем Этап 2 судит ссылки сейчас — только чтение: значения живут в окружении
    (`config/advertisers.py`), и правка — это настройка, а не версия, как у порогов."""

    points: list[SettingNumber]
    thresholds: list[SettingNumber]
    lists: list[SettingList]

    @classmethod
    def of(cls, found: InForce) -> AdvertiserSettingsView:
        def number(item: Number) -> SettingNumber:
            return SettingNumber(label=item.label, value=item.value, setting=item.setting)

        return cls(
            points=[number(item) for item in found.points],
            thresholds=[number(item) for item in found.thresholds],
            lists=[
                SettingList(label=item.label, values=list(item.values), setting=item.setting)
                for item in found.lists
            ],
        )
