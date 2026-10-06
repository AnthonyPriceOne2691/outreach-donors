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

**Признаки — общие для любой ниши** (06.10.2026, слово Anthony: «оценка не
под конкретную нишу, мультинишевая» — агентство разбирает разные ниши).
До этого коммерческий анкор узнавали по словам ставок («odds», «deposit»,
«bonus») и по «review» где угодно в коротком анкоре, а от этой проверки
зависели ещё четыре надбавки. Финансовый блог дал «куплено» 4 из 4 —
и все ложные: сайт основателя блога («my year-end review», 431 страница),
соседние блоги («his review»), бонус банка 2007 года.

Признаки делятся по силе:

- **сильные** говорят «здесь деньги» сами, в любой нише: `rel=sponsored`,
  пометка рекламы, партнёрская метка в адресе (`affiliate.py`) и призыв
  к действию в начале анкора («Buy now», «Sign up», «Claim bonus»);
- **слабые** — слово сделки в коротком анкоре без «my/his/our»
  («discount», «price», «review»). Из них одних набирается не больше
  «спорно»: решает человек.

Повторяемость и «встречается у двух доноров» умножают только сильный
признак: ссылка со многих страниц без него — автор, блогролл, навигация.

**Давнее размещение — не «существующий рекламодатель».** Если самая свежая
статья со ссылкой на домен старше `CRAWL_STALE_YEARS` (3 года), он идёт
«мимо» с причиной. Без даты правило молчит: незнание — не давность.

**Чего в скоринге нет.** «Тематика не совпадает» (+1): тематики донора
в базе нет — есть домен, страна и ключи прогона. Строка требования
осталась невыполненной, и это сказано здесь, а не спрятано нулём.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime

from backend.config import crawl as cfg
from backend.features.core.domain import Verdict
from backend.features.crawl.affiliate import NETWORK_ROOTS, affiliation
from backend.features.crawl.anchors import AnchorKind, anchor_kind
from backend.features.crawl.denylist import Denial, DenyReason, denial_for, is_own_brand
from backend.features.crawl.links import OutLink
from backend.features.crawl.markers import marker_reason
from backend.features.crawl.paid_pages import paid_pages

logger = logging.getLogger(__name__)

# --- Веса требования ---
POINTS_REL_SPONSORED = 5
POINTS_MARKER = 4
POINTS_DOFOLLOW_IN_BODY = 2
POINTS_COMMERCIAL_ANCHOR = 1

#: Партнёрская метка в адресе ссылки (`affiliate.py`): платные отношения
#: рекламодателя с донором, одинаковые в любой нише.
POINTS_AFFILIATE = 2
POINTS_PAID_SOURCE = 2
#: Зона некоммерческих организаций: фонды, сообщества, ассоциации. Платную
#: статью о себе они заказывают редко, и «куплено» им без человека не дают —
#: ethereum.org, cardano.org, bitcoin.org стояли источниками в чужих статьях.
NONPROFIT_SUFFIXES: tuple[str, ...] = (".org",)

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
#: Призыв к действию или партнёрская ссылка, закрытые `nofollow`: донор сам
#: пометил ссылку как рекламу. Требование награждает обратное — dofollow.
POINTS_PAID_LOOKING = 2

#: Порог «куплена» и нижняя граница ручной проверки — из требования.
BOUGHT_AT = 4
REVIEW_AT = 2


_KIND_WORDS = {
    AnchorKind.CALL: "призыв",
    AnchorKind.TRADE: "товар серой ниши",
    AnchorKind.DEAL: "слово сделки",
}


@dataclass(frozen=True, slots=True)
class LinkScore:
    """Баллы одной ссылки и то, из чего они сложились.

    `strong` — у ссылки есть признак, который сам говорит «здесь деньги»
    в любой нише; повторяемость умножает только такие.
    """

    points: int
    reasons: tuple[str, ...]
    strong: bool = False


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
    #: DR домена, если его спрашивали (`big_sites`); `None` — не спрашивали
    #: или провайдер домена не знает.
    dr: int | None = None
    #: Есть сильный признак рекламы — усилитель «у двух доноров» работает
    #: только поверх него.
    strong: bool = False
    #: Все статьи со ссылкой на домен старше `CRAWL_STALE_YEARS`: усилитель
    #: не возвращает давнего в «куплено».
    stale: bool = False


def score_link(link: OutLink, *, source: bool = False) -> LinkScore:
    """Баллы одной ссылки по признакам самой ссылки.

    `source` — ссылка стоит в статье, помеченной платной целиком, и статья
    не о её домене (`paid_pages`): пометка достаётся ей как источнику.

    Повторяемость сюда не входит: она про домен, а не про ссылку,
    и считается уровнем выше.
    """
    points = 0
    reasons: list[str] = []
    strong = False

    marked = marker_reason(link)
    if source and (link.sponsored or marked is not None):
        points += POINTS_PAID_SOURCE
        reasons.append(
            f"ссылка-источник в платной статье (пометка на всех её ссылках) +{POINTS_PAID_SOURCE}"
        )
    else:
        if link.sponsored:
            points += POINTS_REL_SPONSORED
            reasons.append(f"rel=sponsored +{POINTS_REL_SPONSORED}")
            strong = True
        if marked is not None:
            points += POINTS_MARKER
            reasons.append(f"пометка рекламного материала ({marked}) +{POINTS_MARKER}")
            strong = True
    affiliate = affiliation(link.url)
    if affiliate is not None:
        points += POINTS_AFFILIATE
        reasons.append(f"партнёрская ссылка ({affiliate.mark}) +{POINTS_AFFILIATE}")
        strong = True

    kind = anchor_kind(link.anchor)
    calls = kind in (AnchorKind.CALL, AnchorKind.TRADE) or affiliate is not None
    strong = strong or kind is AnchorKind.CALL
    if link.dofollow and link.in_body and (kind is not AnchorKind.PLAIN or affiliate):
        points += POINTS_DOFOLLOW_IN_BODY
        reasons.append(f"dofollow из тела на коммерческий +{POINTS_DOFOLLOW_IN_BODY}")
    if kind is not AnchorKind.PLAIN:
        what = _KIND_WORDS[kind]
        points += POINTS_COMMERCIAL_ANCHOR
        reasons.append(f"коммерческий анкор ({what}) +{POINTS_COMMERCIAL_ANCHOR}")
    if calls and not link.dofollow and not link.sponsored:
        # Признак из замера: донор закрыл призыв или партнёрскую ссылку
        # от передачи веса, но не пометил `sponsored`. Так выглядит
        # размещение там, где правила требования дают ноль.
        points += POINTS_PAID_LOOKING
        reasons.append(f"коммерческая ссылка под nofollow +{POINTS_PAID_LOOKING}")

    return LinkScore(points=points, reasons=tuple(reasons), strong=strong)


def _verdict_for(points: int) -> Verdict:
    if points >= BOUGHT_AT:
        return Verdict.BOUGHT
    if points >= REVIEW_AT:
        return Verdict.PENDING
    return Verdict.SKIPPED


def score_candidates(
    links: list[OutLink], donor_root: str = "", *, today: date | None = None
) -> list[Candidate]:
    """Кандидаты по домену-получателю: балл, вердикт и причины.

    Единица решения — домен, а не ссылка: письмо уходит владельцу
    домена один раз, сколько бы страниц он ни занимал. Балл домена —
    лучший балл его ссылок плюс надбавка за повторяемость, если у домена
    есть сильный признак рекламы.

    Ссылка через сеть партнёрок считается ссылкой на рекламодателя,
    которого сеть назвала в адресе (`affiliate.py`).

    `donor_root` нужен для одного случая, найденного на живых данных:
    донор ссылается на свой же бренд в другой зоне. Корни разные,
    владелец один.
    """
    moment = today or datetime.now(UTC).date()
    resolved = [_advertiser_of(link) for link in links]
    pages = paid_pages(resolved)
    by_root: defaultdict[str, list[OutLink]] = defaultdict(list)
    for link in resolved:
        by_root[link.target_root].append(link)

    out = [_candidate(root, group, donor_root, moment, pages) for root, group in by_root.items()]
    out.sort(key=lambda c: (-c.points, c.target_root))
    return out


def _advertiser_of(link: OutLink) -> OutLink:
    """Ссылка через сеть партнёрок — к рекламодателю, которого сеть назвала."""
    found = affiliation(link.url)
    if found is None or found.target_root is None or found.target_host is None:
        return link
    return replace(link, target_host=found.target_host, target_root=found.target_root)


def _denial(root: str, group: list[OutLink], donor_root: str) -> Denial | None:
    denial = denial_for(root, group[0].url)
    if denial is None and is_own_brand(root, donor_root):
        denial = Denial(DenyReason.OWN_BRAND, donor_root)
    if denial is None and root in NETWORK_ROOTS:
        # Сеть не назвала рекламодателя: письмо ушло бы сети, а не тому,
        # кто платит донору.
        denial = Denial(DenyReason.HIDDEN, root)
    return denial


def _candidate(
    root: str,
    group: list[OutLink],
    donor_root: str,
    today: date,
    paid: dict[str, frozenset[str]],
) -> Candidate:
    denial = _denial(root, group, donor_root)
    pages = len({link.page_url for link in group})
    scored = [(link, score_link(link, source=_is_source(link, paid))) for link in group]
    best, score = max(scored, key=lambda pair: pair[1].points)
    strong = any(link_score.strong for _, link_score in scored)

    points = score.points
    reasons = list(score.reasons)
    if pages >= REPEAT_PAGES and strong:
        points += POINTS_REPEATED
        reasons.append(f"ссылки с {pages} страниц донора +{POINTS_REPEATED}")
    elif pages >= REPEAT_PAGES:
        # Повторяемость без сильного признака — не схема, а автор, блогролл,
        # навигация или обязательная ссылка («играй ответственно»): сайт
        # основателя блога стоял на 431 странице (финансы, 06.10).
        reasons.append(
            f"ссылки с {pages} страниц, но сильных признаков рекламы нет — "
            "автор, блогролл или навигация"
        )

    stale = _stale(group, today)
    if denial is not None:
        verdict = Verdict.BLOCKED
        reasons.append(f"кому не пишем: {denial.reason.value} ({denial.matched})")
    elif stale is not None:
        verdict = Verdict.SKIPPED
        reasons.append(stale)
    else:
        verdict = _verdict_for(points)
    if verdict is Verdict.BOUGHT and root.endswith(NONPROFIT_SUFFIXES):
        verdict = Verdict.PENDING
        reasons.append("зона .org — чаще фонд или сообщество, чем рекламодатель: решает человек")

    return Candidate(
        target_root=root,
        points=points,
        verdict=verdict,
        reasons=reasons,
        links=len(group),
        pages=pages,
        denial=denial,
        best_link=best,
        strong=strong,
        stale=stale is not None,
    )


def _is_source(link: OutLink, paid: dict[str, frozenset[str]]) -> bool:
    subjects = paid.get(link.page_url)
    return subjects is not None and link.target_root not in subjects


def _stale(group: list[OutLink], today: date) -> str | None:
    """«Давнее размещение» — если все статьи со ссылкой датированы и старые.

    Хоть одна статья без даты — правило молчит: незнание — не давность.
    """
    dates = [link.page_published for link in group]
    known = [day for day in dates if day is not None]
    if not known or len(known) < len(dates):
        return None
    freshest = max(known)
    if freshest >= _years_before(today, cfg.STALE_YEARS):
        return None
    return (
        f"давнее размещение: последняя статья {freshest:%d.%m.%Y} — "
        f"старше {cfg.STALE_YEARS} лет, рекламодатель не нынешний"
    )


def _years_before(today: date, years: int) -> date:
    # 29 февраля N лет назад бывает не всегда — тогда 28-е.
    day = 28 if (today.month, today.day) == (2, 29) else today.day
    return today.replace(year=today.year - years, day=day)


def boost_across_donors(candidates: list[Candidate], seen_on: dict[str, int]) -> None:
    """Усилитель требования: домен встречается на ≥ 2 наших донорах.

    Считается снаружи — по всей базе, а не по одному обходу: в одном
    обходе донор всегда один, и признак «встречается у двоих» внутри
    него не вычисляется никак. Поднять вердикт он может, опустить — нет.
    """
    for candidate in candidates:
        if seen_on.get(candidate.target_root, 0) < 2 or candidate.verdict is Verdict.BLOCKED:
            continue
        if not candidate.strong or candidate.stale:
            # Тот же случай, что у повторяемости: на регулятора ссылаются
            # все доноры ниши, на соседний блог — все блоги ниши, и
            # «встречается у двоих» про них верно, а рекламодателем их не
            # делает. Давнего усилитель в «куплено» тоже не возвращает.
            continue
        candidate.points += POINTS_REPEATED
        candidate.reasons.append(
            f"встречается у {seen_on[candidate.target_root]} наших доноров +{POINTS_REPEATED}"
        )
        candidate.verdict = _verdict_for(candidate.points)
