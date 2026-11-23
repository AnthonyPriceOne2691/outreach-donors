"""Вход, «кто я», смена своего пароля и выход.

Четыре маршрута, и все четыре доступны без прав: правами меряется работа,
а это — сам доступ к ней.

**«Кто я», смена пароля и выход работают с разовым паролем за спиной.**
Остальные маршруты — нет: иначе требование сменить выданный пароль
держится на вежливости интерфейса.

**У учётки с кодом верная пара — ещё не вход** (23.11.2026): ответ 403 с признаком
`X-Access-Refusal: second-step` и телом `SecondStep` — шаг и вызов к нему; пропуск выдаёт второй
шаг (`code_routes.py`). Отказом, а не 200: экран и скрипт, которые шагов не знают, покажут его
слова как отказ входа и не примут ответ без пропуска за вход.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api import deps
from backend.api.auth.schemas import Credentials, Me, PasswordChange, SecondStep, SignedIn
from backend.api.deps import SignedPass, db_session, signed_in, signed_pass
from backend.api.errors import ACCESS_REFUSAL
from backend.config import access as cfg
from backend.features.access import challenge, sessions
from backend.features.access.administration import WrongPasswordError, change_own_password
from backend.features.access.attempts import TooManyAttemptsError
from backend.features.access.challenge import Step
from backend.features.access.login import LoginFailedError, attempted_email, login
from backend.features.access.login import SecondStep as Pending
from backend.features.access.repository import actor_of, normalize_email
from backend.features.core.models.access import UserModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["вход"])


def _keys(request: Request, email: str) -> tuple[str, str]:
    """Ключи счётчика попыток: почта и адрес обращения.

    Адрес берётся из соединения. За обратным прокси это его адрес,
    поэтому на сервере uvicorn запускается с `--proxy-headers`, а nginx
    передаёт настоящий — иначе весь интернет для счётчика выглядит одним
    клиентом, и первый же перебор закрывает вход всем сразу.
    """
    address = request.client.host if request.client else "адрес неизвестен"
    return f"email:{normalize_email(email)}", f"addr:{address}"


#: Признак ответа «пара подошла, нужен второй шаг» (`X-Access-Refusal`).
SECOND_STEP = "second-step"

#: Что сказать о шаге тому, кто шагов не знает: старый экран и скрипт покажут это отказом входа.
STEP_WORDS: dict[Step, str] = {
    Step.CODE: (
        "Пароль подошёл — нужен код из приложения-аутентификатора (второй шаг входа). "
        "Нет поля для кода — обновите страницу"
    ),
    Step.ENROLL: (
        "Пароль подошёл — для вашей роли вход с кодом обязателен: включите его (второй шаг "
        "входа). Нет экрана включения — обновите страницу"
    ),
}


def _second_step(pending: Pending) -> JSONResponse:
    """Ответ «нужен второй шаг»: 403 с признаком и вызовом — без пропуска."""
    minutes = int(challenge.TTL.total_seconds()) // 60
    body = SecondStep(
        detail=STEP_WORDS[pending.step],
        step=pending.step,
        challenge=pending.challenge,
        expires_in_minutes=minutes,
    )
    return JSONResponse(
        body.model_dump(mode="json"),
        status_code=status.HTTP_403_FORBIDDEN,
        headers={ACCESS_REFUSAL: SECOND_STEP},
    )


@router.post(
    "/login",
    response_model=SignedIn,
    summary="Вход",
    responses={
        status.HTTP_403_FORBIDDEN: {
            "model": SecondStep,
            "description": "Пара подошла, нужен второй шаг: код или включение кода",
        }
    },
)
async def sign_in(
    body: Credentials,
    request: Request,
    session: AsyncSession = Depends(db_session),
) -> SignedIn | JSONResponse:
    """Проверить пару и выдать пропуск — или, если у учётки код, вызов второго шага."""
    keys = _keys(request, body.email)
    # Счётчик берётся через модуль, а не по имени: так его можно
    # подменить целиком — в тестах и при переезде в общее хранилище.
    try:
        deps.attempts.check(*keys)
    except TooManyAttemptsError:
        # Остановленная попытка до базы не доходит и в журнал не пишется —
        # иначе перебор писал бы в базу столько строк, сколько запросов.
        # Но и молчать нельзя: в журнале подбор выглядел бы как десять
        # попыток и тишина, то есть как прекратившийся. Живой прогон
        # 19.09.2026 это и показал — десять записей, а попыток было
        # двенадцать. Почта — только похожая на почту, как в журнале действий
        # (`attempted_email`): пароль, набранный не в то поле, в журнал не ложится.
        logger.warning(
            "вход: попытки исчерпаны, пароль даже не проверялся",
            extra={"email": attempted_email(body.email), "attempt_key": keys[1]},
        )
        raise

    try:
        opened = await login(session, body.email, body.password)
    except LoginFailedError:
        deps.attempts.failed(*keys)
        # Запись о неудачной попытке обязана пережить отказ: без коммита
        # она откатывается вместе с ним, и подбор пароля выглядит как
        # тишина — ровно то, ради чего эта запись и делается.
        await session.commit()
        raise

    deps.attempts.succeeded(keys[0])
    await session.commit()
    if isinstance(opened, Pending):
        return _second_step(opened)

    user = await session.get(UserModel, opened.user_id)
    assert user is not None  # только что вошли этой учёткой
    return SignedIn(
        token=opened.token,
        expires_in_hours=cfg.TOKEN_TTL_HOURS,
        user=Me.of(user, actor_of(user)),
    )


@router.get("/me", response_model=Me, summary="Кто я и что мне можно")
async def me(user: UserModel = Depends(signed_in)) -> Me:
    return Me.of(user, actor_of(user))


@router.post("/password", response_model=SignedIn, summary="Сменить свой пароль")
async def change_password(
    body: PasswordChange,
    user: UserModel = Depends(signed_in),
    session: AsyncSession = Depends(db_session),
) -> SignedIn:
    """Сменить свой пароль. Неверный старый считает тот же счётчик, что у входа, — по
    учётке (аудит 10.10.2026): украденный суточный пропуск иначе подбирал старый пароль
    без предела. Ключ свой: подбор здесь не закрывает вход, а вход не сбрасывает подбор.

    Ответ — новый пропуск с карточкой, как у входа: пропуска, выданные до смены, больше
    не действуют (проверка прода 10.10.2026), и этот сеанс без нового вышел бы с ними."""
    key = f"password:{user.id}"
    try:
        deps.attempts.check(key)
    except TooManyAttemptsError:
        # Как у входа: остановленная попытка до проверки не доходит, но в журнале видна.
        logger.warning(
            "смена пароля: попытки исчерпаны, старый пароль даже не проверялся",
            extra={"user_id": user.id},
        )
        raise
    try:
        token = await change_own_password(
            session, user_id=user.id, current=body.current, new=body.new
        )
    except WrongPasswordError:
        deps.attempts.failed(key)
        raise
    deps.attempts.succeeded(key)
    await session.commit()
    await session.refresh(user)
    return SignedIn(
        token=token, expires_in_hours=cfg.TOKEN_TTL_HOURS, user=Me.of(user, actor_of(user))
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Выйти: пропуск закрывается и на сервере",
)
async def sign_out(signed: SignedPass = Depends(signed_pass)) -> Response:
    """Выход (аудит 20.11.2026, №7): этот пропуск закрыт до конца своего срока, и его копия —
    из журнала, со снимка экрана, с чужого компьютера — больше не пускает. Другие сеансы учётки
    не задеты. С разовым паролем за спиной выйти тоже можно.

    Redis не ответил — 503 словами: выход не записан, и пропуск закроется только сроком. Экран
    стирает пропуск и тогда: человек просил выйти, держать его в сеансе нечем."""
    if await sessions.close(signed.payload):
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return JSONResponse(
        {"detail": sessions.NOT_CLOSED}, status_code=status.HTTP_503_SERVICE_UNAVAILABLE
    )
