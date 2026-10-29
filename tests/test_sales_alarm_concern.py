"""Слова пометок тревог продаж: те же правила, что у всех тревог.

Что пометка есть у каждой тревоги, держит общая защита (`test_alarm_concern.py`), какая именно —
проверки самих тревог рядом с их сценариями. Здесь — что слова короткие, человеческие и что
таблицы пометок покрывают всё, что могут спросить.
"""

from __future__ import annotations

import pytest
from backend.features.ops.alarms import Concern
from backend.features.sales import (
    autobuild_alarm,
    autosend_jobs,
    handoff_console,
    handoff_kommo,
    handoff_telegram,
    loops,
    weekly,
    weekly_jobs,
)
from backend.features.sales.agent import notify
from backend.features.sales.models import HandoffKommo
from tests.test_alarm_concern import _plain

MARKS = (
    autobuild_alarm.EMPTY,
    autosend_jobs.STALLED,
    *handoff_kommo.REFUSALS.values(),
    handoff_kommo.RECOVERED,
    handoff_kommo.UNNOTED,
    *handoff_telegram.CONCERNS.values(),
    loops.CRASHED,
    loops.MISSET,
    notify.NO_BOT,
    notify.NOBODY_WAITS,
    notify.UNTOLD,
    weekly.BOX_BACK,
    weekly.DRAFTS_UNTOLD,
    weekly.LEADS_LOST,
    weekly.NOT_READY,
    weekly_jobs.ON_SCREEN,
)


@pytest.mark.parametrize("mark", MARKS, ids=lambda mark: mark.why)
def test_a_sales_mark_is_short_and_plain(mark: Concern) -> None:
    _plain(mark)


def test_every_kommo_refusal_a_handoff_can_end_in_has_its_mark() -> None:
    """Отказ Kommo ведёт в эти три состояния, а передачи, которые ждут человека, — их часть: у
    недельного отчёта и у тревоги отказа нет случая без пометки."""
    assert set(handoff_kommo.REFUSALS) == {
        HandoffKommo.RETRY,
        HandoffKommo.UNCONFIRMED,
        HandoffKommo.FAILED,
    }
    assert set(handoff_console.WAITING) <= set(handoff_kommo.REFUSALS)


def test_only_a_retry_of_kommo_is_no_cause() -> None:
    """Повтор идёт сам; неподтверждённую запись и отказ решает человек."""
    causes = {state for state, mark in handoff_kommo.REFUSALS.items() if mark.cause}

    assert causes == {HandoffKommo.UNCONFIRMED, HandoffKommo.FAILED}


def test_a_lost_personal_message_is_a_cause_and_a_lost_group_copy_is_not() -> None:
    """Копия уходит после личного: телемаркетолог лида уже получил."""
    assert handoff_telegram.CONCERNS[handoff_telegram.PERSONAL].cause
    assert not handoff_telegram.CONCERNS[handoff_telegram.GROUP].cause
