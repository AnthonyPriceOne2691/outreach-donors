"""План полосы советов судьи — до единого решения: сколько пар, какими правилами, примеры и что это
тронет дальше. Отбор — тот же, что у применения (`judge_apply.said`): план и порция не расходятся.

**Примеры — жребием, а не верхом очереди.** Двадцать пять пар в порядке md5 домена, тем же, что у
случайной выборки (`sample.py`): по верху очереди по баллу любая полоса выглядит правдоподобно.
Слова судьи у примера — по фактам строки суда и признаку её донора (`judge_store.Catalogs`), как их
видит человек под своим решением (`judge_advice.py`).

**Правило не из таблицы — тоже словами** (`CHECK_WORDS`, 11.11.2026). В колонке правила строки суда
стоят и ключи, которых в таблице `RULES` нет: совет поставила сверка с ответом модели, признак
донора-каталога или второй круг. В разбивке полосы у них короткие слова отсюда, а не ключ и не
составная фраза сверки — она у каждой пары своя. Незнакомый ключ план не роняет: показан как пришёл.
У примера слова даёт `advise` — но только когда он ставит тот же совет тем же правилом, что стоит в
строке: совет, поставленный не таблицей (второй круг), её словами не объяснить, и пример тогда
говорит словами своего правила.

**«Не пишем»: кого снимет.** Спорная пара без решения рекламодателя не заводит, и её «не пишем»
обычно никого не снимает. Но у домена бывает рекламодатель, заведённый по ссылке, которую обход
больше не находит: подходящих ссылок у него нет, и «не пишем» по спорной его снимет вместе с
письмами (`promote.withdraw` — явное решение о домене). План называет таких числом до нажатия.

**«Пишем»: что запускается дальше — ничего само** (разведка 11.11.2026). Рекламодателем домен делает
«Перевести» (`promote.promote`: кнопка блока «К письму» или консоль), она же ставит поиск адресов;
письма собирает «Собрать» на экране писем, уходят они очередью. План поэтому говорит, сколько доменов
станут рекламодателями после перевода и у скольких адрес уже есть, а числа писем не называет: оно
зависит от найденных адресов и цены донора, и до перевода существующим кодом его не посчитать —
отбор писем идёт по строкам рекламодателей (`letters/recipients.py`), которых ещё нет.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.models.advertiser import AdvertiserJudgementModel, CandidateModel
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.crawl.advertiser_rules import writes_to
from backend.features.crawl.judge_apply import Band, Said, Standing, chunked, said, standing_of
from backend.features.crawl.judge_facts import LinkFacts
from backend.features.crawl.judge_rules import CATALOG_RULE, RULES, advise
from backend.features.crawl.judge_store import Catalogs, Cohort
from backend.features.crawl.niche import NICHE
from backend.features.crawl.suppliers import supplier_hosts

Judgement = AdvertiserJudgementModel

#: Сколько пар показывает план: их читают глазами, чтобы поверить числу.
EXAMPLES = 25
#: Правила, которых нет в таблице `RULES`, — короткими словами для разбивки полосы (шапка модуля):
#: общий ответ модели там, где таблица не решила (`judge_rules.advise`), спор у донора-каталога
#: (`judge_rules.CATALOG_RULE`) и исход второго круга — вторая модель (`second_round`).
CHECK_WORDS: dict[str, str] = {
    "verdict": "правила не решили, модель говорит «пишем»",
    CATALOG_RULE: "донор-каталог, его карточки не рекламодатели",
    "second_round": "решил второй круг: вторая модель",
}


@dataclass(frozen=True, slots=True)
class RuleCount:
    """Правило судьи в полосе: ключ, слова и сколько пар оно решило."""

    rule: str
    words: str
    pairs: int


@dataclass(frozen=True, slots=True)
class Example:
    """Пара полосы для показа: кто на кого ссылается, балл скоринга и слова судьи."""

    donor_host: str
    target_root: str
    points: int
    rule: str
    words: str


@dataclass(frozen=True, slots=True)
class Launch:
    """Что «пишем» даёт переводу (`promote.promote`). Стоп-листы не вычтены: их проверяет сам
    перевод и называет отсеянных числом, как у «Сколько переводить» (`promote.standing`)."""

    #: Разных доменов в полосе.
    domains: int
    #: Из них среди рекламодателей ещё нет — их заведёт перевод.
    fresh: int
    #: У скольких из новых адрес уже есть: остальным его найдёт поиск после перевода.
    with_address: int


@dataclass(frozen=True, slots=True)
class Plan:
    """План полосы: сколько, кого и что это тронет."""

    band: Band
    pairs: int
    donors: int
    rules: list[RuleCount]
    examples: list[Example]
    standing: Standing
    #: «Не пишем»: у стольких доменов снимется рекламодатель — других подходящих ссылок у них нет.
    takes_off: int = 0
    #: «Пишем»: что получит перевод.
    launch: Launch | None = None


def rule_words(rule: str) -> str:
    """Правило коротко: не из таблицы — своими словами (`CHECK_WORDS`), из таблицы — её словами;
    незнакомый ключ — как пришёл, а не падение: правила добавляют чаще, чем правят план."""
    if rule in CHECK_WORDS:
        return CHECK_WORDS[rule]
    return next((one.words for one in RULES if one.key == rule), rule)


def _by_lot(rows: Sequence[Said]) -> list[Said]:
    """Пары в порядке md5 домена, затем номера строки — жребий (шапка модуля)."""

    def lot(one: Said) -> tuple[str, int]:
        digest = hashlib.md5(one.target_root.encode("utf-8"), usedforsecurity=False).hexdigest()
        return digest, one.candidate_id

    return sorted(rows, key=lot)


async def _said_words(session: AsyncSession, shown: Sequence[Said]) -> dict[int, str]:
    """Слова судьи парам примеров — по фактам строки суда и признаку её донора: номер строки суда →
    слова. Только там, где таблица ставит тот же совет тем же правилом, что стоит в строке (шапка
    модуля); факты не той формы — без слов."""
    by_number = {one.judgement_id: one for one in shown if one.judgement_id is not None}
    read = await session.execute(
        select(
            Judgement.id,
            Judgement.donor_host,
            Judgement.prompt_version,
            Judgement.prompt_print,
            Judgement.examples_print,
            Judgement.model,
            Judgement.facts,
        ).where(Judgement.id.in_(sorted(by_number)))
    )
    catalogs = Catalogs(session)
    words: dict[int, str] = {}
    for number, donor, *print_, body in read.tuples():
        facts = LinkFacts.from_json(body)
        if facts is None:
            continue
        ruled = advise(facts, catalog_donor=await catalogs.at(Cohort(*print_), donor))
        pair = by_number[number]
        if (ruled.advice, ruled.rule) == (pair.advice, pair.rule):
            words[number] = ruled.words
    return words


async def _examples(session: AsyncSession, rows: Sequence[Said]) -> list[Example]:
    """Примеры полосы со словами судьи; слов по фактам нет — слова правила (`rule_words`)."""
    shown = _by_lot(rows)[:EXAMPLES]
    words = await _said_words(session, shown)
    return [
        Example(
            one.donor_host,
            one.target_root,
            one.points,
            one.rule,
            words.get(one.judgement_id or 0) or rule_words(one.rule),
        )
        for one in shown
    ]


async def _advertised(session: AsyncSession, roots: set[str], *, links_only: bool) -> set[str]:
    """Домены из `roots`, у которых уже есть строка рекламодателя; `links_only` — по найденной
    ссылке, без бизнесов ниши."""
    found: set[str] = set()
    for chunk in chunked(sorted(roots)):
        query = (
            select(DomainModel.host)
            .join(AdvertiserModel, AdvertiserModel.domain_id == DomainModel.id)
            .where(DomainModel.host.in_(chunk))
        )
        if links_only:
            query = query.where(AdvertiserModel.source != NICHE)
        found.update((await session.scalars(query)).all())
    return found


async def _launch(session: AsyncSession, roots: set[str]) -> Launch:
    """Что полоса «пишем» даст переводу: сколько доменов новых и у скольких адрес уже есть."""
    fresh = roots - await _advertised(session, roots, links_only=False)
    addressed = select(ContactModel.id).where(ContactModel.domain_id == DomainModel.id).exists()
    with_address = 0
    for chunk in chunked(sorted(fresh)):
        counted = await session.scalar(
            select(func.count())
            .select_from(DomainModel)
            .where(DomainModel.host.in_(chunk), addressed)
        )
        with_address += int(counted or 0)
    return Launch(len(roots), len(fresh), with_address)


async def taken_off(session: AsyncSession, roots: set[str]) -> set[str]:
    """Домены полосы «не пишем», у которых решение снимет рекламодателя по найденной ссылке: он
    заведён, а подходящих ссылок у домена нет — условием снятия (`promote._rebuild`: без площадок
    поставщиков). Бизнес ниши не снимается (`promote._take_off`). Кнопке это число для плана, а
    ночному шагу — пары, которые он оставляет человеку (`judge_night.py`)."""
    hosts = await _advertised(session, roots, links_only=True)
    if not hosts:
        return set()
    suppliers = await supplier_hosts(session)
    stays: set[str] = set()
    for chunk in chunked(sorted(hosts)):
        chosen = await session.execute(
            select(CandidateModel.target_root, CandidateModel.donor_host).where(
                CandidateModel.target_root.in_(chunk), writes_to()
            )
        )
        stays.update(root for root, donor in chosen.tuples() if donor.lower() not in suppliers)
    return hosts - stays


async def plan(session: AsyncSession, band: Band) -> Plan:
    """План полосы. Ни одного решения; пересчёт советов таблицей — как у `judge_apply.said`."""
    rows = await said(session)
    members = [one for one in rows if one.fits(band)]
    by_rule = Counter(one.rule for one in members)
    roots = {one.target_root for one in members}
    return Plan(
        band=band,
        pairs=len(members),
        donors=len({one.donor_host for one in members}),
        rules=[RuleCount(rule, rule_words(rule), pairs) for rule, pairs in by_rule.most_common()],
        examples=await _examples(session, members),
        standing=standing_of(rows),
        takes_off=len(await taken_off(session, roots)) if band is Band.SKIP else 0,
        launch=await _launch(session, roots) if band is Band.WRITE else None,
    )
