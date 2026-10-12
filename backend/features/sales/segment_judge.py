"""Судья сегмента: подходит ли сайт под сегмент гипотезы — той же формы, что судья площадки.

Судья площадки доноров (`donors/publisher_judge.py`) отвечает на «продаёт ли сайт
размещение»; сбору продаж нужен другой вопрос — «подходит ли сайт под сегмент», и сегмент
у каждой гипотезы свой: описание гипотезы в базе (репозиторий публичный). Форма — та же:

1. **Правила до модели** — бесплатные и точные: платформа из денилиста и госзона
   отсекаются, пустой текст и страница-отказ идут человеку (правила — судьи площадки,
   без копии). Правило режет в любом режиме: это знание, а не мнение модели, мерить
   его нечего.
2. **Модель — строгая форма JSON `{fit, quote, why}`**: «да», «нет», «не понять».
3. **Цитата — дословно из текста сайта**, внутри одной строки, не короче двух слов;
   иначе «на проверку»: цитата, которой нет в тексте, — признак того, что модель
   сочинила, а не прочла. Такая цитата не хранится.
4. **Три полосы**: принять, на проверку, отказ.
5. **Отказ модели не кэшируется**: сбой, пустой ответ — «на проверку» с `unanswered`,
   вердикта нет, и следующий проход судит домен снова.

Текст сайта — выдача (заголовок и описание страницы, что пришла под ключ) и главная
(заголовок, описание, меню): способ жизни — свойство сайта, а не страницы. Текст — данные,
а не указание: метки данных в нём гасятся, как у разбора ответа (`reply_kind.py`).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.config import llm as llm_cfg
from backend.config.judge import JudgeMode
from backend.features.donors.home_signals import HomeSignals, looks_denied
from backend.features.donors.publisher_judge import is_platform, is_public_zone
from backend.features.keywords.hygiene import normalize
from backend.features.sales.segment_models import Decider, Lane
from backend.shared.llm import ModelClient, Refusal, content_of, is_reasoning, post_chat, tokens_of

logger = logging.getLogger(__name__)

TOPIC = "судья сегмента продаж"
#: Версия промпта. Меняется при КАЖДОЙ правке файла промпта: вердикт хранит её, и точность
#: против человека считается по версии.
PROMPT_VERSION = "sales-segment-v1"
PROMPT_PATH = Path(__file__).with_name("prompts") / "segment.md"
#: Операция журнала расхода (`core/usage.OPERATION_PROVIDERS`); её считает потолок продаж.
OPERATION = "sales_segment_judge"
#: Усилие рассуждения — как у судьи площадки: на `minimal` модель читает только буквы.
REASONING_EFFORT = "low"
#: Потолки вывода: рассуждение входит в потолок, и обрезанный ответ пришёл бы пустым.
TOKENS_REASONING = 2000
TOKENS_PLAIN = 300
#: Сколько знаков текста сайта видит модель: сниппет и шапка главной короткие, потолок —
#: против аномалии страницы, а не ради экономии.
MAX_TEXT_CHARS = 2000
#: Цитата короче — не доказательство: одно слово найдётся в любом меню.
MIN_QUOTE_WORDS = 2
#: Ответ модели → полоса.
FITS = {"yes": Lane.ACCEPT, "no": Lane.REJECT, "unsure": Lane.REVIEW}

#: Метки данных. Сайт может написать их сам, чтобы «закрыть» текст раньше времени:
#: в его тексте они заменяются похожими знаками.
SEGMENT_OPEN, SEGMENT_CLOSE = "<<<SEGMENT", "SEGMENT>>>"
SITE_OPEN, SITE_CLOSE = "<<<SITE", "SITE>>>"

#: Подписи строк текста сайта: свои слова, не сайта — цитировать их нельзя.
PAGE_TITLE = "Заголовок в выдаче"
PAGE_DESCRIPTION = "Описание в выдаче"
HOME_TITLE = "Заголовок главной"
HOME_DESCRIPTION = "Описание главной"
HOME_MENU = "Меню главной"
LABELS = (PAGE_TITLE, PAGE_DESCRIPTION, HOME_TITLE, HOME_DESCRIPTION, HOME_MENU)


@lru_cache(maxsize=1)
def load_prompt() -> str:
    """Промпт файлом рядом с модулем: читается один раз на процесс."""
    return PROMPT_PATH.read_text(encoding="utf-8").strip()


def segment_digest(description: str) -> str:
    """Отпечаток описания сегмента: правка описания — новый вопрос модели, и прежний
    вердикт на него не годится. Пробелы не в счёт — их правка вопрос не меняет."""
    return hashlib.sha256(" ".join(description.split()).encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True, slots=True)
class SiteText:
    """Текст сайта для судьи — по строке на кусок: (подпись, слова сайта)."""

    lines: tuple[tuple[str, str], ...]

    @property
    def empty(self) -> bool:
        return not self.lines

    def for_model(self) -> str:
        """Как его видит модель — и как он хранится у кандидата."""
        return "\n".join(f"{label}: {words}" for label, words in self.lines)

    def holds(self, quote: str) -> bool:
        """Цитата — дословно внутри одной строки сайта. Сравнение без регистра, пробелы
        свёрнуты, тире и кавычки — одним знаком: модель переносит строки и ставит
        типографские знаки иначе, чем сайт, и честная цитата иначе не прошла бы."""
        wanted = normalize(quote)
        return bool(wanted) and any(wanted in normalize(words) for _, words in self.lines)

    @classmethod
    def parse(cls, stored: str) -> SiteText:
        """Текст, сохранённый у кандидата, — обратно в строки: по нему сверяется цитата."""
        lines: list[tuple[str, str]] = []
        for line in stored.splitlines():
            label, _, words = line.partition(": ")
            if label in LABELS and words:
                lines.append((label, words))
        return cls(tuple(lines))


def site_text(
    *,
    title: str | None,
    description: str | None,
    home: HomeSignals | None = None,
) -> SiteText:
    """Выдача и главная → текст судьи. Пустые куски не попадают; не больше
    `MAX_TEXT_CHARS` знаков: хвост последней строки срезается."""
    pieces: list[tuple[str, str | None]] = [(PAGE_TITLE, title), (PAGE_DESCRIPTION, description)]
    if home is not None and home.reached:
        menu = " · ".join(home.nav) if home.nav else None
        pieces += [
            (HOME_TITLE, home.title),
            (HOME_DESCRIPTION, home.description),
            (HOME_MENU, menu),
        ]
    lines: list[tuple[str, str]] = []
    left = MAX_TEXT_CHARS
    for label, words in pieces:
        clean = " ".join((words or "").split())
        if not clean or left <= 0:
            continue
        lines.append((label, clean[:left]))
        left -= len(clean)
    return SiteText(tuple(lines))


@dataclass(frozen=True, slots=True)
class SegmentVerdict:
    """Вердикт судьи и то, чем он подтверждён."""

    lane: Lane
    reason: str
    quote: str | None = None
    model: str | None = None
    tokens: int = 0
    decided_by: Decider = Decider.MODEL
    #: Модель не ответила: вердикта нет, на домен не ложится, кэшем не становится.
    unanswered: bool = False

    @property
    def would_cut(self) -> bool:
        """Отрезал бы в `enforce`: в наблюдении считается именно это."""
        return self.lane is Lane.REJECT


def _ruled(lane: Lane, reason: str) -> SegmentVerdict:
    return SegmentVerdict(lane, reason, decided_by=Decider.RULE)


def by_host(host: str) -> SegmentVerdict | None:
    """Правила по одному имени домена — до главной: платформа и госзона режутся сразу,
    сайт такому домену не открываем вовсе."""
    if is_platform(host):
        return _ruled(Lane.REJECT, "платформа из денилиста: это площадка, а не сайт компании")
    if is_public_zone(host):
        return _ruled(Lane.REJECT, "государственная или учебная зона: не компания сегмента")
    return None


def by_text(text: SiteText) -> SegmentVerdict | None:
    """Правила по тексту: пустое и страница-отказ — человеку. Угаданный по отказу доступа
    вердикт был бы случайным попаданием, а не знанием."""
    if text.empty:
        return _ruled(Lane.REVIEW, "выдача и главная не дали текста — судить не по чему")
    if looks_denied(text.for_model()):
        return _ruled(Lane.REVIEW, "вместо страницы пришёл отказ доступа — судить не по чему")
    return None


def before_model(host: str, text: SiteText) -> SegmentVerdict | None:
    """Всё, что решается без модели и не стоит ни одного токена. Порядок — от
    окончательного к осторожному: платформа и госзона режутся, пустое и отказ — человеку."""
    return by_host(host) or by_text(text)


def _quiet(words: str) -> str:
    """Метки данных в чужом тексте — похожими знаками: закрыть текст раньше он не сможет."""
    return words.replace("<<<", "‹‹‹").replace(">>>", "›››")


def user_message(*, host: str, segment: str, text: SiteText) -> str:
    """Сегмент и текст сайта — данными между метками; домен — строкой над ними."""
    return (
        f"Domain: {_quiet(host)}\n{SEGMENT_OPEN}\n{_quiet(segment.strip())}\n{SEGMENT_CLOSE}\n"
        f"{SITE_OPEN}\n{_quiet(text.for_model())}\n{SITE_CLOSE}"
    )


def build_payload(model: str, *, user: str, system: str | None = None) -> dict[str, Any]:
    """Тело запроса: строгая форма JSON, потолок вывода — по семейству модели."""
    limits: dict[str, Any] = (
        {"max_completion_tokens": TOKENS_REASONING, "reasoning_effort": REASONING_EFFORT}
        if is_reasoning(model)
        else {"max_tokens": TOKENS_PLAIN, "temperature": 0}
    )
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system or load_prompt()},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
        **limits,
    }


def _form(content: str) -> dict[str, Any] | str:
    """Строгая форма ответа: объект с `fit` из списка. Иначе — почему нет, словами."""
    try:
        body = json.loads(content)
    except ValueError:
        logger.info("%s: ответ модели не JSON", TOPIC, extra={"content": content[:120]})
        return "ответ модели не разобран"
    if not isinstance(body, dict):
        return "ответ модели не объект"
    fit = str(body.get("fit") or "").strip().lower()
    if fit not in FITS:
        logger.info("%s: ответ не из списка", TOPIC, extra={"fit": fit[:40]})
        return f"ответ модели не из списка: {fit[:40]!r}"
    return {**body, "fit": fit}


def _unproven(quote: str | None, text: SiteText) -> str | None:
    """Почему цитата не доказывает вердикт — словами; `None` — доказывает."""
    if quote is None:
        return "модель не дала цитаты"
    if len(quote.split()) < MIN_QUOTE_WORDS:
        return "цитата короче двух слов — не доказательство"
    if not text.holds(quote):
        return "цитаты нет в тексте сайта — модель не прочла, а сочинила"
    return None


def parse(content: str, text: SiteText) -> SegmentVerdict:
    """Ответ модели → вердикт. Непонятный ответ и цитата не из текста — «на проверку»,
    а не отказ; выдуманная цитата не хранится."""
    form = _form(content)
    if isinstance(form, str):
        return SegmentVerdict(Lane.REVIEW, form)
    raw = form.get("quote")
    quote = raw.strip() if isinstance(raw, str) and raw.strip() else None
    if (why_not := _unproven(quote, text)) is not None:
        return SegmentVerdict(Lane.REVIEW, why_not)
    why = str(form.get("why") or "").strip()[:300]
    return SegmentVerdict(FITS[form["fit"]], why or "по тексту сайта", quote)


class SegmentClient(ModelClient):
    """Вызов модели судьи сегмента. Не бросает: любой сбой — «на проверку»."""

    def _default_model(self) -> str:
        return llm_cfg.SALES_SEGMENT_MODEL

    async def judge(
        self, *, host: str, segment: str, text: SiteText, system: str | None = None
    ) -> SegmentVerdict:
        """Вердикт по одному сайту. Правила до модели — у вызывающего (`decide`)."""
        body = await post_chat(
            self._http,
            api_key=self._api_key,
            payload=build_payload(
                self._model, user=user_message(host=host, segment=segment, text=text), system=system
            ),
            topic=TOPIC,
        )
        if isinstance(body, Refusal):
            logger.warning("%s: %s", TOPIC, body, extra={"host": host})
            return SegmentVerdict(Lane.REVIEW, f"модель не ответила: {body}", unanswered=True)
        tokens = tokens_of(body)
        content = content_of(body, topic=TOPIC)
        if not content:
            # Оплачен: рассуждение съело потолок и до ответа не дошло.
            return SegmentVerdict(
                Lane.REVIEW,
                "пустой ответ модели",
                model=self._model,
                tokens=tokens,
                unanswered=True,
            )
        return replace(parse(content, text), model=self._model, tokens=tokens)


async def decide(
    client: SegmentClient, *, host: str, segment: str, text: SiteText, system: str | None = None
) -> SegmentVerdict:
    """Правила, затем модель. Не бросает. `system` — промпт вместо файла: так eval гоняет
    испорченный промпт (`scripts/eval_sales_segment.py --spoil`), не трогая файл."""
    ruled = before_model(host, text)
    if ruled is not None:
        return ruled
    return await client.judge(host=host, segment=segment, text=text, system=system)


#: Полоса кандидата при выключенном судье: модель не зовётся, решает человек.
JUDGE_OFF = "судья сегмента выключен — решает человек"


def effective(verdict: SegmentVerdict, mode: JudgeMode) -> tuple[Lane, str]:
    """Полоса кандидата по вердикту и режиму (`JudgeMode`, как у судьи площадки).

    Правило режет в любом режиме. В наблюдении (`shadow`) отказ модели не отсекает сайт,
    а отдаёт его человеку с пометкой «отрезал бы»: так видно, насколько отказам можно
    верить, до того как они начнут резать сами (`enforce`). Принятое и «на проверку» —
    как сказал судья в обоих режимах."""
    if verdict.decided_by is Decider.RULE or mode is JudgeMode.ENFORCE:
        return verdict.lane, verdict.reason
    if verdict.lane is Lane.REJECT:
        return Lane.REVIEW, f"судья в наблюдении отрезал бы: {verdict.reason}"
    return verdict.lane, verdict.reason
