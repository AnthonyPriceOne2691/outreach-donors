"""Адреса, которых нет в видимом тексте страницы: разметка для машин и вёрстка «от роботов».

`extract.py` читает то, что видит человек: `mailto:`, текст, обфускацию Cloudflare
и «info [at] site [dot] com». Мимо него шли три вида мест, и первое нашлось живым
замером 20.10.2026 — у донора с исходом «адреса нет» ящик сайта стоял в разметке
организации (`"email": …` в JSON-LD в `<head>`), а на видимых страницах его не было:

- **разметка для машин** — JSON-LD (`Organization`, `LocalBusiness`, `ContactPoint`),
  микроразметка `itemprop="email"`, мета-теги с почтой (`og:email`). Её пишет сам
  сайт и для того, чтобы адрес нашли: это адрес наравне с `mailto:`;
- **текст, перевёрнутый стилем** (`unicode-bidi: bidi-override; direction: rtl`):
  в исходнике `moc.etis@ofni`, на экране — обычный адрес. Тоже прямой;
- **адрес, собранный скриптом или спрятанный в атрибут** — `data-email`, пара
  `data-user` + `data-domain`, `'info' + '@' + 'site.com'` в скрипте, `%40` вместо
  собаки. Человек видит его только после работы скрипта, а то и не видит вовсе.

**Доверие разное.** Разметку и перевёрнутый текст сайт показывает как адрес — они
идут прямыми (`extract_structured`). Исходник страницы шумнее: в скриптах лежат
адреса виджетов, ключи телеметрии, образцы для полей ввода. Поэтому из него берётся
только адрес на домене самого сайта, догадкой (`extract_hidden`): прямой адрес
побеждает его всегда (`quality.best`), а спуск по лестнице догадка не останавливает
(`ladder._descend`).

**Похожее на адрес — не всегда адрес** (ревью 20.10.2026). Пара атрибутов берётся,
только если одно из имён говорит о почте (`data-mail-user`, `data-email-domain`,
`data-mailbox`): виджет чата `data-account="12345" data-host="chat.site"` иначе дал бы
`12345@chat.site`. «Собака» после `//логин:` — вход в адресе ссылки (`//логин:пароль@cdn`,
ключ телеметрии `//ключ@sentry`), а не ящик.

Модуль, как и `extract.py`, ничего не решает о годности: это делает `quality.py`.
"""

from __future__ import annotations

import html as html_entities
import json
import logging
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import unquote

from selectolax.parser import HTMLParser, Node

from backend.features.contacts.extract import clean_tokens, emails_in, unescape_js
from backend.features.contacts.quality import on_site

logger = logging.getLogger(__name__)

#: Ключ почты в разметке: schema.org пишет `email`, у части плагинов — с заглавной.
_EMAIL_KEY = "email"

#: Запасной разбор JSON-LD, который `json.loads` не принял: висячая запятая, два
#: объекта подряд, комментарий в скрипте. Адрес из такого блока всё равно адрес.
_JSONLD_EMAIL_RE = re.compile(r'"email"\s*:\s*"([^"\\]{3,254})"', re.IGNORECASE)

#: Склейка строк в скрипте: `'info' + '@' + 'site.com'` → `info@site.com`. Только
#: кавычка-плюс-кавычка: шире правило склеило бы соседние строки кода между собой.
_CONCAT_RE = re.compile(r"""['"`]\s*\+\s*['"`]""")

#: Поля ввода: в `placeholder` и `value` лежат образцы («you@site.com») и чужой ввод,
#: а не адрес сайта. Снимаются до разбора исходника.
_INPUT_HINT_RE = re.compile(
    r"""\s(?:placeholder|value)\s*=\s*(?:"[^"]{0,2000}"|'[^']{0,2000}')""", re.IGNORECASE
)

#: Хвосты имён атрибутов пары «ящик + домен», которую собирает скрипт: `data-user="info"
#: data-domain="site.com"`. Хвост, а не имя целиком: `data-email-user`, `data-mail-domain`,
#: `data-name` — у сборщиков адресов имена свои.
_USER_TAILS = ("user", "username", "name", "mailbox", "local", "account", "box")
_DOMAIN_TAILS = ("domain", "host", "site")
_TLD_TAILS = ("tld", "zone", "ext")
#: Есть ли на странице атрибут домена такой пары вообще: обходить каждый элемент
#: страницы в мегабайт ради пары, которой нет, — сотни миллисекунд в цикле событий.
_DOMAIN_ATTR_RE = re.compile(r"data-[\w-]*(?:domain|host|site)\s*=", re.IGNORECASE)
#: Имя атрибута пары, которое говорит о почте: без него пара — не адрес (см. модуль).
_MAIL_NAME = "mail"

#: Вход в адресе ссылки — `//логин:пароль@` и `//ключ@`: «собака» здесь не почтовая.
#: Кроме `mailto://` — так пишут и сам адрес.
_URL_LOGIN_RE = re.compile(r"(?<!mailto:)//[^/\s'\"<>@]{1,256}@", re.IGNORECASE)

#: Глубже разметка из сотни вложенных уровней — не разметка, а мусор.
_MAX_DEPTH = 12

#: Скрипт страницы и простое присваивание строки в нём (`var d = "site.com"`) — для
#: сборки `mailto:` из переменных (`_inlined`). Скрипт с сотней присваиваний — бандл.
_SCRIPT_RE = re.compile(r"<script\b[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL)
_ASSIGN_RE = re.compile(r"""\b([A-Za-z_]\w{0,40})\s*=\s*(['"])([^'"\\\n]{1,254})\2""")
_MAX_ASSIGNMENTS = 50


def _mail_value(value: str) -> str:
    """Значение поля почты в адрес: без `mailto:`, сущностей и процентной записи."""
    text = html_entities.unescape(unquote(value)).strip()
    if text[:7].lower() == "mailto:":
        text = text[7:].lstrip("/")
    return text.split("?")[0].strip()


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        yield from (item for item in value if isinstance(item, str))


def _walk(data: Any, depth: int = 0) -> Iterator[str]:
    """Значения ключей `email` на любой глубине разметки — строкой или списком строк."""
    if depth > _MAX_DEPTH:
        return
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(key, str) and key.lower() == _EMAIL_KEY:
                yield from _strings(value)
            yield from _walk(value, depth + 1)
    elif isinstance(data, list):
        for item in data:
            yield from _walk(item, depth + 1)


def _jsonld(tree: HTMLParser) -> set[str]:
    """Почта из JSON-LD — в `<head>` и в теле: текст скриптов `<head>` в текст страницы
    не попадает (`extract.extract_emails`), и разметка организации там терялась."""
    found: set[str] = set()
    for node in tree.css('script[type*="ld+json"]'):
        raw = node.text() or ""
        try:
            values = list(_walk(json.loads(raw)))
        except ValueError:
            # Блок, который строгий разбор не принял, — частое дело: висячая запятая,
            # два объекта подряд. Адрес в нём читается выражением.
            logger.debug("контакты: JSON-LD не разобран строго — читаем выражением")
            values = _JSONLD_EMAIL_RE.findall(raw)
        found.update(_mail_value(value) for value in values)
    return found


def _microdata(tree: HTMLParser) -> set[str]:
    """`itemprop="email"` — у мета-тега в `content`, у ссылки в `href`, у текста — текстом;
    и мета-теги с почтой в имени или свойстве (`og:email`, `business:contact_data:email`)."""
    found: set[str] = set()
    for node in tree.css('[itemprop~="email"]'):
        attrs = node.attributes
        found.add(_mail_value(attrs.get("content") or attrs.get("href") or node.text() or ""))
    for node in tree.css("meta"):
        attrs = node.attributes
        names = f"{attrs.get('property') or ''} {attrs.get('name') or ''}".lower()
        if _EMAIL_KEY in names:
            found.add(_mail_value(attrs.get("content") or ""))
    return found


def _reversed(tree: HTMLParser) -> set[str]:
    """Адрес, записанный задом наперёд и развёрнутый стилем: на экране он обычный."""
    found: set[str] = set()
    for node in tree.css("[style]"):
        style = (node.attributes.get("style") or "").replace(" ", "").lower()
        if "bidi-override" in style and "direction:rtl" in style:
            found.update(emails_in((node.text() or "")[::-1]))
    return found


def extract_structured(html: str) -> set[str]:
    """Адреса, которые сайт показывает как адрес, но не текстом: разметка для машин
    и перевёрнутый текст. Прямые — наравне с `mailto:`."""
    if not html:
        return set()
    tree = HTMLParser(html)
    found = _jsonld(tree) | _microdata(tree) | _reversed(tree)
    return clean_tokens({value for value in found if "@" in value})


def _attribute_pairs(node: Node) -> Iterator[str]:
    """`data-user` + `data-domain` (+ `data-tld`) одного элемента — адресом."""
    data = {
        name[5:].lower(): (value or "").strip()
        for name, value in node.attributes.items()
        if name.lower().startswith("data-")
    }
    if len(data) < 2:
        return
    users = [(name, value) for name, value in data.items() if name.endswith(_USER_TAILS) and value]
    domains = [
        (name, value) for name, value in data.items() if name.endswith(_DOMAIN_TAILS) and value
    ]
    tlds = [value for name, value in data.items() if name.endswith(_TLD_TAILS) and value]
    zone = f".{tlds[0].lstrip('.')}" if tlds else ""
    for user_name, user in users:
        for domain_name, domain in domains:
            if _MAIL_NAME not in user_name and _MAIL_NAME not in domain_name:
                continue
            yield f"{user}@{domain}" if domain.endswith(zone) else f"{user}@{domain}{zone}"


def _inlined(script: str) -> str:
    """Скрипт, который собирает `mailto:` из переменных, — с подставленными значениями.

    `var u="info"; var d="site.com"; … "mailto:" + u + "@" + d` (находка продаж
    20.10.2026): склейка строк видна только после подстановки, и без неё адреса в
    исходнике нет вовсе. Подставляются простые присваивания строк — и только в
    коротком скрипте: в собранном бандле их тысячи, и это не сборка адреса.
    """
    values: dict[str, str] = {}
    for name, _quote, value in _ASSIGN_RE.findall(script):
        values.setdefault(name, value)
    if not values or len(values) > _MAX_ASSIGNMENTS:
        return script
    names = "|".join(re.escape(name) for name in sorted(values, key=len, reverse=True))
    script = re.sub(rf"\+\s*\b({names})\b", lambda m: f'+"{values[m.group(1)]}"', script)
    return re.sub(rf"\b({names})\b\s*\+", lambda m: f'"{values[m.group(1)]}"+', script)


def _source(html: str) -> set[str]:
    """Похожее на адрес в исходнике, раскрытом так, как его раскрыл бы скрипт: сущности,
    процентная запись собаки, экранирование JS, переменные и склейка строк. Образцы
    полей ввода снимаются до этого."""
    source = _INPUT_HINT_RE.sub(" ", html)
    scripts = [
        _inlined(script)
        for script in _SCRIPT_RE.findall(source)
        if "mailto" in script.lower() and "@" in script
    ]
    source = "\n".join([source, *scripts])
    source = html_entities.unescape(source).replace("%2540", "@").replace("%40", "@")
    source = _URL_LOGIN_RE.sub("//", unescape_js(_CONCAT_RE.sub("", source)))
    return emails_in(source)


def extract_hidden(html: str, *, site_host: str) -> set[str]:
    """Адреса на домене сайта из исходника страницы: в атрибутах, в скриптах, в паре
    `data-user` + `data-domain`. Догадки — их берут, только если прямого нет.

    Среди них бывают и прямые адреса той же страницы (исходник содержит и текст):
    накопитель их не удвоит — прямой записан раньше и догадкой не заменяется
    (`collected.Collected.add`).

    Чужой домен отсюда не берётся вовсе: в исходнике лежат адреса виджетов, рассылок
    и библиотек, и правдоподобный чужой адрес из скрипта хуже никакого.
    """
    if not html:
        return set()
    found = _source(html)
    if _DOMAIN_ATTR_RE.search(html):
        for node in HTMLParser(html).css("*"):
            found.update(_attribute_pairs(node))
    return {email for email in clean_tokens(found) if on_site(email.rsplit("@", 1)[-1], site_host)}
