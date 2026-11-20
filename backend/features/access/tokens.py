"""Пропуск: выдача и проверка.

Пропуск подписан симметричным ключом (HS256) и живёт сутки. Обновляющего
пропуска нет: при десяти сотрудниках схема с обновлением не окупается,
а долгоживущий украденный пропуск — это доступ к базе контактов
и к отправке.

**Алгоритм подписи указывается явно при проверке.** Без этого библиотека
берёт алгоритм из самого пропуска, и подделыватель просто пишет там
«без подписи». Это классическая дыра, и закрывается она одной строкой.

**Пустой секрет означает отказ, а не проверку без подписи.** Сервис,
которому не хватает настройки, обязан падать сразу и называть, чего
не хватает, — то же правило, что и для ключей провайдеров.

**Пропуск, выданный до смены пароля, не действует** (проверка прода
10.10.2026). Пропуск несёт отпечаток пароля учётки на момент выдачи, а «кто я»
на каждом запросе (`api/deps.py`, учётку он и так читает из базы) сверяет его
с нынешним. Без этого сброс пароля админом обещал «старый перестанет
действовать», а пропуск, выданный до сброса, открывал данные до конца суток —
и снова, едва человек сменит разовый пароль. Списка выданных пропусков и
миграции для этого не нужно: хеш пароля меняется при каждой смене — соль
bcrypt новая даже для того же пароля.

**Пропуск без любого из своих полей не принимается** (аудит 19.11.2026,
`REQUIRED_CLAIMS`): ни без срока, ни без отпечатка пароля. Без срока библиотека
сочла бы пропуск бессрочным, без отпечатка — годным при любом пароле; пропуска
до отпечатков истекли через сутки после 10.10.2026, и такой теперь собирает
только тот, у кого секрет подписи, — а отпечаток требует ещё и хеша из базы.

**Пропуск несёт свой номер** (`jti`, аудит 20.11.2026): по нему сервер помнит выход и
простой сеанса (`access/sessions.py`). Пропуск, выданный до выкатки номеров, его не несёт и
доживает свои сутки как раньше — без выхода на сервере и без счёта простоя: иначе выкатка
выбросила бы всех разом. Поэтому номера пока нет в `REQUIRED_CLAIMS`; ему место там, когда
такие пропуска истекут — через сутки после выкатки.

**Отказ негодному пропуску — словами, без текста библиотеки** (проверка прода 20.11.2026: экран
показал «Пропуск не принят: Not enough segments»). Почему не принят, пишется в журнал
приложения: человеку английский текст разбора не поможет, а разбирающему — пригодится.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from backend.config import access as cfg
from backend.features.access.permissions import SignedOutError

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"
#: Минимальная длина секрета. Требование стандарта для этой подписи:
#: ключ короче 32 байт ослабляет её до подбора. Проверяем сами — иначе
#: «временный» короткий секрет доживёт до прода. Число — одно с проверкой
#: на старте сервера (`config/access.MIN_SECRET_BYTES`).
MIN_SECRET_LENGTH = cfg.MIN_SECRET_BYTES

#: Поле отпечатка пароля в пропуске и его длина: 16 шестнадцатеричных знаков — 64 бита,
#: случайно совпасть после смены пароля нечему.
STAMP_CLAIM = "stamp"
STAMP_LENGTH = 16

#: Поле номера пропуска (стандартное имя JWT) и его длина: 16 случайных байт — 128 бит, номер
#: не угадать и не повторить. По номеру Redis помнит выход и простой (`access/sessions.py`).
TOKEN_ID_CLAIM = "jti"  # noqa: S105 — имя поля пропуска, не секрет
TOKEN_ID_BYTES = 16

#: Отказ пропуску, который не разобрался: подпись не сошлась, пропуск обрезан или собран
#: не нами. Один на все причины разбора — причина уходит в журнал приложения.
REFUSED = "Пропуск не принят — войдите заново"

#: Без чего пропуск не принимается — и что сказать о нехватке: кто (`sub`), когда выдан
#: (`iat`), до какого срока (`exp`) и отпечаток пароля. Требует сама библиотека (`require`):
#: без этого пропуск без `iat` или `exp` доходил до чтения полей и падал там KeyError —
#: пятисоткой вместо «входа нет».
REQUIRED_CLAIMS = {
    "sub": "В пропуске нет сотрудника",
    "iat": "В пропуске нет времени выдачи",
    "exp": "В пропуске нет срока",
    STAMP_CLAIM: "В пропуске нет отпечатка пароля",
}

#: Заголовок, которым экран шлёт пропуск. Не `Authorization`: на /api/ его занимает пароль
#: прокси — тот же общий пароль сервера, что и у экрана (`deploy/proxy/outreach.conf`). До
#: 11.10.2026 API за паролем прокси не стоял: Bearer и Basic не уживались в одном заголовке, и
#: при публичном коде вход в API был единственным замком перед данными.
TOKEN_HEADER = "X-Access-Token"  # noqa: S105 — имя заголовка, не секрет


class TokenError(SignedOutError):
    """Пропуск не принят: просрочен, подделан или выдан не нами — входа нет (`SignedOutError`)."""


class SecretMissingError(RuntimeError):
    """Секрет подписи не задан. Работать без него нельзя."""


@dataclass(frozen=True, slots=True)
class TokenPayload:
    """Что лежит в пропуске. Ролей и прав здесь нет намеренно.

    Права проверяются по базе на каждом запросе: иначе отключённый
    сотрудник ходит с действующим пропуском до конца суток, а отобранное
    право продолжает действовать.
    """

    user_id: int
    issued_at: datetime
    expires_at: datetime
    #: Отпечаток пароля на момент выдачи (`password_stamp`). Без него пропуск не читается
    #: (`REQUIRED_CLAIMS`).
    password_stamp: str
    #: Номер пропуска (`TOKEN_ID_CLAIM`). `None` — пропуск выдан до выкатки номеров
    #: (20.11.2026): выхода на сервере и простоя у него нет, доживает свои сутки.
    token_id: str | None = None


def _secret() -> str:
    secret = cfg.JWT_SECRET
    if not secret:
        raise SecretMissingError(
            "ACCESS_JWT_SECRET не задан — вход работать не может. "
            "Сгенерировать: openssl rand -hex 32"
        )
    if len(secret.encode("utf-8")) < MIN_SECRET_LENGTH:
        raise SecretMissingError(
            f"ACCESS_JWT_SECRET короче {MIN_SECRET_LENGTH} байт — такую подпись "
            "подбирают. Сгенерировать: openssl rand -hex 32"
        )
    return secret


def password_stamp(password_hash: str | None) -> str:
    """Отпечаток пароля учётки для пропуска.

    HMAC на секрете подписи, а не сам хеш и не его простой отпечаток: пропуск читает любой,
    у кого он есть (подпись его не прячет), а этот отпечаток без секрета не посчитать, и
    о хеше он ничего не говорит.
    """
    signed = hmac.new(
        _secret().encode("utf-8"), f"password:{password_hash or ''}".encode(), hashlib.sha256
    )
    return signed.hexdigest()[:STAMP_LENGTH]


def create_token(user_id: int, *, password_hash: str | None, now: datetime | None = None) -> str:
    """Выдать пропуск сотруднику. `password_hash` — нынешний хеш пароля учётки: его
    отпечаток едет в пропуске, и смена пароля этот пропуск закрывает. Номер пропуска —
    случайный, свой у каждого: по нему закрывается этот пропуск, а не все пропуска учётки."""
    moment = now or datetime.now(UTC)
    expires = moment + timedelta(hours=cfg.TOKEN_TTL_HOURS)
    return jwt.encode(
        {
            "sub": str(user_id),
            "iat": int(moment.timestamp()),
            "exp": int(expires.timestamp()),
            STAMP_CLAIM: password_stamp(password_hash),
            TOKEN_ID_CLAIM: secrets.token_urlsafe(TOKEN_ID_BYTES),
        },
        _secret(),
        algorithm=ALGORITHM,
    )


def read_token(token: str) -> TokenPayload:
    """Проверить пропуск и достать из него сотрудника."""
    try:
        claims = jwt.decode(
            token, _secret(), algorithms=[ALGORITHM], options={"require": list(REQUIRED_CLAIMS)}
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Пропуск просрочен — войдите заново") from exc
    except jwt.MissingRequiredClaimError as exc:
        missing = REQUIRED_CLAIMS.get(exc.claim, f"В пропуске нет поля {exc.claim}")
        raise TokenError(f"{missing} — войдите заново") from exc
    except jwt.InvalidTokenError as exc:
        logger.info("пропуск не принят", extra={"reason": str(exc)})
        raise TokenError(REFUSED) from exc

    try:
        user_id = int(claims["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TokenError("В пропуске нет сотрудника") from exc

    return TokenPayload(
        user_id=user_id,
        issued_at=datetime.fromtimestamp(int(claims["iat"]), tz=UTC),
        expires_at=datetime.fromtimestamp(int(claims["exp"]), tz=UTC),
        # Как лежит: подпись проверена, а отпечаток кладёт только `create_token` — строкой.
        password_stamp=claims[STAMP_CLAIM],
        token_id=_token_id(claims.get(TOKEN_ID_CLAIM)),
    )


def _token_id(raw: object) -> str | None:
    """Номер пропуска: `None` — пропуск выдан до номеров. Номер кладёт только `create_token` —
    непустой строкой; другое значение под нашей подписью значит пропуск, собранный не нами."""
    if raw is None or (isinstance(raw, str) and raw):
        return raw
    raise TokenError(REFUSED)


def password_changed_since(payload: TokenPayload, password_hash: str | None) -> bool:
    """Сменили ли пароль учётки после выдачи пропуска — тогда пропуск не действует.

    До 19.11.2026 пропуск без отпечатка здесь считался годным: так пропуска, выданные до
    отпечатков, доживали свои сутки, а не обрывались выкаткой 10.10.2026. Их давно нет, и
    без отпечатка пропуск теперь не читается вовсе (`read_token`, `REQUIRED_CLAIMS`).
    """
    return not hmac.compare_digest(payload.password_stamp, password_stamp(password_hash))
