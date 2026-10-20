"""Перепроверка адреса, у которого Hunter «ещё проверяет» (202), — сама, по сроку, в окне времени.

Разбор прогона сбора 20.10: после переспросов прохода (`hunter_codes.asked`) лид оставался
«новым» со словами «повторим следующей очисткой», а очистку запускает только кнопка вкладки
лидов — лиды висели неделю. Теперь:

- **срок и окно — у лида** (`verify_columns.VerifyAgainMixin`): «ещё проверяет» — срок через
  `SALES_VERIFY_AGAIN_MIN` минут, словами лида с датой и временем по UTC (`later`); первое
  «ещё проверяет» открывает окно — `SALES_VERIFY_AGAIN_TRIES` таких перепроверок. Вердикт или
  отказ домену — срок и окно сняты (`cleaning._row`). Прежний вердикт лида цел: его держат
  правила первого письма и второго `unknown` (`unconfirmed.py`);
- **предел — временем, а не числом заходов** (ревью 20.10): «Очистить» или «Повторить сейчас»
  раньше срока окно не сокращает. «Ещё проверяет» после окна — отказ `unconfirmed` словами «адрес
  не проверить» (как отказ домену, `hunter_codes.refused`): вечного круга нет, и лид не висит
  «новым» без срока;
- **временный отказ посреди окна — как «ещё проверяет»** (`failed`): сеть, 5xx, частота и потолок
  срок не снимают — перепроверка придёт снова; после окна лид остаётся в прежнем статусе словами
  отказа, без срока;
- **перепроверку ставит очередь** (`deferred.py`: `enqueue_at` и планировщик воркера долгих задач
  продаж) — задачу `verify_again_jobs.recheck` на самый ранний срок в базе (`plan`). Лидов прогона
  сбора, чей проход идёт (конец пуст и пульс свежий), задача не берёт (`free`): их чистит сам
  проход, а срок поставит его конец;
- **окно и срок — от ответа 202**, а не от начала долгого прохода (`answered`);
- **деньги**: переспрос, пока Hunter проверяет, бесплатен (шапка `hunter_codes`); вердикт —
  проверка под месячным потолком, как у любой очистки (`verify_cap.capped`).

Срок — в базе: задачу, потерянную очередью, ставит следующий проход с отложенными лидами и любая
перепроверка, а отчёт прогона показывает срок и «наступил» (`segment_waits.py`).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import sales as cfg
from backend.features.sales import deferred
from backend.features.sales.clean_report import NOT_VERIFIED, Outcome
from backend.features.sales.models import LeadStatus, RejectionReason, SalesLeadModel
from backend.features.sales.segment_models import SalesSegmentCandidateModel as Candidate
from backend.features.sales.segment_models import SalesSegmentRunModel as Run

#: Задача перепроверки — путём строкой: очередь импортирует её в воркере продаж.
JOB = "backend.features.sales.verify_again_jobs.recheck"
#: Приставка номера задачи; хвост — минута срока (`deferred.job_id`).
PREFIX = "sales-verify-again-"
#: Задача берёт и тех, чей срок — в пределах минуты после её: номер задачи — от минуты срока.
EARLY = timedelta(minutes=1)
#: Слова строки лида, чью перепроверку ждут, — в отчёте прогона и в словах лида.
CHECKING = "Hunter ещё проверяет адрес"
#: Проход прогона «идёт», пока его строка отмечалась не раньше этого (пульс — каждые 15 с, в
#: очереди задача может постоять): дольше тишина — задачу потеряли или воркер убит, и задача
#: перепроверки берёт лидов прогона сама (ревью 20.10: иначе — навсегда).
QUIET = timedelta(minutes=10)

#: Часы прохода — косвенным именем: тест двигает их, а не время всего процесса.
_clock = time.monotonic


def clock() -> float:
    """Отметка начала прохода очистки — для `answered`."""
    return _clock()


def answered(moment: datetime, started: float) -> datetime:
    """Когда пришёл ответ — начало прохода (`moment`) и прошедшие целые минуты: окно и срок
    «ещё проверяет» — от ответа, а не от начала долгого прохода (ревью 20.10); минута — шаг
    сроков и номеров задач."""
    return moment + timedelta(minutes=int((_clock() - started) // 60))


def interval() -> timedelta:
    return timedelta(minutes=cfg.VERIFY_AGAIN_MIN)


def window() -> timedelta:
    """Окно перепроверок от первого «ещё проверяет»: столько сроков подряд."""
    return interval() * cfg.VERIFY_AGAIN_TRIES


def _when(at: datetime) -> str:
    return f"{at:%d.%m %H:%M} UTC"


def checking(at: datetime, until: datetime) -> str:
    """Слова лида: Hunter ещё проверяет — когда перепроверка и до какого момента ждём."""
    return (
        f"{NOT_VERIFIED}: {CHECKING} — перепроверка сама около {_when(at)}, ждём до {_when(until)}"
    )


def failed(why: str, at: datetime, until: datetime) -> str:
    """Слова лида: посреди окна сервис отказал — перепроверка всё равно придёт."""
    return f"{NOT_VERIFIED}: {why} — перепроверка сама около {_when(at)}, ждём до {_when(until)}"


def gave_up() -> str:
    """Почему лид отклонён: Hunter не закончил проверку и за всё окно."""
    minutes = cfg.VERIFY_AGAIN_MIN * cfg.VERIFY_AGAIN_TRIES
    return (
        f"адрес не проверить: Hunter не закончил проверку и за {minutes} мин — "
        f"перепроверяли каждые {cfg.VERIFY_AGAIN_MIN} мин"
    )


def later(lead: SalesLeadModel, moment: datetime, *, failure: str | None = None) -> Outcome:
    """Исход лида, за которого Hunter «ещё проверяет» (`failure` пусто) или, посреди окна, сервис
    временно отказал (`failure` — почему): статус и вердикт прежние, срок — следующий, окно — от
    первого «ещё проверяет». Окно кончилось: «ещё проверяет» — отказ словами, отказ сервиса —
    прежний статус словами отказа, без срока."""
    since = lead.verify_since or moment
    until = since + window()
    kept = lead.verification_status is not None
    if moment >= until:
        if failure is None:
            return Outcome(lead.id, LeadStatus.REJECTED, RejectionReason.UNCONFIRMED, gave_up())
        return Outcome(lead.id, lead.status, note=f"{NOT_VERIFIED}: {failure}", kept=kept)
    at = min(moment + interval(), until)
    note = checking(at, until) if failure is None else failed(failure, at, until)
    return Outcome(lead.id, lead.status, note=note, kept=kept, again_at=at, since=since)


def in_window(lead: SalesLeadModel) -> bool:
    """Перепроверка лида по сроку идёт: временный отказ сервиса её не обрывает."""
    return lead.verify_since is not None


def due(now: datetime) -> ColumnElement[bool]:
    """Лиды, чья отложенная перепроверка настала (с запасом в минуту), — кроме отклонённых (их
    больше не проверяют) и лидов прогона, чей проход идёт (`free`)."""
    at = SalesLeadModel.verify_again_at
    timely = at.is_not(None) & (at <= now + EARLY)
    return timely & (SalesLeadModel.status != LeadStatus.REJECTED) & free(now)


def ahead(now: datetime) -> ColumnElement[bool]:
    """Срок перепроверки лида ещё не настал: «Повторить сейчас» его не берёт (отчёт прогона)."""
    at = SalesLeadModel.verify_again_at
    return at.is_not(None) & (at > now)


def free(now: datetime) -> ColumnElement[bool]:
    """Лид не из прогона сбора, чей проход идёт: конец пуст (`finished_at`), и строка прогона
    отмечалась недавно (`QUIET`). Такого чистит сам проход, и задача перепроверки его не берёт —
    две записи в отчёт прогона затёрли бы одна другую; срок поставит конец прохода
    (`segment_jobs.run_once`). Проход, чья строка давно молчит, — не идёт: задачу потеряли или
    воркер убит, и лиды не должны ждать вечно."""
    going = (
        select(Candidate.id)
        .join(Run, Run.id == Candidate.run_id)
        .where(
            Candidate.lead_id == SalesLeadModel.id,
            Run.finished_at.is_(None),
            Run.updated_at > now - QUIET,
        )
    )
    return ~going.exists()


async def next_at(session: AsyncSession, now: datetime) -> datetime | None:
    """Самый ранний срок отложенной перепроверки в базе, который возьмёт задача; нет — пусто."""
    return await session.scalar(
        select(func.min(SalesLeadModel.verify_again_at)).where(
            SalesLeadModel.status != LeadStatus.REJECTED, free(now)
        )
    )


async def plan(
    session: AsyncSession, *, now: datetime, soonest: timedelta = deferred.SOONEST
) -> str | None:
    """Поставить задачу перепроверки на самый ранний срок в базе, не раньше `soonest` от `now`.
    Номер поставленной; `None` — сроков нет, задача на эту минуту уже стоит или очередь не
    ответила."""
    when = await next_at(session, now)
    if when is None:
        return None
    return deferred.plan(PREFIX, JOB, when, now=now, soonest=soonest)
