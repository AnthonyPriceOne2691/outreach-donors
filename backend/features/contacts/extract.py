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

from backend.features.contacts.known_addresses import MAIL_ONLY_DOMAINS
from backend.features.contacts.slugs import link_text_names_section, url_names_section

logger = logging.getLogger(__name__)

# Адрес строгим выражением. Проверяется целиком на очищенном токене.
# Зона — в punycode (`xn--p1ai` — это `.рф`) или буквами, и порядок ветвей
# несущий: буквенная ветвь удовлетворяется уже на «xn», и `findall` на странице
# отдавал обрубок `info@site.xn` (ревью #122). Проверка годности этого не
# видела — `fullmatch` откатывается во вторую ветвь, — поэтому тест идёт через
# извлечение со страницы.
#
# Совпадение начинается только в начале прогона знаков локальной части
# (просмотр назад): на длинном токене без «@» — JSON в скриптах тела — поиск
# иначе пробовал каждую позицию и шёл квадратично. На главной с токеном
# в 49 тысяч знаков это стоило 4,5 с на вызов, в цикле событий, где стоят
# все параллельные домены (ревью #137, e6). Находки те же: начать с середины
# прогона лучше, чем с его начала, выражение не может.
EMAIL_RE = re.compile(
    r"(?<![a-zA-Z0-9._%+\-])[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.(?:xn--[a-zA-Z0-9\-]{2,}|[a-zA-Z]{2,})"
)

# Экранирование JS и JSON: `\u003e` — это `>`, `\x40` — `@`. Встречается в
# скриптах ТЕЛА страницы: их текст selectolax отдаёт вместе с видимым, и со
# страницы уезжал `u003eprivacy@…` (живой прогон 30.09.2026) — обратная косая
# черта в адрес не входит, а `u003e` входит. Раскодированное экранирование
# ещё и открывает адреса, где спрятана сама собака. Скрипты из `<head>` (и
# JSON-LD там же) selectolax в текст не отдаёт вовсе — адреса в них не
# читаются ни до этой правки, ни после.
_JS_ESCAPE_RE = re.compile(r"\\u([0-9a-fA-F]{4})|\\x([0-9a-fA-F]{2})")

# Хвост соседнего слова, слипшийся с зоной: `comJanuary`, `com2024`.
# Границей служит смена регистра или цифра — никакой другой признак тут
# не работает, см. `repair_glued_domain`.
_GLUED_TAIL_RE = re.compile(r"([a-z]{2,})(?=[A-Z0-9])")

#: «at» в скобках или сущностью — маркер адреса всегда.
_AT_RE = re.compile(r"\s*(?:\[at\]|\(at\)|\{at\}|&#64;)\s*", re.IGNORECASE)
_DOT_WORDS = r"(?:\[dot\]|\(dot\)|\{dot\}|\bdot\b|\bpunkt\b|\bточка\b)"
_DOT_RE = re.compile(rf"\s*{_DOT_WORDS}\s*", re.IGNORECASE)

#: Голое «at» — обычное английское слово: «here at AdventureAlan.com»,
#: «2024 At business.com» давали адреса на домене сайта, и те выигрывали
#: выбор (ревью e6, 02.10.2026). Адресом оно становится, только если и точка
#: записана словом — «info at site dot com», —
_AT_SPELLED_RE = re.compile(rf"\s+at\s+(?=[\w-]+\s*{_DOT_WORDS})", re.IGNORECASE)
#: — или перед ним «почтовая» роль: «ads at site.com». Не любая ролевая
#: часть: «Get help at», «Head of Sales at», «Social media at» — проза.
#: И не в обороте «our editor at site.com», «more info at …»: перед ролью
#: слово из `_DETERMINERS`.
_MAIL_ROLES = (
    "info|contact|contacts|editor|editorial|ads|advertise|advertising|advertisement"
    "|partnership|partnerships|enquiries|enquiry|inquiries|webmaster|admin|administrator"
    "|office|mail|email|redaktion|redazione|redaksi|kontakt|kontak|contacto|iklan"
)
_AT_ROLE_RE = re.compile(
    rf"(?:\b(\w+)\s+)?\b({_MAIL_ROLES})\s+at\s+(?=[\w-]+\.[\w-])", re.IGNORECASE
)
_DETERMINERS = frozenset(
    {"the", "our", "your", "my", "their", "his", "her", "its", "a", "an", "more", "for"}
)
#: — или после него почта без портала: «mike.blogger at gmail.com» — так пишут
#: мелкие блоги. Кроме оборотов вроде «Log in at gmail.com» и прозы инструкций
#: вроде «Open your inbox at Gmail.com»: слово перед «at» из `_NOT_LOCAL` —
#: не локальная часть, личным ящиком оно не бывает (ревью e6 #144). Начало —
#: только в начале прогона знаков локальной части, как у `EMAIL_RE`: `\b`
#: срабатывал на каждой точке внутри «a.a.a…», и поиск шёл квадратично
#: (50 тысяч знаков — 25 с, e6).
_AT_FREE_RE = re.compile(
    r"(?<![\w.+-])([\w.+-]+)\s+at\s+(?=(?:"
    + "|".join(re.escape(domain) for domain in sorted(MAIL_ONLY_DOMAINS))
    + r")\b)",
    re.IGNORECASE,
)
_NOT_LOCAL = frozenset(
    {
        "in", "up", "us", "me", "we", "it", "on", "out", "him", "her", "them", "you", "now",
        "here", "there", "this", "that", "your", "our", "the", "more",
        "mail", "email", "inbox", "account", "login", "signin", "signup", "access",
        "available", "online", "free", "help", "support", "settings", "photos", "files",
        "storage", "backup", "calendar", "app", "apps", "web",
    }
)  # fmt: skip


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


def _role_at(match: re.Match[str]) -> str:
    before, role = match.group(1), match.group(2)
    if before and before.lower() in _DETERMINERS:
        return match.group(0)
    return f"{before} {role}@" if before else f"{role}@"


def _free_at(match: re.Match[str]) -> str:
    local = match.group(1)
    return match.group(0) if local.lower() in _NOT_LOCAL else f"{local}@"


def deobfuscate(text: str) -> str:
    r"""`info [at] domain [dot] com` → `info@domain.com`.

    Замена съедает пробелы вокруг маркера. Применяется к копии текста
    и только ради поиска адресов. Голое «at» без скобок — по правилам выше:
    обычное слово в прозе давало правдоподобный адрес на домене сайта,
    к которому ни фильтр качества, ни правило доверия не придерутся.

    Пробелы сначала схлопываются: `\s*` вокруг маркеров на длинном прогоне
    пробелов откатывался с каждой позиции, и замена шла квадратично —
    40 тысяч переводов строк стоили 38 с в цикле событий (ревью #137, e6).
    """
    text = " ".join(text.split())
    text = _AT_SPELLED_RE.sub("@", _AT_RE.sub("@", text))
    text = _AT_FREE_RE.sub(_free_at, _AT_ROLE_RE.sub(_role_at, text))
    return _DOT_RE.sub(".", text)


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


def _unescape_js(text: str) -> str:
    """`\\u003e` → `>`, `\\x40` → `@` — экранирование из скриптов страницы."""
    return _JS_ESCAPE_RE.sub(lambda m: chr(int(m.group(1) or m.group(2), 16)), text)


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
    text = _unescape_js(html_entities.unescape(tree.text(separator=" ")))
    found.update(EMAIL_RE.findall(text))
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

    text = _unescape_js(html_entities.unescape(HTMLParser(html).text(separator=" ")))
    direct = set(EMAIL_RE.findall(text))
    return _clean(set(EMAIL_RE.findall(deobfuscate(text))) - direct)


def find_contact_links(html: str, *, slugs: frozenset[str]) -> set[str]:
    """Ссылки со страницы, ведущие на контактные разделы.

    Берём и по адресу ссылки, и по её тексту: на половине сайтов раздел
    называется `/p/hubungi-kami`, и по слагу его не угадать, зато анкор
    говорит прямо. Отдаём как есть, нормализацией занимается `pages.py`.

    И адрес, и текст сверяются по словам (`slugs.names_section`): заголовок
    «Tudo sobre o caso» — статья, а не раздел «sobre», и десяток таких
    ссылок с главной съедал бюджет обхода раньше угаданных слагов.
    """
    if not html:
        return set()

    links: set[str] = set()
    for node in HTMLParser(html).css("a[href]"):
        href = (node.attributes.get("href") or "").strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        if url_names_section(href, slugs) or link_text_names_section(node.text() or "", slugs):
            links.add(href)
    return links
