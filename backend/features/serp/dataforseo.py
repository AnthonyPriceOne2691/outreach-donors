"""Выдача через DataForSEO — основной источник.

Дешевле запасного примерно в двадцать раз, и в этом весь смысл: выдача —
самая дорогая ступень прогона, а доноры из неё те же самые.

Режим отложенный, двухфазный: задачи ставятся пачкой, результат
забирается опросом. Это не наше усложнение, а устройство провайдера:
синхронного режима у него для нашего объёма нет, и один запрос на ключ
занимал бы минуты.

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
from backend.features.serp.protocol import SerpResult

logger = logging.getLogger(__name__)

LIVE_URL = "https://api.dataforseo.com"
SANDBOX_URL = "https://sandbox.dataforseo.com"

POST_PATH = "/v3/serp/google/organic/task_post"
GET_PATH = "/v3/serp/google/organic/task_get/regular"
#: Остаток на счету. Запрос бесплатный — им и спрашиваем перед прогоном.
BALANCE_PATH = "/v3/appendix/user_data"

#: Больше задач за один запрос провайдер не принимает.
MAX_TASKS_PER_POST = 100

#: Код задачи «принята» и код «готово». Всё остальное — отказ, и его
#: надо называть, а не считать пустой выдачей.
TASK_CREATED = 20100
TASK_OK = 20000
#: Задача ещё в работе — законное состояние, ждём дальше.
TASK_IN_QUEUE = (40601, 40602)

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
    "ee": 2233,
}  # fmt: skip

#: Язык запроса по стране. Там, где не указан, берётся английский:
#: для сбора доноров язык интерфейса влияет слабее, чем регион.
COUNTRY_LANGUAGES: dict[str, str] = {
    "de": "de", "fr": "fr", "es": "es", "it": "it", "nl": "nl", "pl": "pl",
    "br": "pt", "pt": "pt", "mx": "es", "id": "id", "th": "th", "vn": "vi",
    "jp": "ja", "tr": "tr", "ua": "uk", "kz": "ru", "se": "sv", "no": "no",
    "dk": "da", "fi": "fi", "cz": "cs", "ro": "ro", "gr": "el", "il": "he",
    "ar": "es", "cl": "es", "co": "es", "pe": "es", "hu": "hu", "bg": "bg",
}  # fmt: skip

RESULTS_PER_PAGE = 10


class SerpError(RuntimeError):
    """Провайдер выдачи отказал или ответил непонятным."""


class UnknownCountryError(SerpError):
    """Страны нет в карте кодов. Промахнуться страной хуже, чем не начать."""


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
    return COUNTRY_LANGUAGES.get(country.strip().lower(), "en")


def _basic_auth(login: str, password: str) -> str:
    return base64.b64encode(f"{login}:{password}".encode()).decode()


def _organic(items: Sequence[Any], wanted: int) -> list[SerpResult]:
    """Органические позиции из ответа.

    Выдача содержит и карты, и «люди также спрашивают», и рекламу.
    Доноры — только органика; всё остальное пропускается, но пропуск
    не считается ошибкой, это устройство выдачи.
    """
    out: list[SerpResult] = []
    for item in items:
        if not isinstance(item, dict) or item.get("type") != "organic":
            continue
        url, position = item.get("url"), item.get("rank_absolute")
        if not isinstance(url, str) or not isinstance(position, int):
            continue
        out.append(SerpResult(position=position, url=url))
        if len(out) >= wanted:
            break
    return out


def _sort_tasks(tasks: Sequence[Any]) -> tuple[dict[str, str], list[str]]:
    """Разложить ответ постановки на принятые и отклонённые.

    Вынесено из постановки задач: там собиралась и просьба, и разбор
    ответа, и решение об отказе — три темы в одной функции.
    """
    created: dict[str, str] = {}
    refused: list[str] = []

    for task in tasks:
        if not isinstance(task, dict):
            refused.append(f"строка ответа не разобрана: {task!r:.60}")
            continue
        keyword = str((task.get("data") or {}).get("tag") or "")
        if task.get("status_code") == TASK_CREATED and task.get("id"):
            created[keyword] = str(task["id"])
        else:
            refused.append(f"{keyword}: {task.get('status_code')} {task.get('status_message')}")

    return created, refused


class DataForSeoProvider:
    """Источник выдачи поверх отложенного режима провайдера."""

    name = "dataforseo"

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        sandbox: bool | None = None,
        login: str | None = None,
        password: str | None = None,
    ) -> None:
        self._sandbox = cfg.SANDBOX if sandbox is None else sandbox
        self._base = SANDBOX_URL if self._sandbox else LIVE_URL
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

    async def aclose(self) -> None:
        if self._own_client:
            await self._http.aclose()

    async def search(
        self, keywords: Sequence[str], country: str, *, depth_pages: int = 1
    ) -> dict[str, list[SerpResult]]:
        """Выдача по пачке ключей. Ключ без результатов — с пустым списком."""
        if depth_pages < 1:
            raise ValueError("Глубина выдачи считается страницами по десять; минимум одна")

        unique = list(dict.fromkeys(k.strip() for k in keywords if k.strip()))
        out: dict[str, list[SerpResult]] = {k: [] for k in unique}
        if not unique:
            return out

        wanted = depth_pages * RESULTS_PER_PAGE
        for start in range(0, len(unique), MAX_TASKS_PER_POST):
            batch = unique[start : start + MAX_TASKS_PER_POST]
            tasks = await self._post(batch, country, wanted)
            await self._collect(tasks, out, wanted)
        return out

    async def _post(self, keywords: Sequence[str], country: str, depth: int) -> dict[str, str]:
        """Поставить задачи. Возвращает соответствие «ключ → номер задачи»."""
        payload = [
            {
                "keyword": keyword,
                "location_code": location_code(country),
                "language_code": language_code(country),
                "depth": depth,
                # Метка возвращается провайдером как есть — по ней задача
                # сопоставляется с ключом надёжнее, чем по порядку в ответе.
                "tag": keyword,
            }
            for keyword in keywords
        ]

        body = await self._call("POST", POST_PATH, json=payload)
        self.spent += float(body.get("cost") or 0)

        created, refused = _sort_tasks(body.get("tasks") or [])

        if refused:
            # Отказ провайдера — не пустая выдача. Промолчав, мы получили бы
            # прогон, где половина ключей «ничего не нашла».
            logger.error("выдача: провайдер отклонил задачи (%d): %s", len(refused), refused[:5])
        if not created and refused:
            raise SerpError(f"Провайдер отклонил все задачи пачки: {refused[0]}")
        return created

    async def _collect(
        self, tasks: dict[str, str], out: dict[str, list[SerpResult]], wanted: int
    ) -> None:
        """Забрать готовое. Не дождались — это видно, а не тихий ноль."""
        pending = dict(tasks)
        deadline = cfg.POLL_ATTEMPTS

        for attempt in range(deadline):
            if not pending:
                return
            for keyword, task_id in list(pending.items()):
                items = await self._fetch(task_id)
                if items is None:
                    continue
                out[keyword] = _organic(items, wanted)
                pending.pop(keyword, None)

            if pending and attempt < deadline - 1:
                await asyncio.sleep(cfg.POLL_INTERVAL_S)

        if pending:
            logger.error(
                "выдача: %d задач не дождались за %.0f c — ключи остались без выдачи: %s",
                len(pending),
                deadline * cfg.POLL_INTERVAL_S,
                list(pending)[:5],
            )

    async def _fetch(self, task_id: str) -> list[Any] | None:
        """Результат задачи. `None` — ещё не готово."""
        body = await self._call("GET", f"{GET_PATH}/{task_id}")
        self.spent += float(body.get("cost") or 0)

        tasks = body.get("tasks") or []
        if not tasks or not isinstance(tasks[0], dict):
            raise SerpError(f"Ответ по задаче {task_id} не разобран: нет задач")

        task = tasks[0]
        status = task.get("status_code")
        if status in TASK_IN_QUEUE:
            return None
        if status != TASK_OK:
            logger.error(
                "выдача: задача %s отклонена (%s %s)", task_id, status, task.get("status_message")
            )
            return []

        result = task.get("result")
        if not result:
            # Провайдер отработал и ничего не нашёл — законный исход.
            return []
        return list((result[0] or {}).get("items") or [])

    async def balance(self) -> float:
        """Остаток денег на счету провайдера. Запрос бесплатный.

        Нужен по той же причине, что остаток юнитов у Ahrefs: прогон,
        начатый на пустом счету, отказывает посреди платной работы —
        и выглядит это как «выдача ничего не нашла».

        **Счёт в песочнице тот же, что в бою** — учётка одна. Разница
        только в том, что задачи песочницы бесплатны.
        """
        body = await self._call("GET", BALANCE_PATH)
        tasks = body.get("tasks") or []
        result = (tasks[0].get("result") or [{}])[0] if tasks else {}
        money = result.get("money") if isinstance(result, dict) else None
        if not isinstance(money, dict) or "balance" not in money:
            raise SerpError("В ответе о счёте нет остатка — формат провайдера поменялся")
        return float(money["balance"])

    async def _call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise SerpError(f"Провайдер выдачи недоступен: {exc!r}") from exc

        if response.status_code >= 400:
            raise SerpError(
                f"Провайдер выдачи ответил {response.status_code}: {response.text[:200]}"
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
