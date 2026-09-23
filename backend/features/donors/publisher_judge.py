"""Площадка или бренд: судья по тексту выдачи.

**Вердикт по порогам не может ответить на этот вопрос.** DR, трафик, ключи
и гео у букмекера отличные — по построению. Замер 22.09 на нише ставок: из
23 годных доноров одиннадцать оказались самими букмекерами. Цена двойная:
юниты за метрики невозможного донора и репутация отправителя на письме
бренду с предложением купить у него же размещение.

**Различается не «бренд против не-бренда», а способ заработка:** продаёт
своё или пишет про чужое. Первое зависит от ниши, второе — нет, поэтому
списками брендов это не закрывается, а судится по тексту, который сайт сам
о себе написал.

**Текст берётся из выдачи, а не с сайта.** Четверть доменов не отдаёт
страницу вовсе — закрываются в основном крупные бренды, то есть ровно те,
кого важнее всего опознать. Сниппет приходит из индекса поиска, есть и
у них, и уже оплачен вместе с выдачей.

**Цитата обязательна.** Модель возвращает дословный кусок исходного текста,
и он проверяется вхождением. Не нашлось — вердикт понижается до «посмотри»,
а не принимается на веру: это тот же приём, что у разбора ответов, где
названное число обязано встречаться в письме.

**Отказ модели не отбрасывает домен.** Сбой, пустой ответ, нечитаемый
формат — всё это «посмотри», а не «не подходит»: хоронить домен за то, что
у нас не сработала модель, дороже, чем показать его человеку.

**Граница, найденная дымовым прогоном 23.09.** Сайт, который и публикует,
и торгует своим (`kingarthurbaking.com` — рецепты плюс собственный магазин
муки), по букве правила получает `sells_own`: главная страница говорит
«купи у нас». Для линкбилдинга это, скорее всего, ошибка — статьи там
принимают. Отсюда два следствия: судить надо по СНИППЕТУ СТАТЬИ из выдачи,
а не по заглавной странице магазина, и такие случаи обязаны доезжать до
человека, а не резаться молча. Оба уже соблюдены: текст берётся из выдачи,
а включать судью в `enforce` до замера нельзя.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx

from backend.config import judge as cfg
from backend.config import llm as llm_cfg
from backend.shared.llm import Refusal, content_of, is_reasoning, post_chat, tokens_of

logger = logging.getLogger(__name__)

#: Тема для логов: по ней видно, что именно осталось несделанным.
TOPIC = "судья площадки"


class Intent(StrEnum):
    """Способ заработка сайта — то, что спрашивается у модели."""

    SELLS_OWN = "sells_own"  # у себя покупают, бронируют, вносят депозит
    REFERS_OUT = "refers_out"  # обозревает чужих и уводит к ним
    EDITORIAL_ADS = "editorial_ads"  # публикует статьи, живёт с рекламы
    NONE = "none"  # форум, госорган, личная страница
    UNKNOWN = "unknown"  # судить было не по чему


class Recommendation(StrEnum):
    """Что судья советует сделать. Полос три, и средняя — не вежливость:
    спорное и безданное обязаны попадать к человеку, а не в отказ."""

    ACCEPT = "accept"
    REVIEW = "review"
    REJECT = "reject"


#: Намерение → совет. Площадка отсылает наружу или живёт с рекламы;
#: всё остальное — либо продаёт себя, либо не площадка вовсе.
ADVICE: dict[Intent, Recommendation] = {
    Intent.REFERS_OUT: Recommendation.ACCEPT,
    Intent.EDITORIAL_ADS: Recommendation.ACCEPT,
    Intent.SELLS_OWN: Recommendation.REJECT,
    Intent.NONE: Recommendation.REJECT,
    Intent.UNKNOWN: Recommendation.REVIEW,
}

SYSTEM = (
    "Ты определяешь, КАК сайт зарабатывает, по тексту из поисковой выдачи. "
    "Не оцениваешь качество и не угадываешь нишу.\n"
    "Верни строгий JSON: {\"intent\": ..., \"quote\": ..., \"why\": ...}\n"
    "intent — одно из: sells_own (у него покупают, бронируют, вносят депозит, "
    "оформляют подписку на его собственную услугу), refers_out (обозревает и "
    "сравнивает чужих, уводит к ним), editorial_ads (публикует статьи и новости, "
    "живёт с рекламы и подписок), none (форум, госорган, личная страница, "
    "интернет-магазин товаров).\n"
    "quote — ДОСЛОВНЫЙ кусок присланного текста, 3-12 слов, на котором "
    "основан вывод. Не пересказывай и не переводи.\n"
    "why — одно короткое предложение.\n"
    "Похвала себе не делает обзорщиком: заголовок вида "
    "«Top-Rated <Brand> — Sign Up Today» это sells_own, а не refers_out: "
    "хвалят себя, а не сравнивают чужих."
)

# ⚠ В промпте нет ни одного слова ниши, и это стережёт тест
# `test_в_промпте_нет_ни_одного_слова_ниши`. Первая же подсказка вида
# «казино — это оператор» чинит один рынок и ломает перенос на соседний:
# там такой подсказки нет, а судья на неё уже опирается. Пример выше про
# ФОРМУ вывода, а не про предмет.


@dataclass(frozen=True, slots=True)
class Judgement:
    """Вердикт судьи и то, чем он подтверждён."""

    intent: Intent
    recommendation: Recommendation
    quote: str | None
    reason: str
    model: str | None = None
    #: Токены вызова. Ноль у вердиктов, которые модель не стоили:
    #: платформа из денилиста и домен без текста выдачи.
    tokens: int = 0

    @property
    def would_cut(self) -> bool:
        """Отрезал бы в `enforce`. В наблюдении именно это и считается."""
        return self.recommendation is Recommendation.REJECT


def dns_labels(host: str) -> list[str]:
    """Метки домена без порта и хвоста пути."""
    clean = host.strip().lower().split("/")[0].split(":")[0]
    return [label for label in clean.split(".") if label]


def is_platform(host: str) -> bool:
    """Платформа из денилиста.

    ⚠ Совпадение по ЦЕЛОЙ метке. Вхождение строки денило бы
    `google-maps-guide.co.ke` — сайт, к платформе отношения не имеющий.
    """
    return any(label in cfg.PLATFORM_LABELS for label in dns_labels(host))


#: Признаки того, что нам отдали не страницу сайта, а отказ. Сравнение по
#: нижнему регистру, вхождением: формулировки у защит разные, а слова общие.
#:
#: ⚠ Без этой проверки судья выносит вердикт по тексту вроде «Access to this
#: page has been denied» — и иногда угадывает, что и есть худший случай:
#: замер соврал в свою пользу, а мы записали случайное попадание в точность.
#: Класс назван в каноне соседней системы (`gnc.com`), у нас пойман на живом
#: прогоне 23.09: `edmunds.com` со страницей 403 получил «площадка» (угадал),
#: `trivago.com` с той же страницей — «не площадка» (промахнулся). Ни то,
#: ни другое не знание.
DENIAL_MARKERS: tuple[str, ...] = (
    "access denied",
    "access to this page",
    "403",
    "forbidden",
    "attention required",
    "just a moment",
    "are you human",
    "verify you are human",
    "captcha",
    "bot detection",
    "request blocked",
    "unusual traffic",
    "not acceptable",
    "service unavailable",
    "site temporarily unavailable",
)


def looks_denied(text: str) -> bool:
    """Текст похож на отказ доступа, а не на страницу сайта."""
    lowered = text.lower()
    return any(marker in lowered for marker in DENIAL_MARKERS)


def source_text(title: str | None, description: str | None) -> str:
    """Текст, который увидит модель. Пусто — судить не по чему."""
    parts = [part.strip() for part in (title, description) if part and part.strip()]
    return "\n".join(parts)[: cfg.MAX_TEXT_CHARS]


def build_payload(model: str, host: str, text: str) -> dict[str, Any]:
    """Тело запроса с поправкой на семейство модели."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Домен: {host}\nТекст выдачи:\n{text}"},
        ],
    }
    if is_reasoning(model):
        payload["max_completion_tokens"] = 600
        payload["reasoning_effort"] = "minimal"
    else:
        payload["max_tokens"] = 300
        payload["temperature"] = 0
    return payload


def _quote_found(quote: str, text: str) -> bool:
    """Цитата обязана встречаться в исходном тексте.

    Сравнение по свёрнутым пробелам и без регистра: модель переносит строки
    иначе, чем провайдер, и на этом честная цитата иначе не прошла бы.
    """
    def squeeze(value: str) -> str:
        return " ".join(value.lower().split())

    return squeeze(quote) in squeeze(text)


def parse(content: str, text: str) -> Judgement:
    """Ответ модели → вердикт. Непонятный ответ — «посмотри», не отказ."""
    try:
        body = json.loads(content[content.find("{") : content.rfind("}") + 1])
    except (ValueError, TypeError):
        return Judgement(
            Intent.UNKNOWN, Recommendation.REVIEW, None, "ответ модели не разобрать"
        )
    if not isinstance(body, dict):
        return Judgement(
            Intent.UNKNOWN, Recommendation.REVIEW, None, "ответ модели не объект"
        )

    raw_intent = str(body.get("intent", "")).strip().lower()
    try:
        intent = Intent(raw_intent)
    except ValueError:
        return Judgement(
            Intent.UNKNOWN,
            Recommendation.REVIEW,
            None,
            f"намерение не из списка: {raw_intent[:40]!r}",
        )

    quote = body.get("quote")
    quote = quote.strip() if isinstance(quote, str) and quote.strip() else None
    why = str(body.get("why", "")).strip()[:200]

    if quote is None:
        return Judgement(intent, Recommendation.REVIEW, None, "модель не дала цитаты")
    if not _quote_found(quote, text):
        # Цитата, которой нет в тексте, — признак того, что модель сочинила,
        # а не прочитала. Вердикт при этом сохраняется: он уедет человеку.
        return Judgement(
            intent, Recommendation.REVIEW, quote, "цитата не найдена в тексте выдачи"
        )

    return Judgement(intent, ADVICE[intent], quote, why or "по тексту выдачи")


async def judge_host(
    http: httpx.AsyncClient,
    *,
    host: str,
    title: str | None,
    description: str | None,
    model: str | None = None,
    api_key: str | None = None,
) -> Judgement:
    """Вердикт по одному домену. Не бросает: любой сбой — «посмотри»."""
    if is_platform(host):
        return Judgement(
            Intent.NONE, Recommendation.REJECT, None, "платформа из денилиста"
        )

    text = source_text(title, description)
    if not text:
        return Judgement(
            Intent.UNKNOWN, Recommendation.REVIEW, None, "выдача не дала ни заголовка, ни описания"
        )
    if looks_denied(text):
        # Судить отказ доступа нельзя даже когда получается: правильный
        # ответ здесь был бы угадан, а не прочитан.
        return Judgement(
            Intent.UNKNOWN,
            Recommendation.REVIEW,
            None,
            f"вместо страницы пришёл отказ доступа: {text[:60]!r}",
        )

    chosen = model or llm_cfg.JUDGE_MODEL
    answer = await post_chat(
        http,
        api_key=api_key if api_key is not None else llm_cfg.API_KEY,
        payload=build_payload(chosen, host, text),
        topic=TOPIC,
    )
    if isinstance(answer, Refusal):
        logger.warning("%s: %s (%s)", TOPIC, answer, host)
        return Judgement(Intent.UNKNOWN, Recommendation.REVIEW, None, str(answer))

    content = content_of(answer, topic=TOPIC)
    if not content:
        return Judgement(Intent.UNKNOWN, Recommendation.REVIEW, None, "пустой ответ модели")

    verdict = parse(content, text)
    return Judgement(
        verdict.intent,
        verdict.recommendation,
        verdict.quote,
        verdict.reason,
        chosen,
        tokens_of(answer),
    )
