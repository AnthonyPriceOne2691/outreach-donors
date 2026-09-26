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
from backend.features.core.domain import ContactSource, PageKind

# Ролевые адреса. Берём даже на чужом домене: это контакт редакции,
# а не личная почта случайного человека.
ROLE_LOCAL_PARTS = frozenset(
    {
        "info", "contact", "contacts", "hello", "hi", "mail", "email", "office",
        "admin", "administrator", "webmaster", "support", "help",
        "editor", "editorial", "redaktion", "redazione", "redaksi",
        "press", "media", "marketing", "sales", "partnership", "partnerships",
        "ads", "advertise", "advertising", "advertisement", "iklan",
        "enquiries", "enquiry", "inquiries", "team", "kontakt", "kontak", "contacto",
    }
)  # fmt: skip

# Бесплатная почта. Личный ящик вебмастера — валидный контакт, но весит
# меньше адреса на домене сайта: на домене сидит тот, кто им распоряжается.
FREE_MAILBOX_DOMAINS = frozenset(
    {
        "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "yahoo.co.id",
        "hotmail.com", "hotmail.co.uk", "outlook.com", "live.com", "aol.com",
        "icloud.com", "me.com", "proton.me", "protonmail.com", "gmx.com", "gmx.net",
        "mail.ru", "yandex.ru", "yandex.com", "zoho.com", "mail.com",
        "qq.com", "163.com", "126.com", "naver.com",
    }
)  # fmt: skip

# Точные домены сервисов: аналитика, CDN, конструкторы сайтов, соцсети.
VENDOR_DOMAINS = frozenset(
    {
        "example.com", "example.org", "example.net", "domain.com", "yourdomain.com",
        "sentry.io", "wixpress.com", "wix.com", "squarespace.com", "shopify.com",
        "schema.org", "w3.org", "google.com", "googleapis.com", "gstatic.com",
        "facebook.com", "twitter.com", "youtube.com", "instagram.com",
        "jsdelivr.net", "gravatar.com", "cloudflare.com", "wordpress.com", "wordpress.org",
    }
)  # fmt: skip

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
    rf"^(?:(?:your|my)-?(?:{_PLACEHOLDER_WORDS})|company|website|acme|mycompany)"
    r"\.[a-z]{2,6}(?:\.[a-z]{2})?$"
)

NO_REPLY_MARKERS = ("noreply@", "no-reply@", "no_reply@", "donotreply@", "do-not-reply@")

# Адреса чужих отделов. Формально живые, но цену за размещение там не
# называют, а письмо в отписку или в жалобы — это заявка на жалобу.
# Пришло с боевого прогона: со страницы experian.com снялся `optout@`.
WRONG_DEPARTMENT_LOCAL_PARTS = frozenset(
    {
        "optout", "opt-out", "unsubscribe", "remove", "abuse", "dmca", "spam", "phishing",
        "legal", "compliance", "privacy", "gdpr", "dpo", "postmaster", "security",
        "careers", "career", "jobs", "job", "hr", "recruiting", "recruitment", "resume",
        "investors", "ir", "returns", "refund", "refunds",
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
    (lambda _v, local, _d: local in PLACEHOLDER_LOCAL_PARTS, "заглушка вместо адреса"),
    (
        lambda _v, local, _d: len(local) > 1 and len(set(local)) == 1,
        "заглушка из повторённого символа",
    ),
    (
        lambda _v, local, _d: local in WRONG_DEPARTMENT_LOCAL_PARTS,
        "чужой отдел: цену за размещение там не называют",
    ),
    (
        lambda _v, _l, domain: bool(set(domain.split(".")[:-2]) & WRONG_DEPARTMENT_LABELS),
        "чужой отдел, выделенный поддоменом",
    ),
    (lambda _v, _l, domain: domain in VENDOR_DOMAINS, "домен сервиса, а не сайта"),
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
)


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
    """Лучший адрес из найденных. Пустой список — законный исход."""
    if not candidates:
        return None
    return max(candidates, key=lambda c: (weight(c, site_host=site_host), -len(c.email)))
