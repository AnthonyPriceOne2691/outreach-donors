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

**Слепым не судят.** Системный резолвер, отказавший трижды подряд, `mx.health` выключает на весь
процесс, а запасные — настройка (`CONTACTS_DNS_FALLBACK`). Запасных нет — спросить некого, и
«молчит» здесь значит только нашу слепоту: держать из-за неё всю очередь нельзя, письма идут
обычным путём, журнал говорит почему.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from backend.config import contacts as contacts_cfg
from backend.features.contacts import mx
from backend.features.contacts.mx import MailRoute, mail_route

logger = logging.getLogger(__name__)

#: Сколько ждать ответа DNS на один резолвер: вопрос задаётся посреди отправки, а живой отвечает
#: за доли секунды. Умолчание ступени поиска адреса (5 с) рассчитано на прогон, а не на письмо.
ASK_SEC = 2.0

#: Как часто спрашивать о молчащем домене.
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


@dataclass
class Memory:
    """Молчащие домены этого процесса; тесты начинают с пустой (`tests/conftest.py`)."""

    silent: dict[str, _Silence] = field(default_factory=dict)
    #: Журнал уже сказал, что спросить некого: говорится раз на процесс, а не на письмо.
    told_blind: bool = False


MEMORY = Memory()


def domain_of(email: str) -> str:
    """Домен адреса — как его спрашивает DNS: без регистра и краевых пробелов."""
    return email.rpartition("@")[2].strip().lower()


def _blind() -> bool:
    """Спросить некого: системный резолвер выключен на процесс, запасных нет (шапка модуля)."""
    if mx.health.system_is_trusted or contacts_cfg.DNS_FALLBACK.strip(" ,"):
        return False
    if not MEMORY.told_blind:
        MEMORY.told_blind = True
        logger.warning(
            "письма: DNS получателей спросить некого — системный резолвер отключён, "
            "CONTACTS_DNS_FALLBACK пуст: письма идут без проверки, проверить настройку и сеть"
        )
    return True


async def holds(email: str, *, now: datetime) -> bool:
    """Держать ли письмо на адрес `email`: DNS его домена молчит, и не дольше `LONGEST`."""
    host = domain_of(email)
    if not host or _blind():
        return False
    known = MEMORY.silent.get(host)
    if known is not None and now < known.next_check:
        return now - known.since < LONGEST
    route = await mail_route(host, dns_timeout_sec=ASK_SEC)
    if route is not MailRoute.UNKNOWN:
        MEMORY.silent.pop(host, None)
        return False
    since = now if known is None else known.since
    MEMORY.silent[host] = _Silence(since=since, next_check=now + RECHECK)
    if known is None:
        logger.info("письма: DNS домена %s молчит — письма на него ждут", host)
    return now - since < LONGEST
