"""Что отдают маршруты писем."""

from __future__ import annotations

from pydantic import BaseModel, Field

from backend.config import outreach as cfg
from backend.features.core.domain import MessageStatus, Stage
from backend.features.letters import compose, template
from backend.features.letters.chain import MAX_STEPS, cadence
from backend.features.letters.draft import Draft, default_draft
from backend.features.letters.repository import QueuedLetter
from backend.features.letters.uniqueness import corridor_verdict


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
                template.followup(step, stage),
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


class LettersView(BaseModel):
    """Экран писем целиком.

    Кроме самих писем отдаётся то, без чего очередь читается неверно:
    чем заблокирована отправка, настоящий ли транспорт и где кончились
    доноры. Пустая очередь при «всем написали» и при «ни у кого нет
    адреса» выглядит одинаково.
    """

    #: Чья это очередь: доноров (вопрос о цене) или рекламодателей (оффер).
    stage: Stage = Stage.DONORS
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


class BuildQueued(BaseModel):
    """Сборка ушла в очередь задач: она идёт минутами."""

    job_id: str


class EditRequestBody(BaseModel):
    subject: str
    body: str


class SendResult(BaseModel):
    """Чем кончилась отправка одного письма."""

    id: int
    sender_email: str
    #: Ушло ли письмо на самом деле. У нулевого транспорта — нет.
    real: bool
