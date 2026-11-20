"""Список частых паролей: каким он лежит в репозитории и по каким правилам собран.

Список — данные из открытых утечек (SecLists, MIT), отобранные скриптом
`scripts/common_passwords.py` (аудит 20.11.2026). Сам отбор без сети не повторить — исходники
весят по восемь мегабайт и в репозиторий не кладутся, — поэтому здесь сверяется то, что
обязано держаться без них: вид файла, его связь с проверкой пароля и правила отбора на
образцах. Образцы — придуманные строки того же вида, а не записи утечек.
"""

from __future__ import annotations

import pytest
from backend.features.access.passwords import (
    COMMON_PASSWORDS,
    COMMON_PASSWORDS_PATH,
    MIN_PASSWORD_LENGTH,
    folded,
)
from scripts import common_passwords as build

LINES = COMMON_PASSWORDS_PATH.read_text(encoding="utf-8").splitlines()
HEADER = [line for line in LINES if line.startswith("#")]
ENTRIES = [line for line in LINES if line and not line.startswith("#")]


class TestTheFile:
    def test_header_names_the_source_and_carries_its_license(self) -> None:
        """MIT требует нести уведомление с каждой существенной частью; откуда список — по
        закреплённому коммиту и хешам ввода, чтобы сборку можно было повторить."""
        said = "\n".join(HEADER)

        assert all(url in said for url in build.SOURCES)
        assert said.count("sha256 ") == len(build.SOURCES)
        assert "# MIT License" in HEADER
        assert "# Copyright (c) 2018 Daniel Miessler" in HEADER
        assert LINES[: len(HEADER)] == HEADER, "шапка — только сверху"

    def test_entries_are_folded_sorted_and_unique(self) -> None:
        """Как сверяет проверка (`folded`), так и лежит: иначе запись не нашлась бы никогда."""
        assert sorted(set(ENTRIES)) == ENTRIES
        assert all(folded(entry) == entry for entry in ENTRIES)
        assert set(ENTRIES) == COMMON_PASSWORDS

    def test_list_is_built_for_this_minimum(self) -> None:
        """Короче минимума запись не нужна — отказывает длина. Минимум поменяли — список
        пересобирают с новым (`MIN_LENGTH` скрипта): иначе пароли между старым и новым
        минимумом прошли бы мимо списка."""
        assert build.MIN_LENGTH == MIN_PASSWORD_LENGTH
        assert min(map(len, ENTRIES)) == MIN_PASSWORD_LENGTH

    def test_nothing_personal_or_broken_got_in(self) -> None:
        assert [entry for entry in ENTRIES if build.rejected(entry)] == []

    def test_list_is_small_and_not_empty(self) -> None:
        assert 300 < len(ENTRIES) < 5_000
        assert COMMON_PASSWORDS_PATH.stat().st_size < 100_000


class TestSelection:
    @pytest.mark.parametrize(
        "entry",
        [
            "ivan.petrov1975@example.test",
            "htt//members.example.test/",
            "mysite.example123456",
            "c0ffee12deadbeef",  # pragma: allowlist secret
            "*0a1b2c3d4e5f60718293",
            "$hex[6b6f7368]",
            "????????????????",
            "masha89275550142",
            "olga19061995love",
            "120387zxc250391",
            "пароль-по-русски",
            " qwertyuiopasdfg",
            "#qwertyuiopasdfg",
        ],
        ids=[
            "почта",
            "адрес сайта",
            "домен",
            "хеш",
            "хеш со звёздочкой",
            "запись hashcat",
            "знаки вопроса вместо букв",
            "номер телефона",
            "дата рождения",
            "две даты",
            "не ASCII",
            "пробел с краю",
            "решётка шапки",
        ],
    )
    def test_personal_or_broken_entries_are_not_taken(self, entry: str) -> None:
        assert build.rejected(entry)

    @pytest.mark.parametrize(
        "entry",
        [
            "123456789101112",
            "1234567887654321",
            "112233445566778899",
            "12121212121212121212",
            "147258369147258369",
            "314159265358979",
            "qwertyuiop123456",
            "1a2a3a4a5a6a7a8a",
            "gfhjkmgfhjkm123",
        ],
        ids=[
            "счёт",
            "зеркало",
            "сдвоенные цифры",
            "чередование",
            "столбцы клавиатуры",
            "число пи",
            "клавиши и цифры подряд",
            "одна буква a–f",
            "русское слово латиницей",
        ],
    )
    def test_patterns_people_type_are_kept(self, entry: str) -> None:
        assert not build.rejected(entry)

    def test_head_of_either_list_or_both_lists_deeper(self) -> None:
        """Глубже головы одно присутствие — чаще привычка одного человека: берётся только то,
        что есть в обоих списках."""
        head, deep = build.HEAD - 1, build.HEAD + 1
        first = {"one-list-head": head, "both-deeper": deep, "one-list-deeper": deep}
        second = {"both-deeper": deep, "second-head": 0}

        assert build.chosen(first, second) == ["both-deeper", "one-list-head", "second-head"]
