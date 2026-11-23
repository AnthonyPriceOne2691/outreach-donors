"""Нормализация хоста — ключ дедупликации, от него зависит расход юнитов."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

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


@pytest.mark.parametrize(
    "raw",
    [
        *("", "   ", None, "не-адрес", "http://", "localhost", 42),
        # `urlsplit` бросал ValueError: «[» без пары — начало IPv6, полноширинная
        # косая черта меняет хост после NFKC (находка «Продаж», 03.10.2026).
        *("[", "http://[::1", "https://exa[mple.com/", "https://example.com／x"),
    ],
)
def test_garbage_gives_empty_string(raw: object) -> None:
    """Пустая строка, а не исключение: вызывающий выходит до платного вызова."""
    assert normalize_host(raw) == ""  # type: ignore[arg-type]


def test_subdomain_and_root_collapse_to_one_key() -> None:
    """Главное свойство: один сайт — один ключ, значит одна оплата Ahrefs."""
    variants = ["example.com", "www.example.com", "blog.example.com", "https://m.example.com/x"]
    assert len({normalize_host(v) for v in variants}) == 1


def test_the_suffix_list_writes_nothing_to_disk(tmp_path: Path) -> None:
    """Корень контейнера только для чтения (23.11.2026): разбор хоста ничего не пишет в домашний
    каталог. С кэшем tldextract клал бы снимок в `~/.cache`, а на корне только для чтения каждый
    процесс писал бы в журнал ложное «список будет скачиваться по HTTP». Отдельным процессом:
    каталог кэша tldextract вычисляет из `HOME` при импорте."""
    env = {k: v for k, v in os.environ.items() if k not in {"XDG_CACHE_HOME", "TLDEXTRACT_CACHE"}}
    env["HOME"] = str(tmp_path)
    code = "from backend.features.donors.host import normalize_host as n; print(n('www.a.example.co.uk'))"

    done = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parent.parent,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "example.co.uk"
    assert sorted(tmp_path.rglob("*")) == []
