"""Судья сегмента: второй вопрос и тогда, когда модель не дала цитаты вида сайта или дала короче трёх слов.

Замер 19.11 на прогонах сбора: 3 сайта из 102 к модели ушли «на проверку» словами «модель не дала
цитаты» — второго вопроса у них не было: его задавали, только когда цитата не нашлась в тексте.
Теперь второй вопрос — и у них, своей подсказкой (`prompts/segment_again_short.md`): цитата пуста
или коротка, нужны 3–30 слов одной строки о том, что за сайт, а не видно — «не понять» с цитатой.
Один раз на сайт; токены обоих вызовов — в вердикте, в счёте прохода и в его итоге; ответ не
разобран — второго вопроса по-прежнему нет.

Модель — подставным транспортом, сеть не ходит. Сайты и описания выдуманы, ниши, рынки и
письменности разные.
"""

from __future__ import annotations

from typing import Any

import pytest
from backend.config.judge import JudgeMode
from backend.features.sales import segment_client as sc
from backend.features.sales import segment_judge as sj
from backend.features.sales.segment_facts import NO_QUOTE, made_up
from backend.features.sales.segment_judging import PassTally, judge_run
from backend.features.sales.segment_models import Lane
from backend.features.sales.segment_rules import SNIPPET_ONLY
from backend.features.sales.segment_text import HOME_TEXT, PAGE_DESCRIPTION, PAGE_TITLE, SiteText
from sqlalchemy.ext.asyncio import AsyncSession
from tests.segment_facts_fakes import decide_any, facts
from tests.test_sales_segment_judge import client
from tests.test_sales_segment_judging import candidates
from tests.test_sales_segment_run import SEGMENT, hypothesis, make_run
from tests.test_sales_segment_second_chance import (
    BAKERY,
    BAKERY_TEXT,
    FOUND,
    Twice,
    in_turn,
    judge,
)

GREEK = SiteText(
    (
        (PAGE_TITLE, "Ξενοδοχείο στη Ρόδο με θέα στη θάλασσα"),
        (PAGE_DESCRIPTION, "Δωμάτια, τιμές και κρατήσεις απευθείας"),
    )
)
VIETNAMESE = SiteText(
    (
        (PAGE_TITLE, "Xưởng may đồng phục"),
        (HOME_TEXT, "Chúng tôi may đồng phục công sở và giao hàng toàn quốc"),
    )
)
#: (сайт, текст, первый ответ — без цитаты или с двумя словами, цитата второго ответа)
ASKED_AGAIN = [
    (BAKERY, BAKERY_TEXT, facts("yes", "", "пекарня"), FOUND),
    ("hotel-sample.gr", GREEK, facts("yes", "Ξενοδοχείο Ρόδος", "отель"), "Ξενοδοχείο στη Ρόδο με θέα"),
    ("dongphuc-sample.vn", VIETNAMESE, facts("yes", "  ", "ателье"), "may đồng phục công sở"),
]  # fmt: skip


async def _judged(host: str, text: SiteText, *bodies: Any) -> tuple[sj.SegmentVerdict, list[Any]]:
    model, seen = client(in_turn(*bodies))
    verdict = await decide_any(model, host=host, segment="Своё дело по описанию", text=text)
    return verdict, seen


def _user(payload: dict[str, Any]) -> str:
    return str(payload["messages"][-1]["content"])


@pytest.mark.parametrize(
    ("host", "text", "first", "quote"), ASKED_AGAIN, ids=["no-quote", "two-words", "blank"]
)
async def test_an_answer_without_a_quote_is_asked_again_and_the_second_answer_counts(
    host: str, text: SiteText, first: dict[str, Any], quote: str
) -> None:
    verdict, seen = await _judged(host, text, first, facts("yes", quote, "своё дело"))

    assert (verdict.lane, verdict.quote) == (Lane.ACCEPT, quote)
    assert verdict.facts is not None
    assert verdict.facts.why == "своё дело"  # факты второго ответа
    assert (verdict.retried, verdict.retry_held, verdict.proven) == (True, True, True)
    assert (verdict.tokens, verdict.spent) == (2 * 700, sj.Spent(1200, 200))
    assert len(seen) == 2


async def test_the_second_question_is_the_same_question_with_its_own_hint() -> None:
    """Тот же промпт и тот же вопрос по тому же тексту, подсказка — после него: цитата пуста или
    коротка, правило цитаты — нашими словами; слов «не нашлись» в ней нет — их и не было."""
    _, seen = await _judged(BAKERY, BAKERY_TEXT, facts("yes", ""), facts("yes", FOUND))

    first, second = seen
    assert second["messages"][0] == first["messages"][0]
    assert _user(second) == f"{_user(first)}\n{sc.load_again_short()}"
    hint = sc.load_again_short()
    assert "empty or shorter than 3 words" in hint
    assert "verbatim from ONE line" in hint
    assert "without the label" in hint
    assert '"kind": "unclear"' in hint
    assert "{quote}" not in hint
    assert "is not in the website text" not in hint  # подсказка ненайденной цитаты — другая
    assert _user(second).count(sj.SITE_CLOSE) == 1


def test_the_hint_without_a_quote_is_a_prompt_file_next_to_the_judge_prompt() -> None:
    assert sc.AGAIN_SHORT_PATH.parent == sj.PROMPT_PATH.parent
    assert sc.AGAIN_SHORT_PATH.name == "segment_again_short.md"
    assert sc.load_again_short() == sc.AGAIN_SHORT_PATH.read_text(encoding="utf-8").strip()


async def test_two_answers_without_a_quote_go_to_review_and_there_is_no_third_call() -> None:
    verdict, seen = await _judged(
        BAKERY,
        BAKERY_TEXT,
        facts("yes", "", "пекарня"),
        facts("yes", "pan artesanal", "пекарня"),
        facts("yes", FOUND, "пекарня"),  # третьего вопроса нет: этот ответ не спрашивается
    )

    assert (verdict.lane, verdict.reason, verdict.rule) == (Lane.REVIEW, NO_QUOTE, "model_failed")
    assert (verdict.retried, verdict.retry_held, verdict.unanswered) == (True, False, False)
    assert not verdict.proven  # в кэш не ложится: следующий проход спросит снова
    assert verdict.tokens == 2 * 700
    assert len(seen) == 2


async def test_a_quote_of_the_second_answer_not_in_the_text_is_named_and_asked_no_more() -> None:
    again = "pasteles por encargo y pan de cada día"
    verdict, seen = await _judged(
        BAKERY, BAKERY_TEXT, facts("yes", ""), facts("yes", again), facts("yes", FOUND)
    )

    assert (verdict.lane, verdict.reason) == (Lane.REVIEW, made_up(again))
    assert (verdict.retried, verdict.retry_held) == (True, False)
    assert len(seen) == 2


async def test_a_refused_second_call_keeps_the_words_of_the_answer_without_a_quote() -> None:
    """Второй вызов не ответил — сайт не «без ответа»: первый ответ был, его слова — человеку."""
    verdict, seen = await _judged(BAKERY, BAKERY_TEXT, facts("yes", ""), 400)

    assert (verdict.lane, verdict.reason) == (Lane.REVIEW, NO_QUOTE)
    assert (verdict.unanswered, verdict.retried, verdict.retry_held) == (False, True, False)
    assert verdict.tokens == 700
    assert len(seen) == 2


async def test_a_site_the_text_does_not_show_gets_honest_unclear_words_instead_of_no_quote() -> (
    None
):
    """По одному сниппету модель не видит, что за сайт, и не дала цитаты: после второго вопроса —
    «не понять» с цитатой и словами, почему (`SNIPPET_ONLY`), а не «модель не дала цитаты»."""
    hotel = SiteText(
        (
            (PAGE_TITLE, "Otel fiyatları ve rezervasyon"),
            (PAGE_DESCRIPTION, "Odalar, fiyatlar ve iletişim bilgileri burada"),
        )
    )
    verdict, _ = await _judged(
        "otel-sample.com.tr",
        hotel,
        facts("unsure", "", "непонятно, чей сайт"),
        facts("unsure", "Odalar, fiyatlar ve iletişim", "непонятно, чей сайт"),
    )

    assert (verdict.lane, verdict.rule) == (Lane.REVIEW, "unclear")
    assert verdict.reason == f"{SNIPPET_ONLY}. Непонятно, чей сайт"
    assert (verdict.retried, verdict.retry_held) == (True, True)


async def test_the_pass_counts_second_questions_without_a_quote(session: AsyncSession) -> None:
    owner = await hypothesis(session)
    run = await make_run(session, owner)
    held, lost = "konditorei-sample.at", "taller-sample.mx"
    shop, garage = await candidates(session, run.id, owner.id, held, lost)
    model = Twice(
        first={held: facts("yes", "", "кондитерская"), lost: facts("yes", "Taller", "мастерская")},
        second={held: facts("yes", f"О сайте {held}", "кондитерская"), lost: facts("yes", "", "мастерская")},
    )  # fmt: skip

    tally = await judge_run(
        session,
        run_id=run.id,
        segment=SEGMENT,
        mode=JudgeMode.ENFORCE,
        client=judge(model),
        home=None,
    )

    assert isinstance(tally, PassTally)
    assert sorted(model.asked) == sorted([(held, False), (held, True), (lost, False), (lost, True)])
    assert (tally.quote_retried, tally.quote_retry_held) == (2, 1)
    assert (tally.tokens, tally.prompt_tokens, tally.completion_tokens) == (4 * 500, 1800, 200)
    assert (shop.lane, garage.lane) == (Lane.ACCEPT, Lane.REVIEW)
    assert garage.reason == "цитата короче трёх слов — не доказательство"
    report = tally.as_dict()
    assert (report["quote_retried"], report["quote_retry_held"]) == (2, 1)
