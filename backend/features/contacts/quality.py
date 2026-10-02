"""Годность адреса и его вес.

«Похоже на адрес» и «по этому адресу можно писать» — разные вещи. Со
страницы приходят адреса аналитики, шаблонные `your-email@example.com`,
контакты регистратора и куски имён файлов. Отправив письмо по такому,
мы получаем отказ доставки, а отказы доставки бьют по репутации
почтового домена — единственному ресурсу проекта, который не
восстанавливается.

Отказ всегда называет причину: без неё нельзя понять, почему домен
остался без контакта, и калибровать фильтр.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from backend.features.contacts.extract import EMAIL_RE
from backend.features.contacts.known_addresses import FREE_MAILBOX_DOMAINS, ROLE_LOCAL_PARTS
from backend.features.core.domain import ContactSource, PageKind
from backend.features.donors.host import split_host

# Точные домены сервисов: аналитика, CDN, конструкторы сайтов, соцсети.
VENDOR_DOMAINS = frozenset(
    {
        "example.com", "example.org", "example.net", "domain.com", "yourdomain.com",
        "sentry.io", "wixpress.com", "wix.com", "squarespace.com", "shopify.com",
        "schema.org", "w3.org", "google.com", "googleapis.com", "gstatic.com",
        "facebook.com", "twitter.com", "youtube.com", "instagram.com",
        "jsdelivr.net", "gravatar.com", "cloudflare.com", "wordpress.com", "wordpress.org",
        # Компания-владелец Substack: авторы на этом домене не пишут никогда.
        "substackinc.com",
    }
)  # fmt: skip

# Хостер вместо сайта: страница-заглушка «сайт размещён у нас» отдаёт адреса
# хостера — прогон 27.09.2026 снял `support@beget.com` с трёх площадок.
# Сверяются МЕТКИ домена целиком: `ovh` подстрокой сидит в чужих именах
# (`lovhouse.com`), и подстрочная проверка отрезала бы живые сайты. Публичный
# суффикс в сверку не входит: `.ovh` — зона, в ней живут чужие сайты, и
# `contact@monsite.ovh` — ящик сайта, а не хостера (`_labels_above_suffix`).
HOSTER_LABELS = frozenset({"beget", "timeweb", "hostinger", "ovh", "hetzner"})

# Платформы, где пишут авторы. Адрес автора на домене платформы законен
# (`wethefifth@substack.com`), а ролевой ящик там — самой платформы: прогон
# 27.09.2026 снял `support@substack.com` из подвала 37 публикаций.
PUBLISHING_PLATFORM_DOMAINS = frozenset({"substack.com"})

# Юридические адреса платформы: представитель в ЕС и UK и контактная точка по
# DSA. В подвале каждой публикации Substack они лежат у юрфирм
# (`eurepresentative.substack@twobirds.com`, `substack-dsa@lionheartsquared.eu`),
# и проверка ящика ставила им `valid` (25 доноров в CRM) — ящик есть, но читает юрист.
# Правило по форме, а не по доменам: юрфирм много, форма одна.
LEGAL_CONTACT_LOCAL = re.compile(r"^(?:eu|uk)representative(?:$|[._-])|(?:^|[._-])dsa(?:$|[._-])")

# Хвост слова, слипшийся с зоной строчными: `info@site.comif` из «.com if you…».
# Чинить такое нельзя (`extract.repair_glued_domain` объясняет почему), но и
# зоны вида «com/net/org + 1–3 буквы» не бывает: ближайшие живые — `.comcast`,
# `.network`, `.organic` — длиннее на четыре. Отказ спасает от отбивки, которая
# бьёт по репутации почтового домена.
GLUED_LOWERCASE_ZONE = re.compile(r"\.(?:com|net|org)[a-z]{1,3}$")

# Обрывок экранирования JSON в начале адреса: `u003eprivacy@…`, `u002f…@…`.
# Извлечение теперь раскодирует `\uXXXX` до поиска (`extract._unescape_js`),
# а правило держит то, что попало в базу раньше, — при сборке письма. Только
# знаки, которые на странице и экранируют (`<`, `>`, `&`, кавычки, `/`, `@`):
# `u0012345@…` — живой идентификатор, и шире правило отсеивало бы людей
# (ревью #122).
JSON_ESCAPE_LOCAL = re.compile(r"^u00(?:3c|3e|26|22|27|2f|40)")

# Подстроки домена: регистраторы и службы скрытия владельца. Такой адрес
# приходит из RDAP и ведёт не к сайту, а к его регистратору.
REGISTRAR_SUBSTRINGS = (
    "whoisguard", "privacyprotect", "privacy-protect", "domainsbyproxy",
    "contactprivacy", "domainprivacy", "withheldforprivacy", "identityprotect",
    "perfectprivacy", "registrarsafe", "namecheap", "godaddy", "gandi.net",
    "tucows", "enom.com", "networksolutions", "publicdomainregistry",
)  # fmt: skip

PLACEHOLDER_LOCAL_PARTS = frozenset(
    {
        "your", "youremail", "your-email", "yourname", "youraddress",
        "name", "firstname", "lastname", "username", "user",
        "email", "domain", "example", "sample", "test",
        # С боевого прогона: со страницы bankofamerica.com снялось `xxx@xxx.xxx`.
        "xxx", "yyy", "zzz", "abc", "asdf", "qwerty", "foo", "bar",
        # Образцы на других языках и «имя-пример»: `unknown@email.com`,
        # `beispiel@email.com` (ревью e6, 02.10.2026), `max.mustermann@…`. Не John
        # Doe: правило общее со сбором ответов, а там это бывает живой адрес.
        "unknown", "beispiel", "ejemplo", "exemple", "esempio", "voorbeeld", "przyklad",
        "mustermann", "maxmustermann", "vorname", "nachname", "vornamenachname",
    }
)  # fmt: skip

# Домен-заглушка из примера на странице: «напишите на you@yourbusiness.com».
# Боевой прогон 23.09.2026 снял `support@yourcompany.com`, `you@yourbusiness.com`
# и `sarah.mitchell@company.com`. Правило по форме имени, а не список доменов:
# заглушек бесконечно много. `your`/`my` — только со словом-заглушкой после:
# голый префикс отрезал бы настоящие сайты вроде `yourstory.com`.
_PLACEHOLDER_WORDS = (
    "company|business|site|website|web|domain|brand|email|mail|name|org"
    "|organization|store|shop|agency|blog|startup|team"
)
PLACEHOLDER_MAIL_DOMAIN = re.compile(
    rf"^(?:(?:your|my)-?(?:{_PLACEHOLDER_WORDS})|company|website|acme|mycompany|client)"
    r"\.[a-z]{2,6}(?:\.[a-z]{2})?$"
)

NO_REPLY_MARKERS = ("noreply@", "no-reply@", "no_reply@", "donotreply@", "do-not-reply@")

# Ключ телеметрии, а не ящик: у DSN Sentry локальная часть — 32 шестнадцатеричных
# символа. Признак по ФОРМЕ, а не по домену, и это принципиально: список доменов
# сервисов такое не ловит. Боевой прогон 27.09.2026 отдал три варианта подряд —
# `@o317978.ingest.sentry.io`, `@sentry.wixpress.com`, `@sentry.zipify.com`, —
# и последний это Sentry, поднятый на собственном домене площадки: сколько
# доменов в список ни добавь, следующий будет новый. Форма же одна на всех.
TELEMETRY_KEY_LOCAL = re.compile(r"^[0-9a-f]{32}$")

# Зоны, которых не бывает в природе: RFC 2606 держит их под примеры.
# Со страниц они приезжают из образцов в разметке — боевой прогон 27.09.2026
# снял `contact@imaginarylane.example`.
#
# ⚠️ `.test` и `.localhost` сюда НЕ входят намеренно, хотя тем же RFC они тоже
# зарезервированы: на них стоят фикстуры этого репозитория (`site.example.test`),
# и зона выбрана именно потому, что в живую сеть по ней не попасть. Запретив её,
# мы отняли бы у тестов единственный безопасный домен. На страницах адрес в
# зоне `.test` не встречается — в отличие от `.example`, который пишут в примерах.
RESERVED_TLDS = (".example", ".invalid")

# Те самые `.test` и `.localhost` из оговорки выше: фикстуры и песочница. Их нет
# в списке публичных суффиксов, и правило «зоны нет в списке» без этой оговорки
# отняло бы у тестов безопасный домен — замер на базе разработки 30.09.2026.
SANDBOX_TLDS = (".test", ".localhost")

# Адреса чужих отделов. Формально живые, но цену за размещение там не
# называют, а письмо в отписку или в жалобы — это заявка на жалобу.
# Пришло с боевого прогона: со страницы experian.com снялся `optout@`.
WRONG_DEPARTMENT_LOCAL_PARTS = frozenset(
    {
        "optout", "opt-out", "unsubscribe", "remove", "abuse", "dmca", "spam", "phishing",
        "legal", "compliance", "privacy", "gdpr", "dpo", "postmaster", "security",
        "careers", "career", "jobs", "job", "hr", "recruiting", "recruitment", "resume",
        "investors", "ir", "returns", "refund", "refunds",
        # Защита данных и права на нескольких языках: `protecciondedatos@axa-…`
        # прошёл бы мимо одного английского «privacy» (ревью e6, 02.10.2026).
        "copyright", "dataprotection", "dataprivacy", "datenschutz", "datenschutzbeauftragter",
        "protecciondedatos", "protezionedati", "privacidad", "privacidade", "rgpd", "dsgvo",
    }
)  # fmt: skip

# Те же отделы, но выделенные поддоменом: `online@consumerprivacy.experian.com`.
# Сверяются МЕТКИ домена целиком, а не подстроки: издание
# `privacyinternational.org` пишет о приватности и остаётся годным донором,
# а `consumerprivacy.experian.com` — это отдел по отпискам.
WRONG_DEPARTMENT_LABELS = frozenset(
    {
        "privacy", "consumerprivacy", "optout", "opt-out", "unsubscribe",
        "abuse", "legal", "dmca", "compliance", "gdpr", "careers", "jobs",
    }
)  # fmt: skip

# Хвост имени файла, приклеившийся к адресу при разборе текста.
FILE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".css", ".js")

_PAGE_WEIGHT = {
    PageKind.MONEY: 300,
    PageKind.CONTACT: 200,
    PageKind.ABOUT: 100,
    PageKind.HOME: 50,
    # Правовая страница — источник последней надежды: адрес там обычно
    # юридический, а не редакционный. Берём, если другого нет.
    PageKind.LEGAL: 20,
}


@dataclass(frozen=True, slots=True)
class Candidate:
    """Найденный адрес вместе с тем, откуда он взялся."""

    email: str
    source: ContactSource
    page_kind: PageKind = PageKind.HOME
    page_url: str | None = None
    # Уверенность платного сервиса, 0–100. У своих ступеней её нет.
    confidence: int | None = None
    #: Восстановлен из обфускации («info [at] site [dot] com»), а не записан
    #: прямо. Такой адрес бывает обычной фразой, сложившейся в правдоподобный
    #: адрес, и прямой адрес побеждает его всегда (`best`).
    guessed: bool = False

    @property
    def local_part(self) -> str:
        return self.email.split("@", 1)[0]

    @property
    def mail_domain(self) -> str:
        return self.email.split("@", 1)[-1]


#: Правила отсева: проверка и то, как она себя называет. Порядок —
#: от самых частых к редким; правило, добавленное сюда, само попадает
#: в отчёт, и настраивать фильтр можно по именам, а не по догадкам.
_RULES: tuple[tuple[Callable[[str, str, str], bool], str], ...] = (
    (lambda value, _l, _d: any(m in value for m in NO_REPLY_MARKERS), "ящик не принимает ответов"),
    (lambda _v, local, _d: _bare(local) in PLACEHOLDER_LOCAL_PARTS, "заглушка вместо адреса"),
    (
        lambda _v, local, _d: len(local) > 1 and len(set(local)) == 1,
        "заглушка из повторённого символа",
    ),
    (
        lambda _v, local, _d: _bare(local) in WRONG_DEPARTMENT_LOCAL_PARTS,
        "чужой отдел: цену за размещение там не называют",
    ),
    (
        lambda _v, _l, domain: bool(set(domain.split(".")[:-2]) & WRONG_DEPARTMENT_LABELS),
        "чужой отдел, выделенный поддоменом",
    ),
    (lambda _v, local, _d: bool(TELEMETRY_KEY_LOCAL.match(local)), "ключ телеметрии, а не ящик"),
    (lambda _v, _l, domain: _is_sentry_subdomain(domain), "поддомен Sentry, а не почта"),
    (lambda _v, _l, domain: _is_vendor(domain), "домен сервиса, а не сайта"),
    (
        lambda _v, _l, domain: bool(set(_labels_above_suffix(domain)) & HOSTER_LABELS),
        "адрес хостера, а не сайта",
    ),
    (
        lambda _v, local, domain: (
            domain in PUBLISHING_PLATFORM_DOMAINS and local in ROLE_LOCAL_PARTS
        ),
        "служебный ящик платформы, а не автора",
    ),
    (
        lambda _v, local, _d: bool(LEGAL_CONTACT_LOCAL.search(local)),
        "юридический представитель платформы, а не редакция",
    ),
    (
        lambda _v, _l, domain: bool(GLUED_LOWERCASE_ZONE.search(domain)),
        "зона со слипшимся хвостом слова — такой зоны нет",
    ),
    (
        lambda _v, local, _d: bool(JSON_ESCAPE_LOCAL.match(local)),
        "обрывок экранирования JSON (\\u00XX), а не адрес",
    ),
    (lambda _v, _l, domain: domain.endswith(RESERVED_TLDS), "зона под примеры, а не живая"),
    (
        lambda _v, _l, domain: bool(PLACEHOLDER_MAIL_DOMAIN.match(domain)),
        "домен-заглушка из примера на странице",
    ),
    (
        lambda _v, _l, domain: any(chunk in domain for chunk in REGISTRAR_SUBSTRINGS),
        "регистратор или служба скрытия владельца",
    ),
    (lambda _v, _l, domain: domain.endswith(FILE_EXTENSIONS), "хвост имени файла"),
    (lambda _v, _l, domain: domain.startswith(".") or ".." in domain, "домен разобран неверно"),
    (
        # Последним, как самое общее: частные правила выше называют причину
        # точнее (`logo@site.com.png` — «хвост имени файла», а не «нет зоны»).
        # Ловит слово, склеенное с зоной не только у com/net/org (`…@site.ruand`,
        # `…@kompas.co.idyang`). Граница — вшитый список публичных суффиксов,
        # тот же, что у ключа донора (`donors.host`).
        lambda _v, _l, domain: not split_host(domain)[2] and not domain.endswith(SANDBOX_TLDS),
        "зоны нет в списке публичных суффиксов — адрес склеен с текстом",
    ),
)


def _bare(local: str) -> str:
    """Локальная часть без разделителей: `data-protection`, `data.protection`
    и `data_protection` — один и тот же ящик, `max.mustermann` — одна заглушка."""
    return local.replace(".", "").replace("-", "").replace("_", "")


def foreign_on_legal_page(candidate: Candidate, *, site_host: str) -> str | None:
    """Сторонний адрес со страницы правил или конфиденциальности — не адрес редакции.

    На такой странице чужой домен почти всегда регулятор (живой прогон:
    у сайта о продажах со страницы политики снялся адрес хорватского
    ведомства по защите данных), обработчик данных или юрист родителя
    (ревью e6, 02.10.2026). Остаются адреса на домене сайта и бесплатная
    почта: у маленьких сайтов в политике стоит ящик владельца. Страница
    «о нас» и Impressum — не такие: там сторонний адрес — обычно оператор.
    """
    if candidate.page_kind is not PageKind.LEGAL or candidate.source is not ContactSource.PAGE:
        return None
    domain = candidate.mail_domain
    if domain == site_host or domain.endswith(f".{site_host}") or domain in FREE_MAILBOX_DOMAINS:
        return None
    return f"сторонний адрес со страницы правил — регулятор или юрист, а не редакция: {candidate.email}"


def _is_vendor(domain: str) -> bool:
    """Домен сервиса — он сам или любой его поддомен.

    Точного совпадения не хватает, и это выяснилось на боевом прогоне:
    со страниц приезжают ключи Sentry вида
    `f421b49239504a9a9acbf7335cb6e058@o317978.ingest.sentry.io` и
    `…@sentry.wixpress.com`. Домен в списке есть, но адрес был на ТРЕТЬЕМ
    уровне, проверка `domain in VENDOR_DOMAINS` его не видела, и ключ
    телеметрии уезжал в базу как контакт редакции — правдоподобный на вид
    и мёртвый по существу.
    """
    if domain in VENDOR_DOMAINS:
        return True
    return any(domain.endswith(f".{vendor}") for vendor in VENDOR_DOMAINS)


def _labels_above_suffix(domain: str) -> list[str]:
    """Метки домена без публичного суффикса: у `monsite.ovh` это только `monsite`."""
    subdomain, name, _suffix = split_host(domain)
    return [*subdomain.split("."), name] if subdomain else [name]


def _is_sentry_subdomain(domain: str) -> bool:
    """Ключ телеметрии живёт на поддомене: `…@sentry.wixpress.com`.

    `sentry.com` и `sentry.co.uk` — сайты, а не поддомены: пока правило
    смотрело на первую метку домена, их ящики отсеивались вместе с ключами.
    """
    subdomain, _name, _suffix = split_host(domain)
    return subdomain.split(".")[0] == "sentry"


#: Отказ, когда строка вовсе не адрес: у него нет адреса в хвосте.
NOT_AN_ADDRESS = "не похож на адрес"


def rejection_reason(email: str) -> str | None:
    """Почему адресом нельзя пользоваться. `None` — можно.

    Причина называет и правило, и сам адрес: «отсеян фильтром» без имени
    правила не даёт его настроить.
    """
    value = email.strip().lower()
    if not value or not EMAIL_RE.fullmatch(value):
        return NOT_AN_ADDRESS

    local, _, domain = value.partition("@")
    for matches, title in _RULES:
        if matches(value, local, domain):
            return f"{title}: {value}"
    return None


def trusted_guess(email: str, *, site_host: str) -> bool:
    """Можно ли верить адресу, восстановленному из обфускации.

    Верим только своему домену сайта и известной бесплатной почте. Всё
    остальное после замены «at» и «dot» — обычные слова, случайно
    сложившиеся в правдоподобный адрес (`extract.extract_obfuscated`).
    """
    domain = email.split("@", 1)[-1]
    return domain == site_host or domain.endswith(f".{site_host}") or domain in FREE_MAILBOX_DOMAINS


def weight(candidate: Candidate, *, site_host: str) -> int:
    """Чем больше, тем ценнее адрес. Порядок описан в okf/contact-ladder.md."""
    score = _PAGE_WEIGHT.get(candidate.page_kind, 0)

    domain = candidate.mail_domain
    if domain == site_host or domain.endswith(f".{site_host}"):
        score += 40  # адрес на домене сайта — у того, кто им распоряжается
    elif domain in FREE_MAILBOX_DOMAINS:
        score += 10  # личный ящик вебмастера: писать можно, но это слабее

    if candidate.local_part in ROLE_LOCAL_PARTS:
        score += 20

    if candidate.confidence is not None:
        score += candidate.confidence // 10

    return score


def best(candidates: list[Candidate], *, site_host: str) -> Candidate | None:
    """Лучший адрес из найденных. Пустой список — законный исход.

    Догадка из обфускации идёт только тогда, когда прямого адреса нет вовсе:
    с весом за домен сайта «here at site.com» выигрывал у настоящего ящика
    со страницы контактов (ревью e6, 02.10.2026).
    """
    if not candidates:
        return None
    return max(
        candidates, key=lambda c: (not c.guessed, weight(c, site_host=site_host), -len(c.email))
    )
