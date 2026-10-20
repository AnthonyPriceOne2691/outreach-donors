"""Кандидаты из ссылок конкурентов: домены конкурентов, разбор списков и журнал.

Платных вызовов нет: списки — записанными ответами Ahrefs, Ahrefs — подменённым транспортом.
Домены — RFC 2606 (`example.com`, `.net`, `.org`) и поддомены платформ из списка суффиксов:
нормализация хоста (`donors/host.py`) знает только настоящие зоны, `.test` она отбрасывает.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from backend.features.ahrefs.client import AhrefsClient, AhrefsError
from backend.features.core.domain import DonorStatus
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.donors.verdict import Thresholds
from backend.features.runs.competitors import (
    SOURCE_KIND,
    Ask,
    DropReason,
    Fetched,
    Journal,
    candidates_of,
    collapse,
    fetch_list,
    known_donors,
    links_filter,
    refused,
    row_price,
)
from backend.features.runs.planning import Candidates
from sqlalchemy.ext.asyncio import AsyncSession

THRESHOLDS = Thresholds(min_dr=20, min_org_traffic=500, min_refdomains=100, min_keywords=300)
ASK = Ask(targets=("example.com", "example.net"), depth=100)


class TestAsk:
    def test_ask_survives_the_run_row(self) -> None:
        assert Ask.of(ASK.as_dict()) == ASK
        assert ASK.rows_max == 200

    def test_keyword_run_has_no_ask(self) -> None:
        assert Ask.of(None) is None
        assert Ask.of({}) is None

    def test_fetched_list_survives_the_run_row(self) -> None:
        fetched = Fetched("example.com", [("a.github.io", 44.0), ("", None)], 50, 14)

        assert Fetched.restored("example.com", fetched.as_dict()) == fetched


def _list(target: str, *rows: tuple[str, float | None]) -> Fetched:
    return Fetched(target, list(rows), units=50, per_row=14)


class TestCollapse:
    def test_every_row_is_a_candidate_or_a_counted_drop(self) -> None:
        """Строка не пропадает молча: из двухсот строк сто восемьдесят кандидатов — и видно,
        куда делись двадцать."""
        lists = [
            _list(
                "example.com",
                ("alpha.github.io", 45.0),
                ("www.example.org", 50.0),
                ("example.net", 70.0),  # сам конкурент — второй из списка
                ("", None),  # строка без домена
                ("beta.github.io", 15.0),  # DR ниже порога: страховка фильтра Ahrefs
            ),
            _list(
                "example.net",
                ("blog.example.org", 51.0),  # тот же сайт, что у первого конкурента
                ("gamma.github.io", 81.0),  # выше границы крупного сайта
                ("delta.github.io", None),  # DR не пришёл — судит воронка
            ),
        ]

        journal = collapse(ASK, lists, THRESHOLDS)

        assert journal.hosts == ["alpha.github.io", "example.org", "delta.github.io"]
        assert journal.rows == 8
        assert journal.dropped == {
            DropReason.COMPETITOR.value: 1,
            DropReason.UNPARSED.value: 1,
            DropReason.DR_OUTSIDE.value: 2,
            DropReason.REPEAT.value: 1,
        }
        assert journal.rows == len(journal.hosts) + sum(journal.dropped.values())

    def test_site_linking_to_several_competitors_remembers_them_all(self) -> None:
        lists = [
            _list("example.com", ("example.org", 40.0)),
            _list("example.net", ("example.org", 40.0), ("alpha.github.io", 30.0)),
        ]

        journal = collapse(ASK, lists, THRESHOLDS)

        assert journal.linked_to == {
            "example.org": ["example.com", "example.net"],
            "alpha.github.io": ["example.net"],
        }
        # Сколько нового принёс каждый список: повтор засчитан первому.
        assert journal.kept == {"example.com": 1, "example.net": 1}

    def test_subdomains_of_one_site_collapse_into_one_candidate(self) -> None:
        """Ahrefs считает `news.example.org` и `example.org` разными доменами, а пишем мы
        одному владельцу: ключ — корневой домен, как у выдачи."""
        lists = [_list("example.com", ("news.example.org", 40.0), ("example.org", 41.0))]

        journal = collapse(ASK, lists, THRESHOLDS)

        assert journal.hosts == ["example.org"]
        assert journal.dropped == {DropReason.REPEAT.value: 1}

    def test_national_name_of_a_competitor_in_a_list_is_the_competitor(self) -> None:
        """Конкурент — в ASCII-форме IDNA (`competitor_hosts`), а строка списка может прийти
        национальным именем: сравнивается одна форма."""
        ask = Ask(targets=("xn--e1afmkfd.xn--p1ai", "example.net"), depth=100)
        lists = [_list("example.net", ("пример.рф", 40.0), ("example.org", 40.0))]

        journal = collapse(ask, lists, THRESHOLDS)

        assert journal.hosts == ["example.org"]
        assert journal.dropped == {DropReason.COMPETITOR.value: 1}

    def test_accepted_donors_leave_the_list_counted(self) -> None:
        journal = collapse(
            ASK, [_list("example.com", ("example.org", 40.0), ("a.github.io", 40.0))], THRESHOLDS
        )

        journal.without({"example.org"})

        assert journal.hosts == ["a.github.io"]
        assert "example.org" not in journal.linked_to
        assert journal.dropped == {DropReason.DONOR.value: 1}

    def test_nobody_known_adds_no_zero_to_the_journal(self) -> None:
        journal = Journal(hosts=["a.github.io"], linked_to={"a.github.io": ["example.com"]})

        journal.without(set())

        assert journal.dropped == {}

    def test_every_drop_reason_has_words(self) -> None:
        assert all(reason.caption for reason in DropReason)


class TestCandidates:
    def test_candidates_carry_the_journal_and_survive_a_resumed_run(self) -> None:
        lists = [
            Fetched("example.com", [("example.org", 40.0), ("", None)], units=50, per_row=14),
            Fetched("example.net", [("example.org", 41.0)], units=0, per_row=None),
        ]
        journal = collapse(ASK, lists, THRESHOLDS)

        candidates = candidates_of(ASK, lists, journal, row_units=14)
        restored = Candidates.restored(candidates.as_dict())

        assert candidates.hosts == ["example.org"]
        assert (candidates.keywords, candidates.results, candidates.dropped) == (0, 3, 1)
        assert candidates.found_by == {}, "тема письма у ключей; конкурент — не тема"
        assert restored.source == candidates.source
        assert candidates.source["kind"] == SOURCE_KIND
        assert candidates.source["targets"] == [
            {"target": "example.com", "rows": 2, "units": 50, "per_row": 14, "kept": 1},
            {"target": "example.net", "rows": 1, "units": 0, "per_row": None, "kept": 0},
        ]
        assert candidates.source["linked_to"] == {"example.org": ["example.com", "example.net"]}
        assert candidates.source["dropped"] == {"unparsed": 1, "repeat": 1}

    def test_skipped_competitor_is_named_with_its_reason(self) -> None:
        lists = [Fetched("example.com", [("example.org", 40.0)], units=50, per_row=14)]
        journal = collapse(ASK, lists, THRESHOLDS)

        candidates = candidates_of(
            ASK, lists, journal, row_units=14, skipped={"example.net": "код 404"}
        )

        assert candidates.source["skipped"] == [{"target": "example.net", "reason": "код 404"}]
        assert [row["target"] for row in candidates.source["targets"]] == ["example.com"]

    def test_filter_and_price_come_from_the_thresholds_of_the_run(self) -> None:
        where = links_filter(THRESHOLDS)

        assert (where.min_dr, where.max_dr, where.min_traffic) == (20, 80, 500)
        assert row_price(THRESHOLDS) == 14
        no_traffic = Thresholds(min_dr=20, min_org_traffic=0, min_refdomains=0, min_keywords=0)
        assert row_price(no_traffic) == 4


class TestRefusals:
    @pytest.mark.parametrize(
        ("status", "words", "permanent"),
        [
            (401, "Ahrefs не принял ключ", True),
            (403, "нет доступа к спискам ссылающихся доменов", True),
            (402, "кончилась оплата", True),
            (400, "не принял запрос списка (код 400)", True),
            (404, "не принял запрос списка (код 404)", True),
            (422, "не принял запрос списка (код 422)", True),
            (302, "ответил кодом 302 — повтор не поможет", True),
            (405, "ответил кодом 405 — повтор не поможет", True),
            (409, "ответил кодом 409 — повтор не поможет", True),
            (410, "ответил кодом 410 — повтор не поможет", True),
            (429, "ограничил частоту", False),
            (503, "Ahrefs не ответил", False),
            (None, "не получен или не разобран", False),
        ],
    )
    def test_refusal_says_what_to_do_by_the_code_of_the_answer(
        self, status: int | None, words: str, permanent: bool
    ) -> None:
        said = AhrefsError("refdomains: ответ провайдера", permanent=permanent, status=status)

        error = refused("example.com", said)

        assert str(error).startswith("Ссылки конкурента example.com не получены: ")
        assert words in str(error)
        assert "ответ провайдера" in str(error), "текст провайдера — в отказе"
        assert error.permanent is permanent
        assert error.status == status, "код — пропуску списка (`competitor_run`)"


def _client(handler: Any) -> AhrefsClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.test")
    return AhrefsClient(api_key="k", http=http)


class TestFetchList:
    async def test_list_is_read_with_its_cost_from_the_answer(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            rows = [
                {"domain": "example.org", "domain_rating": 44.5},
                {"domain": "a.github.io", "domain_rating": {"domain_rating": 31}},
                {"domain_rating": 50},
            ]
            headers = {
                "x-api-units-cost-total-actual": "50",
                "x-api-units-cost-total": "50",
                "x-api-units-cost-row": "14",
            }
            return httpx.Response(200, json={"refdomains": rows}, headers=headers)

        fetched = await fetch_list(
            _client(handler), "example.com", depth=100, where=links_filter(THRESHOLDS)
        )

        assert fetched.rows == [("example.org", 44.5), ("a.github.io", 31.0), ("", 50.0)]
        assert (fetched.units, fetched.per_row) == (50, 14)

    async def test_refusal_of_ahrefs_becomes_named_refusal_of_the_list(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text="plan does not include site explorer")

        with pytest.raises(Exception, match="нет доступа к спискам") as caught:
            await fetch_list(
                _client(handler), "example.com", depth=100, where=links_filter(THRESHOLDS)
            )

        assert getattr(caught.value, "permanent", False)


async def test_known_donors_are_the_accepted_ones(session: AsyncSession) -> None:
    """Донор — принятый человеком (`donors/standing.py`): годный по порогам кандидат им не
    считается и из списка не выбрасывается."""
    for host, review in [("accepted.example.test", "accepted"), ("pending.example.test", None)]:
        domain = DomainModel(host=host)
        session.add(domain)
        await session.flush()
        session.add(
            DonorModel(
                domain_id=domain.id,
                status=DonorStatus.SUITABLE,
                metrics_refreshed_at=datetime.now(UTC),
                review=review,
            )
        )
    await session.flush()

    found = await known_donors(
        session, ["accepted.example.test", "pending.example.test", "unseen.example.test"]
    )

    assert found == {"accepted.example.test"}
    assert await known_donors(session, []) == set()
