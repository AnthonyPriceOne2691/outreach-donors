"""Что уходит и приходит по маршрутам прогона."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator
from pydantic_core import PydanticCustomError

from backend.config import serp as serp_cfg
from backend.features.core.domain import RunStatus
from backend.features.runs.ahrefs_cap import KeyCap
from backend.features.runs.browse import RunRow
from backend.features.runs.estimate import RESULTS_PER_PAGE, RunForecast
from backend.features.runs.reasons import readable
from backend.features.runs.repository import REASON_KEY
from backend.features.serp.funds import refusal
from backend.features.serp.modes import (
    EXPECTED_WAIT_S,
    SerpMode,
    UnknownModeError,
    mode_of,
    serp_cost_usd,
)
from backend.features.serp.protocol import distinct_keywords
from backend.shared.numbers import spaced

#: Свой потолок юнитов на прогон — не больше этого: страж опечатки, а не лимит. Выше
#: остатка капа потолок ничего не ограничивает — прогон всё равно режется им
#: (`routes.effective_cap`, кап — от размера ключа Ahrefs с 16.10.2026), а двадцать
#: девяток в поле — опечатка. До 10.10.2026 верха не было, и смета отвечала «ваш потолок
#: 100 000 000 000 000 000 000» (проверка прода). Экран держит то же число
#: (`runs/RunPage.tsx`, `CAP`) и отказывает до нажатия.
MAX_RUN_CEILING = 1_000_000


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
    #: Потолок юнитов на этот прогон. Пусто — остаток по капу цикла ключа.
    #: Нужен, чтобы попробовать нишу дёшево: без него единственный способ
    #: ограничить трату — сократить список ключей, а это другой вопрос.
    cap: int | None = Field(default=None, ge=1)
    #: Режим покупки выдачи (`serp/modes.py`). Пусто — умолчание сервиса,
    #: быстрый: слово владельца 14.10.2026 — «в первую очередь скорость».
    serp_mode: SerpMode | None = None

    @field_validator("serp_mode", mode="before")
    @classmethod
    def _known_mode(cls, value: object) -> object:
        """Незнакомый режим — отказ по-русски с перечнем известных, как у прочих полей."""
        if value is None or isinstance(value, SerpMode):
            return value
        try:
            return mode_of(str(value))
        except UnknownModeError as exc:
            raise PydanticCustomError("serp_mode", str(exc)) from None

    @field_validator("keywords")
    @classmethod
    def _what_the_serp_buys(cls, keywords: list[str]) -> list[str]:
        """Ключи — те, за которые заплатит выдача, и потолок — по ним.

        Повторы сводятся здесь, до сметы и до запуска, правилом провайдера
        (`serp.protocol.distinct_keywords`): смета считает ровно то, что купит
        выдача, а прогон хранит то, что купил. До 10.10.2026 два одинаковых ключа
        и вариант с заглавными были «3 ключа» в смете, а выдача покупала два —
        за вариант с заглавными второй раз (проверка прода 10.10.2026).

        Потолок ключей на прогон стоит в настройках, и проверять его
        надо здесь: до этой проверки настройка была объявлена и не
        применялась нигде, а прогон принимал впятеро больше ключей,
        чем заложено в требования."""
        distinct = distinct_keywords(keywords)
        if not distinct:
            raise PydanticCustomError(
                "no_keywords", "Ключей нет — одни пустые строки. Впишите ключи, по одному в строке"
            )
        if len(distinct) > serp_cfg.MAX_KEYWORDS_PER_RUN:
            raise PydanticCustomError(
                "too_many_keywords",
                f"За прогон берём не больше {serp_cfg.MAX_KEYWORDS_PER_RUN} ключей, "
                f"пришло {len(distinct)}. Разбейте список на несколько прогонов: "
                "так видно смету каждого и можно остановиться на середине",
            )
        return distinct

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

    @field_validator("cap")
    @classmethod
    def _ceiling_that_means_something(cls, cap: int | None) -> int | None:
        """Потолок — до `MAX_RUN_CEILING`: выше он ничего не ограничивает."""
        if cap is not None and cap > MAX_RUN_CEILING:
            most, came = (spaced(number) for number in (MAX_RUN_CEILING, cap))
            raise PydanticCustomError(
                "cap_out_of_range",
                f"Потолок юнитов — не больше {most} на прогон, пришло {came}. "
                "Пусто — весь остаток по капу",
            )
        return cap


def _binding(forecast: RunForecast, cap: KeyCap) -> str | None:
    """Упор бюджета словом экрана: остаток у Ahrefs — каким из двух его лимитов (ключа или
    пространства: поднимают их в Ahrefs по-разному), наш кап, потолок прогона. Кап
    неизвестен — `None`: у экрана на это своя плашка."""
    if forecast.units_cap is None:
        return None
    return _side(forecast.binding, cap)


def _side(limit: str, cap: KeyCap) -> str:
    """«provider» — словом экрана: какой из двух лимитов Ahrefs (ключа или пространства)."""
    if limit != "provider":
        return limit
    return "key" if cap.key is None else cap.key.quota.tighter


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
    #: Наш кап на цикл ключа Ahrefs; `None` — размер ключа неизвестен (`cap_unknown`).
    units_cap: int | None
    #: Потрачено нами с начала цикла ключа — то, на что уменьшился кап.
    units_spent_in_cycle: int
    #: Остаток по капу цикла.
    cap_left: int
    #: С какого момента считается цикл, и от ключа ли он: `False` — календарный месяц,
    #: Ahrefs не назвал дату обнуления.
    cycle_start: datetime
    cycle_by_key: bool
    #: Почему кап неизвестен — словами; `None` — известен.
    cap_unknown: str | None = None
    #: Следующее обнуление ключа: до какого дня держится остаток («до 14 ноября»).
    next_reset: datetime
    #: Потолок, названный человеком для этого прогона. Пусто — не назвал.
    run_ceiling: int | None
    budget: int
    affordable: bool
    shortfall: int
    #: Во что упирается бюджет: «key» или «workspace» — лимит Ahrefs (кап здесь не поможет),
    #: «cap» — наш кап цикла, «ceiling» — потолок прогона; кап неизвестен — `None`.
    binding: str | None = None
    #: Каких лимитов не хватает — каждого, теми же словами, жёсткие первыми: не хватает и ключа,
    #: и капа — экран просит проверить оба. Помещается или кап неизвестен — пусто.
    short_of: list[str] = Field(default_factory=list)
    #: Ожидаемая стоимость самой выдачи, в долларах. Кнопку не блокирует:
    #: без выдачи прогона нет вовсе. Но названа быть должна.
    serp_cost_usd: float
    #: Остаток на счету выдачи (DataForSEO); `None` — счёта нет или он не ответил.
    serp_balance_usd: float | None = None
    #: Почему выдачу не купить — «пополните счёт» словами (`serp/funds.py`, 16.10.2026):
    #: кнопку блокирует, как и нехватка юнитов. `None` — купить можно или остаток неизвестен.
    serp_refusal: str | None = None
    #: Почему модель суда не готова — словами владельца (`shared/llm.unready`, 20.10.2026): нет
    #: кредитов, ключ не принят, не ответила. Кнопку блокирует, как «пополните счёт».
    model_refusal: str | None = None
    #: Режим выдачи, по которому посчитана цена, и сколько её ждать, секунд.
    serp_mode: SerpMode
    serp_wait_s: int
    #: Доля уникальных доменов среди результатов, с которой посчитано
    #: `expected_domains`: экран называет по ней долю дублей, а не своим числом.
    unique_share: float
    #: Токены модели на тематику сайтов (`topics/cost.py`) — худший случай и на сайт; ноль —
    #: темы прогон не определит. `topic_measured` — по замеру прошлых прогонов, а не оценка.
    topic_tokens: int = 0
    topic_tokens_per_site: int = 0
    topic_measured: bool = False

    @classmethod
    def of(
        cls,
        forecast: RunForecast,
        *,
        serp_mode: SerpMode,
        cap: KeyCap,
        serp_balance: float | None = None,
        model_refusal: str | None = None,
    ) -> Forecast:
        """Смета и кап, от которого она посчитана: цикл и почему кап неизвестен — от того же
        чтения (`ahrefs_cap.key_cap`), что дал смете кап и наш расход."""
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
            units_spent_in_cycle=forecast.units_spent_in_cycle,
            cap_left=forecast.cap_left,
            cycle_start=cap.cycle.start,
            cycle_by_key=cap.cycle.by_key,
            cap_unknown=cap.unknown,
            next_reset=cap.cycle.next_reset,
            run_ceiling=forecast.run_ceiling,
            budget=forecast.budget,
            affordable=forecast.affordable,
            shortfall=forecast.shortfall,
            binding=_binding(forecast, cap),
            short_of=(
                [] if forecast.units_cap is None else [_side(n, cap) for n in forecast.short_of]
            ),
            serp_cost_usd=(
                cost := serp_cost_usd(forecast.keywords, forecast.depth_pages, serp_mode)
            ),
            serp_balance_usd=serp_balance,
            serp_refusal=refusal(serp_balance, cost),
            model_refusal=model_refusal,
            serp_mode=serp_mode,
            serp_wait_s=EXPECTED_WAIT_S[serp_mode],
            unique_share=forecast.unique_share,
            topic_tokens=forecast.topic_tokens,
            topic_tokens_per_site=forecast.topic_per_site,
            topic_measured=forecast.topic_measured,
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
    #: О скольких доменах прогона человек сказал своё — решением на рассмотрении
    #: прогона или типом сайта на «Отборе» — и сколько раз разошёлся с судьёй
    #: (`runs.browse.tally_reviews`). Считается при чтении: решают после прогона.
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
    #: Кто запустил — почта учётки из записи журнала о запуске. `None` — записи нет:
    #: прогон запущен из консоли или заведён проверкой цепочки.
    started_by: str | None = None
    #: Домены конкурентов у прогона по их ссылкам (`runs/competitors.py`); `None` — по ключам.
    competitors: list[str] | None = None

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
            started_by=row.started_by,
            competitors=row.run.competitor_targets,
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
    #: Доменов ждут решения — каждый один раз: число у «Прогона» в меню.
    review_waiting: int = 0
    #: Из них ждут в очередях нескольких прогонов — в строках посчитаны в каждой.
    review_shared: int = 0


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
