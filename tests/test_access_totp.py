"""Код из приложения, ключ его секретов, коды восстановления и ревизия базы (23.11.2026).

Счёт кода — свой, на стандартной библиотеке (`access/totp.py`), поэтому его верность держат
векторы самого стандарта: RFC 6238, приложение B — SHA1, восемь цифр, ключ «12345678901234567890».
Приложения показывают шесть — те же числа, взятые остатком от 10^6, проверяет вектор RFC 4226.
Остальное — то, что ломается тихо: окно допуска шире задуманного, повтор кода, секрет в базе
открытым, ключ, без которого сервис стартует и запирает обязательные роли.
"""

from __future__ import annotations

import base64
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from backend.config import access as cfg
from backend.config.startup_checks import ConfigError, check_totp
from backend.features.access import recovery_codes, second_step, totp, totp_key
from backend.features.access.second_step import CodeRefusedError, TotpStateError
from backend.features.core.domain import AuditAction, UserRole
from backend.features.core.models.access import AuditLogModel, RecoveryCodeModel, UserModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from tests.migration_helpers import columns_down_and_up

MakeUser = Callable[..., Awaitable[UserModel]]

#: Ключ RFC 6238 (приложение B) для SHA1 — ASCII «12345678901234567890», он же base32 ниже.
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()
#: Минута проверок — некруглая, середина шага.
NOW = datetime(2026, 11, 23, 10, 17, 44, tzinfo=UTC)
STEP = timedelta(seconds=totp.STEP_SECONDS)
REVISION = "f30979ec3e29_totp_second_step.py"


def fresh_key() -> str:
    """Ключ шифрования того вида, что даёт `openssl rand -base64 32`."""
    return base64.b64encode(os.urandom(32)).decode()


@pytest.fixture
def key(monkeypatch: pytest.MonkeyPatch) -> str:
    value = fresh_key()
    monkeypatch.setattr(cfg, "TOTP_KEY", value)
    return value


class TestStandard:
    @pytest.mark.parametrize(
        ("moment", "code"),
        [
            (59, "94287082"),
            (1111111109, "07081804"),
            (1111111111, "14050471"),
            (1234567890, "89005924"),
            (2000000000, "69279037"),
            (20000000000, "65353130"),
        ],
    )
    def test_rfc_6238_vectors_sha1(self, moment: int, code: str) -> None:
        assert totp.code_at(RFC_SECRET, moment, digits=8) == code

    @pytest.mark.parametrize(
        ("counter", "code"), [(0, "755224"), (1, "287082"), (5, "254676"), (9, "520489")]
    )
    def test_rfc_4226_six_digits(self, counter: int, code: str) -> None:
        """Шесть цифр, которые показывает приложение, — RFC 4226, приложение D."""
        assert totp.hotp(b"12345678901234567890", counter) == code

    def test_secret_is_160_bits_of_base32_without_padding(self) -> None:
        secret = totp.new_secret()

        assert len(secret) == 32
        assert "=" not in secret
        assert len(base64.b32decode(secret)) == totp.SECRET_BYTES
        assert secret != totp.new_secret()

    def test_uri_opens_the_app_with_issuer_and_account(self) -> None:
        uri = totp.otpauth_uri(
            "ABCD", account="ivan@team.example.test", issuer="Parsing and Prices"
        )

        assert uri == (
            "otpauth://totp/Parsing%20and%20Prices:ivan@team.example.test?secret=ABCD"
            "&issuer=Parsing%20and%20Prices&algorithm=SHA1&digits=6&period=30"
        )


class TestWindow:
    def test_code_of_this_and_neighbour_steps_fits(self) -> None:
        moment = NOW.timestamp()
        here = totp.step_at(moment)

        for shift in (-1, 0, 1):
            code = totp.code_at(RFC_SECRET, moment + shift * totp.STEP_SECONDS)
            assert totp.matching_step(RFC_SECRET, code, moment) == here + shift

    def test_code_two_steps_away_does_not_fit(self) -> None:
        moment = NOW.timestamp()
        near = {totp.code_at(RFC_SECRET, moment + s * totp.STEP_SECONDS) for s in (-1, 0, 1)}
        far = totp.code_at(RFC_SECRET, moment + 2 * totp.STEP_SECONDS)
        assert far not in near  # без этого проверка ниже не о чём

        assert totp.matching_step(RFC_SECRET, far, moment) is None

    @pytest.mark.parametrize("typed", ["", "12345", "1234567", "12a456", "١٢٣٤٥٦"])
    def test_not_six_ascii_digits_is_not_a_code(self, typed: str) -> None:
        assert not totp.looks_like_code(typed)
        assert totp.matching_step(RFC_SECRET, typed, NOW.timestamp()) is None

    def test_spaces_the_app_shows_do_not_matter(self) -> None:
        code = totp.code_at(RFC_SECRET, NOW.timestamp())

        assert totp.matching_step(RFC_SECRET, f"{code[:3]} {code[3:]}", NOW.timestamp())


class TestKey:
    def test_secret_goes_to_the_base_sealed_and_comes_back(self, key: str) -> None:
        sealed = totp_key.seal(RFC_SECRET)

        assert RFC_SECRET not in sealed
        assert totp_key.unseal(sealed) == RFC_SECRET

    def test_without_a_key_the_code_cannot_be_turned_on(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cfg, "TOTP_KEY", "")

        with pytest.raises(totp_key.TotpKeyMissingError, match="AUTH_TOTP_KEY"):
            totp_key.seal(RFC_SECRET)
        assert not totp_key.ready()

    def test_a_key_of_the_wrong_kind_is_refused_in_words(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cfg, "TOTP_KEY", "короткий")

        with pytest.raises(totp_key.TotpKeyMissingError, match="openssl rand -base64 32"):
            totp_key.seal(RFC_SECRET)

    def test_secret_sealed_by_another_key_is_refused_in_words(
        self, key: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sealed = totp_key.seal(RFC_SECRET)
        monkeypatch.setattr(cfg, "TOTP_KEY", fresh_key())

        with pytest.raises(totp_key.TotpKeyMismatchError, match="кодом восстановления"):
            totp_key.unseal(sealed)


class TestStartup:
    def test_required_roles_without_a_key_stop_the_service(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cfg, "TOTP_REQUIRED_ROLES", frozenset({"admin"}))
        monkeypatch.setattr(cfg, "TOTP_KEY", "")

        with pytest.raises(ConfigError, match="AUTH_TOTP_REQUIRED_ROLES задан \\(admin\\)"):
            check_totp()

    def test_a_broken_key_stops_the_service(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cfg, "TOTP_REQUIRED_ROLES", frozenset())
        monkeypatch.setattr(cfg, "TOTP_KEY", "не-ключ")

        with pytest.raises(ConfigError, match="AUTH_TOTP_KEY не ключ Fernet"):
            check_totp()

    def test_no_roles_and_no_key_is_fine(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cfg, "TOTP_REQUIRED_ROLES", frozenset())
        monkeypatch.setattr(cfg, "TOTP_KEY", "")

        check_totp()

    def test_unknown_role_is_named(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cfg, "TOTP_REQUIRED_ROLES", frozenset({"admins", "admin"}))

        with pytest.raises(ConfigError, match="ролей admins нет"):
            second_step.check_required_roles()

    def test_roles_are_read_comma_separated_and_lowercase(self) -> None:
        assert cfg.role_names(" Admin, operator ,") == {"admin", "operator"}
        assert cfg.role_names("") == frozenset()


class TestRecoveryCodes:
    def test_ten_codes_of_80_bits_in_groups(self) -> None:
        codes = [recovery_codes._fresh() for _ in range(recovery_codes.COUNT)]

        assert all(len(code) == 19 and code.count("-") == 3 for code in codes)
        assert all(recovery_codes.looks_like_code(code) for code in codes)
        assert len(set(codes)) == recovery_codes.COUNT

    def test_typed_from_paper_any_case_spaces_and_lookalikes(self) -> None:
        assert recovery_codes.normalize(" K3M9 x2qa-0011 abcd ") == "k3m9x2qa0011abcd"
        assert recovery_codes.digest("k3m9-x2qa-OOIL-abcd") == recovery_codes.digest(
            "k3m9x2qa0011abcd"
        )

    async def test_a_code_works_once_and_counts_down(
        self, session: AsyncSession, make_user: MakeUser
    ) -> None:
        user = await make_user("ivan@team.example.test")
        codes = await recovery_codes.issue(session, user.id)

        assert await recovery_codes.spend(session, user.id, codes[0].upper(), NOW) == 9
        assert await recovery_codes.spend(session, user.id, codes[0], NOW) is None
        assert await recovery_codes.spend(session, user.id, "не код", NOW) is None
        stored = (await session.scalars(select(RecoveryCodeModel.code_hash))).all()
        assert not set(codes) & set(stored)


async def _enabled(session: AsyncSession, user: UserModel) -> str:
    """Включить код учётке теми же шагами, что экран: начать и подтвердить первым кодом."""
    secret = await second_step.begin(session, user)
    await session.refresh(user)
    await second_step.enable(session, user, totp.code_at(secret, NOW.timestamp()), NOW)
    return secret


class TestAccountCode:
    async def test_secret_lies_sealed_and_turning_on_bumps_the_generation(
        self, session: AsyncSession, make_user: MakeUser, key: str
    ) -> None:
        user = await make_user("ivan@team.example.test")

        secret = await _enabled(session, user)

        assert user.totp_secret is not None
        assert secret not in user.totp_secret
        assert user.totp_enabled_at == NOW
        assert user.token_generation == 1
        assert await recovery_codes.left(session, user.id) == recovery_codes.COUNT
        actions = (await session.scalars(select(AuditLogModel.action))).all()
        assert AuditAction.TOTP_ENABLED in actions

    async def test_the_same_code_does_not_let_in_twice(
        self, session: AsyncSession, make_user: MakeUser, key: str
    ) -> None:
        user = await make_user("ivan@team.example.test")
        secret = await _enabled(session, user)
        later = NOW + STEP
        code = totp.code_at(secret, later.timestamp())

        assert not (await second_step.verify(session, user, code, later)).recovery
        with pytest.raises(CodeRefusedError, match="уже использован"):
            await second_step.verify(session, user, code, later)

    async def test_the_first_code_is_spent_by_turning_on(
        self, session: AsyncSession, make_user: MakeUser, key: str
    ) -> None:
        user = await make_user("ivan@team.example.test")
        secret = await _enabled(session, user)

        with pytest.raises(CodeRefusedError, match="уже использован"):
            await second_step.verify(session, user, totp.code_at(secret, NOW.timestamp()), NOW)

    async def test_turning_on_twice_or_confirming_unstarted_is_refused(
        self, session: AsyncSession, make_user: MakeUser, key: str
    ) -> None:
        user = await make_user("ivan@team.example.test")
        with pytest.raises(TotpStateError, match="Сначала начните"):
            await second_step.enable(session, user, "123456", NOW)

        await _enabled(session, user)

        with pytest.raises(TotpStateError, match="уже включён"):
            await second_step.begin(session, user)

    async def test_wrong_first_code_keeps_the_code_off(
        self, session: AsyncSession, make_user: MakeUser, key: str
    ) -> None:
        user = await make_user("ivan@team.example.test")
        await second_step.begin(session, user)
        await session.refresh(user)

        with pytest.raises(CodeRefusedError, match="не подошёл"):
            await second_step.enable(session, user, "абв", NOW)
        assert not second_step.enabled(user)

    async def test_reset_drops_secret_codes_and_bumps_the_generation(
        self, session: AsyncSession, make_user: MakeUser, key: str
    ) -> None:
        user = await make_user("ivan@team.example.test")
        await _enabled(session, user)

        await second_step.switch_off(session, user, action=AuditAction.TOTP_RESET, author_id=None)

        assert (user.totp_secret, user.totp_enabled_at, user.totp_last_step) == (None, None, None)
        assert user.token_generation == 2
        assert await recovery_codes.left(session, user.id) == 0

    def test_required_follows_the_setting(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cfg, "TOTP_REQUIRED_ROLES", frozenset({"admin"}))

        assert second_step.required(UserRole.ADMIN)
        assert not second_step.required(UserRole.OPERATOR)


class TestRevision:
    async def test_columns_and_table_go_down_and_come_back(self, session: AsyncSession) -> None:
        connection = await session.connection()
        columns = {"totp_secret", "totp_enabled_at", "totp_last_step", "token_generation"}

        down, up = await connection.run_sync(
            lambda sync: columns_down_and_up(sync, REVISION, "users", columns)
        )

        assert (down, up) == (set(), columns)
        tables = await session.scalar(
            text("SELECT count(*) FROM pg_tables WHERE tablename = 'user_recovery_codes'")
        )
        assert tables == 1

    async def test_accounts_made_before_keep_their_passes(self, session: AsyncSession) -> None:
        """Поколение пропусков у прежних строк — ноль: отпечаток пропуска тот же, что до ревизии."""
        found = await session.scalar(
            text(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_name = 'users' AND column_name = 'token_generation'"
            )
        )
        assert found == "0"
