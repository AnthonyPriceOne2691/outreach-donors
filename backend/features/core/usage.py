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

from sqlalchemy.ext.asyncio import AsyncSession

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
    # Уникализация письма: единица расхода — токен.
    "letter_rewrite": UsageProvider.LLM,
    # Отправка: единица — письмо. Считается и у нулевого транспорта,
    # иначе по журналу не отличить «не отправляли» от «отправили даром».
    "letter_send": UsageProvider.EMAIL,
}


class UnknownOperationError(ValueError):
    """Расход по операции, которой нет в списке. Молча отнести её к Ahrefs
    нельзя: провайдер мог быть другой, и счёт разойдётся."""


def record(
    session: AsyncSession,
    *,
    operation: str,
    units: int | None,
    run_id: int | None = None,
) -> UsageRecordModel:
    """Записать строку расхода.

    Пишется даже при неизвестном расходе — с нулём и пометкой в операции:
    сам факт запроса важнее его цены, а пропуск строки скрыл бы, что
    запрос вообще был.
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
    )
    session.add(entry)
    return entry
