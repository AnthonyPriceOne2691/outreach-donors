"""Слова событий доступа — что сторож входов и прав говорит в Telegram (`access_watch.py`).

Слово владельца 17.11.2026 об алертах — «адекватнее и информативнее»: когда, что, где смотреть и
есть ли повод. Аудит 20.11.2026 добавил события доступа, и каждое называет:

- **чья учётка, что с ней и кто это сделал.** Запись без автора — «кто — команда на сервере»:
  так заводит учётку `outreach user-add`, и такой админ — повод спросить, у кого доступ к серверу;
- **что добавилось и что снято** — итоговыми правами до записи и после, словами окна прав
  (`PERMISSION_TITLES`); роль — словами экрана учёток (`ROLE_TITLES`). Учётка включена или
  отключена — этими словами, без списка всех прав, которые пришли или ушли вместе с ней;
- **время** — минутой по Москве, как у ленты тревог (`alarm_words.clock`).

**Новые админы — своим сообщением, остальное — одним общим на проход** (проход раз в минуту —
это и есть «по минутам»): пятнадцать переключателей прав подряд — одно сообщение со списком, а не
пятнадцать. Одно событие — своими словами, полнее строки списка.

**Повод для беспокойства есть всегда** (`alarms.marked`, пометка последней строкой): права и
админов меняют только в разделе «Учётки», и сторож не знает, вы ли это были. Пометка говорит,
что сделать, если не вы.

**Вход с кодом** (23.11.2026, слова — `code_event_words.py`): выключение, сброс и вход кодом
восстановления — своим сообщением на пометку, между новыми админами и общим; включение — строкой
общего сообщения, без повода, пока в нём нет правок доступа. Правкой доступа его там не зовут:
заголовок общего сообщения с включением — «…; включён вход с кодом — N».

Пароли и набранное при входе сюда не попадают: событие доступа их не несёт.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from backend.features.access.permissions import PERMISSION_TITLES
from backend.features.core.domain import AuditAction, Permission, UserRole
from backend.features.ops import code_event_words as code_words
from backend.features.ops.access_events import Access, CodeEvent, Event, Happened
from backend.features.ops.alarm_words import clock
from backend.features.ops.alarms import ROOM, Concern, cause

#: Где смотреть события доступа и всплески входов — экран, как его зовёт меню.
SCREEN = "Журнал действий"

#: Строка «где смотреть» — как у ленты тревог.
WHERE = f"Где смотреть: экран «{SCREEN}»."

#: Кто сделал, если записи без автора: учётку завели командой на сервере (`outreach user-add`).
CONSOLE = "команда на сервере"

#: Сколько событий сообщение называет поимённо: дальше — «и ещё M», как у ленты тревог.
LISTED = 20

#: Роли словами — как на экране учёток: копия `ROLE_TITLES` из `frontend/src/api/labels.ts`,
#: сверка построчно — `tests/test_access_watch_events.py` (две копии расходятся молча). Новая
#: роль без слова здесь краснит там же.
ROLE_TITLES: dict[UserRole, str] = {
    UserRole.OPERATOR: "Оператор",
    UserRole.DONORS: "Аутрич",
    UserRole.ADVERTISERS: "Рекламодатели",
    UserRole.ADMIN: "Админ",
}

#: Пометка новых админов: у админа всё — учётки, пароли и рассылка.
ADMIN = cause(
    "у админа все права, включая учётки, пароли и рассылку",
    "если это не вы и не по вашему слову — отключить учётку и сменить пароли админов",
)

#: Пометка смены доступа: права открывают базу контактов и рассылку.
CHANGED = cause(
    "доступ открывает базу контактов и рассылку",
    "если меняли не вы и не по вашему слову — вернуть как было и сменить пароль того, кто менял",
)


@dataclass(frozen=True, slots=True)
class Notice:
    """Сообщение прохода: текст без пометки, пометка и события, о которых оно."""

    text: str
    concern: Concern
    events: tuple[Happened, ...]


def notices(events: Sequence[Happened], at: datetime) -> list[Notice]:
    """Сообщения прохода: новые админы — первым и своим; выключение, сброс и вход кодом
    восстановления — по сообщению на пометку; остальное (правки доступа, включение кода) — одним
    общим, последним."""
    admins, worried, others = _split(events)
    found = []
    if admins:
        found.append(Notice(_admins(admins, at), ADMIN, tuple(admins)))
    for concern, group in worried.items():
        found.append(Notice(_codes(group, at, concern), concern, tuple(group)))
    if others:
        found.append(Notice(_others(others, at), _common(others), tuple(others)))
    return found


def _split(
    events: Sequence[Happened],
) -> tuple[list[Event], dict[Concern, list[CodeEvent]], list[Happened]]:
    """События прохода по сообщениям: новые админы; выключение, сброс и вход кодом
    восстановления — по пометке; остальное — в общее."""
    admins: list[Event] = []
    worried: dict[Concern, list[CodeEvent]] = {}
    others: list[Happened] = []
    for event in events:
        if isinstance(event, CodeEvent) and code_words.alarming(event):
            worried.setdefault(code_words.concern(event), []).append(event)
        elif isinstance(event, Event) and event.raised:
            admins.append(event)
        else:
            others.append(event)
    return admins, worried, others


def what(event: Happened) -> str:
    """Что случилось с доступом учётки или её входом с кодом — без автора и времени, со строчной
    буквы."""
    if isinstance(event, CodeEvent):
        return code_words.what(event)
    if event.created:
        return f"заведена с ролью «{_role(event.details.get('role'))}»"
    if event.before is None or event.after is None:
        return _stated(event.details)
    return _changed(event.before, event.after)


def who(event: Happened) -> str:
    """Кто сделал: почта автора или команда на сервере."""
    return event.author or CONSOLE


def _admins(events: Sequence[Event], at: datetime) -> str:
    if len(events) > 1:
        return _listed(f"Новые админы — {len(events)}", events, at, ADMIN)
    one = events[0]
    title = "Новый админ" if one.created else "Учётка стала админом"
    return _alone(f"{title} — {one.whose}", one, at)


def _others(events: Sequence[Happened], at: datetime) -> str:
    """Общее сообщение: правки доступа и включения кода. Одно событие — своими словами."""
    if len(events) > 1:
        return _listed(_common_title(events), events, at, _common(events))
    one = events[0]
    if isinstance(one, CodeEvent):
        return _alone(f"{code_words.TITLES[one.action]} — {one.whose}", one, at)
    title = "Заведена учётка" if one.created else "Изменён доступ учётки"
    return _alone(f"{title} {one.whose}", one, at)


def _common_title(events: Sequence[Happened]) -> str:
    """Заголовок общего списка: правки доступа числом и, если есть, включения кода — своим
    числом: включение — не правка доступа."""
    access = sum(isinstance(event, Event) for event in events)
    if access == len(events):
        return f"Изменён доступ учёток — {access}"
    enabled = f"{code_words.TITLES[AuditAction.TOTP_ENABLED]} — {len(events) - access}"
    if not access:
        return enabled
    return f"Изменён доступ учёток — {access}; {enabled[:1].lower()}{enabled[1:]}"


def _common(events: Sequence[Happened]) -> Concern:
    """Пометка общего сообщения: есть правка доступа — её повод; одни включения кода — без
    повода (`code_event_words.ENABLED`)."""
    if any(isinstance(event, Event) for event in events):
        return CHANGED
    return code_words.ENABLED


def _codes(events: Sequence[CodeEvent], at: datetime, concern: Concern) -> str:
    """Сообщение событий входа с кодом одной пометки: одно — своими словами, несколько — списком."""
    title = code_words.TITLES[events[0].action]
    if len(events) > 1:
        return _listed(f"{title} — {len(events)}", events, at, concern)
    return _alone(f"{title} — {events[0].whose}", events[0], at)


def _alone(title: str, event: Happened, at: datetime) -> str:
    """Одно событие своими словами: заголовок с минутой, что и кто, что сделать, где смотреть."""
    said = what(event)
    return "\n".join(
        [
            f"тревога: {title}. В {clock(event.at, at)} МСК.",
            f"{said[:1].upper()}{said[1:]}; кто — {who(event)}.",
            *code_words.advice([event]),
            WHERE,
        ]
    )


def _listed(title: str, events: Sequence[Happened], at: datetime, concern: Concern) -> str:
    """События списком в потолке Telegram: не больше `LISTED` строк и сколько влезает рядом с
    пометкой; не названные — «и ещё M». Строка — минута, чья учётка, что и кто."""
    top = f"тревога: {title}. С {clock(min(event.at for event in events), at)} МСК."
    lines = [f"• {clock(e.at, at)} {e.whose} — {what(e)}; кто — {who(e)}" for e in events]
    tail = [*code_words.advice(events), WHERE]
    room = ROOM - len(concern.words) - 1
    shown = len(lines[:LISTED])
    while True:
        rest = len(lines) - shown
        text = "\n".join([top, *lines[:shown], *([f"• и ещё {rest}"] if rest else []), *tail])
        if len(text) <= room or shown == 0:
            return text
        shown -= 1


def _changed(before: Access, after: Access) -> str:
    """Смена доступа против прежнего: роль, включена ли, итоговые права — что добавлено и снято."""
    parts = []
    if before.role is not after.role:
        parts.append(f"роль «{ROLE_TITLES[before.role]}» → «{ROLE_TITLES[after.role]}»")
    if before.active != after.active:
        parts.append("учётка снова включена" if after.active else "учётка отключена")
    else:
        parts += _difference(before.rights, after.rights)
    return "; ".join(parts) or "итоговые права те же"


def _difference(before: frozenset[Permission], after: frozenset[Permission]) -> list[str]:
    """«добавлено: …», «снято: …» — права в порядке окна прав."""
    added = [right for right in Permission if right in after and right not in before]
    removed = [right for right in Permission if right in before and right not in after]
    found = []
    if added:
        found.append(f"добавлено: {_titled(added)}")
    if removed:
        found.append(f"снято: {_titled(removed)}")
    return found


def _stated(details: Mapping[str, Any]) -> str:
    """Правка, прежнего доступа к которой журнал не знает, — тем, что в записи."""
    parts = []
    if details.get("role") is not None:
        parts.append(f"роль — «{_role(details['role'])}»")
    active = details.get("is_active")
    if isinstance(active, bool):
        parts.append("учётка включена" if active else "учётка отключена")
    granted = details.get("permissions")
    if isinstance(granted, dict):
        parts.append(_overrides(granted))
    return "; ".join(parts) or "доступ изменён"


def _overrides(granted: Mapping[str, Any]) -> str:
    """Точечные права записи: выданные поверх роли и снятые с неё."""
    if not granted:
        return "точечных прав нет — как у роли"
    given = [name for name, allowed in granted.items() if allowed]
    taken = [name for name, allowed in granted.items() if not allowed]
    parts = []
    if given:
        parts.append(f"поверх роли выдано: {_titled(given)}")
    if taken:
        parts.append(f"снято с роли: {_titled(taken)}")
    return "; ".join(parts)


def _titled(rights: Iterable[str]) -> str:
    """Права словами окна прав, в кавычках: «отправлять письма», «Продажи»."""
    return ", ".join(f"«{_right(name)}»" for name in rights)


def _right(name: str) -> str:
    """Право словами; чужое имя — как есть: запись журнала старше нынешнего списка прав."""
    return {right.value: title for right, title in PERMISSION_TITLES.items()}.get(name, name)


def _role(raw: object) -> str:
    """Роль словами; чужое значение — как есть."""
    return {role.value: title for role, title in ROLE_TITLES.items()}.get(str(raw), str(raw))
