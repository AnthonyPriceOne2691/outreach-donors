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
from backend.features.ops import funds_watch, key_watch, mail_watch, stage_silence
from backend.features.ops.alarms import Alarm
from backend.features.runs.spending import crawl_spent_this_month
from backend.features.serp.dataforseo import SerpError
from backend.features.serp.factory import UnknownProviderError, build_provider

logger = logging.getLogger(__name__)

#: Сколько прогон может «идти» без единой отметки о жизни.
RUN_SILENCE_MINUTES = 30

#: За сколько последних обходов смотрим здоровье краула.
CRAWL_RUNS_WATCHED = 10

#: Код тревоги «провайдер не отвечает» — единственной, которую проход узнаёт из сети.
PROVIDERS_ALARM = "provider-unreachable"

#: Сколько минут подряд провайдер молчит, прежде чем это тревога (слово Anthony 15.10.2026:
#: тревога о неответе, который прошёл сам, — ложная). Таймаут, обрыв, 429 или 5xx проходят
#: за секунды; 15 минут и не один опрос — уже поломка.
PROVIDER_DOWN_MINUTES = 15

#: Провайдер с тревогой снова замолчал раньше этого срока — тот же сбой, тревога сразу. Дольше
#: «прошло» ленты сторожа и её опроса (`workers/reaper`: 20 и 10 минут, держит тест): иначе
#: мигающий провайдер давал бы «прошло» посреди нового молчания.
PROVIDER_RELAPSE_MINUTES = 30

#: Имя источника выдачи в тревоге.
SERP = "источник выдачи"


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
        # Кап Ahrefs: 80 % — предупреждение, 95 % — тревога, весь — «кап выбран» (16.10.2026).
        await key_watch.alarm(session, moment),
        # Счёт выдачи меньше порога — прогоны не запустятся, пока не пополнят (16.10.2026).
        funds_watch.alarm(),
        await _crawl_cap_reached(session),
        await _crawl_blocked(session),
    ]
    # Почта этапов со сторожем в политике — видимых этапов (П2б).
    found += await mail_watch.alarms(session, moment, stages=stages)
    return [alarm for alarm in found if alarm is not None]


@dataclass(frozen=True, slots=True)
class Miss:
    """Провайдер не ответил на бесплатный вопрос: кто, почему и пройдёт ли это само."""

    name: str
    reason: str
    #: Повтор не поможет: ключ, права, адрес API, источник не настроен.
    permanent: bool = False


@dataclass(slots=True)
class _Silence:
    """Провайдер молчит: с какого опроса, сколько опросов подряд, что сказал последний и вышла ли
    по нему тревога."""

    since: float
    misses: int
    last: Miss
    down: bool = False


@dataclass
class ProviderWatch:
    """Когда молчание провайдера — тревога, а не случайность (слово Anthony 15.10.2026).

    15.10.2026 рано утром сторож прислал «Провайдер не отвечает: Ahrefs», а следом «прошло»:
    Ahrefs снова ответил сам, прогонов не было — тревога ни к чему не звала. Правило теперь:

    - отказ, который сам не пройдёт (ключ отозван, нет прав, сменился адрес API, источник
      выдачи не настроен), — когда повторился на следующем опросе: один такой ответ бывает и у
      посредника перед API;
    - временный сбой — когда провайдер молчит `PROVIDER_DOWN_MINUTES` минут подряд и не один
      опрос; ответил — счёт сначала;
    - снова замолчал раньше `PROVIDER_RELAPSE_MINUTES` после своей тревоги — тот же сбой,
      тревога сразу.

    Свой на процесс (`WATCH`): сторож в процессе разбора и экран в процессе сервера видят каждый
    свои опросы. Часы монотонные: перевод системных часов срок не сбивает.
    """

    clock: Callable[[], float] = time.monotonic
    silent: dict[str, _Silence] = field(default_factory=dict)
    #: Когда провайдер последний раз был в тревоге — по нему узнаётся «тот же сбой».
    alarmed: dict[str, float] = field(default_factory=dict)

    def judge(self, misses: Sequence[Miss]) -> Alarm | None:
        """Тревога по ответам опроса — или `None`: все ответили или молчание пока случайно."""
        moment = self.clock()
        named = {miss.name: miss for miss in misses}
        for name in [name for name in self.silent if name not in named]:
            del self.silent[name]
        down: list[str] = []
        for name, miss in named.items():
            found = self.silent.setdefault(name, _Silence(since=moment, misses=0, last=miss))
            found.misses, found.last = found.misses + 1, miss
            words = self._words(found, moment)
            found.down = words is not None
            if words is not None:
                self.alarmed[name] = moment
                down.append(f"{name} — {words}")
        if not down:
            return None
        return Alarm(
            code=PROVIDERS_ALARM,
            title="Провайдер не отвечает",
            detail=(
                f"Не отвечает: {'; '.join(down)}. Прогон при недоступном остатке "
                "не запускается вовсе — узнать об этом лучше здесь, чем отказом "
                "посреди оплаченной работы"
            ),
        )

    @property
    def doubtful(self) -> bool:
        """Провайдер молчит, а тревоги по последнему опросу нет: опросить раньше обычного
        (`workers/reaper`). По опросу, а не по часам: срок вышел, а подтвердит его только опрос."""
        return any(not found.down for found in self.silent.values())

    def _words(self, found: _Silence, moment: float) -> str | None:
        """Почему молчание — тревога, словами; `None` — пока случайность."""
        reason = found.last.reason
        if found.misses >= 2 and found.last.permanent:
            return f"отказ, который сам не пройдёт ({reason})"
        minutes = int((moment - found.since) // 60)
        if found.misses >= 2 and minutes >= PROVIDER_DOWN_MINUTES:
            return f"{minutes} мин подряд ({reason})"
        alarmed = self.alarmed.get(found.last.name)
        if alarmed is not None and moment - alarmed < PROVIDER_RELAPSE_MINUTES * 60:
            return f"снова, вскоре после тревоги ({reason})"
        return None


#: Правило тревоги провайдеров этого процесса; тесты начинают с пустого (`tests/conftest.py`).
WATCH = ProviderWatch()


async def probe_providers() -> Alarm | None:
    """Провайдеры не отвечают на бесплатный вопрос — тревога по правилу `WATCH`.

    Отдельно от остальных правил намеренно: это единственная проверка,
    которая ходит в сеть. Смешав её с чтением базы, мы получили бы
    «тревоги», которые нельзя посчитать без интернета, — и набор
    тестов, которому нужен чужой сервер.

    Требование просит сигнал «API недоступен», и отдельно от прогона:
    узнать об этом отказом посреди платной работы — значит узнать
    поздно. Оба запроса бесплатные: остаток юнитов и остаток на счету.
    """
    return WATCH.judge(await ask_providers())


async def ask_providers() -> list[Miss]:
    """Один опрос: кто из провайдеров не ответил, почему и пройдёт ли это само. Причина — в
    тревогу: строка журнала пропадает вместе с контейнером на выкатке (так было 15.10.2026).

    Ответ Ahrefs запоминается для снимка ключа (`key_watch.heard`): опрос идёт без сессии,
    а в снимок его кладёт тот, у кого она есть, — после сети (16.10.2026)."""
    found: list[Miss] = []
    client = AhrefsClient()
    try:
        answer = await client.limits_and_usage()
    except AhrefsError as exc:
        logger.warning("сторож тишины: Ahrefs не ответил (%s)", exc)
        found.append(Miss("Ahrefs", _reason(exc), permanent=exc.permanent))
    except OSError as exc:
        logger.warning("сторож тишины: Ahrefs не ответил (%s)", exc)
        found.append(Miss("Ahrefs", _reason(exc)))
    else:
        key_watch.heard(answer)
    finally:
        await client.aclose()

    serp = await _serp_silent()
    if serp is not None:
        found.append(serp)
    return found


async def _serp_silent() -> Miss | None:
    """Молчит ли источник выдачи. `None` — ответил или счёта у него нет."""
    ahrefs = AhrefsClient()
    try:
        provider = build_provider(ahrefs)
    except UnknownProviderError as exc:
        # Настройка, а не авария: строка в журнале без трассировки, а не
        # ERROR с полным стеком каждые десять минут. Сама не пройдёт — тревога сразу.
        await ahrefs.aclose()
        logger.warning("сторож тишины: источник выдачи не настроен (%s)", exc)
        return Miss(SERP, f"не настроен: {_reason(exc)}", permanent=True)
    balance = getattr(provider, "balance", None)
    try:
        if balance is not None:
            # Остаток — и сторожу счёта: меньше порога — «пополните счёт» (16.10.2026).
            funds_watch.remember(float(await balance()))
    except SerpError as exc:
        logger.warning("сторож тишины: источник выдачи не ответил (%s)", exc)
        return Miss(SERP, _reason(exc), permanent=exc.permanent)
    except OSError as exc:
        logger.warning("сторож тишины: источник выдачи не ответил (%s)", exc)
        return Miss(SERP, _reason(exc))
    finally:
        await ahrefs.aclose()
        aclose = getattr(provider, "aclose", None)
        if aclose is not None:
            await aclose()
    return None


def _reason(exc: BaseException) -> str:
    """Причина неответа одной строкой и коротко — для тревоги в Telegram и на «Обзоре»."""
    words = " ".join(str(exc).split())
    return (words or type(exc).__name__)[:160]


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
