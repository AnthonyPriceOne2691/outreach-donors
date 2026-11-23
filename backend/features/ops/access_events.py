"""События доступа из журнала действий: учётка заведена, роль, права или активность сменились;
вход с кодом включён, выключен, сброшен или пройден кодом восстановления.

Аудит 20.11.2026: новый админ и выданные права были видны только на экране «Журнал действий» —
тому, кто туда зашёл. Здесь — чтение этих записей для сторожа входов и прав (`access_watch.py`):
что случилось, с чьей учёткой, кто это сделал и каким был её доступ до записи.

**Какие записи.** Заведение учётки (`USER_CREATED`) и правка доступа — `USER_UPDATED` с целью
`user:N`: её пишет только `AccessRepository.update_access`. «Правка» в журнале общая — письма,
ящики, рассмотрение прогонов пишутся тем же действием с другой целью, и без условия на цель
сторож говорил бы о каждом поправленном письме.

**Доступ до записи — по самому журналу.** Запись правки несёт только новое: роль, активность или
точечные права целиком. Что добавилось, видно лишь против прежнего, а прежнее — сумма записей
учётки от её заведения (роль, включена, без точечных прав) по порядку номеров. Учётки без записи
о заведении журнал не знает: о такой правке говорится тем, что в записи, без «было». Записи
учётки читаются целиком, без окна, — только когда событие есть: событий доступа единицы в неделю.

**Админ — тот, кто заводит учётки и выдаёт права**, как у правил управления учётками
(`access/administration.py`): роль «Админ» или право «заводить учётки» поверх другой роли.
Оператор с этим правом заведёт себе админа сам — значит, он уже админ.

**Что читать — курсор и окно сверки.** Номер записи база даёт при вставке, а видна запись после
фиксации: правка, вставленная раньше чужой записи и зафиксированная позже, легла бы под курсор,
уже ушедший дальше, и пропала бы. Поэтому читается и всё выше курсора, и всё моложе окна сверки
(`LOOKBACK`), а сказанное в окне сторож помнит по номерам и второй раз не говорит
(`access_mark.py`). Пароли и набранное при входе событие не несёт: этих записей здесь нет.

**Вход с кодом — тем же чтением** (23.11.2026). Код включили, выключили, сбросили или вошли кодом
восстановления (`CODE_ACTIONS`, цель та же — «user:N») — сторож говорит и об этом, а читает эти
записи тем же запросом, курсором и окном сверки, что записи о доступе: общее чтение расширено, а не
заведено второе. Второе чтение тем же курсором дало бы два «последних номера» (запись,
зафиксированная между запросами, видна одному и не видна другому), две пачки и два «докуда
прочитано», которые пришлось бы сводить, чтобы событие не пропало и не пришло дважды. Одно чтение
— одна пачка и один `reach`: всё, что держит «ровно один раз» (`access_mark.py`, окно сверки,
перезапуск, отказ Telegram), работает для них без правок. Доступа эти записи не меняют: в проигрыш
прежнего доступа (`_history`, `_replayed`) не идут — его запрос знает только записи о доступе
(`_about_access`), — и становятся своим событием (`CodeEvent`), а не правкой без «было». Кода в
записи нет: только почта учётки и, у входа кодом восстановления, сколько кодов осталось.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.access import recovery_codes
from backend.features.access.permissions import Actor, has_permission
from backend.features.core.domain import AuditAction, Permission, UserRole
from backend.features.core.models.access import AuditLogModel, UserModel

#: Записи о доступе учётки: заведение и правка.
ACCESS_ACTIONS = (AuditAction.USER_CREATED, AuditAction.USER_UPDATED)

#: Записи о входе с кодом учётки (`access/second_step.py`, `code_login.py`, 23.11.2026): включён,
#: выключен владельцем, сброшен админом или командой на сервере, вход кодом восстановления.
CODE_ACTIONS = (
    AuditAction.TOTP_ENABLED,
    AuditAction.TOTP_DISABLED,
    AuditAction.TOTP_RESET,
    AuditAction.RECOVERY_CODE_USED,
)

#: Цель записи о доступе — «user:N» (`AccessRepository.create`, `update_access`).
USER = "user:"

#: Окно сверки: запись моложе этого читается и под курсором. Правка учётки фиксируется за доли
#: секунды, а перезапуск процесса разбора на выкатке — минута-две: десяти минут хватает обоим.
LOOKBACK = timedelta(minutes=10)

#: Событий за проход не больше этого: остальные — следующим проходом, с того же места. Сообщение
#: называет поимённо и так не всех (`access_event_words.LISTED`).
BATCH = 200


@dataclass(frozen=True, slots=True)
class Access:
    """Доступ учётки, каким его знает журнал: роль, включена ли и точечные права поверх роли."""

    role: UserRole
    active: bool = True
    overrides: Mapping[str, Any] | None = None

    @property
    def rights(self) -> frozenset[Permission]:
        """Итоговые права — той же проверкой, что пускает в разделы (`has_permission`)."""
        actor = Actor(
            user_id=0,
            role=self.role,
            is_active=self.active,
            overrides=dict(self.overrides) if self.overrides else None,
        )
        return frozenset(right for right in Permission if has_permission(actor, right))

    @property
    def admin(self) -> bool:
        """Админ: включён и с ролью «Админ» или с правом «заводить учётки»."""
        return self.active and (self.role is UserRole.ADMIN or Permission.USERS in self.rights)


@dataclass(frozen=True, slots=True)
class Event:
    """Запись журнала о доступе учётки — для слов сторожа."""

    id: int
    at: datetime
    #: Учётка заведена (а не правлена).
    created: bool
    #: Чья учётка — почта.
    whose: str
    #: Кто сделал — почта; `None` — запись без автора: учётку завели командой на сервере.
    author: str | None
    details: Mapping[str, Any]
    #: Доступ до записи и после; `None` — журнал его не знает (нет записи о заведении).
    before: Access | None
    after: Access | None

    @property
    def raised(self) -> bool:
        """Учётка стала админом этой записью: заведена админом, получила роль «Админ» или право
        «заводить учётки», включена снова. Прежнего доступа журнал не знает — по самой записи."""
        if self.after is not None:
            return self.after.admin and not (self.before is not None and self.before.admin)
        granted = self.details.get("permissions")
        return self.details.get("role") == UserRole.ADMIN.value or (
            isinstance(granted, dict) and granted.get(Permission.USERS.value) is True
        )


@dataclass(frozen=True, slots=True)
class CodeEvent:
    """Запись журнала о входе с кодом учётки — для слов сторожа. Доступа она не меняет: прежнего
    и нового доступа у неё нет, и в проигрыш записей о доступе она не идёт."""

    id: int
    at: datetime
    #: Что с кодом: включён, выключен, сброшен или вход кодом восстановления (`CODE_ACTIONS`).
    action: AuditAction
    #: Чья учётка — почта.
    whose: str
    #: Кто сделал — почта; `None` — запись без автора: код сбросили командой на сервере.
    author: str | None
    #: Сколько кодов восстановления осталось — у входа ими; у прочих и у записи без счёта — `None`.
    left: int | None = None


#: Что случилось с учёткой: доступ или вход с кодом — по порядку номеров в одной пачке.
Happened = Event | CodeEvent


@dataclass(frozen=True, slots=True)
class Batch:
    """Что прочитал проход: события и номер, до которого прочитано всё (`None` — журнал пуст)."""

    events: list[Happened]
    reach: int | None


async def read(
    session: AsyncSession, *, cursor: int | None, told: Collection[int], now: datetime
) -> Batch:
    """События доступа и входа с кодом выше курсора и моложе окна сверки, кроме сказанных
    (`told`), по номерам.

    Курсора нет (первый запуск, новый канал) или он дальше последней записи (база восстановлена
    из копии) — читается с последней записи: историю целиком сторож не пересказывает, а о
    последних минутах скажет окно сверки."""
    top = await session.scalar(select(func.max(AuditLogModel.id)))
    if top is None:
        return Batch([], None)
    floor = top if cursor is None or cursor > top else cursor
    found = await session.scalars(
        select(AuditLogModel)
        .where(
            *_watched(),
            AuditLogModel.id <= top,
            or_(AuditLogModel.id > floor, AuditLogModel.created_at >= now - LOOKBACK),
        )
        .order_by(AuditLogModel.id)
        .limit(BATCH + len(told) + 1)
    )
    fresh = [row for row in found.all() if row.id not in told]
    taken = fresh[:BATCH]
    # Сверх пачки — дальше с последнего взятого, но не ниже курсора: остаток под ним дочитает окно.
    reach = top if len(fresh) <= BATCH else max(floor, taken[-1].id)
    return Batch(await _events(session, taken), reach)


def _about_access() -> list[ColumnElement[bool]]:
    """Запись о доступе учётки: заведение или правка с целью «user:N»."""
    return [AuditLogModel.action.in_(ACCESS_ACTIONS), AuditLogModel.target.startswith(USER)]


def _watched() -> list[ColumnElement[bool]]:
    """Запись, о которой говорит сторож: о доступе учётки или о её входе с кодом, цель «user:N»."""
    actions = (*ACCESS_ACTIONS, *CODE_ACTIONS)
    return [AuditLogModel.action.in_(actions), AuditLogModel.target.startswith(USER)]


async def _events(session: AsyncSession, rows: Sequence[AuditLogModel]) -> list[Happened]:
    """События по записям: почты учёток и авторов; у записей о доступе — доступ до и после."""
    if not rows:
        return []
    access = [row for row in rows if row.action in ACCESS_ACTIONS]
    before = await _before(session, access)
    targets = {row.target for row in rows if row.target is not None}
    people = {*_numbers(targets), *(row.user_id for row in rows if row.user_id is not None)}
    emails = await _emails(session, people)
    return [
        _event(row, before.get(row.id), emails)
        if row.action in ACCESS_ACTIONS
        else _code_event(row, emails)
        for row in rows
    ]


async def _before(session: AsyncSession, rows: Sequence[AuditLogModel]) -> dict[int, Access | None]:
    """Доступ учётки до каждой записи о доступе — проигрышем её записей о доступе от заведения."""
    if not rows:
        return {}
    targets = sorted({row.target for row in rows if row.target is not None})
    return _replayed(await _history(session, targets, rows[-1].id))


async def _history(
    session: AsyncSession, targets: Sequence[str], last: int
) -> Sequence[tuple[int, str, AuditAction, dict[str, Any] | None]]:
    """Записи о доступе учёток `targets` до номера `last` включительно — по порядку номеров."""
    found = await session.execute(
        select(AuditLogModel.id, AuditLogModel.target, AuditLogModel.action, AuditLogModel.details)
        .where(*_about_access(), AuditLogModel.target.in_(targets), AuditLogModel.id <= last)
        .order_by(AuditLogModel.id)
    )
    return [(number, target or "", action, details) for number, target, action, details in found]


def _replayed(
    history: Iterable[tuple[int, str, AuditAction, dict[str, Any] | None]],
) -> dict[int, Access | None]:
    """Доступ учётки до каждой записи: сумма её прежних записей от заведения."""
    state: dict[str, Access | None] = {}
    before: dict[int, Access | None] = {}
    for number, target, action, details in history:
        before[number] = state.get(target)
        state[target] = applied(before[number], action, details or {})
    return before


def applied(was: Access | None, action: AuditAction, details: Mapping[str, Any]) -> Access | None:
    """Доступ после записи. Заведение — роль, включена, без точечных прав; правка — поверх
    прежнего то, что в записи. Прежнего не знаем — не знаем и нового."""
    if action is AuditAction.USER_CREATED:
        role = _role(details.get("role"))
        return None if role is None else Access(role)
    if was is None:
        return None
    active, overrides = details.get("is_active"), details.get("permissions")
    return Access(
        role=_role(details.get("role")) or was.role,
        active=active if isinstance(active, bool) else was.active,
        overrides=overrides if isinstance(overrides, dict) else was.overrides,
    )


def _role(raw: object) -> UserRole | None:
    """Роль из подробностей записи; нет её или чужое значение — `None`."""
    return {role.value: role for role in UserRole}.get(str(raw)) if raw is not None else None


def _numbers(targets: Iterable[str]) -> set[int]:
    """Номера учёток из целей «user:N»."""
    return {int(number) for number in (t.removeprefix(USER) for t in targets) if number.isdigit()}


async def _emails(session: AsyncSession, people: Collection[int]) -> dict[int, str]:
    """Почты учёток по номерам."""
    if not people:
        return {}
    found = await session.execute(
        select(UserModel.id, UserModel.email).where(UserModel.id.in_(sorted(people)))
    )
    return dict(found.tuples().all())


def _event(row: AuditLogModel, before: Access | None, emails: Mapping[int, str]) -> Event:
    details = row.details or {}
    return Event(
        id=row.id,
        at=row.created_at,
        created=row.action is AuditAction.USER_CREATED,
        whose=_whose(row.target or "", details, emails),
        author=_author(row.user_id, emails),
        details=details,
        before=before,
        after=applied(before, row.action, details),
    )


def _code_event(row: AuditLogModel, emails: Mapping[int, str]) -> CodeEvent:
    """Событие входа с кодом: чья учётка, кто сделал и — у входа кодом восстановления — сколько
    кодов осталось."""
    details = row.details or {}
    left = details.get(recovery_codes.LEFT)
    return CodeEvent(
        id=row.id,
        at=row.created_at,
        action=row.action,
        whose=_whose(row.target or "", details, emails),
        author=_author(row.user_id, emails),
        left=left if isinstance(left, int) and not isinstance(left, bool) else None,
    )


def _author(user_id: int | None, emails: Mapping[int, str]) -> str | None:
    """Кто сделал: почта автора, его номер — или `None`, запись без автора (команда на сервере)."""
    return None if user_id is None else emails.get(user_id, f"учётка №{user_id}")


def _whose(target: str, details: Mapping[str, Any], emails: Mapping[int, str]) -> str:
    """Чья учётка: почта из учёток, из записи о заведении — или номер."""
    number = target.removeprefix(USER)
    found = emails.get(int(number)) if number.isdigit() else None
    return found or str(details.get("email") or f"учётка №{number}")
