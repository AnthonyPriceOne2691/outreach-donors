"""Сторож тишины: поломки, которые выглядят как «ничего не происходит».

Самый дорогой класс отказов в этом сервисе — не падение, а молчание.
Вебхук платформы сменил адрес, приём ответов отвалился, процесс добивок
не поднялся после перезапуска: сервис при этом зелёный, экраны рисуются,
ошибок нет. Отличить «доноры не отвечают» от «мы перестали слышать
ответы» по одному экрану нельзя — нужно правило, знающее, чего ждать.

**Каждая тревога — это «при X не может быть Y».** Не «давно не было
событий» (может, и писем не было), а «письма ушли два дня назад,
а событий доставки нет ни одного». Тишина сама по себе не поломка;
поломка — тишина там, где обязано быть шумно.

**Сторож ничего не чинит и никого не останавливает.** Он только
называет. Автоматика, которая по своему подозрению останавливает
рассылку, однажды остановит её на ровном месте — а в день, когда
она понадобится, будет выключена.

**Тревоги о почте продаж — только с правом «Продажи»** (решение Anthony 10.10.2026,
П2б): почта считается по этапам, которые видит спрашивающий (`stages`). Доставка, ответы
и добивки — у каждого этапа свои, с кодом и этапом тревоги (`stage_silence.py`, ревью
стыков 12.10.2026): тишина одного направления больше не прячет поломку другого. Общие
тревоги — о прогонах, капах и обходе — этапа не имеют и видны всем.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import ahrefs as ahrefs_cfg
from backend.features.ahrefs.client import AhrefsClient, AhrefsError
from backend.features.core.domain import (
    CrawlOutcome,
    CrawlStatus,
    RunStatus,
    Stage,
    StopReason,
)
from backend.features.core.models.crawl import CrawlRunModel
from backend.features.core.models.run import RunModel
from backend.features.ops import mail_watch, stage_silence
from backend.features.ops.alarms import Alarm
from backend.features.runs.spending import ahrefs_spent_this_month, crawl_spent_this_month
from backend.features.serp.dataforseo import SerpError
from backend.features.serp.factory import UnknownProviderError, build_provider

logger = logging.getLogger(__name__)

#: Сколько прогон может «идти» без единой отметки о жизни.
RUN_SILENCE_MINUTES = 30

#: За сколько последних обходов смотрим здоровье краула.
CRAWL_RUNS_WATCHED = 10

#: Код тревоги «провайдер не отвечает» — единственной, которую проход узнаёт из сети.
PROVIDERS_ALARM = "provider-unreachable"


async def alarms(
    session: AsyncSession, *, stages: Collection[Stage], now: datetime | None = None
) -> list[Alarm]:
    """Всё, что сейчас молчит не по делу; почта этапов — тех, что видит спрашивающий
    (`stages`). Умолчания у этапов нет (ревью продаж к #304): фоновый проход передаёт все."""
    moment = now or datetime.now(UTC)
    # Доставка, ответы и добивки — у каждого видимого этапа свои (П2б, ревью стыков 12.10).
    found: list[Alarm | None] = [*await stage_silence.alarms(session, moment, stages=stages)]
    found += [
        await _runs_stuck(session, moment),
        await _cap_reached(session),
        await _crawl_cap_reached(session),
        await _crawl_blocked(session),
    ]
    # Почта этапов со сторожем в политике — видимых этапов (П2б).
    found += await mail_watch.alarms(session, moment, stages=stages)
    return [alarm for alarm in found if alarm is not None]


async def probe_providers() -> Alarm | None:
    """Провайдеры не отвечают на бесплатный вопрос.

    Отдельно от остальных правил намеренно: это единственная проверка,
    которая ходит в сеть. Смешав её с чтением базы, мы получили бы
    «тревоги», которые нельзя посчитать без интернета, — и набор
    тестов, которому нужен чужой сервер.

    Требование просит сигнал «API недоступен», и отдельно от прогона:
    узнать об этом отказом посреди платной работы — значит узнать
    поздно. Оба запроса бесплатные: остаток юнитов и остаток на счету.
    """
    dead: list[str] = []

    client = AhrefsClient()
    try:
        await client.limits_and_usage()
    except (AhrefsError, OSError) as exc:
        logger.warning("сторож тишины: Ahrefs не ответил (%s)", exc)
        dead.append("Ahrefs")
    finally:
        await client.aclose()

    serp = await _serp_silent()
    if serp is not None:
        dead.append(serp)

    if not dead:
        return None
    return Alarm(
        code=PROVIDERS_ALARM,
        title="Провайдер не отвечает",
        detail=(
            f"Не отвечает: {', '.join(dead)}. Прогон при недоступном остатке "
            "не запускается вовсе — узнать об этом лучше здесь, чем отказом "
            "посреди оплаченной работы"
        ),
    )


async def _serp_silent() -> str | None:
    """Молчит ли источник выдачи. `None` — ответил или счёта у него нет."""
    ahrefs = AhrefsClient()
    try:
        provider = build_provider(ahrefs)
    except UnknownProviderError as exc:
        # Настройка, а не авария: строка в журнале без трассировки, а не
        # ERROR с полным стеком каждые десять минут.
        await ahrefs.aclose()
        logger.warning("сторож тишины: источник выдачи не настроен (%s)", exc)
        return "источник выдачи (не настроен)"
    balance = getattr(provider, "balance", None)
    try:
        if balance is not None:
            await balance()
    except (SerpError, OSError) as exc:
        logger.warning("сторож тишины: источник выдачи не ответил (%s)", exc)
        return "источник выдачи"
    finally:
        await ahrefs.aclose()
        aclose = getattr(provider, "aclose", None)
        if aclose is not None:
            await aclose()
    return None


async def _runs_stuck(session: AsyncSession, moment: datetime) -> Alarm | None:
    """Прогон «идёт», но давно не подавал признаков жизни."""
    edge = moment - timedelta(minutes=RUN_SILENCE_MINUTES)
    stuck = await session.scalar(
        select(func.count(RunModel.id)).where(
            RunModel.status == RunStatus.RUNNING,
            # Отметка о жизни — это `updated_at`: прогон трогает строку
            # каждым ударом, и отдельного поля для того же смысла заводить
            # не стали (`features/runs/lifecycle`).
            RunModel.updated_at < edge,
        )
    )
    if not stuck:
        return None
    return Alarm(
        code="runs-stuck",
        title="Прогон идёт, но молчит",
        detail=(
            f"{stuck} прогонов не подавали признаков жизни дольше "
            f"{RUN_SILENCE_MINUTES} минут. Их должен разобрать отдельный процесс; "
            "если тревога держится — он не запущен"
        ),
    )


async def _crawl_blocked(session: AsyncSession) -> Alarm | None:
    """Обход упирается в защиту сайтов.

    Требование просит при доле отказов выше 30% «паузу и алерт». Паузу
    обход ставит себе сам и останавливается; тревоги до этого правила
    не было — останов виден был только в логе, а сторож про краул
    не знал вовсе.

    Считается по последним обходам, а не за всё время: доля за всю
    историю прячет защиту, включившуюся вчера. Только законченным:
    у идущего обхода исхода ещё нет, и он разбавлял бы долю закрытых.
    """
    rows = (
        (
            await session.execute(
                select(CrawlRunModel)
                .where(CrawlRunModel.status == CrawlStatus.DONE)
                .order_by(CrawlRunModel.id.desc())
                .limit(CRAWL_RUNS_WATCHED)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return None

    stopped = [row for row in rows if row.stop_reason is StopReason.UNHEALTHY]
    blocked = [row for row in rows if row.outcome is CrawlOutcome.BLOCKED]
    if not stopped and len(blocked) * 2 < len(rows):
        return None

    return Alarm(
        code="crawl-blocked",
        title="Обход упирается в защиту",
        detail=(
            f"Из последних {len(rows)} обходов {len(blocked)} закрылись целиком"
            + (f", {len(stopped)} остановлены по доле отказов" if stopped else "")
            + ". Дальше это лечится не повтором, а следующим уровнем каскада: "
            "браузером (CRAWL_BROWSER_ENABLED) или резидентными прокси (CRAWL_PROXY_URLS)"
        ),
    )


async def _cap_reached(session: AsyncSession) -> Alarm | None:
    """Месячный кап выбран. Прогоны молчат не потому, что сломались."""
    spent = await ahrefs_spent_this_month(session)
    if spent < ahrefs_cfg.UNITS_CAP:
        return None
    return Alarm(
        code="cap-reached",
        title="Месячный кап выбран",
        detail=(
            f"Потрачено {spent} юнитов при капе {ahrefs_cfg.UNITS_CAP}. "
            "Новые прогоны не запустятся до первого числа или до поднятия капа — "
            "это не поломка, но выглядит одинаково"
        ),
    )


def _setting_names(exc: ValueError) -> str:
    """Имена настроек из отказа pydantic — без значений; не pydantic — «CRAWL_*»."""
    errors = getattr(exc, "errors", None)
    found = (
        sorted({str(error["loc"][0]) for error in errors() if error.get("loc")})
        if callable(errors)
        else []
    )
    return ", ".join(found) or "CRAWL_*"


async def _crawl_cap_reached(session: AsyncSession) -> Alarm | None:
    """Свой потолок Этапа 2 выбран: прокси и рендеринг не зовутся до первого числа, и обходы
    за защитой закрываются — не потому, что сломались (`crawl/spend.py`).

    Конфиг обхода грузится здесь, а не при импорте: в процессе сторожа идёт и повтор
    передачи лидов продаж, и опечатка в `CRAWL_*` роняла бы его целиком. Битый конфиг —
    своя тревога с именами настроек, без значений: в значении бывает пароль прокси."""
    try:
        from backend.config import crawl as crawl_cfg  # noqa: PLC0415 — см. выше
    except ValueError as exc:
        names = _setting_names(exc)
        logger.warning("сторож тишины: настройки обхода не читаются (%s)", names)
        return Alarm(
            code="crawl-config-broken",
            title="Настройки обхода не читаются",
            detail=(
                f"В .env ошибка в настройках обхода ({names}): обходчик не стартует, "
                "и свой потолок Этапа 2 не проверяется. Значение поправить и пересоздать контейнеры"
            ),
        )
    cap = crawl_cfg.MONTH_CAP_USD
    if cap is None:
        return None
    spent = await crawl_spent_this_month(session)
    if spent < Decimal(str(cap)):
        return None
    return Alarm(
        code="crawl-cap-reached",
        title="Потолок Этапа 2 на обход выбран",
        detail=(
            f"Прокси и рендеринг обошлись в {spent:.2f} $ при своём потолке {cap:.2f} $. "
            "До первого числа или до поднятия потолка платные уровни обхода не зовутся, "
            "и закрытые сайты остаются необойдёнными — это не поломка, но выглядит одинаково"
        ),
    )


@dataclass
class Journal:
    """Строки журнала прохода сторожа: о каждой тревоге — не чаще раза в `every` секунд, о новой
    — сразу; «тихо и правильно» — так же. `every` 0 — каждый проход.

    Фоновый проход стал чаще (`workers/reaper.WATCHDOG_INTERVAL_SEC`), а журнал — нет: строка о
    действующей тревоге на каждом проходе писалась бы в пять раз чаще прежнего, ничего не
    добавляя (ревью #337). Часы монотонные."""

    every: float = 0.0
    clock: Callable[[], float] = time.monotonic
    #: Когда о тревоге (по коду) была последняя строка.
    written: dict[str, float] = field(default_factory=dict)
    #: Когда была последняя строка «тихо и правильно».
    calm_at: float | None = None

    def write(self, told: Sequence[Alarm]) -> None:
        """Строки о тревогах прохода `told` — те, чей срок пришёл."""
        moment = self.clock()
        self.written = {code: at for code, at in self.written.items() if moment - at < self.every}
        if not told and (self.calm_at is None or moment - self.calm_at >= self.every):
            logger.info("сторож тишины: тихо и правильно")
            self.calm_at = moment
        for alarm in told:
            if alarm.code not in self.written:
                logger.warning("сторож тишины: %s — %s", alarm.title, alarm.detail)
                self.written[alarm.code] = moment


async def with_providers(
    found: Sequence[Alarm],
    *,
    probe: Callable[[], Awaitable[Alarm | None]] | None = None,
    journal: Journal | None = None,
) -> list[Alarm]:
    """Сетевая часть прохода сторожа для фонового процесса: опрос провайдеров и строки
    журнала. Без сессии намеренно: зовётся после того, как тревоги базы посчитаны и сессия
    закрыта (`workers/reaper.watch`), — иначе соединение висело бы «idle in transaction» на
    время чужих ответов.

    Провайдеры проверяются здесь — в фоне это уместно: два бесплатных запроса ничего не
    стоят. `probe` — опрос; по умолчанию — `probe_providers` сейчас, фоновый проход
    подставляет свой — с ответом между опросами (`workers/reaper.Providers`). `journal` —
    строки журнала; по умолчанию — все тревоги прохода, фоновый проход подставляет свой — не
    чаще прежнего (`workers/reaper.JOURNAL`).
    """
    unreachable = await (probe or probe_providers)()
    told = list(found) if unreachable is None else [unreachable, *found]
    (journal or Journal()).write(told)
    return told
