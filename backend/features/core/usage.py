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

from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime, time
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import llm as llm_cfg
from backend.features.core.domain import UsageProvider
from backend.features.core.models.ops import UsageRecordModel

#: Имя нашей системы в общей таблице расхода: ключ Ahrefs делится
#: с соседней, и без имени непонятно, чья это строка.
SYSTEM = "outreach-donors"

#: Операция → чей это счёт.
OPERATION_PROVIDERS = {
    "batch_metrics": UsageProvider.AHREFS,
    "by_country": UsageProvider.AHREFS,
    "serp": UsageProvider.AHREFS,
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
    # Уникализация письма: единица расхода — токен.
    "letter_rewrite": UsageProvider.LLM,
    # Отправка: единица — письмо. Считается и у нулевого транспорта,
    # иначе по журналу не отличить «не отправляли» от «отправили даром».
    "letter_send": UsageProvider.EMAIL,
    # Разбор ответа в цену: единица — токен.
    "reply_parse": UsageProvider.LLM,
    # Черновик ответа агентом переписки: единица — токен.
    "agent_draft": UsageProvider.LLM,
    # Проверка адреса лида продаж: единица — проверка. Ключ сервиса общий
    # с соседней системой, и до этой строки его траты не писались нигде.
    "sales_verify": UsageProvider.HUNTER,
    # Поиск адреса доноров и рекламодателей, платная ступень лестницы:
    # единица — запрос, который провайдер принял.
    "contacts_search": UsageProvider.HUNTER,
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
    )
    session.add(entry)
    return entry


class LlmCapExceededError(RuntimeError):
    """Потолок расхода на модель достигнут. Останавливает, как `CapExceededError`
    у Ahrefs: не тратить дальше, сказать словами, сколько и из чего."""


@dataclass(frozen=True, slots=True)
class OwnCap:
    """Свой дневной потолок части операций модели — внутри общего, тем же журналом.

    `tokens` 0 — своего потолка нет. `what` и `setting` — слова отказа: чей
    потолок и какой настройкой его поднять.
    """

    what: str
    operations: frozenset[str]
    tokens: int
    setting: str


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
    """
    moment = now or datetime.now(UTC)
    day_start = datetime.combine(moment.date(), time.min, tzinfo=UTC)
    if llm_cfg.DAILY_TOKEN_CAP:
        spent = await llm_tokens_spent(session, since=day_start)
        if spent >= llm_cfg.DAILY_TOKEN_CAP:
            raise LlmCapExceededError(
                f"потолок расхода на модель за день достигнут: {spent} из "
                f"{llm_cfg.DAILY_TOKEN_CAP} токенов (LLM_DAILY_TOKEN_CAP) — "
                "продолжение завтра или поднять потолок"
            )
    if own is not None and own.tokens:
        spent = await llm_tokens_spent(session, since=day_start, operations=own.operations)
        if spent >= own.tokens:
            raise LlmCapExceededError(
                f"дневной потолок {own.what} выбран: {spent} из {own.tokens} токенов "
                f"({own.setting}); завтра или поднимите {own.setting}"
            )
    if llm_cfg.RUN_TOKEN_CAP and run_id is not None:
        spent = await llm_tokens_spent(session, run_id=run_id)
        if spent >= llm_cfg.RUN_TOKEN_CAP:
            raise LlmCapExceededError(
                f"потолок расхода на модель за прогон №{run_id} достигнут: {spent} из "
                f"{llm_cfg.RUN_TOKEN_CAP} токенов (LLM_RUN_TOKEN_CAP)"
            )
