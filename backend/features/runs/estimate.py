"""Смета прогона до его запуска.

Главное свойство экрана прогона: **стоимость называется до того, как её
потратят.** Отсюда и главная трудность этого файла: точное число доменов
известно только после выдачи, а выдача — это уже трата.

Поэтому смета говорит то, что знает, и прямо называет то, чего не знает:

* сколько результатов выдачи ожидается — это арифметика, ключи на глубину;
* сколько из них останется уникальных доменов — **из истории своих
  прогонов**, худшая доля из недавних (`RunRepository.unique_share`).
  До 24.09.2026 здесь стояла константа 0,17 из одного замера 19.09
  (500 результатов, 85 доменов): тестовые прогоны прода дали 70 доменов
  на 86 результатов и 62 на 8 ключей — смета «до 581» против 1 877
  потраченных, и второй прогон упёрся в потолок уже после покупки выдачи;
* сколько из них новых — неизвестно до выдачи, и смета считает худший
  случай: все новые. Занизить здесь хуже, чем завысить: занижение
  означает «кнопка нажалась, а юниты кончились на середине»;
* сколько новых получит запрос по странам — с 16.10.2026 каждый, кто прошёл
  пороги метрик (топ-5 стран донора по ТЗ), и доля — **худшая из недавних
  прогонов своей страны** (`RunRepository.country_call_share`). До того её брали
  из числа самих запросов, которых было меньше: верхняя страна пакета, совпавшая
  со страной прогона, избавляла от запроса, и старые прогоны занизили бы смету.

Точная смета всё равно считается второй раз, уже по настоящим доменам,
и прогон останавливается до первого платного запроса, если не помещается
(`plan_run`). Этот файл нужен, чтобы человек увидел порядок цифры до того,
как нажмёт.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.features.ahrefs.units import COUNTRY_CALL_SHARE, RunEstimate, estimate_run

#: Результатов на страницу выдачи. Совпадает у обоих источников.
RESULTS_PER_PAGE = 10

#: Доля уникальных доменов среди результатов, пока своей истории нет.
#: Худшая из замеренных на малых прогонах: 23 домена на 27 результатов
#: (0,85). С историей берётся своя — `RunRepository.unique_share`.
UNIQUE_SHARE = 0.85


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
    units_cap: int | None
    """Наш кап на цикл ключа — от размера ключа (`runs/ahrefs_cap.key_cap`); `None` —
    размер неизвестен, и помещаться нечему. Именно на цикл и всегда он: потолок
    конкретного прогона — отдельное число ниже, и подменять одно другим нельзя (на
    живой проверке подменил — и свой потолок в пять тысяч начал вычитаться из
    месячной траты)."""
    #: Потрачено нами юнитов с начала цикла ключа — то, на что уменьшился кап.
    units_spent_in_cycle: int = 0
    #: Потолок, названный человеком для этого прогона. Пусто — не назвал.
    run_ceiling: int | None = None
    #: Доля уникальных доменов, по которой посчитаны домены сметы. Экран
    #: называет по ней долю дублей: подсказка «83% схлопывается в дубли»
    #: стояла от константы 0,17, убранной 24.09, — рядом с «≈ 31 из 40»
    #: (проверка прода 10.10.2026).
    unique_share: float = UNIQUE_SHARE

    @property
    def cap_left(self) -> int:
        """Сколько осталось по нашему капу в цикле ключа; кап неизвестен — ноль."""
        if self.units_cap is None:
            return 0
        return max(0, self.units_cap - self.units_spent_in_cycle)

    @property
    def budget(self) -> int:
        """Сколько можно потратить в этом прогоне — меньшее из трёх:
        остатка у провайдера, остатка по месячному капу и потолка,
        названного человеком.

        Три ограничителя разной природы: первый жёсткий и чужой, второй
        наш и на цикл ключа, третий наш и на один прогон.
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
    units_cap: int | None,
    units_spent_in_cycle: int = 0,
    run_ceiling: int | None = None,
    country_share: float = COUNTRY_CALL_SHARE,
    unique_share: float = UNIQUE_SHARE,
) -> RunForecast:
    """Смета по числу ключей — до единого обращения к провайдерам. Цену самой выдачи
    считает ответ сметы по выбранному режиму (`serp/modes.serp_cost_usd`)."""
    results = max(0, keywords) * max(1, depth_pages) * RESULTS_PER_PAGE
    domains = round(results * unique_share)
    return RunForecast(
        keywords=max(0, keywords),
        depth_pages=max(1, depth_pages),
        expected_results=results,
        expected_domains=domains,
        # Худший случай: все домены новые. Занижение здесь означало бы
        # «кнопка нажалась, а юниты кончились на середине прогона».
        estimate=estimate_run(domains, country_share=country_share),
        units_left=units_left,
        units_cap=units_cap,
        units_spent_in_cycle=units_spent_in_cycle,
        run_ceiling=run_ceiling,
        unique_share=unique_share,
    )
