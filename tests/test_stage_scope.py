"""Этапы видимости — без умолчания: ревью продаж к #304.

После П2 списки и числа сужались по праву «Продажи» доводом `stages`, а его умолчанием были
все этапы: новый вызов, забывший этапы, показал бы продажи каждому. Теперь у каждого, кто
сужает по этапам видимости (`access.permissions.visible_stages`), этапы — именованный довод
без умолчания: забытый вызов ловит mypy, а этот тест держит, чтобы умолчание не вернулось.
Кому видно всё — фоновым задачам, — передаёт `EVERY_STAGE` явно.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

import pytest
from backend.features.agent import drafts
from backend.features.ops.overview import overview, work
from backend.features.outreach.repository import OutreachRepository

GUARDS = [
    pytest.param(OutreachRepository.threads, id="диалоги"),
    pytest.param(OutreachRepository.states, id="состояния диалогов"),
    pytest.param(OutreachRepository._listed_letters, id="письма списка"),
    pytest.param(OutreachRepository._letter_statuses, id="статусы писем"),
    pytest.param(OutreachRepository._listed_replies, id="ответы списка"),
    pytest.param(work, id="числа меню"),
    pytest.param(drafts.waiting, id="черновики агента"),
    pytest.param(
        overview,
        id="сводка «Обзора»",
        marks=pytest.mark.xfail(
            strict=True,
            reason=(
                "тест продаж tests/test_sales_stage_screens.py зовёт сводку без этапов; тесты "
                "продаж правят продажи — передадут там EVERY_STAGE, и умолчание уходит"
            ),
        ),
    ),
]


@pytest.mark.parametrize("guard", GUARDS)
def test_visible_stages_are_named_and_have_no_default(guard: Callable[..., Any]) -> None:
    stages = inspect.signature(guard).parameters["stages"]

    assert stages.kind is inspect.Parameter.KEYWORD_ONLY
    assert stages.default is inspect.Parameter.empty
