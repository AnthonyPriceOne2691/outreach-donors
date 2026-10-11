"""Почтовый адрес отправителя — строкой подписи каждого письма доноров (решение Anthony 11.10.2026).

Шаблоны кода, разбор подписи, письмо, каким его получит адресат, и ревизия `5b1e7c9a3d20`:
она дописывает строку адреса в утверждённые тексты рассылок, созданных раньше.
"""

from __future__ import annotations

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import Stage
from backend.features.core.models.outreach import CampaignModel
from backend.features.crawl.niche import LINKS, NICHE
from backend.features.letters import compose, template
from backend.features.letters.template import TemplateError, Zone
from sqlalchemy import select
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import FILLED
from tests.migration_helpers import load_migration

#: Ревизия, дописывающая адрес в тексты рассылок.
REVISION = "5b1e7c9a3d20_donor_letters_postal_address.py"

#: Адрес из заполненных настроек тестов (`conftest.filled_legal`).
ADDRESS = FILLED["OUTREACH_POSTAL_ADDRESS"]

#: Этап и аудитория каждого вида писем доноров: первое письмо и две добивки у каждого.
KINDS = [(Stage.DONORS, LINKS), (Stage.ADVERTISERS, LINKS), (Stage.ADVERTISERS, NICHE)]

#: Текст рассылки, утверждённый до 11.10.2026: подпись без адреса.
OLD = (
    "subject: Guest article on {{host}}\n\n"
    "[greeting] rewrite\nHi there,\n\n"
    "[opening] rewrite\nI came across {{host}} and liked what I read there.\n\n"
    "[offer] fixed\nI work with an agency that places articles for clients.\n\n"
    "[ask] rewrite\nCould you tell me what a guest article on your site costs, and is the "
    "link dofollow?\n\n"
    "[terms] fixed\nWe pay promptly.\n\n"
    "[signature] fixed\nBest regards,\n{{sender_name}}\n"
)


def _signatures() -> list[tuple[str, Zone]]:
    """Подпись каждого шаблона доноров: первые письма и добивки всех видов."""
    found: list[tuple[str, Zone]] = []
    for stage, audience in KINDS:
        found.append(
            (f"{stage.value}/{audience}", template.for_stage(stage, audience).zone("signature"))
        )
        for step in (1, 2):
            letter = template.followup(step, stage, audience)
            found.append((f"{stage.value}/{audience}/добивка {step}", letter.zone("signature")))
    return found


@pytest.mark.parametrize(("kind", "signature"), _signatures(), ids=[k for k, _ in _signatures()])
def test_every_letter_signs_with_the_address_under_the_name(kind: str, signature: Zone) -> None:
    """Первое письмо, оффер и добивки — у каждого адрес строкой под именем отправителя."""
    lines = signature.text.splitlines()

    assert lines[-2:] == ["{{sender_name}}", "{{postal_address}}"], kind


def test_letter_carries_the_configured_address(filled_legal: None) -> None:
    """Письмо адресату: под именем — адрес из `OUTREACH_POSTAL_ADDRESS`."""
    rendered = compose.render(template.default(), compose.values_for(host="site.example.test"))
    letter = compose.assemble(rendered, {})

    assert letter.body.endswith(f"{FILLED['OUTREACH_SENDER_NAME']}\n{ADDRESS}")
    assert compose.unset_in(letter.body) == []


def test_empty_address_holds_sending(monkeypatch: pytest.MonkeyPatch, filled_legal: None) -> None:
    """Пустой адрес держит отправку, как пустое имя: экран называет настройку до нажатия,
    а в письме — громкая метка, с которой отправка откажет."""
    monkeypatch.setattr(outreach_cfg, "POSTAL_ADDRESS", "")

    rendered = compose.render(template.default(), compose.values_for(host="site.example.test"))

    assert compose.missing_settings() == ["OUTREACH_POSTAL_ADDRESS"]
    assert compose.unset_in(compose.assemble(rendered, {}).body) == ["ФИЗИЧЕСКИЙ АДРЕС НЕ ЗАДАН"]


def test_text_approved_before_is_refused_without_the_revision() -> None:
    """Подпись без адреса разбор теперь не принимает — поэтому ревизия и дописывает его
    в тексты рассылок: иначе их сборка встала бы этим отказом."""
    with pytest.raises(TemplateError, match="postal_address"):
        template.of_campaign(Stage.DONORS, OLD)


class TestWithAddress:
    """Правка текста рассылки ревизией — одна строка адреса в конце подписи."""

    def test_address_goes_at_the_end_of_the_signature(self) -> None:
        changed = load_migration(REVISION).with_address(OLD)

        assert changed == OLD.replace("{{sender_name}}\n", "{{sender_name}}\n{{postal_address}}\n")
        assert (
            template.of_campaign(Stage.DONORS, changed)
            .zone("signature")
            .text.endswith("{{postal_address}}")
        )

    def test_text_with_the_address_stays_as_it_is(self) -> None:
        migration = load_migration(REVISION)
        once = migration.with_address(OLD)

        assert migration.with_address(once) == once

    def test_text_without_a_signature_stays_as_it_is(self) -> None:
        text = "subject: Hi\n\n[greeting] rewrite\nHi there,\n"

        assert load_migration(REVISION).with_address(text) == text

    def test_signature_in_the_middle_takes_the_address_before_the_next_zone(self) -> None:
        """Порядок зон задаёт текст: подпись не последней — адрес до заголовка следующей,
        пустая строка между зонами остаётся."""
        text = "[signature] fixed\nBest regards,\n{{sender_name}}\n\n[terms] fixed\nWe pay.\n"

        assert load_migration(REVISION).with_address(text) == (
            "[signature] fixed\nBest regards,\n{{sender_name}}\n{{postal_address}}\n\n"
            "[terms] fixed\nWe pay.\n"
        )


def _upgrade(connection: Connection) -> None:
    migration = load_migration(REVISION)
    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()


async def test_revision_signs_the_approved_texts_and_only_them(session: AsyncSession) -> None:
    """Ревизия на базе: текст без адреса получает строку, рассылка без своего текста
    (шаблон кода) и текст с адресом не меняются; повтор ничего не меняет."""
    signed = OLD.replace("{{sender_name}}\n", "{{sender_name}}\n{{postal_address}}\n")
    old = CampaignModel(stage=Stage.DONORS, name="old", letter_template=OLD)
    default = CampaignModel(stage=Stage.DONORS, name="default", letter_template=None)
    fresh = CampaignModel(stage=Stage.ADVERTISERS, name="fresh", letter_template=signed)
    session.add_all([old, default, fresh])
    await session.flush()

    for _ in range(2):
        await (await session.connection()).run_sync(_upgrade)

    stored = dict(
        (await session.execute(select(CampaignModel.name, CampaignModel.letter_template))).all()
    )
    assert stored == {"old": signed, "default": None, "fresh": signed}
