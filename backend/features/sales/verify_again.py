"""Перепроверка адреса, у которого Hunter «ещё проверяет» (202), — сама, по сроку и со счётом.

Разбор прогона сбора 20.10: после переспросов прохода (`hunter_codes.asked`) лид оставался
«новым» со словами «повторим следующей очисткой», а очистку запускает только кнопка вкладки
лидов — лиды висели неделю. Теперь:

- **срок и счёт — у лида** (`verify_columns.VerifyAgainMixin`): «ещё проверяет» — счёт +1 и срок
  через `SALES_VERIFY_AGAIN_MIN` минут, словами лида со временем по UTC (`later`); любой другой
  исход очистки — счёт ноль, срока нет (`cleaning._row`). Прежний вердикт лида цел: его держат
  правила первого письма и второго `unknown` (`unconfirmed.py`);
- **не больше `SALES_VERIFY_AGAIN_TRIES` перепроверок**: следующее «ещё проверяет» — отказ
  `unconfirmed` словами «адрес не проверить» (как отказ домену, `hunter_codes.refused`): вечного
  круга нет, и лид не висит «новым» без срока;
- **перепроверку ставит очередь** (`deferred.py`: `enqueue_at` и планировщик воркера долгих задач
  продаж) — задачу `verify_again_jobs.recheck` на самый ранний срок в базе (`plan`); она
  чистит лидов, чей срок настал, тем же проходом очистки (`cleaning.clean_leads`) и ставит
  следующую;
- **деньги**: переспрос, пока Hunter проверяет, бесплатен (шапка `hunter_codes`); вердикт —
  проверка под месячным потолком, как у любой очистки (`verify_cap.capped`): потолок выбран —
  перепроверка встаёт его словами, как очистка.

Срок — в базе: задачу, потерянную очередью, поставит любой следующий проход с отложенными
лидами, а отчёт прогона называет срок и даёт «Повторить сейчас» (`segment_waits.py`). Кнопка
«Очистить» вкладки лидов спрашивает таких лидов сразу: для них это и есть «повторить сейчас».
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import sales as cfg
from backend.features.sales import deferred
from backend.features.sales.clean_report import NOT_VERIFIED, Outcome
from backend.features.sales.models import LeadStatus, RejectionReason, SalesLeadModel

#: Задача перепроверки — путём строкой: очередь импортирует её в воркере продаж.
JOB = "backend.features.sales.verify_again_jobs.recheck"
#: Приставка номера задачи; хвост — минута срока (`deferred.job_id`).
PREFIX = "sales-verify-again-"
#: Задача берёт и тех, чей срок — в пределах минуты после её: номер задачи — от минуты срока.
EARLY = timedelta(minutes=1)
#: Начало слов лида, чью перепроверку ждут, — после «проверка не выполнена: ».
CHECKING = "Hunter ещё проверяет адрес"


def checking(tries: int, at: datetime) -> str:
    """Слова лида: Hunter ещё проверяет — когда перепроверка и какая она по счёту."""
    return (
        f"{NOT_VERIFIED}: {CHECKING} — перепроверка сама около {at:%H:%M} UTC "
        f"({tries} из {cfg.VERIFY_AGAIN_TRIES})"
    )


def gave_up() -> str:
    """Почему лид отклонён: Hunter не закончил проверку и после всех перепроверок."""
    return (
        f"адрес не проверить: Hunter не закончил проверку и после {cfg.VERIFY_AGAIN_TRIES} "
        f"перепроверок через {cfg.VERIFY_AGAIN_MIN} мин"
    )


def later(lead: SalesLeadModel, moment: datetime) -> Outcome:
    """Исход лида, за которого Hunter «ещё проверяет»: статус и вердикт прежние, срок и счёт —
    следующие; сверх `SALES_VERIFY_AGAIN_TRIES` перепроверок — отказ словами."""
    tries = lead.verify_tries + 1
    if tries > cfg.VERIFY_AGAIN_TRIES:
        return Outcome(lead.id, LeadStatus.REJECTED, RejectionReason.UNCONFIRMED, gave_up())
    at = moment + timedelta(minutes=cfg.VERIFY_AGAIN_MIN)
    kept = lead.verification_status is not None
    return Outcome(
        lead.id, lead.status, note=checking(tries, at), kept=kept, again_at=at, tries=tries
    )


def due(now: datetime) -> ColumnElement[bool]:
    """Лиды, чья отложенная перепроверка настала (с запасом в минуту), — кроме отклонённых: их
    больше не проверяют."""
    at = SalesLeadModel.verify_again_at
    return at.is_not(None) & (at <= now + EARLY) & (SalesLeadModel.status != LeadStatus.REJECTED)


async def next_at(session: AsyncSession) -> datetime | None:
    """Самый ранний срок отложенной перепроверки в базе; нет таких — пусто."""
    return await session.scalar(
        select(func.min(SalesLeadModel.verify_again_at)).where(
            SalesLeadModel.status != LeadStatus.REJECTED
        )
    )


async def plan(session: AsyncSession, *, now: datetime) -> str | None:
    """Поставить задачу перепроверки на самый ранний срок в базе. Номер поставленной; `None` —
    сроков нет, задача на эту минуту уже стоит или очередь не ответила."""
    when = await next_at(session)
    if when is None:
        return None
    return deferred.plan(PREFIX, JOB, when, now=now)
