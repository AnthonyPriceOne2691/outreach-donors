"""Упор бюджета: смета и отказ запуска называют, во что упёрлись, и советуют своё.

16.10.2026: смета и отказ при запуске советовали «поднимите кап», когда кончался ключ
Ahrefs, — кап поднимали зря, бюджет всё равно держал остаток ключа. Теперь упор назван:
лимит ключа или пространства Ahrefs (поднимают в Ahrefs), наш кап («Расход») или потолок
прогона (форма). Поровну — жёсткий раньше: подъём мягкого при равном жёстком не помог бы.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from backend.api.runs.schemas import Forecast, _binding
from backend.features.ahrefs.units import Quota
from backend.features.runs.ahrefs_cap import KeyCap
from backend.features.runs.ahrefs_key import Cycle, KeySnapshot
from backend.features.runs.budget import LIMIT_WORDS, Budget, CapExceededError, budget_of
from backend.features.runs.estimate import forecast
from backend.features.runs.planning import plan_run
from backend.features.serp.modes import SerpMode
from tests import test_pipeline as pipeline_tests


class _Client:
    """Ответ Ahrefs об остатке — какой задан."""

    def __init__(self, payload: dict[str, int | None]) -> None:
        self._payload = payload

    async def limits_and_usage(self) -> dict[str, int | None]:
        return self._payload


def _payload(*, key_left: int | None, workspace_left: int | None) -> dict[str, int | None]:
    return {
        "units_limit_api_key": None if key_left is None else 20_000,
        "units_usage_api_key": None if key_left is None else 20_000 - key_left,
        "units_limit_workspace": None if workspace_left is None else 500_000,
        "units_usage_workspace": None if workspace_left is None else 500_000 - workspace_left,
    }


class TestForecastBinding:
    def test_provider_cap_and_ceiling_each_bind_when_smallest(self) -> None:
        def binding(left: int, cap: int, ceiling: int | None = None) -> str:
            return forecast(
                keywords=1, depth_pages=1, units_left=left, units_cap=cap, run_ceiling=ceiling
            ).binding

        assert binding(left=100, cap=9_000) == "provider"
        assert binding(left=9_000, cap=100) == "cap"
        assert binding(left=9_000, cap=8_000, ceiling=50) == "ceiling"

    def test_ties_go_to_the_harder_limit(self) -> None:
        """Ключ и кап поровну — ключ: подъём капа не помог бы; кап и потолок — кап."""
        assert forecast(keywords=1, depth_pages=1, units_left=500, units_cap=500).binding == (
            "provider"
        )
        tie = forecast(keywords=1, depth_pages=1, units_left=900, units_cap=500, run_ceiling=500)
        assert tie.binding == "cap"


class TestShortOf:
    def test_each_limit_short_is_named_hard_first(self) -> None:
        def short(left: int, cap: int, ceiling: int | None = None) -> list[str]:
            return forecast(
                keywords=1, depth_pages=1, units_left=left, units_cap=cap, run_ceiling=ceiling
            ).short_of

        assert short(left=1, cap=10**6) == ["provider"]
        assert short(left=10**6, cap=1) == ["cap"]
        assert short(left=1, cap=1) == ["provider", "cap"]
        assert short(left=10**6, cap=10**6, ceiling=1) == ["ceiling"]
        assert short(left=10**6, cap=10**6) == []


class TestQuotaTighter:
    @pytest.mark.parametrize(
        ("key_left", "workspace_left", "tighter"),
        [
            (3_000, 400_000, "key"),
            (3_000, 1_000, "workspace"),
            (3_000, 3_000, "key"),
            (None, 1_000, "workspace"),
            (3_000, None, "key"),
            (None, None, "key"),
        ],
    )
    def test_which_ahrefs_limit_ends_first(
        self, key_left: int | None, workspace_left: int | None, tighter: str
    ) -> None:
        quota = Quota.from_payload(_payload(key_left=key_left, workspace_left=workspace_left))
        assert quota.tighter == tighter


class TestBudgetOf:
    async def test_cap_below_free_binds_as_ours(self) -> None:
        client = _Client(_payload(key_left=9_000, workspace_left=400_000))
        found = await budget_of(client, cap=500)  # type: ignore[arg-type]
        assert (found.units, found.binding) == (500, "cap")
        assert found.shortage(600) == LIMIT_WORDS["cap"]
        assert "кап поднимают на экране «Расход»" in found.shortage(600)

    async def test_key_binds_and_says_raise_the_key(self) -> None:
        client = _Client(_payload(key_left=700, workspace_left=400_000))
        found = await budget_of(client, cap=5_000, claimed=200)  # type: ignore[arg-type]
        assert (found.units, found.binding) == (500, "key")
        assert found.shortage(600) == LIMIT_WORDS["key"]
        assert "увеличьте лимит ключа в самом Ahrefs — кап здесь не поможет" in found.shortage(600)

    async def test_short_of_both_asks_to_check_both(self) -> None:
        """Слово владельца 16.10.2026: «если и то, и другое — проверьте лимиты ключа и капа»."""
        client = _Client(_payload(key_left=700, workspace_left=400_000))
        found = await budget_of(client, cap=5_000)  # type: ignore[arg-type]
        assert found.shortage(6_000) == LIMIT_WORDS["both"]
        assert "проверьте лимит ключа в Ahrefs и кап" in found.shortage(6_000)

    async def test_equal_cap_and_key_name_the_key(self) -> None:
        client = _Client(_payload(key_left=700, workspace_left=None))
        found = await budget_of(client, cap=700)  # type: ignore[arg-type]
        assert found.binding == "key"

    async def test_workspace_binds_when_it_ends_first(self) -> None:
        client = _Client(_payload(key_left=9_000, workspace_left=300))
        found = await budget_of(client)  # type: ignore[arg-type]
        assert (found.units, found.binding) == (300, "workspace")
        assert found.shortage(400) == LIMIT_WORDS["workspace"]


class TestPlanRefusal:
    async def test_refusal_says_what_is_short_and_keeps_the_trim_advice(self) -> None:
        budget = Budget(free=100, cap=None, side="key")
        with pytest.raises(CapExceededError) as caught:
            await plan_run(
                pipeline_tests.TestPlanning.CANDIDATES,
                pipeline_tests.FakeFreshness(set()),
                units_left=budget.units,
                limit=budget.shortage,
            )
        words = str(caught.value)
        assert "доступно 100" in words
        assert "не хватает лимита ключа Ahrefs: увеличьте лимит ключа в самом Ahrefs" in words
        assert words.endswith("Или сократите список ключей или глубину выдачи.")
        assert "поднимите кап" not in words


def _cap(quota: Quota) -> KeyCap:
    moment = datetime(2026, 10, 25, tzinfo=UTC)
    return KeyCap(
        chosen=None,
        key=KeySnapshot(quota=quota, asked_at=moment),
        cycle=Cycle(
            start=datetime(2026, 10, 21, tzinfo=UTC),
            next_reset=datetime(2026, 11, 21, tzinfo=UTC),
            by_key=True,
        ),
        spent=0,
    )


class TestScreenBinding:
    def test_provider_names_the_tighter_ahrefs_limit(self) -> None:
        made = forecast(keywords=1, depth_pages=1, units_left=100, units_cap=20_000)
        key = Quota.from_payload(_payload(key_left=100, workspace_left=400_000))
        workspace = Quota.from_payload(_payload(key_left=9_000, workspace_left=100))
        assert _binding(made, _cap(key)) == "key"
        assert _binding(made, _cap(workspace)) == "workspace"

    def test_ours_pass_through_and_unknown_cap_is_none(self) -> None:
        quota = Quota.from_payload(_payload(key_left=9_000, workspace_left=400_000))
        capped = forecast(keywords=1, depth_pages=1, units_left=9_000, units_cap=100)
        unknown = forecast(keywords=1, depth_pages=1, units_left=9_000, units_cap=None)
        assert _binding(capped, _cap(quota)) == "cap"
        assert _binding(unknown, _cap(quota)) is None

    def test_screen_short_of_names_the_ahrefs_side(self) -> None:
        made = forecast(keywords=1, depth_pages=1, units_left=1, units_cap=1)
        quota = Quota.from_payload(_payload(key_left=9_000, workspace_left=1))
        view = Forecast.of(made, serp_mode=SerpMode.LIVE, cap=_cap(quota))
        assert view.short_of == ["workspace", "cap"]
        assert view.next_reset == datetime(2026, 11, 21, tzinfo=UTC)
