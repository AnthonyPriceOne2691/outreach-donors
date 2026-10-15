"""Разбор мёртвых прогонов: `python -m backend.workers.reaper`.

Отдельный процесс, а не работа внутри сервера или воркера, и на то две
причины. Серверов бывает несколько — каждый вёл бы свой разбор и ставил
бы свою задачу тому же прогону. А воркер, который и умирает, разобрать
собственную смерть не может.

Цикл простой: раз в `POLL_INTERVAL_SEC` спросить базу, кто давно молчит,
и по каждому — Redis: жива ли его задача. Правила живут в ядре
(`features/runs/lifecycle`), здесь только проводка: сессия, очередь,
интервал и то, что процесс не должен падать целиком из-за одного
неудачного прохода.

Третий цикл — повтор передачи лидов продаж (`features/sales/handoff_jobs.retry_pass`):
передачи, которые Kommo не принял, и задачи, потерянные очередью. Своего
контейнера ему не заводим — ему, как и сторожу, нужна сессия раз в несколько минут.
Модуль продаж цикл импортирует сам, при первом проходе: сбой его импорта — сбой
этого цикла (его ловит `sales/loops.own`), а разбор прогонов и сторож идут своим чередом.

Четвёртый и пятый — автоотправка первых писем продаж и автосборка их очереди
(`features/sales/autosend_jobs.tick`, `features/sales/autobuild.tick`) — так же: модуль
продаж — при первом проходе, а тумблеры (`SALES_ENABLED` и `SALES_AUTOSEND`, `SALES_AUTOBUILD`)
цикл смотрит до импорта. Выключенный проход не грузит в разбор ни отправку и почту, ни сборку
с моделью: процесс живёт в тесном пределе памяти, и сломанный импорт выключенного прохода ронял
бы каждый его цикл.

Неудачи трёх проходов продаж — тревога продаж, а не здоровье процесса (`features/sales/loops.own`):
иначе сломанная автосборка делала бы «нездоровым» процесс, где живёт сторож тишины. Зависший
проход продаж по-прежнему виден по отметке своего цикла. Память циклов продаж — тоже модуль
продаж и тоже лениво, первым проходом: сломан её импорт — падают циклы продаж, а не процесс.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import sales as sales_cfg
from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.crawl.lifecycle import recover as recover_crawls
from backend.features.ops import silence
from backend.features.ops.alarm_feed import Feed
from backend.features.ops.alarms import Alarm
from backend.features.ops.silence import probe_providers
from backend.features.outreach.repository import EVERY_STAGE
from backend.features.runs.lifecycle import Recovery, recover
from backend.features.runs.repository import RunRepository
from backend.shared.logs import setup_logging
from backend.shared.queue import (
    RUN_JOB,
    RUN_JOB_TIMEOUT,
    enqueue_crawl,
    job_alive,
    job_failure,
    runs_queue,
)
from backend.workers.ticker import Pass, every

logger = logging.getLogger(__name__)

#: Как часто ходить за мёртвыми. Кратно меньше порога похорон, чтобы
#: между смертью и её признанием прошло не больше пары проходов, и
#: заметно больше удара о жизни, чтобы не будить базу впустую.
POLL_INTERVAL_SEC = 60.0

#: Как часто сторож смотрит на тишину — раз в две минуты. Срок ТЗ: тревога не позже 15 минут
#: от минуты, когда ящик перестал отправлять. Ящик выбыл — тревога ближайшим проходом; ящик
#: молчит — порог `mail_watch.QUIET_MINUTES` (10 мин), пауза между проходами (2 мин), проход,
#: где провайдеры не ответили до таймаутов (Ahrefs 30 с, источник выдачи 60 с), и Telegram
#: (10 с) — меньше 14 минут. Проход — счёты по базе; провайдеров он спрашивает реже
#: (`PROVIDERS_INTERVAL_SEC`): медленный опрос попадает в окно тревоги не больше одного раза.
#: «Прошло» и строки журнала — по времени, как при прежнем проходе (`QUIET_SEC`, `JOURNAL`).
WATCHDOG_INTERVAL_SEC = 120.0

#: Как часто сторож спрашивает провайдеров — как до частого прохода: два бесплатных запроса
#: раз в десять минут, между опросами — прежний ответ. Чаще незачем: ключ Ahrefs общий.
PROVIDERS_INTERVAL_SEC = 600.0

#: Как часто — пока провайдер молчит, а тревоги ещё нет (`silence.ProviderWatch.doubtful`):
#: раз в четыре минуты. Сбой на секунды снимает следующий же опрос, долгое молчание за
#: `silence.PROVIDER_DOWN_MINUTES` подтверждают несколько. Не чаще прохода через один:
#: медленный опрос по-прежнему попадает в окно тревоги не больше одного раза. Запросы
#: бесплатные, и общему ключу Ahrefs запрос раз в четыре минуты не помеха.
CONFIRM_INTERVAL_SEC = 240.0

#: Сколько тревоги сторожа не должно быть, прежде чем сказать «прошло»: 20 минут с прохода, где
#: её видели, — как при проходе раз в десять минут и двух проходах без неё. Временем, а не числом
#: проходов (ревью #337): проход стал чаще, а мигание короче 20 минут пары «тревога / прошло» не
#: даёт. Срок тревоги это не задевает: «тревога» — сразу (у «провайдер не отвечает» сама тревога
#: — после подтверждения, `silence.ProviderWatch`).
QUIET_SEC = 1200.0

#: Как часто строка журнала о каждой действующей тревоге — как до частого прохода: раз в десять
#: минут, о новой — сразу (`silence.Journal`).
JOURNAL_INTERVAL_SEC = 600.0

#: Что сторож уже сказал человеку в Telegram — по смене состояния (`ops/alarm_feed.py`). Своё
#: имя — своё сказанное: в этом же процессе говорит лента бота продаж (`notify.BOT_FEED`, без
#: имени).
FEED = Feed("watchdog", quiet_sec=QUIET_SEC)

#: Строки журнала фонового сторожа — не чаще `JOURNAL_INTERVAL_SEC` о каждой тревоге.
JOURNAL = silence.Journal(every=JOURNAL_INTERVAL_SEC)


@dataclass
class Providers:
    """Ответ провайдеров между опросами: спрашивать не чаще `PROVIDERS_INTERVAL_SEC` (молчание
    без тревоги — `CONFIRM_INTERVAL_SEC`), первый проход процесса — всегда. Часы монотонные:
    перевод системных часов счёт не сбивает."""

    clock: Callable[[], float] = time.monotonic
    asked_at: float | None = None
    answer: Alarm | None = None
    #: Последний `probe` отдал прежний ответ — провайдеров этот проход не спрашивал.
    stale: bool = False

    async def probe(self) -> Alarm | None:
        """Тревога «провайдер не отвечает» — свежая или прежняя, если срок опроса не пришёл."""
        moment = self.clock()
        every = CONFIRM_INTERVAL_SEC if silence.WATCH.doubtful else PROVIDERS_INTERVAL_SEC
        self.stale = self.asked_at is not None and moment - self.asked_at < every
        if not self.stale:
            self.answer, self.asked_at = await probe_providers(), moment
        return self.answer

    @property
    def unseen(self) -> frozenset[str]:
        """Коды, о которых проход не спрашивал: прежний ответ — не наблюдение, и «провайдер не
        отвечает» ленте снимает только свежий опрос (`Feed.tell(…, unseen=…)`, ревью #337)."""
        return frozenset({silence.PROVIDERS_ALARM}) if self.stale else frozenset()


#: Опрос провайдеров фонового сторожа — с ответом между опросами.
PROVIDERS = Providers()


def _enqueue(run_id: int) -> str | None:
    """Поставить прогону новую задачу. `None` — очередь не ответила.

    Молчание очереди здесь не ошибка процесса: прогон останется
    неразобранным до следующего прохода, и это честнее, чем считать
    его закрытым.
    """
    try:
        return str(runs_queue().enqueue(RUN_JOB, run_id, job_timeout=RUN_JOB_TIMEOUT).id)
    except Exception:
        logger.exception("Разбор: прогон %s продолжить не удалось — очередь не ответила", run_id)
        return None


def _enqueue_crawl(run_id: int) -> str | None:
    """Продолжение обхода — в очередь обходов. `None` — очередь не ответила."""
    try:
        return enqueue_crawl(run_id)
    except Exception:
        logger.exception("Разбор: обход %s продолжить не удалось — очередь не ответила", run_id)
        return None


async def sweep() -> None:
    """Один проход разбора: прогоны Этапа 1 и обходы Этапа 2. Своя сессия
    на проход: процесс живёт сутками, а сессия, живущая столько же, видит
    базу такой, какой она была при её открытии."""
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            _told(
                "Разбор прогонов",
                await recover(
                    RunRepository(session), alive=job_alive, enqueue=_enqueue, failure=job_failure
                ),
            )
        async with factory() as session:
            _told(
                "Разбор обходов",
                await recover_crawls(
                    session, alive=job_alive, enqueue=_enqueue_crawl, failure=job_failure
                ),
            )
    finally:
        await engine.dispose()


def _told(what: str, outcome: Recovery) -> None:
    """Строка журнала — только если проход что-то сделал или не выяснил."""
    if outcome.resumed or outcome.stopped or outcome.unknown:
        logger.info("%s: %s", what, outcome.as_report)


async def watch() -> None:
    """Один проход сторожа тишины.

    Живёт в этом же процессе, а не в своём: сторож ничего не чинит
    и никого не будит — ему нужна только сессия раз в несколько минут.
    Отдельный процесс ради одного запроса к базе — это ещё один
    контейнер, который однажды не поднимется, и тогда молчать будет
    уже сам сторож.

    Сессия — только на чтение тревог. Опрос провайдеров и лента тревог
    (Telegram, Redis) идут после её закрытия: при открытой транзакции
    соединение висело бы «idle in transaction» на время чужих ответов.
    """
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            # Фоновый проход — все этапы: тревоги идут владельцу в Telegram, а не на экран
            # учётки (этапы видимости — `EVERY_STAGE` явно, ревью продаж к #304).
            found = await silence.alarms(session, stages=EVERY_STAGE)
    finally:
        await engine.dispose()
    told = await silence.with_providers(found, probe=PROVIDERS.probe, journal=JOURNAL)
    await FEED.tell(told, unseen=PROVIDERS.unseen)


async def retry_handoffs() -> None:
    """Проход повторов передачи лидов продаж. Импорт модуля продаж — здесь, а не при
    загрузке процесса: упадёт он — упадёт этот цикл, а не весь разбор."""
    from backend.features.sales.handoff_jobs import retry_pass  # noqa: PLC0415 — лениво

    await retry_pass()


async def autosend() -> None:
    """Проход автоотправки первых писем продаж. Тумблеры — до импорта: выключенный проход модуль
    продаж не грузит. Импорт — здесь, как у повтора передачи: упадёт он — упадёт этот цикл, а не
    весь разбор."""
    if not (sales_cfg.ENABLED and sales_cfg.AUTOSEND):
        return
    from backend.features.sales.autosend_jobs import tick  # noqa: PLC0415 — лениво

    await tick()


async def autobuild() -> None:
    """Проход автосборки очереди продаж — тем же порядком, что автоотправка."""
    if not (sales_cfg.ENABLED and sales_cfg.AUTOBUILD):
        return
    from backend.features.sales.autobuild import tick  # noqa: PLC0415 — лениво

    await tick()


async def _sales_loop(interval: float, work: Pass, name: str) -> None:
    """Цикл продаж: неудачи прохода — тревога продаж, а не здоровье процесса (`sales/loops.own`).
    Память цикла — модуль продаж, лениво, первым проходом и одна на цикл: сломан его импорт —
    неудача этого цикла (её ловит `every`). Импорт до `every` вылетел бы из `gather` и уронил бы
    процесс целиком."""
    owned: list[Pass] = []

    async def owned_pass() -> None:
        if not owned:
            from backend.features.sales.loops import own  # noqa: PLC0415 — лениво

            owned.append(own(work, name))
        await owned[0]()

    await every(interval, owned_pass, name=name)


async def _loops() -> None:
    """Проходы с разными интервалами в одном процессе. Падение
    одного не должно останавливать другие — этим занимается `every`."""
    await asyncio.gather(
        every(POLL_INTERVAL_SEC, sweep, name="Разбор мёртвых прогонов"),
        every(WATCHDOG_INTERVAL_SEC, watch, name="Сторож тишины"),
        _sales_loop(sales_cfg.HANDOFF_PASS_SEC, retry_handoffs, "Повтор передачи лидов продаж"),
        _sales_loop(sales_cfg.AUTOSEND_PASS_SEC, autosend, "Автоотправка писем продаж"),
        _sales_loop(sales_cfg.AUTOBUILD_PASS_SEC, autobuild, "Автосборка очереди продаж"),
    )


def main() -> None:
    setup_logging()
    check_storage()
    asyncio.run(_loops())


if __name__ == "__main__":
    main()
