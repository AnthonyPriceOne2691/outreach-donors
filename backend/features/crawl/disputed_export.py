"""Спорные кандидаты файлом: специалист проходит их и даёт обратную связь.

Тест-группа 31.10.2026: на вкладке спорных нужна выгрузка всех спорных таблицей. Экран показывает
очередь страницей, а разобрать сотни пограничных ссылок удобнее в таблице, с колонками под своё
решение. Формат — тот же, что у выгрузки рекламодателей (`advertiser_export.py`): точка с запятой,
UTF-8 с меткой, потолок строк один, текст с чужих страниц в ячейке не исполняется.

**В файле все спорные, и решённые тоже — с пометкой**: специалисту важно видеть, что уже сказано,
а не проверять решённое второй раз вслепую. Две пустые колонки — под ответ: загрузка файла обратно
появится отдельной кнопкой, а пока файл читает человек.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import Verdict
from backend.features.core.models.advertiser import CandidateModel
from backend.features.crawl.advertiser_export import EXPORT_LIMIT, table_csv

#: Заголовки: сначала то, что знает сервис, потом — что впишет специалист.
COLUMNS = (
    "донор",
    "домен",
    "балл",
    "за что",
    "лучшая страница",
    "анкор",
    "решение сейчас",
    "решение специалиста",
    "комментарий",
)

#: Решение человека словами экрана проверки.
DECISIONS: dict[bool | None, str] = {True: "пишем", False: "не пишем", None: "ждёт решения"}


async def disputed(
    session: AsyncSession, *, limit: int = EXPORT_LIMIT
) -> tuple[list[CandidateModel], int]:
    """Спорные кандидаты: не больше `limit` строк и сколько их всего. Порядок полный: сверху —
    самые доказательные, чтобы файл с потолком отдавал те, что ближе к решению."""
    only = CandidateModel.verdict == Verdict.PENDING
    rows = await session.scalars(
        select(CandidateModel)
        .where(only)
        .order_by(
            CandidateModel.points.desc(),
            CandidateModel.donor_host,
            CandidateModel.target_root,
            CandidateModel.id,
        )
        .limit(limit)
    )
    total = await session.scalar(select(func.count()).select_from(CandidateModel).where(only))
    return list(rows.all()), int(total or 0)


def _values(row: CandidateModel) -> tuple[object, ...]:
    return (
        row.donor_host,
        row.target_root,
        row.points,
        "; ".join(str(reason) for reason in row.reasons or []),
        row.best_page_url,
        row.best_anchor,
        DECISIONS[row.confirmed],
        None,
        None,
    )


def to_csv(rows: Sequence[CandidateModel]) -> bytes:
    """Спорные одним файлом."""
    return table_csv(COLUMNS, [_values(row) for row in rows])
