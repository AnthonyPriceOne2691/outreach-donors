"""Значок вкладки: файлы, на которые ссылается `index.html`, лежат в сборке.

Ломается молча. Внутренний nginx отвечает на любой неизвестный путь index.html
с кодом 200 (`try_files … /index.html` — так живёт одностраничное приложение),
поэтому забытый или переименованный файл значка не даёт ни 404, ни строки
в журнале: браузер получает страницу вместо картинки и оставляет вкладку без
значка. Так и было до 10.10.2026 — своего значка не было, и на /favicon.ico
приходил index.html.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "frontend" / "index.html"
# Vite кладёт содержимое `public/` в корень сборки как есть.
PUBLIC = ROOT / "frontend" / "public"


def _icon_hrefs(html: str) -> list[str]:
    """Адреса из `<link rel="…icon…">` — значок вкладки и экрана «Домой»."""
    hrefs = []
    for tag in re.findall(r"<link\b[^>]*>", html):
        rel = re.search(r'\brel="([^"]*)"', tag)
        href = re.search(r'\bhref="([^"]*)"', tag)
        if rel and href and "icon" in rel.group(1):
            hrefs.append(href.group(1))
    return hrefs


def test_every_icon_link_has_its_file() -> None:
    hrefs = _icon_hrefs(INDEX.read_text(encoding="utf-8"))
    assert hrefs, "в frontend/index.html нет ни одной ссылки на значок"
    for href in hrefs:
        assert (PUBLIC / href.lstrip("/")).is_file(), f"{href}: файла нет в frontend/public"
    # /favicon.ico просят и мимо ссылок — браузер по старой памяти, читалки, закладки.
    assert (PUBLIC / "favicon.ico").is_file()


def test_ico_carries_tab_sizes() -> None:
    """Вкладка — 16 px, на плотном экране — 32: ICO без них браузер растянет."""
    data = (PUBLIC / "favicon.ico").read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    assert (reserved, kind) == (0, 1), "favicon.ico — не значок ICO"
    # Ширина в байте записи; 0 означает 256.
    widths = {data[6 + 16 * i] or 256 for i in range(count)}
    assert {16, 32} <= widths


def test_home_screen_icon_is_opaque_square() -> None:
    """Прозрачное iOS заливает чёрным: значок экрана «Домой» — 180 px без альфы."""
    data = (PUBLIC / "apple-touch-icon.png").read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height, _depth, color = struct.unpack_from(">IIBB", data, 16)
    assert (width, height) == (180, 180)
    assert color in (0, 2), "у значка экрана «Домой» есть прозрачность"
