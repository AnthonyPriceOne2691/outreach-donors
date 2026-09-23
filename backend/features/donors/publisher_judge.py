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
from backend.features.donors.home_signals import HomeSignals, looks_denied
from backend.shared.llm import Refusal, content_of, is_reasoning, post_chat, tokens_of

logger = logging.getLogger(__name__)

#: Тема для логов: по ней видно, что именно осталось несделанным.
TOPIC = "судья площадки"


class Intent(StrEnum):
    """Способ заработка сайта — то, что спрашивается у модели."""

    SELLS_PLACEMENT = "sells_placement"  # продаёт статьи и ссылки У СЕБЯ — донор
    LINK_VENDOR = "link_vendor"  # продаёт размещение на ЧУЖИХ сайтах — посредник
    SELLS_OWN = "sells_own"  # у себя покупают, бронируют, вносят депозит
    REFERS_OUT = "refers_out"  # обозревает чужих и уводит к ним
    EDITORIAL_ADS = "editorial_ads"  # публикует статьи, живёт с рекламы
    NON_COMMERCIAL = "non_commercial"  # по уставу не продаёт ни рекламы, ни места
    NONE = "none"  # форум, личная страница, справочник без рекламы
    UNKNOWN = "unknown"  # судить было не по чему


class Recommendation(StrEnum):
    """Что судья советует сделать. Полос три, и средняя — не вежливость:
    спорное и безданное обязаны попадать к человеку, а не в отказ."""

    ACCEPT = "accept"
    REVIEW = "review"
    REJECT = "reject"


class Decider(StrEnum):
    """Кто вынес вердикт. Точность считается по каждому отдельно.

    Без этой отметки у замера одно число на всех, и не видно, какой слой
    ошибается: правило, которому можно верить без взгляда человека, или
    модель, которой нельзя.
    """

    RULE = "rule"  # денилист или модель, подтверждённая структурой главной
    MODEL = "model"  # модель по выдаче, главная не спорила или не открылась
    ARBITER = "arbiter"  # выдача и главная спорили, решала модель по обеим


#: Намерение → совет. Площадка отсылает наружу или живёт с рекламы;
#: всё остальное — либо продаёт себя, либо не площадка вовсе.
ADVICE: dict[Intent, Recommendation] = {
    # Продажа размещения у себя — ровно то, что ищем, и она сильнее всего
    # остального, что сайт продаёт. Прогон №18: `airanklab.com` писал «paid
    # guest post publishing», а отрезан был за `/pricing` своего сервиса.
    Intent.SELLS_PLACEMENT: Recommendation.ACCEPT,
    # Посредник: статья уйдёт не к нему, а цену он перепродаёт с наценкой.
    Intent.LINK_VENDOR: Recommendation.REJECT,
    Intent.REFERS_OUT: Recommendation.ACCEPT,
    Intent.EDITORIAL_ADS: Recommendation.ACCEPT,
    Intent.SELLS_OWN: Recommendation.REJECT,
    # ⚠ «Посмотри», а не отказ. Госорган и общественный вещатель пишут
    # статьи и по тексту неотличимы от издания; различает их только знание
    # модели о том, кто это. Резать по одному знанию нельзя — разметки
    # такие сайты не несут (замер 23.09: ни у одного из пяти), подтвердить
    # нечем, и ошибка стоила бы годного донора.
    Intent.NON_COMMERCIAL: Recommendation.REVIEW,
    Intent.NONE: Recommendation.REJECT,
    Intent.UNKNOWN: Recommendation.REVIEW,
}

#: Версия обоих промптов. Ложится на домен рядом с моделью: точность против
#: человека считается по версии, иначе старые вердикты смешаются с новыми.
PROMPT_VERSION = "judge-v2-placement"

#: Продажа размещения — первым пунктом и с оговоркой «важнее остального».
#: v1 знал только «продаёт своё», и сайт, продающий гостевые статьи у себя,
#: попадал туда же: платная публикация — тоже продажа. Прогон №18 — из
#: 58 сайтов, найденных на странице для авторов, отрезано 31.
PLACEMENT_RULES = (
    "sells_placement (продаёт размещение У СЕБЯ: платные гостевые статьи, "
    "спонсорские публикации, ссылки в своих статьях, рекламные места, медиакит; "
    "это важнее всего остального — сайт может при этом продавать и своё), "
    "link_vendor (продаёт размещение на ЧУЖИХ сайтах: биржа или каталог "
    "площадок, агентство, которое покупает публикации и ссылки у изданий для "
    "клиентов; «купить ссылки на N сайтах»), "
)

PLACEMENT_NOTES = (
    "Где окажется оплаченная статья: на этом домене — sells_placement, на "
    "других сайтах — link_vendor.\n"
    "Приглашение писать для сайта («write for us», «пишите для нас», правила "
    "гостевых публикаций) само по себе не продажа: sells_placement — только "
    "если сказано о плате, спонсорстве или рекламе; иначе суди сайт по "
    "остальному тексту.\n"
)

SYSTEM = (
    "Ты определяешь, КАК сайт зарабатывает, по тексту из поисковой выдачи. "
    "Не оцениваешь качество и не угадываешь нишу.\n"
    'Верни строгий JSON: {"intent": ..., "quote": ..., "why": ...}\n'
    "intent — одно из: "
    + PLACEMENT_RULES
    + "sells_own (у него покупают, бронируют, вносят депозит, "
    "оформляют подписку на его собственную услугу; сюда же интернет-магазин "
    "и производитель, даже если страница — статья), refers_out (обозревает и "
    "сравнивает чужих, уводит к ним), editorial_ads (публикует статьи и новости, "
    "живёт с рекламы и подписок), non_commercial (по уставу не продаёт рекламу: "
    "госорган, общественный вещатель, потребительская или профессиональная "
    "организация, учебное заведение), none (форум, визитка человека, справочник "
    "без рекламы; блог со статьями — editorial_ads, даже личный и без видимой "
    "рекламы).\n"
    "quote — ДОСЛОВНЫЙ кусок присланного текста, 3-12 слов, на котором "
    "основан вывод. Не пересказывай и не переводи.\n"
    "why — одно короткое предложение.\n"
    + PLACEMENT_NOTES
    + "Похвала себе не делает обзорщиком: заголовок вида "
    "«Top-Rated <Brand> — Sign Up Today» это sells_own, а не refers_out: "
    "хвалят себя, а не сравнивают чужих.\n"
    "Обратный случай: текст зовёт зарегистрироваться или купить на ДРУГОМ "
    "сайте, у бренда, чьё имя не совпадает с доменом, — это refers_out. Но "
    "магазин, который продаёт чужие марки У СЕБЯ, — sells_own: важно, где "
    "совершается покупка, а не чья марка.\n"
    "Кто владелец домена, можно брать из собственного знания — так отличается "
    "non_commercial, по тексту похожий на издание. Цитата при этом всё равно "
    "дословно из присланного текста."
)

# ⚠ В промпте нет ни одного слова ниши, и это стережёт тест
# `test_prompt_has_no_niche_words`. Первая же подсказка вида
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
    decided_by: Decider = Decider.MODEL

    @property
    def would_cut(self) -> bool:
        """Отрезал бы в `enforce`. В наблюдении именно это и считается."""
        return self.recommendation is Recommendation.REJECT


def dns_labels(host: str) -> list[str]:
    """Метки домена без порта и хвоста пути."""
    clean = host.strip().lower().split("/")[0].split(":")[0]
    return [label for label in clean.split(".") if label]


#: Метки государственных, учебных и военных зон. Стоят ПРЕДПОСЛЕДНЕЙ меткой
#: перед страной (`gov.in`, `gob.es`, `ac.uk`, `go.jp`) или последней (`.gov`).
PUBLIC_ZONE_LABELS: frozenset[str] = frozenset(
    {"gov", "gob", "gouv", "govt", "gv", "go", "mil", "edu", "ac", "gc"}
)
PUBLIC_ZONE_TLDS: frozenset[str] = frozenset({"gov", "mil", "edu"})


def is_public_zone(host: str) -> bool:
    """Государственный, учебный или военный домен.

    Размещений такие сайты не продают, а коммерческая страница на них почти
    всегда паразитная — взломанный сайт, на котором чужой контент ловит
    трафик. 23.09 модель приняла `rajasthan.gov.in` со статьёй о букмекерах
    Южной Африки: по тексту это обзор, по зоне — взлом.

    ⚠ `go` считается только перед двухбуквенной страной: `go.jp` — зона,
    а `go.com` — обычный сайт.
    """
    labels = dns_labels(host)
    if len(labels) < 2:
        return False
    if labels[-1] in PUBLIC_ZONE_TLDS:
        return True
    return len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in PUBLIC_ZONE_LABELS


def is_platform(host: str) -> bool:
    """Платформа из денилиста.

    ⚠ Совпадение по ЦЕЛОЙ метке. Вхождение строки денило бы
    `google-maps-guide.co.ke` — сайт, к платформе отношения не имеющий.
    """
    return any(label in cfg.PLATFORM_LABELS for label in dns_labels(host))


def source_text(title: str | None, description: str | None) -> str:
    """Текст, который увидит модель. Пусто — судить не по чему."""
    parts = [part.strip() for part in (title, description) if part and part.strip()]
    return "\n".join(parts)[: cfg.MAX_TEXT_CHARS]


def build_payload(
    model: str, host: str, text: str, *, system: str = SYSTEM, label: str = "Текст выдачи"
) -> dict[str, Any]:
    """Тело запроса с поправкой на семейство модели."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Домен: {host}\n{label}:\n{text}"},
        ],
    }
    if is_reasoning(model):
        # Потолок включает рассуждение, а на `low` его больше, чем на
        # `minimal`: обрезанный ответ пришёл бы пустым и ушёл в «посмотри».
        payload["max_completion_tokens"] = 2000
        payload["reasoning_effort"] = cfg.REASONING_EFFORT
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
        logger.info("%s: ответ модели не разобрать: %r", TOPIC, content[:120])
        return Judgement(Intent.UNKNOWN, Recommendation.REVIEW, None, "ответ модели не разобрать")
    if not isinstance(body, dict):
        return Judgement(Intent.UNKNOWN, Recommendation.REVIEW, None, "ответ модели не объект")

    raw_intent = str(body.get("intent", "")).strip().lower()
    try:
        intent = Intent(raw_intent)
    except ValueError:
        logger.info("%s: намерение не из списка: %r", TOPIC, raw_intent[:40])
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
        return Judgement(intent, Recommendation.REVIEW, quote, "цитата не найдена в тексте выдачи")

    return Judgement(intent, ADVICE[intent], quote, why or "по тексту выдачи")


def before_model(host: str, text: str) -> Judgement | None:
    """Всё, что решается без модели и не стоит ни одного токена.

    Порядок — от окончательного к осторожному: платформа и госзона режутся
    правилом, пустой текст и страница-отказ идут человеку.
    """
    if is_platform(host):
        return Judgement(
            Intent.NONE, Recommendation.REJECT, None, "платформа из денилиста",
            decided_by=Decider.RULE,
        )  # fmt: skip
    if is_public_zone(host):
        return Judgement(
            Intent.NON_COMMERCIAL,
            Recommendation.REJECT,
            None,
            "государственная или учебная зона: размещений не продаёт, "
            "коммерческая страница на ней — чужой контент",
            decided_by=Decider.RULE,
        )
    if not text:
        return Judgement(
            Intent.UNKNOWN, Recommendation.REVIEW, None,
            "выдача не дала ни заголовка, ни описания", decided_by=Decider.RULE,
        )  # fmt: skip
    if looks_denied(text):
        # Судить отказ доступа нельзя даже когда получается: правильный
        # ответ здесь был бы угадан, а не прочитан.
        return Judgement(
            Intent.UNKNOWN,
            Recommendation.REVIEW,
            None,
            f"вместо страницы пришёл отказ доступа: {text[:60]!r}",
            decided_by=Decider.RULE,
        )
    return None


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
    text = source_text(title, description)
    ruled = before_model(host, text)
    if ruled is not None:
        return ruled

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


# --- арбитр: выдача и главная спорят --------------------------------------

ARBITER_SYSTEM = (
    "Тебе показывают сайт с двух сторон: страницу из поисковой выдачи и его "
    "главную. Страница похожа на статью; реши, как зарабатывает САЙТ ЦЕЛИКОМ, "
    "а не эта страница.\n"
    'Верни строгий JSON: {"intent": ..., "quote": ..., "why": ...}\n'
    "intent — одно из: " + PLACEMENT_RULES + "sells_own (сайт продаёт СВОЁ — товар, услугу, свой "
    "программный сервис, страховку, приём у врача, работу агентства; статьи "
    "служат этим продажам), editorial_ads (издание: главное у него — статьи, "
    "а подписка, мерч или свои тесты — сбоку), refers_out (обзорщик и "
    "сравнитель, уводит к чужим), non_commercial (по уставу не продаёт рекламу: "
    "госорган, общественный вещатель, потребительская организация).\n"
    "Главное свидетельство — меню главной: «Продукт», «Решения», «Цены», "
    "«Услуги», «Записаться», каталог и корзина говорят о продаже своего; "
    "рубрики, новости, обзоры — об издании; «Advertise», «Реклама», "
    "«Sponsored» — о продаже размещения у себя. Кто владелец домена, можно "
    "брать из собственного знания.\n"
    + PLACEMENT_NOTES
    + "quote — ДОСЛОВНЫЙ кусок присланного текста, 3-12 слов, из любой из двух "
    "частей. why — одно короткое предложение."
)


def arbiter_text(serp: str, home: HomeSignals) -> str:
    """Обе стороны одним текстом: по нему же проверяется цитата."""
    return f"Страница из выдачи:\n{serp}\n\nГлавная:\n{home.as_text()}"


async def arbitrate(
    http: httpx.AsyncClient,
    *,
    host: str,
    serp: str,
    home: HomeSignals,
    model: str | None = None,
    api_key: str | None = None,
) -> Judgement:
    """Вердикт по спорному домену. Не бросает: сбой — «посмотри».

    Отдельный вызов, а не второй круг того же промпта: вопрос другой. Судья
    спрашивает, что это за страница; арбитр — чем живёт сайт, у которого
    страница и витрина говорят разное.
    """
    text = arbiter_text(serp, home)
    chosen = model or llm_cfg.JUDGE_MODEL
    answer = await post_chat(
        http,
        api_key=api_key if api_key is not None else llm_cfg.API_KEY,
        payload=build_payload(chosen, host, text, system=ARBITER_SYSTEM, label="Текст"),
        topic=TOPIC,
    )
    if isinstance(answer, Refusal):
        logger.warning("%s, арбитр: %s (%s)", TOPIC, answer, host)
        return Judgement(
            Intent.UNKNOWN, Recommendation.REVIEW, None, str(answer), decided_by=Decider.ARBITER
        )
    content = content_of(answer, topic=TOPIC)
    verdict = (
        parse(content, text)
        if content
        else Judgement(Intent.UNKNOWN, Recommendation.REVIEW, None, "пустой ответ модели")
    )
    return Judgement(
        verdict.intent,
        verdict.recommendation,
        verdict.quote,
        verdict.reason,
        chosen,
        tokens_of(answer),
        Decider.ARBITER,
    )
