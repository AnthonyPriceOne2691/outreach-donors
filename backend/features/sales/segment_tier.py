"""Тариф судьи сегмента у прогона сбора: обычный или экономный — OpenAI Flex.

**Экономный режим сбора** (решение владельца 16.11.2026): дешёвая выдача
(«стандарт», `serp/modes.py`) и судья на тарифе Flex (`service_tier: "flex"`) — та же модель вдвое
дешевле, ответ медленнее, иногда отказ 429 «нет мощности». Не Batch API: путь судьи тот же — окно,
кэш, второй шанс, потолки и расход (`segment_window`), общего кода нет.

**Признак — в смете прогона** (`TIER_KEY`), как режим выдачи (`segment_jobs.SERP_MODE_KEY`): без
миграции, и задача прогона берёт тариф, который человек видел на запуске, а не настройку сейчас.
Ключа нет — обычный тариф: прогоны до экономного режима и ручной «Сбор базы» идут как шли, и тело
запроса у них прежнее до байта. Режим выдачи «стандарт» тариф судьи не включает: на ручном сборе его
выбирают ради цены выдачи, а Flex медленнее и отказывает — молча включать его нельзя. Незнакомое
значение — постоянный отказ словами (`UnknownTierError`): цены тарифов разные вдвое.

**Деньги.** В смете прогона — цены прайса модели (обычный тариф); долю тарифа (`FLEX_PRICE_SHARE`)
применяет тот, кто считает деньги судьи: смета — деньгам токенов (`segment_estimate.priced`), отчёт
прогона — факту (`segment_spend`); пересуд того же прогона идёт обычным тарифом и считается ценами
прайса. Токены от тарифа не зависят: журнал расхода и дневные потолки
модели (`core/usage`) считают токены, и экономный прогон их не завышает. Ответ, обслуженный другим
тарифом (`service_tier` ответа — не `flex`), оплачен по нему — это предупреждение в журнал
(`served_otherwise`): деньги судьи в отчёте тогда ниже счёта.

**Ожидание и отказы Flex** (документация OpenAI «Flex processing», 16.11.2026):

- ответ медленнее — срок 15 минут, как в примерах OpenAI (`FLEX_TIMEOUT_S`): срок попытки у
  HTTP-клиента (`judge_http`) и срок вызова целиком, с повторами (`FlexPass.post`), — один сайт не
  держит место в окне судьи дольше;
- 429 «Resource Unavailable» — мощности нет, вызов не оплачивается; совет OpenAI — повтор с
  нарастающей паузой. Общий `post_chat` повторяет 429 трижды за секунды; сверх того — `FLEX_RETRIES`
  повторов с паузой `FLEX_PAUSE_S` × 2ⁿ и разбросом. Признак этого отказа в теле OpenAI не публикует,
  поэтому повторяется любой 429, кроме «нет кредитов»: и частота, и мощность проходят сами, а слова —
  «занят», без догадки о причине (`okf/provider-refusals.md`: неизвестный признак не подводится под
  знакомый). Не прошло — «модель не ответила» словами: сайт ждёт следующего прохода, как при любом
  отказе модели. Повтор обычным тарифом (второй совет OpenAI) — нет: он вдвое дороже, а экономный
  режим выбран ради цены;
- «нет кредитов» (429 с признаком в теле, `shared/llm.no_credits`) — не мощность: без повторов,
  проход встаёт, как у обычного тарифа.

**Проход перестаёт ждать** (`FlexPass`): сайт не дождался ответа ни повторами, ни сроком — остальные
сайты прохода модель не спрашивают, их отказ — сразу словами (`HELD`). Иначе без мощности каждый
сайт ждал бы по две-три минуты, а медленный Flex — по четверть часа, и проход держал бы часами
воркер продаж, общий с пачками писем. Ответ сайта, начатого раньше, снимает отказ; следующий проход
(«Продолжить», повтор задачи) — с новым клиентом, и тариф спрашивается снова.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, TypeGuard

import httpx

from backend.config import llm as llm_cfg
from backend.shared.llm import Refusal, RefusalKind, post_chat

logger = logging.getLogger(__name__)

#: Пауза между повторами — своим именем: тест гасит её здесь, а не `asyncio.sleep` у всего процесса
#: (как `shared/net/retry._sleep`).
_sleep = asyncio.sleep


class JudgeTier(StrEnum):
    """Тариф судьи прогона. Строкой — так он лежит в смете прогона."""

    STANDARD = "standard"
    FLEX = "flex"


#: Тариф судьи в смете прогона (`JudgeTier` строкой); ключа нет — обычный.
TIER_KEY = "judge_tier"
#: Значение `service_tier` в теле запроса экономного тарифа.
FLEX_SERVICE_TIER = "flex"
#: Доля прайса у Flex — цены Batch API: у gpt-5-mini ввод $0,125 против $0,25, вывод $1,00 против
#: $2,00 за миллион токенов (прайс OpenAI, 16.11.2026).
FLEX_PRICE_SHARE = 0.5
#: Срок ответа Flex, с: 15 минут — как в примерах OpenAI (умолчание их клиента — 10 минут).
FLEX_TIMEOUT_S = 900.0
#: Повторов 429 сверх попыток `post_chat` и первая пауза, с: 15, 30 и 60 с с разбросом до половины —
#: около двух-трёх минут на сайт, пока мощность не появится.
FLEX_RETRIES = 3
FLEX_PAUSE_S = 15.0
#: Слова отказов: у сайта — «модель не ответила: …», в итоге прохода — причина неответивших.
BUSY = "экономный тариф занят и после {retries} повторов с паузой — {detail}"
LATE = "экономный тариф не ответил за {minutes} мин — сайт ждёт следующего прохода"
HELD = (
    "экономный тариф не ответил другому сайту прохода и после повторов — этот сайт модель не "
    "спрашивала, он ждёт следующего прохода"
)
UNKNOWN = "тариф судьи «{raw}» неизвестен (известные: {known}) — прогон не судит вслепую"


class UnknownTierError(ValueError):
    """Тариф судьи в смете незнакомый: молча взять другой нельзя — цены разные вдвое. Повтор
    задачи не поможет (`runs/failures.is_permanent`)."""

    permanent = True


#: Тарифы строками — как их пишет смета.
KNOWN = tuple(tier.value for tier in JudgeTier)


def _read(estimate: Mapping[str, Any] | None) -> JudgeTier | None:
    """Тариф из сметы: ключа нет или пусто — обычный; незнакомое значение — `None`."""
    raw = (estimate or {}).get(TIER_KEY)
    if raw is None or raw == "":
        return JudgeTier.STANDARD
    return JudgeTier(raw) if isinstance(raw, str) and raw in KNOWN else None


def tier_of(estimate: Mapping[str, Any] | None) -> JudgeTier:
    """Тариф судьи из сметы прогона: ключа нет или пусто — обычный; незнакомое — отказ словами."""
    found = _read(estimate)
    if found is None:
        raw = (estimate or {}).get(TIER_KEY)
        raise UnknownTierError(UNKNOWN.format(raw=raw, known=", ".join(KNOWN)))
    return found


def known_tier(estimate: Mapping[str, Any] | None) -> JudgeTier:
    """Тариф для денег отчёта: незнакомый — обычный. Отчёт не падает, а неизвестная скидка расход
    не занижает; судить такой прогон задача не стала (`tier_of`)."""
    return _read(estimate) or JudgeTier.STANDARD


def asked_tier(raw: str | None) -> tuple[JudgeTier, str | None]:
    """Тариф судьи из запроса сметы — и отказ словами: пусто — обычный; незнакомое — обычный и
    отказ, как у незнакомого режима выдачи: молча взять другой нельзя, цены разные вдвое."""
    found = _read({TIER_KEY: raw})
    if found is not None:
        return found, None
    return JudgeTier.STANDARD, UNKNOWN.format(raw=raw, known=", ".join(KNOWN))


def tier_entry(tier: str) -> dict[str, str]:
    """Тариф в смете прогона и в её отпечатке (`segment_estimate_keys.seen_digest`): Flex — ключом
    `TIER_KEY`; обычный — ничего: смета и отпечаток обычного прогона прежние до байта, а отсутствие
    ключа читается как обычный тариф (`tier_of`)."""
    return {TIER_KEY: JudgeTier.FLEX.value} if tier == JudgeTier.FLEX else {}


def price_share(tier: JudgeTier) -> float:
    """Доля прайса модели, которую платит тариф."""
    return FLEX_PRICE_SHARE if tier is JudgeTier.FLEX else 1.0


def tier_fields(tier: JudgeTier) -> dict[str, str]:
    """Поля тела запроса тарифа: Flex — `service_tier`; обычный — никаких, тело прежнее до байта."""
    return {"service_tier": FLEX_SERVICE_TIER} if tier is JudgeTier.FLEX else {}


def judge_http(tier: JudgeTier) -> httpx.AsyncClient:
    """HTTP-клиент модели судьи со сроком тарифа: обычный — `LLM_TIMEOUT_S`, как у прогона доноров;
    Flex — `FLEX_TIMEOUT_S`: с минутами обычного срока медленный ответ обрывался бы, а оборванный
    запрос провайдер может довести и выставить счёт мимо журнала."""
    return httpx.AsyncClient(
        timeout=FLEX_TIMEOUT_S if tier is JudgeTier.FLEX else llm_cfg.TIMEOUT_S
    )


def busy(answer: dict[str, Any] | Refusal) -> TypeGuard[Refusal]:
    """Отказ 429, который проходит сам: мощность Flex или частота. «Нет кредитов» — не он."""
    return isinstance(answer, Refusal) and answer.status == 429 and not answer.no_credits


def pause_for(attempt: int) -> float:
    """Пауза перед повтором номер `attempt` (с нуля): `FLEX_PAUSE_S` × 2ⁿ и разброс до половины —
    сайты окна, начавшие ждать разом, не вернутся в одну секунду."""
    base = FLEX_PAUSE_S * 2.0**attempt
    return base + random.uniform(0, base / 2)  # noqa: S311 — разброс, а не криптография


class FlexPass:
    """Экономный тариф у одного прохода судьи — клиент судьи живёт проход. Сайт не дождался ответа
    ни повторами 429, ни сроком (`gave_up`) — дальше отказ сразу словами `HELD`, без сети; ответ
    сайта, начатого раньше, снимает его (шапка модуля)."""

    def __init__(self) -> None:
        self.gave_up = False

    async def post(
        self,
        http: httpx.AsyncClient,
        *,
        api_key: str,
        payload: dict[str, Any],
        topic: str,
        host: str = "",
    ) -> dict[str, Any] | Refusal:
        """Запрос экономного тарифа: `post_chat` и повторы 429 с паузой (`_served`); весь вызов —
        не дольше `FLEX_TIMEOUT_S`. Не бросает: отказ — `Refusal` словами."""
        if self.gave_up:
            return Refusal(RefusalKind.REFUSED, HELD, permanent=False)
        try:
            async with asyncio.timeout(FLEX_TIMEOUT_S):
                answer = await _served(
                    http, api_key=api_key, payload=payload, topic=topic, host=host
                )
        except TimeoutError:
            logger.warning(
                "%s: экономный тариф не ответил в срок — проход его больше не ждёт",
                topic,
                extra={"host": host, "seconds": FLEX_TIMEOUT_S},
            )
            self.gave_up = True
            minutes = round(FLEX_TIMEOUT_S / 60)
            return Refusal(RefusalKind.NETWORK, LATE.format(minutes=minutes), permanent=False)
        if busy(answer):  # `_served` отдаёт «занят» только после всех повторов
            logger.warning("%s: проход больше не ждёт экономный тариф", topic, extra={"host": host})
            self.gave_up = True
        elif not isinstance(answer, Refusal):
            self.gave_up = False
            served_otherwise(answer, topic=topic, host=host)
        return answer


def served_otherwise(body: dict[str, Any], *, topic: str, host: str = "") -> bool:
    """Провайдер назвал в ответе другой тариф (`service_tier`, не `flex`): вызов оплачен по нему, а
    отчёт прогона считает деньги судьи долей Flex — расхождение в журнал предупреждением, а не молча.
    Тариф не назван — сверять не с чем."""
    served = body.get("service_tier")
    if served is None or served == FLEX_SERVICE_TIER:
        return False
    logger.warning(
        "%s: просили экономный тариф, ответ обслужен другим — деньги судьи в отчёте ниже счёта",
        topic,
        extra={"host": host, "served": str(served)},
    )
    return True


async def _served(
    http: httpx.AsyncClient, *, api_key: str, payload: dict[str, Any], topic: str, host: str
) -> dict[str, Any] | Refusal:
    """Ответ модели — или отказ: «занят» после `FLEX_RETRIES` повторов словами `BUSY`, прочие —
    как их отдал `post_chat`."""
    answer = await post_chat(http, api_key=api_key, payload=payload, topic=topic)
    for attempt in range(FLEX_RETRIES):
        if not busy(answer):
            return answer
        pause = pause_for(attempt)
        logger.warning(
            "%s: экономный тариф занят, повтор через %.0f с (%s из %s)",
            topic,
            pause,
            attempt + 1,
            FLEX_RETRIES,
            extra={"host": host, "status": answer.status},
        )
        await _sleep(pause)
        answer = await post_chat(http, api_key=api_key, payload=payload, topic=topic)
    if not busy(answer):
        return answer
    words = BUSY.format(retries=FLEX_RETRIES, detail=answer.detail)
    return Refusal(answer.kind, words, permanent=False, status=answer.status, marks=answer.marks)
