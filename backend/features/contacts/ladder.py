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

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx

from backend.config import contacts as cfg
from backend.features.contacts import browser as browser_step
from backend.features.contacts import rdap
from backend.features.contacts.extract import (
    extract_emails,
    extract_obfuscated,
    find_contact_links,
)
from backend.features.contacts.messengers import FoundHandle, harvest_handles
from backend.features.contacts.mx import DELIVERABLE, MailRoute, mail_route
from backend.features.contacts.pages import (
    FetchedPage,
    PageFetcher,
    has_contact_form,
    language_hint,
    page_queue,
    slug_urls,
)
from backend.features.contacts.provider import (
    ContactProvider,
    ProviderBlockedError,
    ProviderError,
    ProviderQuotaError,
    ProviderRateLimitError,
)
from backend.features.contacts.quality import Candidate, best, rejection_reason, trusted_guess
from backend.features.contacts.slugs import LINK_MARKERS
from backend.features.contacts.step_counters import StepCounters
from backend.features.core.domain import ContactSource, ContactStatus, PageKind

logger = logging.getLogger(__name__)


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
    #: Телеграм, скайп, WhatsApp и телефон со страниц домена. Не влияют
    #: на `status`: он про адрес, и менять его смысл значит менять
    #: поведение всего, что читает исход. Читающий решает сам.
    handles: tuple[FoundHandle, ...] = ()
    #: Как домен сайта принимает почту, по ступени MX. Едет в исход, чтобы
    #: читающий итог решал по нему, слать ли письмо, а не угадывал.
    mail_route: MailRoute | None = None

    @property
    def found(self) -> bool:
        return self.status is ContactStatus.FOUND


@dataclass(frozen=True, slots=True)
class _Step:
    """Одна ступень лестницы: как её звать, что она делает, чем подписывает
    найденный адрес."""

    name: str
    run: Callable[[str, _Collected], Awaitable[ContactStatus | None]]
    source: ContactSource


@dataclass(slots=True)
class _Collected:
    """Накопитель годных и отсеянных адресов по одному домену."""

    site_host: str
    good: list[Candidate] = field(default_factory=list)
    #: Каналы связи, кроме почты. Отдельно от `good`, потому что отбор
    #: адресов их не касается: у ника нет ни домена, ни ролевой части,
    #: по которым адрес взвешивают, и отсеивать его нечем.
    handles: set[FoundHandle] = field(default_factory=set)
    rejected: list[tuple[str, str]] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    has_form: bool = False
    #: Сайт закрылся от обычного запроса: 401, 403, 429. Только такие
    #: и имеет смысл открывать браузером — он стоит секунд на страницу.
    blocked: bool = False
    #: Вердикт ступени MX по домену сайта.
    route: MailRoute | None = None

    def _undeliverable(self, email: str) -> str | None:
        """Адрес на домене сайта, который почту не принимает, отбился бы.

        Сверяется имя целиком: вердикт MX — про этот домен, и поддомен без
        почты ничего не говорит о почте родительского. Личный ящик на чужом
        домене (`owner@gmail.com`) остаётся: он доставляем.
        """
        if email.lower().rsplit("@", 1)[-1] != self.site_host:
            return None
        if self.route is MailRoute.NONE:
            return f"домен сайта не принимает почту (ни MX, ни A): {email}"
        if self.route is MailRoute.NULL_MX:
            return f"домен сайта объявил, что почту не принимает (нулевой MX): {email}"
        return None

    def add(self, candidate: Candidate) -> bool:
        """Взять адрес, если он годный и ещё не встречался."""
        if candidate.email in self.seen:
            return False
        self.seen.add(candidate.email)

        reason = rejection_reason(candidate.email) or self._undeliverable(candidate.email)
        if reason:
            self.rejected.append((candidate.email, reason))
            return False
        self.good.append(candidate)
        return True


class ContactLadder:
    """Лестница для одного прогона. Счётчики общие на прогон, поиск — по домену.

    Платный провайдер необязателен: без него лестница работает на трёх
    бесплатных ступенях и честно отдаёт `not_found` там, где заплатила бы.
    Это не заглушка, а рабочий режим для отладки без расхода квоты.
    """

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        provider: ContactProvider | None = None,
        manual_queue_left: int | None = None,
        paid_first: bool = False,
        renderer: browser_step.PageRenderer | None = None,
        stop_without_mail: bool = True,
        collect_handles: bool = False,
    ) -> None:
        self._http = http
        self._provider = provider
        self._renderer = renderer
        self._manual_left = (
            manual_queue_left if manual_queue_left is not None else cfg.MANUAL_QUEUE_MONTHLY_CAP
        )
        self._paid_first = paid_first
        self._stop_without_mail = stop_without_mail
        # Каналы связи читает только прогон по файлу: в базе им места нет,
        # и путь базы платил за них вторым проходом по каждой странице.
        self._collect_handles = collect_handles
        self.counters = StepCounters()

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
        paid = _Step("provider", self._step_provider, ContactSource.PROVIDER)
        return [paid, *free] if self._paid_first else [*free, paid]

    async def find(self, host: str) -> LadderResult:
        """Пройти лестницу по домену до первого адреса."""
        site_host = host.lower().removeprefix("www.")
        collected = _Collected(site_host=site_host)

        route = await self._step_mx(site_host)
        # Вердикт едет в исход при любом конце спуска. Домен без почты к тому
        # же отсеивает адреса на себе (`_Collected.add`): без этого при
        # `stop_without_mail=False` такой адрес уходил в итог найденным,
        # выигрывал у доставляемых и отбился бы при отправке.
        collected.route = route
        if route is MailRoute.NONE and self._stop_without_mail:
            # Домен не принимает почту — писать некуда, и дорогие ступени
            # ради него не работают. Но канал связи у него бывает: в СНГ
            # и Юго-Восточной Азии вебмастер оставляет телеграм, а почту
            # не держит вовсе. Тому, кто ищет и каналы, флаг это отключает.
            return LadderResult(host=site_host, status=ContactStatus.NOT_FOUND, mail_route=route)

        # Отказ ступени запоминается, но спуск не прерывает: бесплатные
        # ступени ничего не стоят, и не дать им отработать из-за кончившейся
        # платной квоты значит потерять домен даром. В прежнем порядке это
        # было не видно — платная ступень стояла последней.
        unfinished: ContactStatus | None = None

        for step in self._sequence():
            status = await step.run(site_host, collected)
            unfinished = unfinished or status

            result = self._settle(collected, step.source, has_form=collected.has_form)
            if result is not None:
                self.counters.mark_found(step.name)
                return result

        if unfinished is not None:
            # Квота, частота или поломка: домен не «без контакта», а
            # «недоспрошен». Смешав их, мы похоронили бы его навсегда.
            return LadderResult(
                host=site_host,
                status=unfinished,
                rejected=tuple(collected.rejected),
                has_form=collected.has_form,
                handles=tuple(collected.handles),
                mail_route=route,
            )

        return self._without_contact(collected, has_form=collected.has_form)

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

    async def _step_pages(self, host: str, collected: _Collected) -> ContactStatus | None:
        """Страницы сайта. Форму, если увидели, записывает в накопитель.

        Обход останавливается, как только адрес найден на странице дорогого
        вида: у страниц «advertise» и «write for us» адрес лучше, и искать
        после них нечего.
        """
        self.counters.pages_entered += 1
        fetcher = PageFetcher(self._http)

        home = await fetcher.home(host)
        if home is None:
            # Сайт не открылся ни в одном виде. Угадывать по нему слаги
            # бессмысленно: это ещё три десятка запросов в ту же стену.
            self.counters.pages_fetched += fetcher.attempts
            collected.blocked = fetcher.blocked
            if fetcher.blocked:
                self.counters.pages_blocked += 1
            return None

        self._harvest(home, collected, host)

        # Ссылки с главной идут впереди угадываемых слагов: там раздел
        # назван словами и лежит по любому адресу, хоть /p/12345.
        links = find_contact_links(home.html, slugs=LINK_MARKERS)
        # Язык берём с главной: она уже скачана, а слаги угадываются после.
        # Без этого локальная площадка не пробуется на своём языке вовсе —
        # потолок попыток кончается на английских слагах.
        language = language_hint(home.html, host)
        queue = page_queue(fetcher.follow(home, links), slug_urls(home.url, language=language))
        visited = {home.url}

        for url, kind in queue:
            if url in visited or fetcher.exhausted:
                continue
            visited.add(url)

            page = await fetcher.get(url, kind)
            if page is None:
                continue

            self._harvest(page, collected, host)
            if collected.good and kind in (PageKind.MONEY, PageKind.CONTACT):
                # Адрес со страницы дорогого вида искать дальше незачем:
                # лучше него на сайте ничего нет.
                break

        self.counters.pages_fetched += fetcher.attempts
        collected.blocked = fetcher.blocked
        if fetcher.blocked:
            self.counters.pages_blocked += 1
        return None

    def _harvest(self, page: FetchedPage, collected: _Collected, site_host: str) -> None:
        """Снять со страницы всё, что похоже на адрес, и заметить форму."""
        collected.has_form = collected.has_form or has_contact_form(page.html)
        if self._collect_handles:
            collected.handles |= harvest_handles(page.html, page_kind=page.kind, page_url=page.url)

        for email in extract_emails(page.html):
            collected.add(Candidate(email, ContactSource.PAGE, page.kind, page_url=page.url))
        for email in extract_obfuscated(page.html):
            # Угаданному адресу верим только на домене сайта: иначе обычная
            # фраза «meet at the dot com» станет контактом.
            if trusted_guess(email, site_host=site_host):
                collected.add(Candidate(email, ContactSource.PAGE, page.kind, page_url=page.url))

    async def _step_browser(self, host: str, collected: _Collected) -> ContactStatus | None:
        """Рендер настоящим браузером — только для того, что не открылось.

        Ступень дорогая: секунды на страницу. Поэтому она смотрит домен,
        только если обычный обход остался ни с чем. Домен, с которого адрес
        уже снят, браузер не видит вовсе.

        Отрисованная страница разбирается тем же `_harvest`, что и скачанная.
        До 30.09.2026 у браузера спрашивали одни адреса, и сайт, который
        открывает только он, оставался без каналов связи и без отметки
        о форме — ровно там, ради чего браузер включают.
        """
        if self._renderer is None or collected.good or not collected.blocked:
            # Браузер смотрит только тех, кто закрыл дверь. Сайт, который
            # открылся и просто не показал адреса, рендером не исправить:
            # замер на шести таких доменах дал ноль адресов.
            return None

        self.counters.browser_entered += 1
        # `follow` обычного обхода — ради одного правила ссылок на оба пути;
        # запросов он не делает, клиент ему не нужен.
        follow = PageFetcher(self._http).follow
        for page in await browser_step.render_pages(self._renderer, host, follow=follow):
            self._harvest(page, collected, host)
        return None

    async def _step_rdap(self, host: str, collected: _Collected) -> ContactStatus | None:
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

    def _note_refusal(self, reason: str) -> None:
        """Записать отказ ступени. Первый отказ сохраняется дословно:
        последующие обычно тот же самый, а первый ближе к причине."""
        self.counters.provider_refused += 1
        if not self.counters.provider_refusal:
            self.counters.provider_refusal = reason

    async def _step_provider(self, host: str, collected: _Collected) -> ContactStatus | None:
        """Платная ступень. Возвращает исход, если платить не вышло."""
        if self._provider is None or collected.route is MailRoute.NULL_MX:
            # Платный сервис ищет адреса на самом домене, а домен с нулевым MX
            # объявил, что почту не принимает: всё, что он отдаст, отобьётся.
            why = "не подключена" if self._provider is None else "нулевой MX"
            logger.debug("контакты: платная ступень по %s не зовётся — %s", host, why)
            return None

        self.counters.provider_entered += 1
        try:
            candidates = await self._provider.find_emails(host)
        except ProviderBlockedError as exc:
            self._note_refusal(str(exc))
            # Раньше этот случай приезжал сюда как «частота» и лестница
            # бодро шла по следующему домену. Ловится первым: он потомок
            # ProviderError, и порядок веток решает.
            logger.exception("контакты: платный сервис закрыл учётку — %s", exc)
            return ContactStatus.BLOCKED
        except ProviderQuotaError as exc:
            self._note_refusal(str(exc))
            logger.warning("контакты: квота платного сервиса исчерпана на %s — %s", host, exc)
            return ContactStatus.NO_QUOTA
        except ProviderRateLimitError as exc:
            self._note_refusal(str(exc))
            logger.warning("контакты: платный сервис ограничил частоту на %s — %s", host, exc)
            return ContactStatus.RATE_LIMITED
        except ProviderError as exc:
            self._note_refusal(str(exc))
            logger.exception("контакты: платный сервис не ответил по %s — %s", host, exc)
            return ContactStatus.ERROR

        for candidate in candidates:
            collected.add(candidate)
        return None

    # --- исходы ---

    def _settle(
        self, collected: _Collected, source: ContactSource, *, has_form: bool
    ) -> LadderResult | None:
        """Собрать результат, если на этой ступени адрес уже есть."""
        winner = best(collected.good, site_host=collected.site_host)
        if winner is None:
            return None
        return LadderResult(
            host=collected.site_host,
            status=ContactStatus.FOUND,
            contact=winner,
            candidates=tuple(collected.good),
            rejected=tuple(collected.rejected),
            source=winner.source if winner.source else source,
            has_form=has_form,
            handles=tuple(collected.handles),
            mail_route=collected.route,
        )

    def _without_contact(self, collected: _Collected, *, has_form: bool) -> LadderResult:
        """Адреса нет. Осталось решить, идёт ли домен в ручную очередь."""
        self.counters.rejected_emails += len(collected.rejected)

        if not has_form:
            self.counters.not_found += 1
            return LadderResult(
                host=collected.site_host,
                status=ContactStatus.NOT_FOUND,
                rejected=tuple(collected.rejected),
                handles=tuple(collected.handles),
                mail_route=collected.route,
            )

        self.counters.form_only += 1
        if self._manual_left > 0:
            self._manual_left -= 1
            self.counters.manual_queued += 1
        else:
            logger.info(
                "контакты: потолок ручной очереди исчерпан, %s ждёт следующего месяца",
                collected.site_host,
            )
        return LadderResult(
            host=collected.site_host,
            status=ContactStatus.FORM_ONLY,
            rejected=tuple(collected.rejected),
            has_form=True,
            handles=tuple(collected.handles),
            mail_route=collected.route,
        )

    @property
    def manual_queue_left(self) -> int:
        """Сколько форм ещё готовы принять руками в этом месяце."""
        return self._manual_left
