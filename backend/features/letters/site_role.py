"""Одна роль на сайт — и в минуту отправки: первое письмо не уходит, если сайту уже пишет
другое направление.

Сайт бывает донором, чьим-то рекламодателем и лидом продаж. Отбор не берёт домен с живым
письмом любого этапа (`attempts.live_letter`), продажи сверяют другое направление при очистке
лида, — но между сборкой и отправкой проходят часы, а то и дни: письмо донору собрано, а
продажи пишут той же компании, или наоборот, — и компания получает два холодных письма в
разных ролях (ревью стыков 12.10.2026). Поэтому первое письмо любого этапа не уходит, если у
домена живое письмо другого этапа (`attempts.roles_elsewhere`). Добивка и ответ в переписке идут
в начатый разговор — их это правило не держит.

Отдельно от `sending.py`: тот упёрся в потолок длины файла. Отправка зовёт проверку одной
строкой рядом со стоп-листом и называет класс отказа сама (`sending.OtherRoleError`, наследник
стоп-листа): пачка и автоотправка продаж идут к следующему письму, а итог пачки называет
причину словами (`batch.why`).

Живое — то же, что у отбора: в очереди, в пути, дошедшее, остановленное человеком. Письмо
в очереди считается намеренно: из двух первых писем разным этапам, ждущих разом, снимается то,
которое пошло первым, а второе уходит; одновременные нажатия снимут оба — но двух писем одному
сайту не отправят.

**Письмо снимается, а не ждёт**: «остановлено» с причиной, называющей направление
(`attempts.elsewhere_reason`: «сайт в работе у другого направления: продажи»). Ждать ему
нечего: письмо другой роли не исчезнет к следующему нажатию, а пачка спотыкалась бы о него
каждый раз. Снятое так «писали» не считается (`attempts.withdrawn`): адрес и потолок попыток
оно не тратит, и когда письмо другой роли не дойдёт, сборка напишет сайту заново.

**Диалог письма продаж остаётся.** У доноров и рекламодателей пустой диалог снятого письма
уходит, как у письма под снятую ссылку (`crawl/offer_stop.out_of_queue`). К диалогу продаж
привязан лид (`sales_threads`, каскадом): удалив диалог, почта вернула бы лида в сборку
продаж, и та переписывала бы ему письмо моделью на каждом круге, пока сайт в работе у другого
направления. Вернуть лида в очередь — решение модуля продаж.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import MessageModel
from backend.features.core.stages import MailRefusalError
from backend.features.crawl.offer_stop import out_of_queue
from backend.features.letters import attempts
from backend.features.letters.chain import FIRST_STEP


async def check(
    session: AsyncSession, message: MessageModel, stage: Stage, refusal: type[MailRefusalError]
) -> None:
    """Первое письмо `message` этапа `stage` сайту, который в работе у другого направления, —
    снять своей фиксацией и отказать `refusal` словами для экрана. Иначе — ничего.

    Письмо успели взять в отправку между чтением и снятием — оно не снято, и «снято» было бы
    неправдой: проверка молчит, а дальше решает захват отправки («уже не в очереди»)."""
    other = await _elsewhere(session, message, stage)
    if other is None:
        return
    reason = attempts.elsewhere_reason(other)
    if not await out_of_queue(session, message, reason=reason, keep_thread=stage is Stage.SALES):
        return
    await session.commit()
    domain = await session.get(DomainModel, message.domain_id)
    site = f" ({domain.host})" if domain is not None else ""
    raise refusal(
        f"Письмо №{message.id} снято: {reason}{site} — одному сайту письма в двух ролях "
        "не уходят. Когда письмо другого направления не дойдёт, сборка напишет сайту заново"
    )


async def _elsewhere(session: AsyncSession, message: MessageModel, stage: Stage) -> Stage | None:
    """Направление, у которого сайт в работе, — у первого письма; `None` — сайт свободен или
    письмо не первое. Живых писем в других ролях бывает два — называется первое по порядку
    этапов, а не случайное."""
    if message.step != FIRST_STEP:
        return None
    found = await session.scalars(attempts.roles_elsewhere(message.domain_id, stage))
    roles = set(found.all())
    return next((role for role in Stage if role in roles), None)
