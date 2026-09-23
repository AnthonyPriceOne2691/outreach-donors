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

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.ahrefs.units import UnitsCost
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
        после выдачи: при постановке точного числа ещё нет."""
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
        run.stats = stats
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
