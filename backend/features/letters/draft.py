"""Текст первого письма у рассылки: правка на экране перед её созданием.

Умолчание — шаблон из кода (`templates/price_request.txt`). Человек правит
его на экране писем по зонам, и рассылка хранит свой вариант целиком:
шаблон в коде потом поменяют, а письма уже идущей рассылки должны
оставаться тем текстом, который утвердили.

**Правка проходит ровно тот же разбор, что и файл.** Набор зон, подпись,
достижимость коридора — всё это проверяет `template.parse`, а не отдельные
правила для экрана: два набора проверок разошлись бы при первой правке
одного из них. Сверх разбора — две вещи, которые файл проходит на ревью,
а экран нет: неизвестные подстановки и метрики Ahrefs.

**Правится только содержимое зон.** Их набор и вид заданы требованиями:
экран не может ни добавить зону, ни сделать условия переписываемыми.

**Правка идёт требованиями своего этапа.** Оффер рекламодателю разбирается
как оффер: найденная ссылка обязана остаться в неизменяемой зоне, и её
подстановки известны только ему — в письме донору `{{anchor}}` опечатка.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from backend.features.core.domain import Stage
from backend.features.letters import compose, guards
from backend.features.letters.template import (
    HEADER_RE,
    Template,
    TemplateError,
    ZoneKind,
    for_stage,
    parse,
    spec_for,
)

#: Названия зон для человека. Ключи — имена из требований.
TITLES: dict[str, str] = {
    "greeting": "Приветствие",
    "opening": "Вступление",
    "offer": "Кто мы",
    "ask": "Вопросы",
    "terms": "Условия",
    "signature": "Подпись",
}


class LetterConflictError(ValueError):
    """У рассылки уже свой текст письма, а прислан другой."""


@dataclass(frozen=True, slots=True)
class DraftZone:
    name: str
    kind: ZoneKind
    title: str
    text: str


@dataclass(frozen=True, slots=True)
class Draft:
    """Письмо, как его видит экран: тема и зоны по порядку."""

    subject: str
    zones: tuple[DraftZone, ...]

    @classmethod
    def of(cls, source: Template) -> Draft:
        return cls(
            subject=source.subject,
            zones=tuple(
                DraftZone(z.name, z.kind, TITLES.get(z.name, z.name), z.text) for z in source.zones
            ),
        )


def default_draft(stage: Stage = Stage.DONORS) -> Draft:
    return Draft.of(for_stage(stage))


def to_text(subject: str, zones: Mapping[str, str], stage: Stage = Stage.DONORS) -> str:
    """Черновик с экрана — в текст шаблона, проверенный как файл.

    Порядок и вид зон берутся у умолчания этапа: экран правит содержимое,
    а не устройство письма.
    """
    like = for_stage(stage)
    unknown = sorted(set(zones) - {z.name for z in like.zones})
    if unknown:
        raise TemplateError(
            f"Таких зон в письме нет: {', '.join(unknown)}. Набор зон задан требованиями"
        )
    if "\n" in subject.strip():
        raise TemplateError("Тема письма — одна строка")

    lines = [f"subject: {subject.strip()}", ""]
    for zone in like.zones:
        if zone.name not in zones:
            continue
        text = zones[zone.name].strip()
        _check_lines(zone.name, text)
        lines += [f"[{zone.name}] {zone.kind.value}", text, ""]
    text = "\n".join(lines)

    checked = parse(text, spec_for(stage))
    _check_values(checked, stage)
    return text


def _check_lines(zone: str, text: str) -> None:
    """Строки, которые разбор принял бы не за текст.

    `#` в начале строки — комментарий, и абзац пропал бы из письма молча;
    `[имя] вид` — заголовок новой зоны. Оба случая с экрана — опечатка,
    а не намерение, и назвать их надо до того, как письмо собрано.
    """
    for line in text.splitlines():
        if line.startswith("#"):
            raise TemplateError(
                f"В зоне «{TITLES.get(zone, zone)}» строка начинается с «#» — "
                "разбор счёл бы её комментарием и выбросил. Убрать «#» или сдвинуть его"
            )
        if HEADER_RE.match(line):
            raise TemplateError(
                f"В зоне «{TITLES.get(zone, zone)}» строка «{line}» выглядит как заголовок зоны"
            )


#: Ссылка для проверки подстановок: значения не важны, важны имена.
_PROBE_LINK = compose.FoundLink(donor_host="", page_url="", anchor="")


def _check_values(checked: Template, stage: Stage) -> None:
    link = _PROBE_LINK if stage is Stage.ADVERTISERS else None
    known = set(compose.values_for(host="", link=link))
    unknown = sorted(checked.placeholders() - known)
    if unknown:
        names = ", ".join(f"{{{{{name}}}}}" for name in unknown)
        allowed = ", ".join(f"{{{{{name}}}}}" for name in sorted(known))
        raise TemplateError(f"Неизвестные подстановки: {names}. Есть только {allowed}")
    guards.assert_no_metrics(f"{checked.subject}\n{checked.body}")


def assert_same(*, campaign: str, stored: str | None, sent: str) -> None:
    """Текст письма берётся при создании рассылки и дальше не меняется.

    Одноимённая рассылка дополняется, а не заводится заново — так же, как
    со сроками добивок. Но срок, проигнорированный молча, виден в цепочке,
    а проигнорированная правка текста — только у адресата. Поэтому
    расхождение — отказ, а не тихое «взяли прежний».
    """
    if stored != sent:
        raise LetterConflictError(
            f"У рассылки «{campaign}» уже свой текст письма, и он не меняется: "
            "письма в ней должны быть одним утверждённым текстом. Новый текст — "
            "новая рассылка под другим именем"
        )
