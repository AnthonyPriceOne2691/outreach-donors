"""Резидентные прокси обхода: список из настройки, смена на следующий и счёт трафика.

Требование Этапа 2 — при доле отказов (403, 429, 5xx, таймаут) выше 10% за последние
500 запросов сбавить темп вдвое и сменить прокси. До 12.10.2026 прокси был одним адресом
из настройки, и сменить его было не на что: сайт, закрывшийся от адреса прокси, оставался
закрытым до конца обхода, а мы платили за каждый отказ.

**Смена — по кругу, по двум поводам.** Отказ через прокси (403, 429, 5xx, обрыв) — его
адрес сайт уже узнал, следующая страница идёт через следующий. Просевшее здоровье обхода
(`walk._react`) — то же решение, принятое по окну в 500 запросов, а не по одной странице.
Прокси один — сменить не на что, и это не ошибка: уровень работает как прежде.

**Все прокси отказали подряд — пометка в записи обхода**, а не только в журнале:
круг закончился, и следующий отказ уже ничего не меняет.

**Адрес прокси несёт логин и пароль.** Битый адрес не доходит сюда вовсе — отказ
на старте обходчика (`config/crawl.proxy_problem`, `check_crawl`). В запись обхода,
журнал и тексты отказов уходит только имя `№2 (gate.example:7777)`; текст исключения,
где адрес может встретиться целиком, чистится по разобранному адресу (`scrub`), а если
транспорт не собрался — наружу идёт только вид исключения.

**Трафик считается по проводу и у неудавшихся вызовов** (`TrafficMeter`): строка
и заголовки запроса, строка, заголовки и тело ответа — каким оно пришло, сжатым, — по
каждому шагу редиректа, и счёт идёт по ходу чтения. Обрыв посреди тела — провайдер
за прочитанное взял, и журнал его видит. Рукопожатие TLS на уровне HTTP не видно:
провайдер насчитает на несколько процентов больше.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable, Sequence
from urllib.parse import unquote, urlsplit

import httpx

from backend.config import crawl as cfg
from backend.config.crawl import proxy_problem
from backend.shared.net.url_guard import GuardedTransport

logger = logging.getLogger(__name__)

#: Версия протокола и разделители строки: « HTTP/1.1\r\n» у запроса, «HTTP/1.1 » и «\r\n»
#: у статуса ответа, пустая строка после заголовков — у обоих.
_REQUEST_LINE = len(" HTTP/1.1\r\n") + 1 + len("\r\n")
_STATUS_LINE = len("HTTP/1.1 200 \r\n") + len("\r\n")

#: Короче этого кусок адреса не вычищается: логин «a» иначе стёр бы каждую «a» в тексте,
#: а пароль в три знака не секрет, а опечатка настройки.
MIN_SECRET = 4


class ProxyUnusableError(RuntimeError):
    """Адрес прокси из настройки не собирается в транспорт: опечатка или схема,
    для которой не установлен пакет. Текст — без логина и пароля."""


def proxy_name(position: int, url: str) -> str:
    """Имя прокси для людей и журнала: номер в списке и хост с портом — без логина.

    У битого адреса хост — догадка разбора, и ею бывает кусок пароля («user:p@ss#…»):
    такой адрес называется только номером.
    """
    number = f"№{position + 1}"
    if proxy_problem(url) is not None:
        return number
    parts = urlsplit(url)
    port = f":{parts.port}" if parts.port else ""
    return f"{number} ({parts.hostname}{port})"


def _secrets(url: str) -> list[str]:
    """Что вычищать из текста: адрес целиком, его часть до хоста, логин и пароль — как
    в адресе и раскодированными. Длинные — первыми, короче `MIN_SECRET` — нет."""
    netloc = urlsplit(url).netloc
    userinfo = netloc.rpartition("@")[0]
    login, _, password = userinfo.partition(":")
    found = {url, netloc if userinfo else "", userinfo, login, password}
    found |= {unquote(part) for part in (userinfo, login, password)}
    return sorted((part for part in found if len(part) >= MIN_SECRET), key=len, reverse=True)


def _headers_size(headers: httpx.Headers) -> int:
    return sum(len(name) + len(value) + 4 for name, value in headers.raw)


class TrafficMeter:
    """Счётчик байт через прокси между двумя `take`."""

    def __init__(self) -> None:
        self.counted = 0

    def take(self) -> int:
        """Отдать насчитанное и начать заново."""
        taken, self.counted = self.counted, 0
        return taken


class _MeteredStream(httpx.AsyncByteStream):
    """Тело ответа: каждый кусок — в счёт сразу, как прочитан, а не в конце."""

    def __init__(self, stream: httpx.AsyncByteStream, meter: TrafficMeter) -> None:
        self._stream = stream
        self._meter = meter

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self._stream:
            self._meter.counted += len(chunk)
            yield chunk

    async def aclose(self) -> None:
        await self._stream.aclose()


class MeteredTransport(httpx.AsyncBaseTransport):
    """Транспорт, считающий байты по проводу у каждого шага, удачного или нет.

    Запрос — в счёт до отправки: оборванный вызов свой запрос уже отдал. Адрес — целиком:
    прокси получает запрос в абсолютной форме (`GET http://…`).
    """

    def __init__(self, inner: httpx.AsyncBaseTransport, meter: TrafficMeter) -> None:
        self._inner = inner
        self._meter = meter

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self._meter.counted += len(request.method) + len(str(request.url)) + _REQUEST_LINE
        self._meter.counted += _headers_size(request.headers)
        response = await self._inner.handle_async_request(request)
        reason = response.extensions.get("reason_phrase", b"")
        if not isinstance(reason, bytes) or not reason:
            reason = httpx.codes.get_reason_phrase(response.status_code).encode()
        self._meter.counted += _STATUS_LINE + len(reason) + _headers_size(response.headers)
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            stream=_MeteredStream(response.stream, self._meter),  # type: ignore[arg-type]
            extensions=response.extensions,
            request=request,
        )

    async def aclose(self) -> None:
        await self._inner.aclose()


def proxy_transport(url: str) -> httpx.AsyncBaseTransport:
    """Транспорт через прокси — с защитой исходящих, как у обычного уровня: адреса страниц
    приходят снаружи. Проверка сертификата выключена по той же причине, что и там
    (`url_guard.guarded_client`): у доноров она протухает постоянно."""
    return GuardedTransport(httpx.AsyncHTTPTransport(proxy=url, verify=False))


class ProxyPool:
    """Список прокси, текущий из них, смена на следующий по кругу и счёт трафика.

    Клиент — один, у текущего прокси: при смене прежний закрывается при следующем
    запросе, а не в момент смены — смену зовут и из синхронного кода обхода.
    """

    def __init__(
        self,
        urls: Sequence[str] | None = None,
        *,
        timeout: float | None = None,
        transport: Callable[[str], httpx.AsyncBaseTransport] | None = None,
    ) -> None:
        listed = cfg.PROXY_URLS if urls is None else urls
        self._urls = list(dict.fromkeys(url.strip() for url in listed if url.strip()))
        self._timeout = timeout if timeout is not None else cfg.PAGE_TIMEOUT_SEC
        self._transport = transport or proxy_transport
        self._at = 0
        self._open: tuple[str, httpx.AsyncClient] | None = None
        self._failed_in_row = 0
        self.meter = TrafficMeter()
        self.switches = 0

    def __bool__(self) -> bool:
        return bool(self._urls)

    def __len__(self) -> int:
        return len(self._urls)

    @property
    def name(self) -> str:
        """Имя текущего прокси — без логина и пароля."""
        return proxy_name(self._at, self._urls[self._at]) if self._urls else "—"

    def scrub(self, text: str) -> str:
        """Текст без адресов, логинов и паролей прокси — по разобранному адресу: сообщение
        httpx может нести адрес целиком, а запись обхода и журнал читают люди."""
        for url in self._urls:
            for secret in _secrets(url):
                text = text.replace(secret, "***")
        return text

    def rotate(self, why: str) -> bool:
        """Перейти к следующему прокси. `False` — менять не на что: прокси один."""
        if len(self._urls) < 2:
            return False
        before = self.name
        self._at = (self._at + 1) % len(self._urls)
        self.switches += 1
        # Смена — штатный ход, не тревога: громко говорит пометка «отказали все» в отчёте.
        logger.info(
            "обход: смена прокси %s → %s — %s",
            before,
            self.name,
            why,
            extra={"proxy_from": before, "proxy_to": self.name, "why": why},
        )
        return True

    def failed(self, why: str) -> bool:
        """Отказ через текущий прокси: смена на следующий. `True` — отказали подряд все."""
        self._failed_in_row += 1
        self.rotate(why)
        return self._failed_in_row >= len(self._urls)

    def succeeded(self) -> None:
        self._failed_in_row = 0

    async def client(self) -> httpx.AsyncClient:
        """Клиент текущего прокси — с защитой исходящих и счётом трафика."""
        url = self._urls[self._at]
        if self._open is not None and self._open[0] == url:
            return self._open[1]
        await self.aclose()
        try:
            transport = self._transport(url)
        except Exception as exc:  # noqa: BLE001 — текст любого несёт адрес с паролем
            # У `InvalidURL` — и кусок пароля, разрезанного «#»: наружу — только имя прокси
            # и вид исключения, текст его не идёт никуда.
            logger.debug("обход: транспорт прокси %s не собрался (%s)", self.name, type(exc))
            raise ProxyUnusableError(
                f"адрес прокси {self.name} не принят ({type(exc).__name__}): "
                "схема http, https или socks5 — последняя с пакетом httpx[socks]"
            ) from None
        client = httpx.AsyncClient(
            transport=MeteredTransport(transport, self.meter), timeout=self._timeout
        )
        self._open = (url, client)
        return client

    async def aclose(self) -> None:
        if self._open is not None:
            await self._open[1].aclose()
            self._open = None
