"""Команда заведения учёток из консоли.

Первый админ заводится только так. Открытая регистрация «пока база
пуста» — это окно, в которое успевает зайти чужой: сервис виден снаружи
с первой минуты, а пустая база живёт до первой команды. Дальше админ
заводит остальных из интерфейса.

Пароль показывается один раз. Хранить его негде: в базе лежит хеш,
а восстановления по почте у нас нет намеренно (docs/SECURITY.md).

Вход с кодом сбрасывается отсюда же (`user-totp-reset`, 23.11.2026): единственный админ, потерявший
телефон и коды восстановления, иначе не вошёл бы вовсе — сбросить его код из интерфейса некому.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Coroutine
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import access, storage
from backend.config.startup_checks import check_storage
from backend.features.access import second_step
from backend.features.access.administration import reset_totp
from backend.features.access.passwords import generate_one_time
from backend.features.access.repository import AccessRepository, EmailTakenError
from backend.features.access.second_step import TotpStateError
from backend.features.core.domain import UserRole

EXIT_OK = 0
EXIT_TAKEN = 3

#: Роли для `user-add --role`: новая роль встаёт в команду сама.
ROLE_NAMES = [role.value for role in UserRole]

#: Срок разового пароля (аудит 20.11.2026) — тем, кто его выдаёт: сброс выдаёт новый и снимает
#: паузу входа после неудач подряд.
_DEADLINE = (
    f"Действует {access.ONE_TIME_TTL_HOURS} ч; не успеет войти — новый выдаст "
    "outreach user-reset --email …"
)


async def cmd_user_add(args: argparse.Namespace) -> int:
    """Завести учётку и показать разовый пароль."""
    check_storage()

    role = UserRole(args.role)
    password = generate_one_time()

    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            repository = AccessRepository(session)
            existing = await repository.count()

            try:
                user = await repository.create(email=args.email, password=password, role=role)
            except EmailTakenError as exc:
                print(f"{exc}. Сбросить пароль существующей: outreach user-reset --email …")
                return EXIT_TAKEN

            await session.commit()
    finally:
        await engine.dispose()

    print(f"Учётка заведена: {user.email}, роль {role.value}")
    print(f"Разовый пароль:  {password}")
    print("\nПароль показан один раз и больше не восстановим — в базе только хеш.")
    print(f"При первом входе система потребует его сменить. {_DEADLINE}")
    if existing == 0:
        print("\nЭто первая учётка в базе. Остальных заводите из интерфейса.")
    return EXIT_OK


async def cmd_user_reset(args: argparse.Namespace) -> int:
    """Выдать новый разовый пароль существующей учётке."""
    check_storage()
    password = generate_one_time()

    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            repository = AccessRepository(session)
            user = await repository.by_email(args.email)
            if user is None:
                print(f"Учётки {args.email} нет. Завести: outreach user-add --email …")
                return EXIT_TAKEN

            await repository.set_password(user.id, password, one_time=True)
            await session.commit()
    finally:
        await engine.dispose()

    print(f"Новый разовый пароль для {user.email}: {password}")
    print(f"Показан один раз. При входе система потребует его сменить. {_DEADLINE}")
    return EXIT_OK


async def cmd_user_totp_reset(args: argparse.Namespace) -> int:
    """Сбросить вход с кодом учётки: секрет и коды восстановления удалены, её пропуска погасли.
    Автора у записи журнала нет — «команда на сервере», как у учётки, заведённой отсюда."""
    check_storage()

    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            user = await AccessRepository(session).by_email(args.email)
            if user is None:
                print(f"Учётки {args.email} нет. Завести: outreach user-add --email …")
                return EXIT_TAKEN
            try:
                await reset_totp(session, user_id=user.id, author=None)
            except TotpStateError as exc:
                print(exc)
                return EXIT_TAKEN
            await session.commit()
    finally:
        await engine.dispose()

    print(f"Вход с кодом у {user.email} сброшен: секрет и коды восстановления удалены,")
    print("открытые сеансы учётки закрыты.")
    if second_step.required(user.role):
        print("Роль требует код — при следующем входе учётка включит его заново.")
    else:
        print("Включить код снова можно самому: «Вход с кодом» в меню у почты.")
    return EXIT_OK


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Команды учёток — `user-add`, `user-reset`, `user-totp-reset` — в разбор `outreach`. Своим
    модулем, как у рекламодателей: разбор команд упёрся в предел длины файла (23.11.2026)."""
    user_add = sub.add_parser("user-add", help="завести учётку и показать разовый пароль")
    user_add.add_argument("--email", required=True, help="почта сотрудника, она же логин")
    user_add.add_argument(
        "--role",
        default="operator",
        choices=ROLE_NAMES,
        help="роль: admin — всё, operator — все этапы, donors — Этап 1, advertisers — Этап 2",
    )

    user_reset = sub.add_parser("user-reset", help="выдать новый разовый пароль")
    user_reset.add_argument("--email", required=True, help="почта сотрудника")

    totp_reset = sub.add_parser(
        "user-totp-reset", help="сбросить вход с кодом: телефон потерян, а другого админа нет"
    )
    totp_reset.add_argument("--email", required=True, help="почта сотрудника")


COMMANDS: dict[str, Callable[[argparse.Namespace], Coroutine[Any, Any, int]]] = {
    "user-add": cmd_user_add,
    "user-reset": cmd_user_reset,
    "user-totp-reset": cmd_user_totp_reset,
}
