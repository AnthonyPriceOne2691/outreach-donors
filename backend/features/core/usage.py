"""Строка журнала расхода — одна на весь сервис.

Расход пишут два разных места: прогон (юниты Ahrefs и выдача) и письма
(токены модели, факт отправки). Общего у них ровно одно, и оно важное:
**операция обязана числиться за провайдером.** Молча отнести незнакомую
операцию к Ahrefs нельзя — счёт разойдётся, и разойдётся тихо, потому что
ключ Ahrefs общий с соседней системой.

Поэтому список операций живёт здесь, а не в репозитории прогонов: второй
экземпляр списка означал бы, что расход письма записан не в тот счёт.
"""

from __future__ import annotations

import logging
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import llm as llm_cfg
from backend.features.core import llm_caps
from backend.features.core.domain import Stage, UsageProvider
from backend.features.core.models.ops import UsageRecordModel
from backend.shared.numbers import spaced

logger = logging.getLogger(__name__)

#: Имя нашей системы в общей таблице расхода: ключ Ahrefs делится
#: с соседней, и без имени непонятно, чья это строка.
SYSTEM = "outreach-donors"

#: Операция → чей это счёт.
OPERATION_PROVIDERS = {
    "batch_metrics": UsageProvider.AHREFS,
    "by_country": UsageProvider.AHREFS,
    "serp": UsageProvider.AHREFS,
    # Списки ссылающихся доменов конкурентов (`runs/competitors.py`): юниты того же ключа.
    "refdomains": UsageProvider.AHREFS,
    # Выдача у основного источника платится деньгами, а не юнитами:
    # единица расхода — доллар, и цену называет сам провайдер в ответе.
    "serp_search": UsageProvider.SERP,
    # Сборка пула ключей: единица расхода — токен. До появления кнопки
    # на экране прогона это жило в консоли и в журнал не попадало вовсе —
    # то есть на вопрос «сколько мы потратили на ключи» ответить было
    # нечем, хотя операция платная и запускает её оператор.
    "keywords": UsageProvider.LLM,
    # Судья площадки: единица — токен. До 23.09 не писался вовсе, и журнал
    # показывал, что судья работает даром.
    "site_judge": UsageProvider.LLM,
    # Тематика сайта (`features/topics/`): единица — токен. Своя строка, а не судья:
    # вызов свой, и «во что обходится тема» иначе не посчитать.
    "site_topic": UsageProvider.LLM,
    # Уникализация письма: единица расхода — токен.
    "letter_rewrite": UsageProvider.LLM,
    # Отправка: единица — письмо. Считается и у нулевого транспорта,
    # иначе по журналу не отличить «не отправляли» от «отправили даром».
    "letter_send": UsageProvider.EMAIL,
    # Разбор ответа в цену: единица — токен.
    "reply_parse": UsageProvider.LLM,
    # Черновик ответа агентом переписки: единица — токен.
    "agent_draft": UsageProvider.LLM,
    # Вид ответа лида продаж: единица — токен.
    "sales_reply_kind": UsageProvider.LLM,
    # Переписывание письма очереди продаж: единица — токен. Своя операция, а не
    # `letter_rewrite` доноров: её считает свой потолок продаж (`sales/usage_cap.py`).
    "sales_letter_rewrite": UsageProvider.LLM,
    # Агент продаж: черновик ответа лиду, ситуация его письма и судья
    # черновика — каждый вызов своей операцией, единица — токен.
    "sales_draft": UsageProvider.LLM,
    "sales_situation": UsageProvider.LLM,
    "sales_judge": UsageProvider.LLM,
    # Сбор новой базы продаж по ключам (срез 6.1): судья сегмента — единица токен; выдача
    # сбора — единица доллар, как `serp_search` доноров, но своей строкой: её считают
    # расходом продаж (`SALES_OPERATIONS`), а не прогоном доноров.
    "sales_segment_judge": UsageProvider.LLM,
    "sales_serp": UsageProvider.SERP,
    # Помощник описания сегмента (16.10.2026): черновик описания → описание для судьи сегмента,
    # по кнопке и при смете сбора; единица — токен, потолок — продаж (`sales/usage_cap.py`).
    "sales_segment_helper": UsageProvider.LLM,
    # Проверка адреса лида продаж: единица — проверка. Ключ сервиса общий
    # с соседней системой, и до этой строки его траты не писались нигде.
    "sales_verify": UsageProvider.HUNTER,
    # Поиск адреса доноров и рекламодателей, платная ступень лестницы:
    # единица — запрос, который провайдер принял.
    "contacts_search": UsageProvider.HUNTER,
    # Обход Этапа 2, платные уровни каскада (`crawl/spend.py`): трафик через резидентный
    # прокси — единица байт, и вызов сервиса рендеринга пустой страницы — единица запрос.
    # Деньги — по цене из настроек; не задана — строка без денег, но с единицами.
    "crawl_proxy": UsageProvider.PROXY,
    "crawl_render": UsageProvider.RENDER,
}


#: Операции этапа продаж — модель и проверка адресов модуля продаж: всё, что начинается
#: с `sales_`. Без права «Продажи» их расхода нет на экране расхода — ни статьями, ни в
#: итогах (решение Anthony 10.10.2026, П2б; `runs/spending.py`). Отправка письма
#: (`letter_send`) — общая на все этапы и в продажи не входит.
SALES_OPERATIONS = frozenset(name for name in OPERATION_PROVIDERS if name.startswith("sales_"))

#: Части дневного потолка модели (`core/llm_caps.py`) — по операциям, без пересечений: продажи —
#: вызовы модели продаж, с агентом продаж (`sales_*`); агент переписки — черновики доноров и
#: рекламодателей (`agent_*`); аутрич — все остальные операции модели: разбор ответов, судья,
#: письма, ключи, тематика. Новая операция модели без приставки попадает в аутрич сама.
_LLM = frozenset(name for name, kind in OPERATION_PROVIDERS.items() if kind is UsageProvider.LLM)
PART_OPERATIONS: dict[str, frozenset[str]] = {
    llm_caps.PART_SALES: frozenset(name for name in _LLM if name.startswith("sales_")),
    llm_caps.PART_AGENT: frozenset(name for name in _LLM if name.startswith("agent_")),
}
PART_OPERATIONS[llm_caps.PART_OUTREACH] = _LLM.difference(*PART_OPERATIONS.values())

#: Платный обход Этапа 2 — то, что держит свой потолок Этапа 2 (`CRAWL_MONTH_CAP_USD`).
CRAWL_OPERATIONS = frozenset({"crawl_proxy", "crawl_render"})

#: Операции, у которых этап один, — его называет сама операция, вызову говорить незачем.
#: Остальные делят этапы (метрики Ahrefs — прогон Этапа 1 и DR кандидатов Этапа 2, поиск
#: адресов, письма), и этап называет вызов; не назвал — строка без этапа, а не «доноры»:
#: догадка разошлась бы со счётом молча. Отправка письма — общая на все этапы нарочно (П2б).
OPERATION_STAGES: dict[str, Stage] = {
    "by_country": Stage.DONORS,
    "refdomains": Stage.DONORS,
    "serp": Stage.DONORS,
    "serp_search": Stage.DONORS,
    "keywords": Stage.DONORS,
    "site_judge": Stage.DONORS,
    "site_topic": Stage.DONORS,
    **dict.fromkeys(CRAWL_OPERATIONS, Stage.ADVERTISERS),
    **dict.fromkeys(SALES_OPERATIONS, Stage.SALES),
}


class UnknownOperationError(ValueError):
    """Расход по операции, которой нет в списке. Молча отнести её к Ahrefs
    нельзя: провайдер мог быть другой, и счёт разойдётся."""


def record(
    session: AsyncSession,
    *,
    operation: str,
    units: int | None = None,
    amount_usd: Decimal | float | None = None,
    run_id: int | None = None,
    stage: Stage | None = None,
) -> UsageRecordModel:
    """Записать строку расхода.

    Пишется даже при неизвестном расходе — с нулём и пометкой в операции:
    сам факт запроса важнее его цены, а пропуск строки скрыл бы, что
    запрос вообще был.

    **Единица расхода у провайдеров разная, и подменять одну другой
    нельзя.** Ahrefs считает юниты, источник выдачи — деньги, модель —
    токены. Поэтому оба поля необязательны и заполняется то, в чём
    провайдер выставляет счёт; пустое поле означает «в этой валюте
    не платили», а не ноль.

    `stage` — этап траты. Не назван — этап операции (`OPERATION_STAGES`), если он у неё
    один; иначе строка без этапа.
    """
    provider = OPERATION_PROVIDERS.get(operation)
    if provider is None:
        raise UnknownOperationError(
            f"Операция «{operation}» не числится за провайдером. Добавьте её "
            f"в OPERATION_PROVIDERS — иначе расход уйдёт не в тот счёт."
        )

    entry = UsageRecordModel(
        system=SYSTEM,
        provider=provider,
        run_id=run_id,
        operation=operation,
        units=units,
        amount_usd=None if amount_usd is None else Decimal(str(amount_usd)),
        stage=stage if stage is not None else OPERATION_STAGES.get(operation),
    )
    session.add(entry)
    return entry


class LlmCapExceededError(RuntimeError):
    """Потолок расхода на модель достигнут. Останавливает, как `CapExceededError`
    у Ahrefs: не тратить дальше, сказать словами, сколько и из чего."""


@dataclass(frozen=True, slots=True)
class OwnCap:
    """Свой дневной потолок части операций модели — внутри общего, тем же журналом.

    `tokens` 0 — своего потолка нет. `what` — слова отказа: чей потолок;
    `setting` — какой настройкой его поднять: для журнала, а не для отказа (`_refused`).

    **Действующий — с экрана «Расход»** (`core/llm_caps.py`): вызывающий даёт потолок из
    `.env`, а задан на экране свой у этой настройки — `tokens` он (`declared` — что дали). Так
    свой потолок продаж и черновиков агента строят их модули, а число берут с экрана.
    """

    what: str
    operations: frozenset[str]
    tokens: int
    setting: str
    #: Что дал вызывающий — из `.env`; `tokens` — действующий.
    declared: int = field(default=0, init=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "declared", self.tokens)
        object.__setattr__(self, "tokens", llm_caps.own_tokens(self.setting, self.tokens))

    def fresh(self) -> OwnCap:
        """Тот же потолок по свежему чтению экрана (`llm_caps.refresh`)."""
        return OwnCap(self.what, self.operations, self.declared, self.setting)


def share_cap(own: int | None, share: float) -> int:
    """Сколько токенов в день у своего потолка части: настройка — как есть, не задана —
    доля общего. Общий 0 («потолка нет») при не заданной своей — потолка нет и у части.

    Одно правило на все части (черновики агента, вызовы модели продаж): своя доля не
    съедает день у разбора цен доноров, судьи прогона, писем и ключей. Общий — действующий:
    с экрана «Расход», не меняли — из `.env` (`llm_caps.daily_cap`).
    """
    if own is not None:
        return own
    return llm_caps.share_tokens(share)


async def llm_tokens_spent(
    session: AsyncSession,
    *,
    since: datetime | None = None,
    run_id: int | None = None,
    operations: Collection[str] | None = None,
) -> int:
    """Токены модели по журналу: с момента `since`, по прогону `run_id`, по операциям."""
    statement = select(func.coalesce(func.sum(UsageRecordModel.units), 0)).where(
        UsageRecordModel.system == SYSTEM,
        UsageRecordModel.provider == UsageProvider.LLM,
    )
    if since is not None:
        statement = statement.where(UsageRecordModel.created_at >= since)
    if run_id is not None:
        statement = statement.where(UsageRecordModel.run_id == run_id)
    if operations is not None:
        statement = statement.where(UsageRecordModel.operation.in_(sorted(operations)))
    return int(await session.scalar(statement) or 0)


async def spent_by_part(session: AsyncSession, *, since: datetime) -> llm_caps.Spent:
    """Токены модели частей потолка с момента `since` — одним запросом, по `PART_OPERATIONS`."""
    rows = await session.execute(
        select(UsageRecordModel.operation, func.coalesce(func.sum(UsageRecordModel.units), 0))
        .where(
            UsageRecordModel.system == SYSTEM,
            UsageRecordModel.provider == UsageProvider.LLM,
            UsageRecordModel.created_at >= since,
        )
        .group_by(UsageRecordModel.operation)
    )
    spent = dict.fromkeys(llm_caps.PARTS, 0)
    for operation, units in rows.tuples().all():
        part = next(
            (name for name, names in PART_OPERATIONS.items() if operation in names),
            llm_caps.PART_OUTREACH,
        )
        spent[part] += int(units or 0)
    return spent


def part_of(own: OwnCap | None) -> str | None:
    """Чья часть блока у вызова: свой потолок продаж или черновиков — их часть, без своего —
    аутрич; свой потолок с другим именем (этап-заглушка тестов) — `None`, он судится сам."""
    if own is None:
        return llm_caps.PART_OUTREACH
    return llm_caps.PART_OF_SETTING.get(own.setting)


async def ensure_llm_within_cap(
    session: AsyncSession,
    *,
    run_id: int | None = None,
    now: datetime | None = None,
    own: OwnCap | None = None,
) -> None:
    """Проверить потолки расхода на модель ДО вызова. Бросает `LlmCapExceededError`.

    Два потолка из настроек, оба в токенах, 0 — потолка нет: за календарный
    день UTC по всем операциям модели и за прогон (только когда прогон есть).
    Считается по журналу расхода, то есть по уже записанным вызовам: вызов,
    начатый до записи предыдущего, проходит — перебор до одного вызова на
    поток (у судьи в прогоне — до числа параллельных). Это цена простоты;
    потолок держит порядок трат, а не каждый токен.

    `own` — ещё и свой дневной потолок части операций (черновики агента): тот же
    день и тот же журнал, только по своим операциям; общий проверяется всё равно.
    Настройка своего потолка не прочиталась (`llm_cfg.UNREAD_CAPS`) — потолок части ноль:
    отказ сразу, до счёта, — пока блок на «Расходе» не сохранён.

    Перед счётом перечитывается блок «Расхода» (`llm_caps.refresh`), и `own`, построенный до
    этого вызова, — тоже по свежему чтению. Блок сохранён — вызов судит лимит его части
    (`part_of`: без своего потолка — аутрич), а не потолки `.env`.
    """
    await llm_caps.refresh(session)
    own = None if own is None else own.fresh()
    _readable(own)
    moment = now or datetime.now(UTC)
    day_start = datetime.combine(moment.date(), time.min, tzinfo=UTC)
    saved = llm_caps.block()
    part = part_of(own)
    if saved is not None and part is not None:
        await _within_part(session, saved, part, since=day_start, moment=moment)
    else:
        await _within_env(session, own, since=day_start, moment=moment)
    if llm_cfg.RUN_TOKEN_CAP and run_id is not None:
        spent = await llm_tokens_spent(session, run_id=run_id)
        if spent >= llm_cfg.RUN_TOKEN_CAP:
            raise _refused(
                f"потолок расхода на модель за прогон №{run_id} достигнут",
                spent=spent,
                cap=llm_cfg.RUN_TOKEN_CAP,
                setting="LLM_RUN_TOKEN_CAP",
            )


async def _within_part(
    session: AsyncSession, saved: llm_caps.Block, part: str, *, since: datetime, moment: datetime
) -> None:
    """Блок сохранён на «Расходе»: лимит части вызова — жёсткий потолок её расхода за сутки
    (`llm_caps.available`). Общий без потолка — и части без потолка: отказов нет, счёт не нужен."""
    if saved.general is None:
        return
    spent = await spent_by_part(session, since=since)
    if llm_caps.available(saved, part, spent) > 0:
        return
    logger.warning(
        "лимит части потолка модели выбран: %s",
        part,
        extra={"part": part, "spent": spent[part], "cap": saved.limit(part)},
    )
    raise LlmCapExceededError(
        f"лимит раздела «{llm_caps.TITLES[part]}» на сегодня выбран: {spaced(spent[part])} из "
        f"{spaced(saved.limit(part))} токенов — {llm_caps.after_part(moment)}"
    )


async def _within_env(
    session: AsyncSession, own: OwnCap | None, *, since: datetime, moment: datetime
) -> None:
    """Блок не сохраняли — потолки из `.env`, как до 29.10.2026: общий и свой потолок части.
    Свой потолок с другим именем (этап-заглушка) — так же и при сохранённом блоке."""
    day_start = since
    general = llm_caps.daily_cap()
    if general:
        spent = await llm_tokens_spent(session, since=day_start)
        if spent >= general:
            raise _refused(
                "потолок расхода на модель за день достигнут",
                spent=spent,
                cap=general,
                setting=llm_caps.GENERAL,
                moment=moment,
            )
    if own is not None and own.tokens:
        spent = await llm_tokens_spent(session, since=day_start, operations=own.operations)
        if spent >= own.tokens:
            raise _refused(
                f"дневной потолок {own.what} выбран",
                spent=spent,
                cap=own.tokens,
                setting=own.setting,
                moment=moment,
            )


def _readable(own: OwnCap | None) -> None:
    """Своя настройка потолка части с опечаткой (`config/_base.spared`) — потолок ноль, пока её
    не поправят: отказ. Блок на «Расходе» сохранён — действует лимит части, и опечатка в `.env`
    ему не мешает. Человеку — слова, журналу — имя настройки, как у `_refused`."""
    if own is None or own.setting not in llm_cfg.UNREAD_CAPS or llm_caps.block() is not None:
        return
    logger.warning("потолок модели не прочитан: %s", own.setting, extra={"setting": own.setting})
    raise LlmCapExceededError(
        f"дневной потолок {own.what} не прочитан из настроек — вызовов нет, пока его не "
        "поправит администратор"
    )


def _refused(
    words: str, *, spent: int, cap: int, setting: str, moment: datetime | None = None
) -> LlmCapExceededError:
    """Отказ потолка: человеку — слова, журналу — имя настройки, которой его поднять.

    До 10.10.2026 имя настройки стояло в самом отказе («…поднимите SALES_DAILY_TOKEN_CAP»),
    и его читал человек в итоге сборки очереди и разбора ответа (находка QA продаж на проде).
    Поднимает потолок администратор — ему имя и нужно, и оно в журнале.

    Дневной потолок (`moment` — минута проверки) говорит, где его поднимают и когда сброс
    (`llm_caps.after_cap`, 29.10.2026): «продолжение завтра» в полночь по Москве значило ещё
    три часа. Потолок прогона — настройка сервера, и сброса у него нет.

    Числа — с разрядами, как на экране (`shared/numbers.spaced`): «2 600 000 из 2 500 000»
    читается сразу, «2600000 из 2500000» — пересчётом нулей.
    """
    logger.warning(
        "потолок модели выбран: %s",
        setting,
        extra={"setting": setting, "spent": spent, "cap": cap},
    )
    then = (
        "поднять потолок прогона может администратор"
        if moment is None
        else llm_caps.after_cap(moment)
    )
    return LlmCapExceededError(f"{words}: {spaced(spent)} из {spaced(cap)} токенов — {then}")
