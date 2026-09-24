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

**Незаполненная обязательная настройка сборку не останавливает, но
называется вслух.** Живой прогон собрал три письма на 1361 токен, не сказав
ни слова о том, что отправить нельзя ни одно: тогда не хватало физического
адреса и отписки (юридический блок снят 23.09.2026, теперь это имя
отправителя).
Отказать здесь было бы неверно — очередь готовят и для того, чтобы
посмотреть текст, — а промолчать нельзя: человек нажмёт «отправить»
и узнает об этом на третьем письме. Поэтому отчёт несёт список того,
чем отправка заблокирована, а сама отправка отказывает жёстко.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.contacts.quality import rejection_reason
from backend.features.core import usage
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.outreach import MessageModel
from backend.features.core.models.run import RunModel
from backend.features.letters import compose, guards
from backend.features.letters.repository import Candidate, LetterRepository
from backend.features.letters.rewrite import Personalization, RewriteClient
from backend.features.letters.template import Template, default, parse
from backend.features.letters.uniqueness import corridor_verdict, difference

logger = logging.getLogger(__name__)

#: Первое письмо цепочки. Добивки — шаги 1 и 2, они придут с Ф5.
FIRST_STEP = 0


def idempotency_key(*, stage: Stage, host: str, step: int) -> str:
    """Домен плюс этап плюс шаг.

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
    #: Текст письма, утверждённый на экране. Пусто — шаблон из кода.
    #: Ложится в рассылку при её создании; у найденной не меняется.
    letter_template: str | None = None
    #: Прогоны, из принятых доноров которых собирается рассылка. Пусто —
    #: все принятые (командная строка, тесты); экран шлёт прогоны всегда.
    run_ids: tuple[int, ...] = ()


class LetterScopeError(ValueError):
    """Из этих прогонов рассылку не собрать. Сообщение говорит, что сделать."""

    #: Повтор задачи это не исправит (`runs/failures.py`).
    permanent = True


@dataclass(frozen=True, slots=True)
class RunScope:
    """Что прогоны рассылки задают письму: страну и нишу каждого донора."""

    country: str | None = None
    #: Домен → ключи, по которым он нашёлся. Это ниша для переписывания
    #: вступления — точнее, чем ключи всей рассылки через запятую.
    found_by: dict[str, list[str]] = field(default_factory=dict)


async def run_scope(repo: LetterRepository, run_ids: Sequence[int]) -> RunScope:
    """Проверить прогоны рассылки и достать из них страну и нишу.

    Отказ — до постановки сборки: рассылка по прогонам разных стран ушла
    бы одним языком, а по прогону с неоконченным поиском контактов — части
    принятых, и остальные молча выпали бы (так же сборку держит соседняя
    система, пока контакты собираются).
    """
    if not run_ids:
        return RunScope()
    runs = await repo.runs(run_ids)
    country = _one_country(runs, run_ids)
    pending = await repo.contacts_pending(run_ids)
    if pending:
        raise LetterScopeError(
            f"Контакты ещё не искали у {pending} принятых доноров этих прогонов. "
            "Дождаться поиска контактов — иначе они выпадут из рассылки"
        )
    return RunScope(country=country, found_by=_found_by(runs))


def _one_country(runs: Sequence[RunModel], run_ids: Sequence[int]) -> str:
    missing = sorted(set(run_ids) - {run.id for run in runs})
    if missing:
        raise LetterScopeError(f"Прогонов {', '.join(map(str, missing))} нет")
    countries = sorted({run.country.lower() for run in runs})
    if len(countries) > 1:
        raise LetterScopeError(
            f"Прогоны из разных стран ({', '.join(countries)}): язык письма берётся от страны. "
            "Собрать по рассылке на страну"
        )
    return countries[0]


def _found_by(runs: Sequence[RunModel]) -> dict[str, list[str]]:
    """Ключи, по которым нашёлся каждый домен, — из всех прогонов рассылки."""
    merged: dict[str, list[str]] = {}
    for run in runs:
        for host, keys in ((run.candidates or {}).get("found_by") or {}).items():
            known = merged.setdefault(host, [])
            known.extend(key for key in keys if key not in known)
    return merged


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
    #: Доноры, чей сохранённый адрес не прошёл нынешний фильтр качества:
    #: причина → сколько. Письма им не готовятся.
    bad_addresses: dict[str, int] = field(default_factory=dict)


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
        # Прогоны проверяются до того, как заведена рассылка: отказ по ним
        # не должен оставлять пустую рассылку с их именем.
        scope = await run_scope(self._repo, request.run_ids)
        if scope.country is not None and scope.country != request.country.lower():
            request = replace(request, country=scope.country)
        campaign = await self._repo.campaign(
            name=request.campaign_name,
            stage=request.stage,
            run_id=request.run_id,
            followup_days=request.followup_days,
            letter_template=request.letter_template,
        )
        # Текст рассылки, а не умолчание: его утвердили при её создании.
        letter_template = (
            parse(campaign.letter_template) if campaign.letter_template else self._template
        )
        # Метрики Ahrefs в письме запрещены правилами Ahrefs. Неизменяемые
        # зоны одинаковы во всех письмах, поэтому шаблон проверяется один
        # раз — до того, как на него потратят двести вызовов модели.
        guards.assert_no_metrics(letter_template.body)

        # Чекпоинт: рассылка заведена. Дальше каждое письмо фиксируется
        # отдельно — упавшая сборка не теряет подготовленного и потраченных
        # на него токенов, а повтор продолжает с того же места: написанным
        # письмо уже считается (`_not_written`).
        await self._session.commit()
        report = BuildReport(campaign_id=campaign.id)
        report.funnel = (
            await self._repo.funnel(request.stage, run_ids=request.run_ids)
        ).as_report()
        report.blocked_by = compose.missing_settings()
        if report.blocked_by:
            logger.warning(
                "письма: очередь собирается, но отправить будет нельзя — не заполнено %s",
                ", ".join(report.blocked_by),
            )

        candidates = await self._repo.candidates(
            request.stage, limit=request.limit, run_ids=request.run_ids
        )
        for candidate in candidates:
            if self._bad_address(candidate, report):
                continue
            await self._prepare(
                candidate,
                letter_template,
                campaign_id=campaign.id,
                request=request,
                report=report,
                found_by=scope.found_by,
            )
            await self._session.commit()

        logger.info(
            "письма: подготовлено %s из %s, токенов %s, вне коридора %s",
            report.prepared,
            len(candidates),
            report.tokens_spent,
            report.off_corridor,
        )
        return report

    @staticmethod
    def _bad_address(candidate: Candidate, report: BuildReport) -> bool:
        """Адрес перепроверяется фильтром качества при каждой сборке.

        Фильтр живёт в лестнице контактов и срабатывает в момент находки,
        а база копится месяцами: правило, добавленное позже, иначе не
        действовало бы на уже сохранённые адреса. Прогон 23.09.2026 оставил
        в базе `you@yourbusiness.com` — фильтр тогда заглушки по домену
        не знал. Проверка до вызова модели: письмо, которое не уйдёт,
        не стоит токенов.
        """
        reason = rejection_reason(candidate.email)
        if reason is None:
            return False
        title = reason.split(":", 1)[0]
        report.bad_addresses[title] = report.bad_addresses.get(title, 0) + 1
        logger.warning("письма: %s пропущен — %s", candidate.host, reason)
        return True

    async def _prepare(
        self,
        candidate: Candidate,
        letter_template: Template,
        *,
        campaign_id: int,
        request: BuildRequest,
        report: BuildReport,
        found_by: dict[str, list[str]],
    ) -> None:
        """Одно письмо: текст, проверки, запись в очередь."""
        rendered = compose.render(
            letter_template,
            compose.values_for(host=candidate.host, domain_id=candidate.domain_id),
        )
        rewritten = await self._rewriter.rewrite(
            rendered,
            Personalization(
                host=candidate.host,
                country=request.country,
                niche=tuple(found_by.get(candidate.host, ())) or request.niche,
            ),
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
