"""Хранение доноров: свежесть, домены, результаты сбора.

Главный запрос здесь — `fresh_hosts`. Он решает, за какие домены мы уже
заплатили, и от него напрямую зависит счёт: ошибка в сторону «не свежий»
оплачивает данные второй раз, ошибка в другую сторону выдаёт наружу
устаревшие цифры.

Свежесть считается по отметке времени, а не по наличию метрик. Домен, про
который Ahrefs ответил «не знаю», тоже получает отметку — иначе каждый прогон
заново жёг бы на нём юниты.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import filters
from backend.config import judge as judge_cfg
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.donors.collect import DomainResult
from backend.features.donors.selection import human_advice


@dataclass(frozen=True, slots=True)
class JudgeRecord:
    """Вердикт судьи в том виде, в каком он ложится на домен."""

    intent: str
    recommendation: str
    reason: str
    quote: str | None = None
    source_url: str | None = None
    model: str | None = None
    #: Версия промпта (`publisher_judge.PROMPT_VERSION`); `None` — без модели.
    version: str | None = None
    #: Кто решил: `rule`, `model`, `arbiter` (`publisher_judge.Decider`).
    decided_by: str | None = None
    #: Что сказала главная; `None` — не спрашивали.
    home: dict[str, Any] | None = None
    #: Где сайт зовёт авторов или рекламодателей (`author_door`). Пустая
    #: строка — меню главной смотрели, двери нет; `None` — не смотрели,
    #: и прежнее знание о двери пересуд не стирает.
    door: str | None = None


def _is_partial(result: DomainResult) -> bool:
    """Разбивка неполная: спрашивали только верхнюю страну.

    Одна строка у полного ответа и одна у дешёвого пути — разные вещи,
    и по длине списка их не отличить.
    """
    return result.geo is not None and result.geo.partial


class DonorRepository:
    """Доступ к доменам и донорам."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def fresh_hosts(
        self,
        hosts: Sequence[str],
        *,
        ttl_days: int = filters.METRICS_TTL_DAYS,
        now: datetime | None = None,
    ) -> set[str]:
        """Домены, по которым данные ещё годны — платить за них не нужно."""
        if not hosts:
            return set()

        moment = now or datetime.now(UTC)
        border = moment - timedelta(days=ttl_days)

        rows = await self._session.execute(
            select(DomainModel.host)
            .join(DonorModel, DonorModel.domain_id == DomainModel.id)
            .where(DomainModel.host.in_(hosts))
            .where(DonorModel.metrics_refreshed_at.is_not(None))
            .where(DonorModel.metrics_refreshed_at > border)
        )
        return set(rows.scalars().all())

    async def fresh_judged(
        self,
        hosts: Sequence[str],
        *,
        ttl_days: int = judge_cfg.TTL_DAYS,
        now: datetime | None = None,
    ) -> dict[str, str]:
        """Домены, которые не судятся заново: хост → действующий совет.

        Это и есть кэш судьи — отдельного хранилища он не требует. Срок
        свой и длиннее метрик: способ заработка сайт меняет раз в годы.

        **Решение человека сильнее и не протухает.** Домен, по которому
        человек сказал своё, не пересуживается вовсе, а режется или
        проходит по его слову: спросить модель снова значит заплатить
        токенами за мнение, которое всё равно ничего не решит.
        """
        if not hosts:
            return {}

        moment = now or datetime.now(UTC)
        border = moment - timedelta(days=ttl_days)
        rows = await self._session.execute(
            select(DomainModel.host, DomainModel.judge_recommendation, DomainModel.human_intent)
            .where(DomainModel.host.in_(hosts))
            .where(
                or_(
                    DomainModel.human_intent.is_not(None),
                    DomainModel.judged_at > border,
                )
            )
        )
        found: dict[str, str] = {}
        for host, machine, human in rows.all():
            found[host] = human_advice(human) if human else (machine or "")
        return found

    async def save_judgements(
        self,
        verdicts: Mapping[str, JudgeRecord],
        *,
        now: datetime | None = None,
    ) -> int:
        """Вердикты судьи на домены. Возвращает число записей.

        ⚠ Решение человека здесь НЕ трогается ни при каких условиях.
        Пересуженный домен обновляет мнение модели, а отметка человека
        остаётся: расхождение между ними — единственный измеритель того,
        как часто модель ошибается, и затирать его пересудом значит
        каждый раз обнулять счёт.
        """
        if not verdicts:
            return 0

        moment = now or datetime.now(UTC)
        await self.ensure_domains(list(verdicts))
        for host, record in verdicts.items():
            # Незнание о двери не перетирает знание: пересуд без главной
            # не должен стирать «Advertise», найденный прошлым судом.
            door = {} if record.door is None else {"site_door": record.door[:256]}
            await self._session.execute(
                update(DomainModel)
                .where(DomainModel.host == host)
                .values(
                    site_intent=record.intent,
                    judge_recommendation=record.recommendation,
                    judge_quote=record.quote,
                    judge_reason=record.reason,
                    judge_source_url=record.source_url,
                    judge_model=record.model,
                    judge_version=record.version,
                    judge_decided_by=record.decided_by,
                    judge_home=record.home,
                    judged_at=moment,
                    **door,
                )
            )
        return len(verdicts)

    async def hosts_without_door(self, hosts: Sequence[str]) -> list[str]:
        """Домены, про чью дверь для авторов ещё не знаем (`site_door IS NULL`)."""
        if not hosts:
            return []
        rows = await self._session.execute(
            select(DomainModel.host)
            .where(DomainModel.host.in_(list(dict.fromkeys(hosts))))
            .where(DomainModel.site_door.is_(None))
            .order_by(DomainModel.host)
        )
        return list(rows.scalars().all())

    async def save_doors(self, doors: dict[str, str]) -> None:
        """Записать увиденное: где дверь или пустую строку — «двери нет»."""
        for host, door in doors.items():
            await self._session.execute(
                update(DomainModel).where(DomainModel.host == host).values(site_door=door[:256])
            )
        await self._session.flush()

    async def ensure_domains(self, hosts: Sequence[str]) -> dict[str, int]:
        """Заводит отсутствующие домены и возвращает соответствие хост → id.

        Вставка идёт одним запросом с игнорированием конфликта: два
        одновременных прогона по пересекающимся ключам не должны падать
        друг о друга.
        """
        if not hosts:
            return {}

        unique = list(dict.fromkeys(hosts))
        await self._session.execute(
            insert(DomainModel)
            .values([{"host": h} for h in unique])
            .on_conflict_do_nothing(index_elements=["host"])
        )
        rows = await self._session.execute(
            select(DomainModel.host, DomainModel.id).where(DomainModel.host.in_(unique))
        )
        return dict(rows.all())  # type: ignore[arg-type]

    async def save_results(
        self, results: Sequence[DomainResult], *, now: datetime | None = None
    ) -> int:
        """Сохраняет пачку результатов сбора. Возвращает число записей.

        Пачка — чекпоинт: после сохранения эти домены выпадают из повторных
        прогонов, и падение на следующей пачке не стоит уже сделанной работы.
        """
        if not results:
            return 0

        moment = now or datetime.now(UTC)
        ids = await self.ensure_domains([r.host for r in results])
        payload = [self._donor_values(r, ids[r.host], moment) for r in results if r.host in ids]
        if not payload:
            return 0

        statement = insert(DonorModel).values(payload)
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=["domain_id"],
                set_={
                    column: statement.excluded[column]
                    for column in (
                        "status",
                        "reject_reason",
                        "metrics",
                        "metrics_refreshed_at",
                        "dr",
                        "org_traffic",
                        "geo",
                        "geo_breakdown",
                        "geo_top_share",
                        "geo_partial",
                        "geo_refreshed_at",
                    )
                },
            )
        )
        return len(payload)

    @staticmethod
    def _donor_values(result: DomainResult, domain_id: int, moment: datetime) -> dict[str, object]:
        breakdown = result.breakdown
        geo_asked = result.geo is not None
        return {
            "domain_id": domain_id,
            "status": result.status,
            # Причина хранится только для отказов: у подходящего донора
            # «причина» была бы объяснением успеха и путала бы отчёт.
            "reject_reason": result.reason[:64] if result.status.value == "unsuitable" else None,
            "metrics": result.raw,
            # Отметка ставится всегда, даже когда данных не пришло: иначе
            # домен вне индекса Ahrefs будет оплачиваться каждым прогоном.
            "metrics_refreshed_at": moment,
            "dr": int(result.metrics.dr) if result.metrics.dr is not None else None,
            "org_traffic": result.metrics.org_traffic,
            "geo": breakdown[0].country if breakdown else None,
            "geo_breakdown": [asdict(item) for item in breakdown] or None,
            "geo_top_share": breakdown[0].share if breakdown else None,
            "geo_partial": _is_partial(result),
            "geo_refreshed_at": moment if geo_asked else None,
        }
