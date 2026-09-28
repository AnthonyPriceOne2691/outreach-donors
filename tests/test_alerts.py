"""Тревога в Telegram: что уходит, что пишется в журнал и чего туда не попадает.

Сеть не ходит: транспорт httpx подменён и записывает запросы. Журнал —
настоящий: запись логгера httpx проходит тот же путь, что на сервере,
и токен ищется в том, что напечатано, а не в том, что должно было быть.
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Callable, Iterator

import httpx
import pytest
from backend.shared import alerts
from backend.shared.logs.setup import RunIdFilter, TextFormatter

TOKEN = "987654321:AAH-secret_token-for-tests"
CHAT = "-1001234567890"

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def bot(monkeypatch: pytest.MonkeyPatch) -> Callable[[Handler], list[httpx.Request]]:
    """Бот настроен; ответ Telegram задаёт тест. Возвращает список запросов."""
    monkeypatch.setattr("backend.config.alerts.TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr("backend.config.alerts.TELEGRAM_CHAT_ID", CHAT)

    def answer_with(handler: Handler) -> list[httpx.Request]:
        seen: list[httpx.Request] = []

        def record(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return handler(request)

        monkeypatch.setattr(
            alerts, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(record))
        )
        return seen

    return answer_with


def _ok(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})


@pytest.fixture
def printed() -> Iterator[io.StringIO]:
    """Журнал так, как его печатает сервис: наш обработчик на корневом
    логгере, уровень INFO — тот, на котором httpx пишет адрес запроса."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(TextFormatter())
    handler.addFilter(RunIdFilter())
    root = logging.getLogger()
    before = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    try:
        yield stream
    finally:
        root.removeHandler(handler)
        root.setLevel(before)


async def test_alert_reaches_the_bot_api(bot: Callable[[Handler], list[httpx.Request]]) -> None:
    seen = bot(_ok)

    sent = await alerts.send_alert("прогон №22 остановлен: кап выбран")

    assert sent is True
    assert len(seen) == 1
    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert json.loads(request.content) == {
        "chat_id": CHAT,
        "text": "outreach-donors: прогон №22 остановлен: кап выбран",
    }


async def test_unconfigured_alert_is_a_loud_line_not_a_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Бот не настроен (так в тестах всегда — conftest): тревога не падает
    и не молчит — строка ERROR с текстом, чтобы событие не пропало."""

    def no_network() -> httpx.AsyncClient:
        raise AssertionError("без токена в сеть ходить незачем")

    monkeypatch.setattr(alerts, "_client", no_network)

    sent = await alerts.send_alert("бэкап не снят")

    assert sent is False
    loud = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(loud) == 1
    assert "ТРЕВОГА НЕ ОТПРАВЛЕНА" in loud[0].getMessage()
    assert "ALERT_TELEGRAM_BOT_TOKEN" in loud[0].getMessage()
    assert "outreach-donors: бэкап не снят" in loud[0].getMessage()


async def test_token_never_reaches_the_log(
    bot: Callable[[Handler], list[httpx.Request]], printed: io.StringIO
) -> None:
    """httpx печатает адрес каждого запроса на INFO, а адрес Bot API несёт
    токен. Без фильтра строка «HTTP Request: POST …/bot<токен>/…» лежала бы
    в журнале каждого контейнера."""
    bot(_ok)

    await alerts.send_alert("проверка")

    log = printed.getvalue()
    assert "HTTP Request: POST" in log, "строка httpx есть — проверяется то, что она печатает"
    assert TOKEN not in log
    assert f"/bot{alerts.HIDDEN}/sendMessage" in log


async def test_network_failure_is_loud_and_hides_the_token(
    bot: Callable[[Handler], list[httpx.Request]], printed: io.StringIO
) -> None:
    """Текст исключения httpx бывает с адресом — и значит, с токеном."""

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"не соединиться с {request.url}", request=request)

    bot(unreachable)

    sent = await alerts.send_alert("проверка")

    assert sent is False
    log = printed.getvalue()
    assert "ТРЕВОГА НЕ ОТПРАВЛЕНА — Telegram недоступен (ConnectError" in log
    assert TOKEN not in log
    assert "outreach-donors: проверка" in log, "текст тревоги не пропадает вместе с ней"


@pytest.mark.parametrize(
    ("status", "description", "advice"),
    [
        (400, "Bad Request: chat not found", "ALERT_TELEGRAM_CHAT_ID"),
        (401, "Unauthorized", "@BotFather"),
        (403, "Forbidden: bot was kicked from the group chat", "вернуть его в чат"),
    ],
)
async def test_refusal_says_what_to_do(
    bot: Callable[[Handler], list[httpx.Request]],
    printed: io.StringIO,
    status: int,
    description: str,
    advice: str,
) -> None:
    bot(
        lambda _r: httpx.Response(
            status, json={"ok": False, "error_code": status, "description": description}
        )
    )

    sent = await alerts.send_alert("проверка")

    assert sent is False
    log = printed.getvalue()
    assert f"Telegram отказал ({status}: {description})" in log
    assert advice in log
    assert TOKEN not in log


async def test_page_of_a_proxy_is_not_a_delivery(
    bot: Callable[[Handler], list[httpx.Request]], printed: io.StringIO
) -> None:
    """Код 200 без `ok` — не доставка: прокси перед Telegram отвечает 200
    своей страницей, и такая тревога не дошла бы молча."""
    bot(lambda _r: httpx.Response(200, text="<html>вход в сеть отеля</html>"))

    assert await alerts.send_alert("проверка") is False
    assert "ALERT_TELEGRAM_API" in printed.getvalue()


async def test_alert_never_raises(bot: Callable[[Handler], list[httpx.Request]]) -> None:
    """Тревогу зовут из закрытия прогона: её исключение подменило бы
    причину остановки своей."""

    def broken(_request: httpx.Request) -> httpx.Response:
        raise RuntimeError("что угодно")

    bot(broken)

    assert await alerts.send_alert("проверка") is False


async def test_long_text_is_cut_to_the_bot_limit(
    bot: Callable[[Handler], list[httpx.Request]],
) -> None:
    """Длиннее 4096 знаков Telegram отказывает целиком — и тревога не ушла
    бы вовсе из-за собственной подробности."""
    seen = bot(_ok)

    await alerts.send_alert("я" * 5000)

    assert len(json.loads(seen[0].content)["text"]) == alerts.MAX_CHARS
