"""Скоринг ссылок: куплено, спорно или мимо.

Шкала и пороги заданы требованием: `rel=sponsored` +5; пометка
sponsored/advertorial/partner/guest post +4; dofollow на внешний
коммерческий домен из тела статьи +2; коммерческий анкор +1; тематика
не совпадает +1. **≥ 4 — купленная, 2–3 — на ручную проверку.**

**Правила требования оставлены как есть, но их не хватает, и это
замерено.** На пяти донорах боевой ниши платные размещения выглядят
так: `nofollow`, без пометки `sponsored`, вне тела статьи — то есть
по правилам требования набирают ноль или единицу и уходят мимо.
А dofollow-ссылки, которые правила награждают, ведут на регистратора
домена и счётчик посещаемости (`okf/advertiser-discovery.md`).

Поэтому добавлены два признака, которых в требовании нет, и оба
получены из данных, а не придуманы:

1. **Повторяемость.** Партнёрская программа получает с донора 244, 84,
   48 ссылок; редакционное упоминание — одну-две. Признак сильный,
   но **работает только поверх коммерческого**: первый прогон по живым
   данным выдал «куплена» регулятору азартных игр, на которого ссылается
   каждая страница каждого донора ниши.
2. **Коммерческий анкор при `nofollow`.** Ссылка «Играть», закрытая
   от передачи веса, — это признанная самим донором реклама. Требование
   награждает dofollow, а здесь нужен ровно обратный признак.

**Чего в скоринге нет.** «Тематика не совпадает» (+1): тематики донора
в базе нет — есть домен, страна и ключи прогона. Строка требования
осталась невыполненной, и это сказано здесь, а не спрятано нулём.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field

from backend.features.core.domain import Verdict
from backend.features.crawl.denylist import Denial, DenyReason, denial_for, is_own_brand
from backend.features.crawl.links import OutLink

logger = logging.getLogger(__name__)

# --- Веса требования ---
POINTS_REL_SPONSORED = 5
POINTS_MARKER = 4
POINTS_DOFOLLOW_IN_BODY = 2
POINTS_COMMERCIAL_ANCHOR = 1

# --- Веса, полученные замером ---
#: Донор ссылается на домен с нескольких страниц. Порог — три страницы:
#: одна-две бывают у редакционного упоминания, дальше начинается схема.
#:
#: **Надбавка не работает в одиночку.** Первый же прогон по настоящим
#: данным выдал «куплена» регулятору азартных игр: на него по закону
#: ссылается каждая страница каждого донора ниши, и одной повторяемости
#: хватило на четыре балла. Повторяемость отвечает на вопрос «насколько
#: это похоже на схему», а не «реклама ли это вообще» — поэтому
#: она умножает уже найденный коммерческий признак, а не заменяет его.
POINTS_REPEATED = 3
REPEAT_PAGES = 3
#: Коммерческий анкор, закрытый `nofollow`: донор сам пометил ссылку
#: как рекламу. Требование награждает обратное — dofollow.
POINTS_PAID_LOOKING = 2

#: Порог «куплена» и нижняя граница ручной проверки — из требования.
BOUGHT_AT = 4
REVIEW_AT = 2

#: Слова, которыми размечают рекламные материалы. Ищутся в адресе
#: страницы и в анкоре: разметка `rel` у доноров этой ниши почти
#: не встречается, а слово в адресе раздела — встречается.
MARKER_WORDS: tuple[str, ...] = (
    "sponsored", "advertorial", "guest-post", "guest_post", "guestpost",
    "partner", "paid-post", "promoted", "advertisement", "advertising",
)  # fmt: skip

#: Коммерческий анкор. Список под нишу ставок и общие призывы к действию;
#: он **не универсален** и меняется вместе с нишей — это его свойство,
#: а не изъян.
MONEY_WORDS: tuple[str, ...] = (
    "bet", "bets", "betting", "casino", "bonus", "odds", "promo", "code",
    "play", "join", "sign up", "signup", "register", "claim", "offer",
    "deposit", "free spins", "review", "visit", "get", "now", "welcome",
)  # fmt: skip

_WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True, slots=True)
class LinkScore:
    """Баллы одной ссылки и то, из чего они сложились."""

    points: int
    reasons: tuple[str, ...]


@dataclass(slots=True)
class Candidate:
    """Кандидат в рекламодатели: домен и всё, что про него известно."""

    target_root: str
    points: int
    verdict: Verdict
    reasons: list[str] = field(default_factory=list)
    links: int = 0
    pages: int = 0
    denial: Denial | None = None
    best_link: OutLink | None = None


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def has_marker(link: OutLink) -> bool:
    """Материал помечен рекламным — в адресе страницы или в анкоре."""
    haystack = f"{link.page_url} {link.anchor}".lower()
    return any(word in haystack for word in MARKER_WORDS)


def is_commercial_anchor(anchor: str) -> bool:
    """Анкор зовёт к действию или называет товар.

    Проверяется по словам, а не подстрокой: подстрока `bet` живёт внутри
    `better`, `alphabet` и десятка обычных слов, и без разбиения
    коммерческим оказывался любой текст.
    """
    return bool(_words(anchor) & set(MONEY_WORDS)) or any(
        phrase in anchor.lower() for phrase in ("sign up", "free spins")
    )


def score_link(link: OutLink) -> LinkScore:
    """Баллы одной ссылки по признакам самой ссылки.

    Повторяемость сюда не входит: она про домен, а не про ссылку,
    и считается уровнем выше.
    """
    points = 0
    reasons: list[str] = []

    if link.sponsored:
        points += POINTS_REL_SPONSORED
        reasons.append(f"rel=sponsored +{POINTS_REL_SPONSORED}")
    if has_marker(link):
        points += POINTS_MARKER
        reasons.append(f"пометка рекламного материала +{POINTS_MARKER}")

    commercial = is_commercial_anchor(link.anchor)
    if link.dofollow and link.in_body and commercial:
        points += POINTS_DOFOLLOW_IN_BODY
        reasons.append(f"dofollow из тела на коммерческий +{POINTS_DOFOLLOW_IN_BODY}")
    if commercial:
        points += POINTS_COMMERCIAL_ANCHOR
        reasons.append(f"коммерческий анкор +{POINTS_COMMERCIAL_ANCHOR}")
    if commercial and not link.dofollow and not link.sponsored:
        # Признак из замера: донор закрыл коммерческую ссылку от передачи
        # веса, но не пометил `sponsored`. Так выглядит размещение в нише,
        # где правила требования дают ноль.
        points += POINTS_PAID_LOOKING
        reasons.append(f"коммерческий анкор под nofollow +{POINTS_PAID_LOOKING}")

    return LinkScore(points=points, reasons=tuple(reasons))


def _verdict_for(points: int) -> Verdict:
    if points >= BOUGHT_AT:
        return Verdict.BOUGHT
    if points >= REVIEW_AT:
        return Verdict.PENDING
    return Verdict.SKIPPED


def score_candidates(links: list[OutLink], donor_root: str = "") -> list[Candidate]:
    """Кандидаты по домену-получателю: балл, вердикт и причины.

    Единица решения — домен, а не ссылка: письмо уходит владельцу
    домена один раз, сколько бы страниц он ни занимал. Балл домена —
    лучший балл его ссылок плюс надбавка за повторяемость.

    `donor_root` нужен для одного случая, найденного на живых данных:
    донор ссылается на свой же бренд в другой зоне. Корни разные,
    владелец один.
    """
    by_root: defaultdict[str, list[OutLink]] = defaultdict(list)
    for link in links:
        by_root[link.target_root].append(link)

    out: list[Candidate] = []
    for root, group in by_root.items():
        denial = denial_for(root, group[0].url)
        if denial is None and is_own_brand(root, donor_root):
            denial = Denial(DenyReason.OWN_BRAND, donor_root)
        pages = len({link.page_url for link in group})
        best = max(group, key=lambda link: score_link(link).points)
        score = score_link(best)

        points = score.points
        reasons = list(score.reasons)
        if pages >= REPEAT_PAGES and points > 0:
            points += POINTS_REPEATED
            reasons.append(f"ссылки с {pages} страниц донора +{POINTS_REPEATED}")
        elif pages >= REPEAT_PAGES:
            # Повторяемость без единого коммерческого признака — это
            # обязательная ссылка: регулятор, лицензия, «играй
            # ответственно». Она есть на каждой странице и рекламой
            # не является.
            reasons.append(f"ссылки с {pages} страниц, но коммерческих признаков нет")

        if denial is not None:
            verdict = Verdict.BLOCKED
            reasons.append(f"кому не пишем: {denial.reason.value} ({denial.matched})")
        else:
            verdict = _verdict_for(points)

        out.append(
            Candidate(
                target_root=root,
                points=points,
                verdict=verdict,
                reasons=reasons,
                links=len(group),
                pages=pages,
                denial=denial,
                best_link=best,
            )
        )

    out.sort(key=lambda c: (-c.points, c.target_root))
    return out


def boost_across_donors(candidates: list[Candidate], seen_on: dict[str, int]) -> None:
    """Усилитель требования: домен встречается на ≥ 2 наших донорах.

    Считается снаружи — по всей базе, а не по одному обходу: в одном
    обходе донор всегда один, и признак «встречается у двоих» внутри
    него не вычисляется никак. Поднять вердикт он может, опустить — нет.
    """
    for candidate in candidates:
        if seen_on.get(candidate.target_root, 0) < 2 or candidate.verdict is Verdict.BLOCKED:
            continue
        if candidate.points <= 0:
            # Тот же случай, что у повторяемости: на регулятора ссылаются
            # все доноры ниши, и «встречается у двоих» про него верно,
            # а рекламодателем его не делает.
            continue
        candidate.points += POINTS_REPEATED
        candidate.reasons.append(
            f"встречается у {seen_on[candidate.target_root]} наших доноров +{POINTS_REPEATED}"
        )
        candidate.verdict = _verdict_for(candidate.points)
