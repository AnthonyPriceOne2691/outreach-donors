"""План полосы советов судьи — до единого решения: сколько пар, какими правилами, примеры и что это
тронет дальше. Отбор — тот же, что у применения (`judge_apply.said`): план и порция не расходятся.

**Примеры — жребием, а не верхом очереди.** Двадцать пять пар в порядке md5 домена, тем же, что у
случайной выборки (`sample.py`): по верху очереди по баллу любая полоса выглядит правдоподобно.
Слова судьи у примера — по фактам строки суда, как их видит человек под своим решением
(`judge_advice.py`).

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
from backend.features.crawl.judge_rules import RULES, advise
from backend.features.crawl.niche import NICHE
from backend.features.crawl.suppliers import supplier_hosts

Judgement = AdvertiserJudgementModel

#: Сколько пар показывает план: их читают глазами, чтобы поверить числу.
EXAMPLES = 25
#: Слова правила «общий ответ модели»: таблица не решила, совет — по ответу (`judge_rules.advise`).
VERDICT_WORDS = "правила не решили, модель говорит «пишем»"


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
    """Правило словами таблицы; общий ответ модели — своими; незнакомый ключ — как записан."""
    if rule == "verdict":
        return VERDICT_WORDS
    return next((one.words for one in RULES if one.key == rule), rule)


def _by_lot(rows: Sequence[Said]) -> list[Said]:
    """Пары в порядке md5 домена, затем номера строки — жребий (шапка модуля)."""

    def lot(one: Said) -> tuple[str, int]:
        digest = hashlib.md5(one.target_root.encode("utf-8"), usedforsecurity=False).hexdigest()
        return digest, one.candidate_id

    return sorted(rows, key=lot)


async def _examples(session: AsyncSession, rows: Sequence[Said]) -> list[Example]:
    """Примеры полосы со словами судьи; факты не той формы — слова правила из таблицы."""
    shown = _by_lot(rows)[:EXAMPLES]
    facts = await session.execute(
        select(Judgement.id, Judgement.facts).where(
            Judgement.id.in_([one.judgement_id for one in shown if one.judgement_id is not None])
        )
    )
    words: dict[int, str] = {}
    for number, body in facts.tuples():
        read = LinkFacts.from_json(body)
        if read is not None:
            words[number] = advise(read).words
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


async def _takes_off(session: AsyncSession, roots: set[str]) -> int:
    """У скольких доменов полосы «не пишем» снимется рекламодатель по найденной ссылке: он заведён,
    а подходящих ссылок у домена нет — условием снятия (`promote._rebuild`: без площадок
    поставщиков). Бизнес ниши не снимается (`promote._take_off`)."""
    hosts = await _advertised(session, roots, links_only=True)
    if not hosts:
        return 0
    suppliers = await supplier_hosts(session)
    stays: set[str] = set()
    for chunk in chunked(sorted(hosts)):
        chosen = await session.execute(
            select(CandidateModel.target_root, CandidateModel.donor_host).where(
                CandidateModel.target_root.in_(chunk), writes_to()
            )
        )
        stays.update(root for root, donor in chosen.tuples() if donor.lower() not in suppliers)
    return len(hosts - stays)


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
        takes_off=await _takes_off(session, roots) if band is Band.SKIP else 0,
        launch=await _launch(session, roots) if band is Band.WRITE else None,
    )
