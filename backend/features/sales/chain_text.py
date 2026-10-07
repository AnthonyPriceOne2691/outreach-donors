"""Текст шаблона цепочки писем продаж: разбор зон и правила записи — без базы.

**Тело — в формате зон шаблонов доноров** (`letters/template.py`): строка `[имя] rewrite`
открывает зону, которую модель переписывает под адресата, `[имя] fixed` — зону, которая
уходит как есть и в модель не попадает; подстановка — `{{имя}}`. Разбор свой, а не
`template.parse`: тот требует набор зон доноров и подпись в шаблоне, а у продаж подпись
и физический адрес дописывает сборка из настроек отправителя (`sender.py`) — шаблон их
не повторяет.

**Правила записи — здесь, одни для экрана, консоли и предпросмотра:**
- первое письмо (шаг 1) — с темой, без «Re:» и «Fwd:» (конституция: поддельная
  переписка — обман адресата); переписываемая часть дотягивает до коридора отличия;
- добивки (шаги 2 и 3) — без темы: они идут в той же переписке; зоны только `fixed` —
  модель в добивках не участвует, как у доноров;
- подстановки — только из `PLACEHOLDERS`; незнакомая или одиночная скобка — отказ;
- метрик Ahrefs нет (`guards.assert_no_metrics`);
- подписи и адреса нет ни зоной, ни подстановкой, ни текстом из настроек (`unsigned`).

Набор, гипотеза, версия и запись — `chain.py`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from backend.config import outreach as cfg
from backend.features.letters import compose, guards
from backend.features.letters.template import (
    HEADER_RE,
    PLACEHOLDER_RE,
    REWRITE_YIELD,
    Template,
    TemplateError,
    Zone,
    ZoneKind,
)
from backend.features.sales.models import SUBJECT_LENGTH
from backend.features.sales.sender import Sender

#: Шаги цепочки: 1 — первое письмо, 2 и 3 — добивки. Больше трёх писем человеку,
#: который дважды промолчал, — жалоба на спам, а не настойчивость (как у доноров).
STEPS = (1, 2, 3)
FIRST_STEP = 1
#: Шаг словами — в отчёте консоли и после «нет» в отказе неполной цепочки.
STEP_TITLES = {1: "первое письмо", 2: "первая добивка", 3: "вторая добивка"}
STEP_WORDS = {1: "первого письма", 2: "первой добивки", 3: "второй добивки"}
#: Языки писем. Новый язык — код здесь: экран берёт список из ответа сервера.
LANGUAGES = ("ru", "en")
#: Подстановки шаблона: имя адресата, компания, сайт компании — значения у лида.
PLACEHOLDERS = ("name", "company", "site")
#: Подстановки, которых у шаблона продаж нет: подпись, имя отправителя, адрес и отписку
#: дописывает сборка из настроек отправителя, а не шаблон.
SIGNED = frozenset(
    {"sender_name", "signature", "postal_address", "physical_address", "address", "unsubscribe_url"}
)
#: Зоны, которых у шаблона продаж нет — по той же причине.
RESERVED_ZONES = frozenset({"signature", "address"})
#: Предел тела: письмо, а не документ.
BODY_LENGTH = 10_000
#: Выдуманные значения подстановок для предпросмотра — по языку письма.
SAMPLE: dict[str, dict[str, str]] = {
    "ru": {"name": "Алекс Пример", "company": "Компания-пример", "site": "example.com"},
    "en": {"name": "Alex Example", "company": "Example Company", "site": "example.com"},
}


#: Тема, выдающая себя за ответ или пересылку: «Re:», «Fwd:», «Отв:» — с номером и без.
_REPLY = re.compile(r"(re|fwd?|отв|ответ)\s*(\[\d+\]|\(\d+\))?\s*:", re.IGNORECASE)
#: Строка, похожая на заголовок зоны, но не заголовок: `[Offer] fixed`, ` [offer] fixed`.
_LOOSE_HEADER = re.compile(r"\s*\[[^\]]*\]\s*\S*\s*")
_BRACE = re.compile(r"[{}]")


class ChainError(TemplateError):
    """Шаблон цепочки не годится. Текст — что поправить.

    Наследник отказа разбора шаблонов доноров: тот же смысл («шаблон разобрать нельзя»),
    тот же ответ экрану (400 в `api/errors.py`) и та же пометка «повтор не поможет»."""


@dataclass(frozen=True, slots=True)
class StepTemplate:
    """Шаблон шага, как его заводят и правят: поля уже приведены и проверены."""

    step: int
    language: str
    subject: str | None
    #: Тело в каноническом виде: зоны по порядку, между ними пустая строка.
    body: str
    zones: tuple[Zone, ...] = field(compare=False)
    active: bool = True

    @property
    def key(self) -> tuple[int, str]:
        """По шагу и языку шаблон узнают в наборе: экран и повторная загрузка."""
        return self.step, self.language

    @property
    def letter(self) -> Template:
        """Шаблон письма в устройстве доноров — для подстановок и доли переписываемого."""
        return Template(subject=self.subject or "", zones=self.zones)


def _step(value: int) -> int:
    if value not in STEPS:
        raise ChainError(f"шага {value} нет: 1 — первое письмо, 2 и 3 — добивки")
    return value


def language_code(value: str) -> str:
    code = value.strip().lower()
    if code not in LANGUAGES:
        raise ChainError(f"языка «{value}» у цепочки нет; есть: {', '.join(LANGUAGES)}")
    return code


def _subject(step: int, value: str | None) -> str | None:
    text = " ".join((value or "").split())
    if step != FIRST_STEP:
        if text:
            raise ChainError(
                "у добивки темы нет — она уходит в той же переписке, тему даёт первое письмо"
            )
        return None
    if not text:
        raise ChainError("у первого письма нет темы — письмо без темы выглядит как спам")
    if len(text) > SUBJECT_LENGTH:
        raise ChainError(f"тема длиннее {SUBJECT_LENGTH} знаков ({len(text)}) — нужна короткая")
    if (prefix := _REPLY.match(text)) is not None:
        raise ChainError(
            f"тема «{text[:40]}» начинается с «{prefix.group(0)}» — переписки ещё нет, а такая "
            "приставка выдаёт первое письмо за ответ или пересылку: это обман адресата "
            "и прямой путь в спам"
        )
    return text


def _text(value: str) -> str:
    lines = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    text = "\n".join(line.rstrip() for line in lines).strip()
    if not text:
        raise ChainError("нет текста письма — начните с «[имя] rewrite» или «[имя] fixed»")
    if len(text) > BODY_LENGTH:
        raise ChainError(f"текст длиннее {BODY_LENGTH} знаков ({len(text)}) — это не письмо")
    return text


@dataclass
class _Opened:
    """Зона, которую разбор сейчас наполняет строками."""

    name: str
    kind: ZoneKind
    lines: list[str] = field(default_factory=list)

    def closed(self) -> Zone:
        text = "\n".join(self.lines).strip()
        if not text:
            raise ChainError(f"зона «{self.name}» пуста — в письме пропал бы абзац")
        return Zone(name=self.name, kind=self.kind, text=text)


def _kind(raw: str, number: int) -> ZoneKind:
    try:
        return ZoneKind(raw)
    except ValueError:
        raise ChainError(
            f"строка {number}: вида зоны «{raw}» нет — rewrite (переписывает модель) "
            "или fixed (уходит как есть)"
        ) from None


def _header(raw: str, number: int, seen: set[str]) -> _Opened | None:
    """Заголовок зоны — новая зона; строка, только похожая на заголовок, — отказ."""
    found = HEADER_RE.match(raw)
    if found is None:
        if _LOOSE_HEADER.fullmatch(raw):
            raise ChainError(
                f"строка {number} «{raw.strip()[:40]}» похожа на заголовок зоны: заголовок — "
                "«[имя] rewrite» или «[имя] fixed» с начала строки, имя латиницей строчными"
            )
        return None
    name = found.group(1)
    if name in RESERVED_ZONES:
        raise ChainError(
            f"строка {number}: зоны «{name}» у шаблона продаж нет — подпись и физический "
            "адрес допишет сборка из настроек отправителя"
        )
    if name in seen:
        raise ChainError(f"строка {number}: зона «{name}» уже есть — имя зоны в письме одно")
    seen.add(name)
    return _Opened(name, _kind(found.group(2), number))


def zones_of(text: str) -> tuple[Zone, ...]:
    """Тело → зоны по порядку. Непонятная строка — отказ с номером, а не пропуск."""
    zones: list[Zone] = []
    current: _Opened | None = None
    seen: set[str] = set()
    for number, raw in enumerate(text.split("\n"), start=1):
        if raw.startswith("#"):
            raise ChainError(
                f"строка {number} начинается с «#» — комментариев у шаблона в базе нет, "
                "а шаблон доноров счёл бы её комментарием; уберите «#» или сдвиньте его"
            )
        opened = _header(raw, number, seen)
        if opened is not None:
            if current is not None:
                zones.append(current.closed())
            current = opened
        elif current is not None:
            current.lines.append(raw)
        elif raw.strip():
            raise ChainError(
                f"строка {number}: текст до первой зоны — «{raw.strip()[:40]}». Текст письма "
                "живёт внутри зоны: начните с «[имя] rewrite» или «[имя] fixed»"
            )
    if current is None:
        raise ChainError("в тексте нет ни одной зоны — начните с «[имя] rewrite» или «[имя] fixed»")
    return (*zones, current.closed())


def _placeholders(text: str) -> None:
    allowed = ", ".join(f"{{{{{name}}}}}" for name in PLACEHOLDERS)
    for name in sorted(set(PLACEHOLDER_RE.findall(text))):
        if name in SIGNED:
            raise ChainError(
                f"подстановки {{{{{name}}}}} у шаблона продаж нет — подпись, имя отправителя, "
                "адрес и отписку допишет сборка из настроек отправителя"
            )
        if name not in PLACEHOLDERS:
            raise ChainError(f"подстановки {{{{{name}}}}} нет; есть только {allowed}")
    rest = PLACEHOLDER_RE.sub("", text)
    if (brace := _BRACE.search(rest)) is not None:
        near = " ".join(rest[max(brace.start() - 20, 0) : brace.end() + 20].split())
        raise ChainError(
            f"скобка вне подстановки: «{near}» — подстановка пишется двумя скобками: {allowed}"
        )


def _corridor(letter: Template) -> None:
    """Достижим ли нижний край коридора отличия — правило шаблонов доноров."""
    share = letter.rewritable_share
    reachable = share * REWRITE_YIELD
    if reachable < cfg.UNIQUENESS_TARGET_MIN:
        raise ChainError(
            f"переписываемые зоны занимают {round(share * 100)}% письма — нижний край коридора "
            f"отличия ({round(cfg.UNIQUENESS_TARGET_MIN * 100)}%) недостижим: даже полное "
            f"переписывание даст около {round(reachable * 100)}%. Удлините зоны rewrite "
            "или укоротите fixed"
        )


def _checked_zones(step: int, text: str) -> tuple[Zone, ...]:
    zones = zones_of(text)
    if step != FIRST_STEP and any(zone.kind is ZoneKind.REWRITE for zone in zones):
        raise ChainError(
            "добивку модель не переписывает — она короткая и идёт в той же переписке; "
            "зоны добивки — только fixed"
        )
    return zones


def _canonical(zones: Iterable[Zone]) -> str:
    return "\n\n".join(f"[{zone.name}] {zone.kind.value}\n{zone.text}" for zone in zones)


def step_template(
    *, step: int, language: str, body: str, subject: str | None = None, active: bool = True
) -> StepTemplate:
    """Поля шаблона → проверенный шаблон. Отказ — первой причиной, словами."""
    number = _step(step)
    code = language_code(language)
    title = _subject(number, subject)
    zones = _checked_zones(number, _text(body))
    letter = Template(subject=title or "", zones=zones)
    _placeholders(f"{letter.subject}\n{letter.body}")
    guards.assert_no_metrics(f"{letter.subject}\n{letter.body}")
    if number == FIRST_STEP:
        _corridor(letter)
    return StepTemplate(number, code, title, _canonical(zones), zones, bool(active))


def _squashed(text: str) -> str:
    return " ".join(text.split()).casefold()


def unsigned(new: StepTemplate, found: Sender) -> None:
    """Подпись и адрес из настроек отправителя в тексте не повторяются: их допишет сборка,
    и в письме они встали бы дважды."""
    body = _squashed(new.body)
    for name, words in (("signature", "подпись"), ("physical_address", "физический адрес")):
        value = found.values.get(name)
        if value and _squashed(value) in body:
            raise ChainError(
                f"в тексте — {words} из настроек отправителя: сборка допишет это сама, "
                "и в письме оно встало бы дважды; уберите из шаблона"
            )


@dataclass(frozen=True, slots=True)
class Preview:
    """Письмо, как его увидит адресат: зоны с выдуманными значениями, затем подпись
    и физический адрес из настроек — в этом порядке их допишет сборка."""

    subject: str | None
    zones: tuple[Zone, ...]
    values: dict[str, str]
    sender: Sender


def preview(new: StepTemplate, found: Sender) -> Preview:
    """Подставить выдуманные значения. Подписи и адреса в шаблоне нет — они из настроек."""
    values = SAMPLE[new.language]
    rendered = compose.render(new.letter, values)
    return Preview(rendered.subject or None, rendered.zones, values, found)
