"""Пароли, пропуска и права — без базы и без сети.

Три темы, у каждой своя цена ошибки: пароль в открытом виде превращает
утечку базы в доступ ко всем сервисам сотрудника; принятый поддельный
пропуск отменяет вход целиком; лишнее право превращает скомпрометированную
учётку в рассылку с наших доменов.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest
from backend.features.access.passwords import (
    COMMON_REFUSAL,
    MAX_PASSWORD_BYTES,
    MIN_PASSWORD_LENGTH,
    ONE_TIME_LENGTH,
    WeakPasswordError,
    _load_common,
    assert_strong_enough,
    folded,
    generate_one_time,
    hash_password,
    one_time_expired,
    verify_password,
)
from backend.features.access.permissions import (
    AccessDeniedError,
    Actor,
    has_permission,
    no_right,
    require,
)
from backend.features.access.tokens import (
    REFUSED,
    REQUIRED_CLAIMS,
    STAMP_CLAIM,
    STAMP_LENGTH,
    TOKEN_ID_CLAIM,
    SecretMissingError,
    TokenError,
    create_token,
    password_changed_since,
    password_stamp,
    read_token,
)
from backend.features.core.domain import Permission, UserRole

ROOT = Path(__file__).resolve().parent.parent


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

    def test_fifteen_is_the_minimum(self) -> None:
        """Аудит 20.11.2026: пятнадцать знаков, а не десять. Прежние пароли входят, как
        входили, — длину судят только при смене."""
        with pytest.raises(WeakPasswordError, match="короче 15 знаков"):
            assert_strong_enough("кот спит у окн")

        assert_strong_enough("кот спит у окна")

    def test_the_screen_asks_for_the_same_minimum(self) -> None:
        """Экран проверяет длину до сервера и называет её в подсказке — своей копией числа.
        Две копии расходятся молча: экран пропускал бы короткий или отказывал длинному."""
        page = ROOT / "frontend" / "src" / "auth" / "ChangePasswordPage.tsx"
        found = re.search(r"^const MIN_LENGTH = (\d+);", page.read_text(encoding="utf-8"), re.M)

        assert found is not None, "в ChangePasswordPage.tsx нет MIN_LENGTH"
        assert int(found.group(1)) == MIN_PASSWORD_LENGTH

    @pytest.mark.parametrize(
        "plain",
        [
            "qwertyuiopasdfgh",
            "QWERTYuiopASDFGH",
            "1q2w3e4r5t6y7u8i",
            "passwordpassword",
            "ILoveYouForever",
        ],
    )
    def test_common_password_is_refused_in_words(self, plain: str) -> None:
        """Длинный пароль, с которого начинается любой подбор, — не пароль (аудит 20.11.2026):
        отказ по списку утёкших и частых, без регистра, и словами — что делать."""
        with pytest.raises(WeakPasswordError) as refused:
            assert_strong_enough(plain)

        assert str(refused.value) == COMMON_REFUSAL
        assert "три-четыре своих слова" in COMMON_REFUSAL

    def test_case_and_composition_do_not_hide_a_listed_password(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Сверка без регистра и в NFC: «й», набранная готовой буквой и составной (и + кратка), —
        одна буква. Список сейчас латиница, а правило — на любой."""
        monkeypatch.setattr(
            "backend.features.access.passwords.COMMON_PASSWORDS",
            frozenset({folded("мой длинный пароль")}),
        )

        with pytest.raises(WeakPasswordError, match="списках утёкших"):
            assert_strong_enough("МОЙ ДЛИННЫЙ ПАРОЛЬ".replace("Й", "Й"))

    def test_list_file_skips_its_header_and_folds_entries(self, tmp_path: Path) -> None:
        listed = tmp_path / "list.txt"
        listed.write_text("# шапка\n#\nQwertyUiop12345\n\nмой пароль\n", encoding="utf-8")

        assert _load_common(listed) == {"qwertyuiop12345", "мой пароль"}

    def test_password_past_the_bcrypt_limit_is_refused_in_words(self) -> None:
        """Аудит 19.11.2026: bcrypt 5 на пароле длиннее 72 байт бросает исключение, и «Сменить
        пароль» отвечал пятисоткой — с 37 русских букв, к которым ведёт совет «три-четыре
        слова». Теперь отказ словами и предел буквами: русская весит два байта."""
        with pytest.raises(WeakPasswordError) as refused:
            assert_strong_enough("я" * (MAX_PASSWORD_BYTES // 2 + 1))

        said = str(refused.value)
        assert f"{MAX_PASSWORD_BYTES} латинских" in said
        assert f"{MAX_PASSWORD_BYTES // 2} русских" in said
        assert "Сократите" in said

    @pytest.mark.parametrize(
        "plain",
        ["x" * MAX_PASSWORD_BYTES, "я" * (MAX_PASSWORD_BYTES // 2)],
        ids=["72 латинских", "36 русских"],
    )
    def test_password_right_at_the_limit_is_kept_and_checked(self, plain: str) -> None:
        """Предел меряется так же, как его меряет bcrypt, — байтами: ровно 72 он ещё считает."""
        assert_strong_enough(plain)
        assert verify_password(plain, hash_password(plain))

    def test_password_past_the_limit_does_not_match_and_blames_no_hash(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Вход с паролем длиннее 72 байт — «не совпадает» ещё до bcrypt (аудит 19.11.2026): его
        отказ писал в журнал «хеш испорчен» про целый хеш. И 73-й байт не отбрасывается молча,
        как у прежних bcrypt: первые 72 совпадают, а пароль — другой."""
        stored = hash_password("x" * MAX_PASSWORD_BYTES)

        with caplog.at_level(logging.DEBUG):
            assert not verify_password("x" * (MAX_PASSWORD_BYTES + 1), stored)
            assert not verify_password("я" * (MAX_PASSWORD_BYTES // 2 + 1), stored)

        assert [record.getMessage() for record in caplog.records] == []

    def test_one_time_password_is_readable_aloud(self) -> None:
        """Пароль диктуют голосом и переписывают руками: похожих знаков
        в нём быть не должно."""
        for _ in range(20):
            password = generate_one_time()
            assert len(password) == ONE_TIME_LENGTH
            assert not set(password) & set("Il1O0")

    def test_one_time_passwords_differ(self) -> None:
        assert len({generate_one_time() for _ in range(50)}) == 50

    def test_one_time_password_lives_its_term_in_hours(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Аудит 20.11.2026: разовый пароль из переписки не остаётся ключом навсегда."""
        monkeypatch.setattr("backend.config.access.ONE_TIME_TTL_HOURS", 72)
        issued = datetime(2026, 11, 20, 9, 41, tzinfo=UTC)

        assert not one_time_expired(issued, issued + timedelta(hours=72))
        assert one_time_expired(issued, issued + timedelta(hours=72, seconds=1))


#: Хеш пароля учётки — какой лежит в базе; смена пароля даёт новый.
HASH = hash_password("три слова подряд")


class TestTokens:
    @pytest.fixture(autouse=True)
    def _secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("backend.features.access.tokens.cfg.JWT_SECRET", "x" * 64)

    def test_roundtrip(self) -> None:
        payload = read_token(create_token(42, password_hash=HASH))
        assert payload.user_id == 42
        assert payload.expires_at > datetime.now(UTC)
        assert payload.password_stamp == password_stamp(HASH)

    def test_expired_token_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("backend.features.access.tokens.cfg.TOKEN_TTL_HOURS", 1)
        long_ago = datetime.now(UTC) - timedelta(hours=5)
        with pytest.raises(TokenError, match="просрочен"):
            read_token(create_token(1, password_hash=HASH, now=long_ago))

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

    @pytest.mark.parametrize(
        ("claim", "said"),
        [("iat", "нет времени выдачи"), ("exp", "нет срока")],
        ids=["без времени выдачи", "без срока"],
    )
    def test_token_without_its_time_is_refused_in_words(self, claim: str, said: str) -> None:
        """Аудит 19.11.2026: пропуск своей подписью без `iat` или `exp` падал при чтении полей
        KeyError — «кто я» отвечал пятисоткой, а не «входа нет»; а без проверки `exp`
        библиотекой пропуск без срока был бы бессрочным. Теперь их требует сама проверка."""
        claims = jwt.decode(
            create_token(7, password_hash=HASH), options={"verify_signature": False}
        )
        del claims[claim]
        forged = jwt.encode(claims, "x" * 64, algorithm="HS256")

        with pytest.raises(TokenError, match=said):
            read_token(forged)

    def test_every_pass_has_its_own_number(self) -> None:
        """Номер пропуска (аудит 20.11.2026) — свой у каждого: по нему выход закрывает этот
        пропуск, а не все пропуска учётки, и простой считается по сеансу, а не по человеку."""
        first, second = (read_token(create_token(7, password_hash=HASH)) for _ in range(2))

        assert first.token_id
        assert second.token_id
        assert first.token_id != second.token_id

    def test_a_pass_issued_before_the_numbers_still_reads(self) -> None:
        """Выданный до выкатки номеров доживает свои сутки: номера пока нет в обязательных
        полях, иначе выкатка выбросила бы всех разом."""
        claims = jwt.decode(
            create_token(7, password_hash=HASH), options={"verify_signature": False}
        )
        del claims[TOKEN_ID_CLAIM]

        assert read_token(jwt.encode(claims, "x" * 64, algorithm="HS256")).token_id is None
        assert TOKEN_ID_CLAIM not in REQUIRED_CLAIMS

    @pytest.mark.parametrize("number", [7, "", ["список"]], ids=["число", "пусто", "список"])
    def test_a_number_we_never_issue_is_refused(self, number: object) -> None:
        """Номер кладёт только выдача — непустой строкой; другое под нашей подписью — пропуск,
        собранный не нами."""
        claims = jwt.decode(
            create_token(7, password_hash=HASH), options={"verify_signature": False}
        )
        claims[TOKEN_ID_CLAIM] = number

        with pytest.raises(TokenError, match=REFUSED):
            read_token(jwt.encode(claims, "x" * 64, algorithm="HS256"))

    def test_a_broken_pass_is_refused_without_the_library_words(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Проверка прода 20.11.2026: отказ нёс текст библиотеки — «Пропуск не принят: Not
        enough segments». Человеку — одна фраза словами, причина — в журнал приложения."""
        with caplog.at_level(logging.INFO), pytest.raises(TokenError) as refused:
            read_token("abc")

        assert str(refused.value) == REFUSED
        assert [
            getattr(r, "reason", "") for r in caplog.records if r.message == "пропуск не принят"
        ]

    def test_missing_secret_refuses_loudly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Пустой секрет — это отказ с инструкцией, а не проверка без подписи."""
        monkeypatch.setattr("backend.features.access.tokens.cfg.JWT_SECRET", "")
        with pytest.raises(SecretMissingError, match="ACCESS_JWT_SECRET"):
            create_token(1, password_hash=HASH)

    def test_short_secret_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Короткий секрет подбирают. Поймано тем, что библиотека
        предупреждает, а у нас предупреждение — это падение теста."""
        monkeypatch.setattr("backend.features.access.tokens.cfg.JWT_SECRET", "коротко")
        with pytest.raises(SecretMissingError, match="короче"):
            create_token(1, password_hash=HASH)


class TestPasswordStamp:
    """Пропуск, выданный до смены пароля, не действует (проверка прода 10.10.2026): сброс
    пароля админом обещал «старый перестанет действовать», а пропуск жил до конца суток."""

    @pytest.fixture(autouse=True)
    def _secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("backend.features.access.tokens.cfg.JWT_SECRET", "x" * 64)

    def test_pass_works_while_the_password_is_the_same(self) -> None:
        payload = read_token(create_token(7, password_hash=HASH))
        assert not password_changed_since(payload, HASH)

    def test_new_password_closes_the_pass(self) -> None:
        """Тот же пароль заново — тоже смена: соль bcrypt новая, хеш другой."""
        payload = read_token(create_token(7, password_hash=HASH))

        assert password_changed_since(payload, hash_password("другой пароль подряд"))
        assert password_changed_since(payload, hash_password("три слова подряд"))

    def test_pass_without_a_stamp_is_refused(self) -> None:
        """Пропуск без отпечатка — отказ (аудит 19.11.2026). До этого он считался годным при
        любом пароле: так пропуска, выданные до отпечатков, доживали свои сутки, а не
        обрывались выкаткой 10.10.2026. Они давно истекли, а собрать такой теперь может только
        тот, у кого секрет подписи, — отпечаток требует от него ещё и хеша пароля из базы."""
        moment = int(datetime.now(UTC).timestamp())
        legacy = jwt.encode(
            {"sub": "7", "iat": moment, "exp": moment + 3600}, "x" * 64, algorithm="HS256"
        )

        with pytest.raises(TokenError, match="нет отпечатка пароля"):
            read_token(legacy)

    def test_stamp_tells_nothing_about_the_hash(self) -> None:
        """Пропуск читает любой, у кого он есть: в нём короткий отпечаток на секрете
        подписи, а не хеш и не его кусок."""
        claims = jwt.decode(
            create_token(7, password_hash=HASH), options={"verify_signature": False}
        )

        stamp = claims[STAMP_CLAIM]
        assert len(stamp) == STAMP_LENGTH
        assert stamp not in HASH
        assert HASH not in str(claims)

    def test_stamp_cannot_be_counted_without_the_secret(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mine = password_stamp(HASH)
        monkeypatch.setattr("backend.features.access.tokens.cfg.JWT_SECRET", "y" * 64)

        assert password_stamp(HASH) != mine


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
        with pytest.raises(AccessDeniedError, match="«заводить учётки»"):
            require(self.OPERATOR, Permission.USERS)

    @pytest.mark.parametrize("permission", list(Permission))
    def test_refusal_is_one_place_for_every_right(self, permission: Permission) -> None:
        # Тесты модулей сверяют отказ по `no_right`, а не копией строки: слова отказа
        # меняются, и копия в чужом тесте держала бы правку до его правки.
        nobody = Actor(
            user_id=6, role=UserRole.OPERATOR, overrides={p.value: False for p in Permission}
        )
        with pytest.raises(AccessDeniedError) as refused:
            require(nobody, permission)
        assert str(refused.value) == no_right(permission)

    def test_anonymous_is_asked_to_log_in(self) -> None:
        with pytest.raises(AccessDeniedError, match="Нужен вход"):
            require(None, Permission.VIEW)
