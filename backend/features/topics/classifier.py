"""Тема сайта моделью: текст, который у нас уже есть, → код темы из общего списка.

**Отдельный вызов, а не второй вопрос судье.** Судья спрашивает, как сайт зарабатывает, и
его промпт стережёт отсутствие слов ниши (`publisher_judge.py`); тема — вопрос о предмете,
и список тем в его промпте подсказывал бы ему нишу. Вызов свой, дешёвая модель своя
(`llm.TOPIC_MODEL`), версия своя — точность темы и судьи меряются порознь.

**Текст — тот, что уже оплачен и прочитан.** Сниппет страницы из выдачи и главная, если её
открыл судья: тема определяется без единого лишнего захода на сайт. Главная важнее страницы:
выдача пришла под наш ключ и показывает одну статью, главная — о чём сайт целиком.
Заглушка защиты вместо текста главной в модель не идёт (`home_signals.looks_denied`).

**Текст сайта — чужие данные, а не указания.** Он идёт между метками `<<<SITE … SITE>>>`, и
метки, вписанные самим сайтом, гасятся похожими знаками: закрыть текст раньше времени сайт не
может. Промпт говорит модели, что между метками — данные (`prompts/classify.md`). Ответ — в
строгой форме JSON (`response_format`).

**Ответ проверяется по списку** (`answer.py`): явный `null` — «тема не ясна», это знание;
проза, обрезанный ответ и тема не из списка — сбой формы (`TopicVerdict.unreadable`): на домен
не пишется. **Отказ — с причиной, а не ноль**: модель не ответила (сеть, ключ, отказ
провайдера, пустой ответ) — темы нет, следующий проход спросит снова, причина — в итоге прохода.
У ключа нет кредитов — причина словами владельца (`TopicVerdict.no_credits`), и пачка дальше
модель не зовёт (`step.classify_many`, 20.10.2026).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import httpx

from backend.config import llm as llm_cfg
from backend.config import topics as cfg
from backend.features.donors.home_signals import HomeSignals, looks_denied
from backend.features.runs.planning import SerpText
from backend.features.topics.answer import parse
from backend.features.topics.taxonomy import prompt_list
from backend.shared.llm import Refusal, content_of, is_reasoning, post_chat, tokens_of

logger = logging.getLogger(__name__)

#: Тема для логов.
LOG_TOPIC = "тематика сайта"

#: Версия промпта и списка тем. Ложится на домен рядом с моделью: тема прежней версии —
#: несвежая, и точность против человека считается по версии, не смешивая старое с новым.
#: Правка промпта или списка без новой версии не пройдёт: хэш текста модели закреплён
#: в `tests/test_topic_classifier.py`.
TOPIC_VERSION = "topic-v1"

PROMPTS_DIR = Path(__file__).with_name("prompts")

#: Промпт — файлом, список тем — из `taxonomy.py`: одна правка темы меняет и экран,
#: и то, что видит модель. Подстановка — заменой, а не `format`: в промпте фигурные
#: скобки образца JSON.
SYSTEM = (
    (PROMPTS_DIR / "classify.md")
    .read_text(encoding="utf-8")
    .strip()
    .replace("{topics}", prompt_list())
)

#: Метки данных: текст сайта — между ними (`user_message`).
SITE_OPEN, SITE_CLOSE = "<<<SITE", "SITE>>>"

#: Потолок ответа у модели с рассуждением: он включает рассуждение, и на `low` его
#: больше, чем на `minimal`, — обрезанный ответ пришёл бы пустым.
REASONING_CEILING = 1500
#: Потолок ответа у модели без рассуждения: JSON из трёх полей.
PLAIN_CEILING = 200

#: Источник текста — на домен: по нему видно, по чему определена тема.
SOURCE_SERP = "serp"
SOURCE_HOME = "home"
MAX_NAV_SHOWN = 25
#: Сколько места держится странице из выдачи, когда главная длинная: иначе меню главной
#: вытесняло бы выдачу целиком, а источником всё равно значилась бы и она.
SERP_KEEP = 400


@dataclass(frozen=True, slots=True)
class SiteText:
    """Что модель прочтёт о сайте и откуда. Пустой текст — читать нечего."""

    text: str
    #: `serp`, `home` или `serp+home` — то, что осталось в тексте после потолка; пусто — нечего.
    source: str = ""

    @property
    def empty(self) -> bool:
        return not self.text


def _serp_text(serp: SerpText | None) -> str:
    if serp is None or serp.empty:
        return ""
    lines = [f"Страница из выдачи: {(serp.title or '').strip()}", (serp.description or "").strip()]
    text = "\n".join(line for line in lines if line.strip())
    return "" if looks_denied(text) else text


def _home_text(home: HomeSignals | None, stored_title: str | None) -> str:
    """Главная: прочитанная судьёй в этом прогоне — заголовок, описание, меню; иначе
    заголовок, сохранённый у домена прошлым судом (`domains.judge_home`)."""
    if home is not None and home.reached and (home.title or home.description):
        parts = [f"Главная: {home.title}".strip(), home.description.strip()]
        if home.nav:
            parts.append("Меню главной: " + " · ".join(home.nav[:MAX_NAV_SHOWN]))
    elif stored_title and stored_title.strip():
        parts = [f"Главная: {stored_title.strip()}"]
    else:
        return ""
    text = "\n".join(part for part in parts if part)
    # Заглушка защиты с кодом 200 — не главная сайта: по ней тема была бы угадана.
    return "" if looks_denied(text) else text


def site_text(
    serp: SerpText | None, home: HomeSignals | None = None, stored_title: str | None = None
) -> SiteText:
    """Текст сайта для модели: главная, потом страница из выдачи — сколько влезет в потолок.

    Источник считается по тому, что осталось после потолка: часть, срезанная целиком, в нём
    не значится. Выдаче держится место (`SERP_KEEP`), чтобы длинная главная её не вытеснила.
    """
    room = cfg.MAX_TEXT_CHARS
    serp_part, home_part = _serp_text(serp), _home_text(home, stored_title)
    home_kept = home_part[: max(0, room - min(len(serp_part), SERP_KEEP) - 1)]
    serp_kept = serp_part[: max(0, room - len(home_kept) - 1)] if home_kept else serp_part[:room]
    kept = {SOURCE_SERP: serp_kept, SOURCE_HOME: home_kept}
    return SiteText(
        text="\n".join(part for part in (home_kept, serp_kept) if part),
        source="+".join(name for name, part in kept.items() if part),
    )


@dataclass(frozen=True, slots=True)
class TopicVerdict:
    """Ответ модели о теме сайта и то, во что он обошёлся."""

    topic: str | None
    secondary: str | None
    reason: str
    model: str | None = None
    tokens: int = 0
    #: Токены ввода и вывода, как их назвал провайдер; у вывода своя цена, и рассуждение
    #: идёт в его счёт. Ноль и ноль — провайдер не назвал раздельно.
    prompt_tokens: int = 0
    completion_tokens: int = 0
    source: str = ""
    #: Темы нет — на домен не ложится, следующий проход спросит снова: модель не ответила
    #: (отказ, сеть, пустой ответ) или ответила не по форме (`unreadable`).
    unanswered: bool = False
    #: Ответ пришёл, а формы нет: проза, обрезан потолком, тема не из списка (`answer.py`).
    unreadable: bool = False
    #: У ключа модели нет кредитов: неответ, после которого пачка модель не зовёт.
    no_credits: bool = False

    @property
    def unclear(self) -> bool:
        """Модель прочла и сказала `null` — темы по тексту не понять. Это знание: на домен."""
        return not self.unanswered and self.topic is None


def _quiet(words: str) -> str:
    """Метки данных в чужом тексте — похожими знаками: закрыть текст раньше он не сможет."""
    return words.replace("<<<", "‹‹‹").replace(">>>", "›››")


def user_message(host: str, text: str) -> str:
    """Текст сайта — данными между метками; домен — строкой над ними."""
    return f"Домен: {_quiet(host)}\n{SITE_OPEN}\n{_quiet(text)}\n{SITE_CLOSE}"


def build_payload(model: str, host: str, text: str) -> dict[str, Any]:
    """Тело запроса: строгая форма JSON, потолок вывода — по семейству модели."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user_message(host, text)},
        ],
        "response_format": {"type": "json_object"},
    }
    if is_reasoning(model):
        payload["max_completion_tokens"] = REASONING_CEILING
        payload["reasoning_effort"] = cfg.REASONING_EFFORT
    else:
        payload["max_tokens"] = PLAIN_CEILING
        payload["temperature"] = 0
    return payload


def _count(value: object) -> int | None:
    """Число токенов из `usage`: целое не меньше нуля, иначе «не названо»."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def split_tokens(body: dict[str, Any]) -> tuple[int, int]:
    """Ввод и вывод из `usage`; не названы или не сходятся с итогом — ноль и ноль:
    по итогу пишется расход (`tokens_of`), и раздельные числа обязаны давать его."""
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return 0, 0
    prompt, completion = _count(usage.get("prompt_tokens")), _count(usage.get("completion_tokens"))
    if prompt is None or completion is None or prompt + completion != tokens_of(body):
        return 0, 0
    return prompt, completion


def _truncated(body: dict[str, Any]) -> bool:
    """Ответ обрезан потолком вывода: начало JSON без конца — не ответ."""
    choices = body.get("choices") or []
    first = choices[0] if choices and isinstance(choices[0], dict) else {}
    return first.get("finish_reason") == "length"


async def classify(
    http: httpx.AsyncClient,
    *,
    host: str,
    text: SiteText,
    model: str | None = None,
    api_key: str | None = None,
) -> TopicVerdict:
    """Тема одного сайта. Не бросает: любой сбой — `unanswered` с причиной."""
    chosen = model or llm_cfg.TOPIC_MODEL
    answer = await post_chat(
        http,
        api_key=api_key if api_key is not None else llm_cfg.API_KEY,
        payload=build_payload(chosen, host, text.text),
        topic=LOG_TOPIC,
    )
    if isinstance(answer, Refusal):
        logger.warning("%s: %s (%s)", LOG_TOPIC, answer, host)
        return TopicVerdict(
            None,
            None,
            str(answer),
            source=text.source,
            unanswered=True,
            no_credits=answer.no_credits,
        )
    # Ответ оплачен, каким бы он ни был: модель, токены и источник — у каждого исхода ниже.
    paid = TopicVerdict(
        None, None, "", chosen, tokens_of(answer), *split_tokens(answer), text.source
    )
    content = content_of(answer, topic=LOG_TOPIC)
    if not content:
        # Рассуждение съело потолок, и до ответа не дошло.
        return replace(paid, reason="пустой ответ модели", unanswered=True)
    if _truncated(answer):
        reason = "ответ модели обрезан потолком вывода"
        return replace(paid, reason=reason, unanswered=True, unreadable=True)
    parsed = parse(content)
    if parsed.unreadable:
        logger.warning("%s: %s (%s)", LOG_TOPIC, parsed.reason, host)
        return replace(paid, reason=parsed.reason, unanswered=True, unreadable=True)
    return replace(paid, topic=parsed.topic, secondary=parsed.secondary, reason=parsed.reason)
