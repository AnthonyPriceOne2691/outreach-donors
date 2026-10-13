"""Этап ответа без письма — по нашему адресу, на который он пришёл.

Ответ, не привязанный ни к письму, ни к переписке (`unbound.py`), этапа по рассылке не имеет.
Но пришёл он на наш адрес — ящик или поддомен ответов одного из доменов рассылки, — а у домена
своё направление: домены доноров, рекламодателей и продаж разные (`SendingDomainModel.stage`,
`SenderModel.stage`). По этому этапу вкладка «Не привязаны», число в «Обзоре» и ответ по номеру
закрыты правом «Продажи» (решение Anthony 10.10.2026, П2), а сторож тишины считает ответы своего
этапа (`ops/stage_silence.py`).

**Адрес — первый из наших.** Приём кладёт адрес доставки первым (`replies/mime.py`), за ним —
«кому» и копию: решает первый адрес, чей домен — наш домен рассылки или его поддомен
(`anna+m417.…@replies.mail-a.example` — поддомен ответов `mail-a.example`, `letters/reply_to.py`).
Строка домена рассылки решает раньше ящиков: у неё направление домена целиком.

**Нашего адреса нет — этапа нет**: ответ принят до 28.09.2026, когда адреса не хранились, или
пришёл на домен, которого больше нет. Такой ответ виден всем, как был.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import Stage
from backend.features.core.models.outreach import SenderModel, SendingDomainModel


async def known_domains(session: AsyncSession) -> dict[str, Stage]:
    """Наши домены рассылки и их этап: ящики, а поверх — строки доменов."""
    found: dict[str, Stage] = {}
    boxes = await session.execute(
        select(SenderModel.domain, SenderModel.stage).order_by(SenderModel.id)
    )
    for domain, stage in boxes.tuples():
        found[_host(domain)] = stage
    rows = await session.execute(select(SendingDomainModel.domain, SendingDomainModel.stage))
    for domain, stage in rows.tuples():
        found[_host(domain)] = stage
    return found


def stage_of(addresses: Sequence[str] | None, domains: Mapping[str, Stage]) -> Stage | None:
    """Этап первого из наших адресов в `addresses`. `None` — нашего адреса среди них нет."""
    for address in addresses or ():
        found = _of_host(_host(address.rpartition("@")[2]), domains)
        if found is not None:
            return found
    return None


async def of_addresses(session: AsyncSession, addresses: Sequence[str] | None) -> Stage | None:
    """Этап ответа по его адресам — одним вопросом о доменах (ответ по номеру)."""
    if not addresses:
        return None
    return stage_of(addresses, await known_domains(session))


def _of_host(host: str, domains: Mapping[str, Stage]) -> Stage | None:
    """Этап домена адреса или ближайшего нашего домена, чей он поддомен."""
    labels = host.split(".")
    for start in range(len(labels) - 1):
        found = domains.get(".".join(labels[start:]))
        if found is not None:
            return found
    return None


def _host(domain: str) -> str:
    """Домен для сравнения — строчными и без точки в конце, как у ящика (`identity`)."""
    return domain.strip().rstrip(".").lower()
