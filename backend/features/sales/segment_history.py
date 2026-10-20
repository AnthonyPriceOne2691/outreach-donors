"""История сбора для сметы прогона: сколько сайтов давала выдача, что гипотеза уже собрала,
сколько токенов судьи стоил сайт и какую их долю занимает вывод.

Смета до выдачи не знает, сколько сайтов найдётся, — но знает, сколько их давали прошлые прогоны
сбора и какие сайты гипотеза уже собрала. Проверка прода 14.10: общая доля 0,85 (замер выдачи
доноров) пообещала заметно больше сайтов и токенов, чем вышло. Числа — из истории своих прогонов,
под любую нишу и рынок, а не константой из одного замера. Так же и доля вывода: вывод модели
дороже ввода, и рассуждение идёт в счёт вывода, — смета берёт её из прогонов с раздельным счётом.
И токены на сайт (разбор прогона сбора 20.10: смета считала константой из замера 14.10, а живой
прогон вышел заметно дороже): длина описания сегмента, текст главных и рассуждение модели у
каждой гипотезы свои — смета берёт их из прогонов с судьёй (`tokens_per_site`).
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.runs.estimate import RESULTS_PER_PAGE, UNIQUE_SHARE
from backend.features.sales.segment_models import SalesSegmentCandidateModel, SalesSegmentRunModel
from backend.features.sales.segment_spend import OUTPUT_SHARE

#: Сколько последних прогонов сбора с выдачей смотрит доля сайтов.
HISTORY_RUNS = 20
#: Меньше запрошенных результатов в истории — доля из горсти: смета берёт запасную.
MIN_HISTORY_RESULTS = 20
#: Меньше токенов с разбивкой в истории — доля вывода из горсти сайтов (около десятка):
#: смета берёт запасную.
MIN_SPLIT_TOKENS = 10_000
#: Меньше сайтов, которые судья спрашивал в истории, — токены на сайт из горсти: смета берёт
#: запасное число (`segment_estimate.TOKENS_PER_SITE`).
MIN_JUDGED_SITES = 20


async def sites_per_result(session: AsyncSession) -> tuple[float, int]:
    """Сколько сайтов давал запрошенный результат выдачи в прошлых прогонах сбора — и по
    скольким прогонам. Без своей истории — запасная доля и ноль прогонов.

    Считается суммой по последним прогонам с выдачей: сайтов (`report.serp.hosts`) на
    запрошенные результаты отвеченных ключей (ключи × глубина × 10). На ту же величину смета её
    и умножает — сверяются сопоставимые числа: выдача отдаёт не каждый запрошенный результат и
    повторяет сайты, и доля «сайтов на пришедшую строку» завышала бы смету на недобор выдачи."""
    rows = await session.execute(
        select(SalesSegmentRunModel.report["serp"], SalesSegmentRunModel.depth_pages)
        .where(SalesSegmentRunModel.report.has_key("serp"))
        .order_by(SalesSegmentRunModel.id.desc())
        .limit(HISTORY_RUNS)
    )
    hosts = asked = runs = 0
    for serp, depth in rows.tuples():
        if not isinstance(serp, dict):
            continue
        slots = int(serp.get("keywords") or 0) * int(depth) * RESULTS_PER_PAGE
        if slots > 0:
            hosts, asked, runs = hosts + int(serp.get("hosts") or 0), asked + slots, runs + 1
    if asked < MIN_HISTORY_RESULTS:
        return UNIQUE_SHARE, 0
    return min(1.0, hosts / asked), runs


async def known_sites(
    session: AsyncSession,
    *,
    hypothesis_id: int,
    country: str,
    keywords: Sequence[str],
    per_keyword: float,
) -> int:
    """Сколько сайтов выдача найдёт, а гипотеза уже собрала: прежние прогоны этой гипотезы на
    том же рынке нашли их теми же ключами. Второй раз их не заводят (`segment_serp`) — не судят
    и адрес не ищут. На ключ — не больше, чем даст сайтов его выдача (`per_keyword`): прежний
    прогон мог быть глубже. Сайт, найденный прежде другим ключом, не вычитается: смета скорее
    завысит, чем занизит."""
    if not keywords:
        return 0
    rows = await session.scalars(
        select(func.count())
        .select_from(SalesSegmentCandidateModel)
        .join(SalesSegmentRunModel, SalesSegmentRunModel.id == SalesSegmentCandidateModel.run_id)
        .where(
            SalesSegmentCandidateModel.hypothesis_id == hypothesis_id,
            SalesSegmentRunModel.country == country,
            SalesSegmentCandidateModel.keyword.in_(list(keywords)),
        )
        .group_by(SalesSegmentCandidateModel.keyword)
    )
    return round(sum(min(count, per_keyword) for count in rows.all()))


async def output_share(session: AsyncSession) -> tuple[float, int]:
    """Доля вывода в токенах судьи сегмента по последним прогонам с раздельным счётом — и по
    скольким. Без такой истории — запасная доля (`segment_spend.OUTPUT_SHARE`) и ноль прогонов.

    Суммой по прогонам (`report.judge`: ввод и вывод — `segment_window.SPLIT_TOKENS`), а не
    средним долей: прогон в десять сайтов не весит столько же, сколько прогон в сотню. Токены без
    разбивки в долю не идут — их делить не на что."""
    judge = SalesSegmentRunModel.report["judge"]
    rows = await session.scalars(
        select(judge)
        .where(judge.has_key("completion_tokens"))
        .order_by(SalesSegmentRunModel.id.desc())
        .limit(HISTORY_RUNS)
    )
    entered = produced = runs = 0
    for found in rows.all():
        if not isinstance(found, dict):
            continue
        prompt, completion = (
            int(found.get("prompt_tokens") or 0),
            int(found.get("completion_tokens") or 0),
        )
        if prompt + completion > 0:
            entered, produced, runs = entered + prompt, produced + completion, runs + 1
    if entered + produced < MIN_SPLIT_TOKENS:
        return OUTPUT_SHARE, 0
    return produced / (entered + produced), runs


def _int(value: object) -> int:
    """Число из отчёта прогона; не число (поле правили руками, его нет) — ноль."""
    return int(value) if isinstance(value, int | float) else 0


async def tokens_per_site(session: AsyncSession) -> tuple[int, int]:
    """Токенов судьи сегмента на сайт по последним прогонам с судьёй — и по скольким прогонам.
    Без такой истории — `(0, 0)`: запасное число подставляет смета.

    Суммой по прогонам (`report.judge`), а не средним прогонов: прогон в десять сайтов не весит
    столько же, сколько прогон в сотню. На сайт — из тех, кого судья спросил заново, а не взял из
    кэша: судил (`judged`, и правилом до модели — тоже сайт прогона) или не дождался ответа модели
    (`unanswered`: токены такого вызова тоже в счёте). Смета умножает это число на новые сайты
    прогона: кэш в ней не вычитается — она скорее завысит, чем занизит."""
    judge = SalesSegmentRunModel.report["judge"]
    rows = await session.scalars(
        select(judge)
        .where(judge.has_key("tokens"))
        .order_by(SalesSegmentRunModel.id.desc())
        .limit(HISTORY_RUNS)
    )
    tokens = sites = runs = 0
    for found in rows.all():
        if not isinstance(found, dict):
            continue
        spent, asked = (
            _int(found.get("tokens")),
            _int(found.get("judged")) + _int(found.get("unanswered")),
        )
        if spent > 0 and asked > 0:
            tokens, sites, runs = tokens + spent, sites + asked, runs + 1
    if sites < MIN_JUDGED_SITES:
        return 0, 0
    return round(tokens / sites), runs
