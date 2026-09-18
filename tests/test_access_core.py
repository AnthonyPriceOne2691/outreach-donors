"""Пароли, пропуска и права — без базы и без сети.

Три темы, у каждой своя цена ошибки: пароль в открытом виде превращает
утечку базы в доступ ко всем сервисам сотрудника; принятый поддельный
пропуск отменяет вход целиком; лишнее право превращает скомпрометированную
учётку в рассылку с наших доменов.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from backend.features.access.passwords import (
    MIN_PASSWORD_LENGTH,
    ONE_TIME_LENGTH,
    WeakPasswordError,
    assert_strong_enough,
    generate_one_time,
    hash_password,
    verify_password,
)
from backend.features.access.permissions import (
    AccessDeniedError,
    Actor,
    has_permission,
    require,
)
from backend.features.access.tokens import (
    SecretMissingError,
    TokenError,
    create_token,
    read_token,
)
from backend.features.core.domain import Permission, UserRole


class TestPasswords:
    def test_hash_is_not_the_password(self) -> None:
        hashed = hash_password("правильный-конь-батарейка")
        assert "правильный" not in hashed
        assert verify_password("правильный-конь-батарейка", hashed)

    def test_wrong_password_refused(self) -> None:
        assert not verify_password("другой", hash_password("исходный"))

    def test_same_password_hashes_differently(self) -> None:
        """Соль: две одинаковые пары в базе не должны выглядеть одинаково,
        иначе видно, у кого пароли совпадают."""
        assert hash_password("одинаковый") != hash_password("одинаковый")

    def test_empty_password_is_an_error_not_an_empty_hash(self) -> None:
        with pytest.raises(WeakPasswordError):
            hash_password("")

    def test_missing_hash_does_not_let_in(self) -> None:
        assert not verify_password("любой", None)
        assert not verify_password("любой", "")

    def test_broken_hash_does_not_let_in(self) -> None:
        """Испорченный хеш — это «не пустить», а не падение на входе."""
        assert not verify_password("любой", "не-хеш-вовсе")

    def test_short_password_named_with_advice(self) -> None:
        with pytest.raises(WeakPasswordError, match="три-четыре слова"):
            assert_strong_enough("x" * (MIN_PASSWORD_LENGTH - 1))

    def test_long_password_passes(self) -> None:
        assert_strong_enough("три слова подряд")

    def test_one_time_password_is_readable_aloud(self) -> None:
        """Пароль диктуют голосом и переписывают руками: похожих знаков
        в нём быть не должно."""
        for _ in range(20):
            password = generate_one_time()
            assert len(password) == ONE_TIME_LENGTH
            assert not set(password) & set("Il1O0")

    def test_one_time_passwords_differ(self) -> None:
        assert len({generate_one_time() for _ in range(50)}) == 50


class TestTokens:
    @pytest.fixture(autouse=True)
    def _secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("backend.features.access.tokens.cfg.JWT_SECRET", "x" * 64)

    def test_roundtrip(self) -> None:
        payload = read_token(create_token(42))
        assert payload.user_id == 42
        assert payload.expires_at > datetime.now(UTC)

    def test_expired_token_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("backend.features.access.tokens.cfg.TOKEN_TTL_HOURS", 1)
        long_ago = datetime.now(UTC) - timedelta(hours=5)
        with pytest.raises(TokenError, match="просрочен"):
            read_token(create_token(1, now=long_ago))

    def test_token_signed_with_another_secret_refused(self) -> None:
        alien = jwt.encode({"sub": "1", "exp": 9999999999}, "y" * 64, algorithm="HS256")
        with pytest.raises(TokenError):
            read_token(alien)

    def test_unsigned_token_refused(self) -> None:
        """Классическая дыра: подделыватель пишет в пропуске «без подписи».
        Алгоритм указан при проверке явно, поэтому такой пропуск не примут."""
        forged = jwt.encode({"sub": "1", "exp": 9999999999}, key="", algorithm="none")
        with pytest.raises(TokenError):
            read_token(forged)

    def test_token_without_user_refused(self) -> None:
        empty = jwt.encode({"exp": 9999999999}, "x" * 64, algorithm="HS256")
        with pytest.raises(TokenError, match="нет сотрудника"):
            read_token(empty)

    def test_missing_secret_refuses_loudly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Пустой секрет — это отказ с инструкцией, а не проверка без подписи."""
        monkeypatch.setattr("backend.features.access.tokens.cfg.JWT_SECRET", "")
        with pytest.raises(SecretMissingError, match="ACCESS_JWT_SECRET"):
            create_token(1)

    def test_short_secret_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Короткий секрет подбирают. Поймано тем, что библиотека
        предупреждает, а у нас предупреждение — это падение теста."""
        monkeypatch.setattr("backend.features.access.tokens.cfg.JWT_SECRET", "коротко")
        with pytest.raises(SecretMissingError, match="короче"):
            create_token(1)


class TestPermissions:
    ADMIN = Actor(user_id=1, role=UserRole.ADMIN)
    OPERATOR = Actor(user_id=2, role=UserRole.OPERATOR)

    def test_admin_can_everything(self) -> None:
        assert all(has_permission(self.ADMIN, p) for p in Permission)

    def test_operator_works_but_does_not_manage_users(self) -> None:
        assert has_permission(self.OPERATOR, Permission.RUN)
        assert has_permission(self.OPERATOR, Permission.SETTINGS)
        assert not has_permission(self.OPERATOR, Permission.USERS)

    def test_sending_is_not_part_of_the_operator_role(self) -> None:
        """Скомпрометированная учётка оператора не должна давать рассылку
        с наших доменов: юниты вернутся первого числа, репутация — нет."""
        assert not has_permission(self.OPERATOR, Permission.SEND)

    def test_sending_can_be_granted_by_name(self) -> None:
        trusted = Actor(user_id=3, role=UserRole.OPERATOR, overrides={"send": True})
        assert has_permission(trusted, Permission.SEND)

    def test_permission_can_be_taken_away_from_admin(self) -> None:
        """Исключения работают в обе стороны — иначе «временно отобрать»
        означает «поменять роль», а это меняет и всё остальное."""
        limited = Actor(user_id=4, role=UserRole.ADMIN, overrides={"send": False})
        assert not has_permission(limited, Permission.SEND)
        assert has_permission(limited, Permission.USERS)

    def test_disabled_account_can_do_nothing(self) -> None:
        fired = Actor(user_id=5, role=UserRole.ADMIN, is_active=False)
        assert not any(has_permission(fired, p) for p in Permission)

    def test_refusal_names_the_action(self) -> None:
        with pytest.raises(AccessDeniedError, match="users"):
            require(self.OPERATOR, Permission.USERS)

    def test_anonymous_is_asked_to_log_in(self) -> None:
        with pytest.raises(AccessDeniedError, match="Нужен вход"):
            require(None, Permission.VIEW)
