"""Второй шаг входа и вход с кодом своей учётки (23.11.2026, `access/second_step.py`).

Шаги по вызову — без пропуска: пропуска ещё нет, его и выдаёт второй шаг. Вызов приходит из
ответа входа (`routes.sign_in`, 403 `second-step`) и годится только своему шагу.

- `POST /auth/login/code` {вызов, код} → пропуск (`SignedIn`): код из приложения или код
  восстановления;
- `POST /auth/login/enroll` {вызов} → секрет для приложения (`TotpSetup`): роль требует код, а он
  не включён;
- `POST /auth/login/enroll/confirm` {вызов, код} → пропуск и коды восстановления (`TotpEnabled`).

Свой код — с пропуском, после смены разового пароля:

- `POST /auth/totp/setup` {пароль} → секрет для приложения;
- `POST /auth/totp/confirm` {код} → новый пропуск и коды восстановления: прежние пропуска учётки
  погасли, этот — тоже;
- `POST /auth/totp/disable` {пароль, код} → карточка «кто я»; роль требует код — нельзя.

**Неверный код считает тот же счётчик попыток в памяти, что пароль, — своим ключом**
(`code:<учётка>`): подбор кода не закрывает вход паролем с того же адреса, а верный пароль не
обнуляет подбор кода. Второй шаг входа ещё и пишет неудачу в журнал и ставит учётку на ту же
паузу, что неверный пароль (`access/code_login.py`); запись неудачи обязана пережить отказ —
коммит до него.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api import deps
from backend.api.auth.schemas import (
    ChallengeEntry,
    CodeEntry,
    CodeOff,
    CodeOnly,
    Me,
    PasswordEntry,
    SignedIn,
    TotpEnabled,
    TotpSetup,
)
from backend.api.deps import db_session, working_user
from backend.config import access as cfg
from backend.features.access import challenge, code_login
from backend.features.access.attempts import TooManyAttemptsError
from backend.features.access.challenge import Challenge, Step
from backend.features.access.code_login import Setup
from backend.features.access.login import Session
from backend.features.access.repository import actor_of
from backend.features.access.second_step import CodeRefusedError
from backend.features.core.models.access import UserModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["вход"])


def _code_key(user_id: int) -> str:
    """Ключ счётчика попыток кода — свой, не почта и не адрес входа паролем."""
    return f"code:{user_id}"


def _admit_attempt(key: str, user_id: int) -> None:
    """Пустить попытку кода или отказать «слишком много попыток» — как у входа: остановленная
    попытка до сверки не доходит, но в журнале приложения видна."""
    try:
        deps.attempts.check(key)
    except TooManyAttemptsError:
        logger.warning(
            "вход с кодом: попытки исчерпаны, код даже не проверялся", extra={"user_id": user_id}
        )
        raise


async def _signed(session: AsyncSession, opened: Session) -> SignedIn:
    """Пропуск с карточкой — в той же форме, что у входа паролем."""
    user = await session.get(UserModel, opened.user_id)
    assert user is not None  # только что вошли этой учёткой
    return SignedIn(
        token=opened.token, expires_in_hours=cfg.TOKEN_TTL_HOURS, user=Me.of(user, actor_of(user))
    )


def _setup(setup: Setup) -> TotpSetup:
    return TotpSetup(secret=setup.secret, uri=setup.uri, issuer=setup.issuer, account=setup.account)


@router.post("/login/code", response_model=SignedIn, summary="Вход: второй шаг — код")
async def sign_in_with_code(
    body: CodeEntry, session: AsyncSession = Depends(db_session)
) -> SignedIn:
    """Код из приложения или код восстановления по вызову входа → пропуск."""
    ticket = challenge.read(body.challenge, Step.CODE)
    key = _code_key(ticket.user_id)
    _admit_attempt(key, ticket.user_id)
    try:
        opened = await code_login.finish_with_code(session, ticket, body.code)
    except CodeRefusedError:
        deps.attempts.failed(key)
        # Неудача — в журнал и в счёт паузы: без коммита они откатились бы вместе с отказом.
        await session.commit()
        raise
    deps.attempts.succeeded(key)
    await session.commit()
    return await _signed(session, opened)


@router.post("/login/enroll", response_model=TotpSetup, summary="Вход: начать включение кода")
async def start_by_challenge(
    body: ChallengeEntry, session: AsyncSession = Depends(db_session)
) -> TotpSetup:
    """Роль требует код, а он не включён: секрет для приложения по вызову входа."""
    ticket = challenge.read(body.challenge, Step.ENROLL)
    setup = await code_login.start_by_challenge(session, ticket)
    await session.commit()
    return _setup(setup)


async def _confirm_by_challenge(
    session: AsyncSession, ticket: Challenge, code: str
) -> tuple[Session, list[str]]:
    """Первый код по вызову — со счётом неудач, как второй шаг."""
    key = _code_key(ticket.user_id)
    _admit_attempt(key, ticket.user_id)
    try:
        result = await code_login.confirm_by_challenge(session, ticket, code)
    except CodeRefusedError:
        deps.attempts.failed(key)
        await session.commit()
        raise
    deps.attempts.succeeded(key)
    await session.commit()
    return result


@router.post(
    "/login/enroll/confirm",
    response_model=TotpEnabled,
    summary="Вход: включить код первым кодом",
)
async def confirm_by_challenge(
    body: CodeEntry, session: AsyncSession = Depends(db_session)
) -> TotpEnabled:
    """Первый код по начатому секрету → код включён, вход состоялся, коды восстановления — один
    раз."""
    ticket = challenge.read(body.challenge, Step.ENROLL)
    opened, codes = await _confirm_by_challenge(session, ticket, body.code)
    signed = await _signed(session, opened)
    return TotpEnabled(**signed.model_dump(), recovery_codes=codes)


@router.post("/totp/setup", response_model=TotpSetup, summary="Вход с кодом: начать включение")
async def setup(
    body: PasswordEntry,
    user: UserModel = Depends(working_user),
    session: AsyncSession = Depends(db_session),
) -> TotpSetup:
    """Включить код по своей воле — сначала пароль, потом секрет для приложения."""
    await deps.confirm_password(user, body.password, place="вход с кодом")
    started = await code_login.start(session, user)
    await session.commit()
    return _setup(started)


@router.post("/totp/confirm", response_model=TotpEnabled, summary="Вход с кодом: включить")
async def confirm(
    body: CodeOnly,
    user: UserModel = Depends(working_user),
    session: AsyncSession = Depends(db_session),
) -> TotpEnabled:
    """Первый код по начатому секрету → код включён. Пропуска учётки, выданные до этого,
    погасли — в ответе новый, как у смены пароля."""
    key = _code_key(user.id)
    _admit_attempt(key, user.id)
    try:
        enabled = await code_login.confirm_own(session, user, body.code)
    except CodeRefusedError:
        deps.attempts.failed(key)
        raise
    deps.attempts.succeeded(key)
    await session.commit()
    await session.refresh(user)
    return TotpEnabled(
        token=enabled.token,
        expires_in_hours=cfg.TOKEN_TTL_HOURS,
        user=Me.of(user, actor_of(user)),
        recovery_codes=enabled.recovery_codes,
    )


@router.post("/totp/disable", response_model=Me, summary="Вход с кодом: выключить")
async def disable(
    body: CodeOff,
    user: UserModel = Depends(working_user),
    session: AsyncSession = Depends(db_session),
) -> Me:
    """Выключить свой код — паролем и кодом. Роль требует код — отказ словами."""
    code_login.check_can_disable(user)
    await deps.confirm_password(user, body.password, place="вход с кодом")
    key = _code_key(user.id)
    _admit_attempt(key, user.id)
    try:
        await code_login.disable_own(session, user, body.code)
    except CodeRefusedError:
        deps.attempts.failed(key)
        raise
    deps.attempts.succeeded(key)
    await session.commit()
    return Me.of(user, actor_of(user))
