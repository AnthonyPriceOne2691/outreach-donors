"""Тело запроса — не больше потолка: у ручек, которые читают его сами.

Приём ответов читает байты письма, события доставки — байты подписи, и разбор веб-фреймворка
ни тем, ни другим не годится. Сырое чтение потолка не знает, поэтому он здесь, один на обе ручки.
"""

from __future__ import annotations

from fastapi import Request


async def body_within(request: Request, limit: int) -> bytes | None:
    """Тело целиком, но не больше потолка. `None` — тело больше потолка.

    Объявленный размер проверяется до чтения, но объявить его отправитель
    не обязан: без этой проверки тело без `Content-Length` читалось бы
    в память целиком, сколько бы его ни прислали.
    """
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        return None
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            return None
    return bytes(body)
