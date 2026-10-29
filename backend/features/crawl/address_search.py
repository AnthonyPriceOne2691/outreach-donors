"""Поиск адресов рекламодателям: проход и добор — решённые за время прохода не ждут кнопки.

Задача поиска одна на сервис (`api/advertisers/routes._search`): идёт — вторую не ставим, иначе
платная ступень заплатила бы за те же домены дважды. Но «пишем» у бизнесов ниши говорят пачкой —
сто восемьдесят решений за минуту (тест-группа 29.10.2026): задача, которую поставило первое
решение, берёт очередь на своём старте, и решённые после него остались бы без поиска, пока
человек не нажмёт «ждут адреса · найти». Поэтому проход **добирает**: кончилась пачка — очередь
спрашивается снова, и круг идёт по тем, кто появился за это время, пока новые есть, потолок
задачи не выбран и кругов не больше `ROUNDS`.

**Ни один домен — дважды за задачу** (`OnceQueue`): круг берёт только тех, кого задача ещё не
видела. Домен, чей поиск кончился «сайт не ответил», правило попыток отдало бы снова (повтор по
сроку, `contacts/attempts.py`), и без этого добор платил бы за него на каждом круге.

**Решение, принятое в последние секунды задачи**, — когда круг уже спросил очередь и нашёл
пустой, а задача ещё не закрылась, — поиск не поставит: задача ещё жива, а второй не ставим. Такой
бизнес ждёт следующего решения или строки «ждут адреса · найти» — её видно на экране писем.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.contacts.ladder import LadderResult
from backend.features.contacts.search import SearchReport, search_contacts
from backend.features.core.domain import Stage
from backend.features.crawl.contacts import AdvertiserContactRepository

#: Кругов добора за задачу, не больше: каждый круг берёт хотя бы один новый домен, так что
#: потолок задачи (`limit`) кончается раньше, а это — от зацикливания на сбое очереди.
ROUNDS = 50


class OnceQueue:
    """Очередь рекламодателей без повторов в пределах задачи (`ContactQueue`)."""

    def __init__(self, base: AdvertiserContactRepository) -> None:
        self._base = base
        self._seen: set[str] = set()

    @property
    def stage(self) -> Stage | None:
        return self._base.stage

    async def pending_hosts(self, *, limit: int = 100) -> list[str]:
        """Ждущие, которых задача ещё не видела, — не больше `limit`."""
        hosts = await self._base.pending_hosts(limit=limit + len(self._seen))
        fresh = [host for host in hosts if host not in self._seen][:limit]
        self._seen.update(fresh)
        return fresh

    async def save(self, results: Sequence[LadderResult]) -> int:
        return await self._base.save(results)

    async def last_tries(self, hosts: Sequence[str]) -> set[str]:
        return await self._base.last_tries(hosts)


def merged(total: SearchReport, more: SearchReport) -> SearchReport:
    """Отчёт задачи из кругов: числа — суммой, заметки — без повторов, остаток ручной
    очереди — последнего круга."""
    counters = dict(total.counters)
    for key, value in more.counters.items():
        counters[key] = counters.get(key, 0) + value
    return SearchReport(
        pending=total.pending + more.pending,
        walked=total.walked + more.walked,
        saved=total.saved + more.saved,
        counters=counters,
        manual_queue_left=more.manual_queue_left,
        notes=list(dict.fromkeys([*total.notes, *more.notes])),
        paid_missing=total.paid_missing or more.paid_missing,
    )


async def search_with_followup(
    session: AsyncSession, *, limit: int, use_browser: bool, paid_first: bool
) -> SearchReport:
    """Поиск адресов рекламодателям с добором: круги, пока очередь даёт новых (шапка модуля).
    Каждый круг фиксируется сам: упади задача на десятом — девять уже записаны."""
    queue = OnceQueue(AdvertiserContactRepository(session))
    total = SearchReport()
    for _ in range(ROUNDS):
        left = limit - total.walked
        if left <= 0:
            break
        report = await search_contacts(
            session, limit=left, use_browser=use_browser, paid_first=paid_first, queue=queue
        )
        await session.commit()
        total = merged(total, report)
        if report.pending == 0:
            break
    return total
