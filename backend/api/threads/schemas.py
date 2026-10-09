"""Что отдают маршруты диалогов."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from backend.api.agent.schemas import DraftCard
from backend.api.letters.schemas import Corridor
from backend.features.agent.drafts import ShownDraft
from backend.features.core.domain import MessageStatus, ReplyKind, Stage
from backend.features.core.models.attachment import ReplyAttachmentModel
from backend.features.core.models.outgoing_attachment import OutgoingAttachmentModel
from backend.features.core.models.outreach import MessageModel, ReplyModel
from backend.features.letters import outgoing_files
from backend.features.letters.mailbox import ThreadMail
from backend.features.letters.outgoing_store import NO_FILES, ThreadFiles
from backend.features.outreach.repository import ThreadDetail, ThreadRow
from backend.features.outreach.threads import ThreadState, review_of
from backend.features.replies.quoting import written_by_hand


class ThreadCard(BaseModel):
    """Строка списка диалогов.

    Состояние приходит вычисленным, а не хранимым: отдельное поле
    рассинхронизируется с письмами при первом сбое, и список врёт именно
    там, где по нему принимают решения.
    """

    id: int
    host: str
    contact_email: str | None
    campaign: str
    #: Этап рассылки: донору писали о цене, рекламодателю — оффер.
    stage: Stage
    state: ThreadState
    messages_sent: int
    last_event_at: datetime | None
    last_reply_at: datetime | None
    price_white: Decimal | None
    price_grey: Decimal | None
    currency: str | None

    @classmethod
    def of(cls, row: ThreadRow) -> ThreadCard:
        return cls(
            id=row.thread.id,
            host=row.host,
            contact_email=row.contact_email,
            campaign=row.campaign_name,
            stage=row.stage,
            state=row.summary.state,
            messages_sent=row.summary.messages_sent,
            last_event_at=row.summary.last_event_at,
            last_reply_at=row.summary.last_reply_at,
            price_white=row.summary.price_white,
            price_grey=row.summary.price_grey,
            currency=row.summary.currency,
        )


class LetterFileCard(BaseModel):
    """Файл к нашему письму: сведения без тела. У письма (`LetterCard.attachments`)
    сам файл — отдельным запросом (`GET /api/messages/{message_id}/attachments/{id}`),
    только на скачивание; ждущий ответа (`ThreadView.pending_files`) — тот, что
    приложили к переписке и ещё ни с одним письмом не отправили."""

    id: int
    name: str
    #: Байт.
    size: int

    @classmethod
    def of(cls, row: OutgoingAttachmentModel) -> LetterFileCard:
        return cls(id=row.id, name=row.name, size=row.size)


class FileRulesCard(BaseModel):
    """Правила файла к ответу — те же числа, что проверяет сервер
    (`letters/outgoing_files.py`). Экран проверяет по ним файл до загрузки — файл
    больше тела запроса отбил бы nginx страницей без слов — и задаёт `accept`
    у кнопки, не держа второй копии чисел."""

    #: Байт на файл.
    max_file_bytes: int
    #: Байт на все файлы одного письма вместе.
    max_letter_bytes: int
    max_files: int
    #: Расширения без точки, в нижнем регистре.
    extensions: list[str]

    @classmethod
    def current(cls) -> FileRulesCard:
        return cls(
            max_file_bytes=outgoing_files.MAX_FILE_BYTES,
            max_letter_bytes=outgoing_files.MAX_LETTER_BYTES,
            max_files=outgoing_files.MAX_FILES,
            extensions=list(outgoing_files.EXTENSIONS),
        )


class OutgoingFileCard(BaseModel):
    """Файл, приложенный к будущему ответу: номер идёт в `file_ids` ответа."""

    id: int
    name: str
    #: Байт.
    size: int
    #: Тип из нашего белого списка — не тот, что назвал браузер.
    content_type: str

    @classmethod
    def of(cls, row: OutgoingAttachmentModel) -> OutgoingFileCard:
        return cls(id=row.id, name=row.name, size=row.size, content_type=row.content_type)


class LetterCard(BaseModel):
    """Наше письмо в переписке."""

    id: int
    step: int
    status: MessageStatus
    subject: str | None
    body: str | None
    sent_at: datetime | None
    #: Доля изменённых слов относительно шаблона, 0–1 — как у письма
    #: в очереди (`QueuedLetterCard.uniqueness`). Поле базы называется
    #: `uniqueness_pct`, но хранит долю: карточка отдавала его под этим
    #: именем, экран поверил имени и печатал «отличие 0%» у письма
    #: с отличием 19% (25.09.2026). Имя в ответе — по смыслу, не по колонке.
    uniqueness: float | None
    #: Номер входящего ответа, на который это письмо отвечает. Пусто —
    #: первое письмо или добивка (`letters/answers.py`).
    answers_reply_id: int | None = None
    #: Файлы письма — сведениями, без тел. Бывают только у ответа.
    attachments: list[LetterFileCard] = Field(default_factory=list)

    @classmethod
    def of(cls, message: MessageModel, files: Sequence[OutgoingAttachmentModel] = ()) -> LetterCard:
        return cls(
            id=message.id,
            step=message.step,
            status=message.status,
            subject=message.subject,
            body=message.body,
            sent_at=message.sent_at,
            uniqueness=message.uniqueness_pct,
            answers_reply_id=message.answers_reply_id,
            attachments=list(map(LetterFileCard.of, files)),
        )


class AnswerBody(BaseModel):
    """Наш ответ на входящий ответ собеседника."""

    reply_id: int
    body: str = Field(min_length=1, max_length=20_000)
    #: Файлы к ответу — номера из загрузки (`POST /api/threads/{id}/files`).
    #: Пусто — ответ без файлов, как прежде.
    file_ids: list[int] = Field(default_factory=list)


class AttachmentCard(BaseModel):
    """Вложение ответа: сведения о файле. Сам файл — отдельным запросом
    (`GET /api/replies/{reply_id}/attachments/{id}`), только на скачивание;
    текст из него — тоже отдельным (`…/attachments/{id}/text`)."""

    id: int
    name: str
    #: Байт. Пусто — размер неизвестен: файл назван, но не пришёл.
    size: int | None
    content_type: str | None
    #: Файл сохранён и скачивается.
    accepted: bool
    #: Почему не сохранён — словами, для человека.
    reason: str | None
    #: Из файла прочитан текст. Сам текст в карточку не едет: карточка диалога
    #: читает сведения о десятке файлов, а текст бывает в двадцать тысяч знаков.
    has_text: bool = False
    #: Почему текста нет или чем он неполон — словами. Текста нет и слов нет —
    #: файл ещё не читали: его прочитает первый запрос текста.
    text_note: str | None = None

    @classmethod
    def of(cls, row: ReplyAttachmentModel) -> AttachmentCard:
        return cls(
            id=row.id,
            name=row.name,
            size=row.size,
            content_type=row.content_type,
            accepted=row.accepted,
            reason=row.reason,
            has_text=row.has_text,
            text_note=row.text_note,
        )


class OfferCard(BaseModel):
    """Одна цена из ответа (`replies/offers.py`). Продукт и ниша — словами
    донора: это цитата письма, а не наш справочник."""

    product: str
    niche: str | None = None
    price: Decimal
    currency: str | None = None
    #: `month` или `year` — цена за срок; пусто — разовая.
    period: str | None = None

    @classmethod
    def listed(cls, stored: Sequence[Mapping[str, Any]] | None) -> list[OfferCard] | None:
        """Список, как он лежит в базе. Пусто остаётся пустым: «разобран
        до списка» и «цен не названо» (`[]`) — разные ответы."""
        return None if stored is None else [cls.model_validate(item) for item in stored]


class IncomingCard(BaseModel):
    """Входящее письмо и то, что из него распознали.

    Исходный текст отдаётся всегда и рядом с разобранным: человек должен
    видеть, откуда взялось число, иначе проверить его нечем.
    """

    id: int
    kind: ReplyKind
    raw_body: str
    #: То, что написал человек, — без цитаты нашего письма и подписи
    #: (`replies/quoting.written_by_hand`, то же правило, что у разбора цены).
    #: Экран показывает его, а письмо целиком — по раскрытию: цитата нашего
    #: же письма и хвост подписи с трекинговыми ссылками растягивали переписку
    #: в простыню (первый настоящий ответ донора, 08.10.2026). Отрезать нечего —
    #: здесь весь текст.
    fresh_body: str
    received_at: datetime
    #: Адрес, с которого ответили. Может отличаться от того, кому писали:
    #: на общий ящик смотрит секретарь и пересылает письмо редактору.
    from_email: str | None
    subject: str | None
    #: Что пришло файлами. Прайс приходит вложением чаще, чем текстом,
    #: и ответ с вложением не должен выглядеть пустым.
    attachments: list[AttachmentCard] = Field(default_factory=list)
    price_white: Decimal | None
    price_grey: Decimal | None
    currency: str | None
    #: Все цены, названные в ответе. Пусто — ответ разобран до 06.10.2026,
    #: когда списка ещё не было, или не разбирался; `[]` — цен не названо.
    offers: list[OfferCard] | None = None
    payment_methods: list[str] | None
    confidence: float | None
    #: Продаёт ли донор размещение по разбору: `sells`, `declines`, `unclear`.
    placement: str | None
    #: Ждёт ли разбор человека. Считается, а не хранится: второе поле
    #: разошлось бы с уверенностью при первой правке порога.
    needs_review: bool
    #: Почему ответ ждёт человека не по уверенности разбора — словами:
    #: автоответ с суммой в валюте (модель автоответы не разбирает, цену
    #: вписывают руками) или ответ лида продаж (вид ответа и путь называет
    #: модуль продаж — `replies.outcome.sales_review`).
    #: Пусто — обычный ответ.
    review_reason: str | None = None
    #: Ответ рекламодателя: не цена, а лид. Его не разбирают, а берут
    #: в работу — `reviewed_by`/`reviewed_at` тогда говорят, кто и когда.
    lead: bool
    reviewed_by: str | None
    reviewed_at: datetime | None

    @classmethod
    def of(
        cls,
        reply: ReplyModel,
        stage: Stage = Stage.DONORS,
        files: Sequence[ReplyAttachmentModel] = (),
    ) -> IncomingCard:
        lead = stage is Stage.ADVERTISERS and reply.kind is ReplyKind.HUMAN
        # У лида нечего разбирать: форма цены для него — отказ (`review_of`).
        review = review_of(reply, stage)
        return cls(
            id=reply.id,
            kind=reply.kind,
            raw_body=reply.raw_body,
            fresh_body=written_by_hand(reply.raw_body),
            received_at=reply.created_at,
            from_email=reply.from_email,
            subject=reply.subject,
            attachments=list(map(AttachmentCard.of, files)),
            price_white=reply.price_white,
            price_grey=reply.price_grey,
            currency=reply.currency,
            offers=OfferCard.listed(reply.offers),
            payment_methods=reply.payment_methods,
            confidence=reply.confidence,
            placement=reply.placement,
            needs_review=review.waiting,
            review_reason=review.reason,
            lead=lead,
            reviewed_by=reply.reviewed_by,
            reviewed_at=reply.reviewed_at,
        )


class ThreadMailCard(BaseModel):
    """Чем переписка пишет дальше: её ящик, пишет ли он и срок следующей
    добивки (`letters/mailbox.py`). Слова ожидания — те же, что у отказа
    отправки: экран говорит их до клика, а не после."""

    #: Адрес ящика переписки; пусто — ящик удалили.
    mailbox: str | None
    #: Почему письма переписки ждут; пусто — ящик пишет.
    waiting: str | None
    #: Шаг и срок следующей добивки; пусто — добивки не будет.
    next_step: int | None
    next_at: datetime | None

    @classmethod
    def of(cls, mail: ThreadMail | None) -> ThreadMailCard | None:
        if mail is None:
            return None
        return cls(
            mailbox=mail.mailbox,
            waiting=mail.waiting,
            next_step=mail.next_step,
            next_at=mail.next_at,
        )


class ThreadView(BaseModel):
    """Переписка целиком."""

    card: ThreadCard
    letters: list[LetterCard]
    incoming: list[IncomingCard]
    #: Коридор отличия — рядом с отличием письма. Отдаёт сервер: на карточке
    #: стояло вшитое «цель 15–25%», которое разошлось бы с настройкой
    #: при первой её правке.
    corridor: Corridor = Field(default_factory=Corridor)
    #: Ящик переписки и следующая добивка. Пусто — первое письмо ещё
    #: не уходило: ящик выберется в момент отправки.
    mail: ThreadMailCard | None = None
    #: Черновики агента к ответам переписки (`agent/drafting.py`).
    drafts: list[DraftCard] = Field(default_factory=list)
    #: Пишет ли агент на этапе переписки: без него кнопки «Написать черновик» нет.
    agent_writes: bool = False
    #: За что отклоняют черновик на этапе переписки (`AgentStage.reject_reasons`).
    agent_reasons: list[str] = Field(default_factory=list)
    #: Файлы, приложенные к переписке и ещё ни с одним письмом не ушедшие: по ним
    #: экран восстанавливает скрепку у формы ответа после перезагрузки страницы.
    pending_files: list[LetterFileCard] = Field(default_factory=list)
    #: Правила файла к ответу — для проверки до загрузки и `accept` у кнопки.
    file_rules: FileRulesCard = Field(default_factory=FileRulesCard.current)

    @classmethod
    def of(
        cls,
        detail: ThreadDetail,
        files: Mapping[int, Sequence[ReplyAttachmentModel]],
        mail: ThreadMail | None = None,
        *,
        drafts: Sequence[ShownDraft] = (),
        agent_writes: bool = False,
        agent_reasons: Sequence[str] = (),
        outgoing: ThreadFiles = NO_FILES,
    ) -> ThreadView:
        return cls(
            card=ThreadCard.of(detail.row),
            letters=[LetterCard.of(m, outgoing.letters.get(m.id, ())) for m in detail.messages],
            incoming=[
                IncomingCard.of(r, detail.row.stage, files.get(r.id, ())) for r in detail.replies
            ],
            mail=ThreadMailCard.of(mail),
            drafts=[DraftCard.of(shown) for shown in drafts],
            agent_writes=agent_writes,
            agent_reasons=list(agent_reasons),
            pending_files=list(map(LetterFileCard.of, outgoing.pending)),
        )
