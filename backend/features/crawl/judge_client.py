"""Вызов модели судьи рекламодателей: подсказка, тело запроса, потолок вывода и второй шанс цитате.

Здесь — только вызов: что видит модель, строгая форма у провайдера, токены и сбой словами. Факты
сверяет `judge_facts.py`, совет ставит `judge_rules.py`. Клиент не бросает: отказ провайдера,
обрезанный и пустой ответ — признаками ответа (`Said`), с токенами, если они оплачены. Ответ без
вердикта строки суда не создаёт — это решает вызывающий (`judge.py`).

**Что видит модель.** Наши строки — домен, анкор и пометки ссылки, — затем текст статьи и текст
главной, каждый между своими метками. Чужой текст — данные: метки внутри него гасятся похожими
знаками (`_quiet`), и закрыть данные раньше времени или сойти за наше правило он не может.

**Второй шанс цитате** (как у судьи продаж, #464): цитата не нашлась в тексте — модель спрашивают
ещё раз по тому же тексту с короткой подсказкой (`prompts/advertiser_judge_again.md`): какие слова не
нашлись — строкой JSON с погашенными метками — и правило дословной цитаты. Один раз на случай;
токены обоих вызовов — в счёт, повтор и его исход — признаками (`Said.retried`, `Said.retry_held`).

**Ключ — в момент вызова** (`llm_cfg.API_KEY`): общая фикстура тестов гасит его там, и клиент,
созданный до неё, не унёс бы ключ разработчика к настоящей модели (#427).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx

from backend.config import llm as llm_cfg
from backend.features.crawl import judge_facts
from backend.features.crawl.judge_facts import LinkFacts
from backend.features.crawl.judge_text import ARTICLE_LABELS, HOME_LABELS, JudgeText
from backend.shared.llm import (
    OUTPUT_LIMIT_MARK,
    ModelClient,
    Refusal,
    content_of,
    is_reasoning,
    post_chat,
    tokens_of,
)

logger = logging.getLogger(__name__)

TOPIC = "судья рекламодателей"
#: Версия подсказки. Меняется при КАЖДОЙ правке файлов подсказки: факты помнят её, и точность против
#: человека считается по когорте; отпечаток файлов (`prompt_print`) держит тест — правка файла без
#: смены версии краснит его.
PROMPT_VERSION = "advertiser-judge-v1"
PROMPT_PATH = Path(__file__).with_name("prompts") / "advertiser_judge.md"
AGAIN_PATH = Path(__file__).with_name("prompts") / "advertiser_judge_again.md"
#: Сколько знаков ненайденных цитат уходит во второй вопрос: цитата — до 30 слов.
AGAIN_QUOTE_CHARS = 300
#: Усилие рассуждения — как у судей площадки и продаж: на `minimal` модель читает только буквы.
REASONING_EFFORT = "low"
#: Потолки вывода: рассуждение входит в потолок, и обрезанный ответ пришёл бы пустым.
TOKENS_REASONING = 4000
TOKENS_PLAIN = 900
CUT_OFF = (
    "модель упёрлась в потолок вывода — рассуждение съело ответ; следующий проход спросит снова"
)
EMPTY = "пустой ответ модели"
#: Метки данных. Сайт может написать их сам, чтобы «закрыть» текст раньше времени: в его тексте
#: они заменяются похожими знаками.
ARTICLE_OPEN, ARTICLE_CLOSE = "<<<ARTICLE", "ARTICLE>>>"
HOME_OPEN, HOME_CLOSE = "<<<HOME", "HOME>>>"


@lru_cache(maxsize=1)
def load_prompt() -> str:
    """Подсказка файлом рядом с модулем: читается один раз на процесс."""
    return PROMPT_PATH.read_text(encoding="utf-8").strip()


@lru_cache(maxsize=1)
def load_again() -> str:
    """Подсказка второго вопроса; `{quote}` — место ненайденных цитат."""
    return AGAIN_PATH.read_text(encoding="utf-8").strip()


def prompt_print() -> str:
    """Отпечаток обоих файлов подсказки — часть когорты фактов (`judge_store.Cohort`)."""
    both = f"{load_prompt()}\n\n{load_again()}".encode()
    return hashlib.sha256(both).hexdigest()[:16]


def _quiet(words: str) -> str:
    """Метки данных в чужом тексте — похожими знаками: закрыть текст раньше он не сможет."""
    return words.replace("<<<", "‹‹‹").replace(">>>", "›››")


@dataclass(frozen=True, slots=True)
class Link:
    """Ссылка, о которой спрашивают: домен, анкор и пометки, найденные обходом (`outlinks`)."""

    target: str
    anchor: str = ""
    sponsored: bool = False
    nofollow: bool = False
    ugc: bool = False
    in_body: bool = True
    #: Пометка статьи, которую нашёл обход (`page_facts`), — её слова.
    page_label: str | None = None
    published: date | None = None


def marks_of(link: Link) -> str:
    """Пометки ссылки нашими словами — строкой над данными."""
    rel = [name for name, on in (("sponsored", link.sponsored), ("nofollow", link.nofollow),
                                 ("ugc", link.ugc)) if on]  # fmt: skip
    marks = [f"rel: {', '.join(rel) if rel else 'none'}"]
    marks.append(f"in the article's text: {'yes' if link.in_body else 'no'}")
    if link.page_label:
        marks.append(f"our crawler saw the page label {json.dumps(link.page_label[:80])}")
    if link.published is not None:
        marks.append(f"article date: {link.published.isoformat()}")
    return "; ".join(marks)


def _block(text: JudgeText, labels: tuple[str, ...]) -> str:
    return "\n".join(f"{label}: {words}" for label, words in text.lines if label in labels)


def user_message(link: Link, text: JudgeText) -> str:
    """Наши строки о ссылке — над метками; статья и главная — данными между своими метками."""
    anchor = json.dumps(_quiet(link.anchor.strip()[:200]), ensure_ascii=False)
    return (
        f"Domain of the link: {_quiet(link.target)}\n"
        f"Anchor of the link: {anchor}\n"
        f"Marks of the link: {_quiet(marks_of(link))}\n"
        f"{ARTICLE_OPEN}\n{_quiet(_block(text, ARTICLE_LABELS))}\n{ARTICLE_CLOSE}\n"
        f"{HOME_OPEN}\n{_quiet(_block(text, HOME_LABELS))}\n{HOME_CLOSE}"
    )


def again_message(user: str, missing: tuple[str, ...]) -> str:
    """Второй вопрос: тот же вопрос по тому же тексту и подсказка — какие слова не нашлись. Слова —
    строкой JSON с погашенными метками: они пришли из ответа модели о чужом тексте."""
    words = json.dumps(_quiet(" | ".join(missing)[:AGAIN_QUOTE_CHARS]), ensure_ascii=False)
    return f"{user}\n{load_again().replace('{quote}', words)}"


def _room(model: str) -> dict[str, Any]:
    """Потолок вывода по семейству модели: у модели с рассуждением оно входит в потолок."""
    if is_reasoning(model):
        return {"reasoning_effort": REASONING_EFFORT, "max_completion_tokens": TOKENS_REASONING}
    return {"temperature": 0, "max_tokens": TOKENS_PLAIN}


def build_payload(model: str, *, user: str, system: str) -> dict[str, Any]:
    """Тело запроса: строгая форма фактов у провайдера и потолок вывода по модели."""
    form = {"type": "json_schema", "json_schema": {**judge_facts.SCHEMA, "strict": True}}
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    return {"model": model, "messages": messages, "response_format": form, **_room(model)}


def cut_by_room(body: dict[str, Any]) -> bool:
    """Ответ оборван потолком вывода: первая альтернатива кончилась на `length`."""
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return False
    return choices[0].get("finish_reason") == "length"


def out_of_room(refusal: Refusal) -> bool:
    """Отказ 400 «упёрлась в потолок вывода» — тот же обрезанный ответ, что 200 с `length`."""
    return refusal.status == 400 and OUTPUT_LIMIT_MARK in refusal.detail.lower()


@dataclass(frozen=True, slots=True)
class Said:
    """Что модель сказала о ссылке: факты — или почему их нет; токены всех вызовов случая.

    Нет фактов — один из признаков: `refusal` (отказ провайдера), `cut_off` (упёрлась в потолок
    вывода), `empty` (пустой ответ), `broken` (не строгая форма), `missing` (цитаты, которых в
    тексте нет). `no_credits` — у ключа нет денег: проход встаёт."""

    facts: LinkFacts | None
    failure: str = ""
    tokens: int = 0
    refusal: Refusal | None = None
    cut_off: bool = False
    empty: bool = False
    broken: bool = False
    missing: tuple[str, ...] = ()
    no_credits: bool = False
    retried: bool = False
    retry_held: bool = False


@dataclass(frozen=True, slots=True)
class Answer:
    """Текст ответа модели и чего стоил вызов."""

    content: str
    tokens: int = 0


def second_chance(first: Said, again: Said) -> Said:
    """Итог второго вопроса: факты нового ответа — или ненайденные цитаты. Новый ответ не дал ничего
    лучше (сбой провайдера, не та форма) — ненайденные цитаты первого. Токены — обоих вызовов. Нет
    кредитов на втором — признак едет дальше: проход встанет."""
    tokens = first.tokens + again.tokens
    if again.facts is not None:
        return Said(again.facts, tokens=tokens, retried=True, retry_held=True)
    if again.missing:
        return Said(None, again.failure, tokens, missing=again.missing, retried=True)
    return Said(
        None,
        first.failure,
        tokens,
        missing=first.missing,
        no_credits=again.no_credits,
        retried=True,
    )


class JudgeClient(ModelClient):
    """Вызовы модели судьи рекламодателей. Не бросает: любой сбой — признаком ответа."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        model: str | None = None,
        api_key: str | None = None,
    ) -> None:
        super().__init__(client, model=model, api_key=api_key)
        self._given_key = api_key

    def _default_model(self) -> str:
        return llm_cfg.ADVERTISER_JUDGE_MODEL

    def _key(self) -> str:
        """Ключ данный — или общий в момент вызова (шапка модуля)."""
        return self._given_key if self._given_key is not None else llm_cfg.API_KEY

    async def _ask(self, *, user: str, system: str, host: str) -> Answer | Said:
        """Один вызов строгой формы: текст ответа — или почему его нет, признаками `Said`."""
        payload = build_payload(self._model, user=user, system=system)
        body = await post_chat(self._http, api_key=self._key(), payload=payload, topic=TOPIC)
        if isinstance(body, Refusal):
            logger.warning("%s: %s", TOPIC, body, extra={"host": host})
            if out_of_room(body):
                return Said(None, CUT_OFF, cut_off=True)
            return Said(
                None, f"модель не ответила: {body}", refusal=body, no_credits=body.no_credits
            )
        tokens = tokens_of(body)
        content = content_of(body, topic=TOPIC)
        if cut_by_room(body):
            # Пустой или обрезанный посередине JSON — одно и то же: ответа нет, оплачен весь вывод.
            logger.warning(
                "%s: ответ обрезан потолком вывода", TOPIC, extra={"host": host, "tokens": tokens}
            )
            return Said(None, CUT_OFF, tokens, cut_off=True)
        if not content:
            return Said(None, EMPTY, tokens, empty=True)
        return Answer(content, tokens)

    async def _facts(self, *, user: str, system: str, text: JudgeText, host: str) -> Said:
        """Один вызов фактов: факты по тексту — или почему их нет."""
        asked = await self._ask(user=user, system=system, host=host)
        if isinstance(asked, Said):
            return asked
        found = judge_facts.read(asked.content, text)
        return Said(
            found.facts, found.failure, asked.tokens, broken=found.broken, missing=found.missing
        )

    async def judge(
        self, *, user: str, text: JudgeText, host: str = "", system: str | None = None
    ) -> Said:
        """Факты одной ссылки; цитата не нашлась — второй вопрос с подсказкой, один на случай."""
        prompt = system or load_prompt()
        said = await self._facts(user=user, system=prompt, text=text, host=host)
        if not said.missing:
            return said
        again = await self._facts(
            user=again_message(user, said.missing), system=prompt, text=text, host=host
        )
        logger.info(
            "судья рекламодателей: цитата не нашлась — второй вопрос",
            extra={"host": host, "held": again.facts is not None},
        )
        return second_chance(said, again)
