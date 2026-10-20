"""Факты о сайте от модели судьи сегмента v4: строгая форма ответа и проверка цитат кодом.

Модель v4 полосу не ставит, а отдаёт факты (промпт `prompts/segment.md`): что за сайт (`kind`),
делает ли его бизнес то, что ищет сегмент (`core`), где сам бизнес (`home`), где он работает
(`served`), работает ли «только там» (`only`) и местный ли он (`local`). Полосу ставит код по таблице
правил и полю гипотезы (`segment_rules.py`): правка поля пересчитывает полосу без модели, а факты
лежат в кэше вердиктов (`sales_segment_verdicts.facts`).

**Форма — у провайдера** (`json_schema`, `strict`): другой формы модель не вернёт; код всё равно
сверяет её (ответ подставной модели и провайдер без строгой формы) — нет поля, не тот тип, значение
не из списка — ответ не разобран: «на проверку», модель спросят снова.

**Цитаты проверяет код** (`SiteText.holds`, как у v3): цитата вида сайта не нашлась — ответ не
доказан целиком, как у v3 («модель сочинила»). Цитата места, страны бизнеса или «только там» не
нашлась — в счёт не идёт этот факт, а не весь ответ; сколько таких — `dropped`: пропуск считается, а не
исчезает молча. Код страны, которого нет в ISO 3166-1, — тоже пропуск.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from backend.features.sales.hypothesis_countries import known_codes
from backend.features.sales.segment_quote import MIN_QUOTE_WORDS, quote_words, unlabeled
from backend.features.sales.segment_text import LABELS, SiteText

logger = logging.getLogger(__name__)

#: Что за сайт: своё дело, посредник, издание о чужих, не компания (госорган, школа, ассоциация,
#: конструктор сайтов), не понять.
KINDS = ("own", "intermediary", "media", "other", "unclear")
#: Делает ли бизнес сайта то, что ищет сегмент (без мест и языка): да, смежное, другое, не видно.
CORES = ("yes", "adjacent", "no", "unsure")
#: «Во все страны мира» в `served`.
WORLD = "*"
#: Ключи ответа — все обязательны.
KEYS = ("kind", "home", "home_quote", "served", "only", "network", "local", "core", "quote", "why")
#: Слова — вместе с числом (`segment_quote.MIN_QUOTE_WORDS`).
SHORT_QUOTE = "цитата короче трёх слов — не доказательство"
NO_QUOTE = "модель не дала цитаты"
MADE_UP = "цитаты нет в тексте сайта — модель не прочла, а сочинила"

_TEXT = {"type": "string"}
#: Строгая форма ответа у провайдера.
SCHEMA: dict[str, Any] = {
    "name": "segment_facts",
    "schema": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": list(KINDS)},
            "home": _TEXT,
            "home_quote": _TEXT,
            "served": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"countries": {"type": "array", "items": _TEXT}, "quote": _TEXT},
                    "required": ["countries", "quote"],
                    "additionalProperties": False,
                },
            },
            "only": _TEXT,
            "network": {"type": "boolean"},
            "local": {"type": "boolean"},
            "core": {"type": "string", "enum": list(CORES)},
            "quote": _TEXT,
            "why": _TEXT,
        },
        "required": list(KEYS),
        "additionalProperties": False,
    },
}


@dataclass(frozen=True, slots=True)
class Place:
    """Где бизнес работает — по одной строке сайта: коды стран и дословные слова сайта."""

    countries: tuple[str, ...]
    quote: str


@dataclass(frozen=True, slots=True)
class SiteFacts:
    """Факты сайта после проверки кодом: только доказанное цитатой."""

    kind: str
    core: str
    #: Слова сайта о том, что он есть и что делает, — найдены в тексте дословно.
    quote: str
    why: str = ""
    #: Страна бизнеса по тексту (ISO 3166-1 alpha-2); пусто — текст её не показал.
    home: str = ""
    home_quote: str = ""
    served: tuple[Place, ...] = ()
    #: Слова сайта «работаем только …»; пусто — не сказано.
    only: str = ""
    local: bool = False
    #: Региональный сайт крупной международной компании или сети (офисы во многих странах).
    network: bool = False
    #: Сколько фактов не в счёт: цитаты нет в тексте или кода страны нет в ISO 3166-1.
    dropped: int = 0

    @property
    def places(self) -> frozenset[str]:
        """Страны, где бизнес работает по тексту, — без «во все страны»."""
        return frozenset(code for place in self.served for code in place.countries if code != WORLD)

    @property
    def world(self) -> bool:
        """Сайт говорит, что работает во всех или почти всех странах мира."""
        return any(WORLD in place.countries for place in self.served)

    def as_json(self) -> dict[str, Any]:
        """Для кэша вердиктов: читается обратно `from_json`."""
        return {
            "kind": self.kind,
            "core": self.core,
            "quote": self.quote,
            "why": self.why,
            "home": self.home,
            "home_quote": self.home_quote,
            "served": [{"countries": list(p.countries), "quote": p.quote} for p in self.served],
            "only": self.only,
            "local": self.local,
            "network": self.network,
            "dropped": self.dropped,
        }

    @classmethod
    def from_json(cls, data: object) -> SiteFacts | None:
        """Факты из кэша; строка не той формы (правили руками) — `None`, громко в лог: модель
        спросят снова, а не решат по обломку."""
        try:
            assert isinstance(data, dict)
            served = tuple(
                Place(tuple(str(code) for code in place["countries"]), str(place["quote"]))
                for place in data.get("served") or ()
            )
            found = cls(
                str(data["kind"]),
                str(data["core"]),
                str(data["quote"]),
                str(data.get("why") or ""),
                str(data.get("home") or ""),
                str(data.get("home_quote") or ""),
                served,
                str(data.get("only") or ""),
                bool(data.get("local")),
                bool(data.get("network")),
                int(data.get("dropped") or 0),
            )
        except (AssertionError, KeyError, TypeError, ValueError) as exc:
            logger.warning("судья сегмента: факты кэша не той формы", extra={"error": repr(exc)})
            return None
        return found if found.kind in KINDS and found.core in CORES else None


def _words(raw: object) -> str:
    """Слова цитаты без наших подписей строк; не строка — пусто."""
    return unlabeled(raw, LABELS).strip() if isinstance(raw, str) else ""


def unproven(quote: str, text: SiteText) -> str | None:
    """Почему цитата вида сайта не доказывает ответ — словами; `None` — доказывает."""
    if not quote:
        return NO_QUOTE
    if quote_words(quote) < MIN_QUOTE_WORDS:
        return SHORT_QUOTE
    if not text.holds(quote):
        return MADE_UP
    return None


def _form(content: str) -> dict[str, Any] | str:
    """Ответ модели → объект строгой формы; иначе — почему нет, словами."""
    try:
        body = json.loads(content)
    except ValueError:
        logger.info("судья сегмента: ответ модели не JSON", extra={"content": content[:120]})
        return "ответ модели не разобран"
    if not isinstance(body, dict):
        return "ответ модели не объект"
    if missing := [key for key in KEYS if key not in body]:
        return f"в ответе модели нет поля «{missing[0]}»"
    kind, core = str(body["kind"]).strip().lower(), str(body["core"]).strip().lower()
    if kind not in KINDS or core not in CORES:
        logger.info(
            "судья сегмента: ответ не из списка", extra={"kind": kind[:20], "core": core[:20]}
        )
        return f"ответ модели не из списка: {kind[:20]!r}, {core[:20]!r}"
    flags = (body["local"], body["network"])
    if not isinstance(body["served"], list) or not all(isinstance(f, bool) for f in flags):
        return "ответ модели не той формы: места, «местный» или «сеть»"
    return {**body, "kind": kind, "core": core}


class _Checked:
    """Проверка фактов по тексту сайта: годное — в факты, негодное — в счёт пропусков."""

    def __init__(self, text: SiteText) -> None:
        self.text, self.dropped = text, 0

    def quote(self, raw: object) -> str:
        """Цитата, найденная в тексте дословно; иначе пусто (и пропуск, если она была)."""
        words = _words(raw)
        if words and self.text.holds(words):
            return words
        self.dropped += int(bool(words))
        return ""

    def code(self, raw: object) -> str:
        code = str(raw).strip().upper() if isinstance(raw, str) else ""
        if code == WORLD or code in known_codes():
            return code
        self.dropped += int(bool(code))
        return ""

    def place(self, raw: object) -> Place | None:
        if not isinstance(raw, dict) or not isinstance(raw.get("countries"), list):
            self.dropped += 1
            return None
        quote = self.quote(raw.get("quote"))
        codes = tuple(dict.fromkeys(code for c in raw["countries"] if (code := self.code(c))))
        return Place(codes, quote) if quote and codes else None


def read_facts(content: str, text: SiteText) -> SiteFacts | str:
    """Ответ модели → факты по тексту сайта; не разобран или цитата вида сайта не доказывает его —
    почему, словами (`unproven`)."""
    form = _form(content)
    if isinstance(form, str):
        return form
    quote = _words(form["quote"])
    if (why_not := unproven(quote, text)) is not None:
        return why_not
    check = _Checked(text)
    home_quote = check.quote(form["home_quote"])
    home = check.code(form["home"]) if home_quote else ""
    served = tuple(found for raw in form["served"] if (found := check.place(raw)) is not None)
    return SiteFacts(
        form["kind"],
        form["core"],
        quote,
        str(form.get("why") or "").strip()[:300],
        home if home != WORLD else "",
        home_quote if home else "",
        served,
        check.quote(form["only"]),
        form["local"],
        form["network"],
        check.dropped,
    )
