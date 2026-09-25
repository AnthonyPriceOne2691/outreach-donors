"""Что уходит и приходит по маршрутам прогона."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator
from pydantic_core import PydanticCustomError

from backend.config import serp as serp_cfg
from backend.features.core.domain import RunStatus
from backend.features.runs.browse import RunRow
from backend.features.runs.estimate import RESULTS_PER_PAGE, RunForecast
from backend.features.runs.reasons import readable
from backend.features.runs.repository import REASON_KEY


class RunRequestBody(BaseModel):
    """Чего хотим от прогона. Ключи приходят списком, а не текстом:
    разбор текста в обработчике — это правило, уехавшее в веб-слой.

    **Отказы — своим текстом, а не умолчанием разбора.** Экран показывает
    отказ целиком, и умолчание давало бы «Input should be less than or equal
    to 5» или «Value error, …» — по-английски и без того, что делать.
    Поэтому проверки здесь бросают `PydanticCustomError`: его сообщение
    доходит до человека как написано.
    """

    keywords: list[str] = Field(min_length=1, max_length=500)
    country: str = Field(min_length=2, max_length=8)
    #: Глубина выдачи — страницами по десять результатов: провайдер берёт
    #: деньги за каждые десять, и смета считает в них же. Экран предлагает
    #: 10, 20, 30, 50 и 100 результатов; сервер принимает любую глубину
    #: от одной страницы до `MAX_DEPTH_PAGES` (командная строка берёт и 40).
    depth_pages: int = 1
    #: Потолок юнитов на этот прогон. Пусто — остаток по месячному капу.
    #: Нужен, чтобы попробовать нишу дёшево: без него единственный способ
    #: ограничить трату — сократить список ключей, а это другой вопрос.
    cap: int | None = Field(default=None, ge=1)

    @field_validator("keywords")
    @classmethod
    def _within_the_run_ceiling(cls, keywords: list[str]) -> list[str]:
        """Потолок ключей на прогон стоит в настройках, и проверять его
        надо здесь: до этой проверки настройка была объявлена и не
        применялась нигде, а прогон принимал впятеро больше ключей,
        чем заложено в требования."""
        if len(keywords) > serp_cfg.MAX_KEYWORDS_PER_RUN:
            raise PydanticCustomError(
                "too_many_keywords",
                f"За прогон берём не больше {serp_cfg.MAX_KEYWORDS_PER_RUN} ключей, "
                f"пришло {len(keywords)}. Разбейте список на несколько прогонов: "
                "так видно смету каждого и можно остановиться на середине",
            )
        return keywords

    @field_validator("depth_pages")
    @classmethod
    def _depth_the_screen_offers(cls, depth: int) -> int:
        """Глубина — от одной страницы выдачи до потолка из настроек.

        Прислать число результатов вместо страниц («50» вместо «5») —
        самая вероятная ошибка на этой границе, и цена её — вдесятеро
        дороже выдача. Поэтому отказ называет обе единицы.
        """
        most = serp_cfg.MAX_DEPTH_PAGES
        if not 1 <= depth <= most:
            raise PydanticCustomError(
                "depth_out_of_range",
                f"Глубина выдачи — от {RESULTS_PER_PAGE} до {most * RESULTS_PER_PAGE} "
                f"результатов на ключ, то есть от 1 до {most} страниц по "
                f"{RESULTS_PER_PAGE}; пришло {depth}. Глубина передаётся страницами, "
                "а не числом результатов",
            )
        return depth


class Forecast(BaseModel):
    """Смета до запуска.

    Числа подписаны как приблизительные не из скромности: точное число
    доменов известно только после выдачи, а выдача — уже трата. Смета
    считает худший случай (все домены новые), а точная проверка идёт
    второй раз, уже по настоящим доменам, до первого платного запроса.
    """

    keywords: int
    depth_pages: int
    expected_results: int
    expected_domains: int
    units_screen: int
    units_metrics: int
    units_by_country: int
    units_total: int
    units_left: int
    units_cap: int
    #: Потрачено нами с начала месяца — то, на что уменьшился кап.
    units_spent_this_month: int
    #: Остаток по месячному капу.
    cap_left: int
    #: Потолок, названный человеком для этого прогона. Пусто — не назвал.
    run_ceiling: int | None
    budget: int
    affordable: bool
    shortfall: int
    #: Ожидаемая стоимость самой выдачи, в долларах. Кнопку не блокирует:
    #: без выдачи прогона нет вовсе. Но названа быть должна.
    serp_cost_usd: float

    @classmethod
    def of(cls, forecast: RunForecast) -> Forecast:
        return cls(
            keywords=forecast.keywords,
            depth_pages=forecast.depth_pages,
            expected_results=forecast.expected_results,
            expected_domains=forecast.expected_domains,
            units_screen=forecast.estimate.screen,
            units_metrics=forecast.estimate.metrics,
            units_by_country=forecast.estimate.by_country,
            units_total=forecast.estimate.total,
            units_left=forecast.units_left,
            units_cap=forecast.units_cap,
            units_spent_this_month=forecast.units_spent_this_month,
            cap_left=forecast.cap_left,
            run_ceiling=forecast.run_ceiling,
            budget=forecast.budget,
            affordable=forecast.affordable,
            shortfall=forecast.shortfall,
            serp_cost_usd=forecast.serp_cost_usd,
        )


class RunCard(BaseModel):
    """Прогон в списке: что запускали, где он сейчас и чем кончился."""

    id: int
    status: RunStatus
    country: str
    keywords: int
    estimated_units: int | None
    actual_units: int | None
    estimate_error: float | None
    stats: dict[str, Any] | None
    started_at: datetime
    #: Когда прогон в последний раз подавал признаки жизни. Для идущего
    #: это удар heartbeat, а не запись результата: по времени последней
    #: записи медленный прогон неотличим от мёртвого.
    alive_at: datetime
    #: Сколько доменов дала выдача. Появляется раньше любых трат —
    #: это первое, что видно после нажатия.
    hosts: int | None
    #: Сколько доменов прогона посмотрел человек на экране отбора и сколько
    #: раз разошёлся с судьёй. Считается при чтении: решают после прогона.
    reviewed: int
    disagreements: int
    #: Очередь рассмотрения: `pending` / `accepted` / `rejected` → сколько.
    #: Пусто — прогон сделан до очереди.
    queue: dict[str, int] = {}
    #: Почему прогон остановлен или прерывался — словами человека. Имени
    #: класса исключения здесь нет, даже если оно записано в отчёте: причины
    #: до 25.09.2026 лежат в базе сырыми и показываются чисто, а сам отчёт
    #: не переписывается (`runs/reasons.py`). `None` — причины нет.
    reason: str | None = None

    @classmethod
    def of(cls, row: RunRow) -> RunCard:
        candidates = row.run.candidates or {}
        return cls(
            id=row.run.id,
            status=row.run.status,
            country=row.run.country,
            keywords=len(row.run.keywords),
            estimated_units=row.run.estimated_units,
            actual_units=row.run.actual_units,
            estimate_error=row.estimate_error,
            stats=row.run.stats,
            started_at=row.run.created_at,
            alive_at=row.run.updated_at,
            hosts=len(candidates["hosts"]) if candidates.get("hosts") is not None else None,
            reviewed=row.review.reviewed,
            disagreements=row.review.disagreements,
            queue=row.queue,
            reason=readable((row.run.stats or {}).get(REASON_KEY)),
        )


class RunsView(BaseModel):
    """Список прогонов и состояние самой очереди.

    Второе здесь не для красоты. Задача, которую некому взять, выглядит
    ровно как работающий сервис: сервер ответил «поставлено», строка
    прогона есть, и дальше не происходит ничего. Число живых воркеров —
    единственное, что отличает эти два случая на экране.
    """

    runs: list[RunCard]
    #: Сколько прогонов всего, а не на этой странице: по нему экран считает
    #: страницы и отличает «прогонов нет» от «на этой странице пусто».
    total: int
    #: Какая это страница (с единицы) и сколько прогонов на ней помещается.
    #: Размер страницы задаёт сервер — экран берёт его отсюда, а не держит свой.
    page: int
    limit: int
    #: Сколько воркеров слушает очередь. `None` — спросить не удалось,
    #: и это не ноль: неизвестность и пустота требуют разных слов.
    workers: int | None
    #: Сколько прогонов стоит в очереди — по всей истории, а не на этой
    #: странице: «задачу некому взять» касается и того, кто смотрит вторую.
    queued: int = 0


class RunQueued(BaseModel):
    """Прогон поставлен в очередь.

    Возвращается номер задачи, а не результат: прогон идёт минутами,
    и держать соединение открытым всё это время значит потерять
    оплаченную работу, если человек закрыл вкладку.
    """

    run_id: int
    job_id: str
    note: str = (
        "Прогон встал в очередь. Смета проверяется ещё раз по настоящим доменам "
        "и останавливает прогон до первого платного запроса, если не помещается."
    )
