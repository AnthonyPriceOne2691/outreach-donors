"""Сторож Hunter: снимок учётки из опроса и тревога «осталось мало» (слово Anthony 02.11.2026:
«главное, чтобы сервис сигнализировал»).

В октябре потолок проверок адреса кончился молча: об этом знал только итог очистки. Теперь — как
у ключа Ahrefs (`key_watch.py`): с `WARN_PERCENT` предупреждение, с `ALARM_PERCENT` тревога, весь —
«выбран». Уровень — в коде тревоги: новый код лента говорит сразу, а «прошло» о прежнем — когда
выйдет его тишина (`alarm_feed.Feed`). Тревога одна за проход — самого высокого уровня.

**Упереться можно в две вещи, и считать надо обе.** Наш потолок месяца (`core/hunter_cap.py`,
проверки адреса с первого числа) и учётка — общий счёт с соседней системой и поиском адресов,
который обнуляется в свой день. Уровень — старший из двух, при равных — то, что кончится раньше:
маленький потолок иначе заслонял бы учётку, выбранную почти до конца. Сверху вниз это не тише
правила «что кончится раньше», а громче только там, где лимит и правда почти выбран.

**Ноль — не «выбрано», а решение человека** (`hunter_cap`): проверки остановлены с экрана, и
тревога об этом ни к чему не зовёт; потолка числом нет — говорит экран, а тревогу держит учётка.

**Опрос — вместе с Ahrefs, но отказ Hunter не «провайдер не отвечает»** (`ask`). Та тревога
говорит, что прогоны не запускаются, а от Hunter они не зависят: поиск адреса и проверка ждут
своего повтора. Отказ — строка журнала, снимок остаётся прежним, экран показывает час ответа.
Ответ ложится в снимок тем же путём, что ответ Ahrefs: сеть — без сессии, запись — у того, у кого
она есть, короткой транзакцией после сети (`keep_heard`).

**Запрос бесплатный и один на опрос**, с бюджетом времени: опрос идёт и под экраном «Обзор», и
двадцать секунд таймаута Hunter с повторами держали бы экран.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime, time
from decimal import Decimal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import contacts as contacts_cfg
from backend.features.contacts.hunter_account import AccountSnapshot, remember, snapshot
from backend.features.contacts.provider import HunterAccount, HunterProvider, ProviderError
from backend.features.core.domain import Stage
from backend.features.core.hunter_cap import VERIFY_OPERATION, verify_month_cap
from backend.features.ops.alarms import Alarm, Concern, cause, no_cause
from backend.features.runs.ahrefs_key import day_words
from backend.features.runs.spending import units_this_month
from backend.shared.numbers import spaced

logger = logging.getLogger(__name__)

#: С какой доли — предупреждение и с какой — тревога, целыми процентами: те же, что у Ahrefs.
WARN_PERCENT = 80
ALARM_PERCENT = 95
FULL_PERCENT = 100

#: Коды по уровням: новый код лента говорит сразу.
WARN_CODE = "hunter-80"
ALARM_CODE = "hunter-95"
REACHED_CODE = "hunter-reached"

#: Сколько секунд опрос ждёт Hunter целиком, с повторами: бесплатный вопрос не должен держать
#: экран «Обзор», а следующий опрос — через десять минут.
ASK_BUDGET_SEC = 15.0

#: Во что упрёмся: наш потолок месяца или учётка.
MONTH, ACCOUNT = "month", "account"

#: Где сдвигают упор — и что значит «выбран» для работы.
_HOW_TO_RAISE = {
    MONTH: "поднять потолок на экране «Расход»",
    ACCOUNT: "докупить кредиты в кабинете Hunter",
}
_REACHED = {
    MONTH: ("Потолок проверок адреса выбран", "платные проверки адресов стоят до первого числа"),
    ACCOUNT: ("Учётка Hunter выбрана", "поиск и проверка адресов стоят до сброса учётки"),
}

#: Пометка предупреждения: проверки идут, остаток есть — повода нет.
WARNED = no_cause("проверки идут: это предупреждение заранее, остаток ещё есть")


@dataclass
class Heard:
    """Последний ответ Hunter об учётке в этом процессе — до записи в снимок."""

    account: HunterAccount | None = None
    at: datetime | None = None


#: Услышанное опросом этого процесса; тесты начинают с пустого (`tests/conftest.py`).
HEARD = Heard()


def _http() -> httpx.AsyncClient:
    """Соединение опроса — своей функцией: тесты подставляют транспорт провода."""
    return httpx.AsyncClient()


async def ask() -> None:
    """Спросить учётку для снимка. Ключа нет — спрашивать нечем, это рабочий режим и не повод
    шуметь; отказ и непонятый ответ — строка журнала, снимок остаётся прежним."""
    if not contacts_cfg.HUNTER_API_KEY:
        return
    try:
        async with asyncio.timeout(ASK_BUDGET_SEC), _http() as http:
            account = await HunterProvider(http).account()
    except (ProviderError, OSError) as exc:
        logger.warning("сторож Hunter: учётка не ответила (%r) — снимок остаётся прежним", exc)
        return
    except ValueError as exc:
        # Ключ не ASCII: заголовок не собрался. Текст такого исключения содержит сам ключ.
        logger.warning(
            "сторож Hunter: ключ Hunter не годится для заголовка (%s) — проверить "
            "CONTACTS_HUNTER_API_KEY",
            type(exc).__name__,
        )
        return
    HEARD.account, HEARD.at = account, datetime.now(UTC)


async def keep_heard(session: AsyncSession) -> bool:
    """Услышанный ответ — в снимок учётки транзакцией `session`; фиксирует вызывающий.
    `False` — класть нечего: опрос Hunter не спрашивал или ответ уже положен."""
    account, at = HEARD.account, HEARD.at
    if account is None or at is None:
        return False
    HEARD.account = HEARD.at = None
    await remember(session, account, at=at)
    return True


@dataclass(frozen=True, slots=True)
class Standing:
    """Hunter на эту минуту: снимок учётки, действующий потолок проверок и выбрано за месяц."""

    account: AccountSnapshot | None
    #: Потолок месяца; строка — почему числа нет.
    cap: int | str
    #: Проверок адреса с первого числа — по журналу расхода.
    spent: int

    @property
    def month_left(self) -> int | None:
        """Осталось до потолка; числа нет — `None`."""
        return None if isinstance(self.cap, str) else max(0, self.cap - self.spent)

    @property
    def month_percent(self) -> int | None:
        """Выбрано от потолка целыми процентами вниз. Числа нет или ноль — `None`: считать не
        от чего, а ноль — проверки остановлены человеком."""
        if isinstance(self.cap, str) or self.cap == 0:
            return None
        return min(FULL_PERCENT, self.spent * 100 // self.cap)

    @property
    def account_percent(self) -> int | None:
        """Выбрано от счёта учётки; снимка нет — `None`."""
        return None if self.account is None else self.account.main.used_percent


async def standing(session: AsyncSession, moment: datetime) -> Standing:
    """Hunter на эту минуту — одно чтение у экрана и у тревоги: числа не разойдутся."""
    return Standing(
        account=await snapshot(session),
        cap=await verify_month_cap(session),
        spent=await units_this_month(session, VERIFY_OPERATION, now=moment),
    )


def _level(percent: int) -> int:
    """Уровень доли: 0 — ниже предупреждения."""
    for level in (FULL_PERCENT, ALARM_PERCENT, WARN_PERCENT):
        if percent >= level:
            return level
    return 0


def _top(found: Standing, *, sales: bool) -> tuple[str, int] | None:
    """Что тревожит: упор и доля — старший уровень, при равных то, что кончится раньше. Месяц
    считают только с правом «Продажи»: проверки адреса — их расход (`runs/spending._seen`)."""
    left: dict[str, Decimal] = {}
    percents: dict[str, int] = {}
    if sales and found.month_percent is not None and found.month_left is not None:
        left[MONTH], percents[MONTH] = Decimal(found.month_left), found.month_percent
    if found.account is not None and found.account_percent is not None:
        left[ACCOUNT] = found.account.account.verifications.remaining
        percents[ACCOUNT] = found.account_percent
    if not percents:
        return None
    binding = max(percents, key=lambda name: (_level(percents[name]), -left[name]))
    return binding, percents[binding]


def amount(value: Decimal) -> str:
    """Число счётчика: разряды неразрывным пробелом, дробная часть — только если она есть."""
    whole, tail = f"{value.normalize():f}".partition(".")[::2]
    return spaced(int(whole)) if not tail else f"{spaced(int(whole))},{tail}"


def reset_words(found: AccountSnapshot) -> str:
    """Сброс учётки словами, днём — как его называет Hunter: «19 ноября»."""
    return day_words(datetime.combine(found.account.reset_on, time.min, tzinfo=UTC))


def _month_words(found: Standing) -> str:
    """Потолок месяца: сколько осталось и что граница — первое число, а не сброс учётки."""
    cap = found.cap if isinstance(found.cap, int) else 0
    line = (
        f"проверок адреса в этом месяце осталось {spaced(found.month_left or 0)} из {spaced(cap)} "
        f"— выбрано {found.month_percent or 0}%. Потолок считается с первого числа"
    )
    if found.account is None:
        return line
    return f"{line}, а учётка обнуляется {reset_words(found.account)} — это разные границы"


def _account_words(found: AccountSnapshot) -> str:
    """Учётка: общий счёт и сколько проверок на него хватит, сброс и что она общая."""
    account = found.account
    verifications = amount(account.verifications.remaining)
    if account.credits is None:
        counted = f"проверок адреса осталось {verifications} из "
        counted += amount(account.verifications.available)
    else:
        counted = (
            f"кредитов осталось {amount(account.credits.remaining)} из "
            f"{amount(account.credits.available)} — хватит на {verifications} проверок адреса"
        )
    return (
        f"на учётке Hunter {counted}, выбрано {found.main.used_percent}%, сброс "
        f"{reset_words(found)}. Учётка общая с соседней системой и поиском адресов"
    )


def _words(found: Standing, binding: str) -> str:
    if binding == ACCOUNT and found.account is not None:
        return _account_words(found.account)
    return _month_words(found)


def _short(binding: str) -> Concern:
    """Пометка тревоги 95 %: следующая очистка может не уложиться — что сдвинуть."""
    return cause(
        "следующая очистка или поиск адресов может не уложиться в остаток",
        f"{_HOW_TO_RAISE[binding]}, если проверки нужны до сброса",
    )


def _reached(binding: str, words: str, stage: Stage | None) -> Alarm:
    title, stopped = _REACHED[binding]
    return Alarm(
        code=REACHED_CODE,
        title=title,
        detail=f"Выбрано: {words}. Это не поломка, но выглядит одинаково",
        stage=stage,
        concern=cause(stopped, f"дождаться сброса или {_HOW_TO_RAISE[binding]}"),
    )


async def alarm(
    session: AsyncSession, moment: datetime, *, stages: Collection[Stage]
) -> Alarm | None:
    """Тревога о Hunter — самого высокого уровня, или `None`: выбрано меньше `WARN_PERCENT` или
    судить не о чем (снимка нет, потолок не числом). Тревога о потолке месяца — этапа продаж:
    только с их правом; об учётке — общая: она и у поиска адресов доноров."""
    found = await standing(session, moment)
    top = _top(found, sales=Stage.SALES in stages)
    if top is None:
        return None
    binding, percent = top
    level = _level(percent)
    if level == 0:
        return None
    stage = Stage.SALES if binding == MONTH else None
    words = _words(found, binding)
    if level == FULL_PERCENT:
        return _reached(binding, words, stage)
    warn = level == WARN_PERCENT
    return Alarm(
        code=WARN_CODE if warn else ALARM_CODE,
        title=f"Hunter выбран на {level}% и больше",
        detail=f"{'Предупреждение' if warn else 'Тревога'}: {words}; {_HOW_TO_RAISE[binding]}",
        stage=stage,
        concern=WARNED if warn else _short(binding),
    )
