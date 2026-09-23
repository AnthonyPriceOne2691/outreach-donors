"""Подстановки и сборка письма из зон.

Здесь письмо превращается из шаблона в текст, который увидит человек.
Уникализация живёт отдельно (`rewrite.py`): она работает уже с готовыми
зонами, потому что вступление под контент донора нельзя написать, не зная
имени сайта.

**Незаполненное обязательное значение не молчит.** Подставить пустоту
на место имени отправителя значило бы собрать письмо, которое выглядит
готовым и подписано никем. Вместо этого в текст встаёт громкая метка,
а `missing()` называет недостающее — по нему отправка и отказывает.

Физический адрес и ссылка отписки обязательными были, пока в письме стоял
юридический блок. Юридические требования сняты решением стороны задачи
23.09.2026, блока в шаблонах больше нет. Подстановки остались: шаблон,
который их использует, получит громкую метку так же, как и раньше.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from backend.config import outreach as cfg
from backend.features.letters import unsubscribe
from backend.features.letters.template import (
    PLACEHOLDER_RE,
    Template,
    Zone,
    ZoneKind,
)


class ComposeError(ValueError):
    """Собрать письмо нельзя: не хватает значения для подстановки."""


#: Значения, без которых письмо не отправляется. Домен донора есть всегда,
#: имя отправителя приходит из настроек.
REQUIRED_VALUES = ("sender_name",)

#: Как выглядит незаполненное обязательное значение в предпросмотре.
#: Заглавными и по-русски — чтобы его нельзя было не заметить в письме,
#: написанном строчными и по-английски.
_UNSET = "«{title}»"

_UNSET_TITLES = {
    "sender_name": "ИМЯ ОТПРАВИТЕЛЯ НЕ ЗАДАНО",
    "postal_address": "ФИЗИЧЕСКИЙ АДРЕС НЕ ЗАДАН",
    "unsubscribe_url": "АДРЕС ОТПИСКИ НЕ ЗАДАН",
}


@dataclass(frozen=True, slots=True)
class Rendered:
    """Шаблон с подставленными значениями, ещё без уникализации."""

    subject: str
    zones: tuple[Zone, ...]

    @property
    def body(self) -> str:
        return "\n\n".join(z.text for z in self.zones)

    def rewritable(self) -> tuple[Zone, ...]:
        return tuple(z for z in self.zones if z.kind is ZoneKind.REWRITE)


@dataclass(frozen=True, slots=True)
class Letter:
    """Готовое письмо и то, чем оно отличается от шаблона."""

    subject: str
    body: str
    #: Текст того же письма без уникализации — против него меряется отличие.
    plain_body: str


#: Номер донора для проверки настроек: сама ссылка при этом никуда
#: не ведёт, и это не важно — проверяется, собирается ли она вообще.
_PROBE_DOMAIN_ID = 0


def values_for(*, host: str, domain_id: int | None = None) -> dict[str, str]:
    """Значения подстановок.

    **Ящика здесь нет намеренно.** Письмо подписано именем человека, а
    ящик выбирается в момент отправки из тех, что сегодня ещё могут
    писать. Подставь мы ящик в текст — очередь пришлось бы нарезать
    по отправителям, и вставший ящик блокировал бы свою часть очереди
    вместо того, чтобы отдать работу остальным.

    **Ссылка отписки у каждого донора своя** — в ней подписанная метка,
    по которой страница узнаёт, кого отписывать. Без номера донора
    (предпросмотр шаблона) ссылки нет, и письмо с таким текстом
    отправка не пропустит.
    """
    return {
        "host": host,
        "sender_name": cfg.SENDER_NAME,
        "postal_address": cfg.POSTAL_ADDRESS,
        "unsubscribe_url": unsubscribe.url_for(domain_id),
    }


def missing(values: dict[str, str]) -> list[str]:
    """Обязательные значения, которых нет. Пусто — письмо можно отправлять."""
    return [name for name in REQUIRED_VALUES if not values.get(name, "").strip()]


#: Настройка, из которой берётся каждое обязательное значение. Нужна ровно
#: для сообщений: «не заполнено sender_name» отправляет искать по коду,
#: «не заполнено OUTREACH_SENDER_NAME» — в окружение.
SETTING_NAMES = {
    "sender_name": "OUTREACH_SENDER_NAME",
    "postal_address": "OUTREACH_POSTAL_ADDRESS",
    "unsubscribe_url": "OUTREACH_UNSUBSCRIBE_URL",
}


def missing_settings() -> list[str]:
    """Незаполненные обязательные настройки письма, по именам в окружении.

    Ссылка отписки собирается из двух настроек сразу, и названа будет та,
    которой не хватает: «не заполнено OUTREACH_UNSUBSCRIBE_URL» при живом
    адресе и пустом секрете отправило бы искать не там.
    """
    values = values_for(host="", domain_id=_PROBE_DOMAIN_ID)
    return [
        unsubscribe.missing_setting() if name == "unsubscribe_url" else SETTING_NAMES[name]
        for name in missing(values)
    ]


def _substitute(text: str, values: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            raise ComposeError(
                f"В шаблоне подстановка {{{{{name}}}}}, а значения для неё нет. "
                "Либо опечатка в шаблоне, либо значение надо добавить в values_for()"
            )
        value = values[name].strip()
        if value:
            return value
        return _UNSET.format(title=_UNSET_TITLES.get(name, f"{name.upper()} НЕ ЗАДАНО"))

    return PLACEHOLDER_RE.sub(replace, text)


def unset_in(text: str) -> list[str]:
    """Незаполненные обязательные значения, оставшиеся в готовом письме.

    Проверяется текст, а не настройки: отправляем мы текст. Настройку
    могли заполнить после того, как письмо собрали, — и в письме всё
    равно стоит метка.
    """
    return [title for title in _UNSET_TITLES.values() if _UNSET.format(title=title) in text]


def render(template: Template, values: dict[str, str]) -> Rendered:
    """Подставить значения во все зоны и в тему."""
    return Rendered(
        subject=_substitute(template.subject, values),
        zones=tuple(
            Zone(name=z.name, kind=z.kind, text=_substitute(z.text, values)) for z in template.zones
        ),
    )


def assemble(rendered: Rendered, rewrites: dict[str, str]) -> Letter:
    """Собрать письмо, подменив переписанные зоны.

    Зона, которой в `rewrites` нет, остаётся шаблонной — это законный
    исход: модель могла отказать, и письмо шаблонным текстом лучше,
    чем письмо без абзаца.
    """
    parts = [
        rewrites.get(z.name, z.text).strip() if z.kind is ZoneKind.REWRITE else z.text
        for z in rendered.zones
    ]
    return Letter(
        subject=rendered.subject,
        body="\n\n".join(parts),
        plain_body=rendered.body,
    )
