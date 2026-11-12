"""Спорные файлом: все спорные, решённые — с пометкой и с тем, кто решил, текст с чужих страниц
не исполняется.

Проверяется содержимое файла, а не факт скачивания: заголовки, строки, колонки под ответ
специалиста, потолок строк и число «сколько всего» в заголовке ответа.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Awaitable, Callable

import pytest
from backend.features.core.domain import UserRole, Verdict
from backend.features.core.models.access import UserModel
from backend.features.crawl import disputed_export
from backend.features.crawl.judge_mark import marker_of
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.stage2_rows import crawled, scored

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]


def _table(content: bytes) -> list[list[str]]:
    text = content.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text), delimiter=";"))


@pytest.fixture
async def disputed(session: AsyncSession) -> None:
    run = await crawled(session, "donor.example.test")
    await scored(session, run, "strong.example.test", verdict=Verdict.PENDING, points=3)
    await scored(session, run, "weak.example.test", verdict=Verdict.PENDING, points=2)
    await scored(session, run, "yes.example.test", verdict=Verdict.PENDING, confirmed=True)
    await scored(session, run, "no.example.test", verdict=Verdict.PENDING, confirmed=False)
    await scored(session, run, "bought.example.test")
    await scored(session, run, "skipped.example.test", verdict=Verdict.SKIPPED)


async def test_the_file_holds_every_disputed_with_its_decision(
    session: AsyncSession, disputed: None
) -> None:
    rows, total = await disputed_export.disputed(session)

    table = _table(disputed_export.to_csv(rows))

    assert total == 4
    assert table[0] == list(disputed_export.COLUMNS)
    by_domain = {line[1]: line for line in table[1:]}
    assert set(by_domain) == {
        "strong.example.test",
        "weak.example.test",
        "yes.example.test",
        "no.example.test",
    }
    assert by_domain["strong.example.test"][6] == "ждёт решения"
    assert by_domain["yes.example.test"][6] == "пишем"
    assert by_domain["no.example.test"][6] == "не пишем"
    line = by_domain["strong.example.test"]
    assert line[:6] == [
        "donor.example.test",
        "strong.example.test",
        "3",
        "rel=sponsored +5",
        "https://donor.example.test/post/strong.example.test",
        "best strong.example.test",
    ]
    # Решения нет — решившего тоже; колонки ответа специалиста пустые: их заполнит человек.
    assert line[7:] == ["", "", ""]


async def test_the_file_says_who_decided_a_person_or_the_judge(
    session: AsyncSession, disputed: None
) -> None:
    """«Решение сейчас» говорило только «пишем» или «не пишем» (12.11.2026): решение судьи от
    решения человека в файле было не отличить. Кто решил — соседней колонкой, словом, без метки
    партии; у нерешённой строки пусто — совета судьи в файле по-прежнему нет."""
    run = await crawled(session, "judged.example.test")
    by_judge = await scored(
        session, run, "catalog-card.example.test", verdict=Verdict.PENDING, confirmed=False
    )
    by_judge.decided_by = marker_of("skip-20261111-093015-a3f9")
    await session.flush()
    rows, _ = await disputed_export.disputed(session)

    table = _table(disputed_export.to_csv(rows))

    who = table[0].index("кто решил")
    assert table[0][who - 1 : who + 2] == ["решение сейчас", "кто решил", "решение специалиста"]
    said = {line[1]: (line[who - 1], line[who]) for line in table[1:]}
    assert said["catalog-card.example.test"] == ("не пишем", "судья")
    assert said["no.example.test"] == ("не пишем", "человек")
    assert said["yes.example.test"] == ("пишем", "человек")
    assert said["strong.example.test"] == ("ждёт решения", "")


async def test_the_strongest_come_first_and_the_cap_is_honest(
    session: AsyncSession, disputed: None
) -> None:
    rows, total = await disputed_export.disputed(session, limit=1)

    assert total == 4
    assert [row.points for row in rows] == [3]


async def test_text_from_foreign_pages_does_not_run_in_the_table(session: AsyncSession) -> None:
    """Анкор снят с чужой страницы: `=HYPERLINK(…)` таблица выполнила бы при открытии."""
    run = await crawled(session, "donor.example.test")
    row = await scored(session, run, "formula.example.test", verdict=Verdict.PENDING)
    row.best_anchor = '=HYPERLINK("http://evil.example.test")'
    row.reasons = ["+rule"]
    await session.flush()

    rows, _ = await disputed_export.disputed(session)
    line = _table(disputed_export.to_csv(rows))[1]

    assert line[5].startswith("'=")
    assert line[3].startswith("'+")


async def test_an_empty_list_is_a_file_with_the_header(session: AsyncSession) -> None:
    rows, total = await disputed_export.disputed(session)

    assert total == 0
    assert _table(disputed_export.to_csv(rows)) == [list(disputed_export.COLUMNS)]


async def test_the_route_gives_the_file_to_a_viewer(
    client: AsyncClient,
    make_user: MakeUser,
    sign_in: SignIn,
    session: AsyncSession,
    disputed: None,
) -> None:
    await session.commit()
    assert (await client.get("/api/advertisers/pending/export")).status_code == 403
    await make_user("спорные@site.com", role=UserRole.OPERATOR)
    token = await sign_in("спорные@site.com")

    answer = await client.get("/api/advertisers/pending/export", headers=bearer(token))

    assert answer.status_code == 200
    assert answer.headers["x-export-rows"] == answer.headers["x-export-asked"] == "4"
    assert "disputed-" in answer.headers["content-disposition"]
    assert answer.content.startswith(b"\xef\xbb\xbf")
    assert len(_table(answer.content)) == 5
