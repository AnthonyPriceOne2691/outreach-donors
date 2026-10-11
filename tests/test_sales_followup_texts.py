"""Добивки продаж тоже разные: тексты шагов 2–3 — при сборке первого письма, каждый в своём
коридоре; отправка добивки берёт их готовыми, модели в проходе добивок нет.

ТЗ: «Формулировки ротируются, двух одинаковых писем не уходит». Сборка на настоящей базе (мир
`test_sales_send_world.py`) со своей цепочкой гипотезы: первое письмо и добивки из слов-меток, у
каждой зоны свои; подделка модели переписывает то, что велит тест (`w.ZoneRewriter`), и отличие
считается ровно: переписанные слова, делённые на слова письма. Тексты и адреса выдуманы.

Наборы зон добивки в коридоре — одного размера (пары): выбор зерном тогда один и тот же при
любом правиле ядра подбора — «любой набор в коридоре» и «наибольший, зерно — среди равных».
"""

from __future__ import annotations

import itertools
from collections import Counter
from typing import Any

import pytest
from backend.config import llm as llm_cfg
from backend.features.core.models.ops import UsageRecordModel
from backend.features.core.models.outreach import MessageModel
from backend.features.letters import followups
from backend.features.letters.compose import Rendered
from backend.features.letters.guards import ForbiddenContentError
from backend.features.letters.rewrite import Personalization, RewriteResult
from backend.features.letters.sending import Sending
from backend.features.sales import chain, chain_text, corridor, followup_texts, queue
from backend.features.sales.models import SalesThreadModel
from backend.features.sales.usage_cap import LETTER_REWRITE
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.test_sales_queue_corridor import FOUR, _words
from tests.test_sales_send import FIRST_DUE, SECOND_DUE, SIGNED, _seen, _transports

JANE = "jane@acme.example.test"
TOKENS = 37
ZONES = ("pa", "pb", "pc", "pd")


def _follow(*names: str, tail: int = 30, fixed: str = "tail") -> str:
    """Добивка из слов-меток: зоны `rewrite` по пять слов и неизменный хвост."""
    zones = [f"[{name}] rewrite\n{_words(name, 5)}" for name in names]
    return "\n\n".join([*zones, f"[{fixed}] fixed\n{_words(fixed, tail)}"])


#: Добивка: четыре зоны `rewrite` по 5 слов и 30 неизменных — 50 слов. Зона, переписанная
#: целиком, — 10% письма: в коридор 15–25% ложатся ровно пары зон (20%), все шесть — одного
#: размера; одна зона — 10%, три — 30%, все — 40%.
FOLLOW = _follow(*ZONES)
PAIRS = {frozenset(pair) for pair in itertools.combinations(ZONES, 2)}
#: Модель переписала всё: первое письмо — вступление (20% — в коридоре одним набором), добивки —
#: все зоны обоих шагов; зоны добивок в общем запросе — с приставкой шага.
EVERY = {"intro": 1.0} | {f"step{step}_{zone}": 1.0 for step in (2, 3) for zone in ZONES}


class Recorder(w.ZoneRewriter):
    """Подделка модели, которая ещё и помнит зоны каждого вызова — имена и тексты."""

    def __init__(self, *plans: dict[str, float], **kwargs: Any) -> None:
        super().__init__(*plans, **kwargs)
        self.calls: list[dict[str, str]] = []

    async def rewrite(
        self, rendered: Rendered, about: Personalization, *, hint: str | None = None
    ) -> RewriteResult:
        self.calls.append({zone.name: zone.text for zone in rendered.rewritable()})
        return await super().rewrite(rendered, about, hint=hint)


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    return await w.world(session, monkeypatch)


async def _chain(
    session: AsyncSession,
    world: w.World,
    *,
    first: str = FOUR,
    second: str = FOLLOW,
    third: str = FOLLOW,
) -> str:
    """Своя цепочка гипотезы на английском; вернуть её версию."""
    made = [
        chain_text.step_template(step=1, language="en", subject="Made-up test", body=first),
        chain_text.step_template(step=2, language="en", body=second),
        chain_text.step_template(step=3, language="en", body=third),
    ]
    for new in made:
        await chain.save(
            session, new, hypothesis_id=world.hypothesis_id, author="тест", author_id=None
        )
    return (await chain.of_set(session, world.hypothesis_id, "en")).version


async def _build(session: AsyncSession, world: w.World, model: Any) -> queue.QueueReport:
    return await queue.build(session, model, hypothesis_id=world.hypothesis_id, limit=10)


async def _links(session: AsyncSession) -> list[SalesThreadModel]:
    """Связи лидов с переписками — из базы, а не из памяти сессии."""
    session.expire_all()
    rows = await session.scalars(select(SalesThreadModel).order_by(SalesThreadModel.lead_id))
    return list(rows)


def _rewritten(body: str, step: int) -> frozenset[str]:
    """Какие зоны добивки переписаны в тексте: у переписанной — новые слова с приставкой шага."""
    return frozenset(zone for zone in ZONES if f"step{step}_{zone}new0" in body)


# --- сборка: тексты шагов 2–3, каждый в своём коридоре -----------------------------------------


async def test_followups_are_written_with_the_first_letter_each_in_its_own_corridor(
    session: AsyncSession, world: w.World
) -> None:
    """Первое письмо — своим вызовом, добивки — одним общим: зоны обоих шагов с приставкой
    шага. Каждая добивка — свой подбор: пара зон, 20%; тексты — у переписки, с версией."""
    version = await _chain(session, world)
    jane = (await w.lead(session, world.hypothesis_id, JANE)).id
    model = Recorder(EVERY)

    report = await _build(session, world, model)

    first, together = model.calls
    assert list(first) == ["greeting", "intro", "aside", "close"]
    assert list(together) == [f"step{step}_{zone}" for step in (2, 3) for zone in ZONES]
    [link] = await _links(session)
    assert link.followups is not None
    assert link.followups["version"] == version
    for step in ("2", "3"):
        kept = link.followups["steps"][step]
        assert kept["difference"] == pytest.approx(0.2)
        assert frozenset(kept["zones"]) in PAIRS
        assert _rewritten(kept["body"], int(step)) == frozenset(kept["zones"])
        assert kept["differences"] == [pytest.approx(0.2)]
    [line] = report.letters
    assert (line.lead_id, line.queued) == (jane, True)
    assert [(later.step, later.attempts, later.reason) for later in line.followups] == [
        (2, 1, None),
        (3, 1, None),
    ]
    assert report.followups_template == 0


async def test_the_letter_of_a_lead_costs_one_row_of_spend_with_its_followups(
    session: AsyncSession, world: w.World
) -> None:
    """Расход письма лида — одной строкой: первое письмо и общий вызов добивок."""
    await _chain(session, world)
    await w.lead(session, world.hypothesis_id, JANE)

    report = await _build(session, world, Recorder(EVERY))

    session.expire_all()
    rows = await session.scalars(
        select(UsageRecordModel.units).where(UsageRecordModel.operation == LETTER_REWRITE)
    )
    assert list(rows) == [2 * TOKENS]
    assert report.tokens_spent == 2 * TOKENS


async def test_a_step_without_rewrite_zones_costs_no_model_and_keeps_no_text(
    session: AsyncSession, world: w.World
) -> None:
    """Вторая добивка только из `fixed`: в общий запрос идут зоны одной первой добивки, у
    переписки — только её текст; вторая уйдёт шаблоном, как раньше."""
    await _chain(session, world, third=w.FOLLOW_BODY[3])
    await w.lead(session, world.hypothesis_id, JANE)
    model = Recorder(EVERY)

    report = await _build(session, world, model)

    assert list(model.calls[1]) == [f"step2_{zone}" for zone in ZONES]
    [link] = await _links(session)
    assert link.followups is not None
    assert list(link.followups["steps"]) == ["2"]
    assert [later.step for later in report.letters[0].followups] == [2]


async def test_chain_with_fixed_followups_calls_the_model_once_per_letter_as_before(
    session: AsyncSession, world: w.World
) -> None:
    """Добивки только из `fixed` (как у цепочки мира): вызов — первому письму, текстов нет."""
    await _chain(session, world, second=w.FOLLOW_BODY[2], third=w.FOLLOW_BODY[3])
    await w.lead(session, world.hypothesis_id, JANE)
    model = Recorder(EVERY)

    report = await _build(session, world, model)

    assert len(model.calls) == 1
    [link] = await _links(session)
    assert link.followups is None
    assert report.letters[0].followups == ()


# --- зерно — номер лида и шаг ---------------------------------------------------------------------


async def test_seed_is_the_lead_and_the_step_sets_differ_between_leads_and_steps(
    session: AsyncSession, world: w.World
) -> None:
    """Две добивки одинакового текста: зерно без шага дало бы им один набор у каждого лида —
    два одинаковых письма в переписке. С шагом — наборы расходятся и между шагами, и между
    лидами; сборка того же лида повторима."""
    await _chain(session, world)
    for n in range(10):
        await w.lead(session, world.hypothesis_id, f"lead{n}@site{n}.example.test")

    await _build(session, world, Recorder(EVERY))

    links = await _links(session)
    assert len(links) == 10
    sets = [
        (frozenset(link.followups["steps"]["2"]["zones"]), link.followups["steps"]["3"]["zones"])
        for link in links
        if link.followups is not None
    ]
    assert len(sets) == 10
    assert all(second in PAIRS and frozenset(third) in PAIRS for second, third in sets)
    assert any(second != frozenset(third) for second, third in sets)
    assert len({second for second, _ in sets}) > 1


def test_seed_of_the_first_letter_is_the_lead_and_of_a_followup_the_lead_and_the_step() -> None:
    assert corridor.seed(7, 1) == 7
    assert corridor.seed(7, 2) != corridor.seed(7, 3)
    assert corridor.seed(7, 2) != corridor.seed(8, 2)


# --- не легла — шаблоном; первое письмо идёт ---------------------------------------------------


async def test_followup_off_the_corridor_goes_as_the_template_and_the_letter_is_queued(
    session: AsyncSession, world: w.World
) -> None:
    """Модель правит у добивок одну зону — 10%, ни один набор не в коридоре. Каждый шаг зовёт
    модель заново сам, со своей подсказкой, до `SALES_REWRITE_ATTEMPTS`; не вышло — добивка
    уйдёт текстом шаблона, а первое письмо встаёт в очередь. Счёт — в отчёте."""
    await _chain(session, world)
    await w.lead(session, world.hypothesis_id, JANE)
    plan = {"intro": 1.0, "step2_pa": 1.0, "step3_pa": 1.0, "pa": 1.0}
    model = Recorder(plan)

    report = await _build(session, world, model)

    # первое письмо, общий вызов добивок, по два повтора на шаг
    assert len(model.calls) == 1 + 1 + 2 + 2
    assert model.hints[2:] == [corridor.hint(0.1)] * 4
    reason = "не легла в коридор отличия после 3 попыток: 10%, 10%, 10% — уйдёт текстом шаблона"
    [line] = report.letters
    assert line.queued is True
    assert [(later.step, later.reason) for later in line.followups] == [(2, reason), (3, reason)]
    assert (report.prepared, report.followups_template) == (1, 2)
    assert await session.scalar(select(func.count()).select_from(MessageModel)) == 1
    [link] = await _links(session)
    assert link.followups is not None
    for step in ("2", "3"):
        kept = link.followups["steps"][step]
        assert kept["difference"] is None
        assert kept["body"] == f"{_words('pa', 5)}\n\n{_words('pb', 5)}\n\n" + (
            f"{_words('pc', 5)}\n\n{_words('pd', 5)}\n\n{_words('tail', 30)}"
        )


async def test_console_names_the_followups_of_each_letter_and_counts_the_template_ones(
    session: AsyncSession, world: w.World, capsys: pytest.CaptureFixture[str]
) -> None:
    """Консоль: у письма — его добивки (отличие или почему шаблоном), у сборки — сколько
    добивок уйдёт текстом шаблона."""
    from backend.cli.sales_queue import run_sales_queue  # noqa: PLC0415

    await _chain(session, world, third=_follow("pa", "pb", tail=8))
    await w.lead(session, world.hypothesis_id, JANE)
    # Вторая добивка — 10 слов rewrite из 18: одна зона — 27,8%, обе — 55,6%: не ложится никогда.
    plan = EVERY | {"pa": 1.0, "pb": 1.0}

    code = await run_sales_queue(session, Recorder(plan), "Выдуманная гипотеза", 5)  # type: ignore[arg-type]

    out = capsys.readouterr().out
    assert code == 0
    assert "  добивок шаблоном: 1\n" in out
    assert (
        "попыток: 1 — в очереди; первая добивка — отличие 20%; вторая добивка не легла в "
        "коридор отличия после 3 попыток: 55,6%, 55,6%, 55,6% — уйдёт текстом шаблона\n"
    ) in out


async def test_first_letter_off_the_corridor_costs_the_followups_nothing(
    session: AsyncSession, world: w.World
) -> None:
    """Первое письмо не легло — лид ждёт, как раньше, и добивкам модель не нужна вовсе."""
    await _chain(session, world)
    await w.lead(session, world.hypothesis_id, JANE)
    model = Recorder({"greeting": 1.0})

    report = await _build(session, world, model)

    assert len(model.calls) == 3
    assert all("step2_pa" not in call for call in model.calls)
    assert report.waiting == Counter({queue.OFF_CORRIDOR: 1})


# --- потолок модели посреди — сборка встаёт словами -------------------------------------------


@pytest.mark.parametrize(
    ("cap", "calls"),
    [
        (31, 1),  # выдуманный: первое письмо (37) его переходит — до общего вызова добивок
        (53, 2),  # выдуманный: с общим вызовом (74) переходит — до повтора шага добивки
    ],
)
async def test_spend_cap_on_the_followups_stops_the_build_and_the_lead_waits_whole(
    session: AsyncSession,
    world: w.World,
    monkeypatch: pytest.MonkeyPatch,
    cap: int,
    calls: int,
) -> None:
    """Потолок модели — до каждого следующего вызова, и у добивок тоже: встал посреди — сборка
    встаёт словами потолка, письмо лида в очередь не встаёт (с добивками без переписки его
    больше не пересобрали бы), лид ждёт следующей сборки."""
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", cap)
    monkeypatch.setattr(llm_cfg, "SALES_DAILY_TOKEN_CAP", 0)
    await _chain(session, world)
    await w.lead(session, world.hypothesis_id, JANE)
    await w.lead(session, world.hypothesis_id, "olga@beta.example.test", name="Olga Example")
    plan = {"intro": 1.0, "step2_pa": 1.0, "step3_pa": 1.0, "pa": 1.0}
    model = Recorder(plan if calls > 1 else EVERY)

    report = await _build(session, world, model)

    assert len(model.calls) == calls
    assert report.stopped is not None
    assert report.stopped.startswith("потолок расхода на модель за день достигнут")
    assert report.waiting == Counter({followup_texts.CAPPED: 1})
    [line] = report.letters
    assert (line.queued, line.reason) == (False, followup_texts.CAPPED)
    assert await session.scalar(select(func.count()).select_from(MessageModel)) == 0
    assert await _links(session) == []


# --- пересборка: в очереди — заново, после отправки — прежние ----------------------------------


async def test_rebuilding_a_queued_letter_rewrites_its_followups_and_a_sent_one_keeps_them(
    session: AsyncSession, world: w.World
) -> None:
    """Цепочку поправили, письмо ещё в очереди — сборка собирает его заново вместе с
    добивками: новые тексты с новой версией. Первое письмо ушло — тексты добивок не меняются."""
    first = await _chain(session, world)
    await w.lead(session, world.hypothesis_id, JANE)
    await _build(session, world, Recorder(EVERY))
    second = await _chain(session, world, third=_follow(*ZONES, fixed="other"))

    report = await _build(session, world, Recorder(EVERY))

    assert (first != second, report.refreshed) == (True, 1)
    [link] = await _links(session)
    assert link.followups is not None
    assert link.followups["version"] == second
    assert _words("other", 30) in link.followups["steps"]["3"]["body"]
    kept = dict(link.followups)
    [letter] = await session.scalars(select(MessageModel))
    await Sending(session, _transports(), now=w.NOW).send(letter.id)
    await _chain(session, world, third=_follow(*ZONES, fixed="third"))

    again = await _build(session, world, Recorder(EVERY))

    assert (again.prepared, again.refreshed) == (0, 0)
    [link] = await _links(session)
    assert link.followups == kept


# --- метрики — отказ, как у шага 1 ----------------------------------------------------------------


class Leaking(w.ZoneRewriter):
    """Подделка модели, которая дописывает метрику в зоны с этим началом имени — мимо
    проверок клиента: проверка собранного письма — последний рубеж."""

    def __init__(self, leaky: str) -> None:
        super().__init__(EVERY)
        self.leaky = leaky

    async def rewrite(
        self, rendered: Rendered, about: Personalization, *, hint: str | None = None
    ) -> RewriteResult:
        result = await super().rewrite(rendered, about, hint=hint)
        for name in result.zones:
            if name.startswith(self.leaky):
                result.zones[name] += " DR 46"
        return result


@pytest.mark.parametrize("leaky", ["intro", "step2_"])
async def test_metrics_in_a_rewritten_followup_are_refused_as_in_the_first_letter(
    session: AsyncSession, world: w.World, leaky: str
) -> None:
    await _chain(session, world)
    await w.lead(session, world.hypothesis_id, JANE)

    with pytest.raises(ForbiddenContentError, match="метрики Ahrefs"):
        await _build(session, world, Leaking(leaky))

    assert await session.scalar(select(func.count()).select_from(MessageModel)) == 0


# --- заметки общего вызова — своему шагу ---------------------------------------------------------


async def test_notes_of_the_shared_call_go_to_their_own_step(
    session: AsyncSession, world: w.World
) -> None:
    """Заметка клиента о зоне шага — этому шагу, без приставки; общая — каждому шагу."""
    await _chain(session, world)
    await w.lead(session, world.hypothesis_id, JANE)
    model = Recorder(
        EVERY, notes=([], ["зона «step3_pd» осталась шаблонной", "ключ модели не задан"])
    )

    report = await _build(session, world, model)

    for title in ("первая добивка", "вторая добивка"):
        assert report.notes[f"{title}: ключ модели не задан"] == 1
    assert report.notes["вторая добивка: зона «pd» осталась шаблонной"] == 1
    assert "первая добивка: зона «pd» осталась шаблонной" not in report.notes
    assert not any("step3_" in note for note in report.notes)


# --- отправка добивки: сохранённый текст той же версии цепочки ----------------------------------


async def _sent_first(session: AsyncSession, world: w.World) -> Any:
    """Письмо Джейн собрано и ушло; вернуть транспорт."""
    await w.lead(session, world.hypothesis_id, JANE)
    await _build(session, world, Recorder(EVERY))
    [letter] = await session.scalars(select(MessageModel))
    source = _transports()
    await Sending(session, source, now=w.NOW).send(letter.id)
    return source


async def test_followups_go_with_the_texts_written_at_the_build(
    session: AsyncSession, world: w.World
) -> None:
    await _chain(session, world)
    source = await _sent_first(session, world)
    [link] = await _links(session)
    assert link.followups is not None
    kept = link.followups["steps"]

    first = await followups.send_due(session, transport=source, limit=5, now=FIRST_DUE)
    second = await followups.send_due(session, transport=source, limit=5, now=SECOND_DUE)

    assert (first.sent, second.sent) == (1, 1)
    _, one, two = _seen(source)
    assert one.body == f"{kept['2']['body']}{SIGNED}"
    assert two.body == f"{kept['3']['body']}{SIGNED}"
    assert _rewritten(one.body, 2) in PAIRS


async def test_chain_changed_after_the_build_the_followup_goes_as_the_template_of_today(
    session: AsyncSession, world: w.World
) -> None:
    """Мутант «текст чужой версии цепочки»: цепочку правили после сборки — сохранённый текст
    написан не ею, и добивка уходит шаблоном шага нынешней цепочки, как раньше."""
    await _chain(session, world)
    source = await _sent_first(session, world)
    await _chain(session, world, second=_follow(*ZONES, fixed="edited"))

    await followups.send_due(session, transport=source, limit=5, now=FIRST_DUE)

    template = _follow(*ZONES, fixed="edited")
    plain = "\n\n".join(part.split("\n", 1)[1] for part in template.split("\n\n"))
    assert _seen(source)[-1].body == f"{plain}{SIGNED}"


async def test_old_thread_without_texts_gets_the_template_as_before(
    session: AsyncSession, world: w.World
) -> None:
    """Переписка, собранная до переписки добивок, текстов не хранит — добивка шаблоном."""
    await _chain(session, world)
    source = await _sent_first(session, world)
    [link] = await _links(session)
    link.followups = None
    await session.flush()

    await followups.send_due(session, transport=source, limit=5, now=FIRST_DUE)

    plain = "\n\n".join(part.split("\n", 1)[1] for part in FOLLOW.split("\n\n"))
    assert _seen(source)[-1].body == f"{plain}{SIGNED}"


# --- {{name|запасное}}: лид без имени не ждёт -----------------------------------------------------

#: Приветствие с запасным текстом — неизменной зоной и в переписываемой.
SPARE_FIRST = FOUR.replace(
    "[greeting] rewrite\n", "[hello] fixed\nHi {{name|there}},\n\n[greeting] rewrite\n"
)
SPARE_FOLLOW = "[hello] fixed\nHi {{name|there}},\n\n" + FOLLOW
SPARE_FIXED_FOLLOW = "[hello] fixed\nHi {{name|there}}, a made-up reminder."


@pytest.mark.parametrize(
    ("name", "hello"), [(None, "Hi there,"), ("Jane Example", "Hi Jane Example,")]
)
async def test_spare_name_lets_a_lead_without_a_name_get_its_letters(
    session: AsyncSession, world: w.World, name: str | None, hello: str
) -> None:
    """Мутанты: `{{name|…}}` держит лида без имени; запасной текст при имени."""
    await _chain(session, world, first=SPARE_FIRST, second=SPARE_FOLLOW, third=SPARE_FIXED_FOLLOW)
    await w.lead(session, world.hypothesis_id, JANE, name=name)
    source = _transports()

    report = await _build(session, world, Recorder(EVERY))

    assert report.prepared == 1, report.waiting
    [letter] = await session.scalars(select(MessageModel))
    assert letter.body is not None
    assert letter.body.startswith(f"{hello}\n\n")
    assert "{{" not in letter.body
    await Sending(session, source, now=w.NOW).send(letter.id)
    await followups.send_due(session, transport=source, limit=5, now=FIRST_DUE)
    await followups.send_due(session, transport=source, limit=5, now=SECOND_DUE)
    _, one, two = _seen(source)
    assert one.body.startswith(f"{hello}\n\n")
    assert two.body == f"{hello} a made-up reminder.{SIGNED}"


async def test_bare_name_still_holds_a_lead_without_a_name(
    session: AsyncSession, world: w.World
) -> None:
    """Голый `{{name}}` держит лида без имени, как раньше — даже рядом с запасной формой."""
    await _chain(session, world, first=SPARE_FIRST, second=SPARE_FOLLOW, third=w.FOLLOW_BODY[2])
    await w.lead(session, world.hypothesis_id, JANE, name=None)

    report = await _build(session, world, Recorder(EVERY))

    assert report.waiting == Counter({"у лида нет значения для {{name}}": 1})


async def test_spare_text_reaches_the_model_as_text_and_survives_the_rewrite(
    session: AsyncSession, world: w.World
) -> None:
    """Подстановки — до переписки: модель видит запасной текст, а не скобки, и он остаётся в
    переписанной зоне письма — без скобок. Подделка переписывает 20 слов из 21, хвост цел."""
    first = FOUR.replace("intro19\n", "intro19 {{name|there}}\n")
    await _chain(session, world, first=first)
    await w.lead(session, world.hypothesis_id, JANE, name=None)
    model = Recorder({"intro": 20 / 21})

    report = await _build(session, world, model)

    assert model.calls[0]["intro"].endswith("intro19 there")
    assert report.prepared == 1, report.waiting
    [letter] = await session.scalars(select(MessageModel))
    assert letter.body is not None
    assert "intronew19 there\n" in letter.body
    assert "{{" not in letter.body
