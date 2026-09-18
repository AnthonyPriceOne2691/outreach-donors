"""Нормализация хоста — ключ дедупликации, от него зависит расход юнитов."""

from __future__ import annotations

import pytest
from backend.features.donors.host import normalize_host


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("example.com", "example.com"),
        ("www.example.com", "example.com"),
        ("https://example.com", "example.com"),
        ("HTTPS://WWW.Example.COM/path?x=1#frag", "example.com"),
        ("http://blog.example.com/post/1", "example.com"),
        ("  https://shop.example.com/  ", "example.com"),
        # Составные суффиксы: срезать «две последние метки» здесь неверно.
        ("www.example.co.uk", "example.co.uk"),
        ("blog.example.com.au", "example.com.au"),
    ],
)
def test_normalizes_to_root_domain(raw: str, expected: str) -> None:
    assert normalize_host(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("user.github.io", "user.github.io"),
        ("shop.myshopify.com", "shop.myshopify.com"),
        ("blog.blogspot.com", "blog.blogspot.com"),
        ("app.netlify.app", "app.netlify.app"),
        ("x.vercel.app", "x.vercel.app"),
        ("y.herokuapp.com", "y.herokuapp.com"),
    ],
)
def test_platform_subdomains_are_not_merged(raw: str, expected: str) -> None:
    """У каждого поддомена платформы свой владелец. Схлопнув их, мы написали бы
    владельцу площадки вместо владельца сайта — и один раз вместо тысячи."""
    assert normalize_host(raw) == expected


@pytest.mark.parametrize("raw", ["someone.wordpress.com", "author.medium.com"])
def test_known_gap_platforms_outside_psl(raw: str) -> None:
    """Задокументированное ограничение, а не недосмотр: wordpress.com и
    medium.com исключены из списка суффиксов самими площадками, поэтому их
    поддомены сворачиваются в корень. Тест фиксирует это поведение — если
    список обновится, он упадёт и заставит перечитать решение."""
    root = raw.split(".", 1)[1]
    assert normalize_host(raw) == root


@pytest.mark.parametrize("raw", ["", "   ", None, "не-адрес", "http://", "localhost", 42])
def test_garbage_gives_empty_string(raw: object) -> None:
    """Пустая строка, а не исключение: вызывающий выходит до платного вызова."""
    assert normalize_host(raw) == ""  # type: ignore[arg-type]


def test_subdomain_and_root_collapse_to_one_key() -> None:
    """Главное свойство: один сайт — один ключ, значит одна оплата Ahrefs."""
    variants = ["example.com", "www.example.com", "blog.example.com", "https://m.example.com/x"]
    assert len({normalize_host(v) for v in variants}) == 1
