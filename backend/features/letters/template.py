"""Шаблон письма: зоны, разбор, проверка.

Устройство шаблона задано ТЗ, а не выбрано здесь: «неизменны оффер,
условия, подпись, юр. блок; меняются приветствие, вступление под контент
донора, формулировка вопроса». Отсюда две зоны — `rewrite` и `fixed` —
и шесть обязательных имён: юридические требования сняты решением стороны
задачи 23.09.2026, и седьмой зоны, юридического блока, больше нет.

**Неизменяемые зоны в модель не уходят вовсе.** Не «просим не менять»,
а физически не отдаём: просьбу модель исполняет почти всегда, и «почти»
здесь означает изменённые условия сделки в письме, которое уже отправлено.

**Набор зон проверяется при разборе.** Шаблон без условий или подписи
собирается так же легко, как правильный, и разница видна только
у адресата. Поэтому отсутствие зоны — отказ разбора, а не предупреждение.

Формат файла разобран построчно и без догадок: строка либо комментарий,
либо `subject:`, либо заголовок зоны, либо текст внутри зоны. Всё
остальное — ошибка с номером строки: молча пропущенная строка шаблона
означает письмо без абзаца, и заметить это можно будет только у адресата.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from backend.config import outreach as cfg
from backend.features.letters.uniqueness import words

#: Заголовок зоны: `[имя] вид`.
HEADER_RE = re.compile(r"^\[([a-z_]+)\]\s+(\w+)\s*$")
_SUBJECT_PREFIX = "subject:"

#: Подстановка `{{имя}}`.
PLACEHOLDER_RE = re.compile(r"\{\{([a-z_]+)\}\}")


class TemplateError(ValueError):
    """Шаблон разобрать нельзя. Сообщение называет строку и что делать."""


class ZoneKind(StrEnum):
    #: Уходит в модель и переписывается под донора.
    REWRITE = "rewrite"
    #: В модель не уходит. Оффер, условия, подпись.
    FIXED = "fixed"


#: Зоны и их вид — ровно по ТЗ. Порядок здесь не важен, порядок в письме
#: задаёт сам файл: переставить абзацы — это правка шаблона, а не кода.
REQUIRED_ZONES: dict[str, ZoneKind] = {
    "greeting": ZoneKind.REWRITE,  # приветствие
    "opening": ZoneKind.REWRITE,  # вступление под контент донора
    "ask": ZoneKind.REWRITE,  # формулировка вопроса о цене
    "offer": ZoneKind.FIXED,  # кто мы и чего хотим
    "terms": ZoneKind.FIXED,  # условия
    "signature": ZoneKind.FIXED,  # подпись
}

#: Зоны добивки. Их три и все неизменяемые: переписывать в напоминании
#: нечего — оно короткое и живёт в треде, где первое письмо процитировано
#: целиком.
FOLLOWUP_ZONES: dict[str, ZoneKind] = {
    "greeting": ZoneKind.FIXED,
    "reminder": ZoneKind.FIXED,  # напоминание о прошлом письме
    "signature": ZoneKind.FIXED,
}

#: Без чего письмо уходит неподписанным. Проверяется в шаблоне, а не перед
#: отправкой: шаблон без подписи надо чинить один раз, а не ловить на каждом
#: письме.
#:
#: Юридического блока (адрес и отписка) здесь больше нет: юридические
#: требования сняты решением стороны задачи 23.09.2026.
REQUIRED_IN_ZONE: dict[str, tuple[str, ...]] = {
    "signature": ("sender_name",),
}

#: Какую долю слов зоны переписывание реально меняет. Остальное — имена,
#: числа и служебные слова, которые остаются на месте при любой правке.
#: Число нужно для одной проверки: достижим ли вообще нижний край коридора.
REWRITE_YIELD = 0.7


@dataclass(frozen=True, slots=True)
class Spec:
    """Чего требуют от шаблона письма этого шага.

    Видов писем два, и требования у них разные: у первого семь зон и
    коридор уникальности, у добивки четыре зоны и никакого коридора —
    переписывать в ней нечего. Без этого различия шаблон добивки нельзя
    было бы даже разобрать: проверка набора зон одна на всех.
    """

    zones: dict[str, ZoneKind]
    #: Проверять ли достижимость коридора уникальности. У добивки
    #: переписываемых зон нет вовсе, и проверка отказала бы всегда.
    corridor: bool


#: Первое письмо: набор зон задан ТЗ, уникализация обязательна.
FIRST = Spec(zones=REQUIRED_ZONES, corridor=True)

#: Добивка: шаблон целиком, модель не участвует (решение 21.09.2026).
FOLLOWUP = Spec(zones=FOLLOWUP_ZONES, corridor=False)


@dataclass(frozen=True, slots=True)
class Zone:
    name: str
    kind: ZoneKind
    text: str


@dataclass(frozen=True, slots=True)
class Template:
    """Разобранный шаблон. Порядок зон — порядок абзацев письма."""

    subject: str
    zones: tuple[Zone, ...]

    def zone(self, name: str) -> Zone:
        for zone in self.zones:
            if zone.name == name:
                return zone
        raise TemplateError(f"В шаблоне нет зоны «{name}»")

    def of_kind(self, kind: ZoneKind) -> tuple[Zone, ...]:
        return tuple(z for z in self.zones if z.kind is kind)

    @property
    def body(self) -> str:
        """Письмо целиком, ещё с подстановками."""
        return "\n\n".join(z.text for z in self.zones)

    @property
    def rewritable_share(self) -> float:
        """Какую долю слов письма занимают переписываемые зоны."""
        total = len(words(self.body))
        if not total:
            return 0.0
        return len(words("\n".join(z.text for z in self.of_kind(ZoneKind.REWRITE)))) / total

    def placeholders(self) -> set[str]:
        return set(PLACEHOLDER_RE.findall(f"{self.subject}\n{self.body}"))


@dataclass
class _Draft:
    """Накопитель разбора: заголовок зоны и собранные под ним строки."""

    name: str
    kind: ZoneKind
    lines: list[str]


def _kind_of(raw: str, line_no: int) -> ZoneKind:
    try:
        return ZoneKind(raw)
    except ValueError:
        allowed = ", ".join(k.value for k in ZoneKind)
        raise TemplateError(
            f"Строка {line_no}: вид зоны «{raw}» неизвестен. Бывают: {allowed}"
        ) from None


def _finish(draft: _Draft | None, into: list[Zone]) -> None:
    if draft is None:
        return
    text = "\n".join(draft.lines).strip()
    if not text:
        raise TemplateError(f"Зона «{draft.name}» пуста — в письме будет пропущен абзац")
    into.append(Zone(name=draft.name, kind=draft.kind, text=text))


def _preamble(raw: str, line_no: int) -> str | None:
    """Строка до первой зоны: либо тема, либо пусто, либо ошибка.

    Третьего варианта нет намеренно: молча пропущенная строка шаблона
    означает письмо без абзаца, и увидит это только адресат.
    """
    stripped = raw.strip()
    if not stripped:
        return None
    if stripped.lower().startswith(_SUBJECT_PREFIX):
        return stripped[len(_SUBJECT_PREFIX) :].strip()
    raise TemplateError(
        f"Строка {line_no}: текст до первой зоны — «{stripped[:60]}». "
        "Текст письма живёт внутри зоны; пояснение начинают с решётки"
    )


def _scan(text: str) -> tuple[str, list[Zone]]:
    """Построчный разбор. Непонятная строка — отказ, а не пропуск."""
    subject = ""
    zones: list[Zone] = []
    draft: _Draft | None = None

    for line_no, raw in enumerate(text.splitlines(), start=1):
        if raw.startswith("#"):
            continue

        header = HEADER_RE.match(raw)
        if header is not None:
            _finish(draft, zones)
            draft = _Draft(header.group(1), _kind_of(header.group(2), line_no), [])
        elif draft is not None:
            draft.lines.append(raw)
        else:
            subject = _preamble(raw, line_no) or subject

    _finish(draft, zones)
    return subject, zones


def _check_zones(zones: list[Zone], spec: Spec) -> None:
    found = {z.name: z.kind for z in zones}
    required = spec.zones
    missing = [name for name in required if name not in found]
    if missing:
        raise TemplateError(
            f"В шаблоне нет обязательных зон: {', '.join(missing)}. "
            "Набор зон задан ТЗ и менять его здесь нельзя"
        )

    extra = [name for name in found if name not in required]
    if extra:
        raise TemplateError(
            f"В шаблоне лишние зоны: {', '.join(extra)}. "
            "Новая зона означает правку ТЗ, а не шаблона"
        )

    for name, kind in required.items():
        if found[name] is not kind:
            raise TemplateError(
                f"Зона «{name}» объявлена как {found[name].value}, а должна быть "
                f"{kind.value}: {'условия сделки в модель не уходят' if kind is ZoneKind.FIXED else 'эта зона переписывается под донора'}"
            )


def _check_placeholders(template: Template) -> None:
    for zone_name, required in REQUIRED_IN_ZONE.items():
        text = template.zone(zone_name).text
        absent = [p for p in required if f"{{{{{p}}}}}" not in text]
        if absent:
            raise TemplateError(
                f"В зоне «{zone_name}» нет подстановок: {', '.join(absent)}. "
                "Без них письмо уходит неподписанным"
            )


def _check_corridor(template: Template) -> None:
    """Достижим ли нижний край коридора вообще.

    Если переписываемая часть занимает, скажем, десятую долю письма, то
    даже полная замена каждого её слова не даст пятнадцати процентов
    отличия — и каждое письмо будет уходить с пометкой «ниже коридора».
    Узнать это надо при сборке шаблона, а не на сотом письме.
    """
    share = template.rewritable_share
    reachable = share * REWRITE_YIELD
    if reachable < cfg.UNIQUENESS_TARGET_MIN:
        raise TemplateError(
            f"Переписываемые зоны занимают {round(share * 100)}% письма — "
            f"нижний край коридора ({round(cfg.UNIQUENESS_TARGET_MIN * 100)}%) недостижим: "
            f"даже полное переписывание даст около {round(reachable * 100)}%. "
            "Удлинить вступление и вопрос или укоротить неизменяемую часть"
        )


def parse(text: str, spec: Spec = FIRST) -> Template:
    """Разобрать и проверить шаблон."""
    subject, zones = _scan(text)
    if not subject:
        raise TemplateError("В шаблоне нет строки «subject:» — письмо уйдёт без темы")

    _check_zones(zones, spec)
    template = Template(subject=subject, zones=tuple(zones))
    _check_placeholders(template)
    if spec.corridor:
        _check_corridor(template)
    return template


def load(path: Path, spec: Spec = FIRST) -> Template:
    """Прочитать шаблон с диска."""
    if not path.exists():
        raise TemplateError(f"Шаблон письма не найден: {path}")
    return parse(path.read_text(encoding="utf-8"), spec)


TEMPLATES = Path(__file__).parent / "templates"

#: Шаблон, лежащий рядом. Текст выдуман — боевой приходит со стороны задачи.
DEFAULT_PATH = TEMPLATES / "price_request.txt"

#: Оффер рекламодателю, Этап 2. Набор зон тот же, что у письма донору,
#: а границы другие: цена донора не называется, метрики провайдера
#: не упоминаются, про площадку говорится только то, что видел обход, —
#: страница и анкор.
ADVERTISER_PATH = TEMPLATES / "advertiser_offer.txt"


def default() -> Template:
    return load(DEFAULT_PATH)


def advertiser() -> Template:
    """Шаблон оффера рекламодателю.

    Отдельная функция, а не параметр: письма двух этапов расходятся
    не набором зон, а тем, чего в них нельзя, — и место, где это
    записано, должно быть одно.
    """
    return load(ADVERTISER_PATH)


def followup(step: int) -> Template:
    """Шаблон добивки. Шаг 1 — первое напоминание, 2 — последнее.

    Шаблоны лежат файлами рядом с первым письмом, а не строками в базе:
    текст добивки один на всю рассылку, правится редко и должен
    проходить ревью кодом.
    """
    path = TEMPLATES / f"followup_{step}.txt"
    if not path.exists():
        raise TemplateError(
            f"Шаблона добивки для шага {step} нет ({path.name}). "
            "Шагов у цепочки столько, сколько шаблонов рядом с первым письмом"
        )
    return load(path, FOLLOWUP)
