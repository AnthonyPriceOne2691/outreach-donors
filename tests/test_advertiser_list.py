"""Рекламодатели списком и файлом (сверка ТЗ 11.10.2026: выгрузка была только у лидов).

Проверяется то, чем список может соврать: кого он называет рекламодателем, «адрес
найден» по строке в базе, а не по исходу поиска, слово оффера — по письмам Этапа 2
и по старшинству, фильтр и выгрузка отвечают на один вопрос, а текст с чужой страницы
в файле не исполняется.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Awaitable, Callable

import pytest
from backend.features.core.domain import (
    ContactStatus,
    MessageStatus,
    ReplyKind,
    Stage,
    UserRole,
    Verdict,
)
from backend.features.core.models.access import UserModel
from backend.features.crawl import advertiser_list
from backend.features.crawl.advertiser_export import to_csv
from backend.features.crawl.advertiser_list import AddressState, AdvertiserFilters, AdvertiserRow
from backend.features.crawl.offer_state import OfferState
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.stage2_rows import addressed, answered, crawled, lettered, promoted, scored

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]


@pytest.fixture
async def token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("список@site.com", role=UserRole.OPERATOR)
    return await sign_in("список@site.com")


async def _states(session: AsyncSession) -> dict[str, tuple[AddressState, OfferState]]:
    found = await advertiser_list.page(session, AdvertiserFilters(limit=100))
    return {row.host: (row.address, row.letter) for row in found.rows}


class TestWhoIsListed:
    async def test_niche_business_only_after_write(self, session: AsyncSession) -> None:
        """Рекламодатель по ссылке — всякий; бизнес ниши — после «пишем»: без решения он
        ждёт в своей очереди, а «не пишем» — не рекламодатель."""
        await promoted(session, "link.example.test")
        await promoted(session, "yes.example.test", source="niche", confirmed=True, decided=True)
        await promoted(session, "waits.example.test", source="niche")
        await promoted(session, "no.example.test", source="niche", decided=True)

        found = await advertiser_list.page(session, AdvertiserFilters())

        assert {row.host for row in found.rows} == {"link.example.test", "yes.example.test"}
        assert found.total == 2
        niche = next(row for row in found.rows if row.source == "niche")
        # У бизнеса ниши «донор» — пример площадки для письма, а не место находки.
        assert niche.donor_host is None

    async def test_strongest_first_and_pages_meet(self, session: AsyncSession) -> None:
        """Балл вниз, при равном — по номеру: строка со стыка страниц не теряется
        и не повторяется."""
        for number in range(5):
            await promoted(session, f"same-{number}.example.test", points=4)
        await promoted(session, "strong.example.test", points=9)

        first = await advertiser_list.page(session, AdvertiserFilters(limit=3))
        second = await advertiser_list.page(session, AdvertiserFilters(limit=3, offset=3))

        hosts = [row.host for row in first.rows + second.rows]
        assert hosts[0] == "strong.example.test"
        assert sorted(hosts[1:]) == [f"same-{n}.example.test" for n in range(5)]
        assert len(set(hosts)) == 6
        assert first.total == second.total == 6


class TestAddress:
    async def test_address_is_a_row_not_the_search_outcome(self, session: AsyncSession) -> None:
        """«Адреса нет» рядом с адресом, оставшимся от прежнего прохода, — неправда:
        адрес — строка в базе. Без адреса форма — работа рук, а не пустота."""
        await promoted(session, "kept.example.test", contact_status=ContactStatus.NOT_FOUND)
        await addressed(session, "kept.example.test")
        await promoted(session, "form.example.test", contact_status=ContactStatus.FORM_ONLY)
        await promoted(session, "never.example.test")

        states = await _states(session)

        assert {host: address for host, (address, _) in states.items()} == {
            "kept.example.test": AddressState.FOUND,
            "form.example.test": AddressState.FORM,
            "never.example.test": AddressState.NONE,
        }


class TestOfferWord:
    async def test_each_outcome_by_its_letters(self, session: AsyncSession) -> None:
        for host in ("none", "queued", "sent", "bounced", "replied", "unsub", "stopped"):
            await promoted(session, f"{host}.example.test")
        await lettered(session, "queued.example.test", MessageStatus.QUEUED)
        await lettered(session, "sent.example.test", MessageStatus.DELIVERED)
        await lettered(session, "bounced.example.test", MessageStatus.BOUNCED)
        replied = await lettered(session, "replied.example.test", MessageStatus.SENT)
        await answered(session, replied)
        unsub = await lettered(session, "unsub.example.test", MessageStatus.SENT)
        await answered(session, unsub, ReplyKind.UNSUBSCRIBE)
        await lettered(session, "stopped.example.test", MessageStatus.STOPPED)

        states = await _states(session)

        assert {host.split(".")[0]: letter for host, (_, letter) in states.items()} == {
            "none": OfferState.NONE,
            "queued": OfferState.QUEUED,
            "sent": OfferState.SENT,
            "bounced": OfferState.BOUNCED,
            "replied": OfferState.REPLIED,
            "unsub": OfferState.UNSUBSCRIBED,
            "stopped": OfferState.STOPPED,
        }

    async def test_the_new_attempt_after_a_bounce_is_queued(self, session: AsyncSession) -> None:
        """Отказ доставки открывает следующий адрес: собранное снова письмо — «собрано»,
        а не «не дошло»; добивка в очереди после ушедшего первого — «ушло»."""
        await promoted(session, "retry.example.test")
        await promoted(session, "chain.example.test")
        await lettered(session, "retry.example.test", MessageStatus.BOUNCED)
        await addressed(session, "retry.example.test", "next@retry.example.test")
        await lettered(session, "retry.example.test", MessageStatus.QUEUED, attempt=2)
        await lettered(session, "chain.example.test", MessageStatus.SENT)
        await lettered(session, "chain.example.test", MessageStatus.QUEUED, step=1)

        states = await _states(session)

        assert states["retry.example.test"][1] is OfferState.QUEUED
        assert states["chain.example.test"][1] is OfferState.SENT

    async def test_a_donor_letter_on_the_same_domain_is_another_talk(
        self, session: AsyncSession
    ) -> None:
        """Письмо донору на том же домене — не оффер: слово считается по письмам Этапа 2."""
        await promoted(session, "both.example.test")
        letter = await lettered(
            session, "both.example.test", MessageStatus.SENT, stage=Stage.DONORS
        )
        await answered(session, letter)

        states = await _states(session)

        assert states["both.example.test"][1] is OfferState.NONE


class TestReasons:
    async def test_the_reasons_are_those_of_the_link_the_letter_is_for(
        self, session: AsyncSession
    ) -> None:
        """ТЗ: «найденные рекламодатели со скорингом и причиной» — причины кандидата
        лучшей ссылки. Пересчитали веса, а перевод не нажимали — новый балл рядом,
        а не вместо того, с которым перевели."""
        await promoted(session, "kept.example.test", donor_host="one.example.test", points=5)
        await promoted(session, "moved.example.test", donor_host="one.example.test", points=5)
        await promoted(session, "lost.example.test", donor_host="one.example.test", points=4)
        one = await crawled(session, "one.example.test")
        await scored(session, one, "kept.example.test", points=5)
        moved = await scored(session, one, "moved.example.test", points=3)
        moved.reasons = ["коммерческий анкор +1", "ссылки с 3 страниц донора +2"]
        await session.flush()

        rows = {
            row.host: row for row in (await advertiser_list.page(session, AdvertiserFilters())).rows
        }

        assert (rows["kept.example.test"].reasons, rows["kept.example.test"].rescored) == (
            ["rel=sponsored +5"],
            None,
        )
        assert rows["moved.example.test"].reasons == [
            "коммерческий анкор +1",
            "ссылки с 3 страниц донора +2",
        ]
        assert (rows["moved.example.test"].points, rows["moved.example.test"].rescored) == (5, 3)
        # Ссылки нет в последнем обходе донора — причин нет, а не чужие.
        assert (rows["lost.example.test"].reasons, rows["lost.example.test"].rescored) == ([], None)


class TestFilters:
    async def test_filters_narrow_and_facets_count_everything(self, session: AsyncSession) -> None:
        await promoted(session, "alpha.example.test", donor_host="one.example.test")
        # На альфу ссылаются два донора, лучшая ссылка — у первого; на бету — один.
        one, three = (
            await crawled(session, "one.example.test"),
            await crawled(session, "three.example.test"),
        )
        await scored(session, one, "alpha.example.test")
        await scored(session, three, "alpha.example.test", verdict=Verdict.PENDING, confirmed=True)
        await scored(session, await crawled(session, "two.example.test"), "beta.example.test")
        # «Не пишем» человека — донор на рекламодателя не ссылается: его отклонили.
        await scored(session, three, "beta.example.test", confirmed=False)
        await addressed(session, "alpha.example.test")
        await lettered(session, "alpha.example.test", MessageStatus.SENT)
        await promoted(session, "beta.example.test", donor_host="two.example.test")
        await promoted(
            session,
            "gamma.example.test",
            donor_host="one.example.test",
            source="niche",
            confirmed=True,
            decided=True,
            contact_status=ContactStatus.FORM_ONLY,
        )

        async def hosts(**narrowed: object) -> list[str]:
            found = await advertiser_list.page(session, AdvertiserFilters(**narrowed))
            return sorted(row.host for row in found.rows)

        assert await hosts(search="ALP") == ["alpha.example.test"]
        # Донор — любой, кто ссылается, как в приёмке; бизнес ниши с тем же примером
        # площадки — не его: ссылки у бизнеса нет.
        assert await hosts(donor="one.example.test") == ["alpha.example.test"]
        assert await hosts(donor="THREE.example.test") == ["alpha.example.test"]
        assert await hosts(source="niche") == ["gamma.example.test"]
        assert await hosts(address=AddressState.FORM) == ["gamma.example.test"]
        assert await hosts(letter=OfferState.SENT) == ["alpha.example.test"]
        assert await hosts(letter=OfferState.NONE, address=AddressState.NONE) == [
            "beta.example.test"
        ]

        facets = await advertiser_list.facets(session)

        assert facets.sources == {"links": 2, "niche": 1}
        assert facets.donors == {
            "one.example.test": 1,
            "two.example.test": 1,
            "three.example.test": 1,
        }
        assert facets.addresses == {
            AddressState.FOUND: 1,
            AddressState.FORM: 1,
            AddressState.NONE: 1,
        }
        assert facets.letters == {OfferState.SENT: 1, OfferState.NONE: 2}


class TestApi:
    async def test_page_facets_and_the_server_size(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        for number in range(3):
            await promoted(session, f"row-{number}.example.test", points=number)
        await session.commit()

        response = await client.get("/api/advertisers/list?page=2&limit=2", headers=bearer(token))

        body = response.json()
        assert response.status_code == 200, body
        assert (body["page"], body["limit"], body["total"]) == (2, 2, 3)
        assert [row["host"] for row in body["rows"]] == ["row-0.example.test"]
        assert body["rows"][0]["letter"] == "none"
        assert body["rows"][0]["address"] == "none"
        assert body["rows"][0]["reasons"] == []
        assert body["export_limit"] == 10_000
        assert "facets" not in body

        facets = (await client.get("/api/advertisers/list/facets", headers=bearer(token))).json()

        assert facets["sources"] == {"links": 3}
        assert facets["letters"] == {"none": 3}

    async def test_a_page_past_the_end_still_says_how_many(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        """Число строк — окном запроса страницы; за концом окну считать нечего, и число
        приходит своим запросом, а не нулём."""
        await promoted(session, "only.example.test")
        await session.commit()

        body = (await client.get("/api/advertisers/list?page=5", headers=bearer(token))).json()

        assert (body["rows"], body["total"]) == ([], 1)

    @pytest.mark.parametrize(
        "query", ["page=0", "limit=0", "limit=101", "letter=gone", "address=maybe", "source=x"]
    )
    async def test_nonsense_is_refused(self, client: AsyncClient, token: str, query: str) -> None:
        response = await client.get(f"/api/advertisers/list?{query}", headers=bearer(token))

        assert response.status_code == 422

    async def test_only_with_the_right_to_look(
        self, client: AsyncClient, make_user: MakeUser, sign_in: SignIn
    ) -> None:
        await make_user("без-просмотра@site.com", permissions={"view": False})
        blind = await sign_in("без-просмотра@site.com")

        for path in (
            "/api/advertisers/list",
            "/api/advertisers/list/export",
            "/api/advertisers/list/facets",
        ):
            assert (await client.get(path, headers=bearer(blind))).status_code == 403
            assert (await client.get(path)).status_code in (401, 403)


class TestExport:
    async def test_file_is_the_filtered_list_in_screen_words(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        await promoted(session, "alpha.example.test", donor_host="one.example.test")
        alpha = await scored(
            session, await crawled(session, "one.example.test"), "alpha.example.test"
        )
        alpha.reasons = ["rel=sponsored +5", "коммерческий анкор +1"]
        await addressed(session, "alpha.example.test")
        await lettered(session, "alpha.example.test", MessageStatus.QUEUED)
        await promoted(session, "beta.example.test", donor_host="two.example.test")
        await promoted(session, "gamma.example.test", source="niche", confirmed=True, decided=True)
        await session.commit()

        response = await client.get(
            "/api/advertisers/list/export?source=links", headers=bearer(token)
        )

        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/csv")
        assert response.headers["x-export-rows"] == response.headers["x-export-asked"] == "2"
        assert response.content.startswith("﻿".encode())
        lines = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig")), delimiter=";"))
        assert lines[0] == [
            "рекламодатель",
            "откуда",
            "донор",
            "страница",
            "анкор",
            "балл",
            "за что",
            "адрес",
            "письмо",
        ]
        assert lines[1] == [
            "alpha.example.test",
            "ссылка на доноре",
            "one.example.test",
            "https://one.example.test/post/alpha.example.test",
            "best alpha.example.test",
            "5",
            "rel=sponsored +5; коммерческий анкор +1",
            "адрес найден",
            "собрано",
        ]
        assert lines[2][0] == "beta.example.test"
        assert lines[2][6:] == ["", "не искали", "не собрано"]

    async def test_a_file_cut_by_the_limit_says_so_in_headers(
        self,
        client: AsyncClient,
        token: str,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Найдено больше, чем ляжет в файл: заголовки говорят обе цифры — экран
        называет их словами, а не отдаёт файл, где молча не хватает строк."""
        monkeypatch.setattr("backend.api.advertisers.reports.EXPORT_LIMIT", 1)
        await promoted(session, "first.example.test", points=9)
        await promoted(session, "second.example.test", points=1)
        await session.commit()

        response = await client.get("/api/advertisers/list/export", headers=bearer(token))

        assert (response.headers["x-export-rows"], response.headers["x-export-asked"]) == ("1", "2")
        lines = response.content.decode("utf-8-sig").splitlines()
        assert len(lines) == 2
        assert lines[1].startswith("first.example.test;")

    async def test_text_from_a_foreign_page_does_not_run_in_a_spreadsheet(
        self, session: AsyncSession
    ) -> None:
        """Анкор снят с чужой страницы: `=HYPERLINK(…)` таблица выполнила бы при открытии."""
        row = await promoted(session, "evil.example.test")
        row.best_anchor = '=HYPERLINK("https://evil.example.test","click")'
        await promoted(session, "niche.example.test", source="niche", confirmed=True, decided=True)
        await session.flush()
        found = await advertiser_list.page(session, AdvertiserFilters())

        body = to_csv(found.rows).decode("utf-8-sig")

        lines = list(csv.reader(io.StringIO(body), delimiter=";"))
        evil = next(line for line in lines if line[0] == "evil.example.test")
        assert evil[4] == '\'=HYPERLINK("https://evil.example.test","click")'
        niche = next(line for line in lines if line[0] == "niche.example.test")
        # У бизнеса ниши балла нет — пусто, а не «0»; откуда — словами, донора нет.
        assert niche[1:7] == ["бизнес ниши", "", "", "", "", ""]

    @pytest.mark.parametrize("sign", ["=", "+", "-", "@", "\t", "\r", "\n"])
    def test_every_column_from_outside_is_inert(self, sign: str) -> None:
        """Адрес страницы и домен тоже приходят снаружи: ячейка, начатая знаком
        формулы, — с апострофом, в какой бы колонке она ни стояла."""
        row = AdvertiserRow(
            id=1,
            host=f"{sign}cmd.example.test",
            source="links",
            run_id=None,
            donor_host=f"{sign}donor.example.test",
            page_url=f'{sign}HYPERLINK("https://x.example.test")',
            anchor="plain",
            points=5,
            reasons=[f"{sign}1+1"],
            rescored=None,
            donors=1,
            contact_status=None,
            address=AddressState.NONE,
            letter=OfferState.NONE,
        )

        [line] = list(csv.reader(io.StringIO(to_csv([row]).decode("utf-8-sig")), delimiter=";"))[1:]

        assert line[0] == f"'{sign}cmd.example.test"
        assert line[2] == f"'{sign}donor.example.test"
        assert line[3] == f'\'{sign}HYPERLINK("https://x.example.test")'
        assert line[6] == f"'{sign}1+1"
