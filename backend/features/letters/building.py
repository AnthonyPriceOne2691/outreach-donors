"""Сборка очереди: от списка доноров до писем со статусом «в очереди».

Одно письмо на донора, на лучший из известных адресов — правило
из `docs/OUTREACH_THREADS.md`. Отбор и его воронка живут в `repository.py`,
текст — в `compose.py` и `rewrite.py`, здесь только порядок действий
и то, что считается по дороге.

**Сборка ничего не отправляет.** Она готовит текст и ставит письма
в очередь; отправляет человек с правом на отправку, глядя на предпросмотр.
Это и есть смысл экрана писем: спорное решение видит человек.

**Письма готовятся по очереди, а не разом.** Каждое стоит вызова модели,
и параллельный набор из двухсот вызовов упёрся бы в лимит скорости
провайдера — а это отказы, которые выглядят как «зоны остались
шаблонными». Сборка идёт в фоне, торопиться ей некуда.

**Низкий процент отличия не отменяет письмо.** Оно встаёт в очередь
с честным числом и вердиктом рядом, и человек решает сам: отправить,
поправить руками или не писать. Тихо выбросить письмо значило бы
показать пустую очередь там, где отказала модель.

**Незаполненный юридический блок сборку не останавливает, но называется
вслух.** Живой прогон собрал три письма на 1361 токен, не сказав ни слова
о том, что отправить нельзя ни одно: физического адреса и отписки нет.
Отказать здесь было бы неверно — очередь готовят и для того, чтобы
посмотреть текст, — а промолчать нельзя: человек нажмёт «отправить»
и узнает об этом на третьем письме. Поэтому отчёт несёт список того,
чем отправка заблокирована, а сама отправка отказывает жёстко.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core import usage
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.outreach import MessageModel
from backend.features.letters import compose, guards
from backend.features.letters.repository import Candidate, LetterRepository
from backend.features.letters.rewrite import Personalization, RewriteClient
from backend.features.letters.template import Template, default
from backend.features.letters.uniqueness import corridor_verdict, difference

logger = logging.getLogger(__name__)

#: Первое письмо цепочки. Добивки — шаги 1 и 2, они придут с Ф5.
FIRST_STEP = 0


def idempotency_key(*, stage: Stage, host: str, step: int) -> str:
    """Домен плюс этап плюс шаг (Э1-39).

    Повтор задачи не должен отправить второе письмо тому же донору:
    это жалоба на спам, а не лишняя строка в базе.
    """
    return f"{stage.value}:{host}:{step}"


@dataclass(frozen=True, slots=True)
class BuildRequest:
    """Что собираем."""

    campaign_name: str
    stage: Stage = Stage.DONORS
    #: Страна прогона — от неё язык письма, а не от языка донора (ТЗ).
    country: str = "us"
    #: Ключи прогона: ниша, по которой донор нашёлся.
    niche: tuple[str, ...] = ()
    limit: int = 50
    run_id: int | None = None
    #: Через сколько дней уходят добивки. Пусто — умолчание настроек.
    #: Задаётся при создании рассылки: сроки подбирают по отклику,
    #: и менять их у идущих цепочек задним числом нельзя.
    followup_days: tuple[int, ...] = ()


@dataclass
class BuildReport:
    """Что получилось. Числа здесь — отчёт человеку, а не служебные счётчики."""

    campaign_id: int
    prepared: int = 0
    tokens_spent: int = 0
    #: Писем, не попавших в коридор отличия. Не отказ, но повод посмотреть.
    off_corridor: int = 0
    funnel: dict[str, int] = field(default_factory=dict)
    #: Почему зоны остались шаблонными: причина → сколько раз.
    notes: dict[str, int] = field(default_factory=dict)
    #: Незаполненные настройки, из-за которых отправить нельзя ни одно
    #: из подготовленных писем. Пусто — очередь готова к отправке.
    blocked_by: list[str] = field(default_factory=list)


class QueueBuilder:
    """Собирает очередь писем из базы доноров."""

    def __init__(
        self,
        session: AsyncSession,
        rewriter: RewriteClient,
        *,
        template: Template | None = None,
    ) -> None:
        self._session = session
        self._rewriter = rewriter
        self._template = template or default()
        self._repo = LetterRepository(session)

    async def build(self, request: BuildRequest) -> BuildReport:
        """Подготовить письма и поставить их в очередь."""
        # Метрики Ahrefs в письме запрещены правилами Ahrefs. Неизменяемые
        # зоны одинаковы во всех письмах, поэтому шаблон проверяется один
        # раз — до того, как на него потратят двести вызовов модели.
        guards.assert_no_metrics(self._template.body)

        campaign = await self._repo.campaign(
            name=request.campaign_name,
            stage=request.stage,
            run_id=request.run_id,
            followup_days=request.followup_days,
        )
        report = BuildReport(campaign_id=campaign.id)
        report.funnel = (await self._repo.funnel(request.stage)).as_report()
        report.blocked_by = compose.missing_settings()
        if report.blocked_by:
            logger.warning(
                "письма: очередь собирается, но отправить будет нельзя — не заполнено %s",
                ", ".join(report.blocked_by),
            )

        candidates = await self._repo.candidates(request.stage, limit=request.limit)
        for candidate in candidates:
            await self._prepare(candidate, campaign_id=campaign.id, request=request, report=report)

        logger.info(
            "письма: подготовлено %s из %s, токенов %s, вне коридора %s",
            report.prepared,
            len(candidates),
            report.tokens_spent,
            report.off_corridor,
        )
        return report

    async def _prepare(
        self,
        candidate: Candidate,
        *,
        campaign_id: int,
        request: BuildRequest,
        report: BuildReport,
    ) -> None:
        """Одно письмо: текст, проверки, запись в очередь."""
        rendered = compose.render(
            self._template,
            compose.values_for(host=candidate.host, domain_id=candidate.domain_id),
        )
        rewritten = await self._rewriter.rewrite(
            rendered,
            Personalization(host=candidate.host, country=request.country, niche=request.niche),
        )
        for note in rewritten.notes:
            report.notes[note] = report.notes.get(note, 0) + 1

        letter = compose.assemble(rendered, rewritten.zones)
        guards.assert_no_metrics(letter.body)

        uniqueness = difference(letter.plain_body, letter.body)
        if corridor_verdict(uniqueness) is not None:
            report.off_corridor += 1

        thread = await self._repo.thread(
            domain_id=candidate.domain_id,
            campaign_id=campaign_id,
            contact_id=candidate.contact_id,
        )
        self._session.add(
            MessageModel(
                campaign_id=campaign_id,
                thread_id=thread.id,
                domain_id=candidate.domain_id,
                contact_id=candidate.contact_id,
                step=FIRST_STEP,
                status=MessageStatus.QUEUED,
                subject=letter.subject,
                body=letter.body,
                uniqueness_pct=uniqueness,
                idempotency_key=idempotency_key(
                    stage=request.stage, host=candidate.host, step=FIRST_STEP
                ),
            )
        )
        if rewritten.tokens_spent:
            usage.record(
                self._session,
                operation="letter_rewrite",
                units=rewritten.tokens_spent,
                run_id=request.run_id,
            )

        report.prepared += 1
        report.tokens_spent += rewritten.tokens_spent
