"""Пороги и расход: сравнение порогов с действующими и учёт трат.

Сравнение проверяется тем же правилом, что и прогон, — здесь это не формальность:
второй экземпляр правила разошёлся бы с настоящим, и экран показывал бы перемены,
которых не будет. И одним правилом с обеих сторон: до 10.10.2026 «было» брало
сохранённый вердикт, а «станет» — одни метрики, и любая правка «возвращала» в базу
домены, отсеянные регионом (проверка прода 10.10.2026).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from backend.features.core.domain import DonorStatus, UsageProvider, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.ops import UsageRecordModel
from backend.features.donors.verdict import Thresholds
from backend.features.outreach.repository import EVERY_STAGE
from backend.features.runs.spending import SpendingRepository
from backend.features.runs.thresholds import ThresholdsRepository, consequences
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

NOW = datetime.now(UTC)

#: Действующие пороги в тестах сравнения — умолчания конфига.
IN_FORCE = Thresholds(min_dr=20, min_org_traffic=500, min_refdomains=100, min_keywords=300)


def _donor(
    *,
    status: DonorStatus,
    dr: int | None,
    traffic: int = 5000,
    refdomains: int = 500,
    keywords: int = 900,
    price: Decimal | None = None,
    dr_only: bool = False,
) -> DonorModel:
    """Домен базы. `dr_only` — отсеян по DR первой ступенью: остальных метрик прогон
    не покупал (`collect._screen_batch`), и в базе только DR."""
    measured = dr is not None and not dr_only
    return DonorModel(
        domain_id=0,
        status=status,
        dr=dr,
        org_traffic=traffic if measured else None,
        metrics={"refdomains": refdomains, "org_keywords": keywords} if measured else None,
        last_price=price,
        last_price_currency=None if price is None else "USD",
    )


def _base() -> list[DonorModel]:
    """База, на которой сохранённый вердикт и метрики расходятся: вердикт ставит
    замер — порогами своей версии и регионом, а руками заведённый донор метрик
    не имеет вовсе."""
    return [
        _donor(status=DonorStatus.SUITABLE, dr=45),
        # Метрики проходят, отсеял регион; второй — страны не получены.
        _donor(status=DonorStatus.UNSUITABLE, dr=50),
        _donor(status=DonorStatus.UNCHECKED, dr=35),
        # «Подходит» по прошлой, мягкой версии порогов: действующие его не пропускают.
        _donor(status=DonorStatus.SUITABLE, dr=12),
        _donor(status=DonorStatus.UNSUITABLE, dr=25, traffic=200),
        _donor(status=DonorStatus.UNSUITABLE, dr=15, dr_only=True),
        # Заведён руками: «годен» — слово человека, метрик нет.
        _donor(status=DonorStatus.SUITABLE, dr=None),
        _donor(status=DonorStatus.UNCHECKED, dr=None),
        # На самой границе ключей и с полученной ценой.
        _donor(status=DonorStatus.SUITABLE, dr=30, keywords=300, price=Decimal(150)),
    ]


class TestOneRuleOnBothSides:
    def test_thresholds_in_force_change_nothing(self) -> None:
        """Пороги, равные действующим, — ноль перемен: по сохранённому вердикту
        «вернулся» бы отсеянный регионом, «выпал» бы подходящий по прошлой версии."""
        result = consequences(_base(), IN_FORCE, in_force=IN_FORCE)

        assert (result.cut, result.cut_with_price, result.admitted, result.undecided) == (
            0,
            0,
            0,
            0,
        )
        assert result.passing_now == result.passing_after == 4

    @pytest.mark.parametrize(
        "stricter",
        [
            Thresholds(30, 500, 100, 300),
            Thresholds(20, 6000, 100, 300),
            Thresholds(20, 500, 600, 300),
            Thresholds(20, 500, 100, 301),
            Thresholds(90, 10_000_000, 1_000_000, 1_000_000),
        ],
    )
    def test_stricter_lets_nobody_through_beyond(self, stricter: Thresholds) -> None:
        result = consequences(_base(), stricter, in_force=IN_FORCE)

        assert (result.admitted, result.undecided) == (0, 0)
        assert result.passing_after == result.passing_now - result.cut

    @pytest.mark.parametrize(
        "softer",
        [
            Thresholds(10, 500, 100, 300),
            Thresholds(20, 100, 100, 300),
            Thresholds(20, 500, 10, 300),
            Thresholds(20, 500, 100, 299),
            Thresholds(0, 0, 0, 0),
        ],
    )
    def test_softer_cuts_nobody(self, softer: Thresholds) -> None:
        result = consequences(_base(), softer, in_force=IN_FORCE)

        assert (result.cut, result.cut_with_price) == (0, 0)
        assert result.passing_after == result.passing_now + result.admitted

    def test_one_more_keyword_cuts_only_the_one_on_the_edge(self) -> None:
        """Случай с прода: ключи 300 → 301 показывали «подходит 130, будет 135,
        вернётся 5». Строже на ключ — отсекается тот, у кого ровно 300, и только он."""
        result = consequences(_base(), Thresholds(20, 500, 100, 301), in_force=IN_FORCE)

        assert (result.cut, result.cut_with_price, result.admitted) == (1, 1, 0)
        assert (result.passing_now, result.passing_after) == (4, 3)


class TestWhoIsCounted:
    def test_rejected_for_another_reason_is_neither_cut_nor_let_through(self) -> None:
        """Отсеянный регионом проходит пороги с обеих сторон — перемены нет. До
        10.10.2026 он считался «вернётся в базу» при любой правке."""
        donors = [_donor(status=DonorStatus.UNSUITABLE, dr=50)]

        result = consequences(donors, Thresholds(40, 500, 100, 300), in_force=IN_FORCE)

        assert (result.passing_now, result.passing_after) == (1, 1)
        assert (result.cut, result.admitted) == (0, 0)

    def test_suitable_by_an_old_version_is_not_cut_again(self) -> None:
        """«Подходит» по прошлой версии, а действующие его уже не пропускают: строже —
        не «выпадет», его отсекли бы и действующие."""
        donors = [_donor(status=DonorStatus.SUITABLE, dr=12)]

        result = consequences(donors, Thresholds(25, 500, 100, 300), in_force=IN_FORCE)

        assert (result.passing_now, result.cut) == (0, 0)

    def test_lower_threshold_lets_through_those_it_cut(self) -> None:
        donors = [_donor(status=DonorStatus.UNSUITABLE, dr=25, traffic=200)]

        result = consequences(donors, Thresholds(20, 100, 100, 300), in_force=IN_FORCE)

        assert (result.admitted, result.passing_after) == (1, 1)

    def test_cut_by_dr_before_other_metrics_is_undecided(self) -> None:
        """Отсеянный по DR остальных метрик не покупал: мягче DR пустил бы его дальше,
        а пройдёт ли он там, скажет только замер — это не «пропустят»."""
        donors = [_donor(status=DonorStatus.UNSUITABLE, dr=15, dr_only=True)]

        result = consequences(donors, Thresholds(10, 500, 100, 300), in_force=IN_FORCE)

        assert (result.undecided, result.admitted, result.passing_after) == (1, 0, 0)

    def test_those_with_price_are_counted_separately(self) -> None:
        """За домен с полученной ценой заплачено не только юнитами, но и письмом.
        Это надо знать до правки порога, а не после."""
        donors = [_donor(status=DonorStatus.SUITABLE, dr=22, price=Decimal("250"))]

        result = consequences(donors, Thresholds(25, 500, 100, 300), in_force=IN_FORCE)

        assert (result.cut, result.cut_with_price) == (1, 1)

    def test_without_metrics_are_not_judged(self) -> None:
        """Домен без метрик пороги не судят — ни с какой стороны."""
        donors = [
            _donor(status=DonorStatus.UNCHECKED, dr=None),
            _donor(status=DonorStatus.SUITABLE, dr=None),
        ]

        result = consequences(donors, Thresholds(90, 10**6, 10**6, 10**6), in_force=IN_FORCE)

        assert (result.without_metrics, result.checked) == (2, 0)
        assert (result.passing_now, result.cut, result.admitted) == (0, 0, 0)

    def test_the_same_rule_as_the_run(self) -> None:
        """Порог по трафику, а не только по DR: обе стороны зовут то же
        `check_metrics`, что и прогон, поэтому знают про все четыре."""
        donors = [_donor(status=DonorStatus.SUITABLE, dr=50, traffic=300)]

        result = consequences(
            donors, Thresholds(20, 500, 100, 300), in_force=Thresholds(20, 200, 100, 300)
        )

        assert result.cut == 1


MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]


class TestPreviewRoute:
    async def test_compares_with_the_version_in_force(
        self, client: AsyncClient, session: AsyncSession, make_user: MakeUser, sign_in: SignIn
    ) -> None:
        """«Действующие» — сохранённая версия, та же, что возьмёт прогон, а не умолчания
        конфига; и её же пороги в предпросмотре — ноль перемен."""
        await make_user("пороги@site.com", role=UserRole.ADMIN)
        token = await sign_in("пороги@site.com")
        await ThresholdsRepository(session).save(
            Thresholds(40, 500, 100, 300), author="ivan@site.com"
        )
        for host, status, dr in (
            ("strong.example.test", DonorStatus.SUITABLE, 45),
            ("region.example.test", DonorStatus.UNSUITABLE, 50),
            ("weak.example.test", DonorStatus.UNSUITABLE, 30),
        ):
            domain = DomainModel(host=host)
            session.add(domain)
            await session.flush()
            session.add(
                DonorModel(
                    domain_id=domain.id,
                    status=status,
                    dr=dr,
                    org_traffic=5000,
                    metrics={"refdomains": 500, "org_keywords": 900},
                )
            )
        await session.flush()

        same = await client.post(
            "/api/settings/preview",
            json={"min_dr": 40, "min_org_traffic": 500, "min_refdomains": 100, "min_keywords": 300},
            headers=bearer(token),
        )
        softer = await client.post(
            "/api/settings/preview",
            json={"min_dr": 20, "min_org_traffic": 500, "min_refdomains": 100, "min_keywords": 300},
            headers=bearer(token),
        )

        assert same.status_code == 200, same.text
        assert {key: same.json()[key] for key in ("passing_now", "cut", "admitted")} == {
            "passing_now": 2,
            "cut": 0,
            "admitted": 0,
        }
        assert {key: softer.json()[key] for key in ("passing_after", "cut", "admitted")} == {
            "passing_after": 3,
            "cut": 0,
            "admitted": 1,
        }


class TestVersions:
    async def test_saving_creates_a_new_version(self, session: AsyncSession) -> None:
        repository = ThresholdsRepository(session)

        first = await repository.save(Thresholds(20, 500, 100, 300), author="ivan@site.com")
        second = await repository.save(Thresholds(25, 500, 100, 300), author="ivan@site.com")
        await session.commit()

        assert first.version == 1
        assert second.version == 2
        current = await repository.current()
        assert current is not None
        assert current.min_dr == 25

    async def test_other_settings_carry_over(self, session: AsyncSession) -> None:
        """Остальные настройки переносятся из текущей версии, а не берутся
        из конфига: подмешать умолчания в десятую версию значило бы молча
        откатить чужую правку."""
        repository = ThresholdsRepository(session)
        first = await repository.save(Thresholds(20, 500, 100, 300), author="ivan@site.com")
        first.units_cap = 12345
        await session.flush()

        second = await repository.save(Thresholds(25, 500, 100, 300), author="ivan@site.com")
        await session.commit()

        assert second.units_cap == 12345


class TestSpending:
    async def test_groups_by_provider_and_operation(self, session: AsyncSession) -> None:
        session.add_all(
            [
                UsageRecordModel(
                    system="outreach",
                    provider=UsageProvider.AHREFS,
                    operation="batch_metrics",
                    units=200,
                ),
                UsageRecordModel(
                    system="outreach",
                    provider=UsageProvider.AHREFS,
                    operation="batch_metrics",
                    units=300,
                ),
                UsageRecordModel(
                    system="outreach",
                    provider=UsageProvider.SERP,
                    operation="serp_task",
                    amount_usd=Decimal("0.12"),
                ),
            ]
        )
        await session.commit()

        spending = await SpendingRepository(session).since_month_start(stages=EVERY_STAGE)

        by_operation = {article.operation: article for article in spending.articles}
        assert by_operation["batch_metrics"].units == 500
        assert by_operation["batch_metrics"].calls == 2
        assert by_operation["serp_task"].amount_usd == Decimal("0.1200")

    async def test_last_month_is_not_counted(self, session: AsyncSession) -> None:
        """Лимиты провайдеров месячные, и расход считается с первого числа:
        «за последние 30 дней» отвечало бы на другой вопрос."""
        old = UsageRecordModel(
            system="outreach",
            provider=UsageProvider.AHREFS,
            operation="batch_metrics",
            units=999,
        )
        session.add(old)
        await session.flush()
        old.created_at = NOW.replace(day=1) - timedelta(days=5)
        await session.commit()

        spending = await SpendingRepository(session).since_month_start(stages=EVERY_STAGE)

        assert all(article.units != 999 for article in spending.articles)
