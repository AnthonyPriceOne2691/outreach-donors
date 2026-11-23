"""Доступ: секрет подписи пропусков и сроки.

Секрета по умолчанию нет намеренно. Умолчание вроде «change-me» однажды
уезжает в прод и остаётся там: подпись пропусков становится известной,
и любой выписывает себе пропуск администратора. Пустое значение вместо
умолчания даёт внятный отказ на входе (features/access/tokens.py).
"""

from __future__ import annotations

from pydantic import Field

from backend.config._base import DomainSettings


class _Access(DomainSettings):
    jwt_secret: str = Field(default="", validation_alias="ACCESS_JWT_SECRET")
    # Сутки. Обновляющего пропуска нет: при десятке сотрудников схема
    # с обновлением не окупается, а долгий украденный пропуск — это доступ
    # к базе контактов и к отправке писем.
    token_ttl_hours: int = Field(default=24, validation_alias="ACCESS_TOKEN_TTL_HOURS")
    # Простой сеанса (аудит 20.11.2026, №6): столько минут без действий человека — и пропуск
    # больше не пускает, хотя суточный срок не вышел (`access/sessions.py`). Ноль — простой не
    # считать, останется только суточный срок: рычаг на случай, если счёт простоя мешает работать.
    idle_minutes: int = Field(default=60, ge=0, validation_alias="ACCESS_IDLE_MINUTES")
    # Попыток входа с одного адреса в минуту. Подбор пароля не должен
    # быть бесплатным.
    login_attempts_per_minute: int = Field(
        default=10, validation_alias="ACCESS_LOGIN_ATTEMPTS_PER_MINUTE"
    )
    # Сколько часов действует разовый пароль, выданный админом (аудит 20.11.2026): трое суток
    # хватает дойти до входа, а пароль из переписки не остаётся ключом навсегда.
    one_time_ttl_hours: int = Field(default=72, ge=1, validation_alias="ACCESS_ONE_TIME_TTL_HOURS")
    # Пауза входа учётки после неудач подряд (`access/lockout.py`, аудит 20.11.2026): с какой
    # неудачи, первая пауза и потолок, в минутах; каждая следующая неудача — пауза вдвое.
    lockout_after_failures: int = Field(
        default=5, ge=1, validation_alias="ACCESS_LOCKOUT_AFTER_FAILURES"
    )
    lockout_first_minutes: int = Field(
        default=1, ge=1, validation_alias="ACCESS_LOCKOUT_FIRST_MINUTES"
    )
    lockout_max_minutes: int = Field(
        default=30, ge=1, validation_alias="ACCESS_LOCKOUT_MAX_MINUTES"
    )
    # Вход с кодом из приложения (`access/second_step.py`, 23.11.2026). Ключ шифрования секретов
    # кода в базе — Fernet, 32 байта в base64 (`openssl rand -base64 32`). Умолчания нет, как у
    # секрета подписи: без ключа код включить нельзя, а с обязательными ролями сервис не стартует.
    totp_key: str = Field(default="", validation_alias="AUTH_TOTP_KEY")
    # Роли, которым вход с кодом обязателен, через запятую: «admin» или «admin,operator». Пусто —
    # не обязателен никому, а включить себе может любая учётка.
    totp_required_roles: str = Field(default="", validation_alias="AUTH_TOTP_REQUIRED_ROLES")
    # Издатель в приложении-аутентификаторе — подпись над кодом. Имя сервиса, не домен.
    totp_issuer: str = Field(default="Parsing and Prices", validation_alias="AUTH_TOTP_ISSUER")


_s = _Access()

#: Секрет подписи — не короче стольких байт: HS256 и HMAC-SHA256 с ключом короче выхода хеша
#: ослаблены до подбора. Одно число на старт сервера (`startup_checks.check_access`), на выдачу
#: пропуска (`access/tokens.py`) и на секрет меток почты (`startup_checks.say_inbound_secret`).
MIN_SECRET_BYTES: int = 32

JWT_SECRET: str = _s.jwt_secret
TOKEN_TTL_HOURS: int = _s.token_ttl_hours
IDLE_MINUTES: int = _s.idle_minutes
LOGIN_ATTEMPTS_PER_MINUTE: int = _s.login_attempts_per_minute
ONE_TIME_TTL_HOURS: int = _s.one_time_ttl_hours
LOCKOUT_AFTER_FAILURES: int = _s.lockout_after_failures
LOCKOUT_FIRST_MINUTES: int = _s.lockout_first_minutes
LOCKOUT_MAX_MINUTES: int = _s.lockout_max_minutes


def role_names(raw: str) -> frozenset[str]:
    """Роли через запятую → имена строчными и без пробелов; пустые части — мимо. Какие роли
    есть, конфиг не знает (он лист): незнакомое имя называет проверка на старте API
    (`access/second_step.check_required_roles`)."""
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


TOTP_KEY: str = _s.totp_key
#: Роли с обязательным входом по коду — значения ролей строками (`role_names`).
TOTP_REQUIRED_ROLES: frozenset[str] = role_names(_s.totp_required_roles)
TOTP_ISSUER: str = _s.totp_issuer
