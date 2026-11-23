"""Вход: проверка пары «почта и пароль» и выдача пропуска.

Три правила, каждое из которых стоило кому-то инцидента.

**Отказ не объясняет, что именно не так.** «Нет такой почты» и «неверный
пароль» — разные сообщения для нас и одно для того, кто подбирает:
первое подтверждает, что учётка есть, и превращает подбор в точечный.

**Отключённая учётка не входит**, даже с верным паролем. Иначе уволенный
сотрудник работает до истечения пропуска.

**Неудачная попытка попадает в журнал.** Иначе подбор пароля невидим:
он выглядит как тишина.

**Время отказа тоже одно** (аудит 10.10.2026). Пароль сверяется и тогда,
когда почты нет, — с подставным хешем той же цены: иначе отказ по
незнакомой почте приходил на проверку bcrypt быстрее, и одинаковый текст
выдавало время ответа.

**В журнал ложится только то, что похоже на почту** (аудит 19.11.2026,
`attempted_email`). Набранное в поле почты пишется в базу при каждой неудаче,
и экран «Журнал» показывает его админам: пароль, набранный не в то поле,
лёг бы туда открытым текстом.

Два отказа говорят своими словами (аудит 20.11.2026), и оба — не подсказка
подбирающему. **Пауза после неудач подряд** (`lockout.py`) называет минуты
до сверки пароля — это выдаёт, что учётка есть, и принято сознательно: за
общим паролем прокси до входа доходят только свои. **Истёкший разовый
пароль** называется только после того, как пара подошла: его видит тот, кто
пароль знает.

**Верная пара — ещё не вход, если у учётки код** (23.11.2026, `second_step.py`): вместо пропуска —
вызов второго шага (`SecondStep`, `challenge.py`): код включён — ввести код; роль требует код, а
он не включён, — включить. Запись `LOGIN`, обнуление счёта неудач и пропуск — только после кода
(`code_login.py`): пароль без кода счёт подбора не обнуляет, иначе подбирающий код с известным
паролем обнулял бы его каждым верным паролем.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache

from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.access import challenge, second_step
from backend.features.access.challenge import Step
from backend.features.access.lockout import AccountPausedError, pause_after, refuse_while_paused
from backend.features.access.passwords import hash_password, one_time_expired, verify_password
from backend.features.access.repository import AccessRepository, normalize_email
from backend.features.access.tokens import create_token
from backend.features.core.domain import AuditAction, UserRole
from backend.features.core.models.access import UserModel

logger = logging.getLogger(__name__)


class LoginFailedError(RuntimeError):
    """Вход не выполнен. Текст один на все причины — намеренно: нет почты, пароль не тот,
    учётка отключена."""


class OneTimeExpiredError(LoginFailedError):
    """Пара подошла, но пароль разовый и его срок вышел (`passwords.one_time_expired`). Отказ
    входа, как и прочие (400, запись журнала), только словами: что делать — просить новый."""


REFUSAL = "Неверная почта или пароль"
EXPIRED = "Разовый пароль истёк — попросите администратора выдать новый"

#: Что ложится в журналы вместо набранного в поле почты, когда оно на почту не похоже.
NOT_AN_EMAIL = "‹не почта›"


def attempted_email(email: str) -> str:
    """Набранная при входе почта — для журнала действий и журнала приложения: как есть, если
    похожа на почту (имя ящика, одна «@», домен с точкой, без пробелов), иначе `NOT_AN_EMAIL`.

    Почта подбора нужна в журнале — ради неё запись и делается. Пароль, набранный не в то
    поле, — нет: разовый пароль (буквы и цифры, `passwords.py`) и пароль из слов через пробел
    почтой не выглядят и остаются заглушкой."""
    typed = normalize_email(email)
    return typed if _looks_like_mail(typed) else NOT_AN_EMAIL


def _looks_like_mail(typed: str) -> bool:
    """Одна «@», имя ящика и домен из двух меток и больше, без пробелов: пароль вида
    `P@ssw0rd` почтой не считается."""
    local, at, domain = typed.rpartition("@")
    labels = domain.split(".")
    shaped = bool(at and local) and "@" not in local and len(labels) > 1 and all(labels)
    return shaped and not any(char.isspace() for char in typed)


@dataclass(frozen=True, slots=True)
class Session:
    """Итог входа: пропуск и то, что нужно показать сразу."""

    token: str
    user_id: int
    email: str
    role: UserRole
    must_change_password: bool


@dataclass(frozen=True, slots=True)
class SecondStep:
    """Пара подошла, но пропуска ещё нет: нужен второй шаг `step` по вызову `challenge`."""

    step: Step
    challenge: str
    user_id: int


@cache
def _stand_in_hash() -> str:
    """Хеш для почты, которой нет: один на процесс, той же цены, что у настоящих
    (`hash_password`), из случайного пароля — совпасть с ним нечему."""
    return hash_password(secrets.token_urlsafe(32))


def _password_matches(password: str, stored: str | None) -> bool:
    """Сверить пароль с хешем учётки, а нет учётки — с подставным: ответ тот же «нет»,
    но за то же время. Сверка с подставным нужна ради её цены, а не ради ответа."""
    if stored is None:
        verify_password(password, _stand_in_hash())
        return False
    return verify_password(password, stored)


def _refuse_while_paused(user: UserModel, email: str, moment: datetime) -> None:
    """Учётка на паузе — отказ до сверки пароля. В журнал действий он не ложится (там — только
    начало паузы), но молчать нельзя: подбор выглядел бы прекратившимся, а он продолжается.
    Строка журнала приложения — как у счётчика в памяти (`api/auth/routes.py`)."""
    try:
        refuse_while_paused(user.locked_until, moment)
    except AccountPausedError:
        logger.warning(
            "вход: учётка на паузе после неудач подряд, пароль даже не проверялся",
            extra={"email": attempted_email(email), "user_id": user.id},
        )
        raise


async def _failed(
    repository: AccessRepository, user: UserModel | None, email: str, moment: datetime
) -> None:
    """Пара не подошла: запись журнала, а у знакомой учётки — ещё одна неудача подряд и, с
    порога, пауза входа с записью о её начале (`lockout.py`). Незнакомая почта строк не заводит."""
    await repository.record(
        AuditAction.LOGIN_FAILED,
        author_id=user.id if user else None,
        details={"email": attempted_email(email), "reason": "пара не подошла"},
    )
    if user is not None:
        await count_toward_pause(repository, user, moment)


async def count_toward_pause(
    repository: AccessRepository, user: UserModel, moment: datetime
) -> None:
    """Ещё одна неудача входа подряд — и, с порога, пауза входа учётки с записью о её начале
    (`lockout.py`). Один счёт на неверный пароль и неверный код (`code_login.py`): подбор кода с
    известным паролем встаёт на ту же паузу."""
    failures = await repository.count_failure(user.id)
    pause = pause_after(failures)
    if pause is None:
        return
    await repository.pause_login(user.id, until=moment + pause)
    await repository.record(
        AuditAction.LOGIN_PAUSED,
        author_id=user.id,
        target=f"user:{user.id}",
        details={
            "email": user.email,
            "неудач подряд": failures,
            "пауза": f"{int(pause.total_seconds()) // 60} мин",
        },
    )


async def _refuse_matched(repository: AccessRepository, user: UserModel, moment: datetime) -> None:
    """Пара подошла, а войти нельзя: учётка отключена — общий отказ; пароль разовый и его
    срок вышел — свой, словами (`OneTimeExpiredError`). Оба — с записью журнала."""
    if not user.is_active:
        await repository.record(
            AuditAction.LOGIN_FAILED,
            author_id=user.id,
            details={"email": user.email, "reason": "учётка отключена"},
        )
        raise LoginFailedError(REFUSAL)

    if user.must_change_password and one_time_expired(user.password_set_at, moment):
        await repository.record(
            AuditAction.LOGIN_FAILED,
            author_id=user.id,
            details={"email": user.email, "reason": "разовый пароль истёк"},
        )
        raise OneTimeExpiredError(EXPIRED)


async def open_session(repository: AccessRepository, user: UserModel, moment: datetime) -> Session:
    """Вход состоялся: минута входа, запись `LOGIN`, счёт неудач с нуля — и пропуск по нынешним
    паролю и поколению пропусков учётки. Один путь для входа паролем и после второго шага."""
    await repository.note_login(user.id, now=moment)
    return Session(
        token=create_token(
            user.id, password_hash=user.password_hash, generation=user.token_generation
        ),
        user_id=user.id,
        email=user.email,
        role=user.role,
        must_change_password=user.must_change_password,
    )


async def login(
    session: AsyncSession, email: str, password: str, *, now: datetime | None = None
) -> Session | SecondStep:
    """Проверить пару и выдать пропуск — или, если у учётки код, вызов второго шага
    (`SecondStep`). Неудача пары — один и тот же отказ; учётка на паузе после неудач подряд —
    отказ со сроком (`lockout.AccountPausedError`), разовый пароль истёк — свой отказ
    (`OneTimeExpiredError`)."""
    moment = now or datetime.now(UTC)
    repository = AccessRepository(session)
    user = await repository.by_email(email)
    if user is not None:
        _refuse_while_paused(user, email, moment)

    # bcrypt — в пуле потоков: четверть секунды счёта в цикле событий держала бы
    # остальные запросы, а с подставным хешем считается и каждая незнакомая почта.
    stored = user.password_hash if user is not None else None
    matched = await asyncio.to_thread(_password_matches, password, stored)
    if user is None or not matched:
        await _failed(repository, user, email, moment)
        raise LoginFailedError(REFUSAL)

    await _refuse_matched(repository, user, moment)
    step = second_step.step_for(user)
    if step is not None:
        return SecondStep(
            step=step, challenge=challenge.issue(user, step, now=moment), user_id=user.id
        )
    return await open_session(repository, user, moment)
