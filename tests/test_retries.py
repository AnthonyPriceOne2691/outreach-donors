"""Повторы и ограничитель: что повторяем, чего не повторяем и сколько ждём.

Требование просит их «на каждый внешний сервис». До этого модуля так
было только у Ahrefs: один таймаут посреди прогона оставлял ключи
без выдачи, а неудавшийся вызов модели — письмо шаблонным.
"""

from __future__ import annotations

import httpx
import pytest
from backend.shared.net.retry import MAX_DELAY_SEC, RETRY_STATUSES, delay_for, with_retries


def _answers(*statuses: int) -> tuple[list[int], object]:
    seen: list[int] = []

    async def call() -> httpx.Response:
        status = statuses[min(len(seen), len(statuses) - 1)]
        seen.append(status)
        return httpx.Response(status, request=httpx.Request("GET", "https://x.test"))

    return seen, call


class TestWhatIsRepeated:
    @pytest.mark.parametrize("status", sorted(RETRY_STATUSES))
    async def test_temporary_answers_are_repeated(self, status: int) -> None:
        seen, call = _answers(status, 200)

        response = await with_retries(call, attempts=3, topic="проверка")  # type: ignore[arg-type]

        assert response.status_code == 200
        assert seen == [status, 200]

    async def test_permanent_refusal_is_not_repeated(self) -> None:
        """«Неверный ключ» повтором не лечится: три попытки — это
        втрое дольше идти к тому же ответу."""
        seen, call = _answers(401)

        response = await with_retries(call, attempts=3, topic="проверка")  # type: ignore[arg-type]

        assert response.status_code == 401
        assert seen == [401]

    async def test_last_answer_is_returned_as_is(self) -> None:
        """Разбирать ответ — дело вызывающего: здесь знают только,
        что бывает временным."""
        seen, call = _answers(503)

        response = await with_retries(call, attempts=2, topic="проверка")  # type: ignore[arg-type]

        assert response.status_code == 503
        assert len(seen) == 2

    async def test_broken_connection_is_repeated_then_raised(self) -> None:
        tries = 0

        async def call() -> httpx.Response:
            nonlocal tries
            tries += 1
            raise httpx.ConnectError("сеть")

        with pytest.raises(httpx.ConnectError):
            await with_retries(call, attempts=3, topic="проверка")

        assert tries == 3


class TestHowLongWeWait:
    def test_pause_grows(self) -> None:
        assert delay_for(0) < delay_for(3)

    def test_provider_asked_to_wait(self) -> None:
        response = httpx.Response(429, headers={"Retry-After": "7"})

        assert 7 <= delay_for(0, response) < 8

    def test_but_not_forever(self) -> None:
        """`Retry-After` бывает в минутах, а прогон ждёт."""
        response = httpx.Response(429, headers={"Retry-After": "3600"})

        assert delay_for(0, response) <= MAX_DELAY_SEC + 0.5

    def test_nonsense_header_does_not_break_the_wait(self) -> None:
        # Заголовки бывают только латиницей — провайдер пришлёт мусор
        # на ней же, и разбираться с ним всё равно придётся.
        response = httpx.Response(429, headers={"Retry-After": "sometime-later"})

        assert delay_for(1, response) > 0
