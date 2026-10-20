"""Поиск контактов пачкой: один путь для консоли, для кнопки и для карточки донора.

Раньше весь порядок работы жил в консольной команде, и запустить поиск
мог только инженер. Это ломало главное обещание веб-слоя: сотрудник
доводит донора до цены, ни разу не обратившись к инженеру.

Три свойства, ради которых порядок именно такой, остались прежними.

**Остаток платного сервиса спрашивается до начала.** Ключ бывает общим
с соседней системой, и своя таблица про её траты ничего не знает.
Не смогли спросить — идём без платной ступени, а не вслепую.

**Платная ступень доноров и рекламодателей включается флагом, а не ключом**
(`CONTACTS_PAID_SEARCH_ENABLED`, решение Anthony 12.10.2026). Ключ общий с проверкой
адресов продаж, и одно его появление включало платный поиск везде — с автопоиском
после каждого «принять». Выключен — бесплатные ступени идут как раньше, а отчёт
говорит почему без платной. Очередь сбора базы продаж платит по своему флагу
(`SALES_SEGMENT_PAID_CONTACTS`, вызывающий передаёт `no_paid`), этот её не держит.

**Пачка — чекпоинт.** Поиск по сотне доменов идёт минутами; падение
на середине не должно стоить уже пройденного. Сохранили пачку — эти
домены выпали из повторного запуска.

**Отчёт показывает каждую ступень.** Сколько доменов вошло, сколько
адресов дала, сколько стоила. Только по этим числам видно, окупается
ли порядок ступеней, — а он и есть главное решение фазы.

**Частота, не отпустившая после повторов, останавливает проход** (сверка ТЗ
12.10.2026). Платный сервис повторяет запрос сам (`provider.py`); не помогло —
проход встаёт после пачки и говорит почему, а не перебирает остальные домены
вхолостую: каждый упёрся бы в тот же лимит. Непройденные остаются в очереди.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import contacts as cfg
from backend.features.contacts.browser import PlaywrightRenderer
from backend.features.contacts.ladder import ContactLadder, DeepLadder, LadderResult
from backend.features.contacts.provider import (
    ContactProvider,
    HunterProvider,
    PaidUnavailable,
    ProviderBlockedError,
    ProviderError,
    ProviderQuotaError,
)
from backend.features.contacts.repository import ContactQueue, ContactRepository
from backend.features.contacts.step_counters import StepCounters
from backend.features.contacts.sweep_trace import TracedRenderer, current_silence, traced, watch
from backend.features.core import usage
from backend.features.core.domain import ContactStatus, Stage
from backend.shared.net.url_guard import guarded_client

logger = logging.getLogger(__name__)

#: Операция журнала расхода: запрос к платному провайдеру адресов.
CONTACTS_OPERATION = "contacts_search"

#: Сколько доменов проходим одновременно. Каждый сайт идёт по одному запросу
#: за раз, и вежливость к нему от этого числа не зависит: оно про то, сколько
#: разных сайтов в работе. 16 с 14.10.2026 (было 8): тысяча рекламодателей шла
#: полчаса с лишним, а сайт отвечает секундами — ждали в основном молчащих.
CONCURRENCY = 16

#: Размер пачки для сохранения. Он же шаг чекпоинта. Пачка ждёт самый медленный
#: сайт, поэтому она вдвое шире одновременных: иначе последние места пачки
#: простаивали бы, пока тянется один молчащий домен.
BATCH = 32

#: Почему платная ступень доноров и рекламодателей не зовётся, пока её не включили.
PAID_SEARCH_OFF = "платный поиск адресов выключен (CONTACTS_PAID_SEARCH_ENABLED)"

#: Нота прохода, у которого не поднялся браузер: пакета нет в окружении (в образе прода
#: его нет — `uv sync --extra browser`) или Chromium не запустился.
NO_BROWSER = "Браузер не поднялся — проход идёт без этой ступени."

#: Проход вызван без платной (`no_paid`): сайт без адреса на страницах — «не спросили»,
#: а не «адреса нет» (`ContactStatus.PAID_OFF`).
PAID_OFF = PaidUnavailable(ContactStatus.PAID_OFF, "проход без платного поиска")

#: Что сказать о доменах без адреса на страницах, когда платная ступень не спросит.
_UNAVAILABLE_THEN = {
    ContactStatus.NO_QUOTA: "«кончилась квота», повтор по сроку очереди (доноров — через сутки)",
    ContactStatus.ERROR: "«ошибка поиска», повтор по сроку очереди (доноров — через сутки)",
    ContactStatus.PAID_OFF: "«не спросили», повтор по сроку очереди (доноров — через сутки)",
}


def _is_deep(queue: object) -> bool:
    """Нужна ли очереди лестница глубже — поиск глубже по кнопке (`deeper.DeeperQueue`).

    Признак — у очереди (`deep_search = True`), а не доводом прохода: «глубже» — это
    про то, кому ищут (донор, у которого обычный поиск кончился «адреса нет»), и одно
    без другого не бывает. Читается значение, а не наличие имени: проверка протокола
    (`runtime_checkable`) видит только имя, и очередь с `deep_search = False` шла бы глубже.
    """
    return getattr(queue, "deep_search", False) is True


@dataclass
class SearchReport:
    """Что дал проход. Числа — и в консоль, и на экран, и в журнал задачи."""

    pending: int = 0
    walked: int = 0
    saved: int = 0
    counters: dict[str, int] = field(default_factory=dict)
    manual_queue_left: int = 0
    #: Что стоит сказать человеку вслух: платный сервис не настроен,
    #: браузер не поднялся, квота кончилась. Не ошибки — условия работы.
    notes: list[str] = field(default_factory=list)
    #: Платная ступень есть, но в этом проходе не спросит — и почему (`PaidUnavailable`):
    #: до прохода — от `paid_step` (её ответ при этом — «ступени нет», `None`) для лестницы;
    #: посреди прохода — ключ не принят или учётку закрыли (`ladder.paid_closed`).
    paid_missing: PaidUnavailable | None = None

    @property
    def as_report(self) -> str:
        return f"доменов {self.walked}, адресов {self.saved}"

    def as_dict(self) -> dict[str, object]:
        return {
            "pending": self.pending,
            "walked": self.walked,
            "saved": self.saved,
            "counters": dict(self.counters),
            "manual_queue_left": self.manual_queue_left,
            "notes": list(self.notes),
        }


def _pays(report: SearchReport, stage: Stage | None, *, no_paid: bool) -> bool:
    """Зовётся ли платная ступень в этом проходе; выключена флагом — отчёт говорит почему.

    Доноры, рекламодатели и перепоиск названных доменов платят только с флагом
    `CONTACTS_PAID_SEARCH_ENABLED` (решение Anthony 12.10.2026): ключ общий с проверкой
    адресов продаж, и одно его появление не включает трату здесь. Очередь продаж
    (`Stage.SALES`) решает своим флагом у вызывающего (`no_paid`), и этот её не держит.
    """
    if no_paid:
        return False
    if stage is Stage.SALES or cfg.PAID_SEARCH_ENABLED:
        return True
    report.notes.append(f"Ступень 3 не зовётся: {PAID_SEARCH_OFF} — идём по бесплатным.")
    return False


async def paid_step(http: httpx.AsyncClient, report: SearchReport) -> ContactProvider | None:
    """Платный сервис, если ключ есть и квота осталась; иначе `None` — «ступени нет».

    Отсутствие ключа — рабочий режим, а не поломка: три бесплатные
    ступени работают и без него, просто часть доменов останется
    без контакта. Включён ли он вовсе, решает флаг до этого (`_pays`).

    Имя публичное: тем же правилом готовность платной ступени проверяет
    до прохода и повтор сбора продаж (`sales/segment_paid.ready`).

    **Ответ — только «готова ли ступень»**: продажи спрашивают ровно это (`is not None`), и
    их тесты подменяют функцию подделкой с тем же ответом. Квота кончилась, остаток не
    узнать, учётку закрыли — `None`, а причина для лестницы — в отчёте прохода
    (`report.paid_missing`, `PaidUnavailable`): исход домена без адреса на страницах её
    называет. До 20.10.2026 все случаи выглядели как «ключа нет», и домен получал «адреса
    нет» на полгода, хотя квота возвращается в начале месяца (находка продаж). Отказ
    остатка разбирается, как посреди прохода (`provider_step.ProviderStep._ask`): квота —
    `no_quota`, непринятый ключ и закрытая учётка — `paid_off` с причиной «ключ не принят»
    или «учётка закрыта», сеть и 5xx — `error`.
    """
    if not cfg.HUNTER_API_KEY:
        report.notes.append("Платный сервис не настроен — идём по бесплатным ступеням.")
        return None

    provider = HunterProvider(http)
    try:
        quota = await provider.quota()
    except ProviderError as exc:
        logger.warning("контакты: остаток платного сервиса не узнать (%s)", exc)
        _unavailable(report, *_balance_refusal(exc))
        return None
    if quota.left <= 0:
        said = "квота платного сервиса исчерпана"
        _unavailable(report, PaidUnavailable(ContactStatus.NO_QUOTA, said), said)
        return None
    # Остаток — это про деньги, а не про доступность: провайдер отвечает
    # на запрос остатка и при закрытой учётке, показывая полный запас.
    # Поэтому строка говорит ровно про остаток и ничего не обещает.
    report.notes.append(f"Платный сервис: остаток квоты {quota.left} поисков.")
    return provider


def _balance_refusal(exc: ProviderError) -> tuple[PaidUnavailable, str]:
    """Отказ остатка — исходом, как отказ посреди прохода: квота, учётка или сбой; и
    словами для отчёта. У учётки причина коротко («ключ не принят», «учётка закрыта»), а
    слова — провайдера: что чинить."""
    if isinstance(exc, ProviderBlockedError):
        return PaidUnavailable(ContactStatus.PAID_OFF, exc.why), str(exc)
    if isinstance(exc, ProviderQuotaError):
        return PaidUnavailable(ContactStatus.NO_QUOTA, str(exc)), str(exc)
    said = f"остаток платного сервиса не узнать: {exc}"
    return PaidUnavailable(ContactStatus.ERROR, said), said


def _unavailable(report: SearchReport, missing: PaidUnavailable, said: str) -> None:
    """Платная ступень не спросит в этом проходе: причина — в отчёт словами (`said`) и в
    отчёт для лестницы (`paid_missing`); ответ `paid_step` — «ступени нет»."""
    report.notes.append(
        f"Платная ступень не зовётся: {said}. Домены без адреса на страницах — "
        f"{_UNAVAILABLE_THEN[missing.status]}; сайты с формой — в «Формы»."
    )
    report.paid_missing = missing


def _note_closed(report: SearchReport, closed: PaidUnavailable | None) -> None:
    """Ключ не принят или учётку закрыли посреди прохода (`ladder.paid_closed`): причина — в
    отчёт прохода, как у отказа до него. Слова провайдера — строкой об отказах ступени."""
    if closed is None:
        return
    report.paid_missing = closed
    report.notes.append(
        f"Платная ступень остановлена посреди прохода: {closed.reason}. Домены без адреса "
        f"на страницах с этого места — {_UNAVAILABLE_THEN[closed.status]}; сайты с формой — "
        "в «Формы»."
    )


async def _walk(ladder: ContactLadder, hosts: list[str], last: set[str]) -> list[LadderResult]:
    """Пройти пачку доменов одновременно, но не все сразу.

    Обмен с каждым сайтом идёт в своём следе (`sweep_trace.traced`): по нему
    лестница знает, ответил ли сайт, — молчащему не платят, а исход
    `no_answer` повторится по сроку (`attempts.py`). `last` — домены, для
    которых этот проход — последний шанс.
    """
    limiter = asyncio.Semaphore(CONCURRENCY)

    async def one(host: str) -> LadderResult:
        async with limiter:
            with traced(host):
                return await ladder.find(host, last_try=host in last)

    return list(await asyncio.gather(*(one(host) for host in hosts)))


async def _browser(stack: AsyncExitStack, report: SearchReport) -> TracedRenderer | None:
    """Браузер на весь проход, если его просили.

    Один раз на проход, а не на домен: запуск стоит около секунды и сотен
    мегабайт, а без просьбы его ступень всё равно не работает. Страница,
    которую он отдал, — тоже ответ сайта (`TracedRenderer`). Не поднялся —
    работаем без него и говорим об этом.
    """
    renderer = await stack.enter_async_context(PlaywrightRenderer())
    if renderer is None:
        report.notes.append(NO_BROWSER)
        return None
    return TracedRenderer(renderer)


def _note_provider_refusal(report: SearchReport, counters: StepCounters) -> None:
    """Сказать вслух, что платная ступень отказывала.

    Громче остатка квоты: остаток к этому моменту уже напечатан, и при
    закрытой учётке он врёт бодростью — провайдер отвечает на запрос
    остатка и показывает полный запас (замерено 22.09.2026).
    """
    if not counters.provider_refusal:
        return
    report.notes.append(
        f"Платная ступень отказала {counters.provider_refused} раз(а): {counters.provider_refusal}"
    )


def _paid_calls(counters: StepCounters) -> int:
    """Запросов к платному провайдеру, которые он принял: отказ (квота,
    закрытая учётка) запросом не считается — провайдер его не засчитывает."""
    return counters.provider_entered - counters.provider_refused


def _record_paid(session: AsyncSession, calls: int, stage: Stage | None) -> None:
    """Запросы к провайдеру — в журнал расхода, в одной записи с найденным.

    До 04.10.2026 поиск адресов платил Hunter, а журнал этого не видел:
    на вопрос «сколько запросов ушло на поиск» отвечала только квота в
    кабинете провайдера — без разбивки по проходам. Этап — чья очередь:
    поиск адресов рекламодателей — расход Этапа 2.
    """
    if calls > 0:
        usage.record(session, operation=CONTACTS_OPERATION, units=calls, stage=stage)


async def search_contacts(
    session: AsyncSession,
    *,
    limit: int,
    use_browser: bool = False,
    paid_first: bool = False,
    no_paid: bool = False,
    on_batch: Callable[[int, int], None] | None = None,
    queue: ContactQueue | None = None,
    donor_id: int | None = None,
) -> SearchReport:
    """Пройти лестницу по тем, кому нужен контакт.

    `queue` выбирает, по кому идём: доноры или рекламодатели Этапа 2.
    Лестница при этом одна — она работает с голым хостом и про роль
    домена не знает. Две очереди с двумя порядками работы разъехались бы
    на первой правке, и разъехались бы молча.

    `donor_id` сужает очередь доноров до одного — поиск с карточки. Это
    тот же проход, а не второй: правило «кому искать» проверяется заново
    уже здесь, в задаче, — между нажатием и исполнением общий поиск мог
    успеть найти адрес, и тогда проходить нечего.

    Расход платной ступени пишется на этап очереди (`ContactQueue.stage`):
    поиск адресов рекламодателей — трата Этапа 2.

    Очередь поиска глубже (`deeper.DeeperQueue`, `_is_deep`) получает лестницу глубже
    (`ladder.DeepLadder`): шире потолки обхода, карта сайта и браузер даже у открытого
    сайта (`site_steps`).
    """
    report = SearchReport()
    repository = queue if queue is not None else ContactRepository(session, donor_id=donor_id)
    # Этап — до обхода: очередь без него падает здесь, а не после оплаченной пачки.
    stage = repository.stage
    hosts = await repository.pending_hosts(limit=limit)
    report.pending = len(hosts)
    if not hosts:
        return report

    timeout = httpx.Timeout(cfg.PAGE_TIMEOUT_SEC, connect=cfg.PAGE_CONNECT_TIMEOUT_SEC)
    async with AsyncExitStack() as stack:
        # Клиент с защитой исходящих: адреса приходят снаружи — из выдачи
        # и из ссылок на чужих страницах. Проверка идёт на каждом шаге
        # редиректа, иначе публичный сайт уводит нас внутрь сети одним 302.
        http = await stack.enter_async_context(guarded_client(timeout=timeout))
        deep = _is_deep(repository)
        # Глубже платная ступень не зовётся по устройству (`deeper.py`): её исход —
        # «адреса нет», а не «не спросили», хотя бы проход и звали с `no_paid`.
        provider: ContactProvider | PaidUnavailable | None = None
        if not deep and _pays(report, stage, no_paid=no_paid):
            # По имени модуля: тесты подменяют `paid_step` своей подделкой.
            provider = await paid_step(http, report) or report.paid_missing
        elif no_paid and not deep:
            provider = PAID_OFF
        # Обмен с сайтами учитывается: по нему решается, ответил ли сайт.
        watch(http)

        stairs = DeepLadder if deep else ContactLadder
        ladder = stairs(
            http,
            provider=provider,
            paid_first=paid_first,
            renderer=await _browser(stack, report) if use_browser else None,
            silence=current_silence,
        )

        for start in range(0, len(hosts), BATCH):
            batch = hosts[start : start + BATCH]
            paid_before = _paid_calls(ladder.counters)
            results = await _walk(ladder, batch, await repository.last_tries(batch))
            report.saved += await repository.save(results)
            _record_paid(session, _paid_calls(ladder.counters) - paid_before, stage)
            await session.commit()
            report.walked = start + len(batch)
            if on_batch is not None:
                on_batch(report.walked, len(hosts))

        if ladder.halt:
            # Частота не отпустила и после повторов: платная ступень дальше не звалась
            # (`ladder.py`), а бесплатные прошли все домены — обрыв прохода оставил бы
            # остальные и без них (ревью продаж #358). Платная — следующим проходом.
            report.notes.append(
                f"Платная ступень остановлена: {ladder.halt}. Домены после неё прошли "
                "бесплатные ступени, платная — следующим проходом"
            )
        report.counters = ladder.counters.as_report()
        report.manual_queue_left = ladder.manual_queue_left
        _note_closed(report, ladder.paid_closed)
        _note_provider_refusal(report, ladder.counters)

    logger.info("контакты: %s", report.as_report)
    return report
