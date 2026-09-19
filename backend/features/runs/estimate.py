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
    units_cap: int

    @property
    def budget(self) -> int:
        """Бюджет прогона — меньшее из остатка провайдера и нашего капа."""
        return min(self.units_left, self.units_cap)

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
    )
