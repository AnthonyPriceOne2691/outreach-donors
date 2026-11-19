"""Ключ домена — в ASCII: один сайт письменностью и в `xn--` — одна запись (19.11.2026).

На проде так разошлись записи: имя письменностью из ссылки и `xn--…` из выдачи давали двух доноров,
два кандидата, две строки памяти. Ключ строят три места — корень (`donors/host.normalize_host`), хост
ссылки обхода (`crawl/links`) и ячейка загрузки (`contacts/sweep_input.host_from_cell`); записанное
раньше переводит миграция `b7e4c2a9d1f3`. Людям имя по-прежнему показывает `shown_host`.

Домены выдуманы: «пример.рф» (`xn--e1afmkfd.xn--p1ai`) и `*.example.test`.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.features.contacts.sweep_input import host_from_cell
from backend.features.core.domain import CrawlStatus, Verdict
from backend.features.core.models.advertiser import CandidateMemoryModel, CandidateModel
from backend.features.core.models.crawl import CrawlRunModel, OutLinkModel
from backend.features.core.models.domain import DomainModel
from backend.features.crawl.links import harvest
from backend.features.donors.host import normalize_host
from backend.features.sales.models import SalesStoplistModel
from backend.shared.net.idn import ascii_host
from sqlalchemy import select
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from tests.migration_helpers import load_migration
from tests.test_crawl_links import LONG, _page

MIGRATION = "b7e4c2a9d1f3_idn_hosts_ascii.py"

#: «пример.рф» — так его хранит база.
IDN, ACE = "пример.рф", "xn--e1afmkfd.xn--p1ai"
#: Поддомен письменностью.
BLOG = "блог.пример.рф"
DONOR = "donor.example.test"


class TestKey:
    @pytest.mark.parametrize(
        "raw",
        [
            IDN,
            "ПРИМЕР.РФ",
            "https://www.блог.пример.рф/статья?x=1",
            ACE,
            f"http://www.{ACE}/",
        ],
    )
    def test_one_site_gives_one_key(self, raw: str) -> None:
        assert normalize_host(raw) == ACE

    def test_ascii_names_stay_as_they_were(self) -> None:
        assert normalize_host("https://WWW.Blog.Example.co.uk/path") == "example.co.uk"

    def test_an_upload_cell_in_script_is_stored_in_ascii(self) -> None:
        assert host_from_cell("https://Блог.Пример.рф/путь [старый]") == ascii_host(BLOG)
        assert host_from_cell("news.example.com") == "news.example.com"


class TestLinks:
    PAGE = f"https://{DONOR}/post/1"

    def _harvest(self, site_host: str, *hrefs: str) -> tuple[list[tuple[str, str]], set[str]]:
        anchors = "".join(f'<p><a href="{href}">ссылка</a></p>' for href in hrefs)
        html = _page(f"<article><p>{LONG}</p>{anchors}</article>")
        shares: set[str] = set()
        page = f"https://{site_host}/post/1"
        links = harvest(html, page, site_host, shares=shares)
        return [(link.target_host, link.target_root) for link in links], shares

    def test_a_link_written_in_script_is_stored_in_ascii(self) -> None:
        links, _ = self._harvest(DONOR, f"https://{BLOG}/статья", f"https://{ACE}/")

        assert links == [(ascii_host(BLOG), ACE), (ACE, ACE)]

    @pytest.mark.parametrize(
        ("site", "href"), [(IDN, f"https://{ACE}/о-нас"), (ACE, f"https://{BLOG}/")]
    )
    def test_a_donor_in_script_links_to_itself(self, site: str, href: str) -> None:
        """Донор и его ссылка в разных формах — тот же сайт: внешней ссылки нет."""
        links, _ = self._harvest(site, href)

        assert links == []

    def test_a_share_button_carries_the_address_in_script(self) -> None:
        """Корень донора — в ASCII, а кнопка несёт адрес статьи письменностью."""
        own = quote(f"https://{IDN}/статья", safe="")

        links, shares = self._harvest(ACE, f"https://t.me/share/url?url={own}")

        assert links == []
        assert len(shares) == 1


def _run_migration(connection: Connection) -> None:
    migration = load_migration(MIGRATION)
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        migration.upgrade()
        migration.upgrade()


class TestMigration:
    #: Донор с двойником в ASCII: сливать — решение человека, имя остаётся как было.
    TWIN = "bücher.example.test"
    #: Метка длиннее 63 знаков после перевода — не переводится.
    LONG_LABEL = "ä" * 60 + ".example.test"

    async def _stored(self, session: AsyncSession) -> None:
        session.add_all(
            [
                DomainModel(host=IDN),
                DomainModel(host=self.TWIN),
                DomainModel(host=ascii_host(self.TWIN)),
            ]
        )
        run = CrawlRunModel(host=DONOR, status=CrawlStatus.DONE)
        session.add(run)
        await session.flush()
        session.add_all(
            OutLinkModel(
                crawl_run_id=run.id,
                page_url=f"https://{DONOR}/post/1",
                url=f"https://{host}/",
                target_host=host,
                target_root=root,
            )
            for host, root in ((BLOG, IDN), (ACE, ACE), (self.LONG_LABEL, self.LONG_LABEL))
        )
        session.add(
            CandidateModel(
                crawl_run_id=run.id, donor_host=DONOR, target_root=IDN, verdict=Verdict.PENDING
            )
        )
        session.add_all(
            CandidateMemoryModel(donor_host=DONOR, target_root=root, confirmed=True)
            for root in (IDN, ACE, "bönan.example.test")
        )
        # Стоп-лист продаж сравнивается строкой: запись письменностью держала бы не того лида.
        session.add_all([SalesStoplistModel(host=IDN), SalesStoplistModel(email=f"info@{DONOR}")])
        await session.flush()

    async def test_names_in_script_become_ascii_and_twins_stay(
        self, session: AsyncSession, caplog: pytest.LogCaptureFixture
    ) -> None:
        await self._stored(session)
        connection = await session.connection()

        with caplog.at_level(logging.WARNING, logger="alembic.runtime.migration"):
            await connection.run_sync(_run_migration)

        domains = set((await session.execute(select(DomainModel.host))).scalars())
        links = set(
            (
                await session.execute(select(OutLinkModel.target_host, OutLinkModel.target_root))
            ).tuples()
        )
        roots = set((await session.execute(select(CandidateModel.target_root))).scalars())
        memory = set((await session.execute(select(CandidateMemoryModel.target_root))).scalars())
        stoplist = set((await session.execute(select(SalesStoplistModel.host))).scalars())
        assert domains == {ACE, self.TWIN, ascii_host(self.TWIN)}
        assert links == {(ascii_host(BLOG), ACE), (ACE, ACE), (self.LONG_LABEL, self.LONG_LABEL)}
        assert roots == {ACE}
        # Пара (донор, «пример.рф») упёрлась в ту же пару в ASCII — память не сливается.
        assert memory == {IDN, ACE, ascii_host("bönan.example.test")}
        assert stoplist == {ACE, None}
        said = caplog.text
        assert f"domains.host «{self.TWIN}» остался как был" in said
        assert f"candidate_memory.target_root «{IDN}» остался как был" in said
        assert f"outlinks.target_host «{self.LONG_LABEL}» не переводится" in said

    @pytest.mark.parametrize(
        "host",
        [IDN, BLOG, ACE, "ПРИМЕР.РФ", "bücher.example.test", "straße.example.test", "ดูบอลสด99.example.test",
         "под_домен.пример.рф", "пример..рф", "пример.рф.", "bad char.пример.рф", "ä" * 60 + ".example.test"],
    )  # fmt: skip
    def test_the_copy_of_the_rule_says_the_same(self, host: str) -> None:
        """Копия в миграции — намеренно (шапка ревизии), и сегодня она обязана совпадать."""
        assert (load_migration(MIGRATION)._ascii(host) or host) == ascii_host(host)
