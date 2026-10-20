"""Выдача через DataForSEO — основной источник.

Дешевле запасного примерно в двадцать раз, и в этом весь смысл: выдача —
самая дорогая ступень прогона, а доноры из неё те же самые.

Основной режим отложенный, двухфазный: задачи ставятся пачкой, результат
забирается опросом. Это не наше усложнение, а устройство провайдера: так
дешевле всего, но ответ идёт минутами. Режимов три (`modes.py`), выбирает
человек на запуске: отложенная очередь, она же с приоритетом (около минуты,
вдвое дороже) и синхронный запрос на каждый ключ (секунды, втрое с лишним
дороже). Проверка на проде 14.10.2026: шесть минут из одиннадцати прогон сбора
ждал выдачу в отложенной очереди.

**Номера задач — наружу до опроса** (`protocol.SerpWatch`): сохранённые
вызывающим, они не дают повтору задачи после сбоя опроса купить ту же
выдачу второй раз. Цена и число собранных страниц — в журнале по каждой
задаче: прогон №1 сбора продаж стоил $0,0168 при смете $0,009, и сверить
было не с чем.

**Песочница — обязательный режим для тестов.** Провайдер отдаёт в ней
выдуманные ответы бесплатно и по той же схеме. Так проводка проверяется
живьём, но через тестовую учётку не проходит ни боевых данных, ни денег.

**Ключ без результатов присутствует в ответе с пустым списком.** Так
вызывающий отличает «по ключу ничего не нашлось» от «ключ потерялся
по дороге» — второе поломка, и оно должно быть видно.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import Sequence
from typing import Any

import httpx

from backend.config import serp as cfg
from backend.features.serp import markets
from backend.features.serp.dataforseo_answers import (
    TASK_IN_QUEUE,
    TASK_OK,
    SerpError,
    Ticker,
    live_items,
    log_task,
    organic_results,
    report_refused,
    sort_posted,
    task_items,
)
from backend.features.serp.modes import SerpMode, mode_of
from backend.features.serp.protocol import SerpResult, SerpWatch, distinct_keywords
from backend.shared.net.retry import RateLimiter, reason_of, unsent, with_retries

#: Что адаптер отдаёт наружу. `SerpError` живёт в разборе ответов
#: (`dataforseo_answers.py`), но его ловят по имени адаптера — список держит
#: это имя явным.
__all__ = [
    "COUNTRY_CODES",
    "DataForSeoProvider",
    "SerpError",
    "UnknownCountryError",
    "language_code",
    "location_code",
]

logger = logging.getLogger(__name__)

#: Боевой адрес провайдера — в отличие от песочницы. Не путать с синхронным
#: режимом (`SerpMode.LIVE`): тот — путь, а не адрес.
PROD_URL = "https://api.dataforseo.com"
SANDBOX_URL = "https://sandbox.dataforseo.com"

POST_PATH = "/v3/serp/google/organic/task_post"
GET_PATH = "/v3/serp/google/organic/task_get/regular"
#: Синхронный режим: одна задача на запрос, ответ в том же запросе.
LIVE_PATH = "/v3/serp/google/organic/live/regular"
#: Остаток на счету. Запрос бесплатный — им и спрашиваем перед прогоном.
BALANCE_PATH = "/v3/appendix/user_data"

#: Сколько раз пробуем один запрос. Провайдер отложенный: его отказы
#: чаще временные, чем окончательные.
ATTEMPTS = 3

#: Запросов в минуту. У провайдера предел 2000 на учётку, наш потолок
#: скромнее: упереться в чужой предел значит получить 429 на весь прогон, а
#: на той же учётке бывают соседние прогоны. 120 (до 14.10.2026) было мало
#: синхронному режиму — запрос на каждый ключ, и сотня ключей стояла бы
#: минуту в очереди одного ограничителя.
RATE_PER_MINUTE = 600

#: Одновременных синхронных запросов: сотня ключей покупается за десяток
#: секунд, и соединения не забиты.
LIVE_CONCURRENCY = 10

#: Приоритет задачи в очереди провайдера: 2 — высокий, около минуты вместо
#: пяти, вдвое дороже (`SerpMode.PRIORITY`).
PRIORITY_HIGH = 2

#: Больше задач за один запрос провайдер не принимает.
MAX_TASKS_PER_POST = 100

#: Код страны у провайдера — это числовой ISO плюс две тысячи. Карта
#: задана явно, а не вычисляется: правило верно для проверенных стран,
#: но провайдер вправе от него отступить, и молча промахнуться страной
#: значит собрать доноров не того рынка.
COUNTRY_CODES: dict[str, int] = {
    "us": 2840, "gb": 2826, "de": 2276, "fr": 2250, "es": 2724, "it": 2380,
    "nl": 2528, "pl": 2616, "ca": 2124, "au": 2036, "in": 2356, "br": 2076,
    "mx": 2484, "id": 2360, "ph": 2608, "za": 2710, "ae": 2784, "sa": 2682,
    "tr": 2792, "ua": 2804, "kz": 2398, "sg": 2702, "my": 2458, "th": 2764,
    "vn": 2704, "jp": 2392, "se": 2752, "no": 2578, "dk": 2208, "fi": 2246,
    "cz": 2203, "ro": 2642, "gr": 2300, "pt": 2620, "ie": 2372, "nz": 2554,
    "il": 2376, "eg": 2818, "ng": 2566, "ke": 2404, "ar": 2032, "cl": 2152,
    "co": 2170, "pe": 2604, "ch": 2756, "at": 2040, "be": 2056, "hu": 2348,
    "bg": 2100, "hr": 2191, "sk": 2703, "si": 2705, "lt": 2440, "lv": 2428,
    "ee": 2233, "uz": 2860,
}  # fmt: skip

#: Язык запроса по стране. Там, где не указан, берётся английский:
#: для сбора доноров язык интерфейса влияет слабее, чем регион.
# Карта «страна → язык» переехала в `features/serp/markets.py`: у неё
# появился второй потребитель (генерация ключей просит несколько языков),
# а две копии разошлись бы молча.

RESULTS_PER_PAGE = 10


class UnknownCountryError(SerpError):
    """Страны нет в карте кодов. Промахнуться страной хуже, чем не начать."""

    permanent = True


def location_code(country: str) -> int:
    code = COUNTRY_CODES.get(country.strip().lower())
    if code is None:
        raise UnknownCountryError(
            f"Страна «{country}» не в карте кодов провайдера. Код страны у него — "
            "числовой ISO плюс 2000; добавить в COUNTRY_CODES, сверившись "
            "со справочником /v3/serp/google/locations"
        )
    return code


def language_code(country: str) -> str:
    """Язык выдачи по рынку. Карта — `features/serp/markets.py`.

    Своей карты здесь больше нет: она подставляла английский незнакомой
    стране, и одиннадцать рынков из пятидесяти пяти искали английскую
    выдачу молча.
    """
    return markets.serp_language(country)


def _basic_auth(login: str, password: str) -> str:
    return base64.b64encode(f"{login}:{password}".encode()).decode()


class DataForSeoProvider:
    """Источник выдачи DataForSEO в выбранном режиме (`modes.SerpMode`)."""

    name = "dataforseo"

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        sandbox: bool | None = None,
        login: str | None = None,
        password: str | None = None,
        mode: SerpMode | None = None,
    ) -> None:
        self.mode = mode if mode is not None else mode_of(None)
        self._sandbox = cfg.SANDBOX if sandbox is None else sandbox
        self._base = SANDBOX_URL if self._sandbox else PROD_URL
        auth = _basic_auth(login or cfg.LOGIN, password or cfg.PASSWORD)
        self._own_client = client is None
        self._http = client or httpx.AsyncClient(
            base_url=self._base,
            timeout=cfg.TIMEOUT_S,
            headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
        )
        #: Сколько денег провайдера потрачено за время жизни адаптера.
        #: В песочнице всегда ноль — на то она и песочница.
        self.spent = 0.0
        #: Задачи, чья цена уже в `spent`: каждая платится один раз (`_count_once`).
        self._counted: set[str] = set()
        self._limiter = RateLimiter(per_minute=RATE_PER_MINUTE)

    async def aclose(self) -> None:
        if self._own_client:
            await self._http.aclose()

    async def search(
        self,
        keywords: Sequence[str],
        country: str,
        *,
        depth_pages: int = 1,
        watch: SerpWatch | None = None,
    ) -> dict[str, list[SerpResult]]:
        """Выдача по пачке ключей. Ключ без результатов — с пустым списком.

        Повтор — одна задача: регистр и лишние пробелы выдаче не важны, а задача
        стоит денег (`protocol.distinct_keywords`, то же правило у сметы)."""
        if depth_pages < 1:
            raise ValueError("Глубина выдачи считается страницами по десять; минимум одна")

        unique = distinct_keywords(keywords)
        out: dict[str, list[SerpResult]] = {k: [] for k in unique}
        if not unique:
            return out

        # Страна — до траты и до первого события: промахнуться рынком хуже, чем не начать.
        location_code(country)
        ticker = Ticker(watch or SerpWatch(), total=len(unique))
        wanted = depth_pages * RESULTS_PER_PAGE
        if self.mode is SerpMode.LIVE:
            await self._live(unique, country, wanted, out, ticker)
            return out
        for start in range(0, len(unique), MAX_TASKS_PER_POST):
            batch = unique[start : start + MAX_TASKS_PER_POST]
            tasks, costs = await self._post(batch, country, wanted, ticker)
            await self._collect(tasks, out, wanted, ticker, costs)
        return out

    def _payload(self, keyword: str, country: str, depth: int) -> dict[str, Any]:
        """Задача на ключ — одна и та же в отложенном и синхронном режиме."""
        task: dict[str, Any] = {
            "keyword": keyword,
            "location_code": location_code(country),
            "language_code": language_code(country),
            "depth": depth,
            # Метка возвращается провайдером как есть — по ней задача
            # сопоставляется с ключом надёжнее, чем по порядку в ответе.
            "tag": keyword,
        }
        if self.mode is SerpMode.PRIORITY:
            task["priority"] = PRIORITY_HIGH
        return task

    async def _post(
        self, keywords: Sequence[str], country: str, depth: int, ticker: Ticker
    ) -> tuple[dict[str, str], dict[str, float]]:
        """Поставить задачи. Возвращает «ключ → номер задачи» и цены задач.

        Ключ с номером из `watch.tasks` снова не ставится: его купила прошлая
        попытка, и результат забирается по номеру."""
        known = {k: ticker.watch.tasks[k] for k in keywords if k in ticker.watch.tasks}
        fresh = [k for k in keywords if k not in known]
        if not fresh:
            await ticker.start()
            return known, {}

        payload = [self._payload(keyword, country, depth) for keyword in fresh]
        body = await self._call("POST", POST_PATH, json=payload)
        self.spent += float(body.get("cost") or 0)

        created, refused, costs = sort_posted(body.get("tasks") or [])
        self._counted.update(created.values())
        # Номера — наружу до опроса: сбой опроса после этой строки не купит их снова.
        await ticker.watch.posted(created)
        await ticker.start()
        if refused:
            await report_refused(refused, costs, ticker, none_taken=not created and not known)
        return {**known, **created}, costs

    async def _live(
        self,
        keywords: Sequence[str],
        country: str,
        wanted: int,
        out: dict[str, list[SerpResult]],
        ticker: Ticker,
    ) -> None:
        """Синхронный режим: запрос на ключ, параллельно, ответ — в том же запросе.

        Номеров задач нет, и запрос не идемпотентен: повтор после обрыва провайдер
        считает новой покупкой. Зато окно такого повтора — секунды, а не минуты.

        Отказ насовсем (ключ, деньги, права) останавливает покупку: новые запросы
        не уходят. Временный сбой после повторов — ключ потерян, как недождавшийся
        в отложенном режиме: в ответе его нет, и прогон называет его потерянным.
        """
        gate = asyncio.Semaphore(LIVE_CONCURRENCY)
        stop: list[SerpError] = []
        await ticker.start()

        async def one(keyword: str) -> None:
            async with gate:
                if stop:
                    out.pop(keyword, None)
                    return
                try:
                    body = await self._call(
                        "POST", LIVE_PATH, json=[self._payload(keyword, country, wanted)]
                    )
                    cost = float(body.get("cost") or 0)
                    self.spent += cost
                    items, pages = live_items(body, keyword)
                except SerpError as error:
                    out.pop(keyword, None)
                    if error.permanent:
                        stop.append(error)
                    # Предупреждение, а не ошибка: отказ насовсем поднимается дальше
                    # (`raise stop[0]`), и его пишет вызывающий; временный — потеря ключа.
                    logger.warning(
                        "выдача: ключ «%s» не куплен — %s: %s",
                        keyword,
                        "покупка остановлена" if error.permanent else "остался без выдачи",
                        error,
                    )
                    return
            out[keyword] = organic_results(items, wanted)
            log_task(keyword, cost, pages)
            await ticker.answered(keyword, cost=cost, pages=pages)

        await asyncio.gather(*(one(keyword) for keyword in keywords))
        if stop:
            raise stop[0]

    async def _collect(
        self,
        tasks: dict[str, str],
        out: dict[str, list[SerpResult]],
        wanted: int,
        ticker: Ticker,
        costs: dict[str, float],
    ) -> None:
        """Забрать готовое. Не дождались — это видно, а не тихий ноль.

        **Окно ожидания растёт с глубиной.** Сто результатов провайдер
        собирает десятью страницами, и задача глубины 10 идёт дольше задачи
        топ-10; окно, рассчитанное на топ-10, отрезало бы оплаченную выдачу.

        **Не дождались — ключа нет в ответе.** Раньше он оставался пустым
        списком и в отчёте прогона выглядел как «ничего не нашлось», хотя
        выдача оплачена и просто не пришла. По протоколу источника пустой
        список — «ничего не нашлось», отсутствие — «потерялся по дороге»:
        прогон считает такие ключи отдельно (`Candidates.lost_keywords`).
        """
        pending = dict(tasks)
        deadline = cfg.POLL_ATTEMPTS * max(1, wanted // RESULTS_PER_PAGE)

        for attempt in range(deadline):
            if not pending:
                return
            for keyword, task_id in list(pending.items()):
                answer = await self._fetch(task_id)
                if answer is None:
                    continue
                items, pages = answer
                out[keyword] = organic_results(items, wanted)
                pending.pop(keyword, None)
                log_task(keyword, costs.get(keyword), pages)
                await ticker.answered(keyword, cost=costs.get(keyword), pages=pages)

            if pending and attempt < deadline - 1:
                await asyncio.sleep(cfg.POLL_INTERVAL_S)

        if pending:
            logger.error(
                "выдача: %d задач не дождались за %.0f c — ключи остались без выдачи: %s",
                len(pending),
                deadline * cfg.POLL_INTERVAL_S,
                list(pending)[:5],
            )
            for keyword in pending:
                out.pop(keyword, None)

    async def _fetch(self, task_id: str) -> tuple[list[Any], int | None] | None:
        """Позиции задачи и сколько страниц собрал провайдер. `None` — ещё не готово."""
        body = await self._call("GET", f"{GET_PATH}/{task_id}")

        tasks = body.get("tasks") or []
        if not tasks or not isinstance(tasks[0], dict):
            raise SerpError(f"Ответ по задаче {task_id} не разобран: нет задач")

        task = tasks[0]
        status = task.get("status_code")
        if status in TASK_IN_QUEUE:
            return None
        self._count_once(task_id, body, task)
        if status != TASK_OK:
            logger.error(
                "выдача: задача %s отклонена (%s %s)", task_id, status, task.get("status_message")
            )
            return [], None
        return task_items(task)

    def _count_once(self, task_id: str, body: dict[str, Any], task: dict[str, Any]) -> None:
        """Цена задачи, которую поставила прошлая попытка (`watch.tasks`), — из ответа забора.

        Забор бесплатный (прайс учётки: task_get — 0 $), но ответ повторяет цену задачи.
        Задачу этой попытки уже сосчитал ответ постановки, и до 20.10.2026 второй счёт
        удваивал расход выдачи в журнале и у предохранителя счёта (находка продаж). Задачу
        прошлой попытки постановка этой не видела: её цена берётся здесь, один раз.
        """
        if task_id in self._counted:
            return
        self._counted.add(task_id)
        cost = task.get("cost")
        if not isinstance(cost, int | float):
            cost = body.get("cost")
        self.spent += float(cost or 0)

    async def balance(self) -> float:
        """Остаток денег на счету провайдера. Запрос бесплатный.

        Нужен по той же причине, что остаток юнитов у Ahrefs: прогон,
        начатый на пустом счету, отказывает посреди платной работы —
        и выглядит это как «выдача ничего не нашла».

        **Счёт в песочнице тот же, что в бою** — учётка одна. Разница
        только в том, что задачи песочницы бесплатны.

        **Отказ — в задаче, а не в коде ответа.** Ответ приходит с 200 и общим кодом
        20000, а отказ лежит кодом и словами в самой задаче, с пустым результатом. 19.10.2026
        сметы подряд получали такой ответ, и журнал писал «формат поменялся», а слова
        провайдера терялись.
        """
        body = await self._call("GET", BALANCE_PATH)
        tasks = body.get("tasks") or []
        task = tasks[0] if tasks and isinstance(tasks[0], dict) else {}
        status = task.get("status_code")
        if status not in (TASK_OK, None):
            raise SerpError(f"Провайдер не назвал остаток: {status} {task.get('status_message')}")
        result = (task.get("result") or [{}])[0]
        money = result.get("money") if isinstance(result, dict) else None
        if not isinstance(money, dict) or "balance" not in money:
            raise SerpError("В ответе о счёте нет остатка — формат провайдера поменялся")
        return float(money["balance"])

    async def _call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        # Повторы и ограничитель — общие на все внешние сервисы.
        # До них один таймаут посреди прогона оставлял ключи без выдачи
        # молча: провайдер отложенный, и «не дождались» здесь обычное
        # дело, а не поломка.
        try:
            response = await with_retries(
                lambda: self._http.request(method, path, **kwargs),
                attempts=ATTEMPTS,
                topic="выдача",
                limiter=self._limiter,
            )
        except httpx.HTTPError as exc:
            # Несобранный запрос — постоянный отказ и без цепочки: в тексте
            # исключения заголовок Basic, а это логин и пароль в base64.
            error = SerpError(
                f"Провайдер выдачи недоступен: {reason_of(exc)}", permanent=unsent(exc)
            )
            raise error from None if unsent(exc) else exc

        if response.status_code >= 400:
            # 5xx, 429 и 408 сюда доходят, уже исчерпав повторы запроса, —
            # это временно; прочие 4xx (ключ, права, деньги на счёте, неверный
            # запрос) повтором не лечатся.
            raise SerpError(
                f"Провайдер выдачи ответил {response.status_code}: {response.text[:200]}",
                permanent=response.status_code < 500 and response.status_code not in (408, 429),
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise SerpError(
                f"Ответ провайдера не разобран как JSON: {response.text[:200]!r}"
            ) from exc

        if not isinstance(body, dict):
            raise SerpError(f"Ждали объект, пришло {type(body).__name__}")
        if body.get("status_code") not in (TASK_OK, None):
            raise SerpError(
                f"Провайдер отказал: {body.get('status_code')} {body.get('status_message')}"
            )
        return body
