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
from urllib.parse import urldefrag, urljoin, urlparse

from selectolax.parser import HTMLParser, Node

from backend.features.crawl.article import extract_article, strip_structural_noise
from backend.features.donors.host import normalize_host

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


@dataclass(frozen=True, slots=True)
class OutLink:
    """Одна исходящая ссылка из тела статьи."""

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


def collect_links(
    body: Node, page_url: str, site_host: str, *, in_body: bool = True
) -> list[OutLink]:
    """Внешние ссылки из тела статьи, по одной на адрес.

    Дедуп внутри страницы намеренный: статья, ссылающаяся на один домен
    трижды, — это одно размещение, а не три. Сколько раз он встречается
    на всём доноре, считается уровнем выше.
    """
    site_root = normalize_host(site_host) or site_host.lower().removeprefix("www.")
    seen: set[str] = set()
    out: list[OutLink] = []

    for node in body.css("a[href]"):
        href = (node.attributes.get("href") or "").strip()
        if not href or href.startswith("#") or href.lower().startswith(_SKIP_SCHEMES):
            continue

        absolute, _ = urldefrag(urljoin(page_url, href))
        parts = urlparse(absolute)
        if parts.scheme not in ("http", "https"):
            continue

        target_host = parts.netloc.lower().split(":")[0]
        known_root = normalize_host(target_host)
        # Суффикс неизвестен снимку — берём хост как есть и помечаем.
        # Тихо отбросить значит потерять рекламодателя в новой зоне
        # и увидеть в отчёте «ссылок нет».
        target_root = known_root or target_host
        if not _is_external(target_root, target_host, site_root) or absolute in seen:
            continue
        seen.add(absolute)

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


def harvest(html: str, page_url: str, site_host: str) -> list[OutLink]:
    """Все внешние ссылки страницы, с пометкой «в теле статьи или вне».

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
        out = collect_links(article.node, page_url, site_host)
        body_urls = {link.url for link in out}

    tree = HTMLParser(html)
    strip_structural_noise(tree)
    root = tree.css_first("body") or tree.root
    if root is None:
        return out

    for link in collect_links(root, page_url, site_host, in_body=False):
        if link.url not in body_urls:
            out.append(link)
    return out
