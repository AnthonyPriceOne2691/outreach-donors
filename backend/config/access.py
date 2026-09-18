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
    # Попыток входа с одного адреса в минуту. Подбор пароля не должен
    # быть бесплатным.
    login_attempts_per_minute: int = Field(
        default=10, validation_alias="ACCESS_LOGIN_ATTEMPTS_PER_MINUTE"
    )


_s = _Access()

JWT_SECRET: str = _s.jwt_secret
TOKEN_TTL_HOURS: int = _s.token_ttl_hours
LOGIN_ATTEMPTS_PER_MINUTE: int = _s.login_attempts_per_minute
