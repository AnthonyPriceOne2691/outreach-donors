"""Отправитель продаж: от чьего имени письмо, чем подписано, куда звать лида.

**Одна строка в базе, а не переменные окружения.** Подпись и адрес правят на экране,
правка видна в журнале с прежним значением, а переменную окружения меняет только
выкатка. Адрес доноров из окружения (`OUTREACH_POSTAL_ADDRESS`) сюда не подставляется:
у продаж свой отправитель, и подставленный чужой адрес выглядел бы заданным.

**Готовность к отправке — одним правилом здесь** (`missing`, `check_ready`): без
физического адреса письмо продаж не уходит — это требование закона о рассылках
(конституция, «Продажи»), без подписи письмо уходит без отправителя. Экран показывает
тот же перечень недостающего, каким откажет отправка (Ф4).

Ссылки проверяются по форме: сайт и созвон — `http(s)://…`, Telegram — `@имя` или
`https://t.me/имя`. Пустое поле — «не задано», а не пустая строка.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction
from backend.features.sales.models import SETTINGS_ROW, SalesSettingsModel

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
)
WHERE = "заполните на экране «Продажи» → «Отправитель»"

_TELEGRAM_NAME = re.compile(r"@[A-Za-z0-9_]{5,32}")
_TELEGRAM_HOSTS = frozenset({"t.me", "telegram.me"})


class SenderSettingsError(ValueError):
    """Поле настроек не годится. Текст — что поправить."""


class SenderNotReadyError(RuntimeError):
    """Отправка продаж невозможна: не хватает настроек. Текст — чего и где заполнить."""


@dataclass(frozen=True, slots=True)
class Sender:
    """Настройки отправителя как есть; пустое — `None`."""

    values: dict[str, str | None] = field(default_factory=lambda: dict.fromkeys(FIELDS))
    updated_by: str | None = None
    updated_at: datetime | None = None

    @property
    def missing(self) -> list[str]:
        """Чего не хватает для отправки — словами отказа, в порядке важности."""
        return [words for name, words in REQUIRED if not self.values.get(name)]


def _of(row: SalesSettingsModel | None) -> Sender:
    if row is None:
        return Sender()
    return Sender({name: getattr(row, name) for name in FIELDS}, row.updated_by, row.updated_at)


async def read(session: AsyncSession) -> Sender:
    """Настройки сейчас. Строки ещё нет — всё «не задано»."""
    return _of(await session.get(SalesSettingsModel, SETTINGS_ROW))


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


def _clean(name: str, raw: str | None) -> str | None:
    """Значение поля: пробелы по краям сняты, пустое — «не задано», форма проверена."""
    label, limit = FIELDS[name]
    value = _shaped(name, raw)
    if not value:
        return None
    if len(value) > limit:
        raise SenderSettingsError(f"{label}: длиннее {limit} знаков ({len(value)})")
    form = _FORMS.get(name)
    return value if form is None else form(name, value)


def cleaned(values: Mapping[str, str | None]) -> dict[str, str | None]:
    """Все поля формы → проверенные значения. Чего нет в форме — «не задано»."""
    unknown = sorted(set(values) - set(FIELDS))
    if unknown:
        raise SenderSettingsError(f"полей {', '.join(unknown)} нет; есть: {', '.join(FIELDS)}")
    return {name: _clean(name, values.get(name)) for name in FIELDS}


async def save(
    session: AsyncSession, values: Mapping[str, str | None], *, author: str, author_id: int | None
) -> Sender:
    """Записать настройки целиком. Без изменений — без журнала. Коммит — за вызывающим."""
    fresh = cleaned(values)
    row = await session.get(SalesSettingsModel, SETTINGS_ROW)
    current = _of(row).values
    was = {name: current[name] for name in FIELDS if current[name] != fresh[name]}
    if not was:
        return _of(row)
    if row is None:  # строку заводит первая правка, а не миграция: в ней нет данных
        row = SalesSettingsModel(id=SETTINGS_ROW)
        session.add(row)
    for name in was:
        setattr(row, name, fresh[name])
    row.updated_by = author
    await session.flush()
    await session.refresh(row)  # время правки ставит база — читаем назад сразу
    await AccessRepository(session).record(
        AuditAction.SALES_KB_CHANGED,
        author_id=author_id,
        target="sales_settings",
        details={"поля": list(was), "было": was},
    )
    logger.info("продажи: отправитель изменён", extra={"fields": list(was)})
    return _of(row)


async def check_ready(session: AsyncSession) -> Sender:
    """Отправка продаж (Ф4) спрашивает здесь: не хватает адреса или подписи — отказ словами."""
    found = await read(session)
    if found.missing:
        raise SenderNotReadyError(
            f"отправка продаж невозможна: {'; '.join(found.missing)} — {WHERE}"
        )
    return found
