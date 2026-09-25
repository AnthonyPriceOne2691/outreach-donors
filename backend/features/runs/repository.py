"""Хранение прогонов: настройки с версиями, сам прогон и строки расхода.

Про версии порогов. Настройки не правятся на месте — каждая правка заводит
новую версию, а прогон ссылается на ту, с которой запускался. Иначе через
полгода нельзя объяснить, почему домен отсеян: пороги с тех пор поменяли,
и вердикт стал необъяснимым.

Про расход. Строки пишутся по ходу прогона, а не в конце. Прогон, упавший
на середине, уже потратил — и эта трата должна остаться в журнале, иначе
разбор «на что ушли юниты» соврёт именно там, где он нужнее всего.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.ahrefs.units import COUNTRY_CALL_SHARE, UNITS_BY_COUNTRY, UnitsCost
from backend.features.core import usage
from backend.features.core.domain import RunStatus, Stage, UsageProvider
from backend.features.core.models.ops import UsageRecordModel
from backend.features.core.models.run import RunModel, RunSettingsModel
from backend.features.donors.verdict import Thresholds
from backend.features.runs.browse import UnknownRunError

#: Имя системы в общей таблице расхода живёт в `core.usage` — здесь оно
#: оставлено ссылкой, потому что на него смотрят запросы ниже.
SYSTEM = usage.SYSTEM


# Какой провайдер стоит за операцией. Список закрытый: неизвестная операция
# должна быть замечена, а не тихо записана как «прочее» — иначе разбор
# расхода со временем превратится в одну строку «прочее» на весь счёт.
#: Сколько проверенных доменов по стране нужно, чтобы доверять её
#: собственной доле запросов по странам. Меньше — умолчание: одна страна
#: с тремя доменами даёт шум, а не число.
MIN_COUNTRY_HISTORY = 20
#: Окно истории: самые новые прогоны по стране, пока не наберётся столько
#: доменов. Старое вымывается само — прогоны до пакетной верхней страны
#: (18.09.2026) звали запрос по странам на каждый домен, и их доля
#: к нынешней логике отношения не имеет.
COUNTRY_HISTORY_WINDOW = 500


def country_share_from(history: Sequence[tuple[str, dict[str, Any]]], country: str) -> float:
    """Доля новых доменов, которым понадобился запрос по странам.

    На вход — прогоны от НОВЫХ к старым: страна и статистика. Берётся
    только своя страна: бэктест на 14 прогонах 22–23.09.2026 показал, что
    доля не переносится между странами (от 3% до 88%), и средняя по чужим
    для страны без истории занижала смету. Без своей истории — умолчание,
    с запасом: занизить здесь значит, что кап не держит трату.

    Считается по юнитам, списанным за запросы по странам, а не по
    вердиктам: это и есть то, что смета должна предсказать.
    """
    own = (country or "").lower()
    checked = 0
    calls = 0.0
    for stats in _own_runs(history, own):
        checked += int(stats["checked_now"])
        calls += _by_country_units(stats) / UNITS_BY_COUNTRY
        if checked >= COUNTRY_HISTORY_WINDOW:
            break
    if checked < MIN_COUNTRY_HISTORY:
        return COUNTRY_CALL_SHARE
    return min(1.0, calls / checked)


def _own_runs(history: Sequence[tuple[str, dict[str, Any]]], own: str) -> Iterator[dict[str, Any]]:
    """Прогоны своей страны, в которых что-то проверялось."""
    for run_country, stats in history:
        if (run_country or "").lower() == own and int(stats.get("checked_now") or 0) > 0:
            yield stats


def _by_country_units(stats: dict[str, Any]) -> int:
    return int((stats.get("units_by_operation") or {}).get("by_country") or 0)


#: Пометки о судьбе прогона в его отчёте. Их читает экран («причина») и
#: разбор мёртвых («продолжений» — сколько раз уже продолжали).
REASON_KEY = "причина"
RESUMES_KEY = "продолжений"
#: Сбой как есть, с именем класса исключения: по нему ищут в журнале.
#: На экран идёт причина — словами человека (`runs/reasons.py`).
FAILURE_KEY = "failure"


def carried_notes(previous: dict[str, Any] | None, *, finished: bool) -> dict[str, Any]:
    """Что из прежнего отчёта переживает новый.

    Отчёт попытки заменяет отчёт прошлой — но пометки о продолжениях не
    её, а прогона: без них счётчик разбора обнулялся бы на каждой попытке,
    а человек не узнал бы, что прогон прерывался. Законченный прогон
    получает итог «продолжен после сбоя», а не «будет продолжен».
    """
    notes = previous or {}
    resumes = int(notes.get(RESUMES_KEY) or 0)
    if not resumes:
        return {}
    kept: dict[str, Any] = {RESUMES_KEY: resumes}
    if finished:
        kept[REASON_KEY] = f"продолжен после сбоя ({resumes} раз): {notes.get(REASON_KEY, '')}"[
            :500
        ]
    return kept


#: Сколько последних прогонов с выдачей смотрит смета до запуска.
UNIQUE_HISTORY_WINDOW = 20

#: Меньше результатов — доля по прогону — шум: три ключа дают 20 доменов
#: из 27, и такой прогон ничего не говорит о большом.
MIN_RESULTS_FOR_SHARE = 20


def unique_share_from(history: Sequence[dict[str, Any]], default: float) -> float:
    """Доля уникальных доменов среди результатов выдачи — худшая из недавних.

    На вход — отчёты прогонов от НОВЫХ к старым. Худшая, а не средняя,
    по той же причине, что и вся смета до запуска: занижение значит, что
    прогон купит выдачу и упрётся в потолок уже после покупки. Доля
    от ниши и страны почти не зависит (0,38–0,85 на 16 прогонах
    22–24.09.2026) — зависит от того, насколько ключи перекрываются, —
    поэтому история общая, а не по стране.
    """
    shares: list[float] = []
    for stats in history:
        results = int(stats.get("serp_results") or 0)
        unique = int(stats.get("unique_hosts") or 0)
        if results >= MIN_RESULTS_FOR_SHARE and unique:
            shares.append(min(1.0, unique / results))
        if len(shares) >= UNIQUE_HISTORY_WINDOW:
            break
    return max(shares) if shares else default


class RunRepository:
    """Доступ к прогонам, их настройкам и журналу расхода."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_settings(self, thresholds: Thresholds, **extra: Any) -> RunSettingsModel:
        """Заводит новую версию настроек. Версия — следующая по счёту."""
        current = await self._session.scalar(select(func.max(RunSettingsModel.version)))
        settings = RunSettingsModel(
            version=(current or 0) + 1,
            min_dr=thresholds.min_dr,
            min_org_traffic=thresholds.min_org_traffic,
            min_refdomains=thresholds.min_refdomains,
            min_keywords=thresholds.min_keywords,
            **extra,
        )
        self._session.add(settings)
        await self._session.flush()
        return settings

    async def create_run(
        self,
        *,
        stage: Stage,
        settings_id: int,
        keywords: Sequence[str],
        country: str,
        depth_pages: int = 1,
        estimated_units: int | None = None,
        status: RunStatus = RunStatus.QUEUED,
    ) -> RunModel:
        """Заводит прогон. По умолчанию — «в очереди»: строка создаётся
        нажатием, а не первой тратой, иначе до первого платного запроса
        показывать нечего, а если задачу никто не возьмёт — то и никогда.

        Смета не обязательна: при постановке точного числа доменов ещё
        нет, оно появляется после выдачи.
        """
        run = RunModel(
            stage=stage,
            settings_id=settings_id,
            status=status,
            keywords=list(keywords),
            country=country,
            depth_pages=depth_pages,
            estimated_units=estimated_units,
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def get(self, run_id: int) -> RunModel:
        """Прогон по номеру. Нет такого — громко: задача, взявшая номер
        несуществующего прогона, иначе просто тихо ничего не делает."""
        run = await self._session.get(RunModel, run_id)
        if run is None:
            raise UnknownRunError(f"Прогона №{run_id} нет")
        return run

    async def settings_of(self, run: RunModel) -> RunSettingsModel:
        """Настройки прогона — явным запросом, а не `run.settings`.

        Связь ленивая, и её чтение в асинхронной сессии — синхронный запрос
        к базе, который падает (MissingGreenlet). Так и было до 24.09.2026:
        задача прогона из очереди падала на первой строке, ни один прогон
        с кнопки экрана не доходил до выдачи. Консольный прогон шёл мимо —
        там потолок передаётся готовым.
        """
        settings = await self._session.get(RunSettingsModel, run.settings_id)
        if settings is None:
            raise UnknownRunError(f"У прогона №{run.id} нет настроек №{run.settings_id}")
        return settings

    async def bind_job(self, run: RunModel, job_id: str | None) -> None:
        """Связать прогон с задачей очереди. Вызывается и при постановке,
        и при продолжении: старый номер после смерти воркера отвечает
        «мертва» и на живую задачу тоже."""
        run.job_id = job_id
        await self._session.flush()

    async def mark_running(self, run: RunModel) -> None:
        """Задача взяла прогон. Отдельная запись, а не побочный эффект
        первой траты: между «взяли» и «потратили» идёт выдача, и всё это
        время человек должен видеть, что прогон уже идёт."""
        run.status = RunStatus.RUNNING
        await self._session.flush()

    async def save_candidates(self, run: RunModel, candidates: dict[str, Any]) -> None:
        """Сохранить выдачу, за которую заплачено.

        Чекпоинт, а не отчёт: продолжение после смерти воркера берёт
        домены отсюда и не покупает выдачу второй раз. Поэтому пишется
        сразу и фиксируется, а не копится до конца прогона.
        """
        run.candidates = candidates
        await self._session.flush()

    async def set_estimate(self, run: RunModel, estimated_units: int) -> None:
        """Смета прогона, посчитанная по настоящим доменам. Появляется
        после выдачи: при постановке точного числа ещё нет.

        **Ставится один раз.** Продолжение после сбоя считает смету по
        оставшимся доменам — меньше первой, — и переписанная она ломала бы
        «смету против факта»: факт считается по журналу за все попытки.
        """
        if run.estimated_units is None:
            run.estimated_units = estimated_units
            await self._session.flush()

    async def touch(self, run_id: int) -> None:
        """Отметить, что прогон жив.

        Отдельным запросом и по номеру, а не через объект: удар идёт
        из своей короткой сессии, пока длинная занята пачкой. Без этого
        медленный живой прогон неотличим от мёртвого — по одному
        `updated_at` они выглядят одинаково.
        """
        await self._session.execute(
            update(RunModel).where(RunModel.id == run_id).values(updated_at=func.now())
        )
        await self._session.commit()

    async def save_stats(self, run: RunModel, stats: dict[str, Any]) -> None:
        """Переписать отчёт прогона. Пометки разбора ложатся туда же,
        где остальной отчёт: человек читает одно место, а не два."""
        run.stats = stats
        await self._session.flush()

    async def stop_run(self, run: RunModel, *, stats: dict[str, Any]) -> None:
        """Закрыть прогон как остановленный. Расход не трогаем: то, что
        он успел потратить, уже записано в журнале построчно."""
        run.status = RunStatus.STOPPED
        run.stats = stats
        await self._session.flush()

    async def country_call_share(self, country: str) -> float:
        """Доля запросов по странам для сметы — из истории своих прогонов.

        Смета по одной константе занижала трату в 14 прогонах из 14:
        доля ходит от 3% до 88% в зависимости от страны. См.
        `country_share_from` и okf/funnel-calibration.md.
        """
        rows = await self._session.execute(
            select(RunModel.country, RunModel.stats)
            .where(RunModel.status == RunStatus.DONE)
            .order_by(RunModel.id.desc())
        )
        return country_share_from([(c, stats or {}) for c, stats in rows.all()], country)

    async def unique_share(self, default: float) -> float:
        """Доля уникальных доменов для сметы до запуска — из своей истории."""
        rows = await self._session.execute(
            select(RunModel.stats)
            .where(RunModel.status == RunStatus.DONE)
            .order_by(RunModel.id.desc())
            .limit(UNIQUE_HISTORY_WINDOW * 2)
        )
        return unique_share_from([stats or {} for stats in rows.scalars().all()], default)

    async def stale(self, *, status: RunStatus, older_than: datetime) -> list[RunModel]:
        """Прогоны в этом состоянии, о которых давно ничего не слышно."""
        rows = await self._session.execute(
            select(RunModel)
            .where(RunModel.status == status, RunModel.updated_at < older_than)
            .order_by(RunModel.id)
        )
        return list(rows.scalars().all())

    #: Прогон в этих состояниях ещё намерен тратить: смета объявлена,
    #: работа не закрыта. Закрытые состояния удержания не несут — за них
    #: уже говорит журнал расхода.
    ACTIVE_STATUSES = (RunStatus.QUEUED, RunStatus.ESTIMATING, RunStatus.RUNNING)

    async def claimed_units(self, *, exclude_run_id: int | None = None) -> int:
        """Сколько юнитов уже обещали потратить идущие прогоны.

        **Зачем это вообще.** Остаток у Ahrefs — правда о прошлом: он
        показывает потраченное, а не обещанное. Идущий прогон обещал свою
        смету, но ещё не потратил её — для Ahrefs этих юнитов как будто нет,
        и следующий прогон планируется под них второй раз. Юниты не
        возвращаются, поэтому обещанное вычитается наравне с потраченным.

        **Удержание не хранится отдельным полем и не заводит своей таблицы.**
        Активный прогон и есть удержание: `estimated_units` минус то, что он
        уже потратил по журналу. Второй счётчик рядом с этими двумя неизбежно
        разошёлся бы с ними — ровно та причина, по которой остаток лимита
        тоже не хранится полем (см. `UsageRecordModel`).

        **Отрицательное удержание не считается.** Прогон, потративший больше
        сметы, ничего больше не держит, но и не возвращает: `max(0, …)`
        не даёт его перерасходу увеличить чужой бюджет.

        Зависшие прогоны сюда не попадают надолго: их закрывает сторож
        (`runs/lifecycle.py`), иначе смерть воркера навсегда съедала бы
        бюджет.
        """
        spent = (
            select(
                UsageRecordModel.run_id.label("run_id"),
                func.coalesce(func.sum(UsageRecordModel.units), 0).label("units"),
            )
            # ⚠ Только юниты Ahrefs. В том же столбце лежат токены судьи
            # с номером прогона, и без фильтра удержание обнулялось, как
            # только судья отработал: параллельный прогон мог занять бюджет,
            # который на деле ещё держится (найдено 23.09).
            .where(UsageRecordModel.provider == UsageProvider.AHREFS)
            .group_by(UsageRecordModel.run_id)
            .subquery()
        )
        claim = func.greatest(RunModel.estimated_units - func.coalesce(spent.c.units, 0), 0)
        statement = (
            select(func.coalesce(func.sum(claim), 0))
            .select_from(RunModel)
            .outerjoin(spent, spent.c.run_id == RunModel.id)
            .where(
                RunModel.status.in_(self.ACTIVE_STATUSES),
                RunModel.estimated_units.is_not(None),
            )
        )
        if exclude_run_id is not None:
            statement = statement.where(RunModel.id != exclude_run_id)
        return int(await self._session.scalar(statement) or 0)

    async def record_usage(
        self, *, run_id: int | None, operation: str, cost: UnitsCost
    ) -> UsageRecordModel:
        """Строка журнала расхода.

        Пишется даже при неизвестном расходе — с нулём и пометкой в операции:
        сам факт запроса важнее его цены, а пропуск строки скрыл бы, что
        запрос вообще был.
        """
        return usage.record(self._session, operation=operation, units=cost.billable, run_id=run_id)

    async def record_tokens(
        self, *, run_id: int | None, operation: str, tokens: int
    ) -> UsageRecordModel:
        """Строка расхода в токенах — так платит модель."""
        return usage.record(self._session, operation=operation, units=tokens, run_id=run_id)

    async def record_money(
        self, *, run_id: int | None, operation: str, amount_usd: float
    ) -> UsageRecordModel:
        """Строка расхода в деньгах — так платит источник выдачи.

        Отдельный метод, а не флаг у предыдущего: там единица расхода
        юнит, здесь доллар, и складывать их в одно поле значит получить
        счёт, в котором ничего не сходится ни с одним провайдером.
        """
        return usage.record(
            self._session, operation=operation, amount_usd=amount_usd, run_id=run_id
        )

    async def finish_run(
        self,
        run: RunModel,
        *,
        status: RunStatus,
        actual_units: int,
        stats: dict[str, Any],
    ) -> None:
        """Закрывает прогон. Статус ставится всегда, включая неуспешный:
        прогон, навсегда оставшийся «идёт», выглядит как зависший сервис."""
        run.status = status
        run.actual_units = actual_units
        run.stats = {
            **carried_notes(run.stats, finished=status is RunStatus.DONE),
            **stats,
        }
        await self._session.flush()

    async def spent_units(self, run_id: int) -> int:
        """Сколько юнитов Ahrefs прогон потратил по журналу — для сверки со сметой.

        ⚠ Только Ahrefs: у модели единица — токен, у выдачи — доллар, и в
        одну сумму с юнитами они не складываются. 23.09 токены судьи попали
        сюда, и «факт» прогона на Филиппины вышел 26 203 при 901 настоящем.
        """
        total = await self._session.scalar(
            select(func.coalesce(func.sum(UsageRecordModel.units), 0))
            .where(UsageRecordModel.run_id == run_id)
            .where(UsageRecordModel.provider == UsageProvider.AHREFS)
        )
        return int(total or 0)

    async def session_commit(self) -> None:
        """Фиксация чекпоинта. Вынесена методом, чтобы прогон не знал про
        устройство сессии и мог быть протестирован с любым хранилищем."""
        await self._session.commit()

    async def session_flush(self) -> None:
        """Отправляет накопленное в базу, не фиксируя транзакцию."""
        await self._session.flush()
