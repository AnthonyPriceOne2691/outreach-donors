"""Точка запуска сервера: `uvicorn backend.api.main:app`.

За обратным прокси запускать с `--proxy-headers`, иначе счётчик попыток
входа видит адрес прокси вместо адреса клиента и считает весь интернет
одним посетителем.
"""

from __future__ import annotations

from backend.api.app import create_app

app = create_app()
