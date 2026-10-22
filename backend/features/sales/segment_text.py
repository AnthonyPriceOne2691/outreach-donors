"""Текст сайта для судьи сегмента: строки с подписями — выдача, шапка главной, отрывок её текста.

Строка — кусок текста сайта со своей подписью (`Заголовок в выдаче: …`). Подписи — наши слова, а
не сайта: цитировать их нельзя (`segment_quote.unlabeled` снимает их из цитаты), по ним же строки
выдачи и главной различаются (`SiteText.without_stub`) и текст хранится у кандидата (`site_text`) —
пересуд и заведение лида читают его обратно (`SiteText.parse`). Сюда же — язык текста против языка
писем (`site_language`): тем же решением, по тем же строкам, в вердикте судьи, при пересуде, при
отмене решения и у лида — по сохранённому тексту строки те же, подписи при них.

Язык сайта меряется только по словам сайта — строкам выдачи и главной (`SITE_LABELS`): служебная
строка со своими словами его не решает. Версия на языке писем у сайта есть — несовпадения нет: в
меню или тексте главной переключатель на этот язык (`SWITCH_LABELS`) или страница выдачи на нём
(`SERP_LABELS`, `segment_language.written_in`) — главная приходит на языке загрузчика, а поисковик
показал страницу на языке писем.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from backend.config import sales as sales_cfg
from backend.features.donors.home_signals import HomeSignals, check_home, looks_denied
from backend.features.sales import segment_language
from backend.features.sales.segment_quote import quote_holds

#: Сколько знаков текста сайта видит модель: сниппет и шапка главной короткие, потолок —
#: против аномалии страницы, а не ради экономии. Отрывок текста главной — сверх него, своим потолком
#: (`SALES_SEGMENT_HOME_TEXT_CHARS`).
MAX_TEXT_CHARS = 2000
#: Подписи строк текста сайта: свои слова, не сайта — цитировать их нельзя (модель цитирует —
#: подпись снимается, `segment_quote.unlabeled`). Строки выдачи и главной различаются по ним же.
PAGE_TITLE = "Заголовок в выдаче"
PAGE_DESCRIPTION = "Описание в выдаче"
HOME_TITLE = "Заголовок главной"
HOME_DESCRIPTION = "Описание главной"
HOME_MENU = "Меню главной"
HOME_TEXT = "Текст главной"
SERP_LABELS = (PAGE_TITLE, PAGE_DESCRIPTION)
HOME_LABELS = (HOME_TITLE, HOME_DESCRIPTION, HOME_MENU, HOME_TEXT)
LABELS = (*SERP_LABELS, *HOME_LABELS)
#: Строки, по которым узнаётся заглушка защиты. Текст главной — нет: в нём законно «captcha»
#: (подпись reCAPTCHA в подвале) или «forbidden», а закрытую главную узнаёт её заголовок (общий
#: `check_home`: заглушка вместо главной — главная не открылась).
STUB_LABELS = (*SERP_LABELS, HOME_TITLE, HOME_DESCRIPTION, HOME_MENU)
#: Строки, где сайт показывает переключатель версий на других языках (`segment_language.switches_to`).
SWITCH_LABELS = (HOME_MENU, HOME_TEXT)
#: Строки, по которым меряется язык сайта (`site_language`): только слова сайта — выдача и главная.
#: Не `LABELS`: строка со своими словами, если ляжет в текст, язык сайта не решает.
SITE_LABELS = (*SERP_LABELS, *HOME_LABELS)


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
        переписывания (`segment_quote.quote_holds`): модель переносит строки и ставит знаки
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


async def page_home(client: httpx.AsyncClient, host: str, page_url: str | None) -> HomeSignals:
    """Главная для судьи — хоста страницы выдачи, а не только корня: у поддомена страны или города
    своя главная (замер 20.10 на наборе трёх гипотез: 14 из 191 страницы выдачи — на поддомене, у 9
    из них главная поддомена другая или главная корня не открылась). Главная поддомена не открылась
    — главная корня (`host`, домен базы). Разбор главной — общий `check_home`."""
    page_host = urlsplit(page_url).hostname or "" if page_url else ""
    if page_host.endswith(f".{host}") and page_host != f"www.{host}":
        found = await check_home(client, page_host)
        if found.reached:
            return found
    return await check_home(client, host)


def site_text(
    *, title: str | None, description: str | None, home: HomeSignals | None = None
) -> SiteText:
    """Выдача и главная → текст судьи. Пустые куски не попадают; не больше
    `MAX_TEXT_CHARS` знаков: хвост последней строки срезается. Отрывок видимого текста главной
    (`HomeSignals.text` общего разбора) — последней строкой, своим потолком
    (`SALES_SEGMENT_HOME_TEXT_CHARS`; 0 — без него)."""
    pieces = [(PAGE_TITLE, title), (PAGE_DESCRIPTION, description), *_home_lines(home)]
    lines: list[tuple[str, str]] = []
    left = MAX_TEXT_CHARS
    for label, words in pieces:
        clean = " ".join((words or "").split())
        if not clean or left <= 0:
            continue
        lines.append((label, clean[:left]))
        left -= len(clean)
    tail = excerpt(home.text, sales_cfg.SEGMENT_HOME_TEXT_CHARS) if home and home.reached else ""
    if tail:
        lines.append((HOME_TEXT, tail))
    return SiteText(tuple(lines))


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
