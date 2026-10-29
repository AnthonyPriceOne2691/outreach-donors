"""Почему цену руками домену не записать — словами отказа, до нажатия и до записи.

Правила одни у кнопки «Записать цену», консоли и карточки донора: карточка
говорит отказ до нажатия (`DonorCard.price_refusal`, 08.10.2026), кнопка — тот же
текст после. Отдельно от записи цены (`manual_price.py`) с 12.10.2026: цена руками
стала шире числа — серая, оплата, — и модуль записи упёрся в потолок длины файла;
шов по смыслу — здесь только чтение, решающее «можно ли».
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import DonorStatus
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.outreach import SenderModel
from backend.features.core.models.run import RunVerdictModel
from backend.features.donors.host import normalize_host
from backend.features.donors.standing import donor_now, pending_in
from backend.features.donors.wording import region_refusal, reject_reason_text
from backend.features.runs.exclusions import (
    ExclusionReason,
    Exclusions,
    by_name,
    not_writing_of,
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
    return (await price_refusals(session, {host: donor})).get(host)


async def price_refusals(
    session: AsyncSession, donors: Mapping[str, DonorModel | None]
) -> dict[str, str]:
    """`price_refusal` списком: домен → запись в `donors` или `None`; ответ — только те, кому
    отказ. Те же правила теми же словами, но запросом на все домены, а не на каждый: загрузка
    доноров таблицей (`donors/bulk_entry.py`, 29.10.2026) несёт до десяти тысяч строк, а
    одна проверка — с десяток запросов."""
    found = await refusals_for(session, list(donors))
    candidates = {
        host: donor
        for host, donor in donors.items()
        if host not in found and donor is not None and not donor_now(donor)
    }
    found.update(await _candidate_refusals(session, candidates))
    return found


async def refusal_for(session: AsyncSession, host: str) -> str | None:
    """Почему домену цену вручную не записываем. `None` — записываем."""
    return (await refusals_for(session, [host])).get(host)


async def refusals_for(session: AsyncSession, hosts: Sequence[str]) -> dict[str, str]:
    """Почему этим доменам цену вручную не записываем: домен → слова; записываем — домена нет.

    Правила — те же, что не пускают домен в прогон (`runs/exclusions.py`):
    второй экземпляр здесь разошёлся бы с ними на первой правке. По имени —
    раньше исключений прогона: гос. зона, которая ещё и в стоп-листе, названа зоной.
    """
    if not hosts:
        return {}
    roots = await _sending_roots(session)
    named = by_name(hosts)
    excluded = await Exclusions(session).excluded_hosts(hosts)
    found: dict[str, str] = {}
    rest: list[str] = []
    for host in hosts:
        reason = named.get(host) or excluded.get(host)
        template = None if reason is None else _REFUSED.get(reason)
        if host in roots:
            found[host] = f"{host} — наш домен рассылки: донором он не бывает."
        elif template is not None:
            found[host] = template.format(host=host)
        else:
            rest.append(host)
    # DR выше порога версии — по последнему замеру, тем же условием, что у выходов.
    ids = (
        await session.execute(select(DomainModel.id).where(DomainModel.host.in_(rest)))
        if rest
        else None
    )
    off = await not_writing_of(session, [] if ids is None else list(ids.scalars().all()))
    found.update(
        {
            host: f"У {host} {off[host]}: донором с ценой его не заводим."
            for host in rest
            if host in off
        }
    )
    return found


async def _sending_roots(session: AsyncSession) -> set[str]:
    """Наши домены рассылки — корнями, как ключ доменов: ящик живёт и на поддомене."""
    domains = await session.scalars(select(SenderModel.domain).distinct())
    return {normalize_host(domain) or domain.strip().lower() for domain in domains.all()}


async def _candidate_refusals(
    session: AsyncSession, donors: Mapping[str, DonorModel]
) -> dict[str, str]:
    """Домен в базе, но не донор: ждёт решения в очереди прогона или не прошёл пороги.

    Решение о кандидате принимают в очереди прогона — там судья, выдача и
    причина, по которой он туда попал. Не прошедший пороги в очередь не
    попадает вовсе: «годен ли по цифрам» решают пороги, а не ручной ввод.

    **Регион — не отказ** (тест-группа 23.10.2026). Его судят страной прогона, который мерил
    домен, а у ручного ввода страны нет: отказ «Германия не входит в топ-5» человеку, не
    задававшему никакой страны, называл чужое требование, а новый домен та же форма заводит
    без порогов вовсе. Регион проверяется последним — остальные пороги такой домен прошёл.
    """
    waits = await pending_in(session, [donor.domain_id for donor in donors.values()])
    found: dict[str, str] = {}
    unsuitable: dict[str, DonorModel] = {}
    for host, donor in donors.items():
        run = waits.get(donor.domain_id)
        if run is not None:
            found[host] = (
                f"{host} ждёт решения в очереди прогона №{run}: решают там. Примете — "
                "цену укажете на карточке донора."
            )
        elif donor.status is DonorStatus.UNSUITABLE and not region_refusal(donor.reject_reason):
            unsuitable[host] = donor
    reserves = await _reserves(session, [donor.domain_id for donor in unsuitable.values()])
    for host, donor in unsuitable.items():
        why = reject_reason_text(donor.reject_reason)
        found[host] = (
            f"{host} не прошёл пороги отбора{f' ({why})' if why else ''}: Этап 2 обходит "
            f"только годных доноров.{_reserve_hint(reserves.get(donor.domain_id))}"
        )
    return found


async def _reserves(session: AsyncSession, domain_ids: Sequence[int]) -> dict[int, int]:
    """Где не прошедший лежит в резерве — туда за ним и идти (резерв, 20.10.2026): домен →
    последний прогон, где он не прошёл пороги. Крупного сайта резерв не берёт — туда за ним
    не посылаем."""
    if not domain_ids:
        return {}
    rows = await session.execute(
        select(RunVerdictModel.domain_id, func.max(RunVerdictModel.run_id))
        .where(RunVerdictModel.domain_id.in_(list(domain_ids)))
        .where(RunVerdictModel.status == DonorStatus.UNSUITABLE.value)
        .where(RunVerdictModel.cause.is_distinct_from(Threshold.BIG.value))
        .group_by(RunVerdictModel.domain_id)
    )
    return {int(domain): int(run) for domain, run in rows.tuples()}


def _reserve_hint(run_id: int | None) -> str:
    """Взятый из резерва на рассмотрение и принятый — донор, и цену ему укажут на карточке."""
    if run_id is None:
        return ""
    return (
        f" Он в резерве прогона №{run_id}: взять на рассмотрение можно там — «Доноры» → «Резерв»."
    )
