"""Прокси обхода списком и смена на следующий (требование Этапа 2).

При доле отказов выше 10% за последние 500 запросов — темп вдвое ниже и смена прокси.
До 12.10.2026 прокси был одним адресом из настройки, и сменить его было не на что.
Проверяется: список из настройки, смена по кругу на отказе и на просевшем здоровье,
пометка «отказали все», логин и пароль прокси — нигде, трафик — по проводу, а платный
уровень без журнала расхода и сверх потолка не зовётся. Пусто в настройке — как прежде.
"""

from __future__ import annotations

import asyncio
import gzip
import logging
from collections.abc import Callable

import httpx
import pytest
from backend.config.crawl import _proxy_list
from backend.features.crawl.fetch import CascadeLevel, FetchOutcome, PageCascade
from backend.features.crawl.limiter import DomainLimiter
from backend.features.crawl.proxies import (
    MeteredTransport,
    ProxyPool,
    ProxyUnusableError,
    TrafficMeter,
    proxy_name,
)
from backend.features.crawl.spend import PROXY_OPERATION
from backend.features.crawl.walk import DonorCrawler

HTML = {"content-type": "text/html; charset=utf-8"}
PAGE = "<html><body><article>" + "Статья о ставках. " * 40 + "</article></body></html>"
FIRST = "http://user1:secret-one@gate-a.example.test:7777"  # pragma: allowlist secret
SECOND = "http://user2:secret-two@gate-b.example.test:8888"  # pragma: allowlist secret


class Ledger:
    """Журнал расхода в памяти: что спросили, что записали, и отказ потолка, если задан."""

    def __init__(self, refused: str | None = None) -> None:
        self.refused = refused
        self.asked: list[str] = []
        self.records: list[tuple[str, int]] = []

    async def refusal(self, operation: str) -> str | None:
        self.asked.append(operation)
        return self.refused

    async def spent(self, operation: str, units: int) -> None:
        self.records.append((operation, units))


class Proxies:
    """Прокси-шлюзы в памяти: у каждого свой ответ; журнал — через какой прокси что ушло."""

    def __init__(self, answers: dict[str, Callable[[httpx.Request], httpx.Response]]) -> None:
        self.answers = answers
        self.used: list[tuple[str, str]] = []

    def transport(self, url: str) -> httpx.AsyncBaseTransport:
        if url not in self.answers:
            raise ValueError(f"Unknown scheme for proxy URL URL('{url}')")

        def handler(request: httpx.Request) -> httpx.Response:
            self.used.append((proxy_name(list(self.answers).index(url), url), request.url.path))
            return self.answers[url](request)

        return httpx.MockTransport(handler)


def _blocked(_: httpx.Request) -> httpx.Response:
    return httpx.Response(403, text="нет", headers=HTML)


def _open(_: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text=PAGE, headers=HTML)


def _direct(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestSettings:
    def test_list_from_commas_and_lines_without_repeats_and_the_old_name_too(self) -> None:
        listed = _proxy_list(f"{FIRST}, {SECOND}\n{FIRST}", "http://gate-c.example.test:1")

        assert listed == (FIRST, SECOND, "http://gate-c.example.test:1")

    def test_empty_setting_is_no_level(self) -> None:
        assert _proxy_list("", "") == ()
        assert not ProxyPool([])


class TestPool:
    def test_names_carry_no_login_or_password(self) -> None:
        pool = ProxyPool([FIRST, SECOND])

        assert pool.name == "№1 (gate-a.example.test:7777)"
        cleaned = pool.scrub(f"ProxyError: 407 from {FIRST} as user1/secret-one")
        assert "secret-one" not in cleaned
        assert "user1" not in cleaned

    def test_rotation_goes_round_and_one_proxy_has_nowhere_to_go(self) -> None:
        pool = ProxyPool([FIRST, SECOND, "http://gate-c.example.test:9"])
        names = [pool.name]
        for _ in range(3):
            assert pool.rotate("тест")
            names.append(pool.name)

        assert [name.split()[0] for name in names] == ["№1", "№2", "№3", "№1"]
        assert pool.switches == 3
        alone = ProxyPool([FIRST])
        assert alone.rotate("тест") is False
        assert alone.name.startswith("№1")

    def test_all_failed_in_a_row_is_known_and_a_success_resets_it(self) -> None:
        pool = ProxyPool([FIRST, SECOND])

        assert pool.failed("код 403") is False
        pool.succeeded()
        assert pool.failed("код 403") is False
        assert pool.failed("код 403") is True

    async def test_bad_address_is_refused_without_the_password(self) -> None:
        pool = ProxyPool(["ftp://user:hunter2@gate.example.test:21"])  # pragma: allowlist secret

        with pytest.raises(ProxyUnusableError) as refused:
            await pool.client()

        assert "hunter2" not in str(refused.value)
        assert "gate.example.test:21" not in str(refused.value), "битый адрес — только номер"
        assert refused.value.__cause__ is None, "цепочка несла бы адрес с паролем"

    @pytest.mark.parametrize(
        "url",
        [
            "http://user:p@ss#word@gate.example.test:7777",  # pragma: allowlist secret
            "http://user:pa?ss@gate.example.test:7777",  # pragma: allowlist secret
            "http://user:pa/ss@gate.example.test:7777",  # pragma: allowlist secret
        ],
    )
    def test_a_password_cut_by_the_parser_never_becomes_a_host(self, url: str) -> None:
        """«#», «?» или «/» в пароле режут адрес, и хостом разбор видит кусок пароля:
        такой адрес называется только номером."""
        assert proxy_name(0, url) == "№1"

    async def test_any_failure_of_the_transport_is_refused_without_the_password(self) -> None:
        """`InvalidURL` — не `ValueError`: до правки он уходил мимо отказа, и кусок пароля —
        в запись обхода и журнал."""

        def invalid(url: str) -> httpx.AsyncBaseTransport:
            raise httpx.InvalidURL(f"Invalid port in {url}")

        pool = ProxyPool([FIRST], transport=invalid)

        with pytest.raises(ProxyUnusableError) as refused:
            await pool.client()

        assert "secret-one" not in str(refused.value)
        assert "InvalidURL" in str(refused.value)

    def test_short_parts_are_not_wiped_out_of_every_word(self) -> None:
        """Логин «a» стирал бы каждую «a» текста; чистится адрес и длинные части."""
        pool = ProxyPool(["http://a:bc@gate.example.test:7777"])  # pragma: allowlist secret

        cleaned = pool.scrub("a bc abc — http://a:bc@gate.example.test:7777 упал")

        assert cleaned.startswith("a bc abc — ")
        assert "a:bc@" not in cleaned


async def test_traffic_counts_the_wire_body_headers_and_redirect_steps() -> None:
    """Провайдер берёт за байты по проводу: тело — сжатым, как пришло, плюс заголовки
    в обе стороны и шаг редиректа."""
    page = b"<html>" + b"x" * 50_000 + b"</html>"
    wire = gzip.compress(page)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new"})
        headers = {**HTML, "content-encoding": "gzip"}
        return httpx.Response(200, stream=httpx.ByteStream(wire), headers=headers)

    meter = TrafficMeter()
    transport = MeteredTransport(httpx.MockTransport(handler), meter)
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.get("https://donor.example.test/old", follow_redirects=True)

    traffic = meter.take()
    assert len(response.content) == len(page)
    assert len(wire) < traffic < len(wire) + 1_000, "по проводу — сжатое тело и заголовки"
    assert len(response.history) == 1
    assert meter.take() == 0, "насчитанное отдано — счёт заново"


class _WireProxy:
    """Настоящий прямой HTTP-прокси на 127.0.0.1: запрос в абсолютной форме — к сайту,
    ответ — обратно, и счёт байт в обе стороны — то, за что берёт провайдер."""

    def __init__(self, *, once: bool = False) -> None:
        self.through = 0
        self.once = once

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while True:
            try:
                head = await reader.readuntil(b"\r\n\r\n")
            except asyncio.IncompleteReadError:
                logging.getLogger(__name__).debug("прокси: клиент закрыл соединение")
                break
            self.through += len(head)
            method, target, version = head.split(b"\r\n")[0].decode().split(" ")
            url = httpx.URL(target)
            site_reader, site_writer = await asyncio.open_connection(url.host, url.port)
            lines = head.split(b"\r\n")[1:]
            site_writer.write(f"{method} {url.raw_path.decode()} {version}\r\n".encode())
            site_writer.write(b"\r\n".join(lines))
            await site_writer.drain()
            answer = await site_reader.read()
            site_writer.close()
            await site_writer.wait_closed()
            self.through += len(answer)
            writer.write(answer)
            await writer.drain()
            if self.once:  # сайт оборвал ответ — и прокси закрывает соединение клиента
                break
        writer.close()
        await writer.wait_closed()


async def _site(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Сайт: редирект и сжатая страница, соединение на запрос."""
    head = await reader.readuntil(b"\r\n\r\n")
    if head.split(b" ")[1].endswith(b"/old"):
        writer.write(b"HTTP/1.1 301 Moved\r\nLocation: /new\r\nContent-Length: 0\r\n\r\n")
    else:
        body = gzip.compress(PAGE.encode() * 20)
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Encoding: gzip\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode()
            + body
        )
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def test_traffic_equals_the_bytes_through_a_real_proxy() -> None:
    """Счёт сверен с настоящим транспортом httpx и настоящим прокси на этой машине:
    байт в байт, со строками запроса и статуса, заголовками и шагом редиректа."""
    proxy = _WireProxy()
    site = await asyncio.start_server(_site, "127.0.0.1", 0)
    gate = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
    site_port = site.sockets[0].getsockname()[1]
    gate_port = gate.sockets[0].getsockname()[1]
    pool = ProxyPool(
        [f"http://127.0.0.1:{gate_port}"],
        transport=lambda url: httpx.AsyncHTTPTransport(proxy=url),
    )
    try:
        client = await pool.client()
        response = await client.get(f"http://127.0.0.1:{site_port}/old", follow_redirects=True)
    finally:
        await pool.aclose()
        for server in (site, gate):
            server.close()
            await server.wait_closed()

    assert len(response.history) == 1
    assert pool.meter.take() == proxy.through


async def _cut_site(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Сайт, оборвавший ответ: обещал 50 000 байт, отдал тысячу и закрыл соединение."""
    await reader.readuntil(b"\r\n\r\n")
    writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 50000\r\n\r\n")
    writer.write(b"x" * 1_000)
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def test_a_call_cut_halfway_still_pays_for_what_was_read() -> None:
    """Обрыв посреди тела — провайдер за прочитанное взял: трата в журнале есть, и свой
    потолок её видит. До правки трафик писался только у удавшихся вызовов."""
    proxy = _WireProxy(once=True)
    site = await asyncio.start_server(_cut_site, "127.0.0.1", 0)
    gate = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
    site_port = site.sockets[0].getsockname()[1]
    pool = ProxyPool(
        [f"http://127.0.0.1:{gate.sockets[0].getsockname()[1]}"],
        transport=lambda url: httpx.AsyncHTTPTransport(proxy=url),
    )
    ledger = Ledger()
    try:
        async with _direct(_blocked) as client:
            cascade = PageCascade(client, browser_enabled=False, proxies=pool, spending=ledger)
            result = await cascade.get(f"http://127.0.0.1:{site_port}/page")
            await cascade.aclose()
    finally:
        for server in (site, gate):
            server.close()
            await server.wait_closed()

    assert result.level is CascadeLevel.HTTP, "не помог и прокси — исход первого уровня"
    assert "RemoteProtocolError" in cascade.degradation.as_dict()[CascadeLevel.PROXY.value]
    [(operation, units)] = ledger.records
    assert operation == PROXY_OPERATION
    assert units > 1_000, "прочитанная тысяча и заголовки — в трате"


async def test_body_assembled_in_advance_is_still_counted() -> None:
    """Ответ, собранный целиком заранее (так отвечают подделки транспорта), счётчик
    тоже видит: тело идёт через него потоком."""
    meter = TrafficMeter()
    transport = MeteredTransport(httpx.MockTransport(_open), meter)
    async with httpx.AsyncClient(transport=transport) as client:
        await client.get("https://donor.example.test/a")

    assert meter.take() > len(PAGE.encode())


class TestCascade:
    async def test_refused_proxy_is_changed_for_the_next_page(self) -> None:
        """Сайт узнал адрес прокси — следующая страница идёт через следующий."""
        gates = Proxies({FIRST: _blocked, SECOND: _open})
        ledger = Ledger()
        async with _direct(_blocked) as client:
            cascade = PageCascade(
                client,
                browser_enabled=False,
                proxies=ProxyPool([FIRST, SECOND], transport=gates.transport),
                spending=ledger,
            )
            first = await cascade.get("https://donor.example.test/a")
            second = await cascade.get("https://donor.example.test/b")
            await cascade.aclose()

        assert first.outcome is FetchOutcome.BLOCKED
        assert (second.outcome, second.level) == (FetchOutcome.OK, CascadeLevel.PROXY)
        assert [name.split()[0] for name, _ in gates.used] == ["№1", "№2"]
        assert [operation for operation, _ in ledger.records] == [PROXY_OPERATION] * 2
        assert all(units > 0 for _, units in ledger.records)
        assert cascade.by_level[CascadeLevel.PROXY] == 1

    async def test_all_proxies_refusing_in_a_row_marks_the_crawl(self) -> None:
        gates = Proxies({FIRST: _blocked, SECOND: _blocked})
        async with _direct(_blocked) as client:
            cascade = PageCascade(
                client,
                browser_enabled=False,
                proxies=ProxyPool([FIRST, SECOND], transport=gates.transport),
                spending=Ledger(),
            )
            for path in ("a", "b"):
                await cascade.get(f"https://donor.example.test/{path}")
            await cascade.aclose()

        noted = cascade.degradation.as_dict()[CascadeLevel.PROXY.value]
        assert "отказали подряд все прокси (2)" in noted
        assert "secret" not in noted

    async def test_without_a_ledger_the_paid_level_is_not_called(self) -> None:
        """Консольный замер журнала не ведёт — и прокси не платит мимо счёта."""
        gates = Proxies({FIRST: _open})
        async with _direct(_blocked) as client:
            cascade = PageCascade(
                client,
                browser_enabled=False,
                proxies=ProxyPool([FIRST], transport=gates.transport),
            )
            result = await cascade.get("https://donor.example.test/a")

        assert result.outcome is FetchOutcome.BLOCKED
        assert gates.used == []
        assert "без журнала расхода" in cascade.degradation.as_dict()["proxy"]

    async def test_cap_refusal_stops_the_paid_level_and_says_so(self) -> None:
        gates = Proxies({FIRST: _open})
        ledger = Ledger(refused="потолок Этапа 2 на обход выбран: 10.00 из 10.00 $")
        async with _direct(_blocked) as client:
            cascade = PageCascade(
                client,
                browser_enabled=False,
                proxies=ProxyPool([FIRST], transport=gates.transport),
                spending=ledger,
            )
            result = await cascade.get("https://donor.example.test/a")

        assert result.outcome is FetchOutcome.BLOCKED
        assert gates.used == []
        assert ledger.records == []
        assert "потолок Этапа 2" in cascade.degradation.as_dict()["proxy"]

    async def test_empty_setting_behaves_as_before(self) -> None:
        async with _direct(_blocked) as client:
            cascade = PageCascade(client, browser_enabled=False, proxies=ProxyPool([]))
            result = await cascade.get("https://donor.example.test/a")

        assert result.outcome is FetchOutcome.BLOCKED
        assert CascadeLevel.PROXY not in cascade.degradation.asked
        assert cascade.degradation.as_dict() == {}

    async def test_an_odd_failure_through_the_proxy_says_only_its_kind(self) -> None:
        """Не сетевое исключение — его текст может нести адрес прокси с паролем: наружу
        только вид, и обход не падает."""

        def odd(url: str) -> httpx.AsyncBaseTransport:
            def handler(_request: httpx.Request) -> httpx.Response:
                raise RuntimeError(f"сломалось у {url}")

            return httpx.MockTransport(handler)

        async with _direct(_blocked) as client:
            cascade = PageCascade(
                client,
                browser_enabled=False,
                proxies=ProxyPool([FIRST], transport=odd),
                spending=Ledger(),
            )
            result = await cascade.get("https://donor.example.test/a")

        assert result.outcome is FetchOutcome.BLOCKED, "исход первого уровня"
        noted = cascade.degradation.as_dict()[CascadeLevel.PROXY.value]
        assert "RuntimeError" in noted
        assert "secret-one" not in noted

    async def test_open_page_never_reaches_the_proxy(self) -> None:
        gates = Proxies({FIRST: _open})
        ledger = Ledger()
        async with _direct(_open) as client:
            cascade = PageCascade(
                client,
                browser_enabled=False,
                proxies=ProxyPool([FIRST], transport=gates.transport),
                spending=ledger,
            )
            result = await cascade.get("https://donor.example.test/a")

        assert result.level is CascadeLevel.HTTP
        assert gates.used == []
        assert ledger.asked == []


class _SpyCascade(PageCascade):
    def __init__(self, client: httpx.AsyncClient) -> None:
        super().__init__(client, browser_enabled=False, proxies=ProxyPool([]))
        self.rotations: list[str] = []

    def rotate_proxy(self, why: str) -> None:
        self.rotations.append(why)


async def test_falling_health_changes_the_proxy_together_with_the_pace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Здоровье ниже порога — темп вдвое ниже и смена прокси, одним ответом (`walk._react`)."""
    monkeypatch.setattr("backend.config.crawl.HEALTH_MIN_REQUESTS", 10)
    pages = [f"/p{n}" for n in range(30)]
    sitemap = "".join(f"<url><loc>https://donor.example.test{path}</loc></url>" for path in pages)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow:\n")
        if path.endswith(".xml"):
            return httpx.Response(200, text=f"<urlset>{sitemap}</urlset>", headers=HTML)
        if path in pages[3::6]:  # каждая шестая: больше 10% отказов, но меньше 30%
            return httpx.Response(503, text="перегружен", headers=HTML)
        return httpx.Response(200, text=PAGE, headers=HTML)

    async with _direct(handler) as client:
        cascade = _SpyCascade(client)
        crawler = DonorCrawler(
            client, limiter=DomainLimiter(delay_sec=0.0, max_delay_sec=0.0), cascade=cascade
        )
        report = await crawler.crawl("donor.example.test")

    assert report.slowed_down is True
    assert len(cascade.rotations) == 1
    assert "в окне здоровья" in cascade.rotations[0]
