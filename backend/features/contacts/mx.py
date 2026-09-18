"""Ступень 0: примет ли домен письмо вообще.

Ступень не ищет адрес. Она отвечает на вопрос, который дешевле задать
до всех остальных: есть ли кому принимать почту на этом домене. Нет —
и ступени 1–3 тратить незачем, а отправка по найденному адресу дала бы
отказ доставки и ударила по репутации отправителя.

Тонкость, из-за которой ступень не сводится к «есть запись MX».
По RFC 5321 при отсутствии MX почта идёт на адрес из записи A. Такие
домены письма принимают, и выбрасывать их нельзя — это стоило бы нам
части базы на ровном месте.

Отдельно: неудачный запрос к DNS — это не «почты нет». Отсутствие данных
не равно отказу, такой домен идёт дальше по лестнице.

**Запасные резолверы — не перестраховка, а вывод из боевого прогона.**
В системной настройке машины первыми стояли IPv6-резолверы, до которых
не было маршрута. Каждый запрос молча истекал по таймауту, ступень
возвращала «неизвестно» по всем доменам подряд и стоила при этом пяти
секунд на домен. Выглядело это как работающая проверка. Поэтому теперь
при отказе системного резолвера запрос повторяется по списку запасных,
а доля «неизвестно» идёт в отчёт прогона отдельным числом.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any

import dns.asyncresolver
import dns.exception
import dns.resolver

from backend.config import contacts as cfg

logger = logging.getLogger(__name__)


class MailRoute(StrEnum):
    """Чем домен принимает почту."""

    MX = "mx"  # есть запись MX — обычный случай
    IMPLICIT = "implicit"  # MX нет, но есть A: по RFC почта идёт туда
    NONE = "none"  # домена нет или он ничем не принимает — стоп
    UNKNOWN = "unknown"  # DNS не ответил; это не отказ, идём дальше


#: Исходы, при которых лестница продолжается.
DELIVERABLE = frozenset({MailRoute.MX, MailRoute.IMPLICIT, MailRoute.UNKNOWN})


class _NoRecordError(Exception):
    """Записи такого типа нет, но сам домен существует."""


class _NoDomainError(Exception):
    """Домена не существует — почту принимать некому."""


class _DnsSilentError(Exception):
    """Ни один резолвер не ответил. Это не отказ домена, а наша слепота."""


#: Столько подряд отказов системного резолвера — и на этот прогон он
#: признаётся нерабочим. Иначе каждый домен платит полным таймаутом
#: за один и тот же заранее известный отказ: на сотне доменов это
#: восемь минут ожидания ни за чем.
SYSTEM_FAILURES_BEFORE_SKIP = 3


class _ResolverHealth:
    """Память о том, отвечает ли системный резолвер.

    Состояние живёт на процесс: в рамках одного прогона ответ на этот
    вопрос один и тот же, и переспрашивать его на каждом домене — значит
    платить полным таймаутом за заранее известный отказ.
    """

    def __init__(self) -> None:
        self.failures = 0
        self._announced = False

    @property
    def system_is_trusted(self) -> bool:
        return self.failures < SYSTEM_FAILURES_BEFORE_SKIP

    def note_failure(self) -> None:
        self.failures += 1
        if not self.system_is_trusted and not self._announced:
            self._announced = True
            logger.warning(
                "MX: системный резолвер не ответил %d раза подряд — дальше спрашиваем "
                "только запасные (CONTACTS_DNS_FALLBACK). Обычная причина: в настройках "
                "машины стоят резолверы, до которых нет маршрута",
                SYSTEM_FAILURES_BEFORE_SKIP,
            )

    def note_success(self) -> None:
        self.failures = 0

    def reset(self) -> None:
        self.failures = 0
        self._announced = False


health = _ResolverHealth()


def _resolvers(timeout: float) -> list[dns.asyncresolver.Resolver]:
    """Системный резолвер, следом запасные.

    Системный первый: он знает про внутренние зоны и ближе. Запасные
    берутся, только когда системный не ответил, — а если он не отвечает
    раз за разом, его перестают спрашивать вовсе.
    """
    out: list[dns.asyncresolver.Resolver] = []
    if health.system_is_trusted:
        system = dns.asyncresolver.Resolver()
        system.timeout = timeout
        system.lifetime = timeout
        out.append(system)

    addresses = [a.strip() for a in cfg.DNS_FALLBACK.split(",") if a.strip()]
    if addresses:
        spare = dns.asyncresolver.Resolver(configure=False)
        spare.nameservers = addresses
        spare.timeout = timeout
        spare.lifetime = timeout
        out.append(spare)

    return out


async def _query(host: str, rdtype: str, timeout_sec: float) -> Any:
    """Запрос по списку резолверов: первый ответ выигрывает.

    «Записи нет» и «домена нет» — это ответы, а не отказы: их резолвер
    даёт уверенно, и перебирать остальных незачем.
    """
    last: Exception | None = None
    resolvers = _resolvers(timeout_sec)
    system_first = health.system_is_trusted

    for number, resolver in enumerate(resolvers, start=1):
        is_system = system_first and number == 1
        try:
            answer = await resolver.resolve(host, rdtype)
        except dns.resolver.NoAnswer as exc:
            # Уверенный ответ, а не отказ: перебирать остальных незачем.
            if is_system:
                health.note_success()
            raise _NoRecordError from exc
        except dns.resolver.NXDOMAIN as exc:
            if is_system:
                health.note_success()
            raise _NoDomainError from exc
        except (dns.exception.DNSException, OSError) as exc:
            last = exc
            if is_system:
                health.note_failure()
            logger.debug("MX: резолвер №%d не ответил по %s (%r)", number, host, exc)
        else:
            if is_system:
                health.note_success()
            return answer

    raise _DnsSilentError(str(last))


async def mail_route(host: str, *, dns_timeout_sec: float | None = None) -> MailRoute:
    """Как домен принимает почту. Один-два запроса к DNS, денег не стоит."""
    timeout = dns_timeout_sec if dns_timeout_sec is not None else cfg.MX_TIMEOUT_SEC

    try:
        answer = await _query(host, "MX", timeout)
    except _NoRecordError:
        logger.debug("MX: записи MX у %s нет — проверяем A", host)
        return await _implicit_route(host, timeout)
    except _NoDomainError:
        logger.debug("MX: домена %s не существует", host)
        return MailRoute.NONE
    except _DnsSilentError as exc:
        logger.warning(
            "MX: ни один резолвер не ответил по %s (%r). Домен идёт дальше как "
            "непроверенный; если таких большинство — проверить сеть и CONTACTS_DNS_FALLBACK",
            host,
            exc,
        )
        return MailRoute.UNKNOWN

    return MailRoute.MX if len(answer) else await _implicit_route(host, timeout)


async def _implicit_route(host: str, timeout_sec: float) -> MailRoute:
    """Записи MX нет — проверяем A: по RFC 5321 почта пойдёт туда."""
    try:
        answer = await _query(host, "A", timeout_sec)
    except (_NoRecordError, _NoDomainError):
        logger.debug("MX: у %s нет ни MX, ни A — почту принимать некому", host)
        return MailRoute.NONE
    except _DnsSilentError as exc:
        logger.info("MX: запрос A по %s не удался (%r)", host, exc)
        return MailRoute.UNKNOWN

    return MailRoute.IMPLICIT if len(answer) else MailRoute.NONE
