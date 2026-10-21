"""Один запрос к модели и разбор её отказов.

Здесь нет ни одного решения про то, что просить, — только про то, как
пережить ответ. Вынесено из генерации ключей, когда та же работа
понадобилась уникализации письма: два экземпляра разбора отказов
разъезжаются на первой же правке, и тише всех расходится тот, который
реже вызывают.

**Отказ не рушит вызывающего.** Возвращается `Refusal`, а решение
о повторе принимает вызывающий: у пула ключей и у одного письма оно разное.

**Причина едет вместе со значением, а не только в лог.** Раньше здесь
возвращался голый `None`, и по нему нельзя было отличить протухший ключ
от честно пустого ответа. Генерация ключей на этом и погорела: `None`
превращался в `[]`, добор крутил углы вхолостую и записывал в отчёт
«модель исчерпала уникальные фразы» — то есть называла неработающий ключ
исчерпанной фантазией. Лог при этом был полон, но читают отчёт.

**Нет кредитов — свой вид отказа, без повторов** (слово владельца 20.10.2026). У OpenAI 429 —
и частота, и пустой счёт; различает их только тело (`no_credits`). Пустой счёт повторялся как
частота, а прогон с ним стартовал, покупал выдачу и уводил каждый сайт «на проверку: сбой».
Теперь прогон до первой траты спрашивает модель (`probe`, `unready`): любой отказ — прогон не
начинается, причина — словами оператору (`words_of`). Остаток счёта обычным ключом не узнать.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx

from backend.config import llm as llm_cfg
from backend.shared.net.retry import reason_of, temporary, unsent, with_retries

logger = logging.getLogger(__name__)

#: Сколько раз пробуем один вызов модели. Перегрузка у провайдера
#: проходит сама, а вызов стоит денег только когда он удался.
ATTEMPTS = 3

API_URL = "https://api.openai.com/v1/chat/completions"

#: Семейства, которые считают токены рассуждений отдельно и не принимают
#: температуру. Перепутать нельзя: провайдер отвечает отказом, а не догадкой.
REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def is_reasoning(model: str) -> bool:
    return model.startswith(REASONING_PREFIXES)


#: Статусы, на которых повтор бессмысленен: ключ, права или сама форма
#: запроса. Разделение то же, что у клиента Ahrefs, и по той же причине —
#: «повторить позже» на протухшем ключе это вечный цикл с бодрым текстом.
PERMANENT_STATUSES = frozenset({400, 401, 403, 404, 422})
#: Ключ не принят: неверный, отозван или без прав на модель.
KEY_STATUSES = frozenset({401, 403})
#: «Нет кредитов» в теле отказа (`error.type`, `error.code`). Ответ OpenAI 20.10.2026: 429,
#: `type: insufficient_quota`, `code: credit_balance_exhausted`; прежде — `insufficient_quota`
#: в обоих.
NO_CREDITS_MARKS = frozenset({"insufficient_quota", "credit_balance_exhausted"})
#: 403 «страна запроса не обслуживается» (`error.code` или `error.type`): ключ годен, а модель отсюда
#: недоступна — это не «ключ не принят», а «модель не ответила» с причиной (`words_of`).
REGION_MARK = "unsupported_country_region_territory"

#: Слова оператору (слово владельца 20.10.2026): нет кредитов — ими же и `str()` такого отказа на
#: любом экране (заметка разбора, черновик агента, «Написать заново»); подробности провайдера — в
#: журнале (`post_chat`). Два других — почему прогон не начат (`words_of`).
NO_CREDITS_WORDS = "у модели нет кредитов — пополните счёт OpenAI"
KEY_WORDS = "ключ модели не принят — проверьте ключ"
SILENT_WORDS = "модель не ответила ({why}) — проверить счёт нельзя, прогон не начат"


class RefusalKind(StrEnum):
    NETWORK = "сеть"  # до провайдера не дошли
    REFUSED = "отказ"  # провайдер ответил и отказал
    FORMAT = "формат"  # ответил, но разобрать нечего
    LOCAL = "запрос"  # запрос не собран у нас: ключ или заголовок
    NO_CREDITS = "нет кредитов"  # на счету ключа нет денег: пополнить может только человек


@dataclass(frozen=True, slots=True)
class Refusal:
    """Почему вызов не удался. Отдаётся вызывающему наравне с ответом.

    `permanent` — это не «серьёзность», а указание, что делать: повтор
    не поможет, чинить надо ключ, права или запрос. `status` — код ответа
    провайдера, если он ответил, `marks` — признаки из тела отказа (`error.type`,
    `error.code`): по ним «ключ не принят» (`words_of`).
    """

    kind: RefusalKind
    detail: str
    permanent: bool
    status: int | None = None
    marks: frozenset[str] = frozenset()

    @property
    def no_credits(self) -> bool:
        """Нет кредитов: проход встаёт, а не зовёт модель вхолостую (слово владельца 20.10.2026)."""
        return self.kind is RefusalKind.NO_CREDITS

    def __str__(self) -> str:
        if self.no_credits:
            return NO_CREDITS_WORDS  # что делать — словами; ответ провайдера — в журнале
        what = "чинить" if self.permanent else "можно повторить"
        return f"модель, {self.kind} ({what}): {self.detail}"


class ModelClient:
    """Каркас клиента одного вида вызовов модели: модель, ключ и HTTP-клиент.

    Общий у уникализации письма, разбора ответа и агента переписки: три
    копии одного конструктора разъехались бы на первой правке — откуда ключ,
    чей HTTP-клиент закрывать. Свой клиент закрывается, данный снаружи — нет:
    его закрывает тот, кто дал.
    """

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        model: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._model = model or self._default_model()
        self._api_key = api_key if api_key is not None else llm_cfg.API_KEY
        self._own_client = client is None
        self._http = client or httpx.AsyncClient(timeout=llm_cfg.TIMEOUT_S)

    def _default_model(self) -> str:
        """Модель вида вызовов, если её не назвали явно (`config/llm.py`)."""
        return llm_cfg.LETTERS_MODEL

    @property
    def model(self) -> str:
        return self._model

    async def aclose(self) -> None:
        if self._own_client:
            await self._http.aclose()


def _error_of(response: httpx.Response) -> dict[str, Any]:
    """Объект `error` тела отказа; тело не JSON или объекта нет — пусто."""
    try:
        body = response.json()
    except ValueError:
        # Не JSON — законный случай для страницы прокси или шлюза.
        # Тело всё равно уедет в ноту целиком, но молчать о разборе нельзя.
        logger.debug("отказ провайдера пришёл не в JSON, берём тело как есть")
        return {}
    error = body.get("error") if isinstance(body, dict) else None
    return error if isinstance(error, dict) else {}


def _message_of(response: httpx.Response) -> str:
    """Внятная фраза провайдера, если она есть, иначе сырое тело."""
    message = _error_of(response).get("message")
    return str(message)[:300] if message else response.text[:300]


def _marks_of(response: httpx.Response) -> frozenset[str]:
    """Признаки отказа из тела (`error.type`, `error.code`); тела или их нет — пусто."""
    error = _error_of(response)
    return frozenset(str(error[field]) for field in ("type", "code") if error.get(field))


def no_credits(response: httpx.Response) -> bool:
    """На счету ключа нет денег: 402 — у любого провайдера; 429 — только с признаком в теле
    (`NO_CREDITS_MARKS`): частота проходит сама, пустой счёт — нет."""
    if response.status_code == 402:
        return True
    return response.status_code == 429 and bool(_marks_of(response) & NO_CREDITS_MARKS)


def _passes(response: httpx.Response) -> bool:
    """Повторять ли ответ модели: временный по коду (`net.retry.temporary`), но не «нет
    кредитов» — повтор пустого счёта втрое дольше ведёт к тому же отказу."""
    return temporary(response) and not no_credits(response)


def _unusable_key(api_key: str, topic: str) -> Refusal | None:
    """Ключ, с которым запрос не собрать. `None` — ключ годится для заголовка."""
    if not api_key.strip():
        # Пустой ключ — не сбой связи, и повтор его не починит. Без этой
        # проверки httpx не собирает заголовок `Bearer ` и роняет
        # LocalProtocolError — наследника HTTPError, то есть «сеть, можно
        # повторить». 24.09.2026 судья на сервере без ключа так записал
        # 44 домена прогона №21: отказ звал повторять, а чинить надо было
        # настройку.
        detail = "LLM_API_KEY не задан — впишите ключ модели в .env"
    elif not api_key.isascii():
        # Ключ едет в HTTP-заголовке, а заголовки только ASCII. Без этой
        # проверки httpx роняет UnicodeEncodeError мимо всей обработки
        # отказов: вызывающий получает трассировку, которая не называет
        # ни ключ, ни что делать. Тот же класс, что у секрета приёма —
        # он тоже едет заголовком и тоже обязан быть латиницей.
        detail = "ключ модели содержит не-ASCII символы — заголовок с ним не собрать"
    else:
        return None
    logger.error("%s: %s", topic, detail)
    return Refusal(RefusalKind.LOCAL, detail, permanent=True)


def _unreached(exc: httpx.HTTPError) -> Refusal:
    """До провайдера не дошли. Обрыв связи повторяем, несобранный запрос — нет:
    он не собран у нас, и повтор соберёт его так же (пробел или перевод
    строки в ключе, битый заголовок). Текст отказа — без заголовков: в них
    ключ модели (`net.retry.reason_of`)."""
    if unsent(exc):
        return Refusal(RefusalKind.LOCAL, reason_of(exc), permanent=True)
    return Refusal(RefusalKind.NETWORK, reason_of(exc), permanent=False)


async def post_chat(
    http: httpx.AsyncClient,
    *,
    api_key: str,
    payload: dict[str, Any],
    topic: str,
) -> dict[str, Any] | Refusal:
    """Запрос к модели. `Refusal` — не получилось, и он говорит почему."""
    # Повторы общие на все внешние сервисы: до них один обрыв связи
    # означал письмо, оставшееся шаблонным, или ответ, оставшийся
    # неразобранным, — и оба случая выглядели как «модель отказала».
    refusal = _unusable_key(api_key, topic)
    if refusal is not None:
        return refusal

    try:
        response = await with_retries(
            lambda: http.post(
                API_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            ),
            attempts=ATTEMPTS,
            topic=topic,
            retry_when=_passes,
        )
    except httpx.HTTPError as exc:
        refusal = _unreached(exc)
        if unsent(exc):
            logger.error("%s: %s", topic, refusal)  # noqa: TRY400 — в трассировке был бы ключ
        else:
            logger.exception("%s: %s", topic, refusal)
        return refusal

    if response.status_code >= 400:
        # Отказ называется целиком: «плохой запрос» без текста провайдера
        # отлаживается вслепую. Но сырое тело — это JSON, и в ноте оператору
        # оно обрывается на первой скобке; провайдер кладёт внятную фразу
        # в `error.message`, и она прямо говорит, что чинить.
        detail = f"HTTP {response.status_code}: {_message_of(response)}"
        logger.error("%s: модель отказала (%s)", topic, detail)
        broke = no_credits(response)
        return Refusal(
            RefusalKind.NO_CREDITS if broke else RefusalKind.REFUSED,
            detail,
            permanent=broke or response.status_code in PERMANENT_STATUSES,
            status=response.status_code,
            marks=_marks_of(response),
        )

    try:
        body = response.json()
    except ValueError:
        logger.exception("%s: ответ модели не разобран как JSON", topic)
        return Refusal(RefusalKind.FORMAT, "ответ не разобран как JSON", permanent=False)

    if not isinstance(body, dict):
        logger.error("%s: ждали объект, пришло %s", topic, type(body).__name__)
        return Refusal(
            RefusalKind.FORMAT, f"ждали объект, пришло {type(body).__name__}", permanent=False
        )
    return body


def content_of(body: dict[str, Any], *, topic: str) -> str:
    """Текст ответа из первой альтернативы. Пусто — значит, разбирать нечего."""
    choices = body.get("choices") or []
    if not choices:
        logger.error("%s: модель вернула ответ без вариантов", topic)
        return ""
    return str(((choices[0] or {}).get("message") or {}).get("content") or "")


#: Ввод и вывод — в ответе без итога: у OpenAI `prompt`/`completion`, у прочих `input`/`output`.
TOKEN_PAIRS = (("prompt_tokens", "completion_tokens"), ("input_tokens", "output_tokens"))


def tokens_of(body: dict[str, Any]) -> int:
    """Сколько токенов стоил вызов. Единственный честный измеритель расхода.

    **Без итога — суммой, а не нулём** (19.10.2026). Ответ без `usage.total_tokens` выглядел
    бесплатным и шёл мимо потолка расхода на модель (`core/usage`). Итога нет — сумма ввода
    и вывода; нет и её — ноль, но строкой в журнал: неизмеренный вызов виден. Значение не
    числом (строка, `True`, отрицательное) — как не названное: `int()` от него ронял бы
    удачный ответ модели.
    """
    usage = body.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    total = _count(usage.get("total_tokens"))
    if total:
        return total
    for pair in TOKEN_PAIRS:
        counted = sum(_count(usage.get(name)) for name in pair)
        if counted:
            return counted
    logger.warning(
        "модель: в ответе нет расхода токенов — вызов посчитан бесплатным",
        extra={"usage_fields": sorted(usage)},
    )
    return 0


def _count(value: object) -> int:
    """Токены из поля `usage`: целое не меньше нуля, а прочее — ноль, как не названное."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


# --- проверка модели до прогона -------------------------------------------------------------------

#: Потолок вывода проверки. У модели с рассуждением он вмещает и рассуждение, и 64 не хватало:
#: gpt-5-mini с усилием `low` не успевала ответить, и OpenAI отвечал не обрезанным 200, а отказом
#: 400 «Could not finish the message because max_tokens or model output limit was reached» — смета
#: закрывала запуск при живых ключе и счёте (прод 21.10.2026). С запасом: модель останавливается,
#: ответив «ok», а не по потолку, — платится то, что она потратила.
PROBE_TOKENS_REASONING = 1024
PROBE_TOKENS_PLAIN = 8
#: Отказ «модель упёрлась в наш потолок вывода»: она отвечала — ключ принят, счёт жив (`probe`).
#: Только эти слова: 400 «Unsupported parameter: 'max_tokens'» — запрос, который модель не приняла,
#: и судья с тем же запросом не ответил бы тоже.
OUTPUT_LIMIT_MARK = "output limit was reached"
#: Усилие рассуждения проверки — нижнее из тех, что принимает любая модель с рассуждением: `minimal`
#: знает только пятое семейство, и отказ 400 у другой закрыл бы запуск исправной модели.
PROBE_EFFORT = "low"
#: Срок попытки: проверку ждёт запрос экрана, а не задача (`LLM_TIMEOUT_S` — минуты).
PROBE_TIMEOUT_S = 20.0
PROBE_TOPIC = "проверка модели до прогона"


class ModelUnreadyError(RuntimeError):
    """Модель прогона не ответила проверке (`ensure_model`): словами `words_of`. Повтор задачи
    не поможет — пополнить счёт или поправить ключ может только человек
    (`runs/failures.is_permanent`)."""

    permanent = True


def probe_payload(model: str) -> dict[str, Any]:
    """Тело проверки: слово в ответ, потолок вывода — по семейству модели."""
    limits: dict[str, Any] = (
        {"max_completion_tokens": PROBE_TOKENS_REASONING, "reasoning_effort": PROBE_EFFORT}
        if is_reasoning(model)
        else {"max_tokens": PROBE_TOKENS_PLAIN}
    )
    return {"model": model, "messages": [{"role": "user", "content": "Ответь: ok"}], **limits}


async def probe(http: httpx.AsyncClient, *, api_key: str, model: str) -> Refusal | None:
    """Жива ли модель: крошечный вызов той же модели тем же ключом — через `post_chat`, с его
    повторами (один обрыв связи запуск не закрывает) и разбором отказов. `None` — модель ответила:
    любой ответ 200, пустой и обрезанный потолком тоже, и отказ «упёрлась в потолок вывода»
    (`OUTPUT_LIMIT_MARK`); `Refusal` — что не так."""
    answer = await post_chat(http, api_key=api_key, payload=probe_payload(model), topic=PROBE_TOPIC)
    if isinstance(answer, Refusal):
        if not _out_of_room(answer):
            return answer
        logger.info("%s: %s отвечала до потолка вывода — ключ и счёт живы", PROBE_TOPIC, model)
        return None
    logger.info("%s: %s ответила", PROBE_TOPIC, model, extra={"tokens": tokens_of(answer)})
    return None


def _out_of_room(refusal: Refusal) -> bool:
    """Модель отвечала, пока не кончился потолок вывода запроса (`OUTPUT_LIMIT_MARK`)."""
    return refusal.status == 400 and OUTPUT_LIMIT_MARK in refusal.detail.lower()


def words_of(refusal: Refusal) -> str:
    """Почему прогон не начат — словами оператору: нет кредитов, ключ не принят (`KEY_STATUSES`,
    ключ не собрать в заголовок) или модель не ответила — с причиной. 403 из-за страны запроса
    (`REGION_MARK`) — не ключ: ключ годен, модель отсюда недоступна."""
    if refusal.kind is RefusalKind.NO_CREDITS:
        return NO_CREDITS_WORDS
    key_refused = refusal.status in KEY_STATUSES and REGION_MARK not in refusal.marks
    if refusal.kind is RefusalKind.LOCAL or key_refused:
        return KEY_WORDS
    return SILENT_WORDS.format(why=refusal.detail[:200])


def probe_client() -> httpx.AsyncClient:
    """Клиент проверки — свой, со сроком `PROBE_TIMEOUT_S`; тест подставляет транспорт здесь."""
    return httpx.AsyncClient(timeout=PROBE_TIMEOUT_S)


async def unready(*models: str) -> str | None:
    """Почему прогону, который позовёт эти модели, не начинать — словами (`words_of`); `None` —
    каждая ответила проверке (`probe`) или моделей нет. Одна модель дважды не спрашивается."""
    if not models:
        return None
    async with probe_client() as http:
        for model in dict.fromkeys(models):
            refusal = await probe(http, api_key=llm_cfg.API_KEY, model=model)
            if refusal is not None:
                return words_of(refusal)
    return None


async def ensure_model(*models: str) -> None:
    """Перед первой тратой задачи прогона: модель не ответила — `ModelUnreadyError` словами."""
    words = await unready(*models)
    if words is not None:
        raise ModelUnreadyError(words)
