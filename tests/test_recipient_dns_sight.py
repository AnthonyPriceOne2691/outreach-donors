"""Слепым не судят: молчание домена сверяется контрольными именами (`letters/recipient_dns.py`).

Упади наш собственный резолвер — молчать станут все домены разом: все письма ждут, отказов нет,
тревоги нет, рассылка стоит молча. Поломка, которая выглядит как тишина. Здесь — чужой сбой и наша
слепота на подставном DNS и на настоящем `contacts/mx.py` с подставной сетью: системный резолвер
выключается тремя тайм-аутами на весь процесс, и сверка обязана отвечать честно и в этом состоянии.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import dns.exception
import pytest
from backend.config import contacts as contacts_cfg
from backend.features.contacts import mx
from backend.features.core.domain import MessageStatus, Stage
from backend.features.letters import batch, recipient_dns
from backend.features.letters.recipient_dns import CONTROLS, RECHECK
from backend.features.letters.sending import Sending
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_letters_send_queue import _queue
from tests.test_recipient_dns_hold import ADDRESS, HOST, NOW, Dns, Postman
from tests.thread_letters import conversation, first_letter

BLIND_LINE = "спросить некого"


@pytest.fixture
def dns_net(monkeypatch: pytest.MonkeyPatch) -> Dns:
    """Подставной ответ DNS: что молчит, сколько раз спросили."""
    found = Dns()
    monkeypatch.setattr(recipient_dns, "mail_route", found)
    return found


def _lines(caplog: pytest.LogCaptureFixture) -> int:
    return sum(BLIND_LINE in row.message for row in caplog.records)


class TestAgainstAnOutage:
    def test_the_controls_have_different_dns(self) -> None:
        assert len(set(CONTROLS)) == 2

    async def test_someone_elses_outage_is_held(self, dns_net: Dns) -> None:
        """Домен молчит, контрольное имя ответило: сбой чужой, письмо ждёт."""
        dns_net.silent.add(HOST)

        assert await recipient_dns.holds(ADDRESS, now=NOW)
        assert HOST in recipient_dns.MEMORY.silent

    async def test_one_control_is_enough(self, dns_net: Dns) -> None:
        """Лёг поставщик одного контрольного имени: нас это не слепит."""
        dns_net.silent.update({HOST, CONTROLS[0]})

        assert await recipient_dns.holds(ADDRESS, now=NOW)
        assert dns_net.asked == [HOST, *CONTROLS]

    async def test_a_domain_that_answers_costs_no_control(self, dns_net: Dns) -> None:
        assert await recipient_dns.holds(ADDRESS, now=NOW) is False
        assert dns_net.asked == [HOST]

    async def test_the_answer_of_the_controls_lives_recheck(self, dns_net: Dns) -> None:
        """Проход идёт раз в минуту по всей очереди: сверка стоит секунд и не платится на письмо."""
        hosts = [f"silent{n}.example.test" for n in range(5)]
        dns_net.silent.update(hosts)

        for minute, host in enumerate(hosts):
            assert await recipient_dns.holds(f"x@{host}", now=NOW + timedelta(minutes=minute))
        early = await recipient_dns.holds(ADDRESS, now=NOW + RECHECK - timedelta(seconds=1))

        assert early is False  # этот домен отвечает: память сверки ему не мешает
        assert dns_net.asked.count(CONTROLS[0]) == 1

        dns_net.silent.add("late.example.test")
        await recipient_dns.holds("x@late.example.test", now=NOW + RECHECK)

        assert dns_net.asked.count(CONTROLS[0]) == 2


class TestOurOwnBlindness:
    @pytest.fixture
    def everything_silent(self, dns_net: Dns) -> Dns:
        dns_net.silent.update({HOST, "other.example.test", *CONTROLS})
        return dns_net

    async def test_nothing_is_held_and_it_is_said_once(
        self, everything_silent: Dns, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Всё молчит: письма идут, строка журнала одна, а не на каждое письмо."""
        with caplog.at_level(logging.WARNING, logger=recipient_dns.__name__):
            held = [
                await recipient_dns.holds(address, now=NOW + timedelta(minutes=minute))
                for minute, address in zip(
                    (0, 1, 20, 40), (ADDRESS, "x@other.example.test") * 2, strict=True
                )
            ]

        assert held == [False, False, False, False]
        assert _lines(caplog) == 1
        assert "CONTACTS_DNS_FALLBACK" in caplog.text

    async def test_false_silence_is_not_remembered(self, everything_silent: Dns) -> None:
        await recipient_dns.holds(ADDRESS, now=NOW)

        assert recipient_dns.MEMORY.silent == {}

    async def test_silence_written_before_is_forgotten(self, dns_net: Dns) -> None:
        """Резолвер упал через минуту после сверки: записанное в эти минуты могло быть нашим."""
        dns_net.silent.add(HOST)
        await recipient_dns.holds(ADDRESS, now=NOW)
        dns_net.silent.update(CONTROLS)

        assert await recipient_dns.holds(ADDRESS, now=NOW + RECHECK) is False
        assert recipient_dns.MEMORY.silent == {}

    async def test_while_blind_nobody_pays_for_a_question(self, everything_silent: Dns) -> None:
        """Слепы — и каждое письмо стояло бы своими секундами: до срока сверки вопросов нет."""
        await recipient_dns.holds(ADDRESS, now=NOW)
        asked = len(everything_silent.asked)

        for minute in range(1, 15):
            await recipient_dns.holds(
                f"x{minute}@other.example.test", now=NOW + timedelta(minutes=minute)
            )

        assert len(everything_silent.asked) == asked

    async def test_blindness_ends_by_itself(
        self, everything_silent: Dns, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Контрольные ответили — проверка снова работает, письмо снова держится."""
        assert await recipient_dns.holds(ADDRESS, now=NOW) is False

        everything_silent.silent.difference_update(CONTROLS)

        with caplog.at_level(logging.INFO, logger=recipient_dns.__name__):
            assert await recipient_dns.holds(ADDRESS, now=NOW + timedelta(minutes=14)) is False
            assert await recipient_dns.holds(ADDRESS, now=NOW + RECHECK)

        assert "снова отвечают" in caplog.text

    async def test_a_new_failure_is_told_again(
        self, everything_silent: Dns, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Раз на сбой, а не на жизнь процесса: второй сбой молчать не должен."""
        with caplog.at_level(logging.WARNING, logger=recipient_dns.__name__):
            await recipient_dns.holds(ADDRESS, now=NOW)
            everything_silent.silent.difference_update(CONTROLS)
            await recipient_dns.holds(ADDRESS, now=NOW + RECHECK)
            everything_silent.silent.update(CONTROLS)
            await recipient_dns.holds(ADDRESS, now=NOW + 2 * RECHECK)

        assert _lines(caplog) == 2

    async def test_the_letter_goes_as_usual(
        self, session: AsyncSession, filled_legal: None, everything_silent: Dns
    ) -> None:
        """Рассылка не стоит молча: письмо ушло обычным путём, почта решает сама."""
        await conversation(session, sent_at=NOW - timedelta(days=9), host="other.example.test")
        letter = await first_letter(session, host=HOST)
        await session.commit()
        postman = Postman()

        await Sending(session, postman, now=NOW).send(letter.id)

        await session.refresh(letter)
        assert letter.status is MessageStatus.SENT
        assert postman.sent == [f"editor@{HOST}"]

    async def test_the_batch_goes_too(
        self, session: AsyncSession, filled_legal: None, dns_net: Dns
    ) -> None:
        await _queue(session, 3)
        dns_net.silent.update(f"donor{n}.example.test" for n in range(3))
        dns_net.silent.update(CONTROLS)

        report = await batch.send_queue(session, Postman(), stage=Stage.DONORS)

        assert (report.sent, report.refused) == (3, {})


class _Resolver:
    """Резолвер подставной сети: `mx.py` настоящий, ответы — из `Net`."""

    def __init__(self, net: Net, *, spare: bool) -> None:
        self.net = net
        self.spare = spare
        self.timeout = 0.0
        self.lifetime = 0.0
        self.nameservers: list[str] = []

    async def resolve(self, host: str, rdtype: str) -> Any:
        self.net.asked.append(("запасной" if self.spare else "системный", host))
        dead = self.net.spare_dead if self.spare else self.net.system_dead
        if dead or host in self.net.outage:
            raise dns.exception.Timeout
        return [f"mx.{host}"]


class Net:
    """Сеть: кто из резолверов жив и какие домены в сбое; что у кого спросили."""

    def __init__(self) -> None:
        self.system_dead = False
        self.spare_dead = False
        self.outage: set[str] = set()
        self.asked: list[tuple[str, str]] = []

    def resolver(self, *, configure: bool = True) -> _Resolver:
        return _Resolver(self, spare=not configure)

    def by(self, who: str) -> list[str]:
        return [host for name, host in self.asked if name == who]


@pytest.fixture
def net(monkeypatch: pytest.MonkeyPatch) -> Net:
    """Настоящий `mx.py`: системный резолвер, запасной и своя память о них."""
    found = Net()
    monkeypatch.setattr(mx.dns.asyncresolver, "Resolver", found.resolver)
    monkeypatch.setattr(mx, "health", mx._ResolverHealth())
    monkeypatch.setattr(recipient_dns, "mail_route", mx.mail_route)
    monkeypatch.setattr(contacts_cfg, "DNS_FALLBACK", "192.0.2.53")
    return found


def _switch_the_system_resolver_off(net: Net) -> None:
    for _ in range(mx.SYSTEM_FAILURES_BEFORE_SKIP):
        mx.health.note_failure()
    assert not mx.health.system_is_trusted
    net.system_dead = True


class TestRealResolvers:
    async def test_system_off_spares_alive_is_an_honest_answer(self, net: Net) -> None:
        """Три тайм-аута выключили системный резолвер на процесс, и его больше не спрашивают.
        Запасные слышат контрольное имя и не слышат домен в сбое: сбой чужой, письмо ждёт."""
        _switch_the_system_resolver_off(net)
        net.outage.add(HOST)

        assert await recipient_dns.holds(ADDRESS, now=NOW)
        assert net.by("системный") == []
        assert net.by("запасной") == [HOST, CONTROLS[0]]

    async def test_three_silent_domains_do_not_blind_us(self, net: Net) -> None:
        """Сами тайм-ауты сбоя выключают системный резолвер — и сверка отвечает так же."""
        hosts = [f"silent{n}.example.test" for n in range(mx.SYSTEM_FAILURES_BEFORE_SKIP + 1)]
        net.outage.update(hosts)

        held = [await recipient_dns.holds(f"x@{host}", now=NOW) for host in hosts]

        assert held == [True] * len(hosts)
        assert not mx.health.system_is_trusted

    async def test_everything_dead_is_blindness_and_not_an_outage(self, net: Net) -> None:
        net.system_dead = net.spare_dead = True

        assert await recipient_dns.holds(ADDRESS, now=NOW) is False
        assert recipient_dns.MEMORY.silent == {}

    async def test_the_system_resolver_comes_back(self, net: Net) -> None:
        """Выключенный системный резолвер сам не вернётся: его больше не спрашивают. Слепая сверка
        даёт ему новый шанс — и он нужен, если запасных нет."""
        _switch_the_system_resolver_off(net)
        net.spare_dead = True
        net.outage.add(HOST)

        assert await recipient_dns.holds(ADDRESS, now=NOW) is False
        assert mx.health.system_is_trusted

        net.system_dead = False

        assert await recipient_dns.holds(ADDRESS, now=NOW + RECHECK)

    async def test_no_resolvers_at_all_is_blindness_that_ends(
        self,
        net: Net,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Запасных нет, системный выключен: спросить некого буквально, и в сеть никто не ходит.
        Очередь не держится, журнал говорит раз, а через срок сверки спрашивается системный."""
        monkeypatch.setattr(contacts_cfg, "DNS_FALLBACK", " , ")
        _switch_the_system_resolver_off(net)

        with caplog.at_level(logging.WARNING, logger=recipient_dns.__name__):
            first = await recipient_dns.holds(ADDRESS, now=NOW)
            second = await recipient_dns.holds(ADDRESS, now=NOW)

        assert (first, second) == (False, False)
        assert net.asked == []
        assert _lines(caplog) == 1

        net.system_dead = False
        net.outage.add(HOST)

        assert await recipient_dns.holds(ADDRESS, now=NOW + RECHECK)
