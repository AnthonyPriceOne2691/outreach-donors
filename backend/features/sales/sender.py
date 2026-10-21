"""Отправитель продаж: от чьего имени письмо, чем подписано, куда звать лида.

**Одна строка в базе, а не переменные окружения.** Подпись и адрес правят на экране,
правка видна в журнале с прежним значением, а переменную окружения меняет только
выкатка. Адрес доноров из окружения (`OUTREACH_POSTAL_ADDRESS`) сюда не подставляется:
у продаж свой отправитель, и подставленный чужой адрес выглядел бы заданным.

**Готовность к отправке — одним правилом здесь** (`missing`, `check_ready`): без
физического адреса письмо продаж не уходит — это требование закона о рассылках
(конституция, «Продажи»), без подписи письмо уходит без отправителя, без имени в From
стоит голый адрес (конституция: «в From — настоящее имя»). Экран показывает тот же
перечень недостающего, каким откажет отправка (Ф4).

**Набор на язык письма** (находка стенда 21.10.2026: английское письмо уходило с русскими
подписью, адресом и именем в From). Имя, должность, подпись и физический адрес
(`LANGUAGE_FIELDS`) задаются ещё и на каждом языке цепочек (`chain_text.LANGUAGES`) — данными, а
не правилом под язык; пустое поле набора — общее поле. Сайт, Telegram и ссылка на созвон — общие.
Набор выбирает язык письма — везде одним правилом (`Sender.in_language`): язык цепочки диалога
(`sales_threads.language`, `of_thread`), а до диалога — язык лида или шаблона, основой кода
(`en-US` → `en`). Готовность продаж — по общему набору, как раньше: набор языка необязателен и
её не меняет (`missing`).

Ссылки проверяются по форме: сайт и созвон — `http(s)://…`, Telegram — `@имя` или
`https://t.me/имя`. Пустое поле — «не задано», а не пустая строка.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction
from backend.features.sales.chain_text import LANGUAGES, UNFILLED, in_language
from backend.features.sales.lead_values import language_base
from backend.features.sales.models import SETTINGS_ROW, SalesSettingsModel, SalesThreadModel

logger = logging.getLogger(__name__)

#: Поле → подпись словами и предел длины. Порядок — порядок формы экрана.
FIELDS: dict[str, tuple[str, int]] = {
    "sender_name": ("имя отправителя", 128),
    "sender_position": ("должность", 128),
    "signature": ("подпись", 1000),
    "website": ("сайт", 255),
    "telegram": ("Telegram для лидов", 255),
    "physical_address": ("физический адрес", 500),
    "call_link": ("ссылка на созвон", 512),
}
#: Поля в несколько строк: подпись и адрес пишут столбиком.
MULTILINE = frozenset({"signature", "physical_address"})
#: Без чего отправка продаж отказывает: поле и слова отказа.
REQUIRED = (
    ("physical_address", "не задан физический адрес"),
    ("signature", "не задана подпись"),
    ("sender_name", "не задано имя отправителя"),
)
WHERE = "заполните на экране «Продажи» → «Отправитель»"
#: Почему подстановка в поле «Отправителя» — отказ: её никто не заменит, и письмо не уйдёт.
UNFILLED_WORDS = (
    "подстановки здесь не работают: поле уходит в письмо как есть, напишите текст целиком"
)
#: Поля, которые зависят от языка письма: имя (в From и подписи), должность, подпись, физический
#: адрес. Пределы и многострочность — общих полей (`FIELDS`, `MULTILINE`).
LANGUAGE_FIELDS = ("sender_name", "sender_position", "signature", "physical_address")
#: Наборы языков в теле записи (`save`): `{"en": {"signature": …}}`. Нет ключа — наборы как были:
#: консоль и прежний экран пишут только общие поля.
SETS = "languages"

_TELEGRAM_NAME = re.compile(r"@[A-Za-z0-9_]{5,32}")
_TELEGRAM_HOSTS = frozenset({"t.me", "telegram.me"})

#: Набор языка: поле → значение; `None` — «в письмах на этом языке общее поле».
LanguageSet = dict[str, str | None]


class SenderSettingsError(ValueError):
    """Поле настроек не годится. Текст — что поправить."""


class SenderNotReadyError(RuntimeError):
    """Отправка продаж невозможна: не хватает настроек. Текст — чего и где заполнить."""


def _no_sets() -> dict[str, LanguageSet]:
    return {code: dict.fromkeys(LANGUAGE_FIELDS) for code in LANGUAGES}


@dataclass(frozen=True, slots=True)
class Sender:
    """Настройки отправителя как есть; пустое — `None`. `values` — что уходит в письмо: общий
    набор, а у отправителя письма на языке (`in_language`) — поля набора языка поверх общих."""

    values: dict[str, str | None] = field(default_factory=lambda: dict.fromkeys(FIELDS))
    updated_by: str | None = None
    updated_at: datetime | None = None
    #: Наборы языков цепочек как записаны — каждый язык, каждое поле набора.
    languages: dict[str, LanguageSet] = field(default_factory=_no_sets)
    #: Общий набор, когда `values` — набор языка; `None` — `values` и есть общий.
    common: dict[str, str | None] | None = None

    @property
    def missing(self) -> list[str]:
        """Чего не хватает для отправки — словами отказа, в порядке важности. По общему
        набору и у отправителя письма на языке: набор языка готовность не меняет."""
        shared = self.values if self.common is None else self.common
        return [words for name, words in REQUIRED if not shared.get(name)]

    def in_language(self, language: str | None) -> Sender:
        """Отправитель письма на языке `language` (код письма или лида, основой: `en-US` → `en`):
        поле набора этого языка, а пустое — общее. Набора нет (язык без цепочки, не задан) —
        общий набор."""
        shared = self.values if self.common is None else self.common
        own = self.languages.get(language_base(language), {})
        return replace(
            self,
            values={name: own.get(name) or value for name, value in shared.items()},
            common=shared,
        )


def set_title(code: str) -> str:
    """Набор языка словами — заголовок блока экрана и начало отказа: «Письма на английском»."""
    return f"Письма {in_language(code)}"


def _sets_of(stored: Mapping[str, Mapping[str, str]] | None) -> dict[str, LanguageSet]:
    """Наборы из колонки: каждый язык цепочек, каждое поле набора; чего нет — «не задано».
    Набор языка, которого у цепочек больше нет, писем не подписывает — писем на нём не пишут —
    и не читается; в колонке он остаётся (`_stored`)."""
    given = stored or {}
    return {
        code: {name: given.get(code, {}).get(name) or None for name in LANGUAGE_FIELDS}
        for code in LANGUAGES
    }


def _of(row: SalesSettingsModel | None) -> Sender:
    if row is None:
        return Sender()
    values = {name: getattr(row, name) for name in FIELDS}
    return Sender(values, row.updated_by, row.updated_at, _sets_of(row.languages))


async def read(session: AsyncSession) -> Sender:
    """Настройки сейчас. Строки ещё нет — всё «не задано»."""
    return _of(await session.get(SalesSettingsModel, SETTINGS_ROW))


async def of_thread(session: AsyncSession, thread_id: int | None) -> Sender:
    """Отправитель писем переписки продаж — на языке её цепочки (`sales_threads.language`): им
    подписаны её письма и добивки. Переписка не продаж (или её нет) — общий набор."""
    language = None
    if thread_id is not None:
        language = await session.scalar(
            select(SalesThreadModel.language).where(SalesThreadModel.thread_id == thread_id)
        )
    return (await read(session)).in_language(language)


def _link(name: str, value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme in ("http", "https") and parts.netloc and not any(c.isspace() for c in value):
        return value
    raise SenderSettingsError(f"{FIELDS[name][0]}: «{value}» — не ссылка, ждём https://…")


def _telegram(name: str, value: str) -> str:
    if _TELEGRAM_NAME.fullmatch(value):
        return value
    parts = urlsplit(value)
    if parts.scheme == "https" and parts.netloc in _TELEGRAM_HOSTS and parts.path.strip("/"):
        return value
    raise SenderSettingsError(
        f"{FIELDS[name][0]}: «{value}» — ждём @имя или ссылку https://t.me/имя"
    )


#: Поля со своей формой: ссылка или Telegram. Остальные — текст как есть.
_FORMS: dict[str, Callable[[str, str], str]] = {
    "website": _link,
    "call_link": _link,
    "telegram": _telegram,
}


def _shaped(name: str, raw: str | None) -> str:
    """Подпись и адрес — столбиком без хвостовых пробелов; остальное — одной строкой."""
    if name not in MULTILINE:
        return " ".join((raw or "").split())
    lines = (raw or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).strip()


def _quoted(found: str) -> str:
    """Подстановка в кавычках: громкая метка («… НЕ ЗАДАНО») уже в них."""
    return found if found.startswith("«") else f"«{found}»"


def _clean(name: str, raw: str | None) -> str | None:
    """Значение поля: пробелы по краям сняты, пустое — «не задано», подстановок нет (поле уходит в
    письмо как есть, `chain_text.UNFILLED`), форма проверена. Отказ — только при записи: строка,
    записанная раньше правила, читается как есть (`_of`), а экран показывает, что в ней
    поправить."""
    label, limit = FIELDS[name]
    value = _shaped(name, raw)
    if not value:
        return None
    if len(value) > limit:
        raise SenderSettingsError(f"{label}: длиннее {limit} знаков ({len(value)})")
    if (left := UNFILLED.search(value)) is not None:
        raise SenderSettingsError(f"{label}: {_quoted(left.group(0))} — {UNFILLED_WORDS}")
    form = _FORMS.get(name)
    return value if form is None else form(name, value)


def cleaned(values: Mapping[str, str | None]) -> dict[str, str | None]:
    """Все поля формы → проверенные значения. Чего нет в форме — «не задано»."""
    unknown = sorted(set(values) - set(FIELDS))
    if unknown:
        raise SenderSettingsError(f"полей {', '.join(unknown)} нет; есть: {', '.join(FIELDS)}")
    return {name: _clean(name, values.get(name)) for name in FIELDS}


def _cleaned_set(code: str, given: Mapping[str, str | None]) -> LanguageSet:
    """Набор языка → проверенные значения тем же правилом, что общие поля (`_clean`); отказ
    называет набор: «Письма на английском — подпись: длиннее 1000 знаков (1001)»."""
    title = set_title(code)
    unknown = sorted(set(given) - set(LANGUAGE_FIELDS))
    if unknown:
        known = ", ".join(LANGUAGE_FIELDS)
        raise SenderSettingsError(f"{title}: полей {', '.join(unknown)} нет; есть: {known}")
    try:
        return {name: _clean(name, given.get(name)) for name in LANGUAGE_FIELDS}
    except SenderSettingsError as exc:
        raise SenderSettingsError(f"{title} — {exc}") from exc


def cleaned_sets(sets: Mapping[str, Mapping[str, str | None] | None]) -> dict[str, LanguageSet]:
    """Наборы языков → проверенные значения: каждый язык цепочек; чего нет в теле — «не задано».
    Язык без цепочки — отказ словами: письма на нём не пишутся, и набор подписывать нечего."""
    unknown = sorted(set(sets) - set(LANGUAGES))
    if unknown:
        raise SenderSettingsError(
            f"наборов на языках {', '.join(unknown)} нет: письма пишутся на {', '.join(LANGUAGES)}"
        )
    return {code: _cleaned_set(code, sets.get(code) or {}) for code in LANGUAGES}


def _stored(
    sets: Mapping[str, LanguageSet], before: Mapping[str, Mapping[str, str]] | None
) -> dict[str, dict[str, str]] | None:
    """Наборы для колонки: только заданные поля и языки; ничего не задано — `None`. Набор языка,
    которого у цепочек больше нет, остаётся как был: экран его не показывает, и правка других
    полей не стирает его молча."""
    kept = {code: dict(own) for code, own in (before or {}).items() if code not in LANGUAGES}
    for code, own in sets.items():
        if filled := {name: value for name, value in own.items() if value}:
            kept[code] = filled
    return kept or None


def _changed(
    before: Mapping[str, str | None], after: Mapping[str, str | None]
) -> dict[str, str | None]:
    """Правленые общие поля с прежними значениями."""
    return {name: before[name] for name in FIELDS if before[name] != after[name]}


def _changed_sets(
    before: Mapping[str, LanguageSet], after: Mapping[str, LanguageSet]
) -> dict[str, str | None]:
    """Правленые поля наборов с прежними значениями — ключом «язык.поле» (`en.signature`):
    журнал называет, какой набор правили."""
    return {
        f"{code}.{name}": before[code][name]
        for code in LANGUAGES
        for name in LANGUAGE_FIELDS
        if before[code][name] != after[code][name]
    }


async def save(
    session: AsyncSession, values: Mapping[str, Any], *, author: str, author_id: int | None
) -> Sender:
    """Записать настройки целиком: общие поля и наборы языков (`SETS`; нет ключа — наборы как
    были). Без изменений — без журнала. Коммит — за вызывающим."""
    given = dict(values)
    raw_sets = given.pop(SETS, None)
    fresh = cleaned(given)
    row = await session.get(SalesSettingsModel, SETTINGS_ROW)
    current = _of(row)
    sets = current.languages if raw_sets is None else cleaned_sets(raw_sets)
    was = _changed(current.values, fresh)
    was_sets = _changed_sets(current.languages, sets)
    if not was and not was_sets:
        return current
    if row is None:  # строку заводит первая правка, а не миграция: в ней нет данных
        row = SalesSettingsModel(id=SETTINGS_ROW)
        session.add(row)
    for name in was:
        setattr(row, name, fresh[name])
    if was_sets:
        row.languages = _stored(sets, row.languages)
    row.updated_by = author
    await session.flush()
    await session.refresh(row)  # время правки ставит база — читаем назад сразу
    changed = was | was_sets
    await AccessRepository(session).record(
        AuditAction.SALES_KB_CHANGED,
        author_id=author_id,
        target="sales_settings",
        details={"поля": list(changed), "было": changed},
    )
    logger.info("продажи: отправитель изменён", extra={"fields": list(changed)})
    return _of(row)


async def check_ready(session: AsyncSession) -> Sender:
    """Отправка продаж (Ф4) спрашивает здесь: нет адреса, подписи или имени — отказ словами."""
    found = await read(session)
    if found.missing:
        raise SenderNotReadyError(
            f"отправка продаж невозможна: {'; '.join(found.missing)} — {WHERE}"
        )
    return found
