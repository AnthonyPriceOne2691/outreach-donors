"""Куда уходит перезапрос цены: в переписку, где донор отвечал, или новым первым письмом.

Решает история донора на Этапе 1 — его переписки в рассылках доноров, прочитанные разом
(`recheck_facts`). Правила идут по порядку, и первое сработавшее — ответ (`conversation`):

1. **Отписался в любой переписке** — отказ. Стоп-лист проверит и отправка, но отписка
   ответом закрывает адрес, а не сайт, и второй ящик того же сайта получил бы письмо
   в обход «больше не пишите».
2. **Ответ ждёт разбора** — отказ: цена может быть уже в нём, и спрашивать её снова,
   не прочитав ответа, — спрашивать то, что донор уже сказал.
3. **Донор отвечал** — в переписку с его последним словом (`in_thread`): её ящиком, на
   адрес, с которого он ответил, веткой к его письму. Слово донора — письмо человека или
   подтверждённая цена, и из автоответа с суммой (`threads.donor_word`). Перезапрос уже
   ушёл, а донор после него молчит, — отказ: второй до его ответа не уходит.
4. **Писали, а донор молчит** — отказ: ещё одно письмо тому, кто промолчал на цепочку, —
   то, от чего держит год тишины (решение Anthony 22.09.2026, `okf/selection-gates.md`).
5. **Письмо остановили, не отправив** («не писать» или стоп-лист) — отказ: «не писать» —
   решение по донору (`attempts.py`), и перезапрос его не обходит.
6. **Переписки нет** — новым первым письмом (`first_letter`): цену вписали руками,
   а донору не писали — или все письма не дошли. Ящик и адрес — как у первого письма:
   свободный ящик этапа, дневной лимит, потолок адресов сайта и свежий адрес; добивок
   нет — перезапрос одиночный (`settle._next_due`, решение 12.10.2026). «Ответившему
   не пишем» не спрашивается — ответа на Этапе 1 и нет.

**Лимиты ящика — до заведения письма** (проверка 12.10.2026): перезапрос в переписку
считается и в дневном лимите ящика, и в часовом лейне писем переписки, — пачка
по двадцати нажатий иначе давала всплеск с одного ящика. Не влезает — отказ словами.

Отказ — `RecheckRefusedError` со словами для экрана: тот же текст видит человек
в списке до нажатия и в ответе на нажатие.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from backend.config import outreach as cfg
from backend.features.contacts.preference import DEAD
from backend.features.contacts.quality import rejection_reason
from backend.features.core.domain import Stage
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.outreach import ReplyModel, SenderModel
from backend.features.letters import attempts, other_role
from backend.features.letters.answers import subject_for
from backend.features.letters.mailbox import silence_words
from backend.features.letters.recheck_facts import Boxes, Facts, Talk, Waiting
from backend.features.letters.sending import SendError
from backend.features.outreach import limits
from backend.features.outreach import senders as sender_rules


class RecheckRefusedError(SendError):
    """Перезапрос не уходит. Сообщение называет причину и что делать."""


class Way(StrEnum):
    """Каким письмом уходит перезапрос."""

    #: Ответом в переписку, где донор отвечал.
    THREAD = "thread"
    #: Новым первым письмом: переписки нет.
    FIRST = "first"


@dataclass(frozen=True, slots=True)
class Plan:
    """Куда и кому уйдёт перезапрос — до заведения письма."""

    way: Way
    contact_id: int
    email: str
    #: Переписка и её рассылка — у ответа в переписку; у первого письма заводятся новые.
    thread_id: int | None = None
    campaign_id: int | None = None
    #: Ящик переписки — у ответа в неё; первому письму ящик выбирает отправка.
    sender_id: int | None = None
    mailbox: str | None = None
    #: Письмо донора, к которому ложится ветка, и тема ответа на него — «Re:».
    in_reply_to: str | None = None
    subject: str | None = None


def plan(facts: Facts, donor: DonorModel, host: str, subject: str, *, sales_visible: bool) -> Plan:
    """Куда уйдёт перезапрос — или отказ словами. `subject` — тема шаблона: ею уходит
    новое письмо, и она же запасная у ответа в переписку без темы."""
    found = facts.talks.get(donor.domain_id, [])
    talk = conversation(host, found)
    queued = facts.queued.get(donor.domain_id)
    if queued is not None:
        # Собранный раньше и не ушедший — в свою переписку.
        talk = next((one for one in found if one.thread.id == queued.thread_id), talk)
    if talk is None:
        return first_letter(facts, donor, host, sales_visible=sales_visible)
    return in_thread(facts, host, talk, subject)


def conversation(host: str, found: Sequence[Talk]) -> Talk | None:
    """Переписка для перезапроса — или отказ словами; `None` — переписки нет, письмо первое."""
    for talk in found:
        if talk.unsubscribed:
            raise RecheckRefusedError(
                f"{host} отписался в переписке №{talk.thread.id}: писать ему больше нельзя."
            )
    for talk in found:
        if talk.waits:
            raise RecheckRefusedError(
                f"Ответ {host} в переписке №{talk.thread.id} ждёт разбора: сначала разберите "
                "его — цена может быть уже в нём."
            )
    answered = _latest_answered(found)
    if answered is None:
        _silent_or_held(host, found)
        return None
    word, asked = answered.last_word, answered.asked_at
    if word is not None and asked is not None and asked > word.created_at:
        raise RecheckRefusedError(
            f"Ждём ответа {host} на перезапрос от {asked:%d.%m.%Y} (переписка "
            f"№{answered.thread.id}): второй до его ответа не уходит. Ответит — цена "
            "обновится из ответа."
        )
    return answered


def _latest_answered(found: Sequence[Talk]) -> Talk | None:
    """Переписка с последним словом донора; `None` — он не отвечал нигде."""
    best: tuple[datetime, int, Talk] | None = None
    for talk in found:
        word = talk.last_word
        if word is not None and (best is None or (word.created_at, word.id) > best[:2]):
            best = (word.created_at, word.id, talk)
    return None if best is None else best[2]


def _silent_or_held(host: str, found: Sequence[Talk]) -> None:
    """Писали, а донор молчит, или письмо остановили, не отправив, — отказ словами."""
    for talk in found:
        if talk.reached() is not None:
            raise RecheckRefusedError(
                f"{host} не ответил на наши письма (переписка №{talk.thread.id}): ещё одно "
                "письмо тому, кто промолчал, — путь к жалобе на спам. Ответит — цена обновится "
                "из ответа сама; знаете цену — укажите её на карточке донора."
            )
    for talk in found:
        letter = talk.held()
        if letter is not None:
            raise RecheckRefusedError(
                f"Письмо {host} №{letter.id} остановили, не отправив: «не писать» или "
                "стоп-лист — это решение по донору, и перезапрос его не обходит."
            )


def in_thread(facts: Facts, host: str, talk: Talk, subject: str) -> Plan:
    """Перезапрос ответом в переписку: её ящиком, на адрес ответившего, веткой к его письму."""
    word = talk.last_word
    first = talk.first_gone()
    if word is None or first is None:
        raise RecheckRefusedError(
            f"В переписке №{talk.thread.id} первое письмо не уходило к почте или {host} "
            "в ней не отвечал: ящика и ветки у перезапроса нет."
        )
    box = facts.boxes.senders.get(first.sender_id) if first.sender_id is not None else None
    domain = facts.boxes.domains.get(box.domain) if box is not None else None
    # Те же слова, что на карточке переписки: ящик удалён, выключен или домен на паузе.
    silence = silence_words(box, domain, gone="первого письма")
    if silence is not None or box is None:
        raise RecheckRefusedError(silence or f"Ящика переписки №{talk.thread.id} нет.")
    _fits(facts.boxes, box, facts.moment)
    contact = _answering(facts, talk, word)
    if contact is None:
        raise RecheckRefusedError(
            f"У переписки №{talk.thread.id} не осталось живого адреса: тот, с которого {host} "
            "отвечал, и тот, куда писали, письма больше не принимают."
        )
    if contact.email in facts.stopped_emails:
        raise RecheckRefusedError(
            f"Адрес {contact.email} в стоп-листе: писать на него нельзя. Снять запись — "
            "на экране «Стоп-лист»."
        )
    return Plan(
        way=Way.THREAD,
        contact_id=contact.id,
        email=contact.email,
        thread_id=talk.thread.id,
        campaign_id=talk.thread.campaign_id,
        sender_id=box.id,
        mailbox=box.email,
        in_reply_to=word.inbound_message_id,
        subject=subject_for(_thread_subject(word, talk) or subject),
    )


def _thread_subject(word: ReplyModel, talk: Talk) -> str | None:
    """Тема ветки: у письма донора, а без неё — у нашего первого письма. Пустое «Re:»
    читалось бы письмом ни о чём (проверка 12.10.2026)."""
    for said in (word.subject, *(m.subject for m in talk.messages if m.step == 0)):
        if said and said.strip():
            return said.strip()
    return None


def _fits(boxes: Boxes, box: SenderModel, moment: datetime) -> None:
    """Ящик переписки сегодня может ещё одно письмо: дневной лимит (первые письма
    и перезапросы), сниженный на сутки лимит и часовой лейн писем переписки."""
    sent = boxes.sent_today.get(box.id, 0)
    cut = boxes.cuts.get(box.id)
    if cut is not None and sent >= cut:
        raise RecheckRefusedError(
            f"Ящику {box.email} снижен лимит на сутки (мягкие отказы): {sent} из {cut} — "
            "перезапрос уходит с ящика переписки и уйдёт с него завтра."
        )
    allowance = sender_rules.warmup_state(box, now=moment).allowance
    if sent >= allowance:
        raise RecheckRefusedError(
            f"Ящик {box.email} выбрал дневной лимит ({sent} из {allowance}): перезапрос "
            "уходит с ящика переписки и уйдёт с него завтра."
        )
    hour, ceiling = boxes.this_hour.get(box.id, 0), cfg.FOLLOWUP_PER_SENDER_PER_HOUR
    if hour >= ceiling:
        raise RecheckRefusedError(
            f"Ящик {box.email} выбрал часовой запас писем переписки ({hour} из {ceiling}): "
            "перезапрос уйдёт с него через час."
        )


def _answering(facts: Facts, talk: Talk, word: ReplyModel) -> ContactModel | None:
    """Адрес, с которого донор ответил последним (приём запоминает его контактом), — или
    адрес переписки. Мёртвый не берётся: перезапрос на него вернулся бы отказом доставки."""
    contacts = facts.contacts.get(talk.thread.domain_id, [])
    alive = [contact for contact in contacts if contact.verification_status != DEAD]
    said = (word.from_email or "").strip().lower()
    answered = next((c for c in alive if said and c.email.lower() == said), None)
    return answered or next((c for c in alive if c.id == talk.thread.contact_id), None)


def first_letter(facts: Facts, donor: DonorModel, host: str, *, sales_visible: bool) -> Plan:
    """Перезапрос новым первым письмом — на лучший живой адрес, куда ещё не писали."""
    other = facts.elsewhere.get(donor.domain_id)
    if other is not None:
        raise RecheckRefusedError(_elsewhere_words(host, other, sales_visible=sales_visible))
    if facts.firsts.get(donor.domain_id, 0) >= attempts.MAX_ADDRESSES:
        raise RecheckRefusedError(f"{host}: {attempts.CAPPED}.")
    contacts = facts.contacts.get(donor.domain_id, [])
    contact = next(
        (
            c
            for c in contacts
            if c.verification_status != DEAD
            and c.id not in facts.written
            and c.email not in facts.stopped_emails
        ),
        None,
    )
    if contact is None:
        raise RecheckRefusedError(_no_address(host, known=bool(contacts)))
    bad = rejection_reason(contact.email)
    if bad is not None:
        raise RecheckRefusedError(
            f"Адрес {contact.email} не проходит проверку ({bad}): перезапрос на него не уйдёт."
        )
    _free_box(facts)
    return Plan(way=Way.FIRST, contact_id=contact.id, email=contact.email)


def _free_box(facts: Facts) -> None:
    """Сегодня есть ящик для первого письма — тем же фильтром, что у отправки
    (`mailbox._free_box`): лимиты домена и направления, разгон, сниженные лимиты."""
    boxes, moment = facts.boxes, facts.moment
    screened = limits.screen(
        list(boxes.senders.values()),
        stage=Stage.DONORS,
        sent_today=boxes.sent_today,
        domains=boxes.domains,
        direction_limit=cfg.direction_limit(Stage.DONORS.value),
        now=moment,
        cuts=boxes.cuts,
        direction_unread=cfg.direction_unread(Stage.DONORS.value),
    )
    spot = sender_rules.pick(
        screened.fit, sent_today=boxes.sent_today, stage=Stage.DONORS, now=moment
    )
    if spot is None:
        why = screened.why() or "все ящики выключены или выбрали дневной лимит"
        raise RecheckRefusedError(
            f"Сегодня писать некому: {why}. Перезапрос новым письмом уйдёт, когда у ящиков "
            "откроется лимит."
        )


def _elsewhere_words(host: str, other: Waiting, *, sales_visible: bool) -> str:
    """Сайту писали в другой роли — отказ: одному адресату не пишут и как донору, и как
    рекламодателю (`recipients._not_written`). Слова — те же, что у карточки донора
    (`other_role`), с именем сайта; продажи — подробно только с их правом."""
    return f"{host} {other_role.words(other, sales_visible=sales_visible)}"


def _no_address(host: str, *, known: bool) -> str:
    """Почему новому письму некуда уйти — словами."""
    if not known:
        return (
            f"У {host} нет адреса: новому письму уйти некуда. Найти или вписать адрес — "
            "на карточке донора."
        )
    return (
        f"У {host} не осталось адреса для нового письма: на прежние уже писали, они не "
        "дошли или в стоп-листе."
    )
