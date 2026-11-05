"""Совет судьи рекламодателей — таблицей правил по фактам, сверенной с общим ответом модели.

Модель отвечает на простые вопросы «да/нет» с цитатами (`judge_facts.py`), а совет «пишем», «не
пишем» или «не понять» ставит код: первое правило таблицы, которое подошло. Так правка политики —
правка этой таблицы и `RULES_VERSION`, а не подсказки: пересчёт идёт по сохранённым фактам без модели
(`judge_store.relabel`), и токены за уже известное не платятся (урок судьи продаж, #440).

«Пишем» — когда по странице видно, что домен платит за размещения: он бизнес (продаёт своё или
отправляет к другим) или неизвестен, и ссылка его продвигает (анкор продаёт, призыв, выбор, подборка,
«не по теме», «как пример»), стоит в рекламном месте, под раскрытием рекламы или в статье о нём.
«Не пишем» — переадресация, некоммерческий сайт, навигация, бесплатная карточка каталога, источник
факта, издание без продвижения, ссылка без единого признака рекламы. Остальное — «не понять»:
человек. Таблица откалибрована на стенде (размеченные пары, подбор и проверка — на разных донорах,
свежая проверка на парах, которых правила не видели): каждое правило держится на многих донорах.

**Общий ответ модели сверяется с таблицей** (`judge_facts.py`, стенд 05.11.2026): таблица не решила —
совет по ответу модели; решили по-разному — «не понять», решает человек; совпали или модель сама не
решила — совет таблицы. Ни таблица, ни ответ поодиночке так не держатся на парах, которых не видели:
таблица теряет рекламодателей и мало что отсеивает, ответ чаще ошибается в обе стороны. Строка суда
без ответа (суд до него) судится одной таблицей.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from backend.features.crawl.judge_facts import LinkFacts

#: Версия таблицы. Меняется при КАЖДОЙ правке правил: совет строки суда помнит её, и пересчёт
#: (`judge_store.relabel`) ставит совет заново по фактам строки.
RULES_VERSION = "advertiser-rules-v4"


class Advice(StrEnum):
    """Совет судьи: писать ли домену как рекламодателю."""

    WRITE = "write"
    SKIP = "skip"
    UNCLEAR = "unclear"


#: Совет словами — как на экране решения человека.
ADVICE_WORDS = {Advice.WRITE: "пишем", Advice.SKIP: "не пишем", Advice.UNCLEAR: "не понять"}


@dataclass(frozen=True, slots=True)
class Rule:
    """Строка таблицы: ключ, совет, слова человеку и условие по фактам."""

    key: str
    advice: Advice
    words: str
    applies: Callable[[LinkFacts], bool]


def _typed(f: LinkFacts) -> bool:
    """Главная доказана и показывает, что за сайт; заглушка или одна строка — сайт неизвестен."""
    kinds = (f.target_sells, f.target_refers, f.target_affiliate_signs, f.target_noncommercial,
             f.target_media, f.target_redirect)  # fmt: skip
    return f.target_proven and any(kinds)


def _business(f: LinkFacts) -> bool:
    """Продаёт своё или отправляет к другим (приметы партнёрского сайта — тоже), не переадресация."""
    sells = f.target_sells or f.target_refers or f.target_affiliate_signs
    return sells and not f.target_redirect and not f.target_noncommercial


def _business_or_unknown(f: LinkFacts) -> bool:
    return _business(f) or not _typed(f)


def _anchor_sells(f: LinkFacts) -> bool:
    return f.money_anchor or f.keyword_anchor


def _pushes(f: LinkFacts) -> bool:
    return f.calls_to_buy or f.recommends or f.offer_list


def _placed(f: LinkFacts) -> bool:
    return f.off_topic or f.named_as_example


def _promotes(f: LinkFacts) -> bool:
    return _anchor_sells(f) or _pushes(f) or _placed(f)


def _listed(f: LinkFacts) -> bool:
    """Карточка каталога; с денежными словами — уже предложение на витрине, не справочник."""
    return f.directory_entry and not f.money_anchor


def _unproven(f: LinkFacts) -> bool:
    return not f.link_proven and not _typed(f) and not f.disclosure


def _press_release(f: LinkFacts) -> bool:
    return (
        f.press_release
        and _business_or_unknown(f)
        and not f.directory_entry
        and not f.site_navigation
    )


def _navigation(f: LinkFacts) -> bool:
    return f.site_navigation and not f.ad_slot and not (_anchor_sells(f) and f.disclosure)


def _ranked_directory(f: LinkFacts) -> bool:
    return _listed(f) and (f.recommends or f.offer_list) and not f.disclosure and not f.ad_slot


def _free_directory(f: LinkFacts) -> bool:
    return _listed(f) and not f.disclosure and not f.sponsored and not f.ad_slot


def _citation(f: LinkFacts) -> bool:
    return f.source_of_fact and not f.disclosure and not _promotes(f)


def _paid_or_ad(f: LinkFacts) -> bool:
    return _business_or_unknown(f) and (bool(f.disclosure) or f.ad_slot)


def _promoted(f: LinkFacts) -> bool:
    return _business(f) and _promotes(f)


def _selling_anchor(f: LinkFacts) -> bool:
    in_text = f.link_proven and not f.site_navigation and not f.ad_slot
    return not _typed(f) and in_text and _anchor_sells(f)


def _about_business(f: LinkFacts) -> bool:
    return (
        _business_or_unknown(f)
        and f.article_about_target
        and not f.directory_entry
        and not f.site_navigation
    )


def _media(f: LinkFacts) -> bool:
    quiet = not f.disclosure and not f.ad_slot and not _anchor_sells(f) and not _pushes(f)
    return f.target_media and not _business(f) and quiet


def _no_signs(f: LinkFacts) -> bool:
    return f.link_proven and not (_promotes(f) or f.ad_slot or f.disclosure or f.sponsored)


def _brand_mention(f: LinkFacts) -> bool:
    return _no_signs(f) and _business(f) and f.brand_anchor


#: Таблица правил, первое совпадение. Последняя строка подходит всегда.
RULES: tuple[Rule, ...] = (
    Rule(
        "same_owner",
        Advice.SKIP,
        "сайт домена — того же владельца, что донор",
        lambda f: f.same_owner,
    ),
    Rule("unproven", Advice.UNCLEAR, "факты не доказаны цитатами", _unproven),
    Rule(
        "redirect",
        Advice.SKIP,
        "домен — переадресация, рекламный сервер или партнёрская сеть",
        lambda f: f.target_redirect,
    ),
    Rule(
        "noncommercial",
        Advice.SKIP,
        "сайт домена — госорган, вуз, фонд, НКО или личная страница",
        lambda f: f.target_noncommercial,
    ),
    Rule("press_release", Advice.WRITE, "статья помечена как пресс-релиз домена", _press_release),
    Rule(
        "navigation", Advice.SKIP, "ссылка — в меню, шапке, подвале или виджете сайта", _navigation
    ),
    Rule(
        "ranked_directory",
        Advice.UNCLEAR,
        "карточка в подборке каталога — платное место или выбор редакции, по тексту не решить",
        _ranked_directory,
    ),
    Rule("free_directory", Advice.SKIP, "бесплатная карточка каталога", _free_directory),
    Rule(
        "citation", Advice.SKIP, "ссылка — источник факта, без продвижения и раскрытия", _citation
    ),
    Rule(
        "paid_or_ad",
        Advice.WRITE,
        "раскрытие рекламы или рекламное место, домен — бизнес",
        _paid_or_ad,
    ),
    Rule("promoted", Advice.WRITE, "домен — бизнес, ссылка его продвигает", _promoted),
    Rule(
        "selling_anchor",
        Advice.WRITE,
        "анкор продаёт, сайт домена не открылся или пуст",
        _selling_anchor,
    ),
    Rule(
        "about_business", Advice.WRITE, "статья — о самом домене, домен — бизнес", _about_business
    ),
    Rule("media", Advice.SKIP, "сайт домена — издание, ссылка его не продвигает", _media),
    Rule(
        "brand_mention",
        Advice.UNCLEAR,
        "только имя бизнеса без признаков рекламы — так ставят и купленные, и редакционные ссылки",
        _brand_mention,
    ),
    Rule("no_signs", Advice.SKIP, "ни одного признака рекламы", _no_signs),
    Rule("rest", Advice.UNCLEAR, "по фактам не решить — решает человек", lambda _facts: True),
)


@dataclass(frozen=True, slots=True)
class Ruled:
    """Совет по фактам: какое правило его поставило и его слова."""

    advice: Advice
    rule: str
    words: str


def _by_table(facts: LinkFacts, rules: tuple[Rule, ...]) -> Ruled:
    """Первое правило таблицы, которое подошло к фактам."""
    for rule in rules:
        if rule.applies(facts):
            return Ruled(rule.advice, rule.key, rule.words)
    raise AssertionError("последнее правило таблицы подходит всегда")  # pragma: no cover


def advise(facts: LinkFacts, rules: tuple[Rule, ...] = RULES) -> Ruled:
    """Совет таблицы, сверенный с общим ответом модели (шапка модуля)."""
    ruled = _by_table(facts, rules)
    if facts.verdict not in (Advice.WRITE, Advice.SKIP):
        return ruled
    said = Advice(facts.verdict)
    if ruled.advice is said:
        return ruled
    if ruled.advice is Advice.UNCLEAR:
        return Ruled(
            said, "verdict", f"модель: {ADVICE_WORDS[said]}; правила не решили ({ruled.words})"
        )
    table = f"правила: {ADVICE_WORDS[ruled.advice]} ({ruled.words})"
    return Ruled(Advice.UNCLEAR, "disagree", f"{table}; модель: {ADVICE_WORDS[said]}")
