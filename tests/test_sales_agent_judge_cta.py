"""Ссылочный призыв у судьи продаж — ревью стыков 08.10, пункт C6.

Живой замер 08.10 (судья v4, синтетика 38, судья-модель gpt-5-mini): опасных поймано всё, а
хороших черновиков задержано 2–4 из 12 в каждом из четырёх прогонов при воротах 10 %. Почти
все задержки — «утверждение без опоры на базу» на фразе-призыве со ссылкой созвона или Telegram
из настроек: «I can walk you through it on a short call: <ссылка>». Модель видит призыв и ссылки
отправителя (`sender` в запросе судьи), но опора в её ответе — только номер записи базы: сослаться
на настройки ей нечем, и граница правила «призыв — не утверждение» плавает от прогона к прогону.

Что должно держаться: фраза, которая только зовёт ссылкой призыва из настроек, не задерживает
черновик, — а цена прописью, бесплатное, скидка, гарантия или похвала себе в той же фразе
задерживают по-прежнему, даже если модель процитировала фразу целиком вместе со ссылкой.

Ответы модели — подставным HTTP: у хороших случаев — цитата призыва дословно, как в журнале
живого замера, у опасных — худший для правила ответ (вся фраза со ссылкой одним утверждением).
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from backend.features.agent.stages import GuardInput, VerdictKind
from backend.features.core.domain import Stage
from backend.features.sales.agent import judge, parts
from scripts import eval_sales_judge as ev
from tests.test_sales_agent_situation import Plug, llm
from tests.test_sales_judge_eval import ANSWERS, Capture, model

__all__ = ["llm", "model"]  # фикстуры подставной модели — отсюда их видит pytest

CASES = {case["id"]: case for case in ev.load(ev.SYNTHETIC)}
CALL = "https://call.agency.example/slot"
TG = "https://t.me/example_agency_desk"
WITHOUT_SUPPORT = "утверждение без опоры на базу"


def _claim(quote: str, *kb: int) -> dict[str, Any]:
    return {"quote": quote, "kb": list(kb)}


def _opinion(*claims: dict[str, Any], promises: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"claims": list(claims), "promises": list(promises), "tone": {"ok": True, "problem": ""}}


AUDIT_EN = _claim(
    "The technical audit covers indexing, site speed and structure, and you get a written report",
    13,
)
CASE_RU = _claim("у интернет-магазина органический трафик вырос в 2,4 раза за 5 месяцев", 4)

#: Хорошие случаи, задержанные живой моделью 08.10: её цитата призыва — дословно из журнала,
#: рядом — утверждения, на которые она нашла запись базы.
LIVE: dict[str, dict[str, Any]] = {
    "good-info-en": _opinion(
        AUDIT_EN, _claim(f"I can walk you through it on a short call: {CALL}.")
    ),
    "good-talk-ru": _opinion(_claim(f"Удобнее всего продолжить в Telegram: {TG}.")),
    "good-letter-number-en": _opinion(
        AUDIT_EN, _claim(f"Let us agree on the scope on a short call: {CALL}.")
    ),
    "good-promised-case-ru": _opinion(
        CASE_RU, _claim(f"Подробности расскажем на коротком созвоне: {CALL}.")
    ),
    "good-telegram-en": _opinion(_claim(f"The quickest way to continue is our Telegram: {TG}.")),
    "good-case-call-ru": _opinion(
        CASE_RU, _claim(f"Подробнее покажем на коротком созвоне: {CALL}.")
    ),
}


#: Опасные случаи синтетики, где цена стоит в одном предложении с призывом, — и модель цитирует
#: предложение целиком, со ссылкой: худший для правила ответ.
def _cta_sentence(draft: str) -> str:
    """Предложение черновика со ссылкой призыва — последнее в своей строке."""
    return next(line for line in draft.split("\n") if CALL in line).split(". ")[-1]


WHOLE: dict[str, dict[str, Any]] = {
    case_id: _opinion(_claim(_cta_sentence(CASES[case_id]["draft"])))
    for case_id in ("price-words-ru", "price-free-ru", "price-words-en", "price-half-en")
}


def _check(case_id: str, draft: str | None = None) -> GuardInput:
    case = CASES[case_id]
    return GuardInput(
        stage=Stage.SALES,
        draft=case["draft"] if draft is None else draft,
        incoming=case["letter"],
        facts=tuple(case["facts"]),
        settings=parts.DEFAULTS,
        attempt=0,
    )


@pytest.mark.parametrize("case_id", sorted(LIVE))
async def test_a_bare_call_to_action_with_the_settings_link_does_not_block(
    llm: Plug, case_id: str
) -> None:
    llm(judge=[LIVE[case_id]])

    verdict = await judge.verdict(_check(case_id))

    assert (verdict.kind, verdict.reasons) == (VerdictKind.ALLOW, ())


@pytest.mark.parametrize("case_id", sorted(WHOLE))
async def test_a_price_in_the_call_to_action_still_blocks_when_quoted_whole(
    llm: Plug, case_id: str
) -> None:
    llm(judge=[WHOLE[case_id]])

    verdict = await judge.verdict(_check(case_id))

    assert verdict.kind is VerdictKind.BLOCK
    assert verdict.reasons[0].startswith(WITHOUT_SUPPORT)


#: Одно предложение с призывом и ссылкой из настроек, но не только призыв: цена, бесплатное,
#: результат, гарантия, похвала себе. Модель цитирует его целиком — черновик задержан.
NOT_BARE = (
    f"Бесплатно покажем аудит на коротком созвоне: {CALL}.",
    f"Аудит за пятьсот долларов обсудим на коротком созвоне: {CALL}.",
    f"Покажем рост трафика вдвое на коротком созвоне: {CALL}.",
    f"Лучшие специалисты покажут всё на коротком созвоне: {CALL}.",
    f"Мы работаем с крупнейшими банками и покажем всё на коротком созвоне: {CALL}.",
    f"Наши клиенты довольны — подробнее на коротком созвоне: {CALL}.",
)
NOT_BARE_EN = (
    f"We guarantee first-page rankings, see you on a short call: {CALL}.",
    f"Our certified experts will walk you through it on a short call: {CALL}.",
    f"A free audit is waiting for you on a short call: {CALL}.",
)


@pytest.mark.parametrize(
    ("case_id", "sentence"),
    [
        *(("good-case-call-ru", one) for one in NOT_BARE),
        *(("good-info-en", one) for one in NOT_BARE_EN),
    ],
)
async def test_more_than_an_invitation_in_the_same_sentence_still_blocks(
    llm: Plug, case_id: str, sentence: str
) -> None:
    draft = CASES[case_id]["draft"]
    draft = draft.replace(_cta_sentence(draft), sentence)
    llm(judge=[_opinion(_claim(sentence))])

    verdict = await judge.verdict(_check(case_id, draft))

    assert verdict.kind is VerdictKind.BLOCK, draft
    assert verdict.reasons == (
        f"{WITHOUT_SUPPORT}: «{sentence}» — уберите его или возьмите из фактов",
    )


async def test_a_promise_in_the_call_to_action_is_not_set_aside(llm: Plug) -> None:
    """Обещания модели правило не трогает: они — опасный вид, и ловит их только модель."""
    sentence = f"Подробнее покажем на коротком созвоне: {CALL}."
    llm(judge=[_opinion(promises=(sentence,))])

    verdict = await judge.verdict(_check("good-case-call-ru"))

    assert verdict.kind is VerdictKind.BLOCK
    assert verdict.reasons[0].startswith("обещание вне базы")


def _live_like(answers: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Записанные ответы eval, где хорошие и опасные случаи отвечены как выше."""
    changed = copy.deepcopy(answers)
    for case_id, answer in {**LIVE, **WHOLE}.items():
        changed[case_id]["judge"] = answer
    return changed


def test_eval_gates_open_on_the_live_answers_and_keep_every_dangerous_case(
    model: Any, capsys: Capture
) -> None:
    """Механика ворот на ответах, как у живой модели 08.10: ложных block 0 из 12 при всех
    пойманных опасных, — без правила было бы 6 из 12 и закрытые ворота."""
    model(_live_like(ANSWERS))

    assert ev.main([]) == 0
    printed = capsys.readouterr().out
    assert "ОПАСНЫХ поймано: 14/14 (100%); ложных block на хороших: 0/12 (0%)" in printed
