"""Настройки агента переписки: текущая версия этапа, история, новая версия.

Как у порогов (`runs/thresholds.py`): «сохранить» — завести новую версию,
а не поправить строку. **Пока версий нет, агент выключен**: черновик без цели
и без предела цены — письмо, которого никто не заказывал. Умолчания ниже —
отправная точка для экрана, а не настройки, по которым агент пишет.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import Stage
from backend.features.core.models.agent import AgentSettingsModel

#: Сколько последних версий отдаётся экрану. Остальные лежат в базе.
HISTORY = 10

#: Этапы, на которых агент ведёт переписку. Явным списком, а не всем `Stage`:
#: новый этап (продажи) не получает агента молча — у него нет ни цели, ни
#: умолчаний, и экран упал бы на первом же чтении.
AGENT_STAGES = (Stage.DONORS, Stage.ADVERTISERS)


class AgentSettingsConflictError(RuntimeError):
    """Две правки одного этапа разом: вторая не ложится поверх первой молча."""


class UnknownAgentStageError(LookupError):
    """Этап, на котором агент переписку не ведёт."""


@dataclass(frozen=True, slots=True)
class AgentSettings:
    """Как агент ведёт разговор на одном этапе."""

    enabled: bool
    goal: str
    tone: str
    points: tuple[str, ...]
    #: У доноров — не дороже, у рекламодателей — не дешевле. `None` — предела
    #: нет, и цену агент не обещает: её называет человек.
    price_limit_usd: Decimal | None
    stop_topics: tuple[str, ...]


_TONE = "Вежливо, коротко и по делу, как пишет живой менеджер. Без давления и канцелярита."
_HAND_OVER = (
    "Договор, счёт на компанию, юридические и налоговые вопросы",
    "Претензии, возвраты и споры об оплате",
    "Просьба созвониться или перейти в мессенджер",
)

_DEFAULTS = {
    Stage.DONORS: AgentSettings(
        enabled=True,
        goal=(
            "Узнать цену размещения статьи с одной ссылкой dofollow, срок публикации "
            "и способы оплаты — и договориться о размещении не дороже предела."
        ),
        tone=_TONE,
        points=(
            "Если цену не назвали — спросить цену гостевой статьи с одной ссылкой dofollow.",
            "Уточнить, помечается ли статья как спонсорская или рекламная.",
            "Спросить срок от оплаты до публикации и навсегда ли размещение.",
            "Цена выше предела — попросить скидку за постоянные заказы.",
        ),
        price_limit_usd=None,
        stop_topics=_HAND_OVER,
    ),
    Stage.ADVERTISERS: AgentSettings(
        enabled=True,
        goal=(
            "Продать размещение статьи со ссылкой на площадке из нашего письма: ответить "
            "на вопросы, назвать цену не ниже предела, договориться о теме и сроке."
        ),
        tone=_TONE,
        points=(
            "Назвать цену статьи с одной ссылкой dofollow и что в неё входит.",
            "Предложить тему статьи под их продукт или попросить их тему.",
            "Назвать срок публикации после оплаты.",
        ),
        price_limit_usd=None,
        stop_topics=(
            *_HAND_OVER,
            "Просьба показать статистику площадки сверх сказанного в письме",
        ),
    ),
}


def defaults(stage: Stage) -> AgentSettings:
    """С чего экран предлагает начать, пока версий этапа нет."""
    return _DEFAULTS[stage]


def settings_of(row: AgentSettingsModel) -> AgentSettings:
    return AgentSettings(
        enabled=row.enabled,
        goal=row.goal,
        tone=row.tone,
        points=tuple(row.points),
        price_limit_usd=row.price_limit_usd,
        stop_topics=tuple(row.stop_topics),
    )


def _versions(stage: Stage) -> Select[tuple[AgentSettingsModel]]:
    """Версии этапа, новая первой."""
    return (
        select(AgentSettingsModel)
        .where(AgentSettingsModel.stage == stage)
        .order_by(AgentSettingsModel.version.desc())
    )


class AgentSettingsRepository:
    """Версии настроек агента: прочитать текущую этапа, завести новую."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def current(self, stage: Stage) -> AgentSettingsModel | None:
        """Последняя версия этапа. `None` — не настраивали, агент выключен."""
        rows = await self._session.execute(_versions(stage).limit(1))
        return rows.scalar_one_or_none()

    async def history(self, stage: Stage, *, limit: int = HISTORY) -> Sequence[AgentSettingsModel]:
        rows = await self._session.execute(_versions(stage).limit(limit))
        return rows.scalars().all()

    async def save(
        self, stage: Stage, settings: AgentSettings, *, author: str
    ) -> AgentSettingsModel:
        """Новая версия настроек этапа.

        Номер версии — следующий за текущей. Две правки разом получили бы один
        номер; уникальность пары «этап, версия» не даёт второй лечь молча, и
        отказ говорит человеку, что делать.
        """
        previous = await self.current(stage)
        version = 1 if previous is None else previous.version + 1
        row = AgentSettingsModel(
            stage=stage,
            version=version,
            created_by=author,
            enabled=settings.enabled,
            goal=settings.goal,
            tone=settings.tone,
            points=list(settings.points),
            price_limit_usd=settings.price_limit_usd,
            stop_topics=list(settings.stop_topics),
        )
        # Своя точка сохранения: отказ откатывает только эту вставку, а не
        # всю транзакцию вызывающего — после него сессия годна к работе.
        try:
            async with self._session.begin_nested():
                self._session.add(row)
        except IntegrityError as exc:
            raise AgentSettingsConflictError(
                f"Настройки агента этапа «{stage.value}» только что сохранил кто-то другой "
                f"(версия {version}) — обновите страницу и сохраните ещё раз"
            ) from exc
        return row
