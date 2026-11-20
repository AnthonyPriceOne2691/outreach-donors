"""Защита исходящих запросов: чужой адрес не должен увести нас внутрь сети.

Сервис ходит за страницами по адресам, которые пришли снаружи: из выдачи,
из ссылок на чужой странице, а с появлением интерфейса — из поля ввода
оператора. Без проверки это дыра: адрес вида `http://169.254.169.254/`
заставляет сервер сходить к метаданным облака и принести оттуда ключи
доступа. На ноутбуке такого адреса нет, на сервере он есть всегда.

**Редирект опаснее прямого адреса.** Проверить адрес на входе мало:
публичный сайт отвечает `302` на внутренний адрес, и клиент послушно идёт
туда — уже без всякой проверки. Поэтому проверка живёт в транспорте и
срабатывает на каждом шаге цепочки, а не один раз перед запросом.

**Размер ответа тоже ограничен — по распакованным байтам.** Чужой сайт может отдавать
гигабайты; без потолка это память сервера, а не наша страница с контактами. Сжатый ответ
опаснее: 29 КБ gzip разворачиваются в 30 МБ, а распаковку httpx делает выше транспорта,
и до 20.11.2026 её ничто не ограничивало (потолок в 1 МБ — прочитано 30 000 033 байт).
Поэтому gzip и deflate распаковывает сам транспорт, шагами не длиннее `INFLATE_STEP`,
и потолок считает уже распакованное; httpx получает ответ без `Content-Encoding` и второй
раз не распаковывает. Просим у сайта только эти два сжатия: br и zstd распаковать
с пределом нечем, и непрошеное сжатие с непустым телом — отказ словами
(`UnboundedEncodingError`).

**Имя распознаётся в потоке, а не в цикле событий.** `getaddrinfo` блокирует:
до 14.10.2026 он шёл прямо в транспорте, и пока система искала имя одного сайта,
стояли все параллельные запросы процесса — судья главных, поиск адресов, обход.
Проверка та же и на том же месте, перед каждым шагом цепочки; ждёт DNS только
свой запрос.

**Отказ распознавания имени считается безопасным.** Имя не разрешилось —
пропускаем: иначе временный сбой DNS остановил бы сбор по живым донорам.
Настоящую атаку это не пропускает: в ней имя обязано разрешиться
во внутренний адрес, иначе соединение не состоится.

**Открытое соединение проверяется тоже.** Имя распознаётся дважды: проверкой
и библиотекой при соединении, — и ответ DNS между ними может смениться: имя с нулевым
сроком жизни отвечает проверке публичным адресом, а соединению внутренним. Поэтому
адрес смотрится и у открытого соединения, до первого байта запроса (`_PublicPeerOnly`):
подменять к этому моменту нечего. Через прокси адрес знает только сам прокси, а сеть
за ним не наша: у него этой проверки нет.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import zlib
from collections.abc import AsyncGenerator, AsyncIterator, Iterable, Iterator
from contextlib import aclosing
from urllib.parse import urlparse

import httpcore
import httpx

from backend.shared.net.charset import page_encoding

logger = logging.getLogger(__name__)

#: Сколько байт ответа читаем максимум — распакованных, а не пришедших по сети.
#: Страница контактов в этот размер укладывается с большим запасом.
MAX_RESPONSE_BYTES = 10 * 1024 * 1024

#: Больше этого распаковка за один шаг не выдаёт (и не больше потолка ответа): 64 КБ deflate
#: разворачиваются до ~64 МБ за один вызов, если вызов не ограничен.
INFLATE_STEP = 64 * 1024

#: Сжатия, которые транспорт распаковывает сам, с пределом на шаг, — окно zlib для каждого.
_WBITS = {"gzip": zlib.MAX_WBITS | 16, "x-gzip": zlib.MAX_WBITS | 16, "deflate": zlib.MAX_WBITS}

#: Что просим у сайта, если вызывающий не оставил своего из того, что мы умеем.
ACCEPT_ENCODING = "gzip, deflate"

ALLOWED_SCHEMES = frozenset({"http", "https"})

#: Имена, за которыми стоят служебные адреса. Часть из них не разрешается
#: обычным способом, часть подменяется настройками машины — поэтому список
#: проверяется до распознавания имени.
BLOCKED_HOSTS = frozenset(
    {
        "localhost",
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
        "169.254.169.254",  # метаданные в облаках трёх крупнейших провайдеров
        "100.100.100.200",
        "fd00:ec2::254",
    }
)


class UnsafeUrlError(ValueError):
    """Адрес не прошёл проверку: чужая схема или внутренний адрес."""


class UnboundedEncodingError(httpx.DecodingError):
    """Сайт сжал ответ тем, что мы не просили и не умеем распаковать с пределом (br, zstd, …).

    Наследник `httpx.DecodingError`: для вызывающих это обычная неоткрывшаяся страница —
    обход, поиск адресов и судьи ловят `httpx.HTTPError` и пишут причину словами.
    """


def _is_private(address: str) -> bool:
    """Внутренний ли это адрес. Не адрес вовсе — значит, это имя, и его
    проверит распознавание."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        logger.debug("url-guard: %r не похоже на адрес — проверяем как имя", address)
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        # Общая сеть операторов 100.64.0.0/10: `is_private` её не видит, а метаданные облака там.
        or not ip.is_global
    )


def _is_address(name: str) -> bool:
    """Адрес ли это, а не имя: адрес проверен как есть, распознавать нечего."""
    try:
        ipaddress.ip_address(name)
    except ValueError:
        logger.debug("url-guard: %r — имя, а не адрес: распознаём", name)
        return False
    return True


def _resolved_addresses(host: str) -> list[str]:
    """Все адреса имени. Пустой список — имя не разрешилось."""
    try:
        return [str(info[4][0]) for info in socket.getaddrinfo(host, None)]
    except (OSError, UnicodeError) as exc:
        logger.debug("url-guard: имя %s не разрешилось (%r) — считаем безопасным", host, exc)
        return []


def assert_safe_url(url: str | httpx.URL) -> None:
    """Проверить один адрес. Бросает `UnsafeUrlError`, если ходить туда нельзя."""
    parsed = urlparse(str(url))
    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"схема {scheme or 'без схемы'} не разрешена: {url}")

    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise UnsafeUrlError(f"в адресе нет хоста: {url}")
    if host in BLOCKED_HOSTS:
        raise UnsafeUrlError(f"служебное имя {host} запрещено")
    if _is_private(host):
        raise UnsafeUrlError(f"внутренний адрес {host} запрещён")

    for address in _resolved_addresses(host):
        if _is_private(address):
            raise UnsafeUrlError(f"имя {host} ведёт на внутренний адрес {address}")


def checked_addresses(host: str) -> list[str]:
    """Адреса имени, прошедшие ту же проверку, — чтобы соединяться по ним, а не по имени.

    Для того, кто соединяется сам (прокси браузера, `contacts/browser_proxy.py`): имя
    распознаётся один раз, и соединение идёт на проверенный адрес — второе распознавание
    могло бы вернуть уже другой, внутренний (подмена DNS между проверкой и соединением).
    В отличие от `assert_safe_url`, нераспознанное имя — отказ: соединяться не с чем.
    """
    name = host.strip().lower().rstrip(".").strip("[]")
    if not name:
        raise UnsafeUrlError(f"в адресе нет хоста: {host!r}")
    if name in BLOCKED_HOSTS:
        raise UnsafeUrlError(f"служебное имя {name} запрещено")
    if _is_private(name):
        raise UnsafeUrlError(f"внутренний адрес {name} запрещён")
    if _is_address(name):
        return [name]
    addresses = _resolved_addresses(name)
    if not addresses:
        raise UnsafeUrlError(f"имя {name} не распознаётся")
    for address in addresses:
        if _is_private(address):
            raise UnsafeUrlError(f"имя {name} ведёт на внутренний адрес {address}")
    return addresses


class _Inflater:
    """Распаковка gzip/deflate выдачами не длиннее шага: бомба не разворачивается за один вызов.

    `decompress(data, step)` останавливается на `step` байтах выхода, непрочитанный вход
    кладёт в `unconsumed_tail`; следующая выдача — только когда её попросят. Перестали
    просить (потолок ответа) — остаток не распаковывается вовсе.
    """

    def __init__(self, coding: str, step: int) -> None:
        self._coding = coding
        self._step = step
        self._zlib = zlib.decompressobj(_WBITS[coding])
        self._fresh = True  # ещё ни одного шага: deflate без обёртки zlib пробуется заново

    def feed(self, data: bytes) -> Iterator[bytes]:
        """Кусок тела с сети → распакованные выдачи по одной за шаг."""
        while not self._zlib.eof:
            piece = self._inflate(data)
            data = self._zlib.unconsumed_tail
            if piece:
                yield piece
            # Выдача короче шага и вход съеден — у zlib больше ничего нет до следующего куска.
            if not data and len(piece) < self._step:
                return

    def finish(self) -> Iterator[bytes]:
        """Хвост после последнего куска. Вход уже съеден `feed`, так что хвост — крохи."""
        try:
            tail = self._zlib.flush()
        except zlib.error as exc:
            raise httpx.DecodingError(f"сжатый ответ ({self._coding}) оборван: {exc}") from exc
        if tail:
            yield tail

    def _inflate(self, data: bytes) -> bytes:
        try:
            piece = self._zlib.decompress(data, self._step)
        except zlib.error as exc:
            if self._fresh and self._coding == "deflate":
                # «deflate» без обёртки zlib: так шлют многие серверы, httpx пробует так же.
                self._fresh = False
                self._zlib = zlib.decompressobj(-zlib.MAX_WBITS)
                return self._inflate(data)
            raise httpx.DecodingError(
                f"сжатый ответ ({self._coding}) не распаковывается: {exc}"
            ) from exc
        self._fresh = False
        return piece


class _Refused:
    """Сжатие, которое не распаковать с пределом: пустое тело (редирект, HEAD) пропускаем,
    непустое — отказ словами."""

    def __init__(self, message: str) -> None:
        self._message = message

    def feed(self, data: bytes) -> Iterator[bytes]:
        if data:
            raise UnboundedEncodingError(self._message)
        return iter(())

    def finish(self) -> Iterator[bytes]:
        return iter(())


def _decoder_for(
    response: httpx.Response, url: httpx.URL, step: int
) -> _Inflater | _Refused | None:
    """Чем распаковывать тело ответа. `None` — тело не сжато."""
    codings = [
        coding.strip().lower()
        for coding in response.headers.get_list("content-encoding", split_commas=True)
    ]
    codings = [coding for coding in codings if coding not in ("", "identity")]
    if len(codings) == 1 and codings[0] in _WBITS:
        return _Inflater(codings[0], step)
    if not codings:
        return None
    return _Refused(
        f"сайт сжал ответ {url} в «{', '.join(codings)}»: такое сжатие мы не просили "
        "и распаковать с пределом не можем"
    )


def _bounded_accept(asked: str | None) -> str:
    """`Accept-Encoding` для сайта: из списка вызывающего — только то, что распаковываем с
    пределом. Не осталось ничего (httpx по умолчанию добавляет br и zstd, если они стоят) —
    `ACCEPT_ENCODING`."""
    kept = [
        part.strip()
        for part in (asked or "").split(",")
        if part.split(";")[0].strip().lower() in {*_WBITS, "identity"}
    ]
    return ", ".join(kept) or ACCEPT_ENCODING


def _without_encoding(headers: httpx.Headers) -> httpx.Headers:
    """Заголовки распакованного ответа: без сжатия и без длины сжатого тела — иначе httpx
    распакует второй раз."""
    return httpx.Headers(
        [
            (name, value)
            for name, value in headers.raw
            if name.lower() not in (b"content-encoding", b"content-length")
        ]
    )


class _CappedStream(httpx.AsyncByteStream):
    """Поток ответа с потолком по распакованным байтам: лишнее не читается и не распаковывается,
    а не отбрасывается после."""

    def __init__(
        self,
        stream: httpx.AsyncByteStream,
        limit: int,
        decoder: _Inflater | _Refused | None = None,
    ) -> None:
        self._stream = stream
        self._limit = limit
        self._decoder = decoder

    async def __aiter__(self) -> AsyncIterator[bytes]:
        read = 0
        async with aclosing(self._pieces()) as pieces:
            async for piece in pieces:
                read += len(piece)
                if read > self._limit:
                    logger.info("url-guard: ответ обрезан на %d байт", self._limit)
                    yield piece[: self._limit - read]
                    return
                yield piece

    async def _pieces(self) -> AsyncGenerator[bytes]:
        """Тело кусками — распакованными, если ответ сжат."""
        async for chunk in self._stream:
            if self._decoder is None:
                yield chunk
                continue
            for piece in self._decoder.feed(chunk):
                yield piece
        if self._decoder is not None:
            for piece in self._decoder.finish():
                yield piece

    async def aclose(self) -> None:
        await self._stream.aclose()


class _PublicPeerOnly(httpcore.AnyIOBackend):
    """Соединение отдаётся запросу, только если на том конце публичный адрес.

    Выбор адреса и запасной путь между IPv6 и IPv4 остаются за библиотекой: проверяется то,
    с чем она в итоге соединилась. Адрес не назван — отказ: проверить нечего.
    """

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 — подпись задаёт библиотека
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        stream = await super().connect_tcp(
            host,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )
        peer = stream.get_extra_info("server_addr")
        address = str(peer[0]) if peer else ""
        if not _is_address(address) or _is_private(address):
            await stream.aclose()
            raise UnsafeUrlError(
                f"имя {host} привело соединение на адрес {address or 'неизвестен'}"
            )
        return stream


def _public_only_transport() -> httpx.AsyncHTTPTransport:
    """Обычный транспорт, но соединения открывает `_PublicPeerOnly`. Своей настройки для этого
    у httpx нет, а пул целиком пришлось бы собирать заново со всеми его умолчаниями. Имена
    внутри библиотек сменятся — упадёт тест на настоящем сокете (`tests/test_url_guard.py`)."""
    transport = httpx.AsyncHTTPTransport(verify=False)
    transport._pool._network_backend = _PublicPeerOnly()
    return transport


class GuardedTransport(httpx.AsyncBaseTransport):
    """Транспорт, проверяющий КАЖДЫЙ запрос, включая шаги редиректа.

    Проверка на входе фетчера защищает только от прямого адреса; шаг
    редиректа идёт мимо неё, потому что адрес там подставляет чужой сервер.
    """

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport | None = None,
        *,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
    ) -> None:
        self._inner = inner or _public_only_transport()
        self._limit = max_response_bytes

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await asyncio.to_thread(assert_safe_url, request.url)
        request.headers["Accept-Encoding"] = _bounded_accept(request.headers.get("Accept-Encoding"))
        response = await self._inner.handle_async_request(request)
        # Шаг не больше потолка и не ноль: для zlib `max_length=0` значит «без предела».
        decoder = _decoder_for(response, request.url, max(1, min(INFLATE_STEP, self._limit)))
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers if decoder is None else _without_encoding(response.headers),
            stream=_CappedStream(response.stream, self._limit, decoder),  # type: ignore[arg-type]
            extensions=response.extensions,
            request=request,
        )

    async def aclose(self) -> None:
        await self._inner.aclose()


def guarded_client(*, timeout: httpx.Timeout | float, **kwargs: object) -> httpx.AsyncClient:
    """Клиент для походов по чужим адресам.

    Проверка сертификата выключена намеренно: у доноров она протухает
    постоянно, а читаем мы публичные страницы и ничего им не передаём.
    Защита здесь от другого — от похода во внутреннюю сеть.

    Страницу без charset в заголовке клиент читает в кодировке из её `<meta>`
    (`charset.page_encoding`), а не всегда в utf-8; свой `default_encoding` — у вызывающего.
    """
    kwargs.setdefault("default_encoding", page_encoding)
    return httpx.AsyncClient(transport=GuardedTransport(), timeout=timeout, **kwargs)  # type: ignore[arg-type]
