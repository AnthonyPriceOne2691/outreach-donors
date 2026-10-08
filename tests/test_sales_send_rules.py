"""Правила письма продаж без отправки — срез 4.6b, T2: подключение, письмо, ключ, стоп-лист.

Подключение — одно правило у отправки, добивок, сборки и экрана: отказ называет всё,
чего не хватает, сразу. Письмо — подстановки лида, блок «Отправителя» в конце и сверка
готового письма. Ключ продаж — с контактом; добивка наследует его из ключа первого письма
(правило общей почты — `tests/test_sales_stage_bridge.py`). Модуль подключён к мосту почты.
Тексты выдуманные, утверждения точные: испорченное правило краснеет.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from backend.config import outreach as outreach_cfg
from backend.config import sales as sales_cfg
from backend.features.contacts.mx import MailRoute
from backend.features.core import stages
from backend.features.core.domain import Stage, SuppressionReason, ThreadStatus
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import CampaignModel, MessageModel, ThreadModel
from backend.features.sales import cleaning, connection, hypotheses, letter, mail, sender
from backend.features.sales.chain_text import step_template
from backend.features.sales.models import LeadStatus, SalesStoplistModel
from backend.features.sales.verifier import FixtureVerifier
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w

# --- подключение -----------------------------------------------------------------------------


async def test_nothing_set_names_everything_missing_at_once(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sales_cfg, "ENABLED", False)
    monkeypatch.delenv("OUTREACH_SALES_SENDGRID_API_KEY", raising=False)
    monkeypatch.setattr(outreach_cfg, "UNSUBSCRIBE_URL", "")

    said = await connection.missing(session)

    assert said == [
        "продажи выключены: SALES_ENABLED не включён",
        "нет своей учётки почты продаж: не задан OUTREACH_SALES_SENDGRID_API_KEY — "
        "письма продаж общей учёткой не уходят",
        "нет ссылки отписки для List-Unsubscribe: не задан OUTREACH_UNSUBSCRIBE_URL",
        f"не задан физический адрес; не задана подпись; не задано имя отправителя — {sender.WHERE}",
    ]


async def test_everything_set_is_connected(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    w.connect(monkeypatch)
    await w.settings(session)

    assert await connection.missing(session) == []
    assert await mail.connected(session) is True
    assert await stages.sales_connected(session) is True


async def test_shared_key_alone_is_not_an_account_of_sales(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Без своего ключа механизм направления отдал бы общую учётку — продажам так нельзя."""
    w.connect(monkeypatch)
    monkeypatch.delenv("OUTREACH_SALES_SENDGRID_API_KEY")
    monkeypatch.setattr(outreach_cfg, "SENDGRID_API_KEY", "SG.made-up-shared-key")
    await w.settings(session)

    with pytest.raises(stages.SalesNotConnectedError) as refused:
        await connection.check(session, "Проба")

    assert str(refused.value) == (
        f"Проба: {stages.SALES_NOT_CONNECTED} — нет своей учётки почты продаж: не задан "
        "OUTREACH_SALES_SENDGRID_API_KEY — письма продаж общей учёткой не уходят"
    )


@pytest.mark.parametrize(
    ("env", "words"),
    [
        ({"OUTREACH_SALES_SENDGRID_API_KEY": "  "}, "OUTREACH_SALES_SENDGRID_API_KEY пуст"),
        ({"OUTREACH_SALES_ALLOWED_RECIPIENTS": None}, "OUTREACH_SALES_ALLOWED_RECIPIENTS не задан"),
    ],
)
async def test_own_account_half_set_is_named(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    env: dict[str, str | None],
    words: str,
) -> None:
    w.connect(monkeypatch)
    for name, value in env.items():
        if value is None:
            monkeypatch.delenv(name)
        else:
            monkeypatch.setenv(name, value)
    await w.settings(session)

    said = await connection.missing(session)

    assert len(said) == 1
    assert said[0].startswith(words)


async def test_physical_address_alone_missing_keeps_sales_off(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    w.connect(monkeypatch)
    await w.settings(session, physical_address=None)

    assert await connection.missing(session) == [f"не задан физический адрес — {sender.WHERE}"]


# --- письмо ---------------------------------------------------------------------------------


def _sender(**changes: str | None) -> sender.Sender:
    values: dict[str, str | None] = dict.fromkeys(sender.FIELDS) | {
        "sender_name": w.SENDER_NAME,
        "signature": w.SIGNATURE,
        "physical_address": w.ADDRESS,
    }
    return sender.Sender(values | changes)


def test_template_step_is_the_letter_step_plus_one() -> None:
    assert [letter.template_step(step) for step in (0, 1, 2)] == [1, 2, 3]


def test_signed_letter_ends_with_signature_then_address() -> None:
    body = letter.signed("Hello Jane,\n\nA made-up text.\n", _sender())

    assert body == f"Hello Jane,\n\nA made-up text.\n\n{w.SIGNATURE}\n\n{w.ADDRESS}"
    assert letter.problem(body, _sender()) is None


@pytest.mark.parametrize(
    ("body", "words"),
    [
        ("Hello Jane,\n\nA made-up text.", "в конце письма нет"),
        (f"Hello Jane,\n\nA made-up text.\n\n{w.SIGNATURE}", "в конце письма нет"),
        (
            f"Hello Jane,\n\nA made-up text.\n\n{w.SIGNATURE}\n\nOld Street 3, Oldtown",
            "в конце письма нет",
        ),
        (
            f"Hello Jane,\n\n{w.SIGNATURE}\n\nMade-up.\n\n{w.SIGNATURE}\n\n{w.ADDRESS}",
            "подпись из настроек отправителя стоит и в тексте письма",
        ),
        (
            f"Hello Jane,\n\nWe are at {w.ADDRESS}.\n\n{w.SIGNATURE}\n\n{w.ADDRESS}",
            "физический адрес из настроек отправителя стоит и в тексте письма",
        ),
        (
            f"Hello {{{{name}}}},\n\nMade-up.\n\n{w.SIGNATURE}\n\n{w.ADDRESS}",
            "в письме подстановка без значения: {{name}}",
        ),
        (
            f"Hello «NAME НЕ ЗАДАНО»,\n\nMade-up.\n\n{w.SIGNATURE}\n\n{w.ADDRESS}",
            "в письме подстановка без значения: «NAME НЕ ЗАДАНО»",
        ),
    ],
)
def test_ready_letter_is_checked_again(body: str, words: str) -> None:
    """Заметка 4.6a: шаблон, записанный до подписи, мог её содержать; подпись и адрес
    могли смениться после сборки; подстановка могла остаться без значения."""
    problem = letter.problem(body, _sender())

    assert problem is not None
    assert words in problem


def test_ready_letter_without_settings_is_not_whole() -> None:
    body = f"Hello Jane,\n\nMade-up.\n\n{w.SIGNATURE}"

    assert letter.problem(body, _sender(physical_address=None)) == (
        f"у отправителя не задан физический адрес — {sender.WHERE}"
    )


def test_lacking_values_are_looked_for_in_every_step() -> None:
    """Добивка с подстановкой без значения застряла бы в начатой переписке."""
    steps = [
        step_template(step=1, language="en", subject=w.SUBJECT, body=w.FIRST_BODY),
        step_template(step=3, language="en", body=w.FOLLOW_BODY[3]),
    ]
    values = {"name": "Jane", "company": "", "site": "acme.example.test"}

    assert letter.lacking(steps, values) == ["{{company}}"]
    assert letter.lacking(steps, values | {"company": "Acme"}) == []


def test_sales_key_carries_the_contact() -> None:
    """Два лида одной компании — два ключа: `{этап}:{домен}:{адрес}:{шаг}`."""
    jane = letter.key("acme.example.test", " Jane@Acme.Example.Test ", 0)
    olga = letter.key("acme.example.test", "olga@acme.example.test", 0)

    assert jane == "sales:acme.example.test:jane@acme.example.test:0"
    assert jane != olga


def test_key_width_is_the_column_width() -> None:
    assert MessageModel.__table__.c.idempotency_key.type.length == letter.KEY_LENGTH


# --- кому писать нельзя ---------------------------------------------------------------------


async def test_stoplist_of_sales_and_the_shared_one_stop_the_lead(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    hypothesis = await w.world(session, monkeypatch)
    jane = await w.lead(session, hypothesis.hypothesis_id, "jane@acme.example.test")
    olga = await w.lead(
        session, hypothesis.hypothesis_id, "olga@mail.beta.example.test", host="beta.example.test"
    )
    ivan = await w.lead(session, hypothesis.hypothesis_id, "ivan@gamma.example.test")
    petr = await w.lead(session, hypothesis.hypothesis_id, "petr@delta.example.test")
    session.add_all(
        [
            SalesStoplistModel(email="jane@acme.example.test"),
            SalesStoplistModel(host="beta.example.test"),
            SuppressionModel(
                domain_id=ivan.domain_id, reason=SuppressionReason.UNSUBSCRIBED, stage=Stage.DONORS
            ),
            SuppressionModel(
                domain_id=petr.domain_id,
                reason=SuppressionReason.UNSUBSCRIBED,
                stage=None,
                expires_at=w.NOW - timedelta(days=1),
            ),
        ]
    )
    await session.flush()

    assert await mail.stopped_by(session, jane, "acme.example.test", now=w.NOW) == (
        "в ручном стоп-листе продаж"
    )
    assert await mail.stopped_by(session, olga, "beta.example.test", now=w.NOW) == (
        "в ручном стоп-листе продаж"
    )
    assert await mail.stopped_by(session, ivan, "gamma.example.test", now=w.NOW) == (
        "стоп-лист, причина «unsubscribed»"
    )
    assert await mail.stopped_by(session, petr, "delta.example.test", now=w.NOW) is None


async def test_other_direction_row_without_a_request_does_not_stop_the_lead(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Строка доноров «не наш донор» — их решение, а не просьба человека не писать."""
    made = await w.world(session, monkeypatch)
    jane = await w.lead(session, made.hypothesis_id, "jane@acme.example.test")
    session.add(
        SuppressionModel(
            domain_id=jane.domain_id, reason=SuppressionReason.MANUAL, stage=Stage.DONORS
        )
    )
    await session.flush()

    assert await mail.stopped_by(session, jane, "acme.example.test", now=w.NOW) is None


# --- мост ------------------------------------------------------------------------------------


def test_sales_module_registers_itself_in_the_mail_bridge() -> None:
    """Пакет продаж грузится с реестром моделей — и подключает себя к мосту почты: письма
    продаж спрашивают этот модуль в API, воркере и консоли, без импорта продаж в почте."""
    assert stages._SALES.load is not None
    assert stages._SALES.load() is mail


# --- очистка: диалог продаж — не другое направление -------------------------------------------


async def _mx(_host: str, **_kwargs: object) -> MailRoute:
    return MailRoute.MX


@pytest.mark.parametrize(
    ("stage", "status"),
    [(Stage.SALES, LeadStatus.READY), (Stage.DONORS, LeadStatus.REJECTED)],
)
async def test_second_lead_of_a_company_in_a_sales_dialog_is_not_busy(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, stage: Stage, status: LeadStatus
) -> None:
    """Первому лиду компании ушло письмо — диалог продаж открыт. Второй лид той же
    компании, загруженный позже, получает своё письмо, а не «домен в работе у другого
    направления»; диалог доноров на домене по-прежнему держит."""
    monkeypatch.setattr(cleaning, "mail_route", _mx)
    hypothesis = await hypotheses.add(session, "Очистка после письма", None)
    company = await w.domain(session, "acme.example.test")
    campaign = CampaignModel(stage=stage, name="Идущая рассылка", status="running")
    session.add(campaign)
    await session.flush()
    session.add(
        ThreadModel(domain_id=company.id, campaign_id=campaign.id, status=ThreadStatus.OPEN)
    )
    second = await w.lead(session, hypothesis.id, "olga@acme.example.test", status=LeadStatus.NEW)

    await cleaning.clean(session, FixtureVerifier(), now=w.NOW)  # type: ignore[arg-type]

    await session.refresh(second)
    assert second.status is status
