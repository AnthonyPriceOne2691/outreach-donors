"""Хранение доноров: свежесть, домены, результаты сбора.

Главный запрос здесь — `fresh_hosts`. Он решает, за какие домены мы уже
заплатили, и от него напрямую зависит счёт: ошибка в сторону «не свежий»
оплачивает данные второй раз, ошибка в другую сторону выдаёт наружу
устаревшие цифры.

Свежесть считается по отметке времени, а не по наличию метрик. Домен, про
который Ahrefs ответил «не знаю», тоже получает отметку — иначе каждый прогон
заново жёг бы на нём юниты. Исключение одно — «не проверен», у которого
не пришли страны (`countries_missing`): про него не ответили, а не ответили
«не знаю», и следующий прогон добирает страны.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import filters
from backend.config import judge as judge_cfg
from backend.features.core.domain import DonorStatus
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.donors.author_door import opened_reason, opens
from backend.features.donors.collect import DomainResult, PaidMetrics
from backend.features.donors.publisher_judge import Decider, Recommendation
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


def judged_for_real() -> ColumnElement[bool]:
    """Вердикт на домене вынесен: модель ответила или решило правило.

    Строка без модели и не от правила — след сбоя: до 25.09.2026 отказ
    модели ложился на домен как вердикт «посмотри» и полгода держал его
    без суда (прогон №21 — 44 домена на сервере без ключа). Такая строка
    не кэш и не вердикт: судья и досуд берут домен снова.
    """
    # `coalesce`: сравнение с NULL в SQL даёт NULL, и `NOT` от него — тоже
    # NULL, то есть строка старого судьи без отметки «кто решил» выпала бы
    # и из кэша, и из досуда разом.
    return or_(
        DomainModel.judge_model.is_not(None),
        func.coalesce(DomainModel.judge_decided_by, "") == Decider.RULE.value,
    )


def countries_missing() -> ColumnElement[bool]:
    """«Не проверен», потому что не пришли страны, а метрики куплены (`collect._resolve_geo`).

    До стран доходит только домен, прошедший все четыре порога, — значит, все
    четыре метрики у него есть, а отметки стран нет: запрос по странам упал.
    Такой домен не свежий, хотя отметка метрик стоит, — и прогон добирает ему
    только страны (`paid_metrics`). «Ahrefs ответил: стран нет» — не сюда:
    отметка стран у него стоит, это «проверяли, данных нет».

    Признак — по колонкам, а не по причине: у записей до 11.10.2026 причина
    «не проверен» не хранилась (`_donor_values`).
    """

    def counted(key: str) -> ColumnElement[bool]:
        # Число, а не просто ключ: `null` в ответе — «Ahrefs не вернул», и такой
        # домен до стран не дошёл. `coalesce` — чтобы NULL не вывернул `NOT` у свежести.
        return func.coalesce(func.jsonb_typeof(DonorModel.metrics[key]), "") == "number"

    return and_(
        DonorModel.status == DonorStatus.UNCHECKED,
        DonorModel.geo_refreshed_at.is_(None),
        DonorModel.dr.is_not(None),
        DonorModel.org_traffic.is_not(None),
        counted("refdomains"),
        counted("org_keywords"),
    )


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
        """Домены, по которым данные ещё годны — платить за них не нужно.

        Без стран (`countries_missing`) — не годны: их добирает прогон.
        """
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
            .where(~countries_missing())
        )
        return set(rows.scalars().all())

    async def paid_metrics(
        self,
        hosts: Sequence[str],
        *,
        ttl_days: int = filters.METRICS_TTL_DAYS,
        now: datetime | None = None,
    ) -> dict[str, PaidMetrics]:
        """Купленные метрики доменов, которым не хватает только стран.

        Метрики в сроке — прогон доберёт страны, не покупая их второй раз;
        метрики старше срока — домен проверяется заново целиком, как любой
        несвежий.
        """
        if not hosts:
            return {}

        border = (now or datetime.now(UTC)) - timedelta(days=ttl_days)
        rows = await self._session.execute(
            select(DomainModel.host, DonorModel.metrics, DonorModel.metrics_refreshed_at)
            .join(DonorModel, DonorModel.domain_id == DomainModel.id)
            .where(DomainModel.host.in_(list(dict.fromkeys(hosts))))
            .where(DonorModel.metrics_refreshed_at > border)
            .where(countries_missing())
        )
        return {
            host: PaidMetrics(raw=raw, bought_at=bought_at)
            for host, raw, bought_at in rows.all()
            if raw is not None and bought_at is not None
        }

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

        След сбоя модели кэшем не считается (`judged_for_real`).
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
                    and_(DomainModel.judged_at > border, judged_for_real()),
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

    async def save_doors(self, doors: dict[str, str]) -> int:
        """Записать увиденное: где дверь или пустую строку — «двери нет».

        Нашлась дверь у отказа «продаёт своё» — вердикт идёт к человеку,
        как если бы судья видел меню сам (`author_door.open_door`). Решение
        человека не трогается. Возвращает, сколько отказов так открыто.
        """
        opened = 0
        for host, door in doors.items():
            domain = await self._session.scalar(select(DomainModel).where(DomainModel.host == host))
            if domain is None:
                continue
            domain.site_door = door[:256]
            if door and opens(domain.site_intent, domain.judge_recommendation):
                domain.judge_recommendation = Recommendation.REVIEW.value
                domain.judge_reason = opened_reason(domain.judge_reason or "", door)
                opened += 1
        await self._session.flush()
        return opened

    async def open_doors(self, hosts: Sequence[str], pages: Mapping[str, str]) -> int:
        """Дверь со страницы выдачи — на домен; отказ «продаёт своё» при
        известной двери — к человеку. Возвращает, сколько отказов открыто.

        `pages` — двери, увиденные на странице, по которой прогон нашёл
        домен. Незнание уступает знанию: NULL и «двери нет» в меню
        сменяются дверью страницы, найденная раньше дверь остаётся.

        Отказ открывается у каждого домена из `hosts`, чья дверь известна,
        а не только у тех, где она нашлась сейчас: вердикт мог быть вынесен
        до правила двери или взят прогоном из кэша, не видев страницы, по
        которой домен пришёл в этот раз. Очередь №18: nextinhr.com со
        страницей `/write-for-us` лежал в отказе. Решение человека
        не трогается.
        """
        opened = 0
        domains = await self._session.scalars(
            select(DomainModel).where(DomainModel.host.in_(list(dict.fromkeys(hosts))))
        )
        for domain in domains:
            page = pages.get(domain.host)
            if page and not domain.site_door:
                domain.site_door = page[:256]
            if domain.site_door and opens(domain.site_intent, domain.judge_recommendation):
                domain.judge_recommendation = Recommendation.REVIEW.value
                domain.judge_reason = opened_reason(domain.judge_reason or "", domain.site_door)
                opened += 1
        await self._session.flush()
        return opened

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
            # Причина — у отказа и у «не проверен»: у подходящего донора она была
            # бы объяснением успеха и путала бы отчёт. «Не проверен» без причины
            # (так было до 11.10.2026) не отличить: «Ahrefs не знает домен» добирать
            # незачем, «страны не получены» добирает следующий прогон.
            "reject_reason": (
                None if result.status is DonorStatus.SUITABLE else result.reason[:64]
            ),
            "metrics": result.raw,
            # Отметка ставится всегда, даже когда данных не пришло: иначе
            # домен вне индекса Ahrefs будет оплачиваться каждым прогоном.
            # У добора стран — время покупки метрик: их этот прогон не покупал.
            "metrics_refreshed_at": result.metrics_at or moment,
            "dr": int(result.metrics.dr) if result.metrics.dr is not None else None,
            "org_traffic": result.metrics.org_traffic,
            "geo": breakdown[0].country if breakdown else None,
            "geo_breakdown": [asdict(item) for item in breakdown] or None,
            "geo_top_share": breakdown[0].share if breakdown else None,
            "geo_partial": _is_partial(result),
            "geo_refreshed_at": moment if geo_asked else None,
        }
