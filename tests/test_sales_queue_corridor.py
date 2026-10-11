"""Письмо продаж в коридоре отличия: подбор зон после одного вызова, запасной путь — повтор с
подсказкой, отличие на виду.

Пилот 11.10: шаблон первого письма целиком `rewrite` — три сборки подряд «вне коридора
отличия — ждёт следующей сборки», числа нигде нет; живые прогоны того же дня — модель
переписывает такие зоны почти целиком. Здесь — сборка на настоящей базе (мир
`test_sales_send_world.py`) со своей цепочкой гипотезы: первое письмо из слов-меток, 100 слов,
у каждой зоны свои. Подделка модели переписывает то, что велит тест (`w.ZoneRewriter`), и
отличие считается ровно: переписанные слова, делённые на сто. Тексты и адреса выдуманы.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

import pytest
from backend.config import llm as llm_cfg
from backend.config import sales as sales_cfg
from backend.features.core.models.ops import UsageRecordModel
from backend.features.core.models.outreach import MessageModel
from backend.features.letters import guards
from backend.features.letters.uniqueness import corridor_verdict
from backend.features.sales import chain, chain_text, corridor, letter, queue
from backend.features.sales.models import SalesThreadModel
from backend.features.sales.usage_cap import LETTER_REWRITE
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w

JANE = "jane@acme.example.test"
OLGA = "olga@beta.example.test"
#: Токенов на вызов у подделки.
TOKENS = 37
OFF = "вне коридора отличия после"


def _words(name: str, count: int) -> str:
    return " ".join(f"{name}{index}" for index in range(count))


def _body(*rewrite: tuple[str, int], fixed: int) -> str:
    zones = [f"[{name}] rewrite\n{_words(name, count)}" for name, count in rewrite]
    return "\n\n".join([*zones, f"[offer] fixed\n{_words('offer', fixed)}"])


#: Четыре зоны `rewrite` — 40 слов из 100. Целиком переписанные наборы в коридоре:
#: {intro} 20%, {greeting, intro} 22%, {aside, close} 18%, {greeting, aside, close} 20%.
FOUR = _body(("greeting", 2), ("intro", 20), ("aside", 10), ("close", 8), fixed=60)
FITTING = {("intro",), ("greeting", "intro"), ("aside", "close"), ("greeting", "aside", "close")}
#: Одна зона `rewrite` — 40 слов из 100: выбор только «всё» — или шаблон.
SINGLE = _body(("intro", 40), fixed=60)
EVERYTHING = {"greeting": 1.0, "intro": 1.0, "aside": 1.0, "close": 1.0}


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    return await w.world(session, monkeypatch)


async def _first_step(session: AsyncSession, world: w.World, body: str) -> None:
    """Своя цепочка гипотезы: первое письмо из слов-меток, добивки — мира продаж."""
    made = [chain_text.step_template(step=1, language="en", subject="Made-up test", body=body)]
    made += [chain_text.step_template(step=n, language="en", body=w.FOLLOW_BODY[n]) for n in (2, 3)]
    for new in made:
        await chain.save(
            session, new, hypothesis_id=world.hypothesis_id, author="тест", author_id=None
        )


async def _build(session: AsyncSession, world: w.World, rewriter: Any) -> queue.QueueReport:
    return await queue.build(session, rewriter, hypothesis_id=world.hypothesis_id, limit=10)


async def _letters(session: AsyncSession) -> list[MessageModel]:
    return list(await session.scalars(select(MessageModel).order_by(MessageModel.id)))


async def _spent(session: AsyncSession) -> list[int | None]:
    """Строки журнала расхода переписывания продаж."""
    rows = await session.scalars(
        select(UsageRecordModel.units)
        .where(UsageRecordModel.operation == LETTER_REWRITE)
        .order_by(UsageRecordModel.id)
    )
    return list(rows)


# --- главный путь: подбор зон после одного вызова -----------------------------------------


async def test_one_call_and_the_set_of_zones_that_lands_in_the_corridor(
    session: AsyncSession, world: w.World
) -> None:
    """Модель переписала вступление и концовку: письмо с обеими — 28%, за коридором; одно
    вступление — 20%. В очередь — оно, концовка — текстом шаблона, вызов модели один."""
    await _first_step(session, world, FOUR)
    jane = await w.lead(session, world.hypothesis_id, JANE)
    model = w.ZoneRewriter({"intro": 1, "close": 1})

    report = await _build(session, world, model)

    assert (len(model.seen), model.hints) == (1, [None])
    [sent] = await _letters(session)
    assert sent.uniqueness_pct == pytest.approx(0.20)
    assert sent.body is not None
    assert "intronew0" in sent.body
    assert "closenew0" not in sent.body
    assert "close0" in sent.body
    assert report.letters == [
        corridor.LetterLine(jane.id, "Jane Example", 1, sent.uniqueness_pct, ("intro",), True)
    ]
    assert report.notes == Counter(
        {"зона «close» оставлена шаблонной подбором под коридор отличия": 1}
    )


async def test_model_that_rewrote_everything_still_lands_with_one_call(
    session: AsyncSession, world: w.World
) -> None:
    """Как пилот: модель переписала все зоны — письмо целиком 40%. Подбор кладёт его в коридор
    тем же вызовом; какой из четырёх наборов — решает зерно лида."""
    await _first_step(session, world, FOUR)
    await w.lead(session, world.hypothesis_id, JANE)
    model = w.ZoneRewriter(EVERYTHING)

    report = await _build(session, world, model)

    [line] = report.letters
    assert (len(model.seen), report.prepared, line.attempts, line.queued) == (1, 1, 1, True)
    assert line.zones in FITTING
    assert corridor_verdict(line.difference) is None


async def test_letters_of_different_leads_take_different_sets(
    session: AsyncSession, world: w.World
) -> None:
    """Зерно подбора — номер лида: у писем разных лидов шаблонными остаются разные зоны, и
    одинаковые абзацы не идут всей пачкой; сборка того же лида повторима (`letters/selection`)."""
    await _first_step(session, world, FOUR)
    for n in range(12):
        await w.lead(session, world.hypothesis_id, f"lead{n}@site{n}.example.test")

    report = await _build(session, world, w.ZoneRewriter(EVERYTHING))

    assert len(report.letters) == 10  # предел сборки
    assert {line.zones for line in report.letters} <= FITTING
    assert len({line.zones for line in report.letters}) > 1


# --- запасной путь: новый вызов с подсказкой --------------------------------------------------


async def test_nothing_fits_below_the_corridor_new_call_hears_change_more(
    session: AsyncSession, world: w.World
) -> None:
    """Модель почти не правила: приветствие и концовка — 10% всем переписанным, ни один набор
    не в коридоре. Второй вызов слышит «слишком близко — 10%: меняй больше» и ложится."""
    await _first_step(session, world, FOUR)
    await w.lead(session, world.hypothesis_id, JANE)
    model = w.ZoneRewriter({"greeting": 1, "close": 1}, {"intro": 1, "close": 1})

    report = await _build(session, world, model)

    first, told = model.hints
    assert first is None
    assert told == corridor.hint(0.10)
    assert told is not None
    assert "too close to the original" in told
    assert "by only 10%" in told
    assert (report.prepared, report.letters[0].attempts, report.letters[0].zones) == (
        1,
        2,
        ("intro",),
    )


async def test_one_rewrite_zone_is_all_or_nothing_and_only_the_new_call_helps(
    session: AsyncSession, world: w.World
) -> None:
    """Одна зона `rewrite` на 40% письма: выбор только «всё» — 40%, за коридором. Второй вызов
    слышит «слишком далеко — 40%: держись ближе к исходным словам» и переписывает половину."""
    await _first_step(session, world, SINGLE)
    await w.lead(session, world.hypothesis_id, JANE)
    model = w.ZoneRewriter({"intro": 1}, {"intro": 0.5})

    report = await _build(session, world, model)

    told = model.hints[1]
    assert told is not None
    assert "went too far" in told
    assert "by 40%" in told
    assert "change fewer words" in told
    [sent] = await _letters(session)
    assert sent.uniqueness_pct == pytest.approx(0.20)
    assert report.letters[0].attempts == 2


async def test_every_call_off_the_corridor_the_lead_waits_with_every_number(
    session: AsyncSession, world: w.World
) -> None:
    """Все `SALES_REWRITE_ATTEMPTS` вызовов мимо: письма нет, лид ждёт следующей сборки — с
    причиной, где отличие каждой попытки по порядку, а не голое «вне коридора»."""
    await _first_step(session, world, SINGLE)
    jane = await w.lead(session, world.hypothesis_id, JANE)
    model = w.ZoneRewriter({"intro": 1})

    report = await _build(session, world, model)

    assert (sales_cfg.REWRITE_ATTEMPTS, len(model.seen)) == (3, 3)
    reason = f"{OFF} 3 попыток: 40%, 40%, 40% — ждёт следующей сборки"
    assert (report.prepared, report.off_corridor) == (0, 1)
    # В сводке причин — ключ без чисел: сто таких лидов — одна строка.
    assert report.waiting == Counter({queue.OFF_CORRIDOR: 1})
    assert await _letters(session) == []
    assert await session.scalar(select(func.count()).select_from(SalesThreadModel)) == 0
    assert report.tokens_spent == 3 * TOKENS
    assert report.letters == [
        corridor.LetterLine(
            jane.id, "Jane Example", 3, pytest.approx(0.4), ("intro",), False, reason
        )  # type: ignore[arg-type]
    ]


async def test_one_attempt_setting_means_one_call_and_no_hint(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`SALES_REWRITE_ATTEMPTS=1` — один вызов на письмо, как до запасного пути: подбор есть,
    повтора нет; не легло — лид ждёт с числом."""
    monkeypatch.setattr(sales_cfg, "REWRITE_ATTEMPTS", 1)
    await _first_step(session, world, SINGLE)
    await w.lead(session, world.hypothesis_id, JANE)
    model = w.ZoneRewriter({"intro": 1}, {"intro": 0.5})

    report = await _build(session, world, model)

    assert (len(model.seen), model.hints) == (1, [None])
    assert report.waiting == Counter({queue.OFF_CORRIDOR: 1})
    assert report.letters[0].reason == f"{OFF} 1 попытки: 40% — ждёт следующей сборки"


async def test_spend_cap_between_calls_stops_the_build_and_the_spend_is_one_row(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Потолок модели — до каждого следующего вызова, как до каждого письма: выбран посреди
    попыток — сборка встаёт словами потолка, следующий лид модели не стоит. Расход письма —
    одной строкой на все его вызовы, и потолок её видит."""
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", 53)  # выдуманный: 37 + 37 его переходят
    monkeypatch.setattr(llm_cfg, "SALES_DAILY_TOKEN_CAP", 0)  # свой потолок продаж — не здесь
    await _first_step(session, world, SINGLE)
    jane = await w.lead(session, world.hypothesis_id, JANE)
    await w.lead(session, world.hypothesis_id, OLGA, name="Olga Example")
    model = w.ZoneRewriter({"intro": 1})

    report = await _build(session, world, model)

    assert len(model.seen) == 2
    assert report.stopped is not None
    assert report.stopped.startswith("потолок расхода на модель за день достигнут: 74 из 53")
    assert report.waiting == Counter({queue.OFF_CORRIDOR: 1})
    [line] = report.letters
    assert (line.lead_id, line.reason) == (
        jane.id,
        f"{OFF} 2 попыток: 40%, 40% — ждёт следующей сборки",
    )
    await session.rollback()  # записанное сборкой — из базы, а не из памяти сессии
    assert await _spent(session) == [2 * TOKENS]


async def test_spend_of_every_call_of_a_letter_is_one_row_and_in_the_report(
    session: AsyncSession, world: w.World
) -> None:
    await _first_step(session, world, FOUR)
    await w.lead(session, world.hypothesis_id, JANE)
    await w.lead(session, world.hypothesis_id, OLGA, name="Olga Example")
    model = w.ZoneRewriter({"greeting": 1}, {"intro": 1})  # Джейн — два вызова, Ольга — один

    report = await _build(session, world, model)

    assert [line.attempts for line in report.letters] == [2, 1]
    assert report.tokens_spent == 3 * TOKENS
    assert await _spent(session) == [2 * TOKENS, TOKENS]


# --- те же проверки по выбранному тексту ------------------------------------------------------


async def test_chosen_text_passes_the_same_checks_in_the_same_order(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Подбор не обходит проверок: метрики — по собранному письму выбранного набора, затем
    коридор, подпись и адрес — по нему же; в очередь встаёт ровно проверенный текст."""
    seen: list[tuple[str, str]] = []
    metrics, problem = guards.assert_no_metrics, letter.problem

    def checked_metrics(text: str) -> None:
        seen.append(("метрики", text))
        metrics(text)

    def checked_problem(body: str, found: Any) -> str | None:
        seen.append(("подпись и адрес", body))
        return problem(body, found)

    await _first_step(session, world, FOUR)
    await w.lead(session, world.hypothesis_id, JANE)
    monkeypatch.setattr(guards, "assert_no_metrics", checked_metrics)
    monkeypatch.setattr(letter, "problem", checked_problem)

    await _build(session, world, w.ZoneRewriter({"intro": 1, "close": 1}))

    [sent] = await _letters(session)
    assert [what for what, _ in seen] == ["метрики", "подпись и адрес"]
    (_, checked), (_, signed) = seen
    assert "intronew0" in checked
    assert "closenew0" not in checked
    assert signed == sent.body


# --- шаблон, а не модель: сборка встаёт ---------------------------------------------------------


async def _leads(session: AsyncSession, world: w.World, *names: str | None) -> None:
    """Лиды по порядку сборки; имени нет — лид ждёт до модели: добивки зовут его по имени."""
    for n, name in enumerate(names):
        await w.lead(session, world.hypothesis_id, f"lead{n}@site{n}.example.test", name=name)


async def test_three_leads_in_a_row_off_the_corridor_stop_the_build_in_words(
    session: AsyncSession, world: w.World
) -> None:
    """Решение владельца: три лида подряд вне коридора после всех попыток — шаблон, а не
    модель. Сборка встаёт словами с советом; следующие лиды модели не стоят."""
    await _first_step(session, world, SINGLE)
    await _leads(session, world, *["Lead Example"] * 5)
    model = w.ZoneRewriter({"intro": 1})

    report = await _build(session, world, model)

    assert len(model.seen) == 3 * sales_cfg.REWRITE_ATTEMPTS
    assert len(report.letters) == corridor.STREAK == 3
    assert report.stopped == corridor.streak_stop()
    assert report.stopped == (
        "шаблон первого письма не даёт писем в коридоре отличия: 3 лида подряд — вне коридора "
        "после всех попыток; поправьте цепочку писем — совет в предупреждении у первого письма"
    )
    assert report.waiting == Counter({queue.OFF_CORRIDOR: 3})


async def test_a_queued_letter_breaks_the_streak(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Два вне коридора, одно в очереди, два вне — серия прервана: сборка идёт до конца."""
    monkeypatch.setattr(sales_cfg, "REWRITE_ATTEMPTS", 1)  # вызов на лида — серия по лидам
    await _first_step(session, world, SINGLE)
    await _leads(session, world, *["Lead Example"] * 5)
    far, fits = {"intro": 1.0}, {"intro": 0.5}

    report = await _build(session, world, w.ZoneRewriter(far, far, fits, far, far))

    assert [line.queued for line in report.letters] == [False, False, True, False, False]
    assert (report.stopped, report.prepared, report.off_corridor) == (None, 1, 4)


async def test_leads_waiting_for_other_reasons_neither_count_nor_break_the_streak(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Лид без имени ждёт до модели — причина не про шаблон: серию он не продолжает и не
    прерывает. Вне, ждёт, вне, ждёт, вне — сборка встаёт на третьем вне коридора, не раньше."""
    monkeypatch.setattr(sales_cfg, "REWRITE_ATTEMPTS", 1)
    await _first_step(session, world, SINGLE)
    await _leads(session, world, "Lead Example", None, "Lead Example", None, "Lead Example")

    report = await _build(session, world, w.ZoneRewriter({"intro": 1}))

    assert len(report.letters) == 3
    assert report.stopped == corridor.streak_stop()
    assert report.waiting == Counter({queue.OFF_CORRIDOR: 3, "у лида нет значения для {{name}}": 2})


async def test_letter_in_the_corridor_that_waits_for_the_signature_does_not_touch_the_streak(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Письмо легло в коридор, но в очередь не встало: подпись отправителя стоит и в тексте
    шаблона (записан до подписи). Причина — не коридор: серию такое письмо не продолжает и не
    прерывает. Вне, подпись, вне, подпись, вне — сборка встаёт на третьем вне коридора."""
    monkeypatch.setattr(sales_cfg, "REWRITE_ATTEMPTS", 1)
    await w.settings(session, signature=None)  # шаблон с подписью записан до неё
    await _first_step(session, world, f"{SINGLE}\n{w.SIGNATURE}")
    await w.settings(session)
    await _leads(session, world, *["Lead Example"] * 5)
    far, fits = {"intro": 1.0}, {"intro": 0.5}

    report = await _build(session, world, w.ZoneRewriter(far, fits, far, fits, far))

    assert [line.reason is None for line in report.letters] == [False] * 5
    assert report.stopped == corridor.streak_stop()
    [signed] = [why for why in report.waiting if why != queue.OFF_CORRIDOR]
    assert signed.startswith("подпись из настроек отправителя стоит и в тексте письма")
    assert report.waiting == Counter({queue.OFF_CORRIDOR: 3, signed: 2})


async def test_console_sums_up_the_waiting_once_and_names_every_number_by_the_letter(
    session: AsyncSession, world: w.World, capsys: pytest.CaptureFixture[str]
) -> None:
    """Консоль: в сводке причин — строка без чисел, в строке письма — отличие каждой попытки;
    серия из трёх лидов — словами остановки."""
    from backend.cli.sales_queue import run_sales_queue  # noqa: PLC0415

    await _first_step(session, world, SINGLE)
    await _leads(session, world, *["Lead Example"] * 3)

    code = await run_sales_queue(session, w.ZoneRewriter({"intro": 1}), "Выдуманная гипотеза", 5)  # type: ignore[arg-type]

    out = capsys.readouterr().out
    assert code == 0
    assert f"  ждут — {queue.OFF_CORRIDOR}: 3\n" in out
    line = f"  Lead Example — {OFF} 3 попыток: 40%, 40%, 40% — ждёт следующей сборки\n"
    assert out.count(line) == 3
    assert f"Сборка остановлена: {corridor.streak_stop()}\n" in out


# --- заметки: одной попытки -------------------------------------------------------------------


async def test_notes_of_the_queued_attempt_only_reach_the_report(
    session: AsyncSession, world: w.World
) -> None:
    """Первый вызов не вернул вступления — «зона «intro» осталась шаблонной»; второй переписал
    его, и в письме оно переписано. В итог идут заметки только второй попытки — той, что
    встала в очередь: заметка первой врала бы о письме."""
    await _first_step(session, world, FOUR)
    await w.lead(session, world.hypothesis_id, JANE)
    model = w.ZoneRewriter(
        {"greeting": 1},
        {"intro": 1, "close": 1},
        notes=(["зона «intro» осталась шаблонной"], []),
    )

    report = await _build(session, world, model)

    assert (report.prepared, report.letters[0].zones) == (1, ("intro",))
    assert report.notes == Counter(
        {"зона «close» оставлена шаблонной подбором под коридор отличия": 1}
    )


async def test_lead_that_waits_keeps_the_notes_of_its_last_attempt(
    session: AsyncSession, world: w.World
) -> None:
    """Не легло ни разу — в итог заметки последней попытки, одной: почему лид остался ни с чем
    (у ключа модели — «ключ не задан»); заметки прежних попыток — в журнале."""
    await _first_step(session, world, SINGLE)
    await w.lead(session, world.hypothesis_id, JANE)
    model = w.ZoneRewriter({"intro": 1}, notes=(["первая"], ["вторая"], ["последняя"]))

    report = await _build(session, world, model)

    assert report.notes == Counter({"последняя": 1})


# --- журнал ----------------------------------------------------------------------------------


async def test_journal_has_a_line_per_call_without_the_address_of_the_lead(
    session: AsyncSession, world: w.World, caplog: pytest.LogCaptureFixture
) -> None:
    """Строка на каждый вызов: лид — номером, попытка, отличие, принятые зоны, вердикт и
    заметки. Адреса лида нет ни в одной строке продаж, а итог сборки идёт в журнал без писем
    по одному — без имён."""
    await _first_step(session, world, FOUR)
    jane = await w.lead(session, world.hypothesis_id, JANE)
    caplog.set_level(logging.INFO, logger="backend.features.sales")

    await _build(
        session, world, w.ZoneRewriter({"greeting": 1, "close": 1}, {"intro": 1, "close": 1})
    )

    tries = [r for r in caplog.records if r.getMessage() == "продажи: попытка письма"]
    got = [
        (r.lead_id, r.attempt, r.attempts, r.difference, r.zones, r.verdict)  # type: ignore[attr-defined]
        for r in tries
    ]
    assert got == [
        (jane.id, 1, 3, 0.1, ["greeting", "close"], corridor_verdict(0.1)),
        (jane.id, 2, 3, 0.2, ["intro"], corridor.IN_CORRIDOR),
    ]
    assert tries[1].notes == [  # type: ignore[attr-defined]
        "зона «close» оставлена шаблонной подбором под коридор отличия"
    ]
    sales = [r for r in caplog.records if r.name.startswith("backend.features.sales")]
    assert all(JANE not in str(vars(record)) for record in sales)
    [built] = [r for r in sales if r.getMessage() == "продажи: очередь собрана"]
    assert not hasattr(built, "letters")
    assert "Jane Example" not in str(vars(built))


# --- слова --------------------------------------------------------------------------------------


def test_reason_names_every_attempt_by_the_rule_of_the_corridor_edge() -> None:
    assert corridor.waiting_reason([0.38, 0.31, 0.27]) == (
        f"{OFF} 3 попыток: 38%, 31%, 27% — ждёт следующей сборки"
    )
    # У края коридора число точнее: «25%» спорило бы с «вне коридора 15–25%».
    assert corridor.waiting_reason([0.2504]) == f"{OFF} 1 попытки: 25,04% — ждёт следующей сборки"


@pytest.mark.parametrize(
    ("count", "words"),
    [(1, "1 попытки"), (2, "2 попыток"), (5, "5 попыток"), (11, "11 попыток"), (21, "21 попытки")],
)
def test_number_of_attempts_after_after(count: int, words: str) -> None:
    assert corridor.waiting_reason([0.4] * count).startswith(f"{OFF} {words}:")


def test_hint_names_the_side_and_the_number_in_english() -> None:
    assert corridor.hint(0.38) == (
        "A previous rewrite of these zones went too far: the email differed from the original "
        "by 38%, and the target is 15–25%. Stay closer to the original wording and change "
        "fewer words."
    )
    assert corridor.hint(0.1495) == (
        "A previous rewrite of these zones stayed too close to the original: the email "
        "differed from it by only 14.95%, and the target is 15–25%. Change more words."
    )


def test_report_line_names_the_lead_the_difference_the_zones_and_the_queue() -> None:
    line = corridor.LetterLine(7, None, 2, 0.2504, ("intro", "close"), False, "почему — словами")

    assert line.as_dict() == {
        "lead_id": 7,
        "name": None,
        "attempts": 2,
        "difference": 0.2504,
        "difference_text": "25,04%",
        "zones": ["intro", "close"],
        "queued": False,
        "reason": "почему — словами",
    }


def test_attempts_setting_is_from_one_to_ten(monkeypatch: pytest.MonkeyPatch) -> None:
    """По умолчанию три; ноль попыток — ни одного письма, больше десяти — опечатка."""
    monkeypatch.delenv("SALES_REWRITE_ATTEMPTS", raising=False)
    assert sales_cfg._Sales(_env_file=None).rewrite_attempts == 3
    for wrong in ("0", "11"):
        monkeypatch.setenv("SALES_REWRITE_ATTEMPTS", wrong)
        with pytest.raises(ValueError, match="SALES_REWRITE_ATTEMPTS"):
            sales_cfg._Sales(_env_file=None)
    monkeypatch.setenv("SALES_REWRITE_ATTEMPTS", "1")
    assert sales_cfg._Sales(_env_file=None).rewrite_attempts == 1
