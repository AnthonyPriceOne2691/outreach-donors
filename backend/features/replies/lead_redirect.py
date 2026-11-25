"""Переадресация в диалоге рекламодателя: «да, готовы обсудить, но напишите вот на этот адрес».

Просьба стороны задачи (слово владельца 16.11.2026: «да, делай сегодня»). Рекламодатель отвечает
на первое письмо «We ask you to contact the necessary Department via partners@…» — до этого диалог
вставал «лид — ждёт человека», и лид терялся. Теперь система сама шлёт ТО ЖЕ первое письмо на
названный адрес, и всё — в ТОЙ ЖЕ переписке: наше письмо → их ответ → наше письмо на новый адрес →
ответы нового адресата ложатся туда же (метка ответа — по письму, `letters/reply_to.py`).

    приём: ответ человека рекламодателю называет чужой адрес → строка `lead_redirects` «ждёт»
    проход: модель — просят ли писать туда и куда (`redirect_ask.py`) → проверка адреса →
    письмо в переписку (`letters/redirect_letter.py`) → отправка в рабочие часы получателя
    и в лимитах ящика (`letters/redirect_send.py`)

**Модель не зовётся, если звать не о чем.** Код сначала ищет адреса в написанном человеком (без
цитаты: в ней наше письмо и наши адреса): не наши, не адрес отправителя ответа, не адрес нашего
письма (`candidates`). Таких нет — строки нет, модель не спрошена. Есть — модель отвечает строгой
формой и выбирает адрес только меткой замаскированного текста (`letters/masking.py`): сама она
адрес не пишет, и адрес гарантированно взят из ответа дословно.

**Переадресация в диалоге одна** (`held`): письмо на адрес из ответа уже заведено — второй раз не
пересылаем; человек взял лид в работу или сам ответил в переписке — дальше ведёт он. Проверяется
при приёме (строку не заводим), при разборе и перед отправкой: человек мог взять лид, пока письмо
ждало рабочих часов получателя, — тогда письмо снимается с причиной.

**Только при приёме и разовым проходом.** Приём ставит на разбор новые ответы (`note`), разовый
проход по диалогам, уже ждущим человека, — команда `outreach lead-redirect` с предпросмотром по
умолчанию. Сам проход старых ответов не берёт: после выкатки письма не уходят по диалогам, которых
человек не видел в предпросмотре. Рубильник — `OUTREACH_LEAD_REDIRECT` (`config/redirect.py`).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import redirect as redirect_cfg
from backend.features.core.domain import ReplyDecision, ReplyKind, Stage
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import MessageModel, ReplyModel, SenderModel
from backend.features.core.models.redirect import LeadRedirectModel
from backend.features.letters.chain import ANSWER_STEP, REDIRECT_STEP
from backend.features.letters.masking import EMAIL_RE
from backend.features.replies.inbound import MAX_TEXT_CHARS
from backend.features.replies.quoting import written_by_hand
from backend.features.replies.redirect import our_label

if TYPE_CHECKING:
    from backend.features.replies.repository import Addressee

#: Почему переадресации нет или письмо снято — словами для экрана и команды.
ONCE = "в этой переписке уже переслали на другой адрес — второй раз не пересылаем"
TAKEN = "лид уже взят в работу — дальше его ведёт человек"
#: «В отказ» (25.11.2026): рекламодатель отказался — на другой адрес оффер не шлём.
REFUSED = "лид в отказе — на другой адрес не пересылаем"
#: Решение человека по лиду → слова причины и кто решил; пусто — взят в работу.
_DECIDED: dict[str | None, tuple[str, str]] = {ReplyDecision.REFUSED.value: (REFUSED, "отказал")}
ANSWERED = "в переписке уже ответили — дальше её ведёт человек"
OURS = "это наш собственный адрес"
SENDER = "это адрес, с которого ответили"
WROTE_TO = "на этот адрес ушло наше письмо"


class State(StrEnum):
    """Что решено по ответу (`lead_redirects.state`)."""

    #: Ждёт прохода: модель не спрошена или проверка адреса не кончилась.
    PENDING = "pending"
    #: Не просят писать на другой адрес.
    NONE = "none"
    #: Просят, но не пересылаем — почему, словами в `reason`.
    REFUSED = "refused"
    #: Письмо на адрес из ответа заведено; ушло ли — статус письма.
    LETTER = "letter"


def enabled() -> bool:
    """Включена ли переадресация: приём ставит ответы на разбор, проход идёт."""
    return redirect_cfg.ENABLED


@dataclass(frozen=True, slots=True)
class Seen:
    """Чьи адреса в ответе — не адрес для переадресации: отправителя ответа, нашего письма
    в этой переписке и наших доменов рассылки."""

    sender: str
    wrote_to: frozenset[str]
    ours: frozenset[str]

    def why_not(self, address: str) -> str | None:
        """Почему адрес — не кандидат, словами; `None` — кандидат."""
        local, _, domain = address.partition("@")
        own = our_label(local) or any(
            domain == mine or domain.endswith(f".{mine}") for mine in self.ours
        )
        checks = (
            (address == self.sender, SENDER),
            (address in self.wrote_to, WROTE_TO),
            (own, OURS),
        )
        return next((why for failed, why in checks if failed), None)


def seen_from(sender: str | None, wrote_to: Iterable[str], ours: Iterable[str]) -> Seen:
    """`Seen` из сырых значений базы — без регистра и краевых пробелов."""
    return Seen(
        sender=(sender or "").strip().lower(),
        wrote_to=frozenset(email.strip().lower() for email in wrote_to if email),
        ours=frozenset(domain.strip().lower() for domain in ours if domain),
    )


def written(text: str) -> str:
    """Что написал человек: без цитаты, под тем же потолком длины, что у разбора ответов."""
    return written_by_hand(text[:MAX_TEXT_CHARS])


def candidates(text: str, seen: Seen) -> tuple[str, ...]:
    """Адреса в написанном человеком, кроме отправителя ответа, нашего письма и наших, —
    по порядку в тексте и без повторов. Пусто — модель не зовётся."""
    found: list[str] = []
    for raw in EMAIL_RE.findall(written(text)):
        address = raw.strip(".").lower()
        if address and address not in found and seen.why_not(address) is None:
            found.append(address)
    return tuple(found)


async def seen_for(session: AsyncSession, reply: ReplyModel, thread_id: int) -> Seen:
    """Адреса, которые в этом ответе не кандидаты: отправителя, наших писем переписки, наши."""
    wrote_to = await session.scalars(
        select(ContactModel.email)
        .join(MessageModel, MessageModel.contact_id == ContactModel.id)
        .where(MessageModel.thread_id == thread_id)
    )
    ours = await session.scalars(select(SenderModel.domain).distinct())
    return seen_from(reply.from_email, wrote_to.all(), ours.all())


async def held(session: AsyncSession, thread_id: int, *, letting: int | None = None) -> str | None:
    """Почему в переписке переадресации не будет — словами; `None` — может быть.

    Письмо на адрес из ответа уже заведено (одно на переписку), в переписке ответили (человек или
    автопилот агента — `answers.py`) или ответ рекламодателя в ней взяли в работу либо отправили в
    отказ. `letting` —
    письмо переадресации, которое проход сейчас отправляет: оно не второе, а то самое."""
    statement = select(MessageModel.step).where(MessageModel.thread_id == thread_id)
    if letting is not None:
        statement = statement.where(MessageModel.id != letting)
    steps = set((await session.scalars(statement)).all())
    if REDIRECT_STEP in steps:
        return ONCE
    if ANSWER_STEP in steps:
        return ANSWERED
    taken = (
        await session.execute(
            select(ReplyModel.reviewed_by, ReplyModel.reviewed_at, ReplyModel.decision)
            .where(
                ReplyModel.thread_id == thread_id,
                ReplyModel.kind == ReplyKind.HUMAN,
                ReplyModel.reviewed_at.is_not(None),
            )
            .order_by(ReplyModel.reviewed_at)
            .limit(1)
        )
    ).first()
    if taken is None:
        return None
    who, when, decision = taken
    words, verb = _DECIDED.get(decision, (TAKEN, "взял"))
    return f"{words}: {verb} {who or 'человек'} {when:%d.%m.%Y}"


async def note(
    session: AsyncSession, reply: ReplyModel, addressee: Addressee
) -> LeadRedirectModel | None:
    """При приёме: ответ человека на письмо рекламодателю, где назван чужой адрес, — на разбор
    проходу. Без модели и без сети: адреса ищет код, решения о переписке — запросы к базе.
    `None` — разбирать нечего: переадресация выключена, не тот ответ, адресов нет или
    переписку уже ведут (`held`)."""
    if not enabled():
        return None
    return await enlist(
        session, reply, stage=addressee.stage, thread_id=addressee.message.thread_id
    )


async def enlist(
    session: AsyncSession, reply: ReplyModel, *, stage: Stage | None, thread_id: int | None
) -> LeadRedirectModel | None:
    """Поставить ответ на разбор переадресации — без рубильника: его смотрит приём (`note`), а
    разовый проход командой зовёт сам человек. `None` — разбирать нечего."""
    if stage is not Stage.ADVERTISERS or reply.kind is not ReplyKind.HUMAN or thread_id is None:
        return None
    if not candidates(reply.raw_body, await seen_for(session, reply, thread_id)):
        return None
    if await held(session, thread_id) is not None:
        return None
    row = LeadRedirectModel(reply_id=reply.id, thread_id=thread_id, state=State.PENDING)
    session.add(row)
    await session.flush()
    return row


async def rows_of(
    session: AsyncSession, thread_ids: Collection[int] | None = None
) -> list[LeadRedirectModel]:
    """Решения переадресации — переписок `thread_ids` или все; по переписке — по порядку ответов."""
    statement = select(LeadRedirectModel).order_by(
        LeadRedirectModel.thread_id, LeadRedirectModel.reply_id
    )
    if thread_ids is not None:
        statement = statement.where(LeadRedirectModel.thread_id.in_(list(thread_ids)))
    return list((await session.scalars(statement)).all())
