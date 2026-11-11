"""Совет судьи рекламодателей — таблицей правил по фактам, сверенной с общим ответом модели.

Модель отвечает на простые вопросы «да/нет» с цитатами (`judge_facts.py`), а совет «пишем», «не
пишем» или «не понять» ставит код: первое правило таблицы, которое подошло. Так правка политики —
правка этой таблицы и `RULES_VERSION`, а не подсказки: пересчёт идёт по сохранённым фактам без модели
(`judge_store.relabel`), и токены за уже известное не платятся (урок судьи продаж, #440).

«Пишем» — когда по странице видно, что домен платит за размещения: он бизнес (продаёт своё или
отправляет к другим) или неизвестен, и ссылка его продвигает (анкор продаёт, призыв, выбор, подборка,
«не по теме», «как пример»), стоит в рекламном месте, под раскрытием рекламы или в статье о нём.
«Не пишем» — переадресация, некоммерческий сайт, навигация, карточка каталога (бесплатная или в
подборке «лучшие …»), источник факта, издание без продвижения, ссылка без единого признака рекламы.
Остальное — «не понять»: человек. Таблица откалибрована на стенде (размеченные пары, подбор и
проверка — на разных донорах, свежая проверка на парах, которых правила не видели): каждое правило
держится на многих донорах.

**Общий ответ модели сверяется с таблицей** (`judge_facts.py`, стенд 05.11.2026): таблица не решила, а
модель говорит «пишем» — совет по ответу; решили по-разному — «не понять», решает человек; совпали или
модель сама не решила — совет таблицы. Ни таблица, ни ответ поодиночке так не держатся на парах, которых
не видели: таблица теряет рекламодателей и мало что отсеивает, ответ чаще ошибается в обе стороны. Строка
суда без ответа (суд до него) судится одной таблицей.

**«Не пишем» ставит только таблица** (суд всей очереди, 06.11.2026). Там, где она не решила, ответ «не
пишем» на живой модели верен через раз: сайт домена похож на издание, а ссылка его продаёт. Такая строка
остаётся человеку: так теряется меньше рекламодателей, а цена — несколько лишних строк в очереди.
Три правила ответ вообще не отменяет (`FIRM`).

**Таблица v6: два решения владельца 11.11.2026.** Обе правки — про строки, которые таблица оставляла
человеку: владелец решил их разом.

- **Подборке «лучшие …» не пишем.** Карточка каталога в подборке — платное место или выбор редакции, и
  по тексту этого не решить: таблица оставляла её человеку (`ranked_directory`, «не понять»). На
  размеченных парах «пишем» модели в подборках чаще было лишним, чем верным. Теперь такая карточка —
  «не пишем»; правило по-прежнему твёрдое, ответ модели его не меняет.
- **У донора-каталога карточки — не рекламодатели.** Страницу карточки модель нередко читает статьёй
  о домене, и таблица видит «пишем» там, где рекламы нет. В v6 «не пишем» получал только спор «таблица —
  пишем, модель — не пишем», согласное «пишем» оставалось — и на размеченных парах оказалось лишним
  куда чаще, чем верным.

**Таблица v7 доводит решение о каталоге до конца** (11.11.2026): у донора-каталога «пишем» — только
явной рекламе на странице. Совет «пишем» остаётся, если таблица поставила его правилом явной рекламы
(`EXPLICIT_AD`: раскрытие рекламы, рекламное место, пометка «пресс-релиз») и общий ответ модели не «не
пишем». Всё остальное, что у обычного издания вышло бы «пишем» или «не понять», — «не пишем» правилом
каталога (`catalog_card`), и слова называют, что сказали таблица и модель. «Не пишем» самой таблицы
остаётся её правилом и её словами: они точнее, и «пишем» модели его у каталога не оспаривает. У
обычного издания (и у донора без признака) совет тот же, что в v6.

**Пометка `sponsored` у ссылки каталога — не явная реклама.** Каталог ставит её всем исходящим ссылкам
разом: это свойство донора, а не знак того, что за карточку заплатили. Правила явной рекламы на неё и
не смотрят — как и у обычного издания, где пометка сама «пишем» не даёт.

**Каталог — признак донора, а не пары, и считается кодом**: ни списка доменов, ни слов ниши. Донор —
каталог, если в когорте фактов у него рассужено достаточно пар и большая их доля — карточки каталога
(`is_catalog`). Знает это только база: признак считают по сохранённым судам донора
(`judge_store.catalog_donors`) и передают в `advise`; без него совет — как у обычного издания.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from backend.features.crawl.judge_facts import LinkFacts

#: Версия таблицы. Меняется при КАЖДОЙ правке правил: совет строки суда помнит её, и пересчёт
#: (`judge_store.relabel`) ставит совет заново по фактам строки.
RULES_VERSION = "advertiser-rules-v7"


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
        Advice.SKIP,
        "карточка каталога в подборке «лучшие\u00a0…»",
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

#: Правила, которые общий ответ модели не отменяет. Карточка каталога: ответ видит бизнес за карточкой,
#: а платит ли он за неё, из текста не видно — бесплатной и карточке в подборке «лучшие …» не пишем
#: при любом ответе (подборка — решение владельца 11.11.2026). Недоказанное: таблица сама сказала,
#: что по тексту не решить, и ответ по тому же тексту этого не решает.
FIRM = frozenset({"free_directory", "ranked_directory", "unproven"})

#: Правила сверки с ответом модели — ключи строки суда, которых в таблице нет. Спор: таблица и модель
#: решили по-разному. Карточка донора-каталога: всё, что у каталога не явная реклама и не «не пишем»
#: самой таблицы; слова человеку — эти и что сказали таблица и модель (`_catalog_words`).
DISAGREE = "disagree"
CATALOG_RULE = "catalog_card"
CATALOG_WORDS = "донор-каталог, его карточки не рекламодатели"
#: Правила явной рекламы на странице: раскрытие рекламы или рекламное место — и пометка «пресс-релиз».
#: Только с ними у донора-каталога остаётся «пишем»: реклама названа самой страницей, а не прочитана
#: из карточки. Пометки `sponsored` ссылки здесь нет: каталог ставит её всем исходящим (шапка).
EXPLICIT_AD = frozenset({"paid_or_ad", "press_release"})

#: Факт «ссылка — карточка каталога» (`LinkFacts.directory_entry`): по его доле в судах донора видно,
#: что донор — каталог.
CARD_FACT = "directory_entry"
#: Донор — каталог: в когорте у него рассужено не меньше стольких пар, и не меньше такой их доли —
#: карточки каталога. Замер на судах очереди проверки (11.11.2026) и на размеченных парах стенда:
#: у доноров от пятнадцати пар доля карточек либо нулевая (обычные издания), либо выше четырёх пятых
#: (каталоги) — середины нет, и порог стоит в этом разрыве с запасом в обе стороны. Двадцать пар —
#: чтобы доля не была случайностью: у донора с несколькими парами одна карточка сдвигает её на
#: десятки процентов, и судить по ней рано. Числа — часть политики совета: их правка — новая
#: `RULES_VERSION`.
CATALOG_MIN_PAIRS = 20
CATALOG_SHARE = 0.7


def is_catalog(pairs: int, cards: int) -> bool:
    """Донор — каталог по своим же судам: пар рассужено достаточно, и карточек каталога среди них —
    не меньше доли (`pairs` — пары донора в когорте, `cards` — из них с фактом `CARD_FACT`)."""
    return pairs >= CATALOG_MIN_PAIRS and cards / pairs >= CATALOG_SHARE


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


def _undecided(ruled: Ruled, said: Advice) -> Ruled:
    """Таблица не решила: «пишем» модели — совет; её «не пишем» — словами, а решает человек."""
    if said is Advice.SKIP:
        return Ruled(Advice.UNCLEAR, ruled.rule, f"{ruled.words}; модель: {ADVICE_WORDS[said]}")
    return Ruled(
        said, "verdict", f"модель: {ADVICE_WORDS[said]}; правила не решили ({ruled.words})"
    )


def _disputed(ruled: Ruled, said: Advice) -> Ruled:
    """Таблица и модель решили по-разному — решает человек, и он видит слова обеих сторон."""
    table = f"правила: {ADVICE_WORDS[ruled.advice]} ({ruled.words})"
    return Ruled(Advice.UNCLEAR, DISAGREE, f"{table}; модель: {ADVICE_WORDS[said]}")


def _catalog_words(ruled: Ruled, verdict: str) -> str:
    """Слова правила каталога: почему «не пишем» — и что сказали таблица и модель. У строки суда без
    общего ответа (суд до него) модель не названа."""
    said = f", модель: {ADVICE_WORDS[Advice(verdict)]}" if verdict in Advice else ""
    return f"{CATALOG_WORDS}; правила: {ADVICE_WORDS[ruled.advice]}{said}"


def _at_catalog(ruled: Ruled, verdict: str) -> Ruled:
    """Совет у донора-каталога (шапка модуля): «не пишем» таблицы — её правилом; «пишем» — только
    явной рекламе на странице, и если модель не сказала «не пишем»; всё остальное — «не пишем»
    правилом каталога."""
    if ruled.advice is Advice.SKIP:
        return ruled
    if ruled.rule in EXPLICIT_AD and verdict != Advice.SKIP:
        return ruled
    return Ruled(Advice.SKIP, CATALOG_RULE, _catalog_words(ruled, verdict))


def advise(
    facts: LinkFacts, rules: tuple[Rule, ...] = RULES, *, catalog_donor: bool = False
) -> Ruled:
    """Совет таблицы, сверенный с общим ответом модели (шапка модуля). `catalog_donor` — донор пары —
    каталог по своим же судам (`is_catalog`); без признака совет — как у обычного издания."""
    ruled = _by_table(facts, rules)
    if catalog_donor:
        return _at_catalog(ruled, facts.verdict)
    if facts.verdict not in (Advice.WRITE, Advice.SKIP) or ruled.rule in FIRM:
        return ruled
    said = Advice(facts.verdict)
    if ruled.advice is said:
        return ruled
    if ruled.advice is Advice.UNCLEAR:
        return _undecided(ruled, said)
    return _disputed(ruled, said)
