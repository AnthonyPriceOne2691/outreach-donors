"""Извлечение адресов из HTML страницы.

Четыре приёма, в порядке отдачи:

    1. `mailto:` в ссылках — самый чистый источник;
    2. адрес текстом на странице;
    3. обфускация Cloudflare (`data-cfemail`, `/cdn-cgi/l/email-protection#`);
    4. ручная обфускация `info [at] domain [dot] com` и HTML-мнемоники.

Приёмы 3 и 4 нужны не для полноты, а потому что сайты, которые продают
размещение, прячут адрес чаще прочих: их и так заваливают. Отказавшись
от них, мы потеряли бы ровно ту часть выборки, ради которой всё делается.

Модуль ничего не фильтрует: «похоже на адрес» и «этим адресом можно
пользоваться» — разные вопросы, второй решает `quality.py`. Единственное,
что он правит, — слипшийся с адресом хвост соседнего слова: это не вопрос
годности, а неверно разобранный токен (`repair_glued_domain`).
"""

from __future__ import annotations

import html as html_entities
import logging
import re

from selectolax.parser import HTMLParser

logger = logging.getLogger(__name__)

# Адрес строгим выражением. Проверяется целиком на очищенном токене.
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# Хвост соседнего слова, слипшийся с зоной: `comJanuary`, `com2024`.
# Границей служит смена регистра или цифра — никакой другой признак тут
# не работает, см. `repair_glued_domain`.
_GLUED_TAIL_RE = re.compile(r"([a-z]{2,})(?=[A-Z0-9])")

_AT_RE = re.compile(r"\s*(?:\[at\]|\(at\)|\{at\}|\bat\b|&#64;)\s*", re.IGNORECASE)
_DOT_RE = re.compile(
    r"\s*(?:\[dot\]|\(dot\)|\{dot\}|\bdot\b|\bpunkt\b|\bточка\b)\s*", re.IGNORECASE
)


def decode_cloudflare(hexstr: str) -> str:
    """Раскодировать `data-cfemail`: XOR каждого байта по первому.

    Схема детерминированная и документирована самим Cloudflare, ключ лежит
    в первом байте строки. Битую строку возвращаем пустой — это не поломка,
    а мусорный атрибут на странице.
    """
    try:
        raw = bytes.fromhex(hexstr.strip())
    except ValueError:
        logger.debug("cfemail: не шестнадцатеричная строка: %r", hexstr[:40])
        return ""
    if len(raw) < 2:
        return ""
    key = raw[0]
    return "".join(chr(byte ^ key) for byte in raw[1:])


def deobfuscate(text: str) -> str:
    """`info [at] domain [dot] com` → `info@domain.com`.

    Замена съедает пробелы вокруг маркера. Применяется к копии текста
    и только ради поиска адресов: ложные срабатывания на обычных словах
    «at» и «dot» отсеет фильтр качества.
    """
    return _DOT_RE.sub(".", _AT_RE.sub("@", text))


def _from_mailto(tree: HTMLParser) -> set[str]:
    found: set[str] = set()
    for node in tree.css("a[href]"):
        href = (node.attributes.get("href") or "").strip()
        if not href.lower().startswith("mailto:"):
            continue
        value = href[7:].split("?")[0].strip()
        # Адрес в mailto бывает процентно-закодирован, а часто и не один.
        for part in value.split(","):
            cleaned = html_entities.unescape(part).strip()
            if cleaned:
                found.add(cleaned)
    return found


def _from_cloudflare(tree: HTMLParser) -> set[str]:
    found: set[str] = set()
    for node in tree.css("[data-cfemail]"):
        decoded = decode_cloudflare(node.attributes.get("data-cfemail") or "")
        if decoded:
            found.add(decoded)
    for node in tree.css('a[href*="/cdn-cgi/l/email-protection#"]'):
        fragment = (node.attributes.get("href") or "").split("#", 1)[-1]
        decoded = decode_cloudflare(fragment)
        if decoded:
            found.add(decoded)
    return found


def repair_glued_domain(domain: str) -> str:
    """Отрезать от зоны хвост слипшегося слова: `gmail.comJanuary` → `gmail.com`.

    Текст страницы не всегда отделяет адрес от следующего слова, и разбор
    склеивает их в один токен. Строка при этом остаётся правдоподобным
    адресом: `ads@gmail.comjanuary` проходит любую проверку формы и уезжает
    в базу мёртвым.

    **Границей служит смена регистра или цифра, а не список известных зон.**
    Список — первое, что приходит в голову, и он ошибается в обе стороны:
    зону, которой в нём нет, он не починит, а живую долгую зону обрежет до
    короткой, потому что `company` начинается на `com`, `network` — на `net`,
    а `institute` — на `in`. Проверено на рабочей реализации: `site.company`
    превращалось в `site.com`, `site.international` — в `site.int`. Это хуже
    отказа доставки: у обрезанного домена бывает живой владелец, и письмо
    уходит не тому, кому писали.

    У регистра и цифры ложных срабатываний нет по устройству: в доменных
    зонах не бывает заглавных букв, а единственная зона с цифрами —
    punycode (`xn--p1ai`), и до цифры в ней дело не доходит: совпадение
    привязано к началу метки, а сразу за `xn` стоит дефис. Отдельной
    проверки на `xn--` здесь поэтому нет — она никогда не срабатывала бы,
    а мёртвая защита обманчивее отсутствующей. Зону держит тест на
    punycode: он покраснеет, если привязку к началу когда-нибудь снимут.

    Чего правило НЕ чинит, осознанно: хвост, целиком набранный строчными
    (`gmail.comand`). Отличить его от настоящей долгой зоны без списка всех
    зон IANA нельзя, а ошибка в эту сторону дороже: адрес с обрезанной зоной
    уедет живому чужому человеку молча. Не починенный, такой адрес и не
    проходит: `quality.GLUED_LOWERCASE_ZONE` отсеивает узкий случай, где зоны
    заведомо нет (`com/net/org` + 1–3 буквы), — отбивка при отправке била бы
    по репутации почтового домена.
    """
    head, _, last_label = domain.rpartition(".")
    if not head:
        return domain
    glued = _GLUED_TAIL_RE.match(last_label)
    return f"{head}.{glued.group(1)}" if glued else domain


def _clean(items: set[str]) -> set[str]:
    """Привести токены к адресам: обрезать пунктуацию, починить зону, снизить регистр.

    Порядок важен: зона чинится ДО снижения регистра, потому что регистр —
    это и есть признак склейки. Снизить его первым значит потерять его.
    """
    cleaned: set[str] = set()
    for item in items:
        token = item.strip().strip(".,;:()<>\"'")
        if not token:
            continue
        local, at, domain = token.partition("@")
        token = f"{local}{at}{repair_glued_domain(domain)}" if at else token
        cleaned.add(token.lower())
    return cleaned


def extract_emails(html: str) -> set[str]:
    """Адреса, написанные на странице прямо: `mailto:`, текст, Cloudflare.

    Пустой результат законен: страница может просто не содержать адреса.
    Поломкой считается не пустота, а исключение разбора — оно летит выше.
    """
    if not html:
        return set()

    tree = HTMLParser(html)
    found = _from_mailto(tree) | _from_cloudflare(tree)
    found.update(EMAIL_RE.findall(html_entities.unescape(tree.text(separator=" "))))
    return _clean(found)


def extract_obfuscated(html: str) -> set[str]:
    """Адреса, восстановленные из `info [at] site [dot] com`.

    Отдаются отдельно от прямых намеренно. Приём шумный: обычная фраза
    «meet at the dot com» после замены выглядит как `meet@the.com` —
    правдоподобный адрес, к которому фильтру качества придраться не за что.

    Поэтому доверие остаётся за вызывающим: он знает домен сайта и берёт
    угаданный адрес, только если тот на этом домене или на известной
    бесплатной почте. Отказаться от приёма нельзя — именно так прячут
    адрес те, кто продаёт размещение.
    """
    if not html:
        return set()

    text = html_entities.unescape(HTMLParser(html).text(separator=" "))
    direct = set(EMAIL_RE.findall(text))
    return _clean(set(EMAIL_RE.findall(deobfuscate(text))) - direct)


def find_contact_links(html: str, *, slugs: frozenset[str]) -> set[str]:
    """Ссылки со страницы, ведущие на контактные разделы.

    Берём и по адресу ссылки, и по её тексту: на половине сайтов раздел
    называется `/p/hubungi-kami`, и по слагу его не угадать, зато анкор
    говорит прямо. Отдаём как есть, нормализацией занимается `pages.py`.
    """
    if not html:
        return set()

    links: set[str] = set()
    for node in HTMLParser(html).css("a[href]"):
        href = (node.attributes.get("href") or "").strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        haystack = f"{href.lower()} {(node.text() or '').lower()}"
        if any(slug in haystack for slug in slugs):
            links.add(href)
    return links
