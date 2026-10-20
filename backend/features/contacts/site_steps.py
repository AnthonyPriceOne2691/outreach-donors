"""Ступени лестницы, которые читают сам сайт: обход страниц и браузер.

Вынесены из `ladder.py`, когда к ним добавился поиск глубже: лестница решает порядок
ступеней и исход, а здесь — как читается сайт. Счётчики — общие на проход
(`StepCounters`), накопитель — один на домен (`Collected`).

**Обычный проход и поиск глубже — один код с разными потолками.** Глубже — это
шире бюджет страниц и запросов (`CONTACTS_DEEP_*`), разделы из карты сайта, которых
нет в ссылках главной, и браузер даже у сайта, который не закрывался: человек просит
один домен, и время на него не делится с сотней соседей (`okf/contact-ladder.md`).
"""

from __future__ import annotations

import httpx

from backend.config import contacts as cfg
from backend.features.contacts import browser as browser_step
from backend.features.contacts.collected import Collected
from backend.features.contacts.extract import (
    extract_emails,
    extract_obfuscated,
    find_contact_links,
)
from backend.features.contacts.hidden import extract_hidden, extract_structured
from backend.features.contacts.messengers import harvest_handles
from backend.features.contacts.pages import (
    FetchedPage,
    PageFetcher,
    has_contact_form,
    language_hint,
    page_queue,
    slug_urls,
)
from backend.features.contacts.quality import Candidate, trusted_guess
from backend.features.contacts.section_map import sitemap_sections
from backend.features.contacts.slugs import LINK_MARKERS
from backend.features.contacts.step_counters import StepCounters
from backend.features.core.domain import ContactSource, PageKind

#: Ссылок, узнанных только по тексту, — до угаданных слагов: у обычного прохода
#: две (`pages._TEXT_ONLY_BEFORE_GUESSES`), глубже — больше: бюджет шире.
_DEEP_TEXT_LINKS = 6


def harvest(page: FetchedPage, collected: Collected, *, collect_handles: bool) -> None:
    """Снять со страницы всё, что похоже на адрес, и заметить форму.

    Прямые — `mailto:`, текст, Cloudflare и разметка для машин. Догадки — обфускация
    и исходник страницы, обе только на домене сайта или бесплатной почте: правдоподобный
    адрес из прозы или чужого скрипта хуже никакого (`extract`, `hidden`).
    """
    collected.note_read(page.url)
    if has_contact_form(page.html):
        collected.note_form(page.url, page.kind)
    if collect_handles:
        collected.handles |= harvest_handles(page.html, page_kind=page.kind, page_url=page.url)

    for email in extract_emails(page.html) | extract_structured(page.html):
        collected.add(Candidate(email, ContactSource.PAGE, page.kind, page_url=page.url))
    site_host = collected.site_host
    guesses = extract_obfuscated(page.html) | extract_hidden(page.html, site_host=site_host)
    for email in guesses:
        # Угаданному адресу верим только на домене сайта: иначе обычная
        # фраза «meet at the dot com» станет контактом.
        if trusted_guess(email, site_host=site_host):
            guess = Candidate(email, ContactSource.PAGE, page.kind, page_url=page.url, guessed=True)
            collected.add(guess)


def _fetcher(http: httpx.AsyncClient, *, deep: bool) -> PageFetcher:
    if not deep:
        return PageFetcher(http)
    return PageFetcher(http, max_pages=cfg.DEEP_MAX_PAGES, max_attempts=cfg.DEEP_MAX_ATTEMPTS)


async def walk_pages(
    http: httpx.AsyncClient,
    host: str,
    collected: Collected,
    counters: StepCounters,
    *,
    deep: bool = False,
    collect_handles: bool = False,
) -> None:
    """Страницы сайта. Форму, если увидели, записывает в накопитель.

    Обход останавливается, как только прямой адрес найден на странице дорогого
    вида: у страниц «advertise» и «write for us» адрес лучше, и искать
    после них нечего. Догадка (`Candidate.guessed`) обход не останавливает: прямой
    адрес со следующей страницы лучше неё (`ladder._descend`).
    """
    counters.pages_entered += 1
    fetcher = _fetcher(http, deep=deep)

    home = await fetcher.home(host)
    if home is None:
        # Сайт не открылся ни в одном виде. Угадывать по нему слаги
        # бессмысленно: это ещё три десятка запросов в ту же стену.
        _close(fetcher, collected, counters)
        return

    harvest(home, collected, collect_handles=collect_handles)

    # Ссылки с главной идут впереди угадываемых слагов: там раздел
    # назван словами и лежит по любому адресу, хоть /p/12345.
    links = find_contact_links(home.html, slugs=LINK_MARKERS)
    # Язык берём с главной: она уже скачана, а слаги угадываются после.
    # Без этого локальная площадка не пробуется на своём языке вовсе —
    # потолок попыток кончается на английских слагах.
    language = language_hint(home.html, host)
    # Карта сайта — запросами того же обхода: в счёт и в потолок попыток.
    mapped = await sitemap_sections(fetcher, home.url, host) if deep else []
    counters.sitemap_sections += len(mapped)
    queue = page_queue(
        fetcher.follow(home, links),
        slug_urls(home.url, language=language),
        mapped=mapped,
        text_first=_DEEP_TEXT_LINKS if deep else None,
    )
    visited = {home.url}

    for url, kind in queue:
        if url in visited or fetcher.exhausted:
            continue
        visited.add(url)

        page = await fetcher.get(url, kind)
        if page is None:
            continue

        harvest(page, collected, collect_handles=collect_handles)
        if collected.direct and kind in (PageKind.MONEY, PageKind.CONTACT):
            # Прямой адрес со страницы дорогого вида искать дальше незачем:
            # лучше него на сайте ничего нет.
            break

    _close(fetcher, collected, counters)


def _close(fetcher: PageFetcher, collected: Collected, counters: StepCounters) -> None:
    """Итог обхода — в счётчики прохода и в накопитель домена."""
    counters.pages_fetched += fetcher.attempts
    collected.blocked = fetcher.blocked
    collected.refusal = collected.refusal or fetcher.refusal
    if fetcher.blocked:
        counters.pages_blocked += 1


async def render_site(
    renderer: browser_step.PageRenderer | None,
    http: httpx.AsyncClient,
    host: str,
    collected: Collected,
    counters: StepCounters,
    *,
    deep: bool = False,
    collect_handles: bool = False,
) -> None:
    """Рендер настоящим браузером — для того, что не открылось, а глубже — для всех.

    Ступень дорогая: секунды на страницу. Поэтому обычный проход зовёт её, только
    если сайт закрылся: сайт, который открылся и просто не показал адреса, рендером
    не исправить — замер на шести таких доменах дал ноль адресов. Глубже зовёт её
    и для открытого сайта без адреса: человек просит один домен, а адрес, который
    рисует скрипт, без браузера не увидеть. Домен, с которого прямой адрес уже снят,
    браузер не видит вовсе; догадка браузер не отменяет.

    Отрисованная страница разбирается тем же `harvest`, что и скачанная.
    """
    if renderer is None or collected.direct or not (collected.blocked or deep):
        return

    counters.browser_entered += 1
    # `follow` обычного обхода — ради одного правила ссылок на оба пути;
    # запросов он не делает, клиент ему не нужен.
    follow = PageFetcher(http).follow
    pages = await browser_step.render_pages(
        renderer,
        host,
        follow=follow,
        max_pages=cfg.DEEP_BROWSER_PAGES if deep else browser_step.MAX_BROWSER_PAGES,
    )
    counters.browser_pages += len(pages)
    for page in pages:
        harvest(page, collected, collect_handles=collect_handles)
