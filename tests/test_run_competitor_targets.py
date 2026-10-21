"""Домены конкурентов прогона — одним правилом для истории прогонов и резерва (`RunModel.competitor_targets`).

Проверка прода 21.10.2026: резерв прогона по конкурентам писал «ключей 0» — схема резерва не знала, что
прогон шёл по ссылкам конкурентов. Теперь обе схемы берут домены из модели.
"""

from __future__ import annotations

from backend.features.core.models.run import RunModel


def test_a_competitor_run_names_its_targets() -> None:
    run = RunModel(competitors={"targets": ["rival.example", "other.example"], "depth": 50})

    assert run.competitor_targets == ["rival.example", "other.example"]


def test_a_keyword_run_has_none() -> None:
    assert RunModel(competitors=None).competitor_targets is None
    assert RunModel(competitors={"targets": []}).competitor_targets is None
