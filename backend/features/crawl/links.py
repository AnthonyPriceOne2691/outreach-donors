"""Исходящие ссылки со страницы донора: анкор, `rel`, домен-получатель.

Четыре грабли здесь не придуманы — они оплачены ложными инцидентами
в соседней системе, и каждая стоила разбора вручную.

1. **`rel` бывает через запятую.** `rel="nofollow,ugc"`, разобранный
   по пробелам, даёт токен `"nofollow,"` — и ссылка считается dofollow.
   Разделитель — любой пробел или запятая.
2. **Анкор бывает картинкой.** У ссылки-баннера текста нет вовсе;
   взяв пустую строку, мы получаем «анкор изменился» на ровном месте
   и письмо, персонализированное под пустоту. Подпись берётся
   из `alt`, потом из `title`.
3. **Регистр и диакритика.** Сравнивать анкоры побайтово нельзя:
   вёрстка донора переводит заголовок в верхний регистр, а человек
   набирает без диакритики. Поэтому у анкора две формы — показываемая
   и ключ сравнения.
4. **Ссылка из меню — не размещение.** Решается тем, что сюда приходит
   уже выделенное тело статьи (`article.py`), а не страница целиком.
5. **Кнопка «поделиться» — не ссылка.** Она ведёт в соцсеть и несёт
   в параметрах адрес самой статьи. На боевом обходе двух финансовых
   площадок такие кнопки дали ~2 000 ссылок из 4 177, и все — «в теле»:
   кандидатов-соцсетей больше, чем всех остальных вместе. Выбрасываются
   при сборе, число — в отчёт обхода (`is_share_button`).

**Считаем внешними ссылки на чужой корневой домен.** Поддомен донора —
это он сам; ссылка с `blog.donor.test` на `donor.test` рекламодателя
не создаёт.

**Домен с неизвестным суффиксом не выбрасывается.** Список публичных
суффиксов вшит снимком и стареет, а новые зоны появляются каждый год.
Отбросив такую ссылку молча, мы потеряли бы рекламодателя и не узнали
бы об этом: в отчёте это выглядело бы как «ссылок нет». Поэтому корнем
берётся сам хост, а ссылка помечается `root_guessed` — и число таких
попадает в отчёт обхода.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from urllib.parse import unquote, urldefrag

from selectolax.parser import HTMLParser, Node

from backend.features.crawl.article import extract_article, strip_structural_noise
from backend.features.donors.host import normalize_host
from backend.shared.net.url_parts import join_url, parse_url, split_url

logger = logging.getLogger(__name__)

#: Разделитель токенов `rel`: пробел ИЛИ запятая. См. грабли №1.
_REL_SPLIT = re.compile(r"[\s,]+")

REL_NOFOLLOW = "nofollow"
REL_SPONSORED = "sponsored"
REL_UGC = "ugc"

#: Схемы, за которыми нет страницы: писать их владельцу бессмысленно.
_SKIP_SCHEMES = ("mailto:", "tel:", "javascript:", "sms:", "data:", "ftp:")

#: Длиннее этого анкор не храним: встречается ссылка, обёрнутая вокруг
#: абзаца, и в базе от неё пользы нет, а место она займёт.
MAX_ANCHOR_CHARS = 300

#: Разделы адреса, которыми соцсети и закладки принимают «поделиться»:
#: `/sharer/sharer.php`, `/shareArticle`, `/sharing/share-offsite`,
#: `/intent/tweet`, `/intent/compose`, `/submit`, `/bookmarklet/…`,
#: `/pin/create/…`, `/send`, `/share/url`. Сравнивается раздел целиком,
#: без расширения: `/send-money/` и `/shares/` — разделы сайта, а не кнопки.
SHARE_SEGMENTS: frozenset[str] = frozenset(
    {
        "share", "sharer", "sharearticle", "share-offsite", "sharing", "intent",
        "submit", "submitlink", "bookmarklet", "compose", "send", "pin",
    }
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class OutLink:
    """Одна исходящая ссылка из тела статьи.

    `page_label` и `page_published` — про статью, а не про ссылку: чем
    статья помечена сама («рубрика sponsored», «This post is sponsored by…»)
    и когда вышла. Снимаются со страницы, пока она в памяти (`page_facts`).
    """

    page_url: str
    url: str
    target_host: str
    target_root: str
    anchor: str
    anchor_key: str
    nofollow: bool
    sponsored: bool
    ugc: bool
    root_guessed: bool = False
    in_body: bool = True
    page_label: str | None = None
    page_published: date | None = None

    @property
    def dofollow(self) -> bool:
        """Dofollow — это отсутствие всех трёх пометок, а не одной.

        `sponsored` и `ugc` тоже говорят поисковику «не передавать вес»;
        считать такую ссылку dofollow значит завысить её балл.
        """
        return not (self.nofollow or self.sponsored or self.ugc)


def parse_rel(value: str | None) -> frozenset[str]:
    """Токены `rel` в нижнем регистре. Разделитель — пробел или запятая."""
    if not value:
        return frozenset()
    return frozenset(token.lower() for token in _REL_SPLIT.split(value.strip()) if token)


def anchor_text(node: Node) -> str:
    """Подпись ссылки: текст, а если его нет — `alt` картинки, потом `title`.

    Пустой анкор у ссылки-баннера — не отсутствие анкора, а картинка
    вместо текста. Взяв пустую строку, мы персонализируем письмо
    под пустоту.
    """
    text = " ".join((node.text() or "").split())
    if text:
        return text[:MAX_ANCHOR_CHARS]
    for image in node.css("img"):
        alt = (image.attributes.get("alt") or "").strip()
        if alt:
            return " ".join(alt.split())[:MAX_ANCHOR_CHARS]
    title = (node.attributes.get("title") or "").strip()
    return " ".join(title.split())[:MAX_ANCHOR_CHARS]


def compare_key(anchor: str) -> str:
    """Форма анкора для сравнения: без регистра и без диакритики.

    Показываемая форма остаётся как есть — её читает человек в карточке.
    А сравнение по ней дало бы расхождение на каждом доноре, который
    пишет заголовки капсом.
    """
    folded = unicodedata.normalize("NFKD", anchor.casefold())
    return " ".join("".join(c for c in folded if not unicodedata.combining(c)).split())


def _is_external(target_root: str, target_host: str, site_root: str) -> bool:
    """Чужой ли это домен.

    Когда корень угадан (суффикс неизвестен снимку), сравнение по корню
    врёт: `blog.donor.test` и `donor.test` дают разные «корни» и поддомен
    донора выглядит рекламодателем. Поэтому проверяется и вложенность
    хоста — она верна в обоих случаях.
    """
    if not target_root:
        return False
    if target_root == site_root:
        return False
    return not (target_host == site_root or target_host.endswith(f".{site_root}"))


def _resolve(page_url: str, href: str) -> tuple[str, str] | None:
    """Ссылка со страницы → абсолютный адрес http(s) без якоря и его хост.

    Чужая схема и битая ссылка — `None`: «[» без пары или полноширинная «／»
    в адресе роняли разбор всей страницы (`shared.net.url_parts`). Адрес
    по IP (`http://[::1]/`, `http://10.0.0.1/`) — тоже `None`: домена у него
    нет, и рекламодателя за ним не бывает (ревью #148).
    """
    joined = join_url(page_url, href)
    if joined is None:
        return None
    absolute, _ = urldefrag(joined)
    parts = parse_url(absolute)
    if parts is None or parts.scheme not in ("http", "https"):
        return None
    if not parts.hostname or _is_ip_literal(parts.hostname):
        return None
    return absolute, parts.hostname.lower()


def _is_ip_literal(host: str) -> bool:
    """`::1`, `10.0.0.1` — адрес, а не имя. Хост из `urlsplit` без скобок,
    так что двоеточие внутри — только у IPv6."""
    return ":" in host or host.replace(".", "").isdigit()


def _carries_site_address(href: str, site_root: str) -> bool:
    """В ссылке — адрес самого донора: `?u=https://donor.com/post`.

    Адрес внутри адреса кодируют один раз, а бывает и два — раскрывается
    дважды. Ищется именно адрес со схемой: `utm_source=donor.com` у ссылки
    рекламодателя — метка источника, а не «поделиться».
    """
    text = unquote(unquote(href))
    own = re.compile(rf"https?://(?:[a-z0-9-]+\.)*{re.escape(site_root)}(?![a-z0-9.-])", re.I)
    return own.search(text) is not None


def is_share_button(href: str, absolute: str, site_root: str) -> bool:
    """Кнопка «поделиться»: путь — приёмник соцсети, а в параметрах — адрес донора.

    Нужны оба признака. Одного пути мало: у рекламодателя бывает раздел
    `/submit`. Одного адреса донора мало: трекер рекламной сети тоже
    несёт страницу-источник (`/click?ref=https://donor.com/…`) — а это
    как раз размещение. Список соцсетей не нужен: правило одно для всех,
    включая те, что появятся завтра.
    """
    parts = split_url(absolute)
    if parts is None:
        return False
    segments = {s.lower().split(".")[0] for s in parts.path.split("/") if s}
    host = (parts.hostname or "").lower()
    if not (segments & SHARE_SEGMENTS or host.startswith("share.")):
        return False
    return _carries_site_address(href, site_root)


def collect_links(
    body: Node,
    page_url: str,
    site_host: str,
    *,
    in_body: bool = True,
    shares: set[str] | None = None,
) -> list[OutLink]:
    """Внешние ссылки из тела статьи, по одной на адрес.

    Дедуп внутри страницы намеренный: статья, ссылающаяся на один домен
    трижды, — это одно размещение, а не три. Сколько раз он встречается
    на всём доноре, считается уровнем выше.

    Кнопки «поделиться» не попадают в ссылки, а складываются в `shares`,
    если его передали: выброшенное должно быть видно в отчёте числом.
    """
    site_root = normalize_host(site_host) or site_host.lower().removeprefix("www.")
    seen: set[str] = set()
    out: list[OutLink] = []

    for node in body.css("a[href]"):
        href = (node.attributes.get("href") or "").strip()
        if not href or href.startswith("#") or href.lower().startswith(_SKIP_SCHEMES):
            continue

        resolved = _resolve(page_url, href)
        if resolved is None:
            continue

        absolute, target_host = resolved
        known_root = normalize_host(target_host)
        # Суффикс неизвестен снимку — берём хост как есть и помечаем.
        # Тихо отбросить значит потерять рекламодателя в новой зоне
        # и увидеть в отчёте «ссылок нет».
        target_root = known_root or target_host
        if not _is_external(target_root, target_host, site_root) or absolute in seen:
            continue
        seen.add(absolute)
        if is_share_button(href, absolute, site_root):
            if shares is not None:
                shares.add(absolute)
            continue

        rel = parse_rel(node.attributes.get("rel"))
        anchor = anchor_text(node)
        out.append(
            OutLink(
                page_url=page_url,
                url=absolute,
                target_host=target_host,
                target_root=target_root,
                anchor=anchor,
                anchor_key=compare_key(anchor),
                nofollow=REL_NOFOLLOW in rel,
                sponsored=REL_SPONSORED in rel,
                ugc=REL_UGC in rel,
                root_guessed=not known_root,
                in_body=in_body,
            )
        )
    return out


def harvest(
    html: str, page_url: str, site_host: str, *, shares: set[str] | None = None
) -> list[OutLink]:
    """Все внешние ссылки страницы, с пометкой «в теле статьи или вне».

    Кнопки «поделиться» выбрасываются в обоих проходах и складываются
    в `shares` — одним множеством, чтобы кнопка не считалась дважды.

    **Почему не только из тела, хотя требование говорит именно так.**
    Требование объясняет себя: ссылки навигации и подвала дают ложных
    рекламодателей. Это верно — и навигация с подвалом отсюда выброшены.
    Но замер на боевой нише показал третий случай, которого в требовании
    нет: размещения живут в витринах офферов — блоках вида «топ-5
    букмекеров», — а они лежат рядом со статьёй, а не внутри неё.
    Провайдер говорит о тех же ссылках `is_content: false`, наше
    выделение тела берёт часть из них, часть нет.

    Отбросив их, мы выполнили бы букву требования и не нашли бы никого:
    на трёх донорах ниши из тела статей вышло три ссылки против сотен,
    которые там есть. Поэтому ссылка не выбрасывается, а помечается,
    и решение «считать ли её размещением» принимает скоринг — там же,
    где живёт допуск по ложным рекламодателям.
    """
    article = extract_article(html)
    body_urls: set[str] = set()
    out: list[OutLink] = []
    if article is not None:
        out = collect_links(article.node, page_url, site_host, shares=shares)
        body_urls = {link.url for link in out}

    tree = HTMLParser(html)
    strip_structural_noise(tree)
    root = tree.css_first("body") or tree.root
    if root is None:
        return out

    for link in collect_links(root, page_url, site_host, in_body=False, shares=shares):
        if link.url not in body_urls:
            out.append(link)
    return out
