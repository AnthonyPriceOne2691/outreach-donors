"""Следующий адрес донора: письмо, не дошедшее ни до кого, «писали» не считается.

Правило из `docs/OUTREACH_THREADS.md`: одно письмо на донора за раз,
и следующий адрес берётся не раньше, чем отвалился предыдущий. Отказ
доставки («ящика нет») и автоответ «ящик больше не читается» открывают
следующий адрес сразу. Ответ человека не открывает никогда: разговор идёт
там, где начался. Тишина после цепочки — тоже нет: её держит свой гейт
отбора в год (`runs/exclusions.py`), а не новый адрес.

До 28.09.2026 обещание стояло в документах и докстрингах, а в коде
не работало вовсе, и держали его три барьера, каждый по отдельности:
мёртвый адрес оставался лучшим (оценка 0 стоит выше пустой), любое письмо
домена — и не дошедшее — считалось «уже писали», а ключ письма
`этап:домен:шаг` не пустил бы второе первое письмо в базу.

**Мёртвое письмо — то, что не дошло и не дойдёт ни до кого:** отказ
доставки или ушедшее (остановленное) письмо на адрес, который потом
похоронили (`preference.DEAD`). Письмо в очереди и в пути живое при
любом адресе: пока исход неизвестен, второго не собираем.

**Письмо, остановленное снятием своей ссылки, — как будто его не было**
(`withdrawn`, сверка ТЗ 11.10.2026). Оффер рекламодателю пишется под найденную
ссылку; человек сказал «не пишем» про ссылку, под которую письмо собрано, а на
домен ссылается другой наш донор (`crawl/promote.withdraw`). Письмо не уходило
и адрес не занимало: «писали» оно не считается, потолок адресов не тратит, и
следующее собирается на тот же адрес под действующую ссылку. Так же — письмо под
ссылку на площадке поставщика, внесённого в стоп-лист после сборки (12.10.2026).

**Письмо, снятое при отправке, потому что сайту пишет другое направление**, — тоже
(ревью стыков 12.10.2026, `letters/site_role.py`): первое письмо этапа не уходит, пока у домена
живое письмо другого этапа (`roles_elsewhere`), и снимается со своей причиной, называющей
направление. Оно не уходило: когда письмо другой роли не дойдёт, сборка напишет сайту заново.
Только снятые так — письмо, остановленное человеком или стоп-листом, держит донора по-прежнему.

**Донор снова открыт для первого письма**, когда мёртвые все его письма —
на обоих этапах, по тому же правилу «одно письмо на сайт», — никто
у донора не ответил и не отписался, а потолок попыток не выбран.
«Не писать» человека — остановленное письмо на живой адрес — живым
и остаётся: это решение по донору, а не по письму (`review.skip`).

**Адрес, на который уже уходило письмо, первым второй раз не берётся.**
Мёртвый не берётся и так; этот запрет — про мягкий отказ (сервер
получателя отказал, адрес жив) и про ошибку разбора: второе «первое
письмо» тому же человеку — ровно то, от чего правило «одно письмо за раз».

**Потолок — три адреса на сайт** (`MAX_ADDRESSES`), на оба этапа сразу.
Для получателя письма на все найденные ящики сайта и есть рассылка
по всем ящикам, даже если каждое следующее уходило после отказа
предыдущего. Три — это лучший адрес и два запасных: у сайта с четырьмя
мёртвыми ящиками из найденных беда не в выборе ящика. Потолок живёт
здесь, а не в настройках, по той же причине, что потолок цепочки
(`chain.MAX_STEPS`): это граница вежливости, а не ручка под нагрузку.

**Номер попытки едет в ключ письма** (`building.idempotency_key`):
первая попытка — прежний ключ, следующие — с `:a<номер>`. Номер —
сколько первых писем этапа у домена уже было, плюс один: к моменту
сборки все они мёртвые, иначе донора нет в отборе. Снятые ссылкой
считаются тоже: ключ письма не повторяется. Два сборщика,
одновременно увидевшие донора открытым, считают один номер — и вторая
вставка падает на уникальности ключа, а не отправляет второе письмо.

**Новая попытка — новый диалог.** Диалог — переписка с одним человеком
(`ThreadModel`: домен, рассылка, адрес), а новая попытка всегда на другой
адрес. Донор видит первое письмо на другой ящик — для него это новая
переписка, и добивки ссылаются на её первое письмо, а не на то, что не дошло.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, ScalarSelect, Select, and_, func, or_, select
from sqlalchemy.orm import InstrumentedAttribute, aliased
from sqlalchemy.orm.util import AliasedClass

from backend.features.contacts.preference import DEAD
from backend.features.core.domain import MessageStatus, ReplyKind, Stage, ThreadStatus
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.letters.chain import FIRST_STEP

#: Сколько адресов сайта пробуем первым письмом, прежде чем остановиться.
MAX_ADDRESSES = 3

#: В очереди или в пути: исход неизвестен, письмо живое при любом адресе.
PENDING = (MessageStatus.QUEUED, MessageStatus.SENDING)

#: Ответы, после которых донору больше не пишут ни на какой адрес.
ANSWERS = (ReplyKind.HUMAN, ReplyKind.UNSUBSCRIBE)

#: Своя причина у письма, остановленного снятием его ссылки (`crawl/offer_stop.py`), —
#: в поле причины недоставки: письмо не ушло, и человек, читающий судьбу домена, видит
#: почему. По ней такое письмо и узнаётся (`withdrawn`) — текст не менять.
LINK_WITHDRAWN = "ссылку письма сняли: «не пишем» по ней — пишем под действующую"

#: То же у первого письма бизнесу ниши, чей пример площадки попал в «кому не пишем»
#: (`letters/offer_standing.py`): письмо не уходило, и следующая сборка напишет бизнесу
#: заново — с примером оттуда, где пишем. Текст не менять.
EXAMPLE_WITHDRAWN = "пример площадки письма — из «кому не пишем»: пишем с другим примером"

#: То же у первого письма оффера по ссылке на площадке поставщика агентства, внесённого
#: в стоп-лист после сборки (`letters/offer_standing.py`, `crawl/promote.apply_suppliers`):
#: письмо не уходило, и следующая сборка напишет рекламодателю под ссылку другого донора,
#: если она есть. Текст не менять.
SUPPLIER_WITHDRAWN = "площадка ссылки письма — поставщик агентства: пишем под ссылку другого донора"

#: Первое письмо, снятое при отправке: у сайта живое письмо другого этапа (`roles_elsewhere`,
#: `letters/site_role.py`). Причина — это начало и направление того письма словами
#: (`elsewhere_reason`): «сайт в работе у другого направления: продажи». Текст не менять.
IN_WORK_ELSEWHERE = "сайт в работе у другого направления"

#: Направление словами — по этапу живого письма в другой роли.
DIRECTIONS: dict[Stage, str] = {
    Stage.DONORS: "доноры",
    Stage.ADVERTISERS: "рекламодатели",
    Stage.SALES: "продажи",
}


def elsewhere_reason(stage: Stage) -> str:
    """Причина снятого письма: сайт в работе у направления этапа `stage`."""
    return f"{IN_WORK_ELSEWHERE}: {DIRECTIONS[stage]}"


#: Причины снятого письма, которое не уходило (`withdrawn`). Этап без направления в
#: `DIRECTIONS` роняет загрузку модуля, а не теряет письмо из «не уходило».
WITHDRAWN_REASONS = (
    LINK_WITHDRAWN,
    EXAMPLE_WITHDRAWN,
    SUPPLIER_WITHDRAWN,
    *(elsewhere_reason(stage) for stage in Stage),
)

#: Те же ответы, отмеченные на самом диалоге (отписка кнопкой — `optout`).
ANSWERED_THREADS = (ThreadStatus.REPLIED, ThreadStatus.UNSUBSCRIBED)

# --- слова для карточки донора: почему письмо не соберётся ---

ANSWERED = "донор ответил — разговор идёт в его диалоге, на другие адреса не пишем"
PENDING_LETTER = "письмо донору уже собрано и ждёт отправки — второе не собирается"
WRITTEN = "донору уже писали — следующие письма идут в тот же диалог"
CAPPED = (
    f"не дошло ни одно из {MAX_ADDRESSES} писем на разные адреса — это потолок: "
    "дальше это уже рассылка по всем ящикам сайта"
)
EXHAUSTED = "адреса кончились — все прежние не дошли"

#: Письмо, адрес или рассылка — или их псевдоним в подзапросе.
_Letter = type[MessageModel] | AliasedClass[MessageModel]
_Contact = type[ContactModel] | AliasedClass[ContactModel]
_Campaign = type[CampaignModel] | AliasedClass[CampaignModel]
#: Номер домена из внешнего запроса, к которому привязан подзапрос, — или сам номер.
_DomainId = InstrumentedAttribute[int] | ColumnElement[int] | int


def next_address_note(earlier: int) -> str:
    """Что сказать рядом с адресом письма, если прежние письма донору не дошли."""
    if earlier == 1:
        return "прошлый адрес не дошёл — письмо уйдёт на следующий"
    return "прошлые адреса не дошли — письмо уйдёт на следующий"


def withdrawn(letter: _Letter) -> ColumnElement[bool]:
    """Письмо остановлено снятием своей ссылки, примера площадки или площадки-поставщика
    или снято при отправке, потому что сайт в работе у другого направления: не уходило,
    и в отборе его нет.

    Причина — через `coalesce`, а не прямым сравнением: у остановленного человеком
    причины нет (`NULL`), сравнение дало бы `NULL`, и под `NOT` в отборе такое письмо
    выпадало бы из «писали» — сборка воскрешала бы остановленное человеком.
    """
    return and_(
        letter.status == MessageStatus.STOPPED,
        func.coalesce(letter.failure_reason, "").in_(WITHDRAWN_REASONS),
    )


def dead(letter: _Letter) -> ColumnElement[bool]:
    """Письмо не дошло и не дойдёт ни до кого — или остановлено снятием ссылки.

    Адрес письма смотрится через свой псевдоним: в запросе отбора адреса
    уже стоят во внешнем `FROM`, и без псевдонима подзапрос проверил бы
    не адрес письма, а адрес, который выбирают сейчас.
    """
    addressee = aliased(ContactModel)
    buried = (
        select(addressee.id)
        .where(addressee.id == letter.contact_id, addressee.verification_status == DEAD)
        .exists()
    )
    return or_(
        letter.status == MessageStatus.BOUNCED,
        and_(letter.status.not_in(PENDING), buried),
        withdrawn(letter),
    )


def live_letter(domain_id: _DomainId) -> ColumnElement[bool]:
    """У домена есть письмо, которое дошло, в пути или остановлено человеком."""
    letter = aliased(MessageModel)
    return select(letter.id).where(letter.domain_id == domain_id, ~dead(letter)).exists()


def in_other_role(letter: _Letter, campaign: _Campaign, stage: Stage) -> ColumnElement[bool]:
    """Письмо `letter` рассылки `campaign` живое и в другой роли, чем этап `stage`: живое —
    то же, что у отбора (`dead`): в очереди, в пути, дошедшее, остановленное человеком.
    Одно условие на отправку (`site_role.py`), перезапрос цены и карточку донора
    (`other_role.elsewhere`)."""
    return and_(campaign.stage != stage, ~dead(letter))


def roles_elsewhere(domain_id: _DomainId, stage: Stage) -> Select[tuple[Stage]]:
    """Этапы живых писем домена в других ролях, чем `stage` (`in_other_role`), — по одной
    строке на письмо: какое направление назвать, решает спрашивающий."""
    letter, campaign = aliased(MessageModel), aliased(CampaignModel)
    return (
        select(campaign.stage)
        .join(letter, letter.campaign_id == campaign.id)
        .where(letter.domain_id == domain_id, in_other_role(letter, campaign, stage))
    )


def pending_letter(domain_id: _DomainId) -> ColumnElement[bool]:
    """У домена есть письмо в очереди или в пути."""
    letter = aliased(MessageModel)
    return (
        select(letter.id).where(letter.domain_id == domain_id, letter.status.in_(PENDING)).exists()
    )


def written_before(domain_id: _DomainId) -> ColumnElement[bool]:
    """Домену уже уходило хоть одно письмо — в любом состоянии, на любом этапе.
    Снятое ссылкой не в счёт: оно не уходило (`withdrawn`)."""
    letter = aliased(MessageModel)
    return select(letter.id).where(letter.domain_id == domain_id, ~withdrawn(letter)).exists()


def answered(domain_id: _DomainId) -> ColumnElement[bool]:
    """Кто-то у донора ответил или отписался — на любое письмо, с любого адреса.

    Ответ ищется и по диалогу, и по письму: связь с письмом стирается при
    его удалении, а диалог остаётся (`replies.repository.domain_of`). Ответ
    сильнее мёртвого адреса: донор, ответивший с одного ящика, второго
    первого письма на другой не получает, даже если первый потом умер.
    """
    by_thread, reply = aliased(ThreadModel), aliased(ReplyModel)
    on_thread = (
        select(reply.id)
        .join(by_thread, by_thread.id == reply.thread_id)
        .where(by_thread.domain_id == domain_id, reply.kind.in_(ANSWERS))
        .exists()
    )
    by_letter, letter_reply = aliased(MessageModel), aliased(ReplyModel)
    on_letter = (
        select(letter_reply.id)
        .join(by_letter, by_letter.id == letter_reply.message_id)
        .where(by_letter.domain_id == domain_id, letter_reply.kind.in_(ANSWERS))
        .exists()
    )
    thread = aliased(ThreadModel)
    marked = (
        select(thread.id)
        .where(thread.domain_id == domain_id, thread.status.in_(ANSWERED_THREADS))
        .exists()
    )
    return or_(on_thread, on_letter, marked)


def first_letters(domain_id: _DomainId) -> ScalarSelect[int]:
    """Сколько первых писем уже было у домена — на обоих этапах. Снятое ссылкой
    адреса не пробовало и потолок не тратит."""
    letter = aliased(MessageModel)
    return (
        select(func.count(letter.id))
        .where(letter.domain_id == domain_id, letter.step == FIRST_STEP, ~withdrawn(letter))
        .scalar_subquery()
    )


def under_cap(domain_id: _DomainId) -> ColumnElement[bool]:
    """Потолок попыток не выбран."""
    return first_letters(domain_id) < MAX_ADDRESSES


def open_for_letter(domain_id: _DomainId) -> ColumnElement[bool]:
    """Донору можно собрать первое письмо — если найдётся свежий адрес (`fresh`)."""
    return and_(~live_letter(domain_id), ~answered(domain_id), under_cap(domain_id))


def attempt_number(domain_id: _DomainId, stage: Stage) -> ScalarSelect[int]:
    """Номер попытки для ключа нового первого письма: прежние первые письма
    этапа плюс один. Письма не удаляются, поэтому номер не повторяется."""
    letter, campaign = aliased(MessageModel), aliased(CampaignModel)
    return (
        select(func.count(letter.id) + 1)
        .join(campaign, campaign.id == letter.campaign_id)
        .where(letter.domain_id == domain_id, letter.step == FIRST_STEP, campaign.stage == stage)
        .scalar_subquery()
    )


def fresh(contact: _Contact) -> ColumnElement[bool]:
    """Адрес жив и писем на него ещё не уходило. Снятое ссылкой — не уходило.

    `IS DISTINCT FROM`, а не `!=`: у адреса без отметки `NULL`, и `!=`
    выбросил бы его вместе с мёртвыми — то есть все найденные лестницей.
    """
    letter = aliased(MessageModel)
    untried = ~select(letter.id).where(letter.contact_id == contact.id, ~withdrawn(letter)).exists()
    return and_(contact.verification_status.is_distinct_from(DEAD), untried)


def fresh_address(domain_id: _DomainId) -> ColumnElement[bool]:
    """У домена есть живой адрес, на который писем ещё не уходило."""
    contact = aliased(ContactModel)
    return select(contact.id).where(contact.domain_id == domain_id, fresh(contact)).exists()
