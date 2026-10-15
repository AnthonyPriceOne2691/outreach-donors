"""Компания — в одной гипотезе (решение владельца 14.10): одной компании пишет одна гипотеза.

Тест прогона сбора 14.10: одной компании могли уйти письма из двух гипотез — разные адреса
одного домена: лид из файла в одной гипотезе и адрес, найденный сбором, в другой. Очистка
ловила только точный адрес (`cleaning._duplicate`), а сбор мог заплатить Hunter за домен,
у которого лид уже есть.

**Домен компании — `sales_leads.domain_id`**: сайт компании, а без него — домен рабочего адреса
(`intake._company`); ключ — `intake.host_key`, поэтому регистр и `www.` второй компании не
делают. Лид сбора — домен сайта из выдачи, лид из «пишите другому» — домен исходного диалога.

**Держатель компании** (`holders`) — её «готовый» лид, а из нескольких — заведённый раньше;
пока «готовых» нет — раньше всех заведённый живой. «Готовый» — тот, с кем работа уже идёт или
может пойти (цепочка, ответ, передача телемаркетологу): он держит компанию и против
раньше заведённого — так ранний лид, вернувшийся из стоп-листа, уступает компанию той
гипотезе, что успела её взять. Живой — не отклонённый (`live`): в том числе с законченной
цепочкой, отписавшийся и переданный — компании уже писали.

**Лида, чью компанию держит лид другой гипотезы, очистка решает по держателю** (`split`) —
после бесплатных правил и проверки почты домена, до платной проверки адреса:
- держатель «готов» — отказ кодом дубля и словами «компания уже в работе гипотезы «…»»
  (`Holder.words`);
- держатель ещё не проверен («новый», без вердикта или после отказа сервиса) — лид **ждёт**:
  статус и прежний вердикт целы, Hunter за него не платится, слова называют держателя и
  очистку, которая его решит (`Holder.waits`). Отказ сразу оставил бы компанию без живых
  лидов, когда проверка отклонит самого держателя (ревью соседней сессии, правка (а)).

**Ждущий не висит** (решение координатора 15.10: плановой очистки нет). Проход очистки, решив
лида — «готов» с новым вердиктом или отклонён, — будит ждущих на его домене, любой гипотезы
(`after`): их узнают по словам ожидания (`hold_words.WAITS`). Разбуженный проходит очистку
следующим кругом того же прохода, всеми правилами: держатель «готов» — отказ, бесплатно;
держатель отклонён — ранний из ждущих держит компанию сам и идёт на платную проверку (в очистку
его уже отдавали, ждал он только держателя), остальные ждут его. Новый лид без слов ожидания
пробуждение не трогает: в очистку его ещё не отдавали, и Hunter за него не платится.

**Компания освободилась — ранний отклонённый из-за держателя возвращается** (решение владельца
15.10, «вернуть раннего в работу»; `freed`). Держателя отклонили уже после того, как из-за него
отклонили лидов других гипотез, и на домене не осталось живого лида: самый ранний лид,
отклонённый именно отклонённым держателем (код дубля и слова `hold_words.HELD` с его номером), —
снова `new`, без прежнего вердикта: очистка проверит его целиком, и новый `unknown` не сойдёт за
второй. Остальные остаются отклонёнными; за раз компания возвращает одного. Ищут свободные
компании оба пути, где лид продаж становится отклонённым: очистка — каждым кругом на доменах, где
круг отклонил лида, и в начале прохода по всей базе (держателя могли отклонить и не очисткой:
руками, запросом); вернувшегося она проверяет тем же проходом, а отклонит проверка — следующий
круг или проход вернёт следующего, отклонённого тем же держателем. И стоп-лист продаж
(`stoplist_leads.close`): там вернувшийся ждёт очистки.

**Очистка гипотезы платит только за свою область** (решение координатора 15.10; `mine`): у
`clean(hypothesis_id=H)` — лиды H, у `clean()` — все, у `clean_leads` — гипотезы названных лидов.
Разбуженные и вернувшиеся лиды своей области проходят очистку целиком, с платной проверкой, тем же
проходом. Лидам чужих гипотез — только бесплатные исходы тем же кругом: держатель «готов» — отказ;
компания для лида свободна — лид `new` словами «компания свободна: …» (`hold_words.FREED`),
вернувшийся — словами возврата; оба кончаются «адрес проверит очистка гипотезы «…»» — своей.
Поиск свободных компаний в начале `clean` — по всей базе: возврат бесплатный. Окно перед очисткой
(«до N запросов») считает и своих лидов, которых этот поиск вернёт (`returnable`), — тем же
помощником, что у прохода.

Лид, отклонённый той же партией очистки, компанию не держит — в базе он ещё живой (`gone`).
Внутри одной гипотезы несколько людей компании — можно, как прежде: держит гипотеза, а не
человек.

**Бесплатная почта — по адресу, не по домену** (`FREE`): `gmail.com` — не компания, и лидов на
нём судит только дубль точного адреса; ни держателя, ни возврата у такого домена нет. Список —
общий `FREE_MAILBOX_DOMAINS`, тот же, что у ответа коллеге и лида из автоответа
(`mail.of_company`).

**Сбор не ищет адрес и не заводит лида домену с живым лидом любой гипотезы** (`held`): очередь
поиска адреса (`segment_queue`) такой домен не берёт — ни бесплатными ступенями, ни платной, — и
лид из готового адреса в `contacts` не заводится (`segment_leads`): он оплатил бы проверку
адреса. Отчёт прогона называет итог сайта словами (`held_in_run`, `Holder.site`) — тем же
условием.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass, field

from sqlalchemy import ColumnElement, Exists, Select, case, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, QueryableAttribute, aliased

from backend.features.contacts.known_addresses import FREE_MAILBOX_DOMAINS
from backend.features.core.models.domain import DomainModel
from backend.features.sales import clean_write
from backend.features.sales.hold_words import BACK, FREED, HELD, WAITS
from backend.features.sales.models import (
    LeadStatus,
    RejectionReason,
    SalesHypothesisModel,
    SalesLeadModel,
)
from backend.features.sales.segment_models import Lane, SalesSegmentCandidateModel

logger = logging.getLogger(__name__)

Candidate = SalesSegmentCandidateModel
#: Бесплатная почта — значениями для `IN`: порядок постоянный, запрос один и тот же.
FREE = sorted(FREE_MAILBOX_DOMAINS)
#: Доменов в одном `IN`: параметров у Postgres не больше 32 767 на запрос.
CHUNK = 1000
#: Своя область прохода очистки — номера гипотез; `None` — все гипотезы.
Scope = Collection[int] | None


def mine(lead: SalesLeadModel, scope: Scope) -> bool:
    """Лид — из своей области прохода: за его проверку проход платит."""
    return scope is None or lead.hypothesis_id in scope


def only(leads: Sequence[SalesLeadModel], scope: Scope) -> list[SalesLeadModel]:
    return [lead for lead in leads if mine(lead, scope)]


@dataclass(frozen=True, slots=True)
class Holder:
    """Лид, который держит компанию (`holders`): «готовый», а без них — раньше всех заведённый
    живой лид на её домене."""

    lead_id: int
    hypothesis_id: int
    #: Имя гипотезы — по нему человек её узнаёт.
    hypothesis: str
    host: str
    #: «Готов» — адрес проверен, работа с ним идёт или пойдёт: такой держатель отклоняет лида
    #: другой гипотезы; ещё не проверенный заставляет его ждать (`split`).
    ready: bool

    @property
    def waits(self) -> str:
        """Почему лид другой гипотезы ждёт и что его решит — словами очистки."""
        return WAITS.say(lead=self.lead_id, hypothesis=self.hypothesis)

    @property
    def words(self) -> str:
        """Почему отклонён лид другой гипотезы — словами очистки."""
        return HELD.say(lead=self.lead_id, hypothesis=self.hypothesis, host=self.host)

    @property
    def site(self) -> str:
        """Итог принятого сайта сбора, которому сбор не ищет адрес и не заводит лида, — словами
        отчёта прогона."""
        return (
            f"компания уже в работе гипотезы «{self.hypothesis}» — адрес не ищем, лида не заводим"
        )


def live(status: QueryableAttribute[LeadStatus] = SalesLeadModel.status) -> ColumnElement[bool]:
    """Лид держит компанию, пока не отклонён: законченная цепочка, отписка и передача
    телемаркетологу не отпускают её — компании уже писали. `status` — колонка псевдонима."""
    return status != LeadStatus.REJECTED


def held(domain_id: ColumnElement[int] | InstrumentedAttribute[int]) -> ColumnElement[bool]:
    """У домена есть живой лид продаж любой гипотезы, и домен — не бесплатная почта: условием
    SQL. Одно у очереди поиска адреса сбора, у итога кандидата в отчёте прогона (`held_in_run`)
    и у возврата (`freed`: компания свободна — не `held`). Таблицы — псевдонимами: внешний запрос
    со своими `domains` и `sales_leads` их не подменит."""
    lead, domain = aliased(SalesLeadModel), aliased(DomainModel)
    holds: Exists = select(lead.id).where(lead.domain_id == domain_id, live(lead.status)).exists()
    free: Exists = select(domain.id).where(domain.id == domain_id, domain.host.in_(FREE)).exists()
    return holds & ~free


def _chunks(values: Collection[int]) -> Iterator[list[int]]:
    ordered = sorted(set(values))
    for start in range(0, len(ordered), CHUNK):
        yield ordered[start : start + CHUNK]


def _ready_first() -> ColumnElement[int]:
    """Порядок держателей: «готовые» — первыми."""
    return case((SalesLeadModel.status == LeadStatus.READY, 0), else_=1)


async def holders(
    session: AsyncSession, domain_ids: Collection[int], *, gone: Collection[int] = ()
) -> dict[int, Holder]:
    """Домен → лид, который держит компанию: «готовый» (из нескольких — заведённый раньше), а
    без них — раньше всех заведённый живой. Держатель у домена один — два «готовых» лида разных
    гипотез на перепроверке не отклонят друг друга. Домены бесплатной почты компанию не дают —
    их здесь нет. `gone` — лиды, отклонённые проходом, но ещё не записанные: в базе они живые, а
    компанию уже не держат."""
    found: dict[int, Holder] = {}
    for chunk in _chunks(domain_ids):
        query = (
            select(
                SalesLeadModel.domain_id,
                SalesLeadModel.id,
                SalesLeadModel.hypothesis_id,
                SalesHypothesisModel.name,
                DomainModel.host,
                SalesLeadModel.status,
            )
            .distinct(SalesLeadModel.domain_id)
            .join(DomainModel, DomainModel.id == SalesLeadModel.domain_id)
            .join(SalesHypothesisModel, SalesHypothesisModel.id == SalesLeadModel.hypothesis_id)
            .where(SalesLeadModel.domain_id.in_(chunk), live(), DomainModel.host.not_in(FREE))
            .order_by(SalesLeadModel.domain_id, _ready_first(), SalesLeadModel.id)
        )
        if gone:
            query = query.where(SalesLeadModel.id.not_in(sorted(gone)))
        for domain_id, lead_id, hypothesis_id, name, host, status in (
            await session.execute(query)
        ).tuples():
            ready = status == LeadStatus.READY
            found[domain_id] = Holder(lead_id, hypothesis_id, name, host, ready)
    return found


@dataclass(slots=True)
class Split:
    """Шаг очистки партии по правилу компании (`split`): кого отклонить, кому ждать, кого
    проверять дальше."""

    #: Компанию держит «готовый» лид другой гипотезы: (лид, слова отказа).
    held: list[tuple[int, str]] = field(default_factory=list)
    #: Держит ещё не проверенный лид другой гипотезы — ждать: (лид, его статус, слова). Вердикт
    #: ждущего не трогается.
    waiting: list[tuple[int, LeadStatus, str]] = field(default_factory=list)
    #: Компания свободна, своя или держит сам лид — к проверке адреса.
    rest: list[SalesLeadModel] = field(default_factory=list)
    #: То же, но лид чужой гипотезы — проверять его проход не платит (`mine`).
    free: list[SalesLeadModel] = field(default_factory=list)
    #: Они же — словами для очистки их гипотезы: (лид, его статус, слова `FREED`).
    handed: list[tuple[int, LeadStatus, str]] = field(default_factory=list)

    def place(self, lead: SalesLeadModel, holder: Holder | None, own: bool) -> None:
        """Лид — по держателю его компании: «готовый» другой гипотезы — отказ, ещё не
        проверенный другой гипотезы — ждать, иначе — к проверке адреса, если лид `own` — из
        своей области прохода, а чужой — к очистке его гипотезы."""
        if holder is None or holder.hypothesis_id == lead.hypothesis_id:
            (self.rest if own else self.free).append(lead)
        elif holder.ready:
            self.held.append((lead.id, holder.words))
        else:
            self.waiting.append((lead.id, lead.status, holder.waits))


async def split(
    session: AsyncSession,
    batch: Sequence[SalesLeadModel],
    alive: Sequence[SalesLeadModel],
    scope: Scope = None,
) -> Split:
    """Живые лиды партии (`alive`) — по держателю их компании (`holders`, `Split.place`). Лиды
    партии вне `alive` она уже отклонила: компанию они не держат, хоть в базе ещё и живые.
    Лиды вне своей области прохода (`scope`) платной проверки не получают (`_handed`)."""
    gone = {lead.id for lead in batch} - {lead.id for lead in alive}
    holding = await holders(session, {lead.domain_id for lead in alive}, gone=gone)
    found = Split()
    for lead in alive:
        found.place(lead, holding.get(lead.domain_id), mine(lead, scope))
    found.handed = await _handed(session, found.free)
    return found


async def _handed(
    session: AsyncSession, leads: Sequence[SalesLeadModel]
) -> list[tuple[int, LeadStatus, str]]:
    """Разбуженные лиды чужих гипотез, чья компания для них свободна: держатель, которого они
    ждали (номер — из слов ожидания, `WAITS.lead`), отклонён. Лид — `new` словами `FREED`: его
    проверит очистка его гипотезы. Слов ожидания нет — лид остаётся, как был."""
    if not leads:
        return []
    awaited = {lead.id: WAITS.lead(lead.cleaning_note) for lead in leads}
    named = sorted({*awaited, *(number for number in awaited.values() if number is not None)})
    found = await session.execute(
        select(SalesLeadModel.id, SalesHypothesisModel.name)
        .join(SalesHypothesisModel, SalesHypothesisModel.id == SalesLeadModel.hypothesis_id)
        .where(SalesLeadModel.id.in_(named))
    )
    names = dict(found.tuples().all())
    return [
        (lead.id, lead.status, FREED.say(lead=number, hypothesis=names[number], own=names[lead.id]))
        for lead in leads
        if (number := awaited[lead.id]) in names
    ]


async def after(
    session: AsyncSession, decided: Collection[int], rejected: Collection[int], scope: Scope
) -> list[SalesLeadModel]:
    """Кого проход очистки берёт следующим кругом, по порядку заведения: ждущих на доменах
    `decided` (там круг решил лида) любой гипотезы — чужим достанутся только бесплатные исходы
    (`split`) — и вернувшихся на доменах `rejected` (там круг отклонил лида, `freed`) из своей
    области: чужих возврат уже отдал очистке их гипотезы. Домены у ждущих и вернувшихся разные:
    ждущий — живой лид, а у свободной компании живых нет."""
    found = await _woken(session, decided) + only(await freed(session, rejected), scope)
    return sorted(found, key=lambda lead: lead.id)


async def _woken(session: AsyncSession, domain_ids: Collection[int]) -> list[SalesLeadModel]:
    """Ждущие на доменах `domain_ids` — лиды `new` любой гипотезы со словами ожидания (`WAITS`).
    Нового лида без них очистка ещё не видела: его возьмёт очистка его гипотезы."""
    found: list[SalesLeadModel] = []
    for chunk in _chunks(domain_ids):
        rows = await session.scalars(
            select(SalesLeadModel).where(
                SalesLeadModel.domain_id.in_(chunk),
                SalesLeadModel.status == LeadStatus.NEW,
                WAITS.match(SalesLeadModel.cleaning_note),
            )
        )
        found += rows.all()
    return found


async def freed(
    session: AsyncSession, domain_ids: Collection[int] | None = None
) -> list[SalesLeadModel]:
    """Свободные компании на доменах `domain_ids` (`None` — по всей базе): живого лида на домене
    нет (не `held`), а лид, отклонённый из-за держателя (код дубля, слова `HELD`), назвал
    держателем отклонённого лида того же домена. Самый ранний такой лид компании — снова `new`,
    без вердикта и словами `BACK` с номером держателя (`_returned`); остальные остаются
    отклонёнными. Бесплатной почты правило не касается. Вернувшиеся — по порядку заведения.
    Коммит — за вызывающим. Поиск по всей базе в начале каждой `clean` идёт регуляркой по всем
    отклонённым кодом дубля — их сотни; при росте до десятков тысяч — сузить: домены, где с
    прошлого прохода сменился статус лида, или частичный индекс по `status`/`rejection_reason`.

    «Держатель отклонён» на свободном домене верно и так, но им круги очистки и сходятся:
    вернувшегося, снова отклонённого из-за держателя, вернёт только отклонение уже нового
    держателя, — а не любой следующий круг, если `held` однажды ошибётся."""
    back = await _first(session, domain_ids)
    await _give_back(session, back)
    return list(back)


async def returnable(session: AsyncSession, hypothesis_id: int) -> int:
    """Сколько лидов гипотезы вернёт поиск свободных компаний в начале прохода очистки (`freed`
    по всей базе) — тем же помощником, без записи: окно перед очисткой называет и их."""
    found = await _first(session, None)
    return sum(1 for lead in found if lead.hypothesis_id == hypothesis_id)


async def _first(
    session: AsyncSession, domain_ids: Collection[int] | None
) -> dict[SalesLeadModel, str]:
    """Самый ранний возвращаемый лид каждой свободной компании → слова возврата `BACK`."""
    first: dict[int, tuple[SalesLeadModel, str]] = {}
    for query in _free(domain_ids):
        for lead, holder_id, name, own in (await session.execute(query)).tuples():
            said = BACK.say(lead=holder_id, hypothesis=name, own=own)
            first.setdefault(lead.domain_id, (lead, said))
    return dict(sorted(first.values(), key=lambda found: found[0].id))


def _free(
    domain_ids: Collection[int] | None,
) -> Iterator[Select[tuple[SalesLeadModel, int, str, str]]]:
    """Запросы `freed` — частями по `CHUNK` доменов, а без доменов — один по всей базе."""
    holder, hypothesis = aliased(SalesLeadModel), aliased(SalesHypothesisModel)
    own = aliased(SalesHypothesisModel)
    query = (
        select(SalesLeadModel, holder.id, hypothesis.name, own.name)
        .join(holder, holder.id == HELD.lead_of(SalesLeadModel.cleaning_note))
        .join(hypothesis, hypothesis.id == holder.hypothesis_id)
        .join(own, own.id == SalesLeadModel.hypothesis_id)
        .join(DomainModel, DomainModel.id == SalesLeadModel.domain_id)
        .where(
            SalesLeadModel.status == LeadStatus.REJECTED,
            SalesLeadModel.rejection_reason == RejectionReason.DUPLICATE.value,
            holder.status == LeadStatus.REJECTED,
            holder.domain_id == SalesLeadModel.domain_id,
            DomainModel.host.not_in(FREE),
            ~held(SalesLeadModel.domain_id),
        )
        .order_by(SalesLeadModel.id)
    )
    if domain_ids is None:
        yield query
        return
    for chunk in _chunks(domain_ids):
        yield query.where(SalesLeadModel.domain_id.in_(chunk))


async def _give_back(session: AsyncSession, back: Mapping[SalesLeadModel, str]) -> None:
    """Вернувшиеся (лид → слова) — снова `new`, без причины и без прежнего вердикта: очистка
    проверит их целиком, и новый `unknown` не сойдёт за второй (`unconfirmed.judged`)."""
    if not back:
        return
    await clean_write.write(session, [_returned(lead, said) for lead, said in back.items()])
    logger.info(
        "продажи: компания освободилась — лиды вернулись",
        extra={"leads": [lead.id for lead in back]},
    )


def _returned(lead: SalesLeadModel, said: str) -> dict[str, object]:
    return {
        clean_write.LEAD_ID: lead.id,
        "status": LeadStatus.NEW,
        "rejection_reason": None,
        "cleaning_note": said,
        "verification_status": None,
        "verification_score": None,
        "verified_at": None,
    }


async def held_sites(
    session: AsyncSession, run_id: int, candidate_ids: Collection[int] | None = None
) -> dict[int, str]:
    """Принятые сайты прогона без лида, чей домен держит живой лид продаж (`held`): кандидат →
    итог словами (`Holder.site`) — адрес им не ищется и лид не заводится, и «ждут поиска» они не
    ждут. `candidate_ids` — только эти кандидаты (страница списка сайтов). Одно правило у отчёта
    прогона (`held_in_run`) и у итога сайта в списке сайтов прогона (`segment_sites`) — тем же
    условием, что у очереди поиска адреса и заведения лидов."""
    query = select(Candidate.id, Candidate.domain_id).where(
        Candidate.run_id == run_id,
        Candidate.lane == Lane.ACCEPT,
        Candidate.lead_id.is_(None),
        held(Candidate.domain_id),
    )
    if candidate_ids is not None:
        query = query.where(Candidate.id.in_(sorted(candidate_ids)))
    rows = (await session.execute(query)).tuples().all()
    holding = await holders(session, {domain for _, domain in rows})
    return {site: holding[domain].site for site, domain in rows if domain in holding}


async def held_in_run(session: AsyncSession, run_id: int) -> Counter[str]:
    """Удержанные сайты прогона (`held_sites`) → сколько, словами итога: для «Без адреса —
    почему» в отчёте прогона."""
    return Counter((await held_sites(session, run_id)).values())
