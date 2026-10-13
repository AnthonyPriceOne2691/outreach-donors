"""Автоответ мёртвого ящика лида продаж назвал, куда писать дальше: «Jane ушла, пишите olga@…».

Приём узнаёт такой автоответ (`replies/classify.DEAD_MAILBOX`: письмо автомата и «больше не
работает», «ящик не читается») и даёт ему вид «отказ доставки»: ящик лида мёртв, цепочка встаёт.
У доноров названный адрес ложится в контакты домена (`replies/redirect.follow`); у продаж
контактов нет — есть лиды, и адрес берёт модуль продаж (ревью стыков, круг 2): задача ответа
продаж (`replies.py`) читает его из написанного рукой и заводит лида той же компании тем же путём,
что «пишите другому» (`referral.from_answer`): очистка, исходный диалог закрыт, письмо — сборкой
для одного лида. Модель не зовётся.

**Какой адрес годится — правило приёма** (`redirect.sort_out`): не отправитель (мёртвый ящик),
не робот, не наш, не уже известный (лиды той же компании), годный по форме. Адрес компании лида
(`of_company`: сайт компании или домен адреса лида) — новый лид, первый из названных: «пишите на
X» называет одного. Адрес на чужом домене — человеку словами: частный ящик бывшего сотрудника —
не адрес компании, и писать туда без человека нельзя (то же правило у доноров).

**Без фразы мёртвого ящика — ничего.** Отказ доставки почтовой службы тоже несёт адреса (кому не
доставлено, postmaster): лида из него не заводим. Фраза — та же, по которой приём узнаёт мёртвый
ящик (`replies/phrases`): сюда может прийти и такой отказ, если приём отдаст продажам все отказы.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.contacts.extract import EMAIL_RE
from backend.features.core.models.outreach import ReplyModel, SenderModel
from backend.features.replies import phrases, redirect
from backend.features.replies.quoting import written_by_hand
from backend.features.sales.mail import Linked, linked
from backend.features.sales.models import SalesLeadModel
from backend.features.sales.referral import Referred
from backend.features.sales.reply_routes import Decision, Route

#: Вид в снимке ответа: ответ разобран — повтор задачи его не берёт (`replies._not_ours`).
KIND = "dead_mailbox"
#: Начало причины у ответа.
DEAD = "ящик лида не читается"
#: Отказ доставки без фразы мёртвого ящика: адреса в нём — не новый человек.
NOT_DEAD = "отказ доставки — нового адреса в нём нет"

#: Лид из ответа на названный адрес: (адрес, имя) → что вышло (`referral.from_answer`).
Refer = Callable[[str | None, str | None], Awaitable[Referred]]


def _dead(body: str) -> bool:
    """«Больше не работает», «ящик не читается» — фраза мёртвого ящика в написанном рукой."""
    return bool(phrases.NO_LONGER_RE.search(body) or phrases.NOT_READ_RE.search(body))


def of_company(address: str, dialog: Linked) -> bool:
    """Адрес компании лида: его домен — сайт компании или домен адреса лида, они сами или их
    поддомены; по зарегистрированному имени (`redirect.same_site`: `mail.acme.com` — тот же
    сайт), а где его нет (выдуманные зоны) — по самому имени."""
    domain = address.rpartition("@")[2]
    for own in {dialog.host.lower(), dialog.lead.email.rpartition("@")[2].lower()} - {""}:
        if domain == own or domain.endswith(f".{own}") or redirect.same_site(address, own):
            return True
    return False


async def colleague(
    session: AsyncSession, reply: ReplyModel, refer: Refer
) -> tuple[Decision, tuple[int, int] | None]:
    """Путь ответа и что сказать — и (гипотеза, лид), если новому лиду пора письмо."""
    body = written_by_hand(reply.raw_body)
    if not _dead(body):
        return Decision(Route.MANUAL, False, NOT_DEAD), None
    dialog = await linked(session, reply.thread_id)
    if dialog is None:
        return Decision(Route.MANUAL, True, f"{DEAD}: у диалога нет лида продаж"), None
    found = await _usable(session, reply, body, dialog)
    company = [address for address in found if of_company(address, dialog)]
    if company:
        referred = await refer(company[0], None)
        said = f"{DEAD} — автоответ назвал {company[0]}: {referred.words}"
        return Decision(Route.REFERRAL, referred.waits, said), referred.ready
    if found:
        where = ", ".join(found)
        said = f"{DEAD} — автоответ называет адрес на чужом домене {where}: лида заводит человек"
        return Decision(Route.MANUAL, True, said), None
    return Decision(Route.MANUAL, False, f"{DEAD} — другого адреса автоответ не назвал"), None


async def _usable(
    session: AsyncSession, reply: ReplyModel, body: str, dialog: Linked
) -> tuple[str, ...]:
    """Названные адреса, годные в лиды (`redirect.sort_out`): не мёртвый ящик, не робот, не наш,
    не лид той же компании, годный по форме — по порядку в письме."""
    named = tuple(dict.fromkeys(found.strip(".").lower() for found in EMAIL_RE.findall(body)))
    ours = await session.scalars(select(SenderModel.domain).distinct())
    known = await session.scalars(
        select(SalesLeadModel.email).where(SalesLeadModel.domain_id == dialog.lead.domain_id)
    )
    found = redirect.sort_out(
        named,
        sender=reply.from_email or "",
        host=dialog.host,
        ours=ours.all(),
        known=[email.lower() for email in known.all()],
    )
    return (*found.added, *found.elsewhere)
