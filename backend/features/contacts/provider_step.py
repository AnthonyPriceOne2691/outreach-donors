"""Ступень 3 лестницы — платный сервис: звать ли его по домену и чем кончился отказ.

Вынесена из `ladder.py`, когда к ней добавились «платная ступень недоступна на этот
проход» (`PaidUnavailable`) и «платный поиск выключен» (`paid_off`): лестница решает
порядок ступеней и исход домена, а здесь — разговор с сервисом и его отказы.

**Ступени нет совсем и ступени нет на этот проход — разное.** Без ключа и с выключенным
флагом платного поиска доноров (решение Anthony 12.10.2026) ступени нет совсем: рабочий
режим, «адреса нет» честное. Квота кончилась, остаток не узнать, учётку закрыли, проход
вызван без платной (`no_paid`) — ступень есть, но не спросит: исход называет причину
(`PaidUnavailable.status`; приходит отчётом прохода — `search.SearchReport.paid_missing`),
а не «адреса нет».
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from backend.features.contacts.collected import Collected
from backend.features.contacts.mx import MailRoute
from backend.features.contacts.provider import (
    ContactProvider,
    PaidUnavailable,
    ProviderBlockedError,
    ProviderDomainRefusedError,
    ProviderError,
    ProviderQuotaError,
    ProviderRateLimitError,
)
from backend.features.contacts.quality import Candidate
from backend.features.contacts.step_counters import StepCounters
from backend.features.core.domain import ContactStatus

logger = logging.getLogger(__name__)


class ProviderStep:
    """Платная ступень одного прохода. Счётчики — общие с лестницей."""

    def __init__(
        self,
        provider: ContactProvider | PaidUnavailable | None,
        counters: StepCounters,
        *,
        silence: Callable[[], str | None] | None = None,
    ) -> None:
        #: Ступень есть, но в этом проходе не спросит — и почему (`PaidUnavailable`).
        self.missing = provider if isinstance(provider, PaidUnavailable) else None
        self._provider = None if isinstance(provider, PaidUnavailable) else provider
        self._counters = counters
        self._silence = silence
        #: Почему проходу пора встать: сервис ограничил частоту и после повторов не
        #: отпустил (`provider.py`). Следующий домен упёрся бы в тот же лимит.
        self.halt = ""

    def needless(self, collected: Collected) -> str:
        """Почему платить за домен незачем; пусто — платим.

        Нулевой MX: сервис ищет адреса на самом домене, а домен объявил, что
        почту не принимает, — всё, что он отдаст, отобьётся. Сайт молчит:
        повторим по сроку, и платить за худший адрес, пока страницы с
        лучшим просто не ответили, рано — кроме последнего прохода.
        """
        if collected.route is MailRoute.NULL_MX:
            return "нулевой MX"
        if self._silence is not None and not collected.last_try:
            return self._silence() or ""
        return ""

    async def run(self, host: str, collected: Collected) -> ContactStatus | None:
        """Спросить сервис. Возвращает исход, если спросить не вышло.

        Ступени нет совсем — `None`, и «адреса нет» честное. Нет на этот проход — её
        исход (`PaidUnavailable.status`): домен «недоспрошен» или «не спросили», как если
        бы сервис отказал посреди прохода (`ladder._unfinished`).
        """
        why = self.needless(collected)
        if why or self._provider is None:
            logger.debug("контакты: платная ступень по %s не зовётся — %s", host, why or "нет")
            return None if why or self.missing is None else self.missing.status
        if self.halt:
            # Частота не отпустила на другом домене прохода: этот упёрся бы в тот же лимит.
            return ContactStatus.RATE_LIMITED

        found = await self._ask(self._provider, host)
        if isinstance(found, ContactStatus):
            return found
        for candidate in found:
            collected.add(candidate)
        return None

    def _refused(self, reason: str) -> None:
        """Записать отказ ступени. Первый отказ сохраняется дословно:
        последующие обычно тот же самый, а первый ближе к причине."""
        self._counters.provider_refused += 1
        if not self._counters.provider_refusal:
            self._counters.provider_refusal = reason

    async def _ask(self, provider: ContactProvider, host: str) -> list[Candidate] | ContactStatus:
        """Запрос к платному сервису: адреса — или исход отказа своим именем."""
        self._counters.provider_entered += 1
        try:
            return await provider.find_emails(host)
        except ProviderBlockedError as exc:
            self._refused(str(exc))
            # Раньше этот случай приезжал сюда как «частота» и лестница
            # бодро шла по следующему домену. Ловится первым: он потомок
            # ProviderError, и порядок веток решает.
            logger.exception("контакты: платный сервис закрыл учётку — %s", exc)
            return ContactStatus.BLOCKED
        except ProviderQuotaError as exc:
            self._refused(str(exc))
            logger.warning("контакты: квота платного сервиса исчерпана на %s — %s", host, exc)
            return ContactStatus.NO_QUOTA
        except ProviderDomainRefusedError as exc:
            # Ответ про домен, а не отказ сервиса: адресов у платной ступени нет, и
            # повтор по сроку спросил бы то же самое. В отказы ступени не идёт —
            # учётка жива, а «провайдер отказал» звало бы человека в его кабинет.
            logger.info("контакты: платный сервис не ищет по %s — %s", host, exc)
            return []
        except ProviderRateLimitError as exc:
            # Повторы с паузой уже были (`provider.py`): лимит не снялся, и проход встаёт.
            self._refused(str(exc))
            self.halt = f"платный сервис ограничил частоту и после повторов не отпустил ({exc})"
            logger.warning("контакты: платный сервис ограничил частоту на %s — %s", host, exc)
            return ContactStatus.RATE_LIMITED
        except ProviderError as exc:
            self._refused(str(exc))
            logger.exception("контакты: платный сервис не ответил по %s — %s", host, exc)
            return ContactStatus.ERROR
