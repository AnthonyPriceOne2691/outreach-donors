"""Окончательно ли пройден домен: что сайт ответил, пока по нему шла лестница.

Лестница отвечает «адреса нет» и тогда, когда сайт не открылся: обрыв
связи, таймауты и отказы 401/403/429 `PageFetcher` превращает в обычное
«не нашли». Прогон по файлу писал такой исход в чекпойнт окончательным,
и стоило связи лечь на час, как все домены этого часа считались
пройденными: возобновление печатало «уже пройдены» и не делало ни одного
запроса (ревью #118, находка 5).

Поэтому прогон смотрит на сам обмен с сайтом — через крючки HTTP-клиента,
по домену на задачу, — и задаёт три вопроса:

- **ответил ли сайт по существу:** отдал страницу или окончательный ответ
  вроде 404. Отказы 401/403/429, ошибки 5xx и запросы без ответа — нет;
- **не оборвался ли обход после этого:** отказ, 5xx или запрос без
  ответа ПОСЛЕ открытой страницы. До неё это поиск рабочего вида главной
  (апекс без записи, `https` без сервера), а не обрыв;
- **открыл ли его браузер**, если обычному запросу сайт отказал.

Исключение одно, и его нашёл живой прогон: имени нет. Ступень MX говорит
«почту принимать некому» (`MailRoute.NONE`) — это ответ работающего DNS,
при обрыве связи она говорит «неизвестно». Если при этом сайт не ответил
ни разу, ничем, повтор ответа не изменит: без исключения мёртвые имена
старого списка шли бы заново на каждом запуске, вечно.

Хосты сайта — его имя с `www.` и без, а также всё, куда ведут его
редиректы: домен, переехавший на новое имя, отвечает уже оттуда. Чужие
хосты (RDAP, например) в счёт не идут.

Граница метода: тело ответа, оборванное после заголовков, засчитывается
как ответ — крючок видит заголовки, а не чтение тела.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import httpx

from backend.features.contacts.browser import PageRenderer
from backend.features.contacts.ladder import LadderResult
from backend.features.contacts.mx import MailRoute
from backend.features.core.domain import ContactStatus

#: Исходы лестницы, после которых домен можно считать пройденным. Прочие
#: (квота, частота, поломка платной ступени) — «недоспрошен» по определению.
CONCLUSIVE = frozenset({ContactStatus.FOUND, ContactStatus.NOT_FOUND, ContactStatus.FORM_ONLY})

#: Отказ сайта: он есть, но нас не пускает. Повтор бывает успешным — 429
#: проходит, 403 открывает браузер.
REFUSALS = frozenset({401, 403, 429})

#: След текущего домена. Задача прогона ставит свой, и запросы, сделанные
#: в ней, пишутся в него — параллельные домены друг другу не мешают.
_CURRENT: ContextVar[SiteTrace | None] = ContextVar("contacts_site_trace", default=None)


@dataclass(slots=True)
class SiteTrace:
    """Обмен с одним сайтом за один проход лестницы."""

    hosts: set[str]
    #: Сайт отдал страницу, которую лестница читает: 2xx и HTML.
    opened: bool = False
    #: Сайт ответил окончательно, но такой страницы не дал: 404, 410, не HTML.
    answered: bool = False
    #: Первая беда ПОСЛЕ открытой страницы: обход оборван.
    trouble: str = ""
    #: Последняя беда, пока сайт не открылся: почему не открылся.
    failure: str = ""
    #: Страниц, открытых браузером.
    rendered: int = 0
    #: Сайт ответил хоть чем-то: страницей, отказом, ошибкой, редиректом.
    #: Не ответил ничем — прогон спрашивает контрольные адреса: легла сеть
    #: или сайт (`file_sweep.network_alive`).
    heard: bool = False
    #: Запрос, ответа на который ещё нет. Лестница ходит по домену
    #: последовательно, и следующий запрос значит, что этот кончился отказом.
    _waiting: str = field(default="", repr=False)

    @classmethod
    def of(cls, site_host: str) -> SiteTrace:
        return cls(hosts={site_host, f"www.{site_host}"})

    def owns(self, url: httpx.URL) -> bool:
        return (url.host or "").lower() in self.hosts

    def sent(self, url: httpx.URL) -> None:
        if self._waiting:
            self._went_wrong(self._waiting, "нет ответа: обрыв или таймаут")
        self._waiting = str(url)

    def received(self, response: httpx.Response) -> None:
        self._waiting = ""
        self.heard = True
        url = str(response.request.url)
        status = response.status_code
        if response.has_redirect_location:
            # Редирект — шаг, а не ответ: ответит тот адрес, куда он ведёт,
            # и этот адрес теперь тоже сайт.
            target = urlsplit(urljoin(url, response.headers["location"])).hostname
            if target:
                self.hosts.add(target.lower())
        elif status in REFUSALS:
            self._went_wrong(url, f"сайт закрылся: {status}")
        elif status >= 500:
            self._went_wrong(url, f"ошибка сервера: {status}")
        elif status < 300 and "html" in response.headers.get("content-type", "").lower():
            # То же условие, что у `PageFetcher`: 2xx без HTML лестница не читает
            # и пробует следующий вид главной — это ещё поиск, а не обход.
            self.opened = True
        else:
            self.answered = True

    def close(self) -> None:
        """Проход кончился: запрос, так и не получивший ответа, — отказ."""
        if self._waiting:
            self._went_wrong(self._waiting, "нет ответа: обрыв или таймаут")
            self._waiting = ""

    def _went_wrong(self, url: str, what: str) -> None:
        if self.opened:
            self.trouble = self.trouble or f"обход оборван на {urlsplit(url).path or '/'} — {what}"
        else:
            self.failure = what

    @property
    def reached(self) -> bool:
        """Сайт ответил по существу и обход не оборвался — или его открыл браузер.

        Открытая страница — ответ по существу, даже если до неё не ответил
        другой вид главной. Ответ без страницы (404, не HTML) — только если
        не отказал ни один вид: «апекс 404, `www` молчит» ещё не ответ, `www`
        мог открыться при повторе (ревью #126). Цена — мёртвый сайт с таким
        апексом пойдёт снова, но не больше `sweep_checkpoint.MAX_ATTEMPTS` раз.
        """
        if self.rendered > 0:
            return True
        if self.trouble:
            return False
        return self.opened or (self.answered and not self.failure)

    def retry_reason(self, result: LadderResult) -> str | None:
        """Почему исход не окончательный. `None` — домен пройден.

        Найденный адрес окончателен, даже если часть страниц не открылась:
        лучше он или хуже возможного, писать по нему уже можно.
        """
        if result.status not in CONCLUSIVE:
            return f"лестница не закончила: {result.status.value}"
        if result.found or self.reached:
            return None
        if result.mail_route is MailRoute.NONE and not self.heard and not self.rendered:
            return None  # имени нет — см. модуль
        return self.trouble or self.failure or "сайт не ответил"


@contextmanager
def traced(site_host: str) -> Iterator[SiteTrace]:
    """След обмена с сайтом на время прохода лестницы по нему."""
    trace = SiteTrace.of(site_host)
    token = _CURRENT.set(trace)
    try:
        yield trace
    finally:
        _CURRENT.reset(token)
        trace.close()


async def _on_request(request: httpx.Request) -> None:
    trace = _CURRENT.get()
    if trace is not None and trace.owns(request.url):
        trace.sent(request.url)


async def _on_response(response: httpx.Response) -> None:
    trace = _CURRENT.get()
    if trace is not None and trace.owns(response.request.url):
        trace.received(response)


def watch(http: httpx.AsyncClient) -> None:
    """Подключить к клиенту учёт обмена. Вне `traced` крючки молчат."""
    hooks = http.event_hooks
    hooks["request"].append(_on_request)
    hooks["response"].append(_on_response)
    http.event_hooks = hooks


class TracedRenderer:
    """Браузер, чьи открытия засчитываются домену: страница, которую отдал
    браузер, — тоже ответ сайта, закрывшегося от обычного запроса."""

    def __init__(self, inner: PageRenderer) -> None:
        self._inner = inner

    async def render(self, url: str) -> str | None:
        html = await self._inner.render(url)
        trace = _CURRENT.get()
        if html and trace is not None:
            trace.rendered += 1
        return html
