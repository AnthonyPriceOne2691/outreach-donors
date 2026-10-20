"""Ступень 1: страницы сайта.

Порядок обхода задан ценностью адреса, а не удобством: сначала страницы,
где сидит тот, кто называет цену за размещение, потом обычные контакты,
потом правовые, где указан оператор сайта.

Четыре приёма здесь не от изящества, а от разбора боевого прогона: из
41 домена, за которые заплатили платному сервису, 14 нас не пустили,
у 7 адрес лежал на странице вне списка, до 6 обход не дошёл в рамках
бюджета. Те же грабли пройдены в соседней системе, и приёмы взяты оттуда.

**Хост пробуется в нескольких видах.** `www.` не срезается: у части
сайтов апекс не имеет записи или не редиректит, и запрос к нему просто
не доезжает. За `https` пробуется `http`: донор с протухшим сертификатом
всё ещё донор.

**Бюджет считает открытые страницы, а не запросы.** Иначе три десятка
404 по угадываемым слагам съедают его до того, как обход дойдёт до
существующей страницы контактов. Отдельный потолок попыток не даёт
зациклиться на сайте, который отвечает всем подряд.

**Правовые страницы входят в список.** Оператора сайта указывают
в «условиях» и «политике» чаще, чем на «контактах», — особенно там,
где контакты сведены к форме.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from selectolax.parser import HTMLParser

from backend.config import contacts as cfg
from backend.features.contacts.extract import extract_emails
from backend.features.contacts.hidden import extract_structured
from backend.features.contacts.slugs import (
    ALL_SLUGS,
    LANGUAGE_BY_TLD,
    LOCALIZED_SLUGS,
    SLUGS,
    WALK_ORDER,
    url_names_section,
)
from backend.features.core.domain import PageKind
from backend.shared.net.url_guard import UnsafeUrlError
from backend.shared.net.url_parts import join_url, parse_url

logger = logging.getLogger(__name__)

# Полный набор заголовков браузера, а не «почти». Проверено на шести
# доменах, закрывшихся от нас: с урезанным набором шесть отказов,
# с полным — четыре. Дешевле, чем платить за эти домены сервису.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Ch-Ua": '"Chromium";v="129", "Not=A?Brand";v="8"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# Признаки контактной формы: адреса нет, но написать можно руками.
# Конструкторы форм узнаются по имени. Голого `<form` здесь больше нет:
# поиск, подписка или вход есть почти на каждом сайте, и с ним «формой
# без адреса» становился любой домен — энциклопедия со строкой поиска
# получала form_only и уходила в ручную очередь.
FORM_MARKERS = ("wpcf7", "gravity_form", "contact-form", "formcraft", "hs-form")

# Своя форма без конструктора: `<form>` с полем для текста письма. Поиск,
# подписка и вход такого поля не имеют. Форма комментария к записи имеет,
# но это не письмо владельцу — её выдаёт сам тег (`commentform`,
# `wp-comments-post`).
_OWN_FORM = re.compile(r"<form\b([^>]*)>(.*?)(?:</form>|\Z)", re.S)

# Страница проверки «вы не робот» вместо сайта: содержимого нет — только скрипт проверки.
# Живой замер 20.10.2026: два донора с исходом «адреса нет» отвечали 202 и страницей
# AWS WAF, и лестница читала её как пустую главную.
#
# **Проверка — страница, с которой нечего взять** (ревью 20.10.2026). Скрипт службы
# защиты стоит и на обычных страницах — Imperva, SDK AWS WAF, Turnstile у формы, «защищено
# DDoS-Guard» в подвале, — и по нему одному одностраничник с адресом читался бы закрытым.
# Поэтому проверкой считается только страница без адреса, без формы и почти без текста —
# и с признаком самой страницы проверки (`_CHALLENGE_PAGE`) или с ответом, каким служба
# отвечает на проверку (`CHALLENGE_STATUSES`), и признаком службы (`PROTECTION_MARKERS`).
_CHALLENGE_PAGE = ("<title>just a moment", "cf_chl_opt", "incapsula incident id")
#: Проверка AWS WAF: её свойства и её скрипт — вместе; по отдельности бывают и у сайта.
_AWS_CHALLENGE = ("gokuprops", "challenge.js")
PROTECTION_MARKERS = (
    "awswafintegration", "token.awswaf.com", "cf-chl-", "challenge-platform",
    "challenges.cloudflare.com", "_incapsula_resource", "captcha-delivery.com",
    "px-captcha", "ddos-guard", "sucuri", "checking your browser",
    "checking if the site connection", "verify that you're not a robot",
    "verify you are human", "showcaptcha",
)  # fmt: skip
#: Чем службы отвечают на проверку: AWS WAF — 202, Cloudflare — 403 и 503, лимит — 429.
CHALLENGE_STATUSES = frozenset({202, 403, 429, 503})
#: Видимого текста у страницы проверки — строка-две: «Just a moment…», «Enable JavaScript».
_CHALLENGE_TEXT_MAX = 600
_INVISIBLE_TAGS = ["script", "style", "noscript", "template", "svg"]

#: Отказы сайта, после которых он «закрылся» (`blocked`): только их и стоит открывать
#: браузером. 451 — закрыт для страны сервера, браузер с того же адреса его не откроет:
#: причина записывается, а браузер не зовётся.
REFUSALS = frozenset({401, 403, 429})
GEO_REFUSAL = 451


#: Хвост страницы, который разбирается и у страницы больше предела: подвал с адресом.
_TAIL_CHARS = 300_000
#: Граница среза — на начале тега или пробеле: адрес не содержит ни того ни другого, и
#: срез по ним не режет его пополам. Ищется в окне у среза: дальше — срез как есть.
_SEAM = re.compile(r"[<\s]")
_SEAM_WINDOW = 4_096


def bounded(text: str) -> str:
    """Страница в пределах разбора: голова и хвост, середина — вон.

    Предел (`CONTACTS_MAX_PAGE_BYTES`) бережёт время разбора, а не память: ответ к этому
    месту уже прочитан целиком. Срез по голове терял подвал — `mailto:` после двух
    миллионов знаков каталога не находился (находка продаж 20.10.2026). Адрес живёт в
    голове (разметка организации, шапка) и в подвале, а середина длинной страницы —
    каталог или лента. Поэтому голова и хвост, разделённые комментарием: слова на стыке
    не склеятся в ложный адрес. Оба края среза сдвинуты к тегу или пробелу (`_SEAM`):
    хвост, начатый посреди `mailto:sales@site`, дал бы `es@site` (ревью 20.10.2026).
    """
    if len(text) <= cfg.MAX_PAGE_BYTES:
        return text
    head = max(cfg.MAX_PAGE_BYTES - _TAIL_CHARS, 0)
    before = [m.start() for m in _SEAM.finditer(text, max(head - _SEAM_WINDOW, 0), head)]
    head = before[-1] if before else head
    tail = len(text) - _TAIL_CHARS
    after = _SEAM.search(text, tail, tail + _SEAM_WINDOW)
    tail = after.start() if after else tail
    return f"{text[:head]}\n<!-- середина страницы не разбиралась -->\n{text[tail:]}"


def _visible_chars(html: str) -> int:
    """Сколько видимого текста на странице — без скриптов, стилей и пробелов подряд."""
    tree = HTMLParser(html)
    tree.strip_tags(_INVISIBLE_TAGS)
    root = tree.body or tree.root
    return len(" ".join((root.text(separator=" ") if root is not None else "").split()))


def _nothing_to_read(html: str) -> bool:
    """Разбор ничего не снял бы со страницы: ни адреса, ни формы, почти нет текста."""
    if has_contact_form(html) or extract_emails(html) or extract_structured(html):
        return False
    return _visible_chars(html) <= _CHALLENGE_TEXT_MAX


def is_challenge(html: str, status: int = 200) -> bool:
    """Страница проверки «вы не робот», а не страница сайта (см. `_CHALLENGE_PAGE`)."""
    lowered = html.lower()
    own = any(marker in lowered for marker in _CHALLENGE_PAGE) or all(
        marker in lowered for marker in _AWS_CHALLENGE
    )
    if not own and not (
        status in CHALLENGE_STATUSES and any(marker in lowered for marker in PROTECTION_MARKERS)
    ):
        return False
    return _nothing_to_read(html)


#: Слова отказа — для исхода «адреса нет»: человек видит, что сайт не пустил,
#: а не что адреса на нём нет.
CHALLENGE = "сайт показал проверку «вы не робот»"


def refusal_words(status: int) -> str:
    """Почему главная не открылась — словами, по коду ответа."""
    if status in (401, 403):
        return f"сайт не пустил нас ({status})"
    if status == 429:
        return "сайт ограничил частоту запросов (429)"
    if status == GEO_REFUSAL:
        return "сайт закрыт для страны нашего сервера (451)"
    return f"главная не открылась (ответ {status})"


@dataclass(frozen=True, slots=True)
class FetchedPage:
    """Скачанная страница и её вид."""

    url: str
    kind: PageKind
    html: str


def kind_of(url: str) -> PageKind:
    """Вид страницы по её адресу. Неузнанное — главная, то есть слабый вес.

    Адрес раскодируется (`url_names_section`): не-латинский слаг уезжает
    в запрос процентными последовательностями (`%D8%A7%D8%AA%D8%B5%D9%84`),
    и без раскодирования арабская страница контактов считалась бы главной,
    то есть её адрес получил бы вес в четыре раза меньше заслуженного.

    Сравнение — по словам, а не подстрокой: статья, принятая за раздел
    рекламы, отдаёт процитированный в ней чужой адрес с весом раздела
    и останавливает обход (`slugs.names_section`).
    """
    for kind in WALK_ORDER:
        if url_names_section(url, ALL_SLUGS[kind]):
            return kind
    return PageKind.HOME


def language_hint(html: str, site_host: str) -> str | None:
    """На каком языке сайт — чтобы пробовать его слаги первыми.

    Спрашиваем главную, а не догадываемся: у `<html lang>` нет причин
    врать, и он есть у большинства площадок. Зона домена — запасной
    признак, слабее: `.com` не говорит ни о чём, а многоязычные зоны
    (`.be`, `.ch`, `.ca`) угадывать вредно, их в таблице и нет.

    `None` — нормальный ответ: тогда обход идёт основным списком.

    Объявленный язык — ответ, даже если своих слагов у него нет: сайт
    на `.md` или `.de` с `lang="en"` английский, и русские или немецкие
    слаги первыми вытеснили бы его английские разделы из бюджета попыток.
    Зона спрашивается только тогда, когда `lang` не объявлен вовсе.
    """
    node = HTMLParser(html).css_first("html") if html else None
    declared = (node.attributes.get("lang") or "").strip().lower() if node else ""
    if declared:
        language = declared.replace("_", "-").split("-")[0]
        return language if language in LOCALIZED_SLUGS else None

    labels = site_host.lower().rstrip(".").split(".")
    return LANGUAGE_BY_TLD.get(_ascii_zone(labels[-1])) if labels else None


def _ascii_zone(label: str) -> str:
    """Зона в том виде, в каком она записана в таблице: `рф` → `xn--p1ai`."""
    try:
        return label.encode("idna").decode("ascii")
    except UnicodeError as exc:
        # Метка, которую IDNA не принимает, в таблице зон не найдётся ни
        # в каком виде: язык тогда не угадывается, обход идёт основным списком.
        logger.debug("зона %r не переводится в IDNA: %s", label, exc)
        return label


def home_variants(site_host: str) -> Iterator[str]:
    """Как пробовать главную: апекс и `www.`, сначала по `https`, потом `http`.

    У части сайтов апекс не имеет записи или не редиректит на `www.`;
    у части протух сертификат. И то и другое — не повод терять донора.
    """
    hosts = (site_host, f"www.{site_host}")
    for scheme in ("https", "http"):
        for host in hosts:
            yield f"{scheme}://{host}/"


# Ссылка, узнанная только по тексту, — вид по адресу у неё «главная»: раздел
# лежит по `/p/12345`, и ради таких текст ссылки и смотрится. Но заголовки
# статей тоже начинаются словом раздела («Support for Ukraine grows»), и восемь
# таких ссылок съедали бюджет открытых страниц раньше угаданных слагов — до
# `/contact/` обход не доходил. Поэтому до догадок идут не больше двух, остальные
# — после; короткий адрес первым: у раздела он короче, чем у статьи.
_TEXT_ONLY_BEFORE_GUESSES = 2


def page_queue(
    followed: list[tuple[str, PageKind]],
    guesses: Iterable[tuple[str, PageKind]],
    *,
    mapped: Iterable[tuple[str, PageKind]] = (),
    text_first: int | None = None,
) -> list[tuple[str, PageKind]]:
    """Порядок обхода: разделы по ссылкам главной, разделы из карты сайта, догадки,
    остальные ссылки.

    Карта сайта (`mapped`) и число ссылок, узнанных только по тексту, до догадок
    (`text_first`) — у поиска глубже: у обычного прохода карты нет, а ссылок две.
    Раздел из карты стоит раньше догадки: он существует, догадка — нет.
    """
    typed = [link for link in followed if link[1] is not PageKind.HOME]
    text_only = sorted(
        (link for link in followed if link[1] is PageKind.HOME),
        key=lambda link: (len(link[0]), link[0]),
    )
    cut = _TEXT_ONLY_BEFORE_GUESSES if text_first is None else text_first
    early = text_only[:cut]
    late = text_only[cut:]
    return [*typed, *mapped, *early, *guesses, *late]


def slug_urls(base: str, *, language: str | None = None) -> Iterator[tuple[str, PageKind]]:
    """Угадываемые адреса страниц от рабочей главной.

    Виды перебираются кругами, а не подряд: сначала первый слаг каждого
    вида, потом второй и так далее. Подряд не работает — слагов почти
    полсотни, потолок попыток вдвое меньше, и правовые страницы в конце
    списка не пробовались бы никогда. Поймано тестом: сайт с формой
    вместо контактов и адресом оператора в «условиях» оставался без
    адреса, хотя адрес был.

    Приоритет вида при этом сохраняется: внутри круга порядок прежний,
    а окончательный выбор делает вес адреса (okf/contact-ladder.md).

    Слаги языка сайта идут ПЕРЕД основными, и это не вежливость к
    локальным площадкам, а единственный способ вообще их попробовать:
    потолок попыток на домен меньше числа слагов, круг доходит примерно
    до шестого в каждом виде, и `hubungi-kami` из основного списка не
    пробуется ни разу. Язык подсказывает `language_hint` по главной.
    """
    root = base.rstrip("/")
    localized = LOCALIZED_SLUGS.get(language or "", {})
    order = {kind: (*localized.get(kind, ()), *SLUGS[kind]) for kind in WALK_ORDER}
    longest = max(len(slugs) for slugs in order.values())
    for position in range(longest):
        for kind in WALK_ORDER:
            slugs = order[kind]
            if position < len(slugs):
                yield f"{root}/{slugs[position]}/", kind


def has_contact_form(html: str) -> bool:
    """Есть ли на странице форма. Ветка «форма без адреса» — ступень 4."""
    lowered = html.lower()
    if any(marker in lowered for marker in FORM_MARKERS):
        return True
    return any(
        "<textarea" in body and "comment" not in tag for tag, body in _OWN_FORM.findall(lowered)
    )


class PageFetcher:
    """Качает страницы одного домена с двумя потолками.

    Потолков два, и это не перестраховка. Первый — на открытые страницы:
    столько мы готовы разобрать. Второй — на попытки: сайт отвечает 404
    на большинство угадываемых слагов, и без него бюджет уходит на
    несуществующие адреса, не дожив до существующих.

    Ошибка по одной странице — не поломка домена, а норма. Поломкой было
    бы промолчать о ней, поэтому счётчики отказов ведутся и уходят в отчёт.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        max_pages: int | None = None,
        max_attempts: int | None = None,
    ) -> None:
        self._client = client
        self._max_pages = max_pages if max_pages is not None else cfg.MAX_PAGES_PER_DOMAIN
        self._max_attempts = (
            max_attempts if max_attempts is not None else cfg.MAX_ATTEMPTS_PER_DOMAIN
        )
        self.opened = 0  # страницы, которые удалось разобрать
        self.attempts = 0  # все запросы, включая 404 и отказы
        self.blocked = False  # сайт закрылся от нас: 401/403/429 или проверка «не робот»
        #: Почему страница не открылась — словами, последний отказ. Нужен исходу,
        #: когда не открылась ни одна: «адреса нет» без причины прятал «сайт не пустил».
        self.refusal = ""
        #: Заголовок последней страницы проверки «вы не робот» — её называет разбор главной
        #: (`donors/home_signals.check_home`: «заглушка защиты: …»).
        self.challenge_title = ""

    @property
    def exhausted(self) -> bool:
        return self.opened >= self._max_pages or self.attempts >= self._max_attempts

    async def get(self, url: str, kind: PageKind) -> FetchedPage | None:
        if self.exhausted:
            return None

        self.attempts += 1
        response = await self._request(url)
        page = None if response is None else self._read(response, kind)
        if page is not None:
            self.opened += 1
        return page

    def _read(self, response: httpx.Response, kind: PageKind) -> FetchedPage | None:
        """Страница сайта из ответа — или `None` с причиной, почему её нет."""
        status = response.status_code
        is_html = "html" in response.headers.get("content-type", "").lower()
        html = bounded(response.text) if is_html else ""
        if html and is_challenge(html, status):
            # Сайт не пустил, показав проверку: браузер её иногда проходит, а читать
            # такую страницу как пустую главную значило бы записать «адреса нет».
            self.blocked = True
            self.refusal = CHALLENGE
            title = HTMLParser(html).css_first("title")
            self.challenge_title = title.text(strip=True) if title is not None else ""
            logger.debug("сайт показал проверку «вы не робот»: %s (%s)", response.url, status)
            return None
        if status >= 400:
            # Закрылся (401/403/429) — повод для браузера; остальное — только причина.
            self.blocked = self.blocked or status in REFUSALS
            self.refusal = refusal_words(status)
            logger.debug("страница не открылась: %s ответил %s", response.url, status)
            return None
        return FetchedPage(url=str(response.url), kind=kind, html=html) if html else None

    async def text(self, url: str) -> str | None:
        """Текст служебного файла сайта — карты (`section_map`): в тот же счёт и потолок
        попыток, что и страницы. Не ответил или отказал — `None`."""
        if self.exhausted:
            return None
        self.attempts += 1
        response = await self._request(url)
        return None if response is None or response.status_code >= 400 else response.text

    async def _request(self, url: str) -> httpx.Response | None:
        """Запрос одной страницы. `None` — страница не открылась."""
        try:
            return await self._client.get(url, headers=HEADERS, follow_redirects=True)
        except httpx.HTTPError as exc:
            logger.debug("страница не открылась: %s — %r", url, exc)
        except UnsafeUrlError as exc:
            # Защита отказала — и правильно: ссылка с чужого сайта ведёт
            # на внутренний адрес или чужую схему. Это отказ ОДНОЙ страницы,
            # а не поломка прогона: 23.09.2026 опечатка `hhttps://` на одном
            # сайте оборвала поиск контактов для всех остальных.
            logger.info("страница пропущена, адрес не прошёл защиту: %s", exc)
        return None

    async def home(self, site_host: str) -> FetchedPage | None:
        """Главная в первом виде, который ответил."""
        for url in home_variants(site_host):
            page = await self.get(url, PageKind.HOME)
            if page is not None:
                return page
            if self.exhausted:
                break
        logger.debug("контакты: главная %s не открылась ни в одном виде", site_host)
        return None

    def follow(self, page: FetchedPage, links: set[str]) -> list[tuple[str, PageKind]]:
        """Ссылки со страницы, приведённые к абсолютным, — только свой домен.

        Чужой домен здесь означает соцсеть или платформу: их контактные
        страницы к нашему донору отношения не имеют.
        """
        host = urlparse(page.url).netloc.lower()
        out: list[tuple[str, PageKind]] = []
        for href in links:
            absolute = join_url(page.url, href) or ""
            parsed = parse_url(absolute)
            # Схема — до хоста: опечатка `hhttps://свой-домен/...` проходила
            # проверку своего домена и уходила в запрос. У битой ссылки схемы нет.
            if (
                parsed is None
                or parsed.scheme not in ("http", "https")
                or parsed.netloc.lower() != host
            ):
                continue
            out.append((absolute, kind_of(absolute)))

        order = {kind: number for number, kind in enumerate(WALK_ORDER)}
        return sorted(out, key=lambda item: order.get(item[1], len(order)))
