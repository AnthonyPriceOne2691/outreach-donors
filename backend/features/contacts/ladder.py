"""Лестница поиска контакта целиком: от MX до ручной очереди.

    MX → страницы сайта → RDAP → платный сервис → форма в ручную очередь

Порядок продиктован ценой, а не удобством изложения (okf/contact-ladder.md).
Платный сервис стоит денег и потому идёт последним: свой парсинг снимает
с него около половины доменов и добирает то, чего он не знает.

Два свойства, ради которых модуль устроен именно так.

**Ступень видит только остаток.** Найденный адрес прекращает спуск сразу,
а не после прохода всех ступеней. Проверяется это не результатом, а
счётом вызовов: при перестановке ступеней результат бы не изменился,
а счёт вырос бы вдвое.

**Каждая ступень считается.** Сколько доменов вошло, сколько адресов
дала, сколько стоила. Без счётчиков нельзя ответить, окупается ли
выбранный порядок, — а он и есть главное решение этой фазы.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from backend.config import contacts as cfg
from backend.features.contacts import browser as browser_step
from backend.features.contacts import rdap, site_steps
from backend.features.contacts.collected import Collected
from backend.features.contacts.messengers import FoundHandle
from backend.features.contacts.mx import DELIVERABLE, MailRoute, mail_route
from backend.features.contacts.provider import ContactProvider, PaidUnavailable
from backend.features.contacts.provider_step import ProviderStep
from backend.features.contacts.quality import Candidate, best
from backend.features.contacts.step_counters import StepCounters
from backend.features.core.domain import ContactSource, ContactStatus

logger = logging.getLogger(__name__)

#: Почему «адреса нет» у домена без почты: писать на него некуда, и ступени не шли.
NO_MAIL_REASON = "домен сайта не принимает почту: у него нет ни MX, ни A"


@dataclass(frozen=True, slots=True)
class LadderResult:
    """Чем кончился поиск по одному домену."""

    host: str
    status: ContactStatus
    contact: Candidate | None = None
    candidates: tuple[Candidate, ...] = ()
    #: Отсеянные адреса с причиной: по ним настраивается фильтр.
    rejected: tuple[tuple[str, str], ...] = ()
    #: Ступень, давшая адрес. Пусто, если не дала ни одна.
    source: ContactSource | None = None
    has_form: bool = False
    #: Страница с формой — её открывает человек на «Формах». Пусто — формы нет
    #: или её адрес длиннее, чем имеет смысл хранить.
    form_url: str | None = None
    #: Телеграм, скайп, WhatsApp и телефон со страниц домена. Не влияют
    #: на `status`: он про адрес, и менять его смысл значит менять
    #: поведение всего, что читает исход. Читающий решает сам.
    handles: tuple[FoundHandle, ...] = ()
    #: Как домен сайта принимает почту, по ступени MX. Едет в исход, чтобы
    #: читающий итог решал по нему, слать ли письмо, а не угадывал.
    mail_route: MailRoute | None = None
    #: Почему сайт не ответил (`no_answer`) — или почему «адреса нет», когда сайт
    #: не прочитан вовсе: не пустил, показал проверку, закрыт для страны сервера.
    reason: str = ""
    #: Страниц сайта разобрано — обходом и браузером: итог поиска глубже называет число.
    pages_read: int = 0

    @property
    def found(self) -> bool:
        return self.status is ContactStatus.FOUND


@dataclass(frozen=True, slots=True)
class _Step:
    """Одна ступень лестницы: как её звать, что она делает, чем подписывает
    найденный адрес."""

    name: str
    run: Callable[[str, Collected], Awaitable[ContactStatus | None]]
    source: ContactSource


class ContactLadder:
    """Лестница для одного прогона. Счётчики общие на прогон, поиск — по домену.

    Платный провайдер необязателен: без него лестница работает на трёх
    бесплатных ступенях и честно отдаёт `not_found` там, где заплатила бы.
    Это не заглушка, а рабочий режим для отладки без расхода квоты.
    """

    #: Поиск глубже (`DeepLadder`, `deeper.py`): шире потолки обхода, карта сайта,
    #: браузер и для открытого сайта, потолок времени на домен.
    _deep = False

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        provider: ContactProvider | PaidUnavailable | None = None,
        manual_queue_left: int | None = None,
        paid_first: bool = False,
        renderer: browser_step.PageRenderer | None = None,
        stop_without_mail: bool = True,
        collect_handles: bool = False,
        silence: Callable[[], str | None] | None = None,
    ) -> None:
        self._http = http
        self._renderer = renderer
        self._manual_left = (
            manual_queue_left if manual_queue_left is not None else cfg.MANUAL_QUEUE_MONTHLY_CAP
        )
        self._paid_first = paid_first
        self._stop_without_mail = stop_without_mail
        # Каналы связи читает только прогон по файлу: в базе им места нет,
        # и путь базы платил за них вторым проходом по каждой странице.
        self._collect_handles = collect_handles
        # Крючок прохода: почему сайт не ответил (`sweep_trace.current_silence`).
        # Молчащему сайту платная ступень не зовётся, а исход — `no_answer`
        # с повтором по сроку (`attempts.py`). Без крючка лестница как прежде.
        self._silence = silence
        self.counters = StepCounters()
        # Платная ступень (`provider_step.py`): ключа нет — её нет совсем; квота кончилась,
        # учётку закрыли, проход без платной — есть, но не спросит (`PaidUnavailable`).
        self._paid = ProviderStep(provider, self.counters, silence=silence)

    @property
    def halt(self) -> str:
        """Почему проходу пора встать: платный сервис ограничил частоту и после повторов
        не отпустил (`provider.py`). Пусто — идём дальше. Следующий домен упёрся бы в тот
        же лимит: платная ступень больше не зовётся, домен — «недоспрошен»."""
        return self._paid.halt

    def _sequence(self) -> list[_Step]:
        """Порядок ступеней после MX.

        Умолчание — от бесплатных к платной: замер говорит, что свой
        парсинг снимает с платной около половины доменов и берёт адрес
        лучше (со страниц «advertise» отвечает тот, кто называет цену).

        `paid_first` переворачивает порядок: платный сервис отвечает
        за секунду против десятка секунд обхода страниц, и когда важнее
        скорость сбора, а не расход, это правильный размен. Цена размена
        честная: платных запросов становится столько же, сколько доменов.
        """
        free = [
            _Step("pages", self._step_pages, ContactSource.PAGE),
            _Step("browser", self._step_browser, ContactSource.PAGE),
            _Step("rdap", self._step_rdap, ContactSource.WHOIS),
        ]
        paid = _Step("provider", self._paid.run, ContactSource.PROVIDER)
        return [paid, *free] if self._paid_first else [*free, paid]

    async def find(self, host: str, *, last_try: bool = False) -> LadderResult:
        """Пройти лестницу по домену до первого адреса.

        `last_try` — сайт не отвечал проходами подряд, и этот последний:
        платная ступень зовётся и при молчании (`attempts.py`).
        """
        site_host = host.lower().removeprefix("www.")
        collected = Collected(site_host=site_host, last_try=last_try)

        route = await self._step_mx(site_host)
        # Вердикт едет в исход при любом конце спуска. Домен без почты к тому
        # же отсеивает адреса на себе (`Collected.add`): без этого при
        # `stop_without_mail=False` такой адрес уходил в итог найденным,
        # выигрывал у доставляемых и отбился бы при отправке.
        collected.route = route
        if route is MailRoute.NONE and self._stop_without_mail:
            # Домен не принимает почту — писать некуда, и дорогие ступени
            # ради него не работают. Но канал связи у него бывает: в СНГ
            # и Юго-Восточной Азии вебмастер оставляет телеграм, а почту
            # не держит вовсе. Тому, кто ищет и каналы, флаг это отключает.
            return LadderResult(
                host=site_host,
                status=ContactStatus.NOT_FOUND,
                mail_route=route,
                reason=NO_MAIL_REASON,
            )

        if not self._deep:
            return await self._descend(collected)
        try:
            async with asyncio.timeout(cfg.DEEP_TIMEOUT_SEC):
                return await self._descend(collected)
        except TimeoutError:
            # Сайт тянет, а человек ждёт итога у кнопки: то, что собрано, — в исход,
            # недособранное — причиной, а не молчанием.
            logger.info("контакты: поиск глубже по %s не уложился в срок", site_host)
            return self._settle(collected, guesses=True) or self._timed_out(collected)

    async def _descend(self, collected: Collected) -> LadderResult:
        """Ступени после MX — до первого прямого адреса.

        **Догадка — исход только после бесплатных ступеней.** Адрес из исходника или
        обфускации (`Candidate.guessed`) не прекращает спуск: прямой адрес со следующей
        ступени лучше (`quality.best`), а ступень до него не пошла бы. Бесплатные кончились
        — догадка и есть исход, и платить за домен с ней незачем.
        """
        # Отказ ступени запоминается, но спуск не прерывает: бесплатные
        # ступени ничего не стоят, и не дать им отработать из-за кончившейся
        # платной квоты значит потерять домен даром. В прежнем порядке это
        # было не видно — платная ступень стояла последней.
        unfinished: ContactStatus | None = None
        steps = self._sequence()
        free_left = sum(step.source is not ContactSource.PROVIDER for step in steps)
        origin: dict[str, str] = {}

        for step in steps:
            status = await step.run(collected.site_host, collected)
            unfinished = unfinished or status
            free_left -= step.source is not ContactSource.PROVIDER
            for candidate in collected.good:
                origin.setdefault(candidate.email, step.name)

            result = self._settle(collected, guesses=free_left == 0)
            if result is not None and result.contact is not None:
                self.counters.mark_found(origin.get(result.contact.email, step.name))
                return result

        if unfinished is not None and collected.has_form:
            # Платная ступень не спросила, а форма есть: сайт — в «Формы» сейчас, а не
            # когда вернётся квота или учётка (ревью 20.10.2026).
            return self._without_contact(collected)
        return self._unfinished(collected, unfinished) or self._without_contact(collected)

    def _timed_out(self, collected: Collected) -> LadderResult:
        """Поиск глубже не уложился в срок и адреса не собрал: «сайт не ответил» с
        причиной — повтор по сроку (`attempts.py`), а не «адреса нет» на полгода."""
        self.counters.no_answer += 1
        return LadderResult(
            host=collected.site_host,
            status=ContactStatus.NO_ANSWER,
            rejected=tuple(collected.rejected),
            has_form=collected.has_form,
            form_url=collected.form_url,
            handles=tuple(collected.handles),
            mail_route=collected.route,
            reason=f"поиск не уложился в {cfg.DEEP_TIMEOUT_SEC:.0f} с — сайт отвечал медленно",
            pages_read=collected.pages_read,
        )

    # --- ступени ---

    async def _step_mx(self, host: str) -> MailRoute:
        self.counters.mx_checked += 1
        route = await mail_route(host)
        if route is MailRoute.UNKNOWN:
            self.counters.mx_unknown += 1
        elif route not in DELIVERABLE:
            self.counters.mx_stopped += 1
            # Сообщение называет, что СЕЙЧАС произойдёт: при выключенном
            # `stop_without_mail` ступени как раз не пропускаются, а нулевой
            # MX не останавливает спуск никогда — прежний текст врал бы.
            if route is MailRoute.NONE and self._stop_without_mail:
                then = "ступени 1–3 пропущены"
            else:
                then = "адреса на нём отсеются, ищем сторонние и каналы связи"
            logger.info("контакты: %s не принимает почту — %s", host, then)
        return route

    async def _step_pages(self, host: str, collected: Collected) -> ContactStatus | None:
        """Страницы сайта (`site_steps.walk_pages`). Форму, если увидели, — в накопитель."""
        await site_steps.walk_pages(
            self._http,
            host,
            collected,
            self.counters,
            deep=self._deep,
            collect_handles=self._collect_handles,
        )
        return None

    async def _step_browser(self, host: str, collected: Collected) -> ContactStatus | None:
        """Браузер (`site_steps.render_site`): для закрывшегося сайта, глубже — для всех.

        Отрисованная страница разбирается тем же `harvest`, что и скачанная.
        До 30.09.2026 у браузера спрашивали одни адреса, и сайт, который
        открывает только он, оставался без каналов связи и без отметки
        о форме — ровно там, ради чего браузер включают.
        """
        await site_steps.render_site(
            self._renderer,
            self._http,
            host,
            collected,
            self.counters,
            deep=self._deep,
            collect_handles=self._collect_handles,
        )
        return None

    async def _step_rdap(self, host: str, collected: Collected) -> ContactStatus | None:
        if not cfg.RDAP_ENABLED:
            return None

        self.counters.rdap_entered += 1
        try:
            candidates = await rdap.find_emails(self._http, host)
        except rdap.RdapUnavailableError as exc:
            # Не отказ, а «не спросили»: домен пойдёт на платную ступень,
            # но знать об этом надо — иначе молчаливый ноль у ступени.
            self.counters.rdap_failed += 1
            logger.info("контакты: RDAP по %s не отработал — %s", host, exc)
            return None

        for candidate in candidates:
            collected.add(candidate)
        return None

    # --- исходы ---

    def _settle(self, collected: Collected, *, guesses: bool) -> LadderResult | None:
        """Собрать результат, если адрес уже есть: прямой — всегда, догадку — когда
        бесплатные ступени кончились (`guesses`, `_descend`)."""
        winner = best(
            collected.good if guesses else collected.direct, site_host=collected.site_host
        )
        if winner is None:
            return None
        return LadderResult(
            host=collected.site_host,
            status=ContactStatus.FOUND,
            contact=winner,
            candidates=tuple(collected.good),
            rejected=tuple(collected.rejected),
            source=winner.source,
            has_form=collected.has_form,
            form_url=collected.form_url,
            handles=tuple(collected.handles),
            mail_route=collected.route,
            pages_read=collected.pages_read,
        )

    def _unfinished(
        self, collected: Collected, unfinished: ContactStatus | None
    ) -> LadderResult | None:
        """Домен «недоспрошен», а не «без контакта»: квота, частота, поломка
        платной ступени — или сайт не ответил. Смешав это с «адреса нет», мы
        похоронили бы домен на полгода из-за минутного сбоя. Платный поиск выключен
        у прохода — «не спросили» (`paid_off`): очередь, которой его включат, возьмёт
        домен раньше срока."""
        reason = "" if unfinished is not None or self._silence is None else self._silence() or ""
        if unfinished is None and not reason:
            return None
        self.counters.no_answer += bool(reason)
        if unfinished is ContactStatus.PAID_OFF:
            # «Не спросили» — исход прочитанного сайта, как «адреса нет»: причина та же,
            # если сайт не прочитан вовсе (`Collected.unread_reason`).
            self.counters.paid_off += 1
            reason = collected.unread_reason
        return LadderResult(
            host=collected.site_host,
            status=unfinished or ContactStatus.NO_ANSWER,
            rejected=tuple(collected.rejected),
            has_form=collected.has_form,
            form_url=collected.form_url,
            handles=tuple(collected.handles),
            mail_route=collected.route,
            reason=reason,
            pages_read=collected.pages_read,
        )

    def _without_contact(self, collected: Collected) -> LadderResult:
        """Адреса нет. Осталось решить, идёт ли домен в ручную очередь.

        Сайт, не прочитанный вовсе, получает причину (`Collected.unread_reason`):
        «адреса нет» у сайта, который нас не пустил, — неправда, и человек решал бы
        по ней, искать ли глубже (живой замер 20.10.2026: 403 и проверка «не робот»
        у четырёх доноров из двенадцати с исходом «адреса нет»).
        """
        self.counters.rejected_emails += len(collected.rejected)

        if not collected.has_form:
            self.counters.not_found += 1
            return LadderResult(
                host=collected.site_host,
                status=ContactStatus.NOT_FOUND,
                rejected=tuple(collected.rejected),
                handles=tuple(collected.handles),
                mail_route=collected.route,
                reason=collected.unread_reason,
                pages_read=collected.pages_read,
            )

        self.counters.form_only += 1
        if self._manual_left > 0:
            self._manual_left -= 1
            self.counters.manual_queued += 1
        else:
            # Потолок «Форм» — ориентир темпа, а не запрет (решение Anthony 11.10.2026):
            # исход тот же, и строка очереди та же.
            logger.info(
                "контакты: форм за проход больше ориентира темпа «Форм» — %s всё равно "
                "«только форма»: ориентир ничего не держит",
                collected.site_host,
            )
        return LadderResult(
            host=collected.site_host,
            status=ContactStatus.FORM_ONLY,
            rejected=tuple(collected.rejected),
            has_form=True,
            form_url=collected.form_url,
            handles=tuple(collected.handles),
            mail_route=collected.route,
            pages_read=collected.pages_read,
        )

    @property
    def deep(self) -> bool:
        """Глубже ли эта лестница — для отчёта и тестов."""
        return self._deep

    @property
    def manual_queue_left(self) -> int:
        """Сколько форм этого прохода ещё в ориентире темпа. Счёт — от ориентира
        на каждый проход, а не от остатка за 30 дней (`forms.monthly_left`)."""
        return self._manual_left


class DeepLadder(ContactLadder):
    """Лестница поиска глубже — одному донору по кнопке (`deeper.py`). Ступени те же,
    шире — их потолки (`site_steps`); платную ступень вызывающий не подключает."""

    _deep = True
