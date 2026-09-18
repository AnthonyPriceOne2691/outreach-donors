"""Команда заведения учёток из консоли.

Первый админ заводится только так. Открытая регистрация «пока база
пуста» — это окно, в которое успевает зайти чужой: сервис виден снаружи
с первой минуты, а пустая база живёт до первой команды. Дальше админ
заводит остальных из интерфейса.

Пароль показывается один раз. Хранить его негде: в базе лежит хеш,
а восстановления по почте у нас нет намеренно (docs/SECURITY.md).
"""

from __future__ import annotations

import argparse

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.access.passwords import generate_one_time
from backend.features.access.repository import AccessRepository, EmailTakenError
from backend.features.core.domain import UserRole

EXIT_OK = 0
EXIT_TAKEN = 3


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
    print("При первом входе система потребует его сменить.")
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
    print("Показан один раз. При входе система потребует его сменить.")
    return EXIT_OK
