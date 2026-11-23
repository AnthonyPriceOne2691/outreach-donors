"""Переписать шаблонные письма очереди: зоны, оставшиеся шаблоном из-за отказа модели.

23.11.2026 у провайдера модели кончились деньги посреди сборки очереди рекламодателям, и по
правилу «отказ модели — не отказ письма» (`rewrite.py`) 68 писем встали в очередь с зонами
шаблона. Собрать их заново сборка не может — этим адресатам письмо уже написано, — а править
по одному руками долго. Это действие берёт такие письма (отбор — `repository.left_as_template`)
и переписывает их **тем же путём, что сборка**: тот же клиент модели и промпт (`RewriteClient`),
то же «что модель знает об адресате» (`finishing.personalization`), тот же подбор зон под
коридор, запреты и отличие (`finishing.finish`), те же пачки и потолок расхода перед пачкой.

**Письмо меняется, только если его текст — ровно шаблон рассылки сейчас.** Шаблон собирается
заново — текстом рассылки и подстановками адресата сегодня — и сверяется с письмом очереди.
Совпало — переписанные зоны встают на место шаблонных, остальное в письме прежнее: оффер,
условия и подпись модель не видит, как и при сборке. Не совпало (поменялись ссылка
рекламодателя, оффер бизнеса ниши, подпись или шаблон рассылки) — письмо остаётся как есть,
с причиной в итоге: переписать его значило бы заодно молча поменять условия.

**Модель снова отказала — письмо остаётся как было.** В базу не пишется ничего, итог называет
причину словами модели, как ноты сборки. «Нет кредитов» останавливает задачу после пачки:
звать модель на остальные письма вхолостую незачем (`shared/llm.Refusal.no_credits`).

**Запись — условием, как правка руками** (`review.py`): письмо меняется, только если оно ещё
в очереди и его текст тот, что прочитан. Пачка или человек, взявшие письмо между чтением
и записью, выигрывают, а письмо считается непереписанным — с причиной.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import outreach as outreach_cfg
from backend.features.core import usage
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import CampaignModel, MessageModel
from backend.features.core.models.run import RunCandidateModel, RunModel
from backend.features.core.stages import SALES_ELSEWHERE, SalesNotConnectedError
from backend.features.crawl.niche import LINKS, NICHE
from backend.features.letters import compose
from backend.features.letters.finishing import Finished, cap_reached, finish, personalization
from backend.features.letters.niche_recipients import NicheRecipients
from backend.features.letters.repository import LetterRepository, QueuedLetter
from backend.features.letters.rewrite import Personalization, RewriteClient, RewriteResult
from backend.features.letters.template import Template, of_campaign
from backend.features.letters.uniqueness import corridor_verdict
from backend.shared.llm import NO_CREDITS_WORDS

logger = logging.getLogger(__name__)

#: Писем за одну задачу — потолок сборки за раз (`api/letters/schemas.LIMIT_MAX`): остальные —
#: следующим нажатием, число на кнопке скажет, сколько их осталось.
TAKE_MAX = 500

#: Расход модели — той же строкой, что у сборки: это тот же вызов на то же письмо.
OPERATION = "letter_rewrite"

#: Почему письмо осталось как было — словами итога (`RewriteReport.left`).
DIVERGED = (
    "текст письма уже не шаблон рассылки (после сборки поменялись ссылка, оффер, подпись или "
    "шаблон) — поправьте руками или уберите из очереди"
)
GONE = "письмо ушло из очереди или его поправили, пока модель его переписывала"
NO_LINK = "рекламодателя сняли после сборки письма — письмо стоит убрать из очереди"
NO_OFFER = "бизнесу ниши письмо больше не собрать — письмо стоит убрать из очереди"
SAME_TEXT = "модель вернула текст шаблона"

#: Отказы до задачи — у кнопки и в консоли одними словами.
NOTHING = "Шаблонных писем в этой очереди нет — переписывать нечего"
#: Очередь уходит сама (`autosend.py`): шаблонные письма уходили бы раньше, чем до них дойдёт
#: модель, — а ради них очередь и останавливают.
SENDING_ITSELF = (
    "Очередь сейчас уходит сама: шаблонные письма уходили бы раньше, чем их перепишет модель. "
    "Остановите её кнопкой «Остановить» под пачкой и нажмите снова"
)


def rewritable_stage(stage: Stage) -> Stage:
    """Этап, чьи письма переписываются здесь; продажам — отказ словами: их письма собирает
    и переписывает модуль продаж со своим коридором (`sales/corridor.py`)."""
    if stage is Stage.SALES:
        raise SalesNotConnectedError(
            "Письма продаж этим действием не переписываются", words=SALES_ELSEWHERE
        )
    return stage


async def templated_count(session: AsyncSession, stage: Stage, audience: str = LINKS) -> int:
    """Сколько писем очереди оставлено шаблоном — число на кнопке «Переписать шаблонные». У
    продаж — ноль: их письма переписывает не это действие (`rewritable_stage`)."""
    if stage is Stage.SALES:
        return 0
    return await LetterRepository(session).templated_count(stage=stage, audience=audience)


@dataclass(frozen=True, slots=True)
class RewriteRequest:
    """Чьи шаблонные письма переписать."""

    stage: Stage
    audience: str = LINKS
    #: Страна письма, когда прогонов нет (рекламодатели), — как у сборки (`BuildRequest`).
    country: str = "us"
    limit: int = TAKE_MAX


@dataclass
class RewriteReport:
    """Что вышло. Числа — отчёт человеку, как у сборки."""

    #: Сколько шаблонных писем задача взяла.
    found: int = 0
    rewritten: int = 0
    tokens_spent: int = 0
    #: Переписанных, но вне коридора отличия: не отказ, повод посмотреть (как у сборки).
    off_corridor: int = 0
    #: Почему письмо осталось как было: причина словами → сколько писем.
    left: dict[str, int] = field(default_factory=dict)
    #: Ноты модели, как у сборки: причина → сколько раз.
    notes: dict[str, int] = field(default_factory=dict)
    #: Почему задача остановилась раньше (потолок расхода, нет кредитов); до остальных писем
    #: она не дошла — они остались как были.
    stopped: str | None = None
    #: Сколько шаблонных писем осталось в очереди после задачи — число на кнопке.
    remaining: int = 0

    def as_dict(self) -> dict[str, Any]:
        """Итог задачи для очереди и экрана."""
        return {
            "found": self.found,
            "rewritten": self.rewritten,
            "tokens": self.tokens_spent,
            "off_corridor": self.off_corridor,
            "left": self.left,
            "notes": self.notes,
            "stopped": self.stopped,
            "remaining": self.remaining,
        }


def _count(into: dict[str, int], reason: str) -> None:
    into[reason] = into.get(reason, 0) + 1


@dataclass(frozen=True, slots=True)
class _Known:
    """Что сборка знала об адресате-доноре: прогон, где его приняли, и цитата сайта."""

    country: str | None
    niche: tuple[str, ...]
    about: str | None


async def _donors_known(
    session: AsyncSession, letters: Sequence[QueuedLetter]
) -> dict[int, _Known]:
    """Страна и ниша — из последнего прогона, где донора приняли, цитата — от судьи.

    У сборки они приходили из прогонов рассылки (`scope.run_scope`), а прогоны рассылка не
    хранит; донор письма принят хотя бы в одном из них, и страна прогонов рассылки одна."""
    rows = await session.execute(
        select(DomainModel.id, DomainModel.host, DomainModel.judge_quote, RunModel)
        .outerjoin(
            RunCandidateModel,
            and_(
                RunCandidateModel.domain_id == DomainModel.id,
                RunCandidateModel.status == "accepted",
            ),
        )
        .outerjoin(RunModel, RunModel.id == RunCandidateModel.run_id)
        .where(DomainModel.id.in_({letter.message.domain_id for letter in letters}))
        .order_by(DomainModel.id, RunModel.id.desc().nulls_last())
        .distinct(DomainModel.id)
    )
    known: dict[int, _Known] = {}
    for domain_id, host, quote, run in rows.all():
        found_by = ((run.candidates or {}).get("found_by") or {}) if run is not None else {}
        known[domain_id] = _Known(
            country=run.country.lower() if run is not None else None,
            niche=tuple(found_by.get(host, ())),
            about=quote,
        )
    return known


class QueueRewriter:
    """Переписывает шаблонные письма очереди этапа и аудитории."""

    def __init__(self, session: AsyncSession, rewriter: RewriteClient) -> None:
        self._session = session
        self._rewriter = rewriter
        self._repo = LetterRepository(session)
        self._templates: dict[int, Template] = {}
        self._donors: dict[int, _Known] = {}

    async def rewrite(self, request: RewriteRequest) -> RewriteReport:
        """Переписать шаблонные письма очереди. Ничего не отправляет."""
        stage = rewritable_stage(request.stage)
        letters = await self._repo.templated(
            stage=stage, audience=request.audience, limit=request.limit
        )
        report = RewriteReport(found=len(letters))
        if stage is Stage.DONORS and letters:
            self._donors = await _donors_known(self._session, letters)
        size = max(1, outreach_cfg.BUILD_CONCURRENCY)
        for start in range(0, len(letters), size):
            report.stopped = await cap_reached(self._session, run_id=None, what="переписывание")
            if report.stopped is None:
                await self._pack(letters[start : start + size], request, report)
            if report.stopped is not None:
                break
        report.remaining = await self._repo.templated_count(stage=stage, audience=request.audience)
        logger.info(
            "письма: переписано шаблонных %s из %s, токенов %s, осталось шаблонных %s",
            report.rewritten,
            report.found,
            report.tokens_spent,
            report.remaining,
            extra={"left": report.left, "stopped": report.stopped},
        )
        return report

    async def _pack(
        self, pack: Sequence[QueuedLetter], request: RewriteRequest, report: RewriteReport
    ) -> None:
        """Пачка: вызовы модели разом, запись — по письму, каждое своей фиксацией.

        Упавший вызов не теряет соседей: их токены уже потрачены. Падение поднимается после
        записи остальных — повтор задачи возьмёт то, что осталось шаблоном."""
        ready: list[tuple[QueuedLetter, compose.Rendered]] = []
        for letter in pack:
            rendered = await self._rendered(letter)
            if isinstance(rendered, str):
                _count(report.left, rendered)
            else:
                ready.append((letter, rendered))
        outcomes = await asyncio.gather(
            *(self._rewriter.rewrite(r, self._about(letter, request)) for letter, r in ready),
            return_exceptions=True,
        )
        fallen = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
        for (letter, rendered), outcome in zip(ready, outcomes, strict=True):
            if isinstance(outcome, RewriteResult):
                await self._settle(letter, rendered, outcome, report)
                await self._session.commit()
            else:
                logger.error("письма: переписывание для %s упало — %r", letter.host, outcome)
        if fallen:
            raise fallen[0]

    async def _rendered(self, letter: QueuedLetter) -> compose.Rendered | str:
        """Шаблон письма сейчас — или почему письмо переписать нельзя (словами итога)."""
        link: compose.FoundLink | None = None
        niche: compose.NicheOffer | None = None
        if letter.stage is Stage.ADVERTISERS and letter.audience == NICHE:
            niche = await NicheRecipients(self._session).offer_of(letter.message.domain_id)
            if niche is None:
                return NO_OFFER
        elif letter.stage is Stage.ADVERTISERS:
            if letter.link is None:
                return NO_LINK
            # Ссылка — в том виде, в каком её берёт сборка (`recipients.advertiser_candidates`).
            link = replace(
                letter.link,
                donor_host=letter.link.donor_host.strip().lower(),
                page_url=letter.link.page_url.strip(),
                anchor=letter.link.anchor.strip(),
            )
        rendered = compose.render(
            await self._template(letter),
            compose.values_for(
                host=letter.host, domain_id=letter.message.domain_id, link=link, niche=niche
            ),
        )
        plain = compose.assemble(rendered, {})
        if (plain.subject, plain.body) != (letter.message.subject, letter.message.body):
            return DIVERGED
        return rendered

    async def _template(self, letter: QueuedLetter) -> Template:
        """Текст рассылки письма — утверждённый при её создании или умолчание этапа, как
        у сборки (`QueueBuilder._letter_template`)."""
        campaign_id = letter.message.campaign_id
        if campaign_id not in self._templates:
            campaign = await self._session.get(CampaignModel, campaign_id)
            stored = campaign.letter_template if campaign is not None else None
            self._templates[campaign_id] = of_campaign(letter.stage, stored, letter.audience)
        return self._templates[campaign_id]

    def _about(self, letter: QueuedLetter, request: RewriteRequest) -> Personalization:
        """Что модель знает об адресате — то же, что знала сборка (`building._about`): у
        рекламодателя — страна письма, у донора — его прогон и цитата сайта."""
        known = self._donors.get(letter.message.domain_id)
        if known is None:
            return personalization(letter.host, country=request.country, niche=())
        return personalization(
            letter.host,
            country=known.country or request.country,
            niche=known.niche,
            about=known.about,
        )

    async def _settle(
        self,
        letter: QueuedLetter,
        rendered: compose.Rendered,
        rewritten: RewriteResult,
        report: RewriteReport,
    ) -> None:
        """Одно письмо: расход, проверки сборки и запись — или причина, почему оно как было."""
        for note in rewritten.notes:
            _count(report.notes, note)
            if note == NO_CREDITS_WORDS:
                report.stopped = note
        if rewritten.tokens_spent:
            usage.record(
                self._session,
                operation=OPERATION,
                units=rewritten.tokens_spent,
                stage=letter.stage,
            )
            report.tokens_spent += rewritten.tokens_spent
        finished = finish(rendered, rewritten, seed=letter.message.domain_id)
        if finished.uniqueness <= 0:
            # Модель отказала или вернула шаблон: письмо как было, причина — её словами.
            _count(report.left, "; ".join(rewritten.notes) or SAME_TEXT)
            return
        if not await _replace(self._session, letter.message, finished):
            _count(report.left, GONE)
            return
        report.rewritten += 1
        if corridor_verdict(finished.uniqueness) is not None:
            report.off_corridor += 1


async def _replace(session: AsyncSession, message: MessageModel, finished: Finished) -> bool:
    """Записать переписанное письмо — только если оно ещё в очереди и его текст тот, что
    прочитан. Ложь — его взяла пачка или поправил человек: писать поверх нельзя."""
    written = await session.execute(
        update(MessageModel)
        .where(
            MessageModel.id == message.id,
            MessageModel.status == MessageStatus.QUEUED,
            MessageModel.subject == message.subject,
            MessageModel.body == message.body,
        )
        .values(
            subject=finished.letter.subject,
            body=finished.letter.body,
            uniqueness_pct=finished.uniqueness,
        )
        .returning(MessageModel.id)
        # Как у правки (`settle.leave`): прочитанное письмо получает новые значения, только
        # если строка записана.
        .execution_options(synchronize_session="fetch")
    )
    return written.first() is not None
