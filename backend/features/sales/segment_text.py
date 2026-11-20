"""Текст сайта для судьи сегмента: строки с подписями — выдача, шапка главной, отрывок её текста.

Строка — кусок текста сайта со своей подписью (`Заголовок в выдаче: …`). Подписи — наши слова, а
не сайта: цитировать их нельзя (`shared/quote.unlabeled` снимает их из цитаты), по ним же строки
выдачи и главной различаются (`SiteText.without_stub`) и текст хранится у кандидата (`site_text`) —
пересуд и заведение лида читают его обратно (`SiteText.parse`). Сюда же — язык текста против языка
писем (`site_language`): тем же решением, по тем же строкам, в вердикте судьи, при пересуде, при
отмене решения и у лида — по сохранённому тексту строки те же, подписи при них.

Язык сайта меряется только по словам сайта — строкам выдачи, главной и страницы из выдачи
(`SITE_LABELS`): служебная строка со своими словами его не решает. Версия на языке писем у сайта есть
— несовпадения нет: в меню или тексте главной (или в тексте страницы из выдачи) переключатель на этот
язык (`SWITCH_LABELS`) или страница выдачи на нём (`SERP_LABELS`, `segment_language.written_in`) —
главная приходит на языке загрузчика, а поисковик показал страницу на языке писем.

**Главная не открылась — страница из выдачи** (`serp_page`): ни главная поддомена, ни главная корня
не открылись — судья скачивает саму страницу, которую показал поисковик, тем же загрузчиком (защита
адреса и размер — у клиента, `shared/net/url_guard.guarded_client`; язык, проверка «вы не робот» и
потолок разбора — общий загрузчик страниц `contacts/pages`; разбор и заглушка — общие `read_home` и
`looks_denied`) и видит её текст строкой `PAGE_TEXT` — на месте текста главной, тем же потолком. Замер
19.11 на прогонах сбора: 2 сайта из 102 ушли человеку словами «главная не открылась — судья видел
только сниппет выдачи». Строки главной и строка страницы вместе не бывают: страницу спрашивают, только
когда главная не открылась. Общего разбора страницы по адресу рядом с `check_home` пока нет — загрузчик
зовётся здесь, одной попыткой и только для текста судьи, а не как ступень поиска адреса.

Попытка страницы — **коротким сроком соединения**, как главная у доноров, дверей и ключей
(`home_timeout`: мёртвый сайт молчит на соединении), а главные судьи — прежним сроком клиента продаж
(`_Through`). **Конечный адрес — того же сайта**, что и исходный (`_on_site`: домен базы или его
поддомен): загрузчик идёт по переходам на любой домен, и текст парковки «домен продаётся» лёг бы
строкой страницы. Увела на чужой сайт — как не открылась: строки нет.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from backend.config import sales as sales_cfg
from backend.features.contacts.pages import FetchedPage, PageFetcher
from backend.features.core.domain import PageKind
from backend.features.donors.home_signals import (
    HomeSignals,
    check_home,
    home_timeout,
    looks_denied,
    read_home,
)
from backend.features.sales import segment_language
from backend.shared.net.idn import ascii_host
from backend.shared.quote import quote_holds

logger = logging.getLogger(__name__)

#: Сколько знаков текста сайта видит модель: сниппет и шапка главной короткие, потолок —
#: против аномалии страницы, а не ради экономии. Отрывок текста главной — сверх него, своим потолком
#: (`SALES_SEGMENT_HOME_TEXT_CHARS`).
MAX_TEXT_CHARS = 2000
#: Подписи строк текста сайта: свои слова, не сайта — цитировать их нельзя (модель цитирует —
#: подпись снимается, `shared/quote.unlabeled`). Строки выдачи и главной различаются по ним же.
PAGE_TITLE = "Заголовок в выдаче"
PAGE_DESCRIPTION = "Описание в выдаче"
HOME_TITLE = "Заголовок главной"
HOME_DESCRIPTION = "Описание главной"
HOME_MENU = "Меню главной"
HOME_TEXT = "Текст главной"
#: Текст страницы, которую показал поисковик, — когда главная не открылась (`serp_page`).
PAGE_TEXT = "Текст страницы из выдачи"
#: Почему страницы из выдачи нет, хоть она и ответила: переход увёл с сайта базы (`serp_page`).
ELSEWHERE = "увела на чужой сайт"
SERP_LABELS = (PAGE_TITLE, PAGE_DESCRIPTION)
HOME_LABELS = (HOME_TITLE, HOME_DESCRIPTION, HOME_MENU, HOME_TEXT)
PAGE_LABELS = (PAGE_TEXT,)
LABELS = (*SERP_LABELS, *HOME_LABELS, *PAGE_LABELS)
#: Строки, по которым узнаётся заглушка защиты. Текст главной и страницы — нет: в нём законно
#: «captcha» (подпись reCAPTCHA в подвале) или «forbidden», а закрытую страницу узнаёт её заголовок
#: (общий `check_home`, `serp_page`: заглушка вместо страницы — страница не открылась).
STUB_LABELS = (*SERP_LABELS, HOME_TITLE, HOME_DESCRIPTION, HOME_MENU)
#: Строки, где сайт показывает переключатель версий на других языках (`segment_language.switches_to`).
SWITCH_LABELS = (HOME_MENU, HOME_TEXT, PAGE_TEXT)
#: Строки, по которым меряется язык сайта (`site_language`): только слова сайта — выдача, главная и
#: страница из выдачи. Не `LABELS`: строка со своими словами, если ляжет в текст, язык сайта не решает.
SITE_LABELS = (*SERP_LABELS, *HOME_LABELS, *PAGE_LABELS)
#: Строки, которые судья скачал с самого сайта: главная или страница из выдачи. Нет ни одной — судья
#: видел только сниппет выдачи (`segment_facts.SiteFacts.snippet_only`).
FETCHED_LABELS = (*HOME_LABELS, *PAGE_LABELS)


@dataclass(frozen=True, slots=True)
class SiteText:
    """Текст сайта для судьи — по строке на кусок: (подпись, слова сайта)."""

    lines: tuple[tuple[str, str], ...]

    @property
    def empty(self) -> bool:
        return not self.lines

    def for_model(self) -> str:
        """Как его видит модель — и как он хранится у кандидата."""
        return "\n".join(f"{label}: {words}" for label, words in self.lines)

    def holds(self, quote: str) -> bool:
        """Цитата — внутри одной строки сайта, с точностью до типографики и опечатки
        переписывания (`shared/quote.quote_holds`): модель переносит строки и ставит знаки
        иначе, чем сайт, и честная цитата иначе не прошла бы."""
        return any(quote_holds(quote, words) for _, words in self.lines)

    def _of(self, labels: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
        return tuple(line for line in self.lines if line[0] in labels)

    @property
    def stubbed(self) -> bool:
        """Есть строка-отказ или заглушка защиты: проверка — по строкам, словами без подписи
        (общий `looks_denied`; строку целиком из «Checking…» он узнаёт только без подписи). Текст
        главной не проверяется (`STUB_LABELS`)."""
        return any(looks_denied(words) for label, words in self.lines if label in STUB_LABELS)

    def without_stub(self) -> SiteText:
        """Текст для модели: заглушка защиты только в строках главной («проверка браузера»
        вместо сайта) — строки главной вон, судится выдача: она пришла от поисковика, а не от
        защиты сайта. Заглушка в выдаче или выдачи нет — текст как есть: его отдаст человеку
        `by_text`."""
        serp = SiteText(self._of(SERP_LABELS))
        if serp.empty or serp.stubbed or not SiteText(self._of(HOME_LABELS)).stubbed:
            return self
        return serp

    @classmethod
    def parse(cls, stored: str) -> SiteText:
        """Текст, сохранённый у кандидата, — обратно в строки: по нему сверяется цитата."""
        lines: list[tuple[str, str]] = []
        for line in stored.splitlines():
            label, _, words = line.partition(": ")
            if label in LABELS and words:
                lines.append((label, words))
        return cls(tuple(lines))


def excerpt(text: str | None, limit: int) -> str:
    """Отрывок не длиннее `limit` знаков: пробелы схлопнуты, срез — по границе слова (письменность
    без пробелов — по знаку)."""
    clean = " ".join((text or "").split())
    if len(clean) <= limit:
        return clean
    cut = clean[:limit]
    return cut.rsplit(" ", 1)[0] if " " in cut else cut


def _home_lines(home: HomeSignals | None) -> list[tuple[str, str | None]]:
    """Шапка главной строками судьи; главная не открылась — строк нет."""
    if home is None or not home.reached:
        return []
    menu = " · ".join(home.nav) if home.nav else None
    return [(HOME_TITLE, home.title), (HOME_DESCRIPTION, home.description), (HOME_MENU, menu)]


async def page_home(
    client: httpx.AsyncClient, host: str, page_url: str | None, *, language: str | None = None
) -> HomeSignals:
    """Главная для судьи — хоста страницы выдачи, а не только корня: у поддомена страны или города
    своя главная (замер 20.10 на наборе трёх гипотез: 14 из 191 страницы выдачи — на поддомене, у 9
    из них главная поддомена другая или главная корня не открылась). Главная поддомена не открылась
    — главная корня (`host`, домен базы). Разбор главной — общий `check_home`. Не открылась и она —
    почему (`HomeSignals.error`) — в журнал прохода с доменом: судья спросит саму страницу из выдачи
    (`serp_page`), а не откроется и та — видит только выдачу, и «не понять» скажет это человеку
    словами (`segment_rules.SNIPPET_ONLY`).

    `language` — язык писем прогона: главная просится на нём, у поддомена и корня одинаково. Сайт с
    версиями по языку браузера отдавал английскую главную и при прогоне на другом языке (проверка
    22.10.2026): модель читала не тот текст, что видят человек и письмо. Без языка — как раньше
    (общий английский заголовок). Пересуд по сохранённому тексту главную не качает вовсе."""
    # Хост ссылки выдачи — в ASCII, как `host` базы: ссылка письменностью иначе не была бы поддоменом.
    page_host = ascii_host(urlsplit(page_url).hostname or "") if page_url else ""
    if page_host.endswith(f".{host}") and page_host != f"www.{host}":
        found = await check_home(client, page_host, language=language)
        if found.reached:
            return found
    root = await check_home(client, host, language=language)
    if not root.reached:
        logger.info(
            "судья сегмента: главная не открылась",
            extra={"host": host, "page_host": page_host or None, "error": root.error},
        )
    return root


def _on_site(host: str, url: str) -> bool:
    """Адрес — `http(s)` на домене базы `host` или его поддомене (`www.` — тоже поддомен). Хост — в
    ASCII, как `host` базы: адрес письменностью иначе не был бы своим сайтом."""
    parts = urlsplit(url)
    page_host = ascii_host(parts.hostname or "")
    if parts.scheme not in ("http", "https") or not page_host:
        return False
    return page_host == host or page_host.endswith(f".{host}")


def _page_to_read(host: str, page_url: str | None) -> str | None:
    """Адрес страницы из выдачи, которую стоит скачать вместо главной: своего сайта (`_on_site`) и
    не сама главная — её уже спросил `page_home`; иначе `None`."""
    if not page_url or not _on_site(host, page_url):
        return None
    parts = urlsplit(page_url)
    return page_url if parts.path not in ("", "/") or parts.query else None


class _Through(httpx.AsyncBaseTransport):
    """Запросы — через клиент продаж: его пул, защита адреса и потолок ответа (`url_guard`), а
    срок — у запроса, того клиента, что его построил. Так одна попытка страницы из выдачи берёт свой
    короткий срок (`serp_page`), а срок общего клиента не трогается: соседние сайты окна судьи в то
    же время ходят им за главными. Переходы ведёт внешний клиент — конечный адрес в его ответе."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._client.send(request, stream=True, follow_redirects=False)


async def _one_try(
    client: httpx.AsyncClient, host: str, url: str, language: str | None
) -> tuple[FetchedPage | None, str]:
    """Одна попытка страницы из выдачи общим загрузчиком — коротким сроком соединения (шапка
    модуля). Конечный адрес не своего сайта (`_on_site`) — страницы нет, как у не открывшейся.
    Возвращает страницу и почему её нет — слова отказа загрузчика или `ELSEWHERE`."""
    async with httpx.AsyncClient(transport=_Through(client), timeout=home_timeout()) as short:
        fetcher = PageFetcher(short, max_pages=1, max_attempts=1, language=language)
        try:
            fetched = await fetcher.get(url, PageKind.HOME)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
            logger.info(
                "судья сегмента: страница из выдачи упала", extra={"host": host, "error": repr(exc)}
            )
            fetched = None
    if fetched is not None and not _on_site(host, fetched.url):
        logger.info(
            "судья сегмента: страница из выдачи увела на чужой сайт",
            extra={"host": host, "landed": urlsplit(fetched.url).hostname},
        )
        return None, ELSEWHERE
    return fetched, fetcher.refusal


async def serp_page(
    client: httpx.AsyncClient, host: str, page_url: str | None, *, language: str | None = None
) -> HomeSignals | None:
    """Страница, которую показал поисковик, — судье, когда главная не открылась (шапка модуля). Одна
    попытка коротким сроком соединения, на языке писем, как главная; разбор и заглушка — общие
    (`read_home`, `looks_denied`), а проверку «вы не робот» узнаёт загрузчик. `None` — качать
    нечего: адреса нет, он чужой, это главная или текст страниц выключен
    (`SALES_SEGMENT_HOME_TEXT_CHARS` = 0); не открылась или увела на чужой сайт — почему, в
    `HomeSignals.error` и в журнал прохода с доменом."""
    url = _page_to_read(host, page_url)
    if url is None or sales_cfg.SEGMENT_HOME_TEXT_CHARS <= 0:
        return None
    fetched, refusal = await _one_try(client, host, url, language)
    found = read_home(fetched.html) if fetched is not None else None
    if found is not None and not looks_denied(f"{found.title} {found.description}"):
        logger.info(
            "судья сегмента: главная не открылась — судья читает страницу из выдачи",
            extra={"host": host},
        )
        return found
    error = refusal or ("заглушка защиты" if found is not None else "не ответила")
    logger.info(
        "судья сегмента: страница из выдачи не открылась — судья видит только выдачу",
        extra={"host": host, "error": error},
    )
    return HomeSignals(reached=False, error=error)


def _tail(label: str, found: HomeSignals | None) -> list[tuple[str, str]]:
    """Отрывок видимого текста открывшейся страницы (`HomeSignals.text` общего разбора) — строкой со
    своей подписью, своим потолком (`SALES_SEGMENT_HOME_TEXT_CHARS`; 0 — без строки)."""
    words = (
        excerpt(found.text, sales_cfg.SEGMENT_HOME_TEXT_CHARS) if found and found.reached else ""
    )
    return [(label, words)] if words else []


def site_text(
    *,
    title: str | None,
    description: str | None,
    home: HomeSignals | None = None,
    page: HomeSignals | None = None,
) -> SiteText:
    """Выдача и главная → текст судьи. Пустые куски не попадают; не больше
    `MAX_TEXT_CHARS` знаков: хвост последней строки срезается. Отрывок видимого текста главной
    (`HomeSignals.text` общего разбора) — последней строкой, своим потолком
    (`SALES_SEGMENT_HOME_TEXT_CHARS`; 0 — без него); главная не открылась, а страница из выдачи
    открылась (`serp_page`) — на его месте отрывок её текста (`PAGE_TEXT`), тем же потолком."""
    pieces = [(PAGE_TITLE, title), (PAGE_DESCRIPTION, description), *_home_lines(home)]
    lines: list[tuple[str, str]] = []
    left = MAX_TEXT_CHARS
    for label, words in pieces:
        clean = " ".join((words or "").split())
        if not clean or left <= 0:
            continue
        lines.append((label, clean[:left]))
        left -= len(clean)
    return SiteText((*lines, *_tail(HOME_TEXT, home), *_tail(PAGE_TEXT, page)))


def _words_of(text: SiteText, labels: tuple[str, ...]) -> str:
    """Слова строк с этими подписями — строка на строку."""
    return "\n".join(words for label, words in text.lines if label in labels)


def site_language(text: SiteText, letters: str | None) -> segment_language.Mismatch | None:
    """Сайт уверенно не на языке писем `letters` и без версии на нём — на каком он
    (`segment_language`); `None` — на нём, не уверены или язык писем не задан.

    Меряются строки сайта (`SITE_LABELS`), без главной-заглушки (`SiteText.without_stub` — судья
    видит текст так же). Версия на языке писем: переключатель в меню или тексте главной
    (`segment_language.switches_to`) или страница выдачи уверенно на нём
    (`segment_language.written_in`). Одно решение у вердикта судьи, пересуда, отмены решения и лида
    (`segment_leads`): оно повторяется по сохранённому тексту."""
    if not letters:
        return None
    site = text.without_stub()
    found = segment_language.mismatch(_words_of(site, SITE_LABELS), letters)
    if found is None or segment_language.written_in(_words_of(site, SERP_LABELS), letters):
        return None
    return None if segment_language.switches_to(_words_of(site, SWITCH_LABELS), letters) else found
