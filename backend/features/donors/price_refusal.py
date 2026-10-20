"""Почему цену руками домену не записать — словами отказа, до нажатия и до записи.

Правила одни у кнопки «Записать цену», консоли и карточки донора: карточка
говорит отказ до нажатия (`DonorCard.price_refusal`, 08.10.2026), кнопка — тот же
текст после. Отдельно от записи цены (`manual_price.py`) с 12.10.2026: цена руками
стала шире числа — серая, оплата, — и модуль записи упёрся в потолок длины файла;
шов по смыслу — здесь только чтение, решающее «можно ли».
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import DonorStatus
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.outreach import SenderModel
from backend.features.core.models.run import RunVerdictModel
from backend.features.donors.host import normalize_host
from backend.features.donors.standing import decided_in, donor_now
from backend.features.donors.wording import reject_reason_text
from backend.features.runs.exclusions import (
    ExclusionReason,
    Exclusions,
    by_name,
    not_writing_reason,
)
from backend.features.runs.refusals import Threshold

#: Исключения прогона, которые закрывают и ручной ввод, — словами отказа.
#: Молчания и «не продаём» здесь нет намеренно: цену человек знает не из письма.
_REFUSED: dict[ExclusionReason, str] = {
    ExclusionReason.STOPLIST: (
        "{host} в стоп-листе: ему не пишем, и донором с ценой его не заводим. "
        "Снять запись — на экране «Стоп-лист»."
    ),
    ExclusionReason.SUPPLIER: (
        "{host} — донор-поставщик агентства: его рекламодателей не трогаем, "
        "Этап 2 по нему не запускается."
    ),
    ExclusionReason.REJECTED: (
        "{host} отклонён человеком: донором его не заводим, пока решение не снимут "
        "в очереди прогона — она по ссылке в карточке донора."
    ),
    ExclusionReason.PUBLIC_ZONE: (
        "{host} — гос. или учебная зона: размещений такие сайты не продают."
    ),
    ExclusionReason.PLATFORM: "{host} — платформа или соцсеть: разместиться там нельзя.",
    ExclusionReason.MAJOR_MEDIA: "{host} — крупное СМИ: им не пишем, донором с ценой не заводим.",
    ExclusionReason.REFERENCE: (
        "{host} — справочник или исследования: им не пишем, донором с ценой не заводим."
    ),
}


async def price_refusal(session: AsyncSession, host: str, donor: DonorModel | None) -> str | None:
    """Почему цену руками домену не записать — словами отказа; `None` — запишется.
    `donor` — запись домена в `donors`, если она есть.

    Одни слова на отказ «Записать цену» и на карточку донора
    (`DonorCard.price_refusal`): в карточке человек читает до нажатия ровно
    то, что скажет отказ, — как у ящика переписки (`letters/mailbox._silence`).
    До 08.10.2026 о том, что принятый донор в стоп-листе, человек узнавал,
    только вписав цену и нажав кнопку.
    """
    refusal = await refusal_for(session, host)
    if refusal is not None or donor is None or donor_now(donor):
        return refusal
    return await _candidate_refusal(session, donor, host)


async def refusal_for(session: AsyncSession, host: str) -> str | None:
    """Почему домену цену вручную не записываем. `None` — записываем.

    Правила — те же, что не пускают домен в прогон (`runs/exclusions.py`):
    второй экземпляр здесь разошёлся бы с ними на первой правке.
    """
    if host in await _sending_roots(session):
        return f"{host} — наш домен рассылки: донором он не бывает."
    reason = by_name([host]).get(host) or (await Exclusions(session).excluded_hosts([host])).get(
        host
    )
    template = None if reason is None else _REFUSED.get(reason)
    if template is not None:
        return template.format(host=host)
    # DR выше порога версии — по последнему замеру, тем же условием, что у выходов.
    domain_id = await session.scalar(select(DomainModel.id).where(DomainModel.host == host))
    off = None if domain_id is None else await not_writing_reason(session, domain_id)
    return None if off is None else f"У {host} {off}: донором с ценой его не заводим."


async def _sending_roots(session: AsyncSession) -> set[str]:
    """Наши домены рассылки — корнями, как ключ доменов: ящик живёт и на поддомене."""
    domains = await session.scalars(select(SenderModel.domain).distinct())
    return {normalize_host(domain) or domain.strip().lower() for domain in domains.all()}


async def _candidate_refusal(session: AsyncSession, donor: DonorModel, host: str) -> str | None:
    """Домен в базе, но не донор: ждёт решения в очереди прогона или не прошёл пороги.

    Решение о кандидате принимают в очереди прогона — там судья, выдача и
    причина, по которой он туда попал. Не прошедший пороги в очередь не
    попадает вовсе: «годен ли по цифрам» решают пороги, а не ручной ввод.
    """
    waits = await decided_in(session, donor.domain_id, None)
    if waits is not None:
        return (
            f"{host} ждёт решения в очереди прогона №{waits}: решают там. Примете — "
            "цену укажете на карточке донора."
        )
    if donor.status is DonorStatus.UNSUITABLE:
        why = reject_reason_text(donor.reject_reason)
        return (
            f"{host} не прошёл пороги отбора{f' ({why})' if why else ''}: Этап 2 обходит "
            f"только годных доноров.{await _reserve_hint(session, donor.domain_id)}"
        )
    return None


async def _reserve_hint(session: AsyncSession, domain_id: int) -> str:
    """Где не прошедший лежит в резерве — туда за ним и идти (резерв, 20.10.2026): взятый
    оттуда на рассмотрение и принятый — донор, и цену ему укажут на карточке."""
    run_id = await session.scalar(
        select(func.max(RunVerdictModel.run_id))
        .where(RunVerdictModel.domain_id == domain_id)
        .where(RunVerdictModel.status == DonorStatus.UNSUITABLE.value)
        # Крупного сайта резерв не берёт — туда за ним не посылаем.
        .where(RunVerdictModel.cause.is_distinct_from(Threshold.BIG.value))
    )
    if run_id is None:
        return ""
    return (
        f" Он в резерве прогона №{run_id}: взять на рассмотрение можно там — «Доноры» → «Резерв»."
    )
