"""Молчит ли DNS домена получателя — спросить до отправки, а не узнать отказом (02.11.2026).

Хостинг получателей лёг, сервера имён замолчали, и платформа за минуты отказала двум десяткам
писем. Отказ — это потраченное место в дневном лимите ящика, погашенная цепочка и строка против
ящика, а тот же ответ виден заранее: вопрос к DNS стоит миллисекунды и денег не стоит, резолвер
уже есть (`contacts/mx.py`).

**Молчит — письмо ждёт.** Отправка отвечает `sending.RecipientDnsError`, отказом «не сейчас», как
у окна получателя (`core/window.py`): первое письмо остаётся в очереди, у добивки срок возвращается
(`followups.Chain.restore`), ящик не захвачен. Письма не ушло, отказа нет, ящику нечего считать.

**Только молчание.** «Домена нет» и «почты нет» — ответы DNS, а не сбой: такому письму здесь не
мешают, оно идёт, как шло. Вопрос этот модуль задаёт один: держать ли письмо до ответа.

**Память на домен.** Проходы отправки идут раз в минуту и заглядывают в каждое письмо очереди, а
молчащий домен стоит секунд ожидания: на письмо спрашивать нельзя, домен спрашивается не чаще
`RECHECK`. Память — процесса, как у `mx.health`: перезапуск считает молчание заново, и ждать
лишние часы письму не страшно. Ответивший домен память забывает.

**Не держим вечно.** Домен молчит дольше `LONGEST` — он не лежит, а умер: письмо идёт обычным
путём и получает свой отказ, а не стоит в начале очереди навсегда.

**Слепым не судят** (замечание ревью продаж). Упади наш собственный резолвер — молчать станут все
домены разом: все письма ждут, отказов нет, тревоги нет, рассылка стоит молча. Поломка, которая
выглядит как тишина. Поэтому молчащий домен сверяется контрольными именами (`CONTROLS`): большие
домены с разными поставщиками DNS. Ответил хоть один — сбой чужой, письмо ждёт. Молчат оба — слепы
мы: письма идут обычным путём, журнал говорит что проверить, раз на сбой, а молчание доменов не
запоминается, и записанное раньше забывается: оно могло быть нашим.

Ответ сверки живёт `RECHECK`: она стоит секунд, а письмо спрашивает о ней каждое. Пока мы слепы,
письма и своего домена не спрашивают. Слепота кончается сама: срок ответа вышел — сверка спрашивает
снова. Для этого слепой проход возвращает системный резолвер: отказавший трижды подряд, он выключен
на весь процесс (`mx.health`) и сам не вернётся, потому что его больше не спрашивают, а запасные —
настройка, и их может не быть (`CONTACTS_DNS_FALLBACK`). Системный выключен, а запасные отвечают —
сверка отвечает честно: запасные слышат контрольные имена и не слышат домен в сбое.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from backend.features.contacts import mx
from backend.features.contacts.mx import MailRoute, mail_route

logger = logging.getLogger(__name__)

#: Сколько ждать ответа DNS на один резолвер: вопрос задаётся посреди отправки, а живой отвечает
#: за доли секунды. Умолчание ступени поиска адреса (5 с) рассчитано на прогон, а не на письмо.
ASK_SEC = 2.0

#: Контрольные имена: большие домены с разными поставщиками DNS (Google и Azure). Молчат оба —
#: дело не в них, а в нас.
CONTROLS = ("gmail.com", "outlook.com")

#: Как часто спрашивать о молчащем домене и сколько живёт ответ контрольных имён.
RECHECK = timedelta(minutes=15)

#: На сколько откладывается добивка: срок у неё один, и спрашивать раньше незачем.
WAIT = timedelta(minutes=30)

#: Дольше этого молчание — уже не сбой: письмо не держится.
LONGEST = timedelta(days=3)


@dataclass(slots=True)
class _Silence:
    """Домен молчит: с какой минуты и когда спросить снова."""

    since: datetime
    next_check: datetime


@dataclass(slots=True)
class _Sight:
    """Что сказала сверка контрольными именами и до какой минуты этому верить."""

    sees: bool
    until: datetime


@dataclass
class Memory:
    """Что знает этот процесс; тесты начинают с пустой (`tests/conftest.py`)."""

    silent: dict[str, _Silence] = field(default_factory=dict)
    sight: _Sight | None = None
    #: Журнал уже сказал о слепоте: говорится раз на сбой, а не на письмо.
    told_blind: bool = False


MEMORY = Memory()


def domain_of(email: str) -> str:
    """Домен адреса — как его спрашивает DNS: без регистра и краевых пробелов."""
    return email.rpartition("@")[2].strip().lower()


def _blind(now: datetime) -> bool:
    """Сверка сказала «слепы», и срок её ответа не вышел: спрашивать незачем."""
    sight = MEMORY.sight
    return sight is not None and not sight.sees and now < sight.until


async def _control_answered() -> bool:
    """Ответило ли контрольное имя: любой ответ DNS, а не только запись почты."""
    for host in CONTROLS:
        if await mail_route(host, dns_timeout_sec=ASK_SEC) is not MailRoute.UNKNOWN:
            return True
    return False


def _heard(sees: bool) -> None:
    """Свежий ответ сверки. Слепы — память о молчании не в счёт, системному резолверу новый шанс,
    журнал — раз на сбой; прозрели — следующий сбой журнал скажет снова."""
    if sees:
        if MEMORY.told_blind:
            MEMORY.told_blind = False
            logger.info("письма: контрольные имена снова отвечают — DNS получателей проверяется")
        return
    MEMORY.silent.clear()
    mx.health.reset()
    if not MEMORY.told_blind:
        MEMORY.told_blind = True
        logger.warning(
            "письма: DNS получателей спросить некого — молчат и контрольные имена (%s): письма "
            "идут без проверки. Проверить сеть сервера, резолвер контейнера и "
            "CONTACTS_DNS_FALLBACK",
            ", ".join(CONTROLS),
        )


async def _we_see(now: datetime) -> bool:
    """Отвечает ли DNS нам вообще: домен молчит, и надо знать — он или мы. Ответ живёт `RECHECK`."""
    sight = MEMORY.sight
    if sight is None or now >= sight.until:
        sight = _Sight(sees=await _control_answered(), until=now + RECHECK)
        MEMORY.sight = sight
        _heard(sight.sees)
    return sight.sees


async def holds(email: str, *, now: datetime) -> bool:
    """Держать ли письмо на адрес `email`: DNS его домена молчит, контрольные имена отвечают, и
    не дольше `LONGEST`."""
    host = domain_of(email)
    if not host or _blind(now):
        return False
    known = MEMORY.silent.get(host)
    if known is not None and now < known.next_check:
        return now - known.since < LONGEST
    route = await mail_route(host, dns_timeout_sec=ASK_SEC)
    if route is not MailRoute.UNKNOWN:
        MEMORY.silent.pop(host, None)
        return False
    if not await _we_see(now):
        return False
    since = now if known is None else known.since
    MEMORY.silent[host] = _Silence(since=since, next_check=now + RECHECK)
    if known is None:
        logger.info("письма: DNS домена %s молчит — письма на него ждут", host)
    return now - since < LONGEST
