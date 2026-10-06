"""Бот продаж в Telegram — срез 5.3, T2: три попытки, отказы словами, токен не утекает.

Сеть не ходит: транспорт httpx подменён и записывает запросы. Журнал настоящий —
строка логгера httpx проходит тот же путь, что на сервере, и токен ищется в том,
что напечатано. Номера чатов и токен — выдуманные.
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Callable, Iterator

import httpx
import pytest
from backend.cli import sales_telegram as cli
from backend.cli.main import _COMMANDS, _KEPT_ON_INTERRUPT, build_parser
from backend.config import sales as cfg
from backend.features.sales import telegram
from backend.features.sales.telegram import Chat, SalesBot, TelegramError
from backend.shared.logs.setup import RunIdFilter, TextFormatter

TOKEN = "7310452968:AAF-sales_token-for-tests"  # pragma: allowlist secret
PERSONAL = "583920471"
GROUP = "-1009384756102"

Handler = Callable[[httpx.Request], httpx.Response]


class Recorder:
    """Подставной Bot API: отвечает по очереди заданными ответами и помнит запросы."""

    def __init__(self, *answers: httpx.Response | Exception) -> None:
        self.answers = list(answers)
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))


def ok(result: object = None) -> httpx.Response:
    answer = {"message_id": 41} if result is None else result
    return httpx.Response(200, json={"ok": True, "result": answer})


def refused(code: int, description: str = "Bad Request: chat not found") -> httpx.Response:
    return httpx.Response(code, json={"ok": False, "error_code": code, "description": description})


@pytest.fixture
def token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "TELEGRAM_BOT_TOKEN", TOKEN)


@pytest.fixture
def pauses(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(telegram, "_sleep", sleep)
    return slept


@pytest.fixture
def printed() -> Iterator[io.StringIO]:
    """Журнал так, как его печатает сервис, на уровне INFO — там httpx пишет адрес."""
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


def test_suite_never_carries_a_real_bot() -> None:
    """Страховка набора: `.env` разработчика с живым ботом продаж не доходит до тестов."""
    assert (cfg.TELEGRAM_BOT_TOKEN, cfg.TELEGRAM_CHAT_ID, cfg.TELEGRAM_GROUP_CHAT_ID) == (
        "",
        "",
        "",
    )


@pytest.mark.usefixtures("token")
async def test_message_reaches_bot_api_once(pauses: list[float]) -> None:
    api = Recorder(ok())
    async with api.client() as http:
        await SalesBot(http).send(PERSONAL, "Новый лид\nЭмейл Рассылка\nСсылка")
    assert len(api.seen) == 1
    assert str(api.seen[0].url) == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    body = json.loads(api.seen[0].content)
    assert (body["chat_id"], body["text"]) == (PERSONAL, "Новый лид\nЭмейл Рассылка\nСсылка")
    assert pauses == []


@pytest.mark.usefixtures("token")
async def test_a4_three_attempts_then_temporary_refusal(pauses: list[float]) -> None:
    api = Recorder(refused(503, "Service Unavailable"))
    async with api.client() as http:
        with pytest.raises(TelegramError) as caught:
            await SalesBot(http).send(PERSONAL, "текст")
    assert len(api.seen) == 3
    assert pauses == [2.0, 5.0]
    assert caught.value.permanent is False
    assert "HTTP 503" in str(caught.value)
    assert "3 попытки" in str(caught.value)


@pytest.mark.usefixtures("token")
async def test_third_attempt_that_passes_is_success(pauses: list[float]) -> None:
    api = Recorder(refused(502, "Bad Gateway"), httpx.ReadTimeout("медленно"), ok())
    async with api.client() as http:
        await SalesBot(http).send(PERSONAL, "текст")
    assert len(api.seen) == 3
    assert pauses == [2.0, 5.0]


@pytest.mark.usefixtures("token")
@pytest.mark.parametrize(
    ("code", "advice"),
    [
        (400, "sales-telegram-chat-id"),
        (401, "SALES_TELEGRAM_BOT_TOKEN"),
        (403, "Start"),
        (404, "SALES_TELEGRAM_BOT_TOKEN"),
    ],
)
async def test_refusal_is_not_repeated_and_says_what_to_do(
    code: int, advice: str, pauses: list[float]
) -> None:
    api = Recorder(refused(code, "Forbidden: bot was blocked by the user"))
    async with api.client() as http:
        with pytest.raises(TelegramError) as caught:
            await SalesBot(http).send(PERSONAL, "текст")
    assert len(api.seen) == 1
    assert pauses == []
    assert caught.value.permanent is True
    assert advice in str(caught.value)
    assert TOKEN not in str(caught.value)


@pytest.mark.usefixtures("token")
async def test_token_from_httpx_error_never_reaches_the_text(pauses: list[float]) -> None:
    """Текст ошибки httpx несёт адрес запроса, а в адресе Bot API — токен."""
    leak = httpx.ConnectError(f"cannot reach https://api.telegram.org/bot{TOKEN}/sendMessage")
    api = Recorder(leak)
    async with api.client() as http:
        with pytest.raises(TelegramError) as caught:
            await SalesBot(http).send(PERSONAL, "текст")
    assert TOKEN not in str(caught.value)
    assert "ConnectError" in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True


@pytest.mark.usefixtures("token")
async def test_short_wait_asked_by_telegram_is_kept(pauses: list[float]) -> None:
    flood = httpx.Response(
        429,
        json={"ok": False, "description": "Too Many Requests", "parameters": {"retry_after": 3}},
    )
    api = Recorder(flood, ok())
    async with api.client() as http:
        await SalesBot(http).send(PERSONAL, "текст")
    assert pauses == [3.0]


@pytest.mark.usefixtures("token")
async def test_long_wait_asked_by_telegram_is_a_refusal_not_a_sleep(pauses: list[float]) -> None:
    flood = httpx.Response(
        429,
        json={"ok": False, "description": "Too Many Requests", "parameters": {"retry_after": 317}},
    )
    api = Recorder(flood)
    async with api.client() as http:
        with pytest.raises(TelegramError, match="317") as caught:
            await SalesBot(http).send(PERSONAL, "текст")
    assert pauses == []
    assert len(api.seen) == 1
    assert caught.value.permanent is False


@pytest.mark.usefixtures("token")
async def test_page_that_is_not_bot_api_is_named(pauses: list[float]) -> None:
    api = Recorder(httpx.Response(200, text="<html>портал сети</html>"))
    async with api.client() as http:
        with pytest.raises(TelegramError, match="не похож на Bot API") as caught:
            await SalesBot(http).send(PERSONAL, "текст")
    assert caught.value.permanent is True
    assert pauses == []


async def test_unconfigured_bot_refuses_without_a_request(pauses: list[float]) -> None:
    api = Recorder(ok())
    async with api.client() as http:
        with pytest.raises(TelegramError, match="SALES_TELEGRAM_BOT_TOKEN") as caught:
            await SalesBot(http).send(PERSONAL, "текст")
    assert api.seen == []
    assert caught.value.permanent is True


@pytest.mark.usefixtures("token")
async def test_empty_chat_refuses_without_a_request() -> None:
    api = Recorder(ok())
    async with api.client() as http:
        with pytest.raises(TelegramError, match="SALES_TELEGRAM_CHAT_ID"):
            await SalesBot(http).send("", "текст")
    assert api.seen == []


@pytest.mark.usefixtures("token")
async def test_httpx_log_line_hides_the_token(printed: io.StringIO) -> None:
    api = Recorder(ok())
    async with api.client() as http:
        await SalesBot(http).send(PERSONAL, "текст")
    log = printed.getvalue()
    assert "sendMessage" in log, "строка httpx о запросе должна быть — иначе проверять нечего"
    assert TOKEN not in log
    assert telegram.HIDDEN in log


UPDATES = [
    {
        "update_id": 902117,
        "message": {
            "message_id": 1,
            "text": "/start",
            "chat": {
                "id": 583920471,
                "type": "private",
                "first_name": "Тест",
                "last_name": "Тестов",
            },
        },
    },
    {
        "update_id": 902118,
        "my_chat_member": {
            "chat": {"id": -1009384756102, "type": "supergroup", "title": "Выдуманная группа"}
        },
    },
    {
        "update_id": 902119,
        "message": {"message_id": 2, "text": "ещё", "chat": {"id": 583920471, "type": "private"}},
    },
    {"update_id": 902120, "poll": {"id": "x"}},
]


@pytest.mark.usefixtures("token")
async def test_chats_are_read_once_and_each_is_named_once() -> None:
    api = Recorder(ok(UPDATES))
    async with api.client() as http:
        found = await SalesBot(http).chats()
    assert len(api.seen) == 1
    assert str(api.seen[0].url).endswith("/getUpdates")
    assert found == [
        Chat(583920471, "private", "Тест Тестов"),
        Chat(-1009384756102, "supergroup", "Выдуманная группа"),
    ]


@pytest.mark.usefixtures("token")
async def test_webhook_on_the_bot_is_named() -> None:
    api = Recorder(refused(409, "Conflict: can't use getUpdates method while webhook is active"))
    async with api.client() as http:
        with pytest.raises(TelegramError, match="deleteWebhook"):
            await SalesBot(http).chats()


@pytest.mark.usefixtures("token")
async def test_updates_of_unknown_shape_are_a_loud_refusal() -> None:
    api = Recorder(ok({"not": "a list"}))
    async with api.client() as http:
        with pytest.raises(TelegramError, match="getUpdates"):
            await SalesBot(http).chats()


# --- команда `outreach sales-telegram-chat-id` ------------------------------------------------


def _command(monkeypatch: pytest.MonkeyPatch, api: Recorder) -> None:
    monkeypatch.setattr(cli, "_client", api.client)


@pytest.mark.usefixtures("token")
async def test_command_prints_chat_ids(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _command(monkeypatch, Recorder(ok(UPDATES)))
    assert await cli.cmd_sales_telegram_chat_id(None) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "583920471\tprivate\tТест Тестов" in out
    assert "-1009384756102\tsupergroup\tВыдуманная группа" in out
    assert "SALES_TELEGRAM_CHAT_ID" in out


@pytest.mark.usefixtures("token")
async def test_command_without_updates_says_who_has_to_press_start(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _command(monkeypatch, Recorder(ok([])))
    assert await cli.cmd_sales_telegram_chat_id(None) == cli.EXIT_NOBODY
    assert "Start" in capsys.readouterr().out


async def test_command_without_token_is_a_config_refusal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = Recorder(ok(UPDATES))
    _command(monkeypatch, api)
    assert await cli.cmd_sales_telegram_chat_id(None) == cli.EXIT_NOT_CONFIGURED
    assert "SALES_TELEGRAM_BOT_TOKEN" in capsys.readouterr().err
    assert api.seen == []


@pytest.mark.usefixtures("token")
async def test_command_names_telegram_refusal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _command(monkeypatch, Recorder(refused(401, "Unauthorized")))
    assert await cli.cmd_sales_telegram_chat_id(None) == cli.EXIT_REFUSED
    err = capsys.readouterr().err
    assert "HTTP 401" in err
    assert TOKEN not in err


def test_command_is_registered_in_the_console() -> None:
    """Команда — в перечне продаж (`cli/sales_commands.py`), `main.py` берёт его целиком.
    Прерывание говорит правду: команда только читает — а не «домены остались в базе»."""
    assert "sales-telegram-chat-id" in _COMMANDS
    assert build_parser().parse_args(["sales-telegram-chat-id"]).command == "sales-telegram-chat-id"
    assert "ничего не изменилось" in _KEPT_ON_INTERRUPT["sales-telegram-chat-id"]
