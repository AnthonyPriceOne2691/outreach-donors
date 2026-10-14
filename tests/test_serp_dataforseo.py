"""Адаптер выдачи: проводка отложенного режима.

Отложенный режим — это две фазы и куча мест, где ключ может потеряться
молча: задачу отклонили, результат не дождались, ответ пришёл не в том
порядке. Проверяется именно это, а не «нашлись ли ссылки».
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from backend.features.serp import markets
from backend.features.serp.dataforseo import (
    DataForSeoProvider,
    SerpError,
    UnknownCountryError,
    language_code,
    location_code,
)
from backend.features.serp.modes import SerpMode
from backend.features.serp.protocol import SerpProgress, SerpWatch

pytestmark = pytest.mark.asyncio


#: Цена одной задачи в ответе постановки — у провайдера она есть у каждой задачи.
TASK_COST = 0.003


def _task(task_id: str, keyword: str, *, status: int = 20100) -> dict[str, Any]:
    return {
        "id": task_id,
        "status_code": status,
        "status_message": "Task Created." if status == 20100 else "Отказ",
        "cost": TASK_COST if status == 20100 else 0,
        "data": {"tag": keyword},
    }


def _result(
    items: list[dict[str, Any]], *, status: int = 20000, pages: int | None = None
) -> dict[str, Any]:
    first: dict[str, Any] = {"items": items}
    if pages is not None:
        first["pages_count"] = pages
    return {
        "status_code": 20000,
        "tasks": [{"status_code": status, "status_message": "Ok.", "result": [first]}],
    }


def _organic(position: int, url: str) -> dict[str, Any]:
    return {"type": "organic", "rank_absolute": position, "url": url}


class Provider:
    """Провайдер-заглушка: отвечает по сценарию и считает обращения."""

    def __init__(
        self,
        *,
        results: dict[str, list[dict[str, Any]]] | None = None,
        refuse: set[str] | None = None,
        queue_rounds: int = 0,
        ready: set[str] | None = None,
        pages: dict[str, int] | None = None,
    ) -> None:
        self.results = results or {}
        self.pages = pages or {}
        #: Строки задач, как их поставил адаптер, — по ним видно поля задачи.
        self.payloads: list[dict[str, Any]] = []
        self.refuse = refuse or set()
        self.queue_rounds = queue_rounds
        #: Ключи, задачи по которым готовы сразу, сколько бы ни ждали остальные.
        self.ready = ready or set()
        self.posts = 0
        self.gets: list[str] = []
        self._ids: dict[str, str] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("task_post"):
            self.posts += 1
            rows = json.loads(request.content)
            self.payloads.extend(rows)
            keywords = [row["keyword"] for row in rows]
            tasks = []
            for number, keyword in enumerate(keywords):
                if keyword in self.refuse:
                    tasks.append(_task("", keyword, status=40501))
                    continue
                task_id = f"task-{number}-{keyword}"
                self._ids[task_id] = keyword
                tasks.append(_task(task_id, keyword))
            return httpx.Response(200, json={"status_code": 20000, "cost": 0.05, "tasks": tasks})

        task_id = request.url.path.rsplit("/", 1)[-1]
        self.gets.append(task_id)
        keyword = self._ids.get(task_id, "")

        waiting = keyword not in self.ready and self.gets.count(task_id) <= self.queue_rounds
        if self.queue_rounds and waiting:
            return httpx.Response(
                200,
                json={"status_code": 20000, "tasks": [{"status_code": 40602, "result": None}]},
            )
        return httpx.Response(
            200, json=_result(self.results.get(keyword, []), pages=self.pages.get(keyword))
        )


def _provider(handler: Any, *, mode: SerpMode = SerpMode.STANDARD) -> DataForSeoProvider:
    """Отложенный режим — явно: умолчание сервиса синхронное (`SERP_MODE`), а проводка
    очереди проверяется здесь вся."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://dfs")
    return DataForSeoProvider(client, sandbox=True, login="l", password="p", mode=mode)


class TestWiring:
    async def test_each_keyword_gets_its_own_results(self) -> None:
        """Сопоставление идёт по метке, а не по порядку в ответе: порядок
        провайдер не обещает."""
        site = Provider(
            results={
                "первый": [_organic(1, "https://a.com/")],
                "второй": [_organic(2, "https://b.com/")],
            }
        )
        out = await _provider(site).search(["первый", "второй"], "us")

        assert [r.url for r in out["первый"]] == ["https://a.com/"]
        assert [r.url for r in out["второй"]] == ["https://b.com/"]

    async def test_keyword_without_results_is_present_and_empty(self) -> None:
        """Иначе «по ключу ничего не нашлось» не отличить от «ключ потерялся»."""
        out = await _provider(Provider(results={"пусто": []})).search(["пусто"], "us")
        assert out == {"пусто": []}

    async def test_duplicates_are_asked_once(self) -> None:
        site = Provider(results={"один": [_organic(1, "https://a.com/")]})
        out = await _provider(site).search(["один", "один", " один "], "us")

        assert list(out) == ["один"]
        assert len(site.gets) == 1

    async def test_case_and_inner_spaces_are_no_new_task(self) -> None:
        """Поиску регистр и лишние пробелы не важны, а задача стоит денег: до
        10.10.2026 «Budget Tips» после «budget tips» покупалась второй раз
        (проверка прода). Задача ставится один раз — в первом написании."""
        site = Provider(results={"budget tips": [_organic(1, "https://a.com/")]})

        out = await _provider(site).search(["budget tips", "Budget Tips", "budget   tips"], "us")

        assert list(site._ids.values()) == ["budget tips"]
        assert list(out) == ["budget tips"]
        assert len(site.gets) == 1

    async def test_only_organic_taken(self) -> None:
        """В выдаче есть карты, реклама и «люди также спрашивают» — доноров
        там нет."""
        items = [
            {"type": "paid", "rank_absolute": 1, "url": "https://ad.com/"},
            _organic(2, "https://real.com/"),
            {"type": "people_also_ask", "rank_absolute": 3},
        ]
        out = await _provider(Provider(results={"к": items})).search(["к"], "us")
        assert [r.url for r in out["к"]] == ["https://real.com/"]

    async def test_depth_limits_results(self) -> None:
        items = [_organic(i, f"https://site{i}.com/") for i in range(1, 25)]
        out = await _provider(Provider(results={"к": items})).search(["к"], "us", depth_pages=1)
        assert len(out["к"]) == 10

    async def test_large_batch_is_split(self) -> None:
        """Больше сотни задач за раз провайдер не принимает."""
        site = Provider()
        await _provider(site).search([f"ключ-{i}" for i in range(150)], "us")
        assert site.posts == 2

    async def test_cost_is_counted(self) -> None:
        provider = _provider(Provider(results={"к": []}))
        await provider.search(["к"], "us")
        assert provider.spent > 0


class TestTheBalance:
    """Остаток на счету спрашивается бесплатно — как у Ahrefs.

    Прогон, начатый на пустом счету, отказывает посреди платной работы,
    а выглядит это как «выдача ничего не нашла».
    """

    async def test_balance_is_read(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("user_data")
            return httpx.Response(
                200,
                json={
                    "status_code": 20000,
                    "tasks": [{"status_code": 20000, "result": [{"money": {"balance": 51.9}}]}],
                },
            )

        assert await _provider(handler).balance() == pytest.approx(51.9)

    async def test_changed_format_is_loud(self) -> None:
        """Молчаливый ноль здесь читался бы как «денег нет» и остановил
        бы прогон, которому ничего не мешает."""

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"status_code": 20000, "tasks": [{"result": [{"money": {}}]}]}
            )

        with pytest.raises(SerpError, match="остатка"):
            await _provider(handler).balance()


class TestFailures:
    async def test_refused_task_does_not_hide_the_others(self) -> None:
        site = Provider(results={"живой": [_organic(1, "https://a.com/")]}, refuse={"битый"})
        out = await _provider(site).search(["битый", "живой"], "us")

        assert out["битый"] == []  # отказ виден по логу, а ключ остаётся в ответе
        assert [r.url for r in out["живой"]] == ["https://a.com/"]

    async def test_all_tasks_refused_is_loud(self) -> None:
        site = Provider(refuse={"а", "б"})
        with pytest.raises(SerpError, match="отклонил все задачи"):
            await _provider(site).search(["а", "б"], "us")

    async def test_provider_error_is_loud(self) -> None:
        def broken(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="упало")

        with pytest.raises(SerpError, match="500"):
            await _provider(broken).search(["к"], "us")

    async def test_not_json_is_loud(self) -> None:
        def html(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>заглушка</html>")

        with pytest.raises(SerpError, match="JSON"):
            await _provider(html).search(["к"], "us")

    async def test_unknown_country_refuses_before_spending(self) -> None:
        """Промахнуться страной хуже, чем не начать: соберём доноров
        не того рынка и заплатим за них."""
        site = Provider()
        with pytest.raises(UnknownCountryError, match="не в карте"):
            await _provider(site).search(["к"], "зз")
        assert site.posts == 0

    async def test_zero_depth_refused(self) -> None:
        with pytest.raises(ValueError, match="минимум одна"):
            await _provider(Provider()).search(["к"], "us", depth_pages=0)


class TestWaiting:
    async def test_task_in_queue_is_polled_again(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("backend.features.serp.dataforseo.cfg.POLL_INTERVAL_S", 0)
        site = Provider(results={"к": [_organic(1, "https://a.com/")]}, queue_rounds=2)

        out = await _provider(site).search(["к"], "us")

        assert [r.url for r in out["к"]] == ["https://a.com/"]
        assert len(site.gets) == 3  # два раза «в очереди», третий — готово

    async def test_never_ready_keyword_is_absent_not_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Не дождались — ключа нет в ответе. Пустой список по протоколу значит
        «ничего не нашлось», и до 25.09.2026 недождавшийся ключ выглядел так же:
        выдача оплачена, а в отчёте — неудачный ключ."""
        monkeypatch.setattr("backend.features.serp.dataforseo.cfg.POLL_INTERVAL_S", 0)
        monkeypatch.setattr("backend.features.serp.dataforseo.cfg.POLL_ATTEMPTS", 2)
        site = Provider(
            results={"к": [_organic(1, "https://a.com/")], "м": []},
            queue_rounds=99,
            ready={"м"},
        )

        out = await _provider(site).search(["к", "м"], "us")

        assert "к" not in out
        assert out["м"] == []

    async def test_deeper_serp_is_waited_for_longer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Сто результатов провайдер собирает десятью страницами, и задача
        идёт дольше: окно ожидания растёт с глубиной. Задача, готовая на пятом
        опросе, у топ-10 с окном в два опроса потерялась бы, а у топ-30 —
        дождана."""
        monkeypatch.setattr("backend.features.serp.dataforseo.cfg.POLL_INTERVAL_S", 0)
        monkeypatch.setattr("backend.features.serp.dataforseo.cfg.POLL_ATTEMPTS", 2)

        shallow = Provider(results={"к": [_organic(1, "https://a.com/")]}, queue_rounds=4)
        assert "к" not in await _provider(shallow).search(["к"], "us", depth_pages=1)
        assert len(shallow.gets) == 2

        deep = Provider(results={"к": [_organic(1, "https://a.com/")]}, queue_rounds=4)
        out = await _provider(deep).search(["к"], "us", depth_pages=3)
        assert [r.url for r in out["к"]] == ["https://a.com/"]
        assert len(deep.gets) == 5


class TestCountries:
    async def test_known_countries(self) -> None:
        assert location_code("us") == 2840
        assert location_code(" GB ") == 2826

    async def test_language_follows_country(self) -> None:
        assert language_code("de") == "de"
        assert language_code("br") == "pt"

    async def test_english_is_an_answer_not_a_fallback(self) -> None:
        """Прежнее правило звучало «английский как умолчание безопаснее
        отказа», и оно оказалось неверным: одиннадцать стран из пятидесяти
        пяти молча получали английский — Австрия, Швейцария, Бельгия,
        Саудовская Аравия, Египет и другие, кого просто забыли внести.
        Прогон по ним искал английскую выдачу по английским ключам
        и отчитывался успехом.

        Для США английский — ответ, а не заглушка, и запись у них явная.
        """
        assert language_code("us") == "en"

        with pytest.raises(markets.UnknownMarketError):
            language_code("зз")


class TestWhichRefusalIsRetried:
    """Прогон повторяет временное и останавливается на постоянном
    (`runs/failures.py`). Ошибка выдачи сама говорит, какая она."""

    @pytest.mark.parametrize("code", [500, 503, 429, 408])
    async def test_passing_trouble_is_not_permanent(self, code: int) -> None:
        def answer(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(code, text="временно")

        with pytest.raises(SerpError) as caught:
            await _provider(answer).search(["к"], "us")
        assert not caught.value.permanent, f"{code} проходит сам — прогон стоит продолжить"

    @pytest.mark.parametrize("code", [401, 402, 403, 404])
    async def test_key_money_and_rights_are_permanent(self, code: int) -> None:
        def answer(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(code, text="нельзя")

        with pytest.raises(SerpError) as caught:
            await _provider(answer).search(["к"], "us")
        assert caught.value.permanent, f"{code} повтором не лечится"

    async def test_unknown_country_is_permanent(self) -> None:
        with pytest.raises(SerpError) as caught:
            await _provider(lambda _r: httpx.Response(200)).search(["к"], "zz")
        assert caught.value.permanent


class Live:
    """Синхронный путь провайдера: одна задача на запрос, ответ в том же запросе."""

    def __init__(
        self,
        *,
        results: dict[str, list[dict[str, Any]]] | None = None,
        fail: dict[str, int] | None = None,
        refuse: set[str] | None = None,
    ) -> None:
        self.results = results or {}
        self.fail = fail or {}
        self.refuse = refuse or set()
        self.asked: list[str] = []
        self.paths: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        rows = json.loads(request.content)
        assert len(rows) == 1, "синхронный путь берёт одну задачу на запрос"
        keyword = rows[0]["keyword"]
        self.asked.append(keyword)
        if keyword in self.fail:
            return httpx.Response(self.fail[keyword], text="нельзя")
        task = {
            "status_code": 40501 if keyword in self.refuse else 20000,
            "status_message": "Ok.",
            "cost": 0.01,
            "result": [{"pages_count": 5, "items": self.results.get(keyword, [])}],
        }
        return httpx.Response(200, json={"status_code": 20000, "cost": 0.01, "tasks": [task]})


def _listener() -> tuple[list[SerpProgress], Any]:
    events: list[SerpProgress] = []

    async def hear(event: SerpProgress) -> None:
        events.append(event)

    return events, hear


class TestModes:
    """Режим покупки — цена против ожидания (`serp/modes.py`, проверка на проде 14.10.2026)."""

    async def test_standard_task_asks_no_priority(self) -> None:
        site = Provider(results={"к": []})
        await _provider(site).search(["к"], "us")
        assert "priority" not in site.payloads[0]

    async def test_priority_is_a_field_of_the_same_queue(self) -> None:
        """Приоритетная очередь — то же поле задачи, а не другой путь: около минуты
        вместо пяти, тот же опрос."""
        site = Provider(results={"к": [_organic(1, "https://a.com/")]})
        out = await _provider(site, mode=SerpMode.PRIORITY).search(["к"], "us")

        assert site.payloads[0]["priority"] == 2
        assert [r.url for r in out["к"]] == ["https://a.com/"]

    async def test_live_is_one_request_per_keyword_without_polling(self) -> None:
        site = Live(
            results={"а": [_organic(1, "https://a.com/")], "б": [_organic(1, "https://b.com/")]}
        )
        provider = _provider(site, mode=SerpMode.LIVE)

        out = await provider.search(["а", "б", "А"], "us")

        assert sorted(site.asked) == ["а", "б"]  # повтор ключа — один запрос
        assert all(path.endswith("/live/regular") for path in site.paths)
        assert [r.url for r in out["а"]] == ["https://a.com/"]
        assert [r.url for r in out["б"]] == ["https://b.com/"]
        assert provider.spent == pytest.approx(0.02)

    async def test_live_refused_keyword_stays_in_the_answer_empty(self) -> None:
        out = await _provider(Live(refuse={"битый"}), mode=SerpMode.LIVE).search(["битый"], "us")
        assert out == {"битый": []}

    async def test_live_passing_trouble_loses_only_that_keyword(self) -> None:
        """Временный сбой после повторов — ключ потерян, как недождавшийся в отложенном
        режиме: в ответе его нет, остальные куплены."""
        site = Live(results={"живой": [_organic(1, "https://a.com/")]}, fail={"битый": 503})

        out = await _provider(site, mode=SerpMode.LIVE).search(["битый", "живой"], "us")

        assert "битый" not in out
        assert [r.url for r in out["живой"]] == ["https://a.com/"]

    async def test_live_refusal_for_good_stops_buying(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Деньги, ключ, права — повтором не лечатся: следующие ключи не покупаются."""
        monkeypatch.setattr("backend.features.serp.dataforseo.LIVE_CONCURRENCY", 1)
        site = Live(fail=dict.fromkeys(("а", "б", "в"), 402))

        with pytest.raises(SerpError) as caught:
            await _provider(site, mode=SerpMode.LIVE).search(["а", "б", "в"], "us")

        assert caught.value.permanent
        assert site.asked == ["а"]

    async def test_live_unknown_country_refuses_before_spending(self) -> None:
        site = Live()
        with pytest.raises(UnknownCountryError):
            await _provider(site, mode=SerpMode.LIVE).search(["к"], "зз")
        assert site.asked == []


class TestProgress:
    """Ход покупки — экрану «идёт или зависло»: шесть минут ожидания выдачи экран
    показывал «в очереди задач» и нули (проверка на проде 14.10.2026)."""

    async def test_queue_reports_start_then_each_keyword(self) -> None:
        events, hear = _listener()
        site = Provider(results={"а": [], "б": []}, pages={"а": 5})

        await _provider(site).search(["а", "б"], "us", watch=SerpWatch(on_progress=hear))

        assert events[0] == SerpProgress(done=0, total=2)
        assert [event.done for event in events] == [0, 1, 2]
        first = next(event for event in events if event.keyword == "а")
        assert first.cost_usd == pytest.approx(TASK_COST)
        assert first.pages == 5

    async def test_refused_task_is_an_answer(self) -> None:
        """Отклонённая задача — ответ: ключ в выдаче пуст, и ждать по нему нечего."""
        events, hear = _listener()
        site = Provider(results={"живой": []}, refuse={"битый"})

        await _provider(site).search(["битый", "живой"], "us", watch=SerpWatch(on_progress=hear))

        assert [event.done for event in events] == [0, 1, 2]
        assert events[1].keyword == "битый"

    async def test_live_reports_each_keyword_with_its_price(self) -> None:
        events, hear = _listener()
        site = Live(results={"а": [], "б": []})

        await _provider(site, mode=SerpMode.LIVE).search(
            ["а", "б"], "us", watch=SerpWatch(on_progress=hear)
        )

        assert events[0] == SerpProgress(done=0, total=2)
        assert sorted(event.keyword for event in events[1:]) == ["а", "б"]
        assert all(
            event.cost_usd == pytest.approx(0.01) and event.pages == 5 for event in events[1:]
        )

    async def test_broken_listener_does_not_lose_the_paid_serp(self) -> None:
        """Выдача уже оплачена: сбой записи хода — в журнал, а не в пустой прогон."""

        async def broken(_event: object) -> None:
            raise RuntimeError("база легла")

        site = Provider(results={"к": [_organic(1, "https://a.com/")]})
        watch = SerpWatch(on_progress=broken, on_posted=broken)

        out = await _provider(site).search(["к"], "us", watch=watch)

        assert [r.url for r in out["к"]] == ["https://a.com/"]


class TestTaskNumbers:
    """Номера задач — наружу до опроса: повтор задачи после сбоя опроса забирает
    купленное по номеру, а не покупает выдачу второй раз."""

    async def test_numbers_are_handed_out_before_the_first_poll(self) -> None:
        site = Provider(results={"к": []})
        seen: list[tuple[dict[str, str], int]] = []

        async def keep(tasks: Any) -> None:
            seen.append((dict(tasks), len(site.gets)))

        watch = SerpWatch(on_posted=keep)
        await _provider(site).search(["к"], "us", watch=watch)

        assert seen == [({"к": "task-0-к"}, 0)]  # ни одного опроса до сохранения
        assert watch.tasks == {"к": "task-0-к"}

    async def test_known_number_is_polled_not_bought(self) -> None:
        site = Provider(results={"к": [_organic(1, "https://a.com/")]})
        site._ids["task-old"] = "к"  # задача, купленная прошлой попыткой

        out = await _provider(site).search(["к"], "us", watch=SerpWatch(tasks={"к": "task-old"}))

        assert site.posts == 0
        assert site.gets == ["task-old"]
        assert [r.url for r in out["к"]] == ["https://a.com/"]

    async def test_only_new_keywords_are_posted(self) -> None:
        site = Provider(results={"старый": [], "новый": []})
        site._ids["task-old"] = "старый"
        watch = SerpWatch(tasks={"старый": "task-old"})

        await _provider(site).search(["старый", "новый"], "us", watch=watch)

        assert [row["keyword"] for row in site.payloads] == ["новый"]
        assert set(watch.tasks) == {"старый", "новый"}
