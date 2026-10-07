"""Уникализация письма: переписать три зоны под конкретного донора.

Что переписывается, задано ТЗ и разобрано в `template.py`. Здесь — как
именно.

**В модель уходят только переписываемые зоны.** Оффер, условия, подпись
она не видит вовсе, поэтому изменить их не может.
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
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.config import outreach as outreach_cfg
from backend.features.letters import masking
from backend.features.letters.compose import Rendered
from backend.features.letters.guards import metrics_leak
from backend.features.letters.uniqueness import words
from backend.shared.llm import (
    ModelClient,
    Refusal,
    content_of,
    is_reasoning,
    post_chat,
    tokens_of,
)

logger = logging.getLogger(__name__)

#: Тема для логов.
TOPIC = "письма"

#: Запас токенов на зону. Рассуждающая модель тратит их и на размышление.
TOKENS_PER_ZONE_REASONING = 400
TOKENS_PER_ZONE_PLAIN = 200

#: Промпт — файлом в `prompts/`, а не строкой здесь: правка поведения модели —
#: это правка файла, и её видно по пути, а не по чтению диффа этого модуля.
#: `strip` снимает перевод строки в конце файла: в самом промпте его нет.
PROMPT_PATH = Path(__file__).with_name("prompts") / "rewrite.md"
SYSTEM = PROMPT_PATH.read_text(encoding="utf-8").strip()


#: Сколько символов цитаты сайта отдаём модели: на тему хватает фразы,
#: а длинный чужой текст — это и токены, и поверхность для подсказок модели.
ABOUT_MAX = 240

#: Адрес или ссылка в тексте: в чужой цитате их режем, в ответе модели — не
#: принимаем (правило «Never add links… or contact details»).
_CONTACTISH = re.compile(r"https?://\S+|www\.\S+|\S+@\S+\.\w+", re.IGNORECASE)


def site_about(quote: str | None) -> str | None:
    """Цитата со страницы сайта — чистой фразой: без ссылок и адресов, короткой."""
    if not quote:
        return None
    text = " ".join(_CONTACTISH.sub(" ", quote).split())[:ABOUT_MAX].strip()
    return text or None


@dataclass(frozen=True, slots=True)
class Personalization:
    """Всё, что мы знаем о доноре к моменту письма.

    `about` — о чём сайт: цитата его страницы, сохранённая судьёй при отборе.
    Без неё вступление оставалось шаблонным («my topic»), хотя требование —
    «под контент донора» (боевой прогон 06.10.2026). Чужой текст идёт модели
    как данные, а не указание, и чищенным (`site_about`).
    """

    host: str
    country: str
    #: Ключи прогона — ниша, по которой донор нашёлся.
    niche: tuple[str, ...] = ()
    about: str | None = None

    def as_prompt(self) -> str:
        lines = [f"Recipient site: {self.host}", f"Target country: {self.country.upper()}"]
        if self.niche:
            lines.append("The site was found in search results for: " + ", ".join(self.niche))
        about = site_about(self.about)
        if about:
            lines.append(f'What the site publishes (a quote from one of its pages): "{about}"')
        return "\n".join(lines)


@dataclass(slots=True)
class RewriteResult:
    """Переписанные зоны и то, чего не получилось."""

    zones: dict[str, str] = field(default_factory=dict)
    tokens_spent: int = 0
    #: Почему зон меньше, чем просили. Пусто — всё переписано.
    notes: list[str] = field(default_factory=list)


#: Сколько слов зоны просить поменять — пределы. Меньше пятой части модель
#: не отличает от «не трогай», больше половины теряет смысл фразы.
CHANGE_MIN = 0.2
CHANGE_MAX = 0.5


def change_share(rendered: Rendered) -> float:
    """Какую долю слов переписываемых зон просить поменять.

    Цель — середина коридора для письма целиком, а зоны занимают в разных
    шаблонах разную долю. Постоянное «поменяй половину» давало 20% при
    переписываемых 40% письма, но при боевом тексте (23.09.2026), где
    переписывается около 80%, вывело бы письмо к 40% — за верхний край.
    """
    total = len(words(rendered.body))
    rewritable = sum(len(words(z.text)) for z in rendered.rewritable())
    if not total or not rewritable:
        return CHANGE_MAX
    middle = (outreach_cfg.UNIQUENESS_TARGET_MIN + outreach_cfg.UNIQUENESS_TARGET_MAX) / 2
    return min(CHANGE_MAX, max(CHANGE_MIN, middle * total / rewritable))


def build_payload(
    model: str, *, zones: dict[str, str], about: Personalization, change: float = CHANGE_MAX
) -> dict[str, Any]:
    """Тело запроса с поправкой на семейство модели."""
    user = (
        f"{about.as_prompt()}\n\n"
        f"Change roughly {round(change * 100)}% of the words in each zone.\n\n"
        f"Zones to rewrite:\n{json.dumps(zones, ensure_ascii=False)}"
    )
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


class RewriteClient(ModelClient):
    """Клиент уникализации. Считает токены — это и есть расход на письмо."""

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
            payload=build_payload(
                self._model, zones=masked, about=about, change=change_share(rendered)
            ),
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
            _accept(name, text, labels.get(name, {}), before=zones[name], into=result)

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


#: Пункт нумерованного списка: номер в начале строки.
_LIST_ITEM = re.compile(r"^\s*(\d+)[.)]\s", re.MULTILINE)


def _list_changed(before: str, after: str) -> str | None:
    """Потеряла ли модель пункт списка или его номер. `None` — список цел."""
    want = _LIST_ITEM.findall(before)
    got = _LIST_ITEM.findall(after)
    if want == got:
        return None
    return f"в списке были пункты {', '.join(want)}, модель вернула {', '.join(got) or 'ни одного'}"


def _accept(
    name: str, text: str, labels: dict[str, str], *, before: str, into: RewriteResult
) -> None:
    """Принять переписанную зону, если с ней всё в порядке."""
    try:
        restored = masking.unmask(text, labels)
    except masking.UnmaskError as exc:
        logger.exception("письма: зона «%s» отклонена: %s", name, exc)
        into.notes.append(f"зона «{name}»: {exc}")
        return

    lost = _list_changed(before, restored)
    if lost is not None:
        # Вопросы письма — то, на что донор отвечает и что разбирает разбор
        # ответа. Потерянный пункт — потерянный ответ, и чинить зону нечем.
        logger.error("письма: зона «%s» отклонена, %s", name, lost)
        into.notes.append(f"зона «{name}»: {lost}")
        return

    added = _CONTACTISH.findall(restored)
    if any(found not in before for found in added):
        # Правило «Never add links… or contact details» проверяется, а не
        # просится: в запросе теперь чужой текст со страницы сайта.
        logger.error("письма: зона «%s» отклонена, модель дописала ссылку или адрес", name)
        into.notes.append(f"зона «{name}»: модель дописала ссылку или адрес")
        return

    leak = metrics_leak(restored)
    if leak is not None:
        # Метрики в письме запрещены правилами Ahrefs, и нарушение стоит
        # ключа. Зона выбрасывается целиком — чинить её тут нечем.
        logger.error("письма: зона «%s» отклонена, модель дописала метрики (%s)", name, leak)
        into.notes.append(f"зона «{name}»: модель дописала метрики ({leak})")
        return

    into.zones[name] = restored
