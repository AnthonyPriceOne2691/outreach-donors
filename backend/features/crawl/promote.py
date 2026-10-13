"""Кандидат становится рекламодателем: дедуп, стоп-листы, лучшая ссылка.

**Дедупликация выражена базой, а не кодом.** Требование говорит «один
рекламодатель — одно письмо, сколько бы страниц он ни занимал»;
у таблицы `advertisers` домен уникален, и второй строке взяться неоткуда.
Здесь остаётся собрать из нескольких кандидатов один — и выбрать
из них лучшую ссылку, ту, под которую будет написано письмо.

**Кто попадает.** Кандидат с вердиктом «куплена» либо подтверждённый
человеком. Отклонённый человеком не попадает, даже если баллов у него
много: решение человека сильнее вердикта скоринга. Крупные сайты
(DR > 80) сюда не доходят — их отсевает пересчёт (`big_sites.py`),
если только человек не сказал «пишем» сам.

**Кого отсеиваем до того, как искать контакт.** Доноров из стоп-листа
поставщиков — их рекламодатели это чужие клиенты и свои же размещения;
поставщик — общим правилом с обходом, хоть с экрана, хоть консолью
(`suppliers.py`). Поставщик, внесённый после перевода, снимает свои ссылки и у
заведённых рекламодателей: их доводит сборка писем (`apply_suppliers`).
И адресатов из общего стоп-листа: он один на оба этапа, потому что
один и тот же адресат не должен получить письмо и как донор,
и как рекламодатель.

Отсев идёт **до** поиска контакта намеренно: платная ступень лестницы
стоит денег, и тратить их на того, кому не напишем, незачем.

**Пересчёт обновляет, а не задваивает.** Скоринг будет меняться; строка
рекламодателя переписывается на месте, потому что письмо ему одно
и адресат один.

**«Не пишем» человека снимает рекламодателя** (`withdraw`, сверка ТЗ 11.10.2026). До этого
решение ставило кандидату `confirmed=False`, и только: заведённый переводом
рекламодатель оставался, а сборка и отправка смотрят на него, а не на кандидата, —
оффер ложному рекламодателю собрался бы и ушёл. Решение по кандидату — про его
ссылку: на тот же домен бывает ссылается второй наш донор, и его «куплено» первая
ссылка не отменяет. Поэтому домен снимается, только когда подходящих кандидатов
у него не осталось — тем же условием, что у перевода; остались — лучшая ссылка
пересобирается из них. Вместе с рекламодателем снимаются его письма по найденной
ссылке (`offer_stop.py`), а письмо, собранное под снятую ссылку, не уходит.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.crawl.advertiser_rules import writes_to
from backend.features.crawl.niche import NICHE
from backend.features.crawl.offer_stop import stop_offers
from backend.features.crawl.suppliers import supplier_hosts
from backend.features.letters import attempts

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PromotionReport:
    """Что случилось при переводе. Числа, а не «готово».

    Отказы считаются по причинам: «ни одного рекламодателя» и «все
    отсеяны стоп-листом поставщиков» — разные новости, и вторая
    означает, что список стоит перечитать.
    """

    considered: int = 0
    promoted: int = 0
    updated: int = 0
    skipped_supplier: int = 0
    skipped_suppressed: int = 0
    removed: int = 0
    by_human: int = 0
    hosts: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, int]:
        return {
            "рассмотрено": self.considered,
            "заведено": self.promoted,
            "обновлено": self.updated,
            "донор в стоп-листе поставщиков": self.skipped_supplier,
            "адресат в общем стоп-листе": self.skipped_suppressed,
            "снято с уже заведённых": self.removed,
            "подтверждено человеком": self.by_human,
        }


@dataclass(frozen=True, slots=True)
class Standing:
    """Что видно над кнопкой «Перевести»: сколько переводить и сколько уже есть.

    `ready` — домены среди «куплено» и «пишем», `fresh` — те из них, кого
    среди рекламодателей ещё нет. Стоп-листы здесь не вычитаются: их
    проверяет сам перевод и называет отсеянных в отчёте числом.
    """

    ready: int
    fresh: int
    advertisers: int
    with_address: int


async def standing(session: AsyncSession) -> Standing:
    """Числа для экрана — тем же условием, что и сам перевод."""
    known = (
        select(AdvertiserModel.id)
        .join(DomainModel, DomainModel.id == AdvertiserModel.domain_id)
        .where(DomainModel.host == CandidateModel.target_root)
        .exists()
    )
    roots = func.count(func.distinct(CandidateModel.target_root))
    ready = await session.scalar(select(roots).where(writes_to()))
    fresh = await session.scalar(select(roots).where(writes_to(), ~known))
    addressed = select(ContactModel.id).where(ContactModel.domain_id == AdvertiserModel.domain_id)
    total = await session.scalar(select(func.count(AdvertiserModel.id)))
    with_address = await session.scalar(
        select(func.count(AdvertiserModel.id)).where(addressed.exists())
    )
    return Standing(
        ready=int(ready or 0),
        fresh=int(fresh or 0),
        advertisers=int(total or 0),
        with_address=int(with_address or 0),
    )


def _wins(candidate: CandidateModel, current: CandidateModel) -> bool:
    """Лучший кандидат на домен: сначала балл, потом охват страниц.

    Под лучшую ссылку пишется письмо, и «лучшая» здесь значит самая
    доказательную: высокий балл при ссылках со многих страниц труднее
    объяснить случайностью.
    """
    if candidate.points != current.points:
        return candidate.points > current.points
    return candidate.pages > current.pages


async def _suppressed(session: AsyncSession) -> set[str]:
    """Хосты из общего стоп-листа. Он один на оба этапа."""
    rows = await session.execute(
        select(DomainModel.host)
        .join(SuppressionModel, SuppressionModel.domain_id == DomainModel.id)
        .where(SuppressionModel.in_force(datetime.now(UTC)))
    )
    return {host.lower() for host in rows.scalars().all()}


async def _domain_ids(session: AsyncSession, hosts: list[str]) -> dict[str, int]:
    """Номера доменов, заводя недостающие.

    Рекламодателя в `domains` обычно ещё нет: туда попадают доноры
    из выдачи, а этот пришёл со страницы донора.
    """
    if not hosts:
        return {}
    statement = insert(DomainModel).values([{"host": host} for host in hosts])
    await session.execute(statement.on_conflict_do_nothing(index_elements=["host"]))
    rows = await session.execute(
        select(DomainModel.host, DomainModel.id).where(DomainModel.host.in_(hosts))
    )
    return dict(rows.all())  # type: ignore[arg-type]


def _pick(candidates: list[CandidateModel]) -> dict[str, CandidateModel]:
    """По одному лучшему кандидату на домен."""
    best: dict[str, CandidateModel] = {}
    for candidate in candidates:
        current = best.get(candidate.target_root)
        if current is None or _wins(candidate, current):
            best[candidate.target_root] = candidate
    return best


async def promote(session: AsyncSession) -> PromotionReport:
    """Перевести подходящих кандидатов в рекламодателей."""
    report = PromotionReport()
    report.removed += await apply_rejections(session)
    rows = list((await session.execute(select(CandidateModel).where(writes_to()))).scalars().all())
    report.considered = len({row.target_root for row in rows})
    if not rows:
        return report

    suppliers, suppressed = await supplier_hosts(session), await _suppressed(session)

    # Доноры-поставщики отсеиваются ДО выбора лучшего кандидата, а не
    # после. Иначе рекламодатель терялся, когда лучшая ссылка на него
    # приходила от поставщика, а обычный донор ссылался слабее: домен
    # выпадал целиком. Поймано собственным тестом.
    all_roots = {row.target_root for row in rows}
    clean = [row for row in rows if row.donor_host.lower() not in suppliers]
    report.skipped_supplier = len(all_roots - {row.target_root for row in clean})

    best = _pick(clean)

    keep: dict[str, CandidateModel] = {}
    dropped: list[str] = sorted(all_roots - set(best))
    for root, candidate in best.items():
        if root in suppressed:
            report.skipped_suppressed += 1
            dropped.append(root)
            continue
        keep[root] = candidate

    # Стоп-лист пополняют задним числом: донор становится партнёром
    # уже после того, как его рекламодатели заведены. Найдено живым
    # прогоном — отсев молчал про тех, кто попал в базу раньше него.
    await _drop(session, dropped, report)

    if not keep:
        logger.info(
            "рекламодатели: подходящих нет — %s отсеяно стоп-листом поставщиков, %s общим",
            report.skipped_supplier,
            report.skipped_suppressed,
        )
        return report

    ids = await _domain_ids(session, sorted(keep))
    donors_per_root = _donors_per_root(clean)
    await _write(session, keep, ids, donors_per_root, report)
    logger.info("рекламодатели: %s", report.as_dict())
    return report


async def _drop(session: AsyncSession, roots: list[str], report: PromotionReport) -> None:
    """Снять рекламодателей, которых отсеяли стоп-листы.

    Снимается только то, что отсеяли **в этом проходе**: отсутствие
    домена среди кандидатов значит «про него сейчас ничего не считали»,
    а не «он больше не рекламодатель». Стереть по отсутствию значило бы
    терять заведённых руками.
    """
    if not roots:
        return
    ids = (
        (await session.execute(select(DomainModel.id).where(DomainModel.host.in_(sorted(roots)))))
        .scalars()
        .all()
    )
    if not ids:
        return
    found = (
        (
            await session.execute(
                select(AdvertiserModel).where(AdvertiserModel.domain_id.in_(list(ids)))
            )
        )
        .scalars()
        .all()
    )
    for row in found:
        await session.delete(row)
        report.removed += 1
    if found:
        logger.info("рекламодатели: снято по стоп-листам %s", report.removed)
    await session.flush()


@dataclass(frozen=True, slots=True)
class Withdrawn:
    """Что «не пишем» человека сделало с рекламодателем домена."""

    #: Рекламодатель по найденной ссылке снят: подходящих кандидатов у домена не осталось.
    removed: bool = False
    #: Писем по найденной ссылке снято — из очереди и со сроков добивок (`offer_stop.py`).
    stopped: int = 0

    def as_details(self) -> dict[str, object]:
        """Для журнала: что сняло решение. Пусто — рекламодателя оно не тронуло."""
        details: dict[str, object] = {}
        if self.removed:
            details["рекламодатель снят"] = True
        if self.stopped:
            details["снято писем"] = self.stopped
        return details


async def withdraw(session: AsyncSession, rejected: CandidateModel) -> Withdrawn:
    """«Не пишем» человека по кандидату — что стало с рекламодателем его домена.

    Подходящих кандидатов у домена не осталось (тем же условием, что у перевода,
    со стоп-листом поставщиков) — рекламодатель снимается вместе с письмами по
    найденной ссылке. Остались — рекламодатель пересобирается из них, а письма
    в очереди не под его новую лучшую ссылку останавливаются: их текст — про
    страницу, о которой человек сказал «не пишем», или про ту, что сменил повторный
    обход. Остановленное так письмо следующую сборку не держит — она пишет под
    действующую ссылку (`offer_stop.py`).
    """
    await session.flush()
    advertiser = await session.scalar(
        select(AdvertiserModel)
        .join(DomainModel, DomainModel.id == AdvertiserModel.domain_id)
        .where(DomainModel.host == rejected.target_root)
    )
    if advertiser is None:
        return Withdrawn()
    return await _rebuild(session, advertiser, rejected.target_root, await supplier_hosts(session))


async def _rebuild(
    session: AsyncSession,
    advertiser: AdvertiserModel,
    host: str,
    suppliers: set[str],
    *,
    reason: str = attempts.LINK_WITHDRAWN,
) -> Withdrawn:
    """Рекламодатель `host` — заново из подходящих кандидатов, кроме ссылок с площадок
    поставщиков (условие перевода). Не осталось их — снят вместе с письмами по найденной
    ссылке; остались — лучшая ссылка пересобирается, а первые письма в очереди не под неё
    снимаются причиной `reason` (`offer_stop.py`)."""
    rest = [
        row
        for row in (
            await session.scalars(
                select(CandidateModel).where(CandidateModel.target_root == host, writes_to())
            )
        ).all()
        if row.donor_host.lower() not in suppliers
    ]
    if not rest:
        stopped = await stop_offers(session, advertiser.domain_id, reason=reason)
        removed = await _take_off(session, advertiser)
        if removed or stopped:
            logger.info("рекламодатели: %s снят — %s; писем снято %s", host, reason, stopped)
        return Withdrawn(removed=removed, stopped=stopped)

    ids = {host: advertiser.domain_id}
    await _write(session, _pick(rest), ids, _donors_per_root(rest), PromotionReport())
    keep = advertiser.best_page_url or ""
    stopped = await stop_offers(session, advertiser.domain_id, keep=keep, reason=reason)
    return Withdrawn(stopped=stopped)


async def _take_off(session: AsyncSession, advertiser: AdvertiserModel) -> bool:
    """Снять рекламодателя по найденной ссылке. Ложь — снимать было нечего.

    Бизнес ниши (`niche.py`) остаётся: его строка — решение человека о нём самом,
    и снимается только ссылка, под которую писал бы оффер по найденной ссылке.
    """
    if advertiser.source != NICHE:
        await session.delete(advertiser)
        await session.flush()
        return True
    had_link = bool(advertiser.best_page_url or advertiser.best_anchor)
    advertiser.best_page_url = None
    advertiser.best_anchor = None
    return had_link


async def apply_rejections(session: AsyncSession) -> int:
    """«Не пишем», которое ещё не дошло до рекламодателя, — довести, как свежее решение.

    Решения до 11.10.2026 рекламодателя не снимали, а два одновременных решения
    по ссылкам одного домена могут оставить ему снятую ссылку: каждое пересобирало
    рекламодателя, не видя другого. Довести — значит сделать то же, что решение
    (`withdraw`): снять рекламодателя, если подходящих кандидатов не осталось, или
    пересобрать его ссылку. Зовут перевод и сборка писем Этапа 2 — без ручного шага.
    Число — снятых рекламодателей.

    Берутся только те, кому довести есть что: рекламодатель со ссылкой, и ссылка
    снята либо подходящих кандидатов у домена нет. Домен, о котором сейчас ничего
    не считали, остаётся (`_drop`): снимают по явному решению, а не по отсутствию.
    """
    other = aliased(CandidateModel)
    still_chosen = (
        select(other.id)
        .where(other.target_root == CandidateModel.target_root, writes_to(other))
        .exists()
    )
    pending = await session.scalars(
        select(CandidateModel)
        .join(DomainModel, DomainModel.host == CandidateModel.target_root)
        .join(AdvertiserModel, AdvertiserModel.domain_id == DomainModel.id)
        .where(
            CandidateModel.confirmed.is_(False),
            AdvertiserModel.best_page_url.is_not(None),
            or_(AdvertiserModel.best_page_url == CandidateModel.best_page_url, ~still_chosen),
        )
        .order_by(CandidateModel.id)
    )
    removed = 0
    for candidate in pending.all():
        removed += int((await withdraw(session, candidate)).removed)
    if removed:
        logger.info("рекламодатели: снято прежним «не пишем» %s", removed)
    return removed


async def apply_suppliers(session: AsyncSession) -> int:
    """Поставщик, внесённый после перевода, — довести до рекламодателей, чья лучшая ссылка
    на его площадке (сверка ТЗ 12.10.2026). Число — снятых рекламодателей.

    Стоп-лист поставщиков пополняют задним числом, а запись держала только обход и перевод:
    сборка и отправка смотрят на строку рекламодателя, и оффер под ссылку на площадке
    поставщика собирался и уходил его рекламодателю — чужому клиенту. Довести — как
    «не пишем» (`withdraw`): ссылка пересобирается из подходящих кандидатов других доноров,
    а нет их — рекламодатель снимается. Первые письма в очереди под ссылку поставщика
    снимаются своей причиной (`attempts.SUPPLIER_WITHDRAWN`): они не уходили, и сборка
    пишет тому же адресату под действующую ссылку. Поставщик — одним правилом с обходом
    и переводом (`suppliers.supplier_hosts`). Зовёт сборка писем Этапа 2 до отбора.
    """
    suppliers = await supplier_hosts(session)
    if not suppliers:
        return 0
    rows = await session.execute(
        select(AdvertiserModel, DomainModel.host)
        .join(DomainModel, DomainModel.id == AdvertiserModel.domain_id)
        .where(func.lower(func.trim(AdvertiserModel.best_donor_host)).in_(sorted(suppliers)))
        .order_by(AdvertiserModel.id)
    )
    removed = 0
    for advertiser, host in rows.tuples().all():
        rebuilt = await _rebuild(
            session, advertiser, host, suppliers, reason=attempts.SUPPLIER_WITHDRAWN
        )
        removed += int(rebuilt.removed)
    if removed:
        logger.info("рекламодатели: снято поставщиком, внесённым после перевода, %s", removed)
    return removed


def _donors_per_root(rows: list[CandidateModel]) -> dict[str, int]:
    """Сколько разных наших доноров ссылается на домен."""
    seen: dict[str, set[str]] = {}
    for row in rows:
        seen.setdefault(row.target_root, set()).add(row.donor_host)
    return {root: len(hosts) for root, hosts in seen.items()}


async def _write(
    session: AsyncSession,
    keep: dict[str, CandidateModel],
    ids: dict[str, int],
    donors: dict[str, int],
    report: PromotionReport,
) -> None:
    """Записать рекламодателей, обновляя уже заведённых на месте."""
    rows = (
        await session.execute(
            select(AdvertiserModel).where(AdvertiserModel.domain_id.in_(list(ids.values())))
        )
    ).scalars()
    existing: dict[int, AdvertiserModel] = {row.domain_id: row for row in rows}

    for root, candidate in keep.items():
        domain_id = ids.get(root)
        if domain_id is None:
            logger.warning("рекламодатели: домен %s не завёлся — строка пропущена", root)
            continue

        row = existing.get(domain_id)
        if row is None:
            row = AdvertiserModel(domain_id=domain_id)
            session.add(row)
            report.promoted += 1
            report.hosts.append(root)
        else:
            report.updated += 1

        row.points = candidate.points
        row.donors = donors.get(root, 1)
        row.links = candidate.links
        row.best_donor_host = candidate.donor_host
        row.best_page_url = candidate.best_page_url
        row.best_anchor = candidate.best_anchor
        human = candidate.confirmed is True
        # У бизнеса ниши это поле — решение человека о нём самом (`niche.decide`):
        # перевод по ссылке его не переписывает. Иначе «пишем» бизнесу ниши молча
        # становилось «не пишем», стоило его ссылке набрать «куплено», и наоборот.
        if row.source != NICHE:
            row.confirmed_by_human = human
        report.by_human += int(human)

    await session.flush()
