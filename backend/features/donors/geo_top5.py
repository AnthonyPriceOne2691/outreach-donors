"""Докупка топ-5 стран донорам с неполной разбивкой — `outreach geo-top5`.

До 16.10.2026 запрос по странам не шёл, когда верхняя страна пакета совпадала
со страной прогона: вердикт он не менял, а у донора оставалась одна страна вместо
пяти (`donors.geo_partial`). С 16.10 прогон спрашивает страны всегда (`collect.py`,
решение владельца: «org_traffic по топ-5 странам» — обязательное поле ТЗ), а уже
записанным их докупает эта команда — разово, по «да» человека: запрос по странам
на донора, `UNITS_BY_COUNTRY` юнитов.

**Только данные.** Разбивка, доля верхней страны и сама верхняя — из ответа; статус,
причина и решение человека не трогаются: вердикт вынесен правилом своей версии
настроек, и докупка его не пересуживает — как и пересчёт долей (`geo_recount.py`).
Верхняя страна полного ответа бывает не той, что дал пакет, — такие называются
отдельно: решать о них человеку.

**Кап — до первого запроса, правилом трат вне прогона** (`runs/budget.units_free`):
смета сверяется с остатком месячного капа по своему журналу и с остатком у
провайдера, оба — за вычетом обещанного идущими прогонами. Остаток провайдера
неизвестен или смета не помещается — отказ словами до первой траты. И по ходу:
провайдер взял за запрос больше сметы — запрос, который в остаток уже не влезает,
не делается.

**Фиксация — после каждого донора.** Расход ложится в журнал той же транзакцией,
что разбивка, и так же, как пишет прогон (`RunRepository.record_usage`): обрыв
посреди списка не теряет купленного и не оставляет трату без строки. Повторный
запуск берёт только оставшихся — у докупленных флаг снят.

**Сбой по одному донору — строка в журнал и дальше**: провайдер не ответил одному,
следующему ответит. Отказ, который повтор не исправит (ключ, права, квота —
`AhrefsError.permanent`), — стоп словами: дальше каждый запрос упал бы так же.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from sqlalchemy import case, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.ahrefs.client import AhrefsClient, AhrefsError
from backend.features.ahrefs.units import UNITS_BY_COUNTRY, UsageCollector
from backend.features.core.domain import Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.donors.geo import COUNTRY_ROWS, CountryShare, build_breakdown
from backend.features.donors.standing import is_donor
from backend.features.runs.budget import QuotaUnavailableError, units_left
from backend.features.runs.repository import RunRepository
from backend.features.runs.spending import cap_left

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PartialDonor:
    """Донор с неполной разбивкой: кому докупать."""

    donor_id: int
    host: str
    org_traffic: int | None
    #: Верхняя страна пакета — единственная в разбивке.
    geo: str | None
    #: Принят человеком — донор экрана «Доноры».
    accepted: bool


@dataclass(frozen=True, slots=True)
class Top5Plan:
    """Кому докупать за этот запуск и сколько таких всего."""

    donors: list[PartialDonor]
    #: С неполной разбивкой под условием — до `--limit`.
    found: int

    @property
    def units(self) -> int:
        """Смета: запрос по странам на каждого."""
        return len(self.donors) * UNITS_BY_COUNTRY


async def plan_top5(
    session: AsyncSession, *, accepted_only: bool = False, limit: int | None = None
) -> Top5Plan:
    """Кому докупать. Только читает.

    Принятые человеком — первыми: это доноры, которых видит экран и выгрузка, и
    `--limit` отдаёт юниты им. Дальше — по номеру записи: порядок полный, и запуски
    с одним `--limit` идут по списку, а не вразброс.
    """
    accepted = is_donor()
    statement = (
        select(
            DonorModel.id,
            DomainModel.host,
            DonorModel.org_traffic,
            DonorModel.geo,
            accepted.label("accepted"),
        )
        .join(DomainModel, DomainModel.id == DonorModel.domain_id)
        .where(DonorModel.geo_partial.is_(True))
        .order_by(case((accepted, 0), else_=1), DonorModel.id)
    )
    if accepted_only:
        statement = statement.where(accepted)
    rows = (await session.execute(statement)).tuples().all()
    # Решения нет — `review` пуст, и условие даёт NULL, а не «нет»: `bool` сводит.
    found = [
        PartialDonor(donor_id, host, traffic, geo, bool(is_accepted))
        for donor_id, host, traffic, geo, is_accepted in rows
    ]
    return Top5Plan(donors=found if limit is None else found[:limit], found=len(found))


@dataclass(frozen=True, slots=True)
class Room:
    """Сколько можно потратить сейчас — правилом трат вне прогона (`runs/budget.units_free`)."""

    cap: int
    #: Осталось по месячному капу — по своему журналу расхода.
    cap_left: int
    #: Обещано идущими прогонами и ещё не потрачено.
    claimed: int
    #: Осталось у Ahrefs за вычетом обещанного; `None` — неизвестно.
    provider_left: int | None
    #: Почему остаток у Ahrefs неизвестен.
    provider_unknown: str | None = None

    @property
    def free(self) -> int:
        """Меньшее из двух остатков; остаток провайдера неизвестен — ноль: вслепую не тратим."""
        if self.provider_left is None:
            return 0
        return max(0, min(self.cap_left - self.claimed, self.provider_left))


async def room_for(session: AsyncSession, client: AhrefsClient | None, *, cap: int) -> Room:
    """Остатки капа и провайдера. Остаток у Ahrefs — бесплатным запросом; без клиента
    (ключа нет) или без ответа — неизвестен, и сказано почему."""
    month = await cap_left(session, cap=cap)
    claimed = await RunRepository(session).claimed_units()
    if client is None:
        return Room(cap, month, claimed, None, "ключа Ahrefs нет (AHREFS_API_KEY)")
    try:
        provider = await units_left(client, claimed=claimed)
    except QuotaUnavailableError as exc:
        logger.warning("geo-top5: остаток у Ahrefs неизвестен — %s", exc)
        return Room(cap, month, claimed, None, str(exc))
    return Room(cap, month, claimed, provider)


def refusal(plan: Top5Plan, room: Room) -> str | None:
    """Почему не купить — словами; `None` — помещается."""
    if room.provider_left is None:
        return (
            f"остаток юнитов у Ahrefs неизвестен — тратить вслепую нельзя: {room.provider_unknown}"
        )
    if plan.units <= room.free:
        return None
    fits = room.free // UNITS_BY_COUNTRY
    hint = f" Помещается доноров: {fits} — `--limit {fits}`." if fits else ""
    return (
        f"не помещается: {len(plan.donors)} × {UNITS_BY_COUNTRY} = {plan.units} юнитов, "
        f"можно потратить {room.free}.{hint}"
    )


@dataclass(slots=True)
class Top5Report:
    """Что вышло: кому докуплено, кому нет и почему, во что обошлось."""

    bought: list[str] = field(default_factory=list)
    #: Верхняя страна полного ответа — не та, что дал пакет: хост, было, стало.
    moved: list[tuple[str, str | None, str]] = field(default_factory=list)
    #: Ahrefs ответил без стран — разбивка оставлена как была.
    empty: list[str] = field(default_factory=list)
    #: Провайдер не ответил этому донору — хост и почему.
    failed: list[tuple[str, str]] = field(default_factory=list)
    #: Пока ждали ответа, донора обновил прогон — его разбивка уже полная.
    taken: list[str] = field(default_factory=list)
    #: Юнитов по журналу — сколько записано за этот запуск.
    units: int = 0
    #: Почему остановились: отказ провайдера, который повтор не исправит.
    stopped: str | None = None
    #: Почему остановились: следующий запрос не помещается в остаток.
    capped: str | None = None


async def buy_top5(
    session: AsyncSession,
    client: AhrefsClient,
    plan: Top5Plan,
    *,
    allowed: int,
    now: datetime | None = None,
) -> Top5Report:
    """Запрос по странам каждому по очереди — штатным клиентом: частоту и повторы
    держит он. Фиксация — после каждого донора.

    `allowed` — сколько можно потратить (`Room.free` при показе). Смета помещалась,
    но провайдер мог взять за запрос больше `UNITS_BY_COUNTRY`: следующий запрос,
    который по смете в остаток не влезает, не делается.
    """
    moment = now or datetime.now(UTC)
    day = moment.date().isoformat()
    report = Top5Report()
    spent = UsageCollector()
    previous, client.on_usage = client.on_usage, spent
    try:
        for donor in plan.donors:
            if report.units + UNITS_BY_COUNTRY > allowed:
                report.capped = (
                    f"записано {report.units} юнитов из {allowed}, "
                    f"следующий запрос ({UNITS_BY_COUNTRY}) не помещается"
                )
                break
            go_on = await _buy_one(session, client, donor, day=day, moment=moment, report=report)
            report.units += await _journal(session, spent)
            await session.commit()
            if not go_on:
                break
    finally:
        client.on_usage = previous
        # Трата, о которой клиент успел сообщить до обрыва, — тоже строкой журнала.
        if spent.pending:
            report.units += await _journal(session, spent)
            await session.commit()
    return report


async def _buy_one(
    session: AsyncSession,
    client: AhrefsClient,
    donor: PartialDonor,
    *,
    day: str,
    moment: datetime,
    report: Top5Report,
) -> bool:
    """Страны одному донору. `False` — дальше нельзя: отказ, который повтор не исправит."""
    try:
        response = await client.metrics_by_country(donor.host, day, top_n=COUNTRY_ROWS)
    except AhrefsError as exc:
        if exc.permanent:
            logger.exception("geo-top5: остановлено на %s — повтор не поможет", donor.host)
            report.stopped = f"{donor.host}: {exc}"
            return False
        logger.warning("geo-top5: страны для %s не получены: %s", donor.host, exc)
        report.failed.append((donor.host, str(exc)))
        return True
    breakdown = build_breakdown(response.rows, donor.org_traffic or 0)
    if not breakdown:
        logger.warning("geo-top5: Ahrefs не вернул стран для %s — оставлено как было", donor.host)
        report.empty.append(donor.host)
        return True
    if not await _write(session, donor.donor_id, breakdown, moment):
        report.taken.append(donor.host)
        return True
    report.bought.append(donor.host)
    if breakdown[0].country != (donor.geo or "").lower():
        report.moved.append((donor.host, donor.geo, breakdown[0].country))
    return True


async def _write(
    session: AsyncSession, donor_id: int, breakdown: list[CountryShare], moment: datetime
) -> bool:
    """Полная разбивка — на донора, если она у него всё ещё неполная.

    Условием, а не слепой записью: прогон, обновивший донора, пока ждали ответа, уже
    записал свежую разбивку вместе с вердиктом, и затирать её нельзя (как у пересчёта
    долей, `geo_recount.apply_recount`). Верхняя страна — из ответа, чтобы доля рядом
    с ней была её долей. Статус, причина и решение человека не трогаются.
    """
    updated = await session.execute(
        update(DonorModel)
        .where(DonorModel.id == donor_id, DonorModel.geo_partial.is_(True))
        .values(
            geo=breakdown[0].country,
            geo_breakdown=[asdict(item) for item in breakdown],
            geo_top_share=breakdown[0].share,
            geo_partial=False,
            geo_refreshed_at=moment,
        )
        .returning(DonorModel.id)
    )
    return updated.scalar_one_or_none() is not None


async def _journal(session: AsyncSession, spent: UsageCollector) -> int:
    """Купленное — в журнал расхода, как пишет прогон: операция клиента (`by_country`),
    юниты из заголовков ответа, этап — доноры. Без номера прогона: докупка не прогон."""
    runs = RunRepository(session)
    units = 0
    for operation, cost in spent.drain():
        await runs.record_usage(run_id=None, operation=operation, cost=cost, stage=Stage.DONORS)
        units += cost.billable
    return units
