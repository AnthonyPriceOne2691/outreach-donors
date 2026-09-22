"""Кандидат становится рекламодателем: дедуп, стоп-листы, лучшая ссылка.

**Дедупликация выражена базой, а не кодом.** Требование говорит «один
рекламодатель — одно письмо, сколько бы страниц он ни занимал»;
у таблицы `advertisers` домен уникален, и второй строке взяться неоткуда.
Здесь остаётся собрать из нескольких кандидатов один — и выбрать
из них лучшую ссылку, ту, под которую будет написано письмо.

**Кто попадает.** Кандидат с вердиктом «куплена» либо подтверждённый
человеком. Отклонённый человеком не попадает, даже если баллов у него
много: решение человека сильнее вердикта скоринга.

**Кого отсеиваем до того, как искать контакт.** Доноров из стоп-листа
поставщиков — их рекламодатели это чужие клиенты и свои же размещения.
И адресатов из общего стоп-листа: он один на оба этапа, потому что
один и тот же адресат не должен получить письмо и как донор,
и как рекламодатель.

Отсев идёт **до** поиска контакта намеренно: платная ступень лестницы
стоит денег, и тратить их на того, кому не напишем, незачем.

**Пересчёт обновляет, а не задваивает.** Скоринг будет меняться; строка
рекламодателя переписывается на месте, потому что письмо ему одно
и адресат один.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import Verdict
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.advertisers import AdvertiserModel, SupplierDonorModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.ops import SuppressionModel

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


def _wins(candidate: CandidateModel, current: CandidateModel) -> bool:
    """Лучший кандидат на домен: сначала балл, потом охват страниц.

    Под лучшую ссылку пишется письмо, и «лучшая» здесь значит самая
    доказательную: высокий балл при ссылках со многих страниц труднее
    объяснить случайностью.
    """
    if candidate.points != current.points:
        return candidate.points > current.points
    return candidate.pages > current.pages


async def _suppliers(session: AsyncSession) -> set[str]:
    rows = await session.execute(
        select(SupplierDonorModel.host).where(SupplierDonorModel.in_force(datetime.now(UTC)))
    )
    return {host.lower() for host in rows.scalars().all()}


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
    rows = list(
        (
            await session.execute(
                select(CandidateModel).where(
                    (CandidateModel.verdict == Verdict.BOUGHT) | CandidateModel.confirmed.is_(True)
                )
            )
        )
        .scalars()
        .all()
    )
    # Решение человека сильнее вердикта скоринга: отклонённый не идёт
    # дальше, сколько бы баллов ему ни насчитали.
    rows = [row for row in rows if row.confirmed is not False]
    report.considered = len({row.target_root for row in rows})
    if not rows:
        return report

    suppliers, suppressed = await _suppliers(session), await _suppressed(session)

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
        row.confirmed_by_human = candidate.confirmed is True
        if row.confirmed_by_human:
            report.by_human += 1

    await session.flush()
