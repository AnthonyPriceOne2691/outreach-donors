"""Уникализация письма: переписать три зоны под конкретного донора.

Что переписывается, задано ТЗ и разобрано в `template.py`. Здесь — как
именно.

**В модель уходят только переписываемые зоны.** Оффер, условия, подпись
и юридический блок она не видит вовсе, поэтому изменить их не может.
Это не осторожность, а единственный способ: просьбу «не трогай» модель
исполняет почти всегда, а «почти» означает письмо с изменёнными
условиями сделки, ушедшее адресату.

**Отказ модели — не отказ письма.** Зона остаётся шаблонной, письмо
собирается, процент отличия выходит низким, и это видно на экране рядом
с письмом. Молча подставить шаблон и показать «уникализировано» было бы
ровно тем классом ошибки, где поломка выглядит как работа.

**О чём сайт, мы не знаем.** В базе есть домен, страна и ключи прогона —
ниша, а не контент. Поэтому модели прямо запрещено ссылаться на
конкретную статью: выдуманная ссылка на несуществующий материал — это
не персонализация, а повод не отвечать. Ограничение записано
в `docs/WEB_LAYER.md` как долг: заголовок главной ступень контактов
и так качает, сохранить его дешевле, чем кажется.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from backend.config import llm as cfg
from backend.features.letters import masking
from backend.features.letters.compose import Rendered
from backend.features.letters.guards import metrics_leak
from backend.shared.llm import Refusal, content_of, is_reasoning, post_chat, tokens_of

logger = logging.getLogger(__name__)

#: Тема для логов.
TOPIC = "письма"

#: Запас токенов на зону. Рассуждающая модель тратит их и на размышление.
TOKENS_PER_ZONE_REASONING = 400
TOKENS_PER_ZONE_PLAIN = 200

SYSTEM = """You rewrite parts of a business outreach email so that no two \
emails read the same.

You receive a JSON object: each key is a zone name, each value is that zone's \
current text. Reply with a JSON object using exactly the same keys and nothing \
else — no prose, no markdown, no extra keys.

Rules:
- Keep the meaning, intent and reading level of every zone. You are rephrasing, \
not writing a new email.
- Keep the same language as the input and the same approximate length.
- Change roughly half of the wording. Fewer changes make the emails look \
identical to spam filters; more changes lose the point of the sentence.
- Never claim to have read a specific article, author or section of the site. \
You have not seen the site. General interest in what it covers is fine.
- Never mention SEO metrics of any kind: domain rating, DR, organic traffic, \
referring domains, keyword counts, or the name of any SEO tool.
- Never add links, prices, promises, deadlines or contact details.
- Leave any token in square brackets, such as [address 1], exactly as it is.
- Plain text only. No greeting or signature beyond what the zones contain."""


@dataclass(frozen=True, slots=True)
class Personalization:
    """Всё, что мы знаем о доноре к моменту письма."""

    host: str
    country: str
    #: Ключи прогона — ниша, по которой донор нашёлся.
    niche: tuple[str, ...] = ()

    def as_prompt(self) -> str:
        lines = [f"Recipient site: {self.host}", f"Target country: {self.country.upper()}"]
        if self.niche:
            lines.append("The site was found in search results for: " + ", ".join(self.niche))
        return "\n".join(lines)


@dataclass(slots=True)
class RewriteResult:
    """Переписанные зоны и то, чего не получилось."""

    zones: dict[str, str] = field(default_factory=dict)
    tokens_spent: int = 0
    #: Почему зон меньше, чем просили. Пусто — всё переписано.
    notes: list[str] = field(default_factory=list)


def build_payload(model: str, *, zones: dict[str, str], about: Personalization) -> dict[str, Any]:
    """Тело запроса с поправкой на семейство модели."""
    user = f"{about.as_prompt()}\n\nZones to rewrite:\n{json.dumps(zones, ensure_ascii=False)}"
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user},
        ],
        # Строгая форма ответа: разбирать свободный текст модели там, где
        # ответ обязан быть словарём, значит чинить разбор после каждой
        # смены модели.
        "response_format": {"type": "json_object"},
    }
    if is_reasoning(model):
        payload["max_completion_tokens"] = max(1500, len(zones) * TOKENS_PER_ZONE_REASONING)
        payload["reasoning_effort"] = "minimal"
    else:
        payload["max_tokens"] = max(600, len(zones) * TOKENS_PER_ZONE_PLAIN)
        payload["temperature"] = 1
    return payload


def parse_zones(content: str, *, expected: set[str]) -> dict[str, str]:
    """Разобрать ответ модели. Лишние ключи отбрасываются с записью в лог.

    Пустой словарь — законный исход: вызывающий оставит зоны шаблонными.
    """
    try:
        body = json.loads(content)
    except ValueError:
        logger.exception("письма: ответ модели не разобран как JSON: %s", content[:200])
        return {}

    if not isinstance(body, dict):
        logger.error("письма: модель вернула %s вместо словаря зон", type(body).__name__)
        return {}

    unknown = sorted(set(body) - expected)
    if unknown:
        # Считается, а не отбрасывается молча: лишний ключ означает, что
        # модель поняла задачу иначе, и это видно только здесь.
        logger.warning("письма: модель вернула лишние зоны: %s", ", ".join(unknown))

    return {
        k: v.strip() for k, v in body.items() if k in expected and isinstance(v, str) and v.strip()
    }


class RewriteClient:
    """Клиент уникализации. Считает токены — это и есть расход на письмо."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        model: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._model = model or cfg.LETTERS_MODEL
        self._api_key = api_key if api_key is not None else cfg.API_KEY
        self._own_client = client is None
        self._http = client or httpx.AsyncClient(timeout=cfg.TIMEOUT_S)

    @property
    def model(self) -> str:
        return self._model

    async def aclose(self) -> None:
        if self._own_client:
            await self._http.aclose()

    async def rewrite(self, rendered: Rendered, about: Personalization) -> RewriteResult:
        """Переписать зоны письма. Незаполненный результат — законный исход."""
        zones = {z.name: z.text for z in rendered.rewritable()}
        refusal = self._refusal(zones)
        if refusal is not None:
            return refusal

        masked, labels = _mask_all(zones)
        if masked is None:
            return RewriteResult(notes=["маскирование не сработало — в модель ничего не ушло"])

        body = await post_chat(
            self._http,
            api_key=self._api_key,
            payload=build_payload(self._model, zones=masked, about=about),
            topic=TOPIC,
        )
        if isinstance(body, Refusal):
            # Причина называется в ноте, а не только в логе: ноту видит
            # оператор в предпросмотре письма, лог — никто.
            return RewriteResult(notes=[str(body)])

        return self._collect(body, zones=zones, labels=labels)

    def _refusal(self, zones: dict[str, str]) -> RewriteResult | None:
        """Причина не звать модель вовсе. `None` — звать можно."""
        if not zones:
            return RewriteResult(notes=["в шаблоне нет переписываемых зон"])
        if not self._api_key:
            return RewriteResult(notes=["LLM_API_KEY не задан — письма уходят шаблонными"])
        return None

    def _collect(
        self,
        body: dict[str, Any],
        *,
        zones: dict[str, str],
        labels: dict[str, dict[str, str]],
    ) -> RewriteResult:
        """Принять ответ модели зона за зоной.

        Непринятая зона называется в заметках: письмо с шаблонным абзацем
        и письмо, переписанное целиком, должны отличаться не только
        процентом, но и объяснением.
        """
        result = RewriteResult(tokens_spent=tokens_of(body))
        answered = parse_zones(content_of(body, topic=TOPIC), expected=set(zones))
        for name, text in answered.items():
            _accept(name, text, labels.get(name, {}), into=result)

        result.notes.extend(
            f"зона «{name}» осталась шаблонной" for name in zones if name not in result.zones
        )
        return result


def _mask_all(zones: dict[str, str]) -> tuple[dict[str, str] | None, dict[str, dict[str, str]]]:
    """Замаскировать все зоны разом.

    Отказ здесь общий на все зоны, а не на одну: если адрес просочился,
    запрос не отправляется вовсе — проверить это после отправки нельзя.
    """
    masked: dict[str, str] = {}
    labels: dict[str, dict[str, str]] = {}
    for name, text in zones.items():
        hidden = masking.mask(text)
        left = masking.leaked(hidden.text)
        if left is not None:
            logger.error(
                "письма: в зоне «%s» остался адрес после маскирования (%s) — "
                "запрос к модели не отправлен",
                name,
                left,
            )
            return None, {}
        masked[name] = hidden.text
        labels[name] = hidden.labels
    return masked, labels


def _accept(name: str, text: str, labels: dict[str, str], *, into: RewriteResult) -> None:
    """Принять переписанную зону, если с ней всё в порядке."""
    try:
        restored = masking.unmask(text, labels)
    except masking.UnmaskError as exc:
        logger.exception("письма: зона «%s» отклонена: %s", name, exc)
        into.notes.append(f"зона «{name}»: {exc}")
        return

    leak = metrics_leak(restored)
    if leak is not None:
        # Метрики в письме запрещены правилами Ahrefs, и нарушение стоит
        # ключа. Зона выбрасывается целиком — чинить её тут нечем.
        logger.error("письма: зона «%s» отклонена, модель дописала метрики (%s)", name, leak)
        into.notes.append(f"зона «{name}»: модель дописала метрики ({leak})")
        return

    into.zones[name] = restored
