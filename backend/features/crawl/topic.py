"""«Тематика не совпадает» +1 — строка шкалы требования, которой до сверки ТЗ 11.10.2026 не было.

**Тема донора — ключи прогона, по которым он нашёлся** (`found_by` прогона Этапа 1;
нет записи — все ключи прогона). Донор, заведённый руками, прогона не знает — его
тема неизвестна.

**Тема ссылки — анкор и путь целевой страницы.** Текст вокруг ссылки обход не хранит
(требование к хранению: сырой HTML не храним — только ссылки, анкоры и адрес
страницы), поэтому его нет и здесь. Тема рекламодателя — по всем его ссылкам
у донора: хоть одна о том же, что донор, — тематика совпадает.

**Слова, а не подстроки, и без окончаний.** «betting» и «bet», «loans» и «loan» —
одна тема: окончания срезаются, и слово, начинающее другое (от трёх букв), считается
тем же. Ошибка этого приёма — в сторону «совпадает», то есть без балла: лишний балл
дороже пропущенного.

**Незнание — не несовпадение.** Правило молчит, если у донора нет ключей или у ссылок
домена меньше двух слов темы: анкор — имя бренда («Acme»), путь — «/», призыв
(«Sign up», «Claim bonus»), слово сделки («review», «discount»), одно слово вроде
«honest». Имя бренда о теме не говорит ничего, призыв и сделка — о действии,
а не о предмете, одно слово — шум.

**Слабый признак.** Баллы требования складываются как есть, но из слабых признаков
«куплено» не набирается (`okf/link-scoring.md`): этот балл не поднимает до «куплено»
кандидата без сильного признака рекламы — решает человек (`scoring._candidate`).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.models.domain import DomainModel
from backend.features.core.models.run import RunCandidateModel, RunModel
from backend.features.crawl.anchors import DEAL_WORDS, PERSONAL, is_address
from backend.features.crawl.links import OutLink
from backend.shared.net.url_parts import split_url

#: Слово темы: буквы любого алфавита, без цифр. Короче трёх — служебное.
_LETTERS = re.compile(r"[^\W\d_]{3,}")

#: Слова, которые о предмете не говорят: служебные, оценочные, части адресов
#: и сами слова «сайт», «онлайн». Без них «best CRM» и «best betting sites»
#: совпали бы по «best».
_NOT_A_TOPIC: frozenset[str] = frozenset(
    {
        "the", "and", "for", "with", "from", "into", "your", "you", "this", "that", "one",
        "two", "its", "but", "not", "can", "will", "just", "only", "also", "than", "then",
        "out", "off", "via", "per", "very", "much", "many", "some", "other", "own", "same",
        "these", "those", "are", "was", "were", "has", "have", "how", "what", "why", "who",
        "when", "where", "which", "about", "more", "most", "best", "top", "new", "now",
        "free", "all", "any", "near", "here", "there", "click", "read", "learn",
        "www", "http", "https", "html", "htm", "php", "asp", "aspx", "index", "home",
        "page", "pages", "post", "posts", "blog", "article", "articles", "category", "tag",
        "tags", "amp", "utm", "com", "net", "org", "site", "sites", "website", "websites",
        "online", "web", "official", "link", "links", "url", "default", "main", "guide",
        "guides", "tips", "list", "news", "info", "ultimate", "complete",
    }
)  # fmt: skip

#: Призыв и слово сделки в анкоре — о действии, а не о предмете (`anchors.py`).
#: Только у ссылки: в ключах донора «book», «play», «shop» — тема («book hotel»).
#: «bet» сюда не входит: в адресе `/sports/bet/` это предмет, а не глагол.
_ACTION_WORDS: frozenset[str] = frozenset(
    {
        "sign", "signup", "buy", "order", "claim", "register", "subscribe", "get", "start",
        "started", "try", "apply", "join", "visit", "use", "download", "install", "play",
        "shop", "book", "yours", "today", "see", "find", "check", "discover", "explore",
        "compare", "save", "win", "earn", "more",
    }
    | DEAL_WORDS
)  # fmt: skip

#: Меньше стольких слов темы у ссылок домена — темы не видно: одно слово — это
#: «honest» или «guide», а не предмет. «payday loans», «car insurance» — уже тема.
MIN_LINK_WORDS = 2

#: Окончания, которые срезаются: множественное, причастие, деятель.
_ENDINGS: tuple[str, ...] = ("ings", "ing", "ers", "er", "ies", "es", "s", "ed")

#: Короче этого основа не обрезается и началом другого слова не считается.
MIN_STEM = 3


def _stem(word: str) -> str:
    for ending in _ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= MIN_STEM:
            return word[: -len(ending)]
    return word


#: Латиницей длиннее этого слово — не слово, а метка партнёрки в адресе
#: (`/siepxuosjnofgkewnd/`); без гласных — тоже (`/trddp/`).
_MAX_WORD = 15
_VOWELS = frozenset("aeiouy")


def _is_word(token: str) -> bool:
    """Похоже на слово, а не на метку: у латиницы есть гласные и разумная длина."""
    if not token.isascii():
        return True
    return len(token) <= _MAX_WORD and not _VOWELS.isdisjoint(token)


def topic_words(text: str, *, skip: Iterable[str] = ()) -> set[str]:
    """Слова темы в тексте: без служебных, меток и слов `skip`."""
    dropped = _NOT_A_TOPIC | PERSONAL | set(skip)
    return {
        word for word in _LETTERS.findall(text.lower()) if word not in dropped and _is_word(word)
    }


def stems(text: str, *, skip: Iterable[str] = ()) -> set[str]:
    """Основы слов темы в тексте — то, что сравнивается."""
    return {_stem(word) for word in topic_words(text, skip=skip)}


def _alike(one: str, other: str) -> bool:
    """Одна тема: основы равны или одна начинает другую («bet» — «bett[ing]»)."""
    short, long_ = sorted((one, other), key=len)
    return len(short) >= MIN_STEM and long_.startswith(short)


def _labels(root: str) -> list[str]:
    """Буквенные части имени домена: `content-hub.example.test` → `content`, `hub`…"""
    return _LETTERS.findall(root.lower())


def link_words(link: OutLink, donor_root: str = "") -> set[str]:
    """Тема одной ссылки: анкор (если это текст, а не адрес) и путь целевой страницы.

    Без имён получателя и донора — имя бренда о теме не говорит ничего. Имя
    ищется и внутри слитного домена: анкор «Content Hub» у `contenthub.example.test`,
    профиль донора в соцсети (`/donorname`).
    """
    parts = split_url(link.url)
    path = parts.path if parts is not None else ""
    anchor = "" if is_address(link.anchor) else link.anchor
    names = _labels(link.target_root) + _labels(donor_root)
    words = topic_words(f"{anchor} {path}", skip=_ACTION_WORDS)
    return {word for word in words if not any(word in name for name in names)}


def off_topic(group: Sequence[OutLink], topic: Sequence[str], donor_root: str = "") -> str | None:
    """Причина «тематика не совпадает» — или `None`: совпадает или неизвестно.

    `topic` — ключи донора. Хоть одна ссылка домена о том же — совпадает.
    Слов темы у ссылок меньше `MIN_LINK_WORDS` — неизвестно, и правило молчит.
    """
    donor = stems(" ".join(topic))
    if not donor:
        return None
    seen: set[str] = set()
    for link in group:
        words = link_words(link, donor_root)
        if any(_alike(_stem(word), key) for word in words for key in donor):
            return None
        seen |= words
    if len(seen) < MIN_LINK_WORDS:
        return None
    shown = ", ".join(f"«{key}»" for key in list(topic)[:2])
    return f"тематика не совпадает: у донора {shown}, у ссылок — {', '.join(sorted(seen)[:4])}"


async def donor_topic(session: AsyncSession, host: str) -> list[str]:
    """Ключи прогонов, по которым донор нашёлся. Пусто — донора в прогонах нет.

    Прогон — где домен стоит кандидатом (`run_candidates`) или в выдаче
    (`runs.candidates.found_by`): у прогонов до рассмотрения строк кандидатов нет.
    """
    domain = select(DomainModel.id).where(DomainModel.host == host).scalar_subquery()
    in_review = select(RunCandidateModel.run_id).where(RunCandidateModel.domain_id == domain)
    rows = await session.execute(
        select(RunModel.keywords, RunModel.candidates)
        .where(or_(RunModel.id.in_(in_review), RunModel.candidates["found_by"].has_key(host)))
        .order_by(RunModel.id)
    )
    keys: list[str] = []
    for keywords, candidates in rows.all():
        for key in _found_by(candidates, host) or keywords or ():
            if key not in keys:
                keys.append(str(key))
    return keys


def _found_by(candidates: dict[str, Any] | None, host: str) -> list[str]:
    found = ((candidates or {}).get("found_by") or {}).get(host)
    return list(found) if isinstance(found, list) else []
