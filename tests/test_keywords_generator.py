"""Сборка пула: деление по углам, добор, остановка.

Настоящая модель здесь не зовётся. Проверять надо не её, а наши решения:
сколько просим у каждого угла, когда добираем, когда прекращаем — и что
потолок считается после дедупликации, а не до.
"""

from __future__ import annotations

import httpx
import pytest
from backend.features.keywords.angles import UnknownPresetError, load_prompt, preset
from backend.features.keywords.client import Ask, KeygenClient, LlmError, build_payload
from backend.features.keywords.generator import PoolBuilder

pytestmark = pytest.mark.asyncio


class FakeClient:
    """Модель-заглушка: отдаёт заранее заданные ответы по кругу."""

    def __init__(self, answers: list[list[str]]) -> None:
        self.answers = answers
        self.asked: list[int] = []  # сколько фраз просили каждый раз
        self.tokens_spent = 0
        self.calls = 0
        self.refusals: list[str] = []

    async def ask(self, ask: Ask) -> list[str]:
        self.asked.append(ask.max_phrases)
        self.calls += 1
        self.tokens_spent += 100
        if not self.answers:
            return []
        return self.answers.pop(0)


class RefusingClient:
    """Модель, которая не отвечает: так выглядит протухший ключ.

    Заведена потому, что прежняя заглушка умела только «модель ответила
    пустым», а отказ и пустой ответ давали один и тот же `[]` — и отчёт
    называл неработающий ключ исчерпанной фантазией.
    """

    def __init__(self) -> None:
        self.asked: list[int] = []
        self.tokens_spent = 0
        self.calls = 0
        self.refusals: list[str] = []

    async def ask(self, ask: Ask) -> list[str]:
        self.asked.append(ask.max_phrases)
        self.calls += 1
        self.refusals.append("модель, отказ (чинить): HTTP 401: ключ не принят")
        return []


class TestModelRefusal:
    async def test_permanent_refusal_stops_asking(self) -> None:
        """Живой прогон с протухшим ключом сделал двенадцать обречённых
        вызовов — по углу на каждый круг добора, и каждый ещё с повторами.

        Отказ, названный неисправимым, повторять незачем: считаем его
        всем углам, но по сети идём один раз.
        """
        transport = httpx.MockTransport(
            lambda request: httpx.Response(401, json={"error": {"message": "ключ не принят"}})
        )
        async with httpx.AsyncClient(transport=transport) as http:
            client = KeygenClient(http, api_key="k")
            with pytest.raises(LlmError):
                await PoolBuilder(client).build(cap=10, country="US")

        assert client.calls == 1, "по сети сходили больше одного раза"
        assert len(client.refusals) > 1, "отказ не посчитан остальным углам"

    async def test_empty_pool_from_refusals_is_loud(self) -> None:
        """Пустой пул из-за отказов — ошибка, а не результат: пул собирается
        ДО траты на выдачу, и молчаливый ноль пускает прогон идти ни за чем."""
        builder = PoolBuilder(RefusingClient())  # type: ignore[arg-type]

        with pytest.raises(LlmError, match="модель отказала"):
            await builder.build(cap=10, country="US")

    async def test_empty_pool_without_refusals_stays_a_result(self) -> None:
        """Модель отвечала, фразы не прошли отбор — это законный исход,
        и ошибкой он становиться не должен."""
        pool = await PoolBuilder(FakeClient([])).build(cap=10, country="US")  # type: ignore[arg-type]

        assert pool.keywords == []
        assert pool.report.refusals == []

    async def test_refusals_are_counted_in_the_report(self) -> None:
        """Доля «не знаю» — отдельное число в отчёте, а не отсутствие записи."""
        client = FakeClient([_phrases("тема", 10)])
        client.refusals = ["модель, сеть (можно повторить): обрыв"]

        pool = await PoolBuilder(client).build(cap=3, country="US")  # type: ignore[arg-type]

        assert pool.keywords
        assert pool.report.as_dict()["refusals"] == 1


def _phrases(prefix: str, count: int) -> list[str]:
    """Заведомо непохожие фразы: общих слов у них нет, и дедуп их не тронет.

    Первая версия этого помощника делала фразы вида «тема запрос номер N
    слово» — они отличались одним словом из пяти, дедуп схлопывал их
    в одну, и тесты падали. Это ровно то, для чего дедуп и написан.
    """
    return [f"{prefix}{i}альфа {prefix}{i}бета" for i in range(count)]


class TestSplit:
    async def test_every_angle_is_asked(self) -> None:
        client = FakeClient([_phrases(f"тема{i}", 10) for i in range(4)])
        pool = await PoolBuilder(client).build(  # type: ignore[arg-type]
            cap=8, country="Philippines", preset_name="media"
        )

        assert client.calls == 4  # четыре угла пресета
        assert len(pool.report.per_angle) == 4
        assert len(pool.keywords) == 8

    async def test_asks_with_a_buffer(self) -> None:
        """Просим больше, чем нужно: часть отсеют гигиена и дедуп."""
        client = FakeClient([_phrases("a", 20)])
        await PoolBuilder(client).build(cap=4, country="X", preset_name="reviews")  # type: ignore[arg-type]

        assert client.asked[0] > 2

    async def test_zero_cap_asks_nothing(self) -> None:
        client = FakeClient([])
        pool = await PoolBuilder(client).build(cap=0, country="X")  # type: ignore[arg-type]

        assert pool.keywords == []
        assert client.calls == 0


class TestBackfill:
    async def test_shortfall_is_topped_up(self) -> None:
        """Первый заход недодал — добираем, а не отдаём неполный пул."""
        client = FakeClient(
            [_phrases("раз", 1), _phrases("два", 1), _phrases("три", 1)]
            + [_phrases(f"добор{i}", 4) for i in range(6)]
        )
        pool = await PoolBuilder(client).build(cap=9, country="X", preset_name="guides")  # type: ignore[arg-type]

        assert pool.report.rounds >= 1
        assert len(pool.keywords) == 9

    async def test_stops_when_model_runs_dry(self) -> None:
        """Ни один угол не дал новой фразы — крутить модель дальше значит
        платить за повторы, которые всё равно выбросит дедуп."""
        client = FakeClient([_phrases("одно", 2), [], [], [], [], [], [], [], []])
        pool = await PoolBuilder(client).build(cap=50, country="X", preset_name="guides")  # type: ignore[arg-type]

        assert len(pool.keywords) < 50
        assert client.calls <= 3 + 3  # три угла плюс один круг добора

    async def test_cap_counted_after_deduplication(self) -> None:
        """Иначе пул выглядит набранным, а после отсева почти-дублей
        оказывается меньше заказанного."""
        duplicates = ["новости манилы", "последние новости манилы", "новости манилы сегодня"]
        client = FakeClient([duplicates, _phrases("иные", 5), _phrases("другие", 5)])

        pool = await PoolBuilder(client).build(cap=6, country="X", preset_name="guides")  # type: ignore[arg-type]

        assert len(pool.keywords) == len(set(pool.keywords))
        assert pool.report.near_duplicates >= 1


class TestReport:
    async def test_report_explains_the_result(self) -> None:
        client = FakeClient([["хорошая фраза раз", "site:site.com мусор"], [], []])
        pool = await PoolBuilder(client).build(cap=3, country="Philippines", preset_name="guides")  # type: ignore[arg-type]

        report = pool.report
        assert report.asked > 0
        assert report.received == 2
        assert any("оператор" in reason for reason in report.rejected.values())
        assert report.tokens > 0
        assert report.country == "Philippines"


class TestPresets:
    async def test_known_presets_have_angles(self) -> None:
        for name in ("media", "reviews", "guides", "wide"):
            assert preset(name)

    async def test_unknown_preset_lists_the_known(self) -> None:
        """Оператор выбирает из готовых: угол — часть промпта, и неудачная
        формулировка обнаружится после оплаченной выдачи."""
        with pytest.raises(UnknownPresetError, match="media"):
            preset("такого-нет")

    async def test_every_angle_has_its_prompt(self) -> None:
        for angles in (preset("media"), preset("reviews"), preset("guides"), preset("wide")):
            for angle in angles:
                assert "{max_phrases}" in load_prompt(angle.prompt)


class TestPayload:
    async def test_reasoning_model_gets_its_own_parameters(self) -> None:
        """Перепутать нельзя: провайдер отвечает отказом, а не догадкой."""
        payload = build_payload("gpt-5", Ask(system="s", user="u", max_phrases=30))

        assert "max_completion_tokens" in payload
        assert payload["reasoning_effort"] == "minimal"
        assert "temperature" not in payload

    async def test_plain_model_gets_temperature(self) -> None:
        payload = build_payload("gpt-4.1-mini", Ask(system="s", user="u", max_phrases=30))

        assert payload["temperature"] == 0
        assert "max_tokens" in payload
        assert "reasoning_effort" not in payload


class TestClientFailures:
    async def test_model_refusal_is_not_a_crash(self) -> None:
        """Один отказ не должен отменять весь пул: угол остаётся без фраз,
        а причина видна в логе."""

        def refuse(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, text="слишком часто")

        http = httpx.AsyncClient(transport=httpx.MockTransport(refuse))
        client = KeygenClient(http, model="gpt-5", api_key="k")

        assert await client.ask(Ask(system="s", user="u", max_phrases=5)) == []

    async def test_missing_key_says_what_to_do(self) -> None:
        client = KeygenClient(httpx.AsyncClient(), model="gpt-5", api_key="")
        with pytest.raises(LlmError, match="LLM_API_KEY"):
            await client.ask(Ask(system="s", user="u", max_phrases=5))

    async def test_tokens_are_counted(self) -> None:
        def answer(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": '["раз", "два"]'}}],
                    "usage": {"total_tokens": 42},
                },
            )

        http = httpx.AsyncClient(transport=httpx.MockTransport(answer))
        client = KeygenClient(http, model="gpt-5", api_key="k")

        assert await client.ask(Ask(system="s", user="u", max_phrases=5)) == ["раз", "два"]
        assert client.tokens_spent == 42
