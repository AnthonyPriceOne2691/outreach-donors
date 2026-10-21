"""Проход судьи по кандидатам прогона: кого судим, что считаем, кого режем.

**Судья стоит ДО Ahrefs, а не после.** Это и есть весь смысл: за домен,
который мы всё равно отбросим, платить метриками незачем. Сравнение цен:
~430 токенов дешёвой модели против 2 юнитов за просев по DR и 28 за пороги.

**В наблюдении он всё равно судит всех — и это не расточительство.**
Отрезать он не отрезает, но считает, сколько юнитов СЭКОНОМИЛ БЫ, если бы
резал. Это единственное число, которым можно обосновать включение: до него
разговор о пользе судьи остаётся разговором.

**Кэш отдельного хранилища не требует.** Вердикт лежит на домене с отметкой
времени, и свежий домен просто не попадает в список на суд. Срок свой,
длиннее метрик: способ заработка сайт меняет раз в годы.

**Сбой судьи не отбирает домен.** Любой отказ — «посмотри», и в `enforce`
такой домен идёт дальше как обычно: терять его из-за нашей поломки дороже,
чем оплатить ему метрики. **У ключа нет кредитов** (слово владельца 20.10.2026) —
до конца прохода модель не зовётся (`JudgeSummary.stopped`): остальные домены — без
вердикта, «модель не ответила» с причиной словами, а не вызов на каждый.

**Нет текста выдачи — судят главную** (`by_home`): кандидат из списка ссылок конкурентов
или выдача без сниппета. Тем же походом, что вторая сторона (`check_home`), и тем же
текстом читает тематика. Не открылась и она — вердикта нет (`Judgement.blind`): в кэш он
не ложится, домен судится следующим прогоном, а в отчёте — своим числом (`no_text`).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field, replace

import httpx

from backend.config import judge as cfg
from backend.features.donors.author_door import author_door, open_door
from backend.features.donors.home_signals import HomeSignals, check_home
from backend.features.donors.publisher_judge import (
    PROMPT_VERSION,
    Decider,
    Intent,
    Judgement,
    Recommendation,
    arbitrate,
    before_model,
    judge_host,
    source_text,
)
from backend.features.donors.repository import JudgeRecord
from backend.features.donors.site_index import index_homes
from backend.features.runs.planning import SerpText
from backend.features.serp.protocol import SerpProvider
from backend.shared.llm import NO_CREDITS_WORDS

logger = logging.getLogger(__name__)

#: Цена ступеней, которых стоил бы отрезанный домен (okf/unit-economy.md).
#: Считаем только две первые: страны зовутся не всем, и приписывать их
#: каждому значило бы завышать экономию.
UNITS_DR = 2
UNITS_METRICS = 28
#: Пометка вердикта по главной: текста выдачи у кандидата не было (`by_home`).
BY_HOME = "по главной: текста выдачи нет"


@dataclass(slots=True)
class JudgeSummary:
    """Что судья сделал за прогон. Читается человеком в отчёте."""

    judged: int = 0
    """Скольких судили на самом деле — то есть заплатили за них токенами."""

    from_cache: int = 0
    """Скольких не пересуживали: вердикт ещё свеж."""

    would_cut: int = 0
    """Скольких отрезал бы. В `enforce` это и есть отрезанные."""

    to_review: int = 0
    """Скольких отправил человеку советом «посмотри»: спорные, отказы доступа."""

    unanswered: int = 0
    """Модель не ответила: вердикта нет, домен ушёл человеку без совета
    и в следующий раз судится снова. Отдельным числом, а не в `judged`:
    прогон №21 без ключа модели отчитался «судили 59, токенов 0» — судья,
    который не работал, выглядел как судья, никого не отрезавший."""

    unanswered_reason: str | None = None
    """Почему не ответила — первая причина: что чинить."""

    tokens: int = 0
    by_intent: dict[str, int] = field(default_factory=dict)
    by_decider: dict[str, int] = field(default_factory=dict)
    """Кто решил: правило, модель, арбитр. Точность меряется по каждому."""

    would_cut_paid: int = 0
    """Отрезал бы из тех, за кого ещё не платили. Только они и дают экономию:
    свежий домен досуживается ради знания, его метрики уже куплены."""

    home_unreached: int = 0
    """Главная не открылась. Отдельным числом: иначе закрывшийся сайт
    выглядит как сайт без признаков магазина."""

    no_text: int = 0
    """Судить было не по чему: ни текста выдачи, ни главной. Модель не звали, вердикта
    нет — домен судится следующим прогоном. Отдельным числом: иначе такой домен читался
    бы судом «посмотри»."""

    from_index: int = 0
    """Скольких судили по образу главной из индекса поиска."""

    index_usd: float = 0.0
    """Цена запросов `site:` — деньги источника выдачи, в журнал отдельно."""

    stopped: str | None = None
    """У ключа модели нет кредитов: до конца прохода модель не зовётся — почему, словами."""

    @property
    def units_saved(self) -> int:
        """Во что обошлись бы отрезанные, дойди они до Ahrefs.

        Нижняя граница: считаются просев по DR и пороги, запрос по странам
        не считается вовсе. Завышать экономию нельзя — на неё будут
        ссылаться, решая, включать ли отказ.
        """
        return self.would_cut_paid * (UNITS_DR + UNITS_METRICS)

    def merge(self, other: JudgeSummary) -> None:
        """Сложить итог другого прохода — досуд идёт пачками."""
        self.judged += other.judged
        self.from_cache += other.from_cache
        self.would_cut += other.would_cut
        self.would_cut_paid += other.would_cut_paid
        self.to_review += other.to_review
        self.unanswered += other.unanswered
        self.unanswered_reason = self.unanswered_reason or other.unanswered_reason
        self.tokens += other.tokens
        self.home_unreached += other.home_unreached
        self.no_text += other.no_text
        self.from_index += other.from_index
        self.index_usd += other.index_usd
        self.stopped = self.stopped or other.stopped
        for key, count in other.by_intent.items():
            self.by_intent[key] = self.by_intent.get(key, 0) + count
        for key, count in other.by_decider.items():
            self.by_decider[key] = self.by_decider.get(key, 0) + count

    def record(self, verdict: Judgement, *, paid: bool) -> None:
        self.tokens += verdict.tokens
        if verdict.blind:
            self.no_text += 1
            return
        if verdict.unanswered:
            self.unanswered += 1
            if self.unanswered_reason is None:
                self.unanswered_reason = verdict.reason[:300]
            return
        self.judged += 1
        self.by_intent[verdict.intent.value] = self.by_intent.get(verdict.intent.value, 0) + 1
        who = verdict.decided_by.value
        self.by_decider[who] = self.by_decider.get(who, 0) + 1
        if verdict.recommendation is Recommendation.REJECT:
            self.would_cut += 1
            self.would_cut_paid += int(paid)
        elif verdict.recommendation is Recommendation.REVIEW:
            self.to_review += 1


@dataclass(frozen=True, slots=True)
class JudgePass:
    """Итог прохода: что записать и с кем идти дальше."""

    verdicts: dict[str, JudgeRecord]
    summary: JudgeSummary
    rejected: set[str]
    """Кого судья отрезал. В наблюдении этот список НЕ применяется."""

    homes: dict[str, HomeSignals] = field(default_factory=dict)
    """Главные, которые судья открыл в этом проходе (или их образ из индекса): тот же
    текст читает тематика сайта (`topics/step.py`), второй раз за главной не ходят."""


async def second_opinion(
    http: httpx.AsyncClient,
    home_client: httpx.AsyncClient | None,
    *,
    host: str,
    text: SerpText | None,
    verdict: Judgement,
) -> tuple[Judgement, HomeSignals | None]:
    """Главная как вторая сторона. Способ заработка — свойство сайта, а не
    страницы, и выдача показывает страницу.

    **Правило доказательства: отрезать может только структура.** Модель
    отказывает, лишь когда главная подтверждает продажу своего — корзиной,
    разметкой услуги, тарифами. Без подтверждения её отказ идёт человеку:
    замер 23.09 — арбитр, судивший всех, поймал 16 из 16 компаний-услуг,
    но отрезал и 5 из 77 изданий, у которых сбоку свой магазин или курс.
    С правилом — ноль ложных отказов, и ни одна компания не прошла в приём.

    Исходы:
    - «продаёт своё» по выдаче и продажа на главной — решено ПРАВИЛОМ;
    - «издание» по выдаче — всегда к арбитру, если главная открылась:
      блог компании по выдаче неотличим от издания (стоматология, агентство,
      страховщик — 23.09 модель пропустила восемь таких);
    - главная не открылась — остаётся вердикт модели.
    """
    if home_client is None or verdict.decided_by is Decider.RULE or verdict.intent not in DISPUTED:
        return verdict, None
    home = await check_home(home_client, host)
    return await settle(http, host=host, text=text, verdict=verdict, home=home), home


#: Вердикты модели, которые главная может подтвердить или оспорить.
#: `sells_placement` и `link_vendor` сюда не входят: продажа размещения —
#: вопрос о СТРАНИЦЕ приёма, а не о витрине, и корзина на главной его
#: не отменяет (прогон №18: `/pricing` отрезал продавца гостевых статей).
DISPUTED = frozenset({Intent.SELLS_OWN, Intent.REFERS_OUT, Intent.EDITORIAL_ADS})


async def settle(
    http: httpx.AsyncClient,
    *,
    host: str,
    text: SerpText | None,
    verdict: Judgement,
    home: HomeSignals,
) -> Judgement:
    """Вердикт модели и уже прочитанная главная → итог. Без скачивания:
    эталон (`scripts/eval_judge.py`) подаёт сюда главную из файла."""
    if not home.reached:
        return verdict
    if verdict.intent is Intent.SELLS_OWN:
        if not home.sells:
            return verdict
        marks = ", ".join((*home.shop, *home.service)[:3])
        return replace(
            verdict, reason=f"{verdict.reason} · главная: {marks}", decided_by=Decider.RULE
        )

    serp = source_text(text.title, text.description) if text else ""
    ruling = await arbitrate(http, host=host, serp=serp, home=home)
    ruling = replace(ruling, tokens=ruling.tokens + verdict.tokens)
    if ruling.recommendation is Recommendation.REJECT and not home.sells:
        ruling = replace(
            ruling,
            recommendation=Recommendation.REVIEW,
            reason=f"{ruling.reason} · на главной не видно продажи — посмотри",
        )
    return ruling


def page_of(home: HomeSignals) -> SerpText | None:
    """Главная страницей для судьи: заголовок и описание, а описания нет — начало видимого
    текста. Не открылась или пуста — `None`: судить не по чему."""
    description = home.description or home.text
    if not home.reached or not (home.title or description):
        return None
    return SerpText(url="", title=home.title or None, description=description or None)


def _without_text(host: str, text: SerpText | None) -> bool:
    """Текста выдачи нет, а правило без текста домен не решает (платформа, госзона решают)."""
    ruled = before_model(host, source_text(text.title, text.description) if text else "")
    return ruled is not None and ruled.blind


async def by_home(
    http: httpx.AsyncClient, home_client: httpx.AsyncClient, *, host: str
) -> tuple[Judgement, HomeSignals]:
    """Кандидат без текста выдачи: судья читает главную тем же походом, что вторую сторону
    (`check_home`: robots, пауза на хост, свой таймаут), и судит по ней одним вызовом —
    спорить странице с главной не о чем. Отказ модели по главной режет, только если его
    подтверждает структура (корзина, услуга), иначе — «посмотри»: так же досуд по главной
    (`backfill._from_home_verdict`). Главная не открылась или пуста — вердикта нет."""
    home = await check_home(home_client, host)
    page = page_of(home)
    verdict = await judge_host(
        http,
        host=host,
        title=page.title if page else None,
        description=page.description if page else None,
    )
    if verdict.blind or verdict.unanswered or verdict.decided_by is Decider.RULE:
        return verdict, home
    reason = f"{BY_HOME} · {verdict.reason}"
    if verdict.recommendation is not Recommendation.REJECT:
        return replace(verdict, reason=reason), home
    if verdict.intent is Intent.SELLS_OWN and home.sells:
        marks = ", ".join((*home.shop, *home.service)[:3])
        return replace(
            verdict, reason=f"{reason} · главная: {marks}", decided_by=Decider.RULE
        ), home
    return replace(
        verdict, recommendation=Recommendation.REVIEW, reason=f"{reason} · посмотри"
    ), home


async def through_index(
    http: httpx.AsyncClient,
    index: SerpProvider,
    results: dict[str, tuple[Judgement, HomeSignals | None]],
    texts: Mapping[str, SerpText],
) -> float:
    """Второй проход по тем, чья главная закрылась: образ главной из индекса.

    Спрашиваем только про принятых моделью — у отрезанных и отправленных
    к человеку спорить не о чем. Пакетом, одним запросом на всех: `site:`
    у источника выдачи стоит денег, и по одному это десятки вызовов.
    """
    blind = [
        host
        for host, (verdict, home) in results.items()
        if home is not None
        and not home.reached
        and verdict.decided_by is Decider.MODEL
        and verdict.recommendation is Recommendation.ACCEPT
    ]
    homes, cost = await index_homes(index, blind)
    for host, home in homes.items():
        verdict, _ = results[host]
        text = texts.get(host)
        serp = source_text(text.title, text.description) if text else ""
        ruling = await arbitrate(http, host=host, serp=serp, home=home)
        ruling = replace(ruling, tokens=ruling.tokens + verdict.tokens)
        if ruling.recommendation is Recommendation.REJECT:
            # Структуры в индексе нет — правило доказательства шлёт к человеку.
            ruling = replace(
                ruling,
                recommendation=Recommendation.REVIEW,
                reason=f"{ruling.reason} · главная закрыта, судил по индексу — посмотри",
            )
        results[host] = (ruling, home)
    return cost


def door_of(text: SerpText | None, home: HomeSignals | None) -> str | None:
    """Зовёт ли сайт авторов: страница из выдачи или меню главной."""
    return author_door(
        text.url if text else None,
        text.title if text else None,
        home.nav if home is not None and home.reached else (),
    )


def door_record(door: str | None, home: HomeSignals | None) -> str | None:
    """Что записать о двери. Нашлась — где; не нашлась, а меню главной
    смотрели — пустая строка; меню не смотрели — `None`, то есть «не знаем»:
    отсутствие двери в выдаче не значит, что её нет в меню."""
    if door is not None:
        return door
    return "" if home is not None and home.reached else None


def _collect(
    results: Mapping[str, tuple[Judgement, HomeSignals | None]],
    texts: Mapping[str, SerpText],
    summary: JudgeSummary,
    unpaid: Collection[str],
) -> tuple[dict[str, JudgeRecord], set[str]]:
    """Вердикты — в записи для базы и в счёт прохода.

    Ответ, которого модель не дала, считается, но в записи не попадает:
    на домене остаётся прежнее знание (или никакого), и следующий прогон
    судит его снова. Дверь такого домена досмотрит проверка очереди
    (`donors.doors`) — она видит и страницу выдачи.
    """
    verdicts: dict[str, JudgeRecord] = {}
    rejected: set[str] = set()
    for host, (judged, home) in results.items():
        text = texts.get(host)
        door = door_of(text, home)
        verdict = open_door(judged, door)
        summary.record(verdict, paid=host in unpaid)
        if verdict.unanswered or verdict.blind:
            continue
        if home is not None and not home.reached:
            summary.home_unreached += 1
        if home is not None and home.via == "index":
            summary.from_index += 1
        verdicts[host] = JudgeRecord(
            intent=verdict.intent.value,
            recommendation=verdict.recommendation.value,
            reason=verdict.reason[:256],
            quote=verdict.quote,
            source_url=text.url if text else None,
            model=verdict.model,
            version=PROMPT_VERSION if verdict.model else None,
            decided_by=verdict.decided_by.value,
            home=home.as_dict() if home is not None else None,
            door=door_record(door, home),
        )
        if verdict.recommendation is Recommendation.REJECT:
            rejected.add(host)
    return verdicts, rejected


async def _judged(
    http: httpx.AsyncClient, home_client: httpx.AsyncClient | None, host: str, text: SerpText | None
) -> tuple[Judgement, HomeSignals | None]:
    """Вердикт одного домена: без текста выдачи — по главной (`by_home`), иначе выдача и главная
    второй стороной (`second_opinion`)."""
    if home_client is not None and _without_text(host, text):
        return await by_home(http, home_client, host=host)
    first = await judge_host(
        http,
        host=host,
        title=text.title if text else None,
        description=text.description if text else None,
    )
    return await second_opinion(http, home_client, host=host, text=text, verdict=first)


def _opened(results: Mapping[str, tuple[Judgement, HomeSignals | None]]) -> dict[str, HomeSignals]:
    """Главные, которые судья видел в проходе, — тексту темы сайта (`JudgePass.homes`)."""
    return {host: home for host, (_, home) in results.items() if home is not None}


async def judge_candidates(
    http: httpx.AsyncClient,
    hosts: list[str],
    texts: Mapping[str, SerpText],
    *,
    already_judged: Mapping[str, str] | None = None,
    paid: Collection[str] | None = None,
    home_client: httpx.AsyncClient | None = None,
    index: SerpProvider | None = None,
    stopped: str | None = None,
) -> JudgePass:
    """Судит домены, которые ещё не судили. Не бросает.

    `already_judged` — свежие вердикты из базы: их владелец берёт одним
    запросом до прохода, чтобы не ходить в базу на каждый домен.
    `paid` — за кого ещё предстоит платить Ahrefs; только они дают экономию.
    Пусто — все. `home_client` — клиент для главных; нет его — без главной.
    `index` — источник выдачи для `site:`, когда главная закрыта; нет его —
    закрытая главная оставляет вердикт модели. `stopped` — модель встала прошлой пачкой
    (нет кредитов): свежие — из кэша, остальные — без вызова, «не ответила» той же причиной.
    """
    fresh = already_judged or {}
    unpaid = set(hosts) if paid is None else set(paid)
    summary = JudgeSummary(from_cache=sum(1 for host in hosts if host in fresh), stopped=stopped)
    pending = [host for host in hosts if host not in fresh]
    verdicts: dict[str, JudgeRecord] = {}
    rejected = {host for host, rec in fresh.items() if rec == Recommendation.REJECT.value}

    if not pending:
        return JudgePass(verdicts, summary, rejected)

    gate = asyncio.Semaphore(cfg.CONCURRENCY)
    results: dict[str, tuple[Judgement, HomeSignals | None]] = {}

    async def one(host: str) -> None:
        async with gate:
            if summary.stopped is not None:
                summary.unanswered += 1  # без кредитов модель не зовём: судится следующим прогоном
                return
            results[host] = await _judged(http, home_client, host, texts.get(host))
        if results[host][0].no_credits:
            summary.stopped = NO_CREDITS_WORDS

    # `gather` без `return_exceptions` уронил бы прогон из-за одного домена,
    # а судья — не та ступень, ради которой стоит терять оплаченную выдачу.
    outcomes = await asyncio.gather(*(one(host) for host in pending), return_exceptions=True)
    for host, outcome in zip(pending, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            logger.warning("судья площадки упал на %s: %s", host, outcome)

    if index is not None and summary.stopped is None:
        try:
            summary.index_usd = await through_index(http, index, results, texts)
        except Exception as exc:  # noqa: BLE001 — индекс необязателен, вердикты уже есть
            logger.warning("судья площадки: индекс не ответил (%r) — остаются вердикты модели", exc)

    collected, cut = _collect(results, texts, summary, unpaid)
    verdicts.update(collected)
    rejected |= cut
    if summary.stopped is not None:
        summary.unanswered_reason = summary.stopped  # что чинить — словами, а не кодом провайдера
    return JudgePass(verdicts, summary, rejected, _opened(results))
