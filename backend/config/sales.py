"""Модуль «Продажи»: выключатель, проверяльщик адресов, CRM для лидов и бот телемаркетолога.

**По умолчанию выключен.** Продажи пишут живым людям от имени компании,
и включение — решение человека для конкретного развёртывания, а не умолчание
кода. Сейчас выключать нечего: в модуле только данные (гипотезы и лиды)
и команды консоли. Выключатель читают срезы, которые приносят работу
продаж; первой — проверка ключей продаж на старте.

**Агент переписки на этапе продаж — своим тумблером, тоже выключен**
(`SALES_AGENT_ENABLED`): без него строки продаж нет в реестре этапов агента
(`agent/stages.AGENT_STAGES`) — черновиков ответов лидам модель не пишет, судья
не зовётся, в группу продаж о черновиках не пишется, экран настроек агента этапа
продаж не показывает. Включает владелец, когда агент замерен на наборе. Ведёт он
первые ответы лида в диалоге — `SALES_AGENT_MAX_TURNS` (2: ТЗ, «максимум второй»).

**Проверяльщик адресов по умолчанию выдуманный (`fixture`).** Живой стоит
денег с ключа, общего с соседней системой, и на него переключает человек:
`SALES_VERIFIER_PROVIDER=live`. Ключ — тот же `CONTACTS_HUNTER_API_KEY`,
что у поиска адресов: сервис один, второй переменной для него не заводим. У живого —
обязательный месячный потолок проверок `SALES_VERIFIER_MONTH_CAP` (счётчик проверок свой).

**Kommo по умолчанию тоже выдуманный (`fixture`).** Живой пишет сделки
в чужую CRM, и отозвать записанное нельзя: на `live` переключает человек,
а без поддомена, ключа, воронки, этапа или ответственного клиент не
собирается (`features/sales/kommo.build_kommo`). Задан адрес шлюза агентства
(`SALES_KOMMO_GATEWAY_URL`) — `live` идёт через шлюз, а не в API v4 напрямую: тогда
нужны ключ шлюза, источник, тег, воронка, этап и поддомен (ссылка на сделку).
Обязательные поля сделки — `SALES_KOMMO_LEAD_FIELDS`, постоянными значениями: форма
проверяется при сборке клиента `live`; шлюзу их не передать — в его запросе таких полей нет.

**Негодное значение выключает продажи, а не роняет процесс.** Этот модуль импортирует и общий
код — реестр этапов агента (`agent/stages.py`), задачи общего воркера и процесс разбора мёртвых
(`workers/reaper.py`): отказ при импорте ронял бы на старте API, общий воркер и reaper — приём
ответов и события доноров, а не только продажи. Поэтому чтение не падает: число вне
границ, не «true/false», чужой режим судьи, окно отправки или время отчёта не по образцу — поле
берёт умолчание, а модуль продаж выключен целиком (`BROKEN` не пуст): `ENABLED`, агент продаж,
автоотправка и автосборка. Громко — строкой журнала уровня ERROR при импорте, с именем, значением
и тем, что годится, в каждом процессе, который читает настройки продаж: она раньше настройки его
журнала, и печатает её запасной обработчик `logging` (stderr контейнера). Экран и отказы продаж
говорят словами без имён настроек (`features/sales/connection.gaps`). Общий старт идёт.

**Предел ходов агента и поля сделки — без разбора при импорте.** Опечатка в них, найденная
при импорте, выключила бы весь модуль продаж, а не один черновик или одну передачу. Здесь обе
настройки — текстом как есть, а разбирает их тот, кто читает: предел — задача ответа
(`features/sales/agent_turns.max_turns`: негодный — черновика нет, причина словами у ответа),
поля — сборка клиента `live` (`features/sales/kommo`: негодные — `ConfigError` словами на первой
передаче, как у прочих настроек `live`).

**Окно отправки — рабочие часы получателя по его часам** (Ф4, 4.3): дни «1-5» или «1,3,5»
(1 — понедельник), часы «09:00-17:00» (час одной цифрой — «9:00-17:00» — тот же); разбор при
старте — опечатка не ждёт первого письма.
**Телемаркетологу пишет свой бот продаж, а не бот тревог.** Сообщение о лиде —
бизнес-событие для сотрудника, тревога — сигнал владельцу о поломке. Отозванный
или заблокированный бот продаж не должен глушить тревогу о том, что лид не
доставлен: она уходит другим ботом (`ALERT_TELEGRAM_*`). Адрес Bot API — общий,
`ALERT_TELEGRAM_API`. Пустые значения — не ошибка конфига: передача лида тогда
кончается «не доставлено» и тревогой, а не падением на старте.

**Ссылка на диалог** собирается от `SALES_APP_URL` — адреса сервиса, который
открывает телемаркетолог: до подключения Kommo и при его сбое она заменяет
ссылку на сделку.

**Судья черновика продаж по умолчанию режет (`enforce`)** — в отличие от судьи
площадки (`JUDGE_MODE`, наблюдение по умолчанию): черновик продаж читает живой
человек, и выдуманная цена в нём дороже правки. `shadow` — для замера судьи на
живых черновиках: вердикт ложится в журнал и в попытки черновика, но черновик
не задерживает. Судья, который не смог проверить, отдаёт черновик человеку
в обоих режимах.

**Наборы eval судьи — вне репозитория** (он публичный): каталоги
`SALES_JUDGE_GOLDEN_DIR` (опора на базу) и `SALES_STYLE_GOLDEN_DIR` (стиль),
файлы и хэши — в манифестах `scripts/data/sales_*_golden.manifest.json`.

**Автоотправка и автосборка — своими тумблерами, обе выключены** (`SALES_AUTOSEND`,
`SALES_AUTOBUILD`): первые письма без человека уходят незнакомым людям, а тексты, переписанные
моделью, встают в очередь без человека — включает владелец. Проходы — в процессе разбора
(`workers/reaper.py`); пауза между первыми письмами ящика — `SALES_AUTOSEND_GAP_MIN` минут со
случайным разбросом (`features/sales/autosend.py`).

**Судья сегмента сбора базы по умолчанию наблюдает (`shadow`)** — как судья площадки
(`JUDGE_MODE`), тем же перечислением: отказ модели, включённый до замера, резал бы
вслепую. В наблюдении вердикт пишется, а «отказ» модели отдаёт сайт человеку с пометкой
«отрезал бы»; `enforce` режет; `off` — модель не зовётся, каждый сайт решает человек.
Набор eval судьи сегмента — вне репозитория, каталог `SALES_SEGMENT_GOLDEN_DIR`.

**Недельный отчёт в группу продаж по умолчанию не уходит** (`SALES_WEEKLY_REPORT_AT`, срез 6.3):
всё, что продажи шлют наружу без человека, по умолчанию выключено. Включает человек — днём и
временем по Москве, рекомендуемое «MON 09:00» («MON 9:00» — то же время: час одной цифрой прочесть
иначе нельзя); разбор при старте — опечатка не ждёт понедельника. Экран отчёта и выгрузка работают
и без него.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import time
from enum import Enum, StrEnum

from pydantic import Field
from pydantic.fields import FieldInfo

from backend.config._base import DomainSettings, Fallback, spared
from backend.config.judge import JudgeMode

logger = logging.getLogger(__name__)


class SalesJudgeMode(StrEnum):
    """Что судья черновика продаж делает со своим вердиктом.

    ENFORCE — нарушения возвращают черновик писателю на правку, не сошлось — человеку.
    SHADOW — вердикт пишется в журнал и в попытки черновика, но черновик не задерживает.
    """

    SHADOW = "shadow"
    ENFORCE = "enforce"


class _Sales(DomainSettings):
    enabled: bool = Field(default=False, validation_alias="SALES_ENABLED")
    agent_enabled: bool = Field(default=False, validation_alias="SALES_AGENT_ENABLED")
    # Сколько ответов лида в диалоге агент ведёт черновиком (ТЗ: «первый ответ, максимум
    # второй»): со следующего — человек. Строкой: разбор — при чтении (шапка модуля).
    agent_max_turns: str = Field(default="2", validation_alias="SALES_AGENT_MAX_TURNS")
    # `fixture` — вердикты по правилам из адреса, без сети и денег;
    # `live` — Hunter Email Verifier тем же ключом, что ступень 3 поиска.
    verifier_provider: str = Field(default="fixture", validation_alias="SALES_VERIFIER_PROVIDER")
    # Месячный потолок проверок адреса у `live` — в пределах проверок тарифа Hunter. Числом без
    # умолчания: не задан — платная часть очистки встаёт словами. Строкой: разбор — при чтении
    # (`features/sales/verify_cap.month_cap`), опечатка не роняет общий старт.
    verifier_month_cap: str = Field(default="", validation_alias="SALES_VERIFIER_MONTH_CAP")
    # `fixture` — сделки с выдуманными номерами, без сети; `live` — закрытая
    # интеграция аккаунта Kommo долгоживущим ключом (секрет: в `.env`).
    kommo_provider: str = Field(default="fixture", validation_alias="SALES_KOMMO_PROVIDER")
    kommo_subdomain: str = Field(default="", validation_alias="SALES_KOMMO_SUBDOMAIN")
    kommo_token: str = Field(default="", validation_alias="SALES_KOMMO_TOKEN")
    # Номера из Kommo — строкой: пустое значение образца не роняет чтение
    # настроек, а негодное называет фабрика клиента словами до первого запроса.
    kommo_pipeline_id: str = Field(default="", validation_alias="SALES_KOMMO_PIPELINE_ID")
    kommo_status_id: str = Field(default="", validation_alias="SALES_KOMMO_STATUS_ID")
    kommo_responsible_user_id: str = Field(
        default="", validation_alias="SALES_KOMMO_RESPONSIBLE_USER_ID"
    )
    # Шлюз агентства вместо API v4: адрес и ключ (секрет: в `.env`), значение поля
    # источника и тег сделки и контакта. Адрес пуст — `live` идёт в API v4 напрямую.
    kommo_gateway_url: str = Field(default="", validation_alias="SALES_KOMMO_GATEWAY_URL")
    kommo_gateway_key: str = Field(default="", validation_alias="SALES_KOMMO_GATEWAY_KEY")
    kommo_source: str = Field(default="", validation_alias="SALES_KOMMO_SOURCE")
    kommo_tag: str = Field(default="", validation_alias="SALES_KOMMO_TAG")
    # Обязательные поля сделки — постоянными значениями: JSON «номер поля → значение»
    # (`{"712345": "Email"}`); разбор — при сборке клиента `live`. Шлюз их не принимает.
    kommo_lead_fields: str = Field(default="", validation_alias="SALES_KOMMO_LEAD_FIELDS")
    send_days: str = Field(default="1-5", validation_alias="SALES_SEND_DAYS")
    send_hours: str = Field(default="09:00-17:00", validation_alias="SALES_SEND_HOURS")
    # На сколько минут от открытия окна расходятся письма, ждавшие его. 0 — все в минуту
    # открытия; разброс шире окна обрезает само окно (`core/window.defer_until`): «на весь день»
    # (300–480) годится. Больше суток — опечатка (ревью стыков 13.10).
    send_spread_min: int = Field(
        default=30, ge=0, le=1440, validation_alias="SALES_SEND_SPREAD_MIN"
    )
    # Доля жалоб в окне последних писем ящика, с которой он встаёт на паузу (Ф4, 4.5b), —
    # задолго до 0,3% почтовых сервисов: на 50 письмах одна жалоба. Решение владельца. Порог —
    # `ceil(доля · 50)` жалоб (`outreach/health`): любая доля до 0.02 — та же одна жалоба, что у
    # 0.001. Долей, а не процентом: «0.1» или «0.3», задуманные как проценты, ослабили бы паузу
    # молча — больше 0.02 выключает продажи громко (ревью стыков 13.10).
    complaint_pause: float = Field(
        default=0.001, gt=0, le=0.02, validation_alias="SALES_COMPLAINT_PAUSE"
    )

    # Уверенность вида ответа, ниже которой ответ ждёт человека (ручная очередь
    # продаж). Предложение до замера на наборе владельца — как у разбора цены.
    reply_confidence: float = Field(
        default=0.80, ge=0.0, le=1.0, validation_alias="SALES_REPLY_CONFIDENCE"
    )
    # Каталог внешнего набора ответов для eval (`scripts/eval_sales_reply.py
    # --golden`): обезличенная переписка лежит вне репозитория — он публичный.
    golden_dir: str = Field(default="", validation_alias="SALES_GOLDEN_DIR")
    # Автоответ в треде продаж цепочку не останавливает, а переносит следующий шаг:
    # до даты возвращения из текста или на столько дней от автоответа.
    ooo_delay_days: int = Field(default=7, ge=1, le=60, validation_alias="SALES_OOO_DELAY_DAYS")
    # Вызовов модели на письмо очереди в одной сборке: ни один набор переписанных зон не лёг
    # в коридор отличия — новый вызов с подсказкой (`features/sales/corridor.py`); 1 — без
    # повтора. Больше десяти — опечатка: подсказка не помогла за десять, поможет шаблон.
    rewrite_attempts: int = Field(default=3, ge=1, le=10, validation_alias="SALES_REWRITE_ATTEMPTS")
    # Первые письма по расписанию, без человека (`features/sales/autosend.py`): проход раз в
    # столько секунд, с ящика — не больше письма за проход и пауза между его первыми письмами.
    autosend: bool = Field(default=False, validation_alias="SALES_AUTOSEND")
    autosend_pass_sec: int = Field(
        default=60, ge=10, le=3600, validation_alias="SALES_AUTOSEND_PASS_SEC"
    )
    autosend_gap_min: int = Field(
        default=15, ge=1, le=240, validation_alias="SALES_AUTOSEND_GAP_MIN"
    )
    # Очередь пополняется сама, когда писем меньше, чем ящики продаж могут отправить за сутки.
    autobuild: bool = Field(default=False, validation_alias="SALES_AUTOBUILD")

    # Бот продаж (секрет: в `.env`). Телемаркетолог один раз жмёт Start, его
    # chat id печатает `outreach sales-telegram-chat-id`.
    telegram_bot_token: str = Field(default="", validation_alias="SALES_TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", validation_alias="SALES_TELEGRAM_CHAT_ID")
    # Копия каждого сообщения о лиде — в группу продаж; выключается настройкой.
    telegram_group_chat_id: str = Field(default="", validation_alias="SALES_TELEGRAM_GROUP_CHAT_ID")
    telegram_group_copy: bool = Field(default=True, validation_alias="SALES_TELEGRAM_GROUP_COPY")
    # Адрес сервиса для ссылки на диалог: `https://…` без косой черты в конце.
    app_url: str = Field(default="", validation_alias="SALES_APP_URL")
    # Когда недельный отчёт уходит в группу продаж: день и время по Москве, рекомендуемое
    # «MON 09:00». Пусто (по умолчанию) — не уходит: наружу без человека — только по решению.
    weekly_report_at: str = Field(default="", validation_alias="SALES_WEEKLY_REPORT_AT")

    # Судья черновика агента продаж: режим (`enforce` — режет, `shadow` — замер) и
    # каталоги наборов его eval вне репозитория.
    judge_mode: SalesJudgeMode = Field(
        default=SalesJudgeMode.ENFORCE, validation_alias="SALES_JUDGE_MODE"
    )
    judge_golden_dir: str = Field(default="", validation_alias="SALES_JUDGE_GOLDEN_DIR")
    style_golden_dir: str = Field(default="", validation_alias="SALES_STYLE_GOLDEN_DIR")
    # Каталог набора для прогона агента на накопленных ответах (`scripts/sales_replay.py`):
    # набор из настоящих писем живёт вне репозитория; пусто — не задан.
    replay_dir: str = Field(default="", validation_alias="SALES_REPLAY_DIR")
    # Ворота автоответа агента продаж: решений по ситуации не меньше этого, прежде чем экран
    # «Агент: качество» судит её доли (`agent/calibration.py`); меньше — «данных мало». 30:
    # ноль ложных молчаний на 30 — с уверенностью 95% их не больше 10% (правило трёх), а
    # доля «без правки» 80% на 30 решениях — плюс-минус 7 пунктов. Меньше пяти — доля из
    # горсти решений, больше тысячи — опечатка.
    autoreply_min_n: int = Field(
        default=30, ge=5, le=1000, validation_alias="SALES_AUTOREPLY_MIN_N"
    )
    # Судья сегмента сбора базы (срез 6.1): режим и каталог набора его eval вне репозитория.
    segment_judge_mode: JudgeMode = Field(
        default=JudgeMode.SHADOW, validation_alias="SALES_SEGMENT_JUDGE_MODE"
    )
    segment_golden_dir: str = Field(default="", validation_alias="SALES_SEGMENT_GOLDEN_DIR")
    # Платная ступень поиска адреса у сбора (Hunter, ключ общий с донорами). Выключена: ключ,
    # включённый для доноров, иначе молча включил бы её и сбору — до тысяч поисков на прогон.
    segment_paid_contacts: bool = Field(
        default=False, validation_alias="SALES_SEGMENT_PAID_CONTACTS"
    )


def _days(text: str) -> frozenset[int]:
    """«1-5» или «1,3,5» → дни недели с нуля (0 — понедельник), как `date.weekday()`."""
    refusal = f"SALES_SEND_DAYS «{text}»: дни — числа 1…7, например «1-5»"
    found: set[int] = set()
    for part in text.split(","):
        first, _, last = part.strip().partition("-")
        try:
            found.update(range(int(first) - 1, int(last or first)))
        except ValueError as exc:
            raise ValueError(refusal) from exc
    if not found or not found <= set(range(7)):
        raise ValueError(refusal)
    return frozenset(found)


#: Час одной цифрой: «9:00» — то же, что «09:00» (`_clock`).
_ONE_DIGIT_HOUR = re.compile(r"\d:\d\d")


def _clock(text: str) -> time:
    """«09:00» → время; час одной цифрой («9:00») — тот же час: прочесть его иначе нельзя."""
    text = text.strip()
    return time.fromisoformat(f"0{text}" if _ONE_DIGIT_HOUR.fullmatch(text) else text)


def _hours(text: str) -> tuple[time, time]:
    """«09:00-17:00» → открытие и закрытие окна; закрытие позже открытия."""
    start, _, end = text.partition("-")
    try:
        opens, closes = _clock(start), _clock(end)
    except ValueError as exc:
        raise ValueError(f"SALES_SEND_HOURS «{text}»: часы — «09:00-17:00»") from exc
    if opens >= closes:
        raise ValueError(f"SALES_SEND_HOURS «{text}»: окно закрывается раньше, чем открывается")
    return opens, closes


#: Дни недели в `SALES_WEEKLY_REPORT_AT` — с понедельника, как `date.weekday()`.
WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


def _weekly_at(text: str) -> tuple[int, time] | None:
    """«MON 09:00» → день недели с нуля (0 — понедельник) и время по Москве; пусто — `None`."""
    if not text.strip():
        return None
    refusal = f"SALES_WEEKLY_REPORT_AT «{text}»: день и время по Москве, например «MON 09:00»"
    day, _, clock = text.strip().partition(" ")
    try:
        at = _clock(clock)
    except ValueError as exc:
        raise ValueError(refusal) from exc
    if day.upper() not in WEEKDAYS or at.tzinfo is not None:
        raise ValueError(refusal)
    return WEEKDAYS.index(day.upper()), at


#: Начало строки журнала о негодных настройках продаж — её ищут в журнале процесса.
BROKEN_SAID = (
    "ПРОДАЖИ ВЫКЛЮЧЕНЫ — настройка продаж с ошибкой: модуль продаж не работает до исправления, "
    "общий старт идёт"
)


#: Границы числа словами: `ge=1, le=240` — «от 1 до 240», `gt=0` — «больше 0».
_BOUNDS = (("gt", "больше"), ("ge", "от"), ("lt", "меньше"), ("le", "до"))


def _expected(field: FieldInfo) -> str:
    """Что годится полю — словами: «true или false», «одно из: shadow, enforce», «целое от 1 до
    240», «число больше 0 до 0.01»."""
    kind: object = field.annotation
    if kind is bool:
        return "true или false"
    if isinstance(kind, type) and issubclass(kind, Enum):
        return "одно из: " + ", ".join(str(item.value) for item in kind)
    if kind not in (int, float):
        return "значение по образцу `.env.example`"
    bounds = [
        f"{word} {getattr(rule, key):g}"
        for key, word in _BOUNDS
        for rule in field.metadata
        if hasattr(rule, key)
    ]
    return " ".join(["целое" if kind is int else "число", *bounds])


#: Негодная настройка продаж берёт умолчание своего поля — и выключает модуль целиком (`BROKEN`).
_FALLBACKS = {
    str(field.validation_alias): Fallback(
        field.default, _expected(field), "умолчание поля, модуль продаж выключен"
    )
    for field in _Sales.model_fields.values()
}


def _parsed[T](parse: Callable[[str], T], name: str) -> T:
    """Текстовая настройка, разобранная при старте; негодная — умолчание поля и отказ разбора
    словами в `BROKEN`."""
    field = _Sales.model_fields[name]
    try:
        return parse(getattr(_s, name))
    except ValueError as exc:
        BROKEN[str(field.validation_alias)] = str(exc)
        # Строкой общего разбора без падения (`config/_base.spared`); трассировка тут — лишняя.
        logger.error(  # noqa: TRY400
            "НАСТРОЙКА НЕ ПРОЧИТАНА — %s; взято: умолчание поля, модуль продаж выключен",
            exc,
            extra={"setting": str(field.validation_alias)},
        )
        return parse(field.default)


# Окружение и `.env` — как у прочих доменов (`DomainSettings`); негодное не роняет импорт.
_s, _unread = spared(_Sales, _FALLBACKS)
#: Негодные настройки продаж: имя → что с ней словами. Не пусто — модуль продаж выключен целиком
#: (тумблеры ниже), строка журнала — при импорте (`BROKEN_SAID`); общий старт идёт.
BROKEN: dict[str, str] = {
    name: f"{name}=«{raw[:40]}» — нужно {_FALLBACKS[name].wants}" for name, raw in _unread.items()
}
SEND_DAYS: frozenset[int] = _parsed(_days, "send_days")
SEND_OPENS, SEND_CLOSES = _parsed(_hours, "send_hours")
#: Когда недельный отчёт уходит в группу продаж: день недели (0 — понедельник) и время по
#: Москве (`features/sales/weekly_jobs.py`). `None` — в Telegram не уходит; так по умолчанию.
WEEKLY_REPORT_AT: tuple[int, time] | None = _parsed(_weekly_at, "weekly_report_at")
#: Тумблеры продаж — только при разобранных настройках.
_FINE = not BROKEN

ENABLED: bool = _s.enabled and _FINE
#: Строка продаж в реестре этапов агента переписки (`agent/stages.SALES_STAGE`).
AGENT_ENABLED: bool = _s.agent_enabled and _FINE
#: Ответов лида в диалоге, на которые агент пишет черновик, — текстом как есть: разбирает
#: задача ответа (`features/sales/agent_turns.max_turns`).
AGENT_MAX_TURNS: str = _s.agent_max_turns.strip()
VERIFIER_PROVIDER: str = _s.verifier_provider
#: Месячный потолок проверок адреса — текстом как есть: разбирает `sales/verify_cap.month_cap`.
VERIFIER_MONTH_CAP: str = _s.verifier_month_cap.strip()

KOMMO_PROVIDER: str = _s.kommo_provider
KOMMO_SUBDOMAIN: str = _s.kommo_subdomain.strip().lower()
KOMMO_TOKEN: str = _s.kommo_token.strip()
KOMMO_PIPELINE_ID: str = _s.kommo_pipeline_id.strip()
KOMMO_STATUS_ID: str = _s.kommo_status_id.strip()
KOMMO_RESPONSIBLE_USER_ID: str = _s.kommo_responsible_user_id.strip()
#: Адрес шлюза агентства: задан — `live` идёт через шлюз (`features/sales/kommo_gateway.py`).
KOMMO_GATEWAY_URL: str = _s.kommo_gateway_url.strip()
KOMMO_GATEWAY_KEY: str = _s.kommo_gateway_key.strip()
KOMMO_SOURCE: str = _s.kommo_source.strip()
KOMMO_TAG: str = _s.kommo_tag.strip()
#: Обязательные поля сделки — текстом как есть: разбирает сборка клиента `live`
#: (`features/sales/kommo.lead_fields`), в тело сделки API v4 их кладёт `kommo_live.py`.
KOMMO_LEAD_FIELDS: str = _s.kommo_lead_fields.strip()

SEND_SPREAD_MIN: int = _s.send_spread_min
COMPLAINT_PAUSE: float = _s.complaint_pause
#: Порог уверенности вида ответа лида (`features/sales/replies.py`).
REPLY_CONFIDENCE: float = _s.reply_confidence
#: Где лежит внешний набор ответов для eval. Пусто — не задан.
GOLDEN_DIR: str = _s.golden_dir.strip()
#: На сколько дней автоответ переносит следующий шаг продаж, если даты в нём нет.
OOO_DELAY_DAYS: int = _s.ooo_delay_days
#: Вызовов модели на письмо очереди продаж в одной сборке (`features/sales/corridor.py`).
REWRITE_ATTEMPTS: int = _s.rewrite_attempts
#: Автоотправка первых писем продаж по расписанию (`features/sales/autosend.py`).
AUTOSEND: bool = _s.autosend and _FINE
AUTOSEND_PASS_SEC: int = _s.autosend_pass_sec
#: Пауза между первыми письмами одного ящика, минут, — до разброса ±50%.
AUTOSEND_GAP_MIN: int = _s.autosend_gap_min
#: Автосборка очереди продаж (`features/sales/autobuild.py`).
AUTOBUILD: bool = _s.autobuild and _FINE
#: Как часто автосборка смотрит, хватает ли очереди: сборка — минуты модели, очередь — на сутки.
AUTOBUILD_PASS_SEC = 10 * 60
JUDGE_MODE: SalesJudgeMode = _s.judge_mode
JUDGE_GOLDEN_DIR: str = _s.judge_golden_dir.strip()
STYLE_GOLDEN_DIR: str = _s.style_golden_dir.strip()
REPLAY_DIR: str = _s.replay_dir.strip()
AUTOREPLY_MIN_N: int = _s.autoreply_min_n
#: Режим судьи сегмента сбора базы (`features/sales/segment_judge.py`).
SEGMENT_JUDGE_MODE: JudgeMode = _s.segment_judge_mode
SEGMENT_GOLDEN_DIR: str = _s.segment_golden_dir.strip()
#: Платная ступень поиска адреса у сбора (`segment_run._contacts`): выключена — только бесплатные.
SEGMENT_PAID_CONTACTS: bool = _s.segment_paid_contacts

#: Не больше стольких запросов в секунду. Предел Kommo из его документации —
#: семь в секунду с одного IP для любой интеграции; чаще — 429, а частые 429
#: закрывают доступ целиком (403).
KOMMO_RATE_PER_SEC = 7
#: Сколько ждать ответа Kommo на один запрос.
KOMMO_TIMEOUT_SEC = 20.0

TELEGRAM_BOT_TOKEN: str = _s.telegram_bot_token.strip()
TELEGRAM_CHAT_ID: str = _s.telegram_chat_id.strip()
TELEGRAM_GROUP_CHAT_ID: str = _s.telegram_group_chat_id.strip()
TELEGRAM_GROUP_COPY: bool = _s.telegram_group_copy
APP_URL: str = _s.app_url.strip().rstrip("/")

#: Сколько ждать Telegram на одну попытку. Попыток три (`features/sales/telegram`).
TELEGRAM_TIMEOUT_SEC = 10.0

#: Через сколько проход по расписанию повторяет передачу, которую Kommo не принял.
#: Лидов единицы в день: четверть часа не теряет лида и не дёргает Kommo зря.
HANDOFF_RETRY_SEC = 15 * 60
#: Как часто проход заглядывает в передачи (`features/sales/handoff_jobs.retry_pass`).
HANDOFF_PASS_SEC = 5 * 60
#: Сколько задача держит передачу. Дольше — задача умерла, передачу берёт следующая:
#: три попытки Kommo и три Telegram укладываются в минуты.
HANDOFF_CLAIM_SEC = 15 * 60

if BROKEN:
    # При импорте — в каждом процессе, который читает настройки продаж, и раньше настройки его
    # журнала: строку печатает запасной обработчик `logging` (stderr контейнера).
    logger.error("%s: %s", BROKEN_SAID, "; ".join(BROKEN.values()), extra={"settings": [*BROKEN]})
