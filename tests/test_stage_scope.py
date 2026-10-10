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
from backend.features.letters import stoplist
from backend.features.ops import mail_watch, silence
from backend.features.ops.overview import overview, work
from backend.features.outreach.repository import OutreachRepository
from backend.features.runs.spending import SpendingRepository

GUARDS = [
    pytest.param(OutreachRepository.threads, id="диалоги"),
    pytest.param(OutreachRepository.states, id="состояния диалогов"),
    pytest.param(OutreachRepository._listed_letters, id="письма списка"),
    pytest.param(OutreachRepository._letter_statuses, id="статусы писем"),
    pytest.param(OutreachRepository._listed_replies, id="ответы списка"),
    pytest.param(OutreachRepository.senders, id="ящики рассылки"),
    pytest.param(OutreachRepository.enabled_domains, id="включённые домены"),
    pytest.param(work, id="числа меню"),
    pytest.param(drafts.waiting, id="черновики агента"),
    pytest.param(stoplist.rows, id="стоп-лист"),
    pytest.param(SpendingRepository.since_month_start, id="расход"),
    pytest.param(silence.alarms, id="сторож тишины"),
    pytest.param(mail_watch.alarms, id="сторож почты"),
    pytest.param(overview, id="сводка «Обзора»"),
]


@pytest.mark.parametrize("guard", GUARDS)
def test_visible_stages_are_named_and_have_no_default(guard: Callable[..., Any]) -> None:
    stages = inspect.signature(guard).parameters["stages"]

    assert stages.kind is inspect.Parameter.KEYWORD_ONLY
    assert stages.default is inspect.Parameter.empty
