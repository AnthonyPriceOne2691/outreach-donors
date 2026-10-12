"""Текст шаблона цепочки писем продаж: разбор зон и правила записи — без базы.

**Тело — в формате зон шаблонов доноров** (`letters/template.py`): строка `[имя] rewrite`
открывает зону, которую модель может переписать под адресата — в отдельном письме она может
остаться текстом шаблона: какие из переписанных зон принять, сборка подбирает под коридор
отличия (`corridor.py`); `[имя] fixed` — зону, которая уходит как есть и в модель не
попадает; подстановка — `{{имя}}`. Разбор свой, а не
`template.parse`: тот требует набор зон доноров и подпись в шаблоне, а у продаж подпись
и физический адрес дописывает сборка из настроек отправителя (`sender.py`) — шаблон их
не повторяет.

**Правила записи — здесь, одни для экрана, консоли и предпросмотра:**
- первое письмо (шаг 1) — с темой, без «Re:» и «Fwd:» (конституция: поддельная
  переписка — обман адресата); переписываемая часть дотягивает до коридора отличия;
- добивки (шаги 2 и 3) — без темы: они идут в той же переписке. Зоны `rewrite` — как у
  шага 1 (ТЗ: формулировки ротируются, двух одинаковых писем не уходит): тексты добивок
  переписывает сборка первого письма, каждый в своём коридоре (`followup_texts.py`); у
  добивки с ними переписываемая часть тоже дотягивает до коридора. Добивка только из
  `fixed` записывается, как раньше: она уходит текстом шаблона;
- коридор достижим, но трудно (`corridor_warning`), — шаг записывается с предупреждением
  словами, а не отказом: у каждого шага своё, по его числам;
- подстановки — только из `PLACEHOLDERS`; незнакомая или одиночная скобка — отказ. У имени —
  ещё форма с запасным текстом: `{{name|there}}` — лиду без имени запасной текст (`spare`);
- метрик Ahrefs нет (`guards.assert_no_metrics`);
- подписи и адреса нет ни зоной, ни подстановкой, ни текстом из настроек (`unsigned`).

Набор, гипотеза, версия и запись — `chain.py`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from backend.config import outreach as cfg
from backend.features.letters import compose, guards, uniqueness
from backend.features.letters.rewrite import CHANGE_MIN, change_share
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
#: Подстановка с запасным текстом — только у имени: `{{name|there}}` — лиду с именем имя, без
#: имени — запасной текст. Лид без имени (назван в ответе «пишите другому», загружен из файла
#: без имени) при голом `{{name}}` ждал бы вечно: «у лида нет значения» (`letter.lacking`).
SPARE_OF = "name"
#: Запасной текст — обращение, а не абзац: до 30 знаков, одной строкой, без скобок.
SPARE_LENGTH = 30
#: Подсказка формы словами — экрану и отказам.
SPARE_HINT = "лиду без имени — {{name|there}}"
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


#: Просьба к модели мельче трети слов зоны — коридор трудно достижим: так мелко модель не
#: правит. Пилот 11.10.2026: шаблон целиком `rewrite`, просьба — пятая часть слов, и три
#: сборки подряд вне коридора; живые прогоны того же дня — модель переписывает такие зоны
#: почти целиком (42–66% отличия письма), и в коридор письмо ложится только подбором зон
#: (`corridor.py`). Просьбу считает `rewrite.change_share`: при середине коридора 20% порог —
#: переписываемое больше 60% письма; шаблон с переписываемыми 30–50% ложится с первого вызова.
HARD_CHANGE = 1 / 3
#: Переписываемая зона короче — даже самая малая просьба модели (`CHANGE_MIN`, пятая часть
#: слов) в ней меньше слова: «Hi {{name}},» модель не трогает или переписывает целиком.
SHORT_ZONE = round(1 / CHANGE_MIN)
#: Одна зона `rewrite` больше этой доли письма — подбору зон выбирать нечего («всё или
#: ничего»), а модель переписывает такую зону почти целиком. Живые прогоны 12.10.2026 (по 20
#: писем): одна зона на 41% письма — в коридоре 20 из 20 с первого вызова; одна зона на 55–87%
#: добивки — с первого вызова 0–3 из 20, после повторов шаблоном уходили 3–19 из 20; та же
#: добивка двумя зонами по предложению — 20 из 20 с первого вызова.
SINGLE_ZONE_MAX = 0.45
#: Совет словами: модели — вступление, по предложению, остальное уходит как есть.
ADVICE = (
    "rewrite — только вступление, около 30–50% письма, 2–3 зонами по предложению, остальное — fixed"
)
#: Тот же совет добивке: вступления у неё нет, доля и деление — те же.
FOLLOWUP_ADVICE = "rewrite — около 30–50% добивки, 2–3 зонами по предложению, остальное — fixed"

#: Тема, выдающая себя за ответ или пересылку: «Re:», «Fwd:», «Отв:» — с номером и без.
_REPLY = re.compile(r"(re|fwd?|отв|ответ)\s*(\[\d+\]|\(\d+\))?\s*:", re.IGNORECASE)
#: Строка, похожая на заголовок зоны, но не заголовок: `[Offer] fixed`, ` [offer] fixed`.
_LOOSE_HEADER = re.compile(r"\s*\[[^\]]*\]\s*\S*\s*")
_BRACE = re.compile(r"[{}]")
#: Всё, что похоже на подстановку с запасным текстом, — и кривое тоже: отказ словами.
_PIPED = re.compile(r"\{\{([^{}|]*)\|([^{}]*)\}\}")
#: Годная подстановка с запасным текстом — её заменяет сборка (`spare`).
_SPARE = re.compile(r"\{\{name\|([^{}|\n]+)\}\}")


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

    @property
    def warning(self) -> str | None:
        """Коридор отличия достижим, но трудно — словами (`corridor_warning`); `None` — нет."""
        return corridor_warning(self.step, self.zones)


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
            f"строка {number}: вида зоны «{raw}» нет — rewrite (может переписать модель) "
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


def _spare_problem(text: str) -> str | None:
    """Что не так с запасным текстом — словами; `None` — годен."""
    shown = text.strip()
    if "|" in text:
        return "черта в подстановке одна: имя, черта, запасной текст"
    if "\n" in text:
        return "запасной текст — одной строкой"
    if not shown:
        return "запасной текст пуст — лиду без имени в письме осталась бы дыра"
    if len(shown) > SPARE_LENGTH:
        return f"запасной текст длиннее {SPARE_LENGTH} знаков ({len(shown)}) — нужно обращение"
    return None


def _spares(text: str) -> str:
    """Подстановки с запасным текстом — годные, иначе отказ словами; текст — без них."""
    for found in _PIPED.finditer(text):
        whole = found.group(0)[:60]
        if found.group(1) != SPARE_OF:
            raise ChainError(
                f"«{whole}»: запасной текст — только у имени, строчными и без пробелов у "
                f"скобок: {SPARE_HINT}"
            )
        if (why := _spare_problem(found.group(2))) is not None:
            raise ChainError(f"«{whole}»: {why} — {SPARE_HINT}")
    return _PIPED.sub("", text)


def _placeholders(text: str) -> None:
    allowed = ", ".join(f"{{{{{name}}}}}" for name in PLACEHOLDERS)
    rest = _spares(text)
    for name in sorted(set(PLACEHOLDER_RE.findall(rest))):
        if name in SIGNED:
            raise ChainError(
                f"подстановки {{{{{name}}}}} у шаблона продаж нет — подпись, имя отправителя, "
                "адрес и отписку допишет сборка из настроек отправителя"
            )
        if name not in PLACEHOLDERS:
            raise ChainError(f"подстановки {{{{{name}}}}} нет; есть только {allowed}")
    rest = PLACEHOLDER_RE.sub("", rest)
    if (brace := _BRACE.search(rest)) is not None:
        near = " ".join(rest[max(brace.start() - 20, 0) : brace.end() + 20].split())
        raise ChainError(
            f"скобка вне подстановки: «{near}» — подстановка пишется двумя скобками: {allowed}; "
            f"{SPARE_HINT}"
        )


def spare(text: str, name: str) -> str:
    """`{{name|запасное}}` у лида с именем — `{{name}}` (имя подставит сборка), без имени —
    запасной текст. Подстановки — до переписки: модель видит уже текст, а не скобки."""
    named = bool(name.strip())
    return _SPARE.sub(lambda found: "{{name}}" if named else found.group(1).strip(), text)


def filled(letter: Template, name: str) -> Template:
    """Шаблон письма, где `{{name|…}}` — уже имя или запасной текст (`spare`)."""
    return Template(
        subject=spare(letter.subject, name),
        zones=tuple(Zone(zone.name, zone.kind, spare(zone.text, name)) for zone in letter.zones),
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


def _fine_ask(letter: Template) -> list[str]:
    """Просьба модели мельче трети слов зоны — переписываемого слишком много."""
    asked = change_share(compose.Rendered(subject=letter.subject, zones=letter.zones))
    if asked >= HARD_CHANGE:
        return []
    return [
        f"модель просят менять около {round(asked * 100)}% слов каждой зоны, а так мелко она "
        "не правит — переписывает зоны почти целиком, и в коридор письмо ложится, только если "
        "часть зон останется текстом шаблона"
    ]


def _single_zone(letter: Template) -> list[str]:
    """Одна зона `rewrite` на большую долю письма: подбору выбирать нечего (`SINGLE_ZONE_MAX`)."""
    zones = letter.of_kind(ZoneKind.REWRITE)
    if len(zones) != 1 or letter.rewritable_share <= SINGLE_ZONE_MAX:
        return []
    share = round(letter.rewritable_share * 100)
    return [
        f"одна зона rewrite «{zones[0].name}» — {share}% письма: модель перепишет её почти "
        "целиком, а подбору зон выбрать нечего — разбейте её на 2–3 зоны по предложению"
    ]


def _short_zones(letter: Template) -> list[str]:
    """Переписываемые зоны в несколько слов: приветствие, подпись строкой."""
    short = [
        f"«{zone.name}»"
        for zone in letter.of_kind(ZoneKind.REWRITE)
        if len(uniqueness.words(zone.text)) < SHORT_ZONE
    ]
    if not short:
        return []
    what, them = ("зона", "её") if len(short) == 1 else ("зоны", "их")
    return [
        f"{what} rewrite {', '.join(short)} короче {SHORT_ZONE} слов — модель {them} "
        "не трогает или переписывает целиком"
    ]


def corridor_warning(step: int, zones: Iterable[Zone]) -> str | None:
    """Коридор отличия достижим, но трудно: предупреждение шагу словами, `None` — годен.

    У каждого шага — по его числам: доля переписываемого и короткие зоны своего письма.
    Добивке только из `fixed` предупреждать не о чем: модель её не видит. Отказ — только у
    недостижимого нижнего края (`_corridor`). Трудный шаблон записывается, а в коридор письма
    кладёт подбор зон: часть переписанного остаётся текстом шаблона; не легло — новый вызов
    модели (`sales/corridor.py`). Число в совете — доля переписываемого."""
    letter = Template(subject="", zones=tuple(zones))
    found = _fine_ask(letter) + _single_zone(letter) + _short_zones(letter)
    if not found:
        return None
    share = round(letter.rewritable_share * 100)
    advice = ADVICE if step == FIRST_STEP else FOLLOWUP_ADVICE
    return (
        f"Коридор отличия трудно достижим: {'; '.join(found)}. Совет: {advice} "
        f"(сейчас переписывается {share}%)."
    )


def _canonical(zones: Iterable[Zone]) -> str:
    return "\n\n".join(f"[{zone.name}] {zone.kind.value}\n{zone.text}" for zone in zones)


def step_template(
    *, step: int, language: str, body: str, subject: str | None = None, active: bool = True
) -> StepTemplate:
    """Поля шаблона → проверенный шаблон. Отказ — первой причиной, словами."""
    number = _step(step)
    code = language_code(language)
    title = _subject(number, subject)
    zones = zones_of(_text(body))
    letter = Template(subject=title or "", zones=zones)
    _placeholders(f"{letter.subject}\n{letter.body}")
    guards.assert_no_metrics(f"{letter.subject}\n{letter.body}")
    # Добивка только из `fixed` уходит текстом шаблона — коридора у неё нет.
    if number == FIRST_STEP or letter.of_kind(ZoneKind.REWRITE):
        _corridor(letter)
    return StepTemplate(number, code, title, _canonical(zones), zones, bool(active))


def squashed(text: str) -> str:
    """Текст без различий в пробелах и регистре — для сверки с настройками отправителя."""
    return " ".join(text.split()).casefold()


def unsigned(new: StepTemplate, found: Sender) -> None:
    """Подпись и адрес из настроек отправителя в тексте не повторяются: их допишет сборка,
    и в письме они встали бы дважды."""
    body = squashed(new.body)
    for name, words in (("signature", "подпись"), ("physical_address", "физический адрес")):
        value = found.values.get(name)
        if value and squashed(value) in body:
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
    #: Коридор отличия трудно достижим — словами (`corridor_warning`); `None` — нет.
    warning: str | None = None


def preview(new: StepTemplate, found: Sender) -> Preview:
    """Подставить выдуманные значения. Подписи и адреса в шаблоне нет — они из настроек."""
    values = SAMPLE[new.language]
    rendered = compose.render(filled(new.letter, values["name"]), values)
    return Preview(rendered.subject or None, rendered.zones, values, found, new.warning)
