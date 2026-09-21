"""Смета прогона до его запуска.

Главное свойство экрана прогона: **стоимость называется до того, как её
потратят.** Отсюда и главная трудность этого файла: точное число доменов
известно только после выдачи, а выдача — это уже трата.

Поэтому смета говорит то, что знает, и прямо называет то, чего не знает:

* сколько результатов выдачи ожидается — это арифметика, ключи на глубину;
* сколько из них останется уникальных доменов — **замер**, а не догадка:
  на прогоне 19.09.2026 из 500 результатов осталось 85 доменов, то есть
  83% схлопнулось в дубли;
* сколько из них новых — неизвестно до выдачи, и смета считает худший
  случай: все новые. Занизить здесь хуже, чем завысить: занижение
  означает «кнопка нажалась, а юниты кончились на середине».

Точная смета всё равно считается второй раз, уже по настоящим доменам,
и прогон останавливается до первого платного запроса, если не помещается
(`plan_run`). Этот файл нужен, чтобы человек увидел порядок цифры до того,
как нажмёт.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.config import serp as serp_cfg
from backend.features.ahrefs.units import RunEstimate, estimate_run

#: Результатов на страницу выдачи. Совпадает у обоих источников.
RESULTS_PER_PAGE = 10

#: Доля уникальных доменов среди результатов выдачи. Замер 19.09.2026:
#: 85 уникальных на 500 результатов. Число будет уточняться по мере
#: накопления своих прогонов — на другой нише выдача другая.
UNIQUE_SHARE = 0.17


@dataclass(frozen=True, slots=True)
class RunForecast:
    """Что известно до запуска. Числа приблизительные — и так и подписаны."""

    keywords: int
    depth_pages: int
    expected_results: int
    expected_domains: int
    estimate: RunEstimate
    units_left: int
    """Остаток **у провайдера** за вычетом обещанного идущими прогонами.
    Про наш кап он ничего не знает: ключ общий с соседней системой."""
    units_cap: int
    """Наш добровольный кап на месяц. Именно месячный и всегда он:
    потолок конкретного прогона — отдельное число ниже, и подменять
    одно другим нельзя (на живой проверке подменил — и свой потолок
    в пять тысяч начал вычитаться из месячной траты)."""
    #: Потрачено нами юнитов с начала месяца — то, на что уменьшился кап.
    units_spent_this_month: int = 0
    #: Потолок, названный человеком для этого прогона. Пусто — не назвал.
    run_ceiling: int | None = None
    #: Во что обойдётся сама выдача. Юниты и доллары не складываются:
    #: это два разных счёта у двух разных провайдеров.
    serp_cost_usd: float = 0.0

    @property
    def cap_left(self) -> int:
        """Сколько осталось по нашему месячному капу."""
        return max(0, self.units_cap - self.units_spent_this_month)

    @property
    def budget(self) -> int:
        """Сколько можно потратить в этом прогоне — меньшее из трёх:
        остатка у провайдера, остатка по месячному капу и потолка,
        названного человеком.

        Три ограничителя разной природы: первый жёсткий и чужой, второй
        наш и месячный, третий наш и на один прогон.
        """
        limits = [self.units_left, self.cap_left]
        if self.run_ceiling is not None:
            limits.append(self.run_ceiling)
        return max(0, min(limits))

    @property
    def affordable(self) -> bool:
        """Помещается ли смета в бюджет. По этому полю блокируется кнопка."""
        return self.estimate.total <= self.budget

    @property
    def shortfall(self) -> int:
        """Сколько юнитов не хватает. Ноль, если хватает."""
        return max(0, self.estimate.total - self.budget)


def forecast(
    *,
    keywords: int,
    depth_pages: int,
    units_left: int,
    units_cap: int,
    units_spent_this_month: int = 0,
    run_ceiling: int | None = None,
) -> RunForecast:
    """Смета по числу ключей — до единого обращения к провайдерам."""
    results = max(0, keywords) * max(1, depth_pages) * RESULTS_PER_PAGE
    domains = round(results * UNIQUE_SHARE)
    return RunForecast(
        keywords=max(0, keywords),
        depth_pages=max(1, depth_pages),
        expected_results=results,
        expected_domains=domains,
        # Худший случай: все домены новые. Занижение здесь означало бы
        # «кнопка нажалась, а юниты кончились на середине прогона».
        estimate=estimate_run(domains),
        units_left=units_left,
        units_cap=units_cap,
        units_spent_this_month=units_spent_this_month,
        run_ceiling=run_ceiling,
        # Выдача платится деньгами, и кнопку она не блокирует: к моменту,
        # когда смета показана, эта трата неизбежна — без выдачи прогона
        # нет вовсе. Но названа она должна быть: до этого числа расход
        # на выдачу не показывался нигде.
        serp_cost_usd=round(
            max(0, keywords) * max(1, depth_pages) * serp_cfg.PRICE_PER_KEYWORD_USD, 4
        ),
    )
