"""Что отдают маршруты писем."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from backend.config import outreach as cfg
from backend.features.core.domain import MessageStatus, Stage
from backend.features.crawl.niche import LINKS
from backend.features.letters import compose, template, unknown_outcome
from backend.features.letters.chain import MAX_STEPS, cadence
from backend.features.letters.draft import Draft, default_draft
from backend.features.letters.repository import QueuedLetter
from backend.features.letters.transport import TransportError
from backend.features.letters.transport_factory import build_transport
from backend.features.letters.uniqueness import corridor_verdict

logger = logging.getLogger(__name__)


class QueuedLetterCard(BaseModel):
    """Одно письмо в очереди — целиком, вместе с текстом.

    Имя длиннее соседних нарочно: `LetterCard` уже занят письмом
    в переписке (`api/threads`), и две одноимённые схемы в одном описании
    API разъезжаются молча — клиент собирает типы по именам.

    Текст приходит сразу, а не отдельным запросом на каждое выбранное
    письмо: экран для того и сделан, чтобы человек читал письма подряд,
    а очередь редко длиннее сотни. Второй запрос на каждый щелчок сделал
    бы чтение рваным ради экономии, которой не видно.
    """

    id: int
    host: str
    email: str | None
    campaign: str
    status: MessageStatus
    subject: str | None
    body: str | None
    #: Доля изменённых слов относительно шаблона, 0–1.
    uniqueness: float | None
    #: Что не так с этим числом. Пусто — в коридоре.
    verdict: str | None
    #: Добивки этого донора — текстом, каким они уйдут.
    followups: list[FollowupCard]

    @classmethod
    def of(cls, row: QueuedLetter) -> QueuedLetterCard:
        share = row.message.uniqueness_pct
        return cls(
            id=row.message.id,
            host=row.host,
            email=row.email,
            campaign=row.campaign,
            status=row.message.status,
            subject=row.message.subject,
            body=row.message.body,
            uniqueness=share,
            verdict=corridor_verdict(share) if share is not None else None,
            followups=followups_for(
                row.host,
                row.followup_days,
                domain_id=row.message.domain_id,
                stage=row.stage,
                audience=row.audience,
                subject=row.message.subject,
            ),
        )


class FollowupCard(BaseModel):
    """Добивка в предпросмотре: что уйдёт этому донору и когда.

    Отдаётся вместе с письмом по той же причине, что и его текст:
    согласуя первое письмо, человек согласует всю цепочку, и прочесть
    её он должен целиком, а не узнать о втором письме от донора.
    """

    step: int
    subject: str
    body: str
    #: Через сколько дней после предыдущего письма уйдёт.
    in_days: int


def followups_for(
    host: str,
    days: list[int] | None,
    *,
    domain_id: int | None = None,
    stage: Stage = Stage.DONORS,
    audience: str = LINKS,
    subject: str | None = None,
) -> list[FollowupCard]:
    """Добивки адресата: шаблон шага этапа с его подстановками.

    Собирается на месте, а не хранится: текст добивки один на всю
    рассылку и живёт файлом в коде. Хранить его копией у каждого письма
    значит завести вторую правду, которая разойдётся с первой правкой
    шаблона.

    `subject` — тема первого письма: с ней добивки и уйдут
    (`followups.Chain.compose_letter`), и показывать другую значило бы
    согласовать не то, что отправится.
    """
    schedule = cadence(days)
    cards: list[FollowupCard] = []
    for step in range(1, MAX_STEPS):
        letter = compose.assemble(
            compose.render(
                template.followup(step, stage, audience),
                compose.values_for(host=host, domain_id=domain_id),
            ),
            {},
        )
        cards.append(
            FollowupCard(
                step=step,
                subject=(subject or "").strip() or letter.subject,
                body=letter.body,
                in_days=schedule[step - 1] if step <= len(schedule) else 0,
            )
        )
    return cards


class Corridor(BaseModel):
    """Границы коридора отличия. Отдаёт сервер, а не хранит фронт: второй
    экземпляр чисел разъехался бы с настройкой при первой её правке,
    и экран показывал бы «в коридоре» там, где его уже нет."""

    min: float = cfg.UNIQUENESS_TARGET_MIN
    max: float = cfg.UNIQUENESS_TARGET_MAX


class Transport(BaseModel):
    """Чем отправляем и отправляем ли на самом деле."""

    name: str
    #: Ложь здесь дороже всего: письмо, помеченное отправленным и никуда
    #: не ушедшее, выглядит как работа.
    real: bool
    #: Почему транспорт не собрался, если не собрался.
    problem: str | None = None

    @classmethod
    def current(cls, stage: str | None = None) -> Transport:
        """Каким транспортом располагаем — для экрана писем (учётка этапа) и главной.

        Отказ собрать транспорт — это не поломка экрана, а его содержание:
        человек должен видеть, что отправлять нечем, и почему.
        """
        try:
            transport = build_transport(stage=stage)
        except TransportError as exc:
            # В лог тоже, а не только на экран: человек прочтёт и забудет,
            # а разбираться, почему рассылка стоит, будут по логам сервера.
            logger.warning("письма: транспорт не собран — %s", exc)
            return cls(name="—", real=False, problem=str(exc))
        return cls(name=transport.name, real=transport.real)


class LetterZoneView(BaseModel):
    name: str
    #: `rewrite` — переписывает модель под донора, `fixed` — уходит как есть.
    kind: str
    title: str
    text: str


class LetterDraftView(BaseModel):
    """Текст первого письма по зонам — для правки перед созданием рассылки."""

    subject: str
    zones: list[LetterZoneView]

    @classmethod
    def of(cls, source: Draft) -> LetterDraftView:
        return cls(
            subject=source.subject,
            zones=[
                LetterZoneView(name=z.name, kind=z.kind.value, title=z.title, text=z.text)
                for z in source.zones
            ],
        )


class LetterDraftBody(BaseModel):
    """Поправленный текст: тема и содержимое зон по именам."""

    subject: str = Field(min_length=1, max_length=300)
    zones: dict[str, str]


#: Аудитория рассылки Этапа 2 (`campaigns.audience`).
Audience = Literal["links", "niche"]


def _stage_two_only(stage: Stage, audience: str) -> None:
    """Бизнесы ниши — только Этап 2: им предлагают размещение, а донору — вопрос о цене."""
    if audience != LINKS and stage is not Stage.ADVERTISERS:
        raise ValueError(
            "Бизнесам ниши пишут только на Этапе 2: им предлагают размещение, а не спрашивают цену"
        )


class LettersView(BaseModel):
    """Экран писем целиком.

    Кроме самих писем отдаётся то, без чего очередь читается неверно:
    чем заблокирована отправка, настоящий ли транспорт и где кончились
    доноры. Пустая очередь при «всем написали» и при «ни у кого нет
    адреса» выглядит одинаково.
    """

    #: Чья это очередь: доноров (вопрос о цене) или рекламодателей (оффер).
    stage: Stage = Stage.DONORS
    #: Аудитория Этапа 2, для которой показаны текст по умолчанию и воронка.
    audience: Audience = "links"
    letters: list[QueuedLetterCard]
    #: Сроки добивок по умолчанию — для формы создания рассылки.
    #: Отдаёт сервер, а не хранит фронт: второй экземпляр чисел
    #: разошёлся бы с настройкой при первой её правке.
    followup_default: list[int] = Field(default_factory=lambda: list(cfg.FOLLOWUP_DAYS))
    #: Текст первого письма по умолчанию — для правки перед созданием
    #: рассылки. Отдаёт сервер по той же причине, что и сроки.
    letter_default: LetterDraftView = Field(
        default_factory=lambda: LetterDraftView.of(default_draft())
    )
    #: Настройки, из-за которых отправить нельзя ни одно письмо.
    blocked_by: list[str]
    transport: Transport
    corridor: Corridor
    funnel: dict[str, int]
    #: Сколько писем этапа и аудитории ждёт в очереди — всех. Список `letters` — только
    #: начало очереди (`LetterRepository.queued` с потолком), а кнопка пачки называет
    #: очередь целиком.
    queued_total: int
    #: Сколько писем берёт одна пачка «Отправить очередь» (`batch.BATCH_MAX`). Отдаёт
    #: сервер: окно подтверждения называет его, когда очередь длиннее пачки, а копия
    #: числа во фронте разошлась бы с пачкой при первой правке. Ставит маршрут — из того
    #: же места, что и число в ответе пачки (`SendQueueQueued.queued`).
    batch_max: int


class BuildRequestBody(BaseModel):
    campaign: str
    #: Кому: донорам — вопрос о цене, рекламодателям — оффер под найденную
    #: ссылку. У рекламодателей прогонов нет, `run_ids` для них — отказ.
    stage: Stage = Stage.DONORS
    country: str = "us"
    #: Ключи прогона: ниша, по которой донор нашёлся.
    niche: list[str] = []
    limit: int = 50
    #: Через сколько дней после предыдущего письма уходят добивки.
    #: Пусто — умолчание настроек. Задаётся здесь, а не в настройках
    #: сервиса, потому что сроки подбирают по отклику, и у рассылки,
    #: которая уже идёт, они меняться не должны.
    followup_days: list[int] = Field(default_factory=list, max_length=MAX_STEPS - 1)
    #: Поправленный текст первого письма. Пусто — у новой рассылки шаблон
    #: из кода, у найденной — её собственный текст.
    letter: LetterDraftBody | None = None
    #: Прогоны, из принятых доноров которых собирается рассылка. Страна
    #: письма берётся из них. Пусто — все принятые доноры базы.
    run_ids: list[int] = Field(default_factory=list, max_length=50)
    #: Кому рассылка Этапа 2: `links` — рекламодателям по найденной ссылке,
    #: `niche` — бизнесам ниши из выдачи (свой оффер и свои добивки).
    audience: Audience = "links"

    @model_validator(mode="after")
    def _niche_is_stage_two(self) -> BuildRequestBody:
        _stage_two_only(self.stage, self.audience)
        return self


class BuildQueued(BaseModel):
    """Сборка ушла в очередь задач: она идёт минутами."""

    job_id: str


class SendQueueBody(BaseModel):
    """Какую очередь отправить пачкой: этап и, на Этапе 2, аудиторию."""

    stage: Stage = Stage.DONORS
    #: Чьи письма: по найденной ссылке или бизнесов ниши. Пачка одной аудитории
    #: письма другой не берёт; без аудитории — по ссылке, как до бизнесов ниши.
    audience: Audience = "links"

    @model_validator(mode="after")
    def _niche_is_stage_two(self) -> SendQueueBody:
        _stage_two_only(self.stage, self.audience)
        return self


class SendQueueQueued(BaseModel):
    """Пачка ушла в очередь задач: письма уходят по одному, минутами."""

    job_id: str
    #: Сколько писем возьмёт пачка: очередь этапа, но не больше потолка пачки, — то же
    #: число, что «Отправить N» в окне подтверждения. Уйдёт не больше, чем позволит
    #: дневной лимит ящиков, — итог скажет строка задачи.
    queued: int


class EditRequestBody(BaseModel):
    subject: str
    body: str


class SendResult(BaseModel):
    """Чем кончилась отправка одного письма."""

    id: int
    sender_email: str
    #: Ушло ли письмо на самом деле. У нулевого транспорта — нет.
    real: bool


class UnknownLetterCard(BaseModel):
    """Письмо с неизвестным исходом — с тем, по чему его ищут в журнале
    платформы: кому, с какого ящика и когда его отдали почте."""

    id: int
    host: str
    email: str | None
    sender_email: str | None
    campaign: str
    #: Какое это письмо — словами: первое, добивка, ответ в переписке.
    what: str
    #: Когда началась передача почте. Если письмо ушло, от этого времени
    #: считаются срок добивки и дневной лимит ящика.
    since: datetime
    #: Переписка письма: добивку и ответ после решения ведёт она, а не очередь.
    thread_id: int | None

    @classmethod
    def of(cls, letter: unknown_outcome.StuckLetter) -> UnknownLetterCard:
        return cls(
            id=letter.message.id,
            host=letter.host,
            email=letter.email,
            sender_email=letter.sender_email,
            campaign=letter.campaign,
            what=letter.what,
            since=letter.since,
            thread_id=letter.message.thread_id,
        )


class UnknownLettersView(BaseModel):
    """Блок «Исход неизвестен» на экране писем."""

    stage: Stage
    letters: list[UnknownLetterCard]
    #: Через сколько минут после начала передачи письмо попадает сюда.
    after_minutes: int = unknown_outcome.STUCK_MINUTES


class ResolveBody(BaseModel):
    """Что человек нашёл в журнале платформы: письмо ушло или нет."""

    outcome: unknown_outcome.Outcome


class ResolvedLetter(BaseModel):
    """Чем кончилось решение: состояние письма и то же словами."""

    id: int
    status: MessageStatus
    said: str
