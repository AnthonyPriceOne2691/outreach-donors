"""Демонстрация на тестовой базе: заводится, показывает цены списком, убирается.

Демонстрационные данные рисуют экраны раньше живых писем (`cli/demo_data.py`),
и до 06.10.2026 их не заводил ни один тест: сломанная демонстрация видна была
только на снимке экрана. Здесь — то, ради чего в неё добавлен список цен:
под ценой ответа стоят все цены письма, и каждое число из текста письма.
И то, что на боевой базе — при настоящей почте — она не заводится (`cli/demo_guard.py`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from backend.cli import demo_data
from backend.cli.demo_content import REPLIES, reply_text
from backend.config import outreach as outreach_cfg
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import ReplyModel, SenderModel
from backend.features.replies.money import appears_in
from backend.shared import demo
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_mail_test import _rows

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


async def _seed(session: AsyncSession) -> None:
    senders = await demo_data._seed_senders(session, NOW)
    await demo_data._seed_sent_today(session, senders, NOW)
    await demo_data._seed_queue(session, NOW)
    await demo_data._seed_threads(session, NOW)
    await session.commit()


async def test_priced_demo_reply_lists_every_price_of_its_text(session: AsyncSession) -> None:
    await _seed(session)

    priced = (
        (
            await session.execute(
                select(ReplyModel)
                .where(ReplyModel.price_white.is_not(None))
                .order_by(ReplyModel.id)
            )
        )
        .scalars()
        .all()
    )

    assert priced
    for reply in priced:
        assert reply.offers
        # Та же проверка, что у разбора: числа нет в письме — выдумка.
        assert all(appears_in(Decimal(offer["price"]), reply.raw_body) for offer in reply.offers)
    both = priced[0]
    assert [(offer["product"], offer["price"], offer["period"]) for offer in both.offers or []] == [
        ("размещение статьи", "250", None),
        ("размещение статьи с пометкой «партнёрский материал»", "180", None),
        ("ссылка в опубликованной статье", "120", None),
        ("ссылка на главной", "300", "month"),
    ]
    assert len(priced[1].offers or []) == 3


async def test_reply_text_says_only_the_prices_the_donor_named(session: AsyncSession) -> None:
    """«— None EUR» в «Диалогах»: шаблон ответа «цена» держал фразу о цене
    с пометкой и у донора, который её не называл (city-news, 07.10.2026)."""
    await _seed(session)

    replies = (await session.execute(select(ReplyModel).order_by(ReplyModel.id))).scalars().all()

    assert replies
    for reply in replies:
        assert "None" not in reply.raw_body
        assert "{" not in reply.raw_body, "в тексте осталась незаполненная метка шаблона"
    white_only = [r for r in replies if r.price_white is not None and r.price_grey is None]
    both = [r for r in replies if r.price_grey is not None]
    assert white_only, "ответа без цены с пометкой нет — проверять нечего"
    assert all("пометкой" not in reply.raw_body for reply in white_only)
    assert both
    for reply in both:
        assert reply.price_grey is not None
        assert appears_in(reply.price_grey, reply.raw_body)


def test_reply_text_of_each_price_set() -> None:
    _, template = REPLIES["цена"]

    both = reply_text(template, Decimal("250"), Decimal("180"))
    white_only = reply_text(template, Decimal("400"), None)

    assert "Размещение статьи — 250 EUR, с пометкой «партнёрский материал» — 180 EUR." in both
    assert "Размещение статьи — 400 EUR. Ссылка в опубликованной статье" in white_only
    # Без цены текст не форматируется: ответ без цены шаблоном не пишется.
    assert reply_text("Прайс уточняю.", None, None) == "Прайс уточняю."


async def test_clear_removes_only_the_demo(session: AsyncSession) -> None:
    session.add(DomainModel(host="real-donor.invalid"))
    await _seed(session)

    removed = await demo_data._clear(session)

    hosts = (await session.execute(select(DomainModel.host))).scalars().all()
    assert removed > 0
    assert hosts == ["real-donor.invalid"]
    assert (await session.execute(select(ReplyModel))).scalars().all() == []


class TestLiveBase:
    """На боевой базе демонстрация не заводится (`cli/demo_guard.py`): демо-ящики доноров
    без строки лимитов встали бы в рассылку первыми, и первые письма вставали бы отказом
    платформы. Боевая — где почта настоящая; убирать выдуманное можно всегда."""

    async def test_real_mail_refuses_and_writes_nothing(
        self,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Ключ не нужен: признак настоящей почты — без сборки транспорта."""
        monkeypatch.setattr(outreach_cfg, "TRANSPORT", "sendgrid")
        monkeypatch.setattr(outreach_cfg, "SENDGRID_API_KEY", "")
        before = await _rows(session)

        code = await demo_data.run_demo_seed(session, clear=False, now=NOW)

        assert code == demo_data.EXIT_REFUSED
        assert (
            "Демонстрация не заведена: это боевая база — почта уходит по-настоящему "
            "(OUTREACH_TRANSPORT=sendgrid)" in capsys.readouterr().out
        )
        assert await _rows(session) == before

    async def test_unknown_transport_is_refused_too(
        self,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(outreach_cfg, "TRANSPORT", "carrier-pigeon")
        before = await _rows(session)

        code = await demo_data.run_demo_seed(session, clear=False, now=NOW)

        assert code == demo_data.EXIT_REFUSED
        printed = capsys.readouterr().out
        assert "Демонстрация не заведена: боевая ли это база, не понять" in printed
        assert "«carrier-pigeon» неизвестен" in printed
        assert await _rows(session) == before

    async def test_clear_works_with_real_mail(
        self,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Демонстрацию, заведённую до включения почты, иначе было бы не убрать."""
        session.add(DomainModel(host="real-donor.invalid"))
        await _seed(session)
        monkeypatch.setattr(outreach_cfg, "TRANSPORT", "sendgrid")

        code = await demo_data.run_demo_seed(session, clear=True, now=NOW)

        assert code == demo_data.EXIT_OK
        assert "Демонстрационные данные убраны" in capsys.readouterr().out
        hosts = (await session.execute(select(DomainModel.host))).scalars().all()
        assert hosts == ["real-donor.invalid"]
        assert (await session.execute(select(SenderModel))).scalars().all() == []

    async def test_null_transport_seeds_as_before(
        self,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(outreach_cfg, "TRANSPORT", "null")

        code = await demo_data.run_demo_seed(session, clear=False, now=NOW)

        assert code == demo_data.EXIT_OK
        assert "Заведено: ящиков рассылки" in capsys.readouterr().out
        senders = (await session.execute(select(SenderModel))).scalars().all()
        assert senders
        assert all(demo.is_demo(sender.domain) for sender in senders)
