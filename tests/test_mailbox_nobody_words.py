"""«Сегодня писать некому» называет все причины — и годные ящики, выбравшие дневной лимит.

Проверка на проде 22.10.2026: пачка продаж встала с «ящику снижен лимит на сутки (мягкие
отказы): … — 2 из 2», а четыре других ящика молча выбрали первую ступень разгона (5 из 5).
Отказ выглядел поломкой одного ящика, хотя это рост объёма новых ящиков. Теперь отказ
называет и ступень разгона, и отсеянного лимитами (`letters/mailbox._nobody`), и тем же
правилом — перезапрос цены у доноров (`letters/recheck_target._nobody`).

Ящики — в памяти, без базы: слова собираются из ступени разгона и итога фильтра лимитов.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from backend.features.core.domain import SenderStatus, Stage
from backend.features.core.models.outreach import SenderModel
from backend.features.letters import mailbox, recheck_target
from backend.features.outreach import limits, senders

#: Ступени разгона — выдуманные: тест не зависит от настроек окружения.
STEPS = (5, 10, 15, 20)
NOW = datetime(2026, 10, 22, 8, 41, 17, tzinfo=UTC)


@pytest.fixture(autouse=True)
def steps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(senders, "WARMUP_STEPS", STEPS)
    monkeypatch.setattr(senders, "WARMUP_STEP_DAYS", 2)


def _box(number: int, *, first_letter: datetime | None) -> SenderModel:
    """Ящик продаж с капом 20: `first_letter` — первое письмо разгона; `None` — разгона нет."""
    return SenderModel(
        id=number,
        domain=f"mail-{number}.example.test",
        email=f"alex@mail-{number}.example.test",
        stage=Stage.SALES,
        daily_cap=20,
        status=SenderStatus.FREE,
        enabled=True,
        warmup_started_at=None if first_letter is None else first_letter - timedelta(hours=1),
        warmup_first_sent_at=first_letter,
    )


def test_the_warmup_step_and_the_cut_box_are_both_named() -> None:
    """Случай с прода: четыре ящика на первой ступени разгона, пятому лимит снижен."""
    fit = tuple(_box(n, first_letter=NOW - timedelta(hours=2)) for n in range(1, 5))
    cut = f"{limits.BOX_CUT}: alex@mail-5.example.test — 2 из 2"

    words = mailbox._nobody(limits.Screened(fit=fit, refused={5: cut}), NOW)

    assert words == (
        "Сегодня писать некому: 4 ящика выбрали дневной лимит разгона — 5 писем на ящик; "
        f"{cut}. Письмо остаётся в очереди — завтра лимит откроется заново"
    )


def test_steps_are_grouped_and_a_finished_warmup_is_a_plain_daily_limit() -> None:
    """Разные ступени — своими группами; ящик без разгона — просто дневной лимит (его кап)."""
    fit = (
        _box(1, first_letter=NOW - timedelta(days=2)),  # третий день — вторая ступень
        _box(2, first_letter=NOW - timedelta(days=3)),  # четвёртый день — та же ступень
        _box(3, first_letter=None),
    )

    words = mailbox._nobody(limits.Screened(fit=fit), NOW)

    assert words == (
        "Сегодня писать некому: 2 ящика выбрали дневной лимит разгона — 10 писем на ящик; "
        "1 ящик выбрал дневной лимит — 20 писем. "
        "Письмо остаётся в очереди — завтра лимит откроется заново"
    )


def test_only_refused_boxes_promise_no_tomorrow() -> None:
    """Годных ящиков нет — отказ называет только отсеянных, «завтра» не обещает, как прежде."""
    words = mailbox._nobody(limits.Screened(fit=(), refused={1: limits.DOMAIN_PAUSED}), NOW)

    assert words == f"Сегодня писать некому: {limits.DOMAIN_PAUSED}. Письмо остаётся в очереди"


def test_the_price_recheck_names_the_same_reasons() -> None:
    """Перезапрос цены у доноров — тем же правилом: ступень разгона и отсеянный лимитами."""
    fit = (_box(1, first_letter=NOW - timedelta(hours=2)),)
    cut = f"{limits.BOX_CUT}: alex@mail-2.example.test — 2 из 2"

    words = recheck_target._nobody(limits.Screened(fit=fit, refused={2: cut}), NOW)

    assert words == (
        "Сегодня писать некому: 1 ящик выбрал дневной лимит разгона — 5 писем; "
        f"{cut}. Перезапрос новым письмом уйдёт, когда у ящиков откроется лимит."
    )


@pytest.mark.parametrize(
    ("count", "word"),
    [
        (1, "ящик"),
        (2, "ящика"),
        (4, "ящика"),
        (5, "ящиков"),
        (11, "ящиков"),
        (12, "ящиков"),
        (21, "ящик"),
        (22, "ящика"),
        (111, "ящиков"),
    ],
)
def test_the_word_agrees_with_the_number(count: int, word: str) -> None:
    assert mailbox._word(count, "ящик", "ящика", "ящиков") == word
