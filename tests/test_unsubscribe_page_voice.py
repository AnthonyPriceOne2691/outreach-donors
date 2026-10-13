"""Страница отписки письма продаж: подпись «Отправителя» продаж и язык письма.

ТЗ: физический адрес — «одна строка внизу письма и на странице отписки»; гипотеза СНГ — «с
русской воронкой». Этап — по метке письма: у письма продаж метка несёт номер письма
(`letters/unsubscribe.letter_url`), номер → письмо → рассылка → этап. Письмо доноров и
неизвестная метка — страница как была: английский, имя и адрес из `OUTREACH_*`. Подтверждение
кнопкой и One-Click — прежние тесты (`tests/test_api_unsubscribe.py`); здесь — что нажатие на
странице продаж пишет то же и отвечает на языке письма. База и модуль продаж настоящие.
"""

from __future__ import annotations

import logging

import pytest
from backend.api.unsubscribe import routes as unsubscribe_routes
from backend.api.unsubscribe import signer
from backend.config import outreach as outreach_cfg
from backend.features.core import stages
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import CampaignModel, MessageModel
from backend.features.letters import unsubscribe
from backend.features.letters.sending import Sending
from backend.features.letters.unsubscribe import Label
from backend.features.sales.models import SETTINGS_ROW, SalesSettingsModel, SalesThreadModel
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import FILLED, make_donor
from tests.test_sales_handoff_rows import sales_dialog
from tests.test_sales_stage_bridge import FakeSalesMail, _seen, _transports
from tests.test_sales_stage_mail import NOW, sales_world

SECRET = "v" * 32
CLIENT = "127.0.0.1"
#: Подпись доноров — из окружения (`OUTREACH_*`).
OURS_NAME = "Anna Ro"
OURS_ADDRESS = "1 Main Street, Dublin"
#: «Отправитель» продаж — строка в базе, адрес столбиком, как его пишут на экране.
SALES_NAME = "Мария Примерова"
SALES_ADDRESS = "Made-up Agency Ltd\n17 Example Lane\nTestville 4170"
SALES_FOOTER = (
    f"<footer>{SALES_NAME} · Made-up Agency Ltd, 17 Example Lane, Testville 4170</footer>"
)
DONORS_FOOTER = f"<footer>{OURS_NAME} · {OURS_ADDRESS}</footer>"


@pytest.fixture(autouse=True)
def page_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", SECRET)
    monkeypatch.setattr(outreach_cfg, "SENDER_NAME", OURS_NAME)
    monkeypatch.setattr(outreach_cfg, "POSTAL_ADDRESS", OURS_ADDRESS)
    # Счётчик частоты — в памяти процесса и общий на все тесты (`tests/test_api_unsubscribe.py`).
    unsubscribe_routes._throttle.clear(CLIENT)


async def _sales_letter(session: AsyncSession, *, language: str = "ru") -> tuple[MessageModel, str]:
    """Письмо продаж лиду с диалогом на языке `language` и «Отправитель» продаж в базе."""
    dialog = await sales_dialog(session)
    session.add_all(
        [
            SalesThreadModel(
                thread_id=dialog.thread.id,
                lead_id=dialog.lead.id,
                language=language,
                chain_version="chain-made-up-41",
            ),
            SalesSettingsModel(
                id=SETTINGS_ROW, sender_name=SALES_NAME, physical_address=SALES_ADDRESS
            ),
        ]
    )
    await session.commit()
    return dialog.message, dialog.lead.email


def _link(domain_id: int, letter_id: int | None = None) -> str:
    return "/api/unsubscribe/" + unsubscribe.label_for(
        domain_id, letter_id=letter_id, secret=SECRET
    )


async def _donor_letter(session: AsyncSession, domain_id: int) -> MessageModel:
    """Письмо доноров тому же сайту."""
    campaign = CampaignModel(stage=Stage.DONORS, name="Доноры", status="running")
    session.add(campaign)
    await session.flush()
    letter = MessageModel(
        campaign_id=campaign.id,
        domain_id=domain_id,
        step=0,
        status=MessageStatus.SENT,
        subject="Hi",
        body="Hi",
        idempotency_key="donors:acme.example.test:0",
    )
    session.add(letter)
    await session.commit()
    return letter


class TestLabel:
    def test_the_letter_survives_the_round_trip(self) -> None:
        label = unsubscribe.label_for(417, letter_id=9031, secret=SECRET)

        assert label.startswith("u417m9031.")
        assert unsubscribe.parse(label, secret=SECRET) == Label(417, 9031)
        assert unsubscribe.domain_id_from(label, secret=SECRET) == 417

    def test_a_donor_label_is_read_as_before(self) -> None:
        """Ссылки, ушедшие до номера письма, отписывают как отписывали."""
        label = unsubscribe.label_for(417, secret=SECRET)

        assert unsubscribe.parse(label, secret=SECRET) == Label(417, None)

    @pytest.mark.parametrize(("old", "new"), [("m9031.", "m9032."), ("u417m", "u418m")])
    def test_a_label_moved_to_another_letter_or_site_does_not_fit(self, old: str, new: str) -> None:
        label = unsubscribe.label_for(417, letter_id=9031, secret=SECRET)

        assert unsubscribe.parse(label.replace(old, new), secret=SECRET) is None

    def test_dropping_the_letter_does_not_make_a_donor_label(self) -> None:
        """Подпись закрывает оба номера: номер письма не отрезать."""
        label = unsubscribe.label_for(417, letter_id=9031, secret=SECRET)
        signature = label.rsplit(".", 1)[1]

        assert unsubscribe.parse(f"u417.{signature}", secret=SECRET) is None

    @pytest.mark.parametrize("label", ["u417m.abcdef0123", "u417m9031", "m9031.abcdef0123"])
    def test_nonsense_is_not_a_label(self, label: str) -> None:
        assert unsubscribe.parse(label, secret=SECRET) is None


class TestSalesPage:
    async def test_the_page_is_signed_by_the_sales_sender_in_russian(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        letter, lead = await _sales_letter(session)

        response = await client.get(_link(letter.domain_id, letter.id))

        assert response.status_code == 200
        page = response.text
        assert SALES_FOOTER in page
        assert OURS_ADDRESS not in page
        assert OURS_NAME not in page
        assert '<html lang="ru">' in page
        # Письмо продаж ушло человеку: страница говорит о его адресе, а не о сайте
        # (решение владельца 12.10.2026) — «все адреса сайта» она не обещает.
        assert "Больше не присылать вам письма?" in page
        assert "вашего адреса" in page
        assert "адреса этого сайта" not in page
        assert '<button type="submit">Отписаться</button>' in page
        assert "Unsubscribe" not in page
        # Чужого письма метка не раскрывает: адреса получателя на странице нет.
        assert lead not in page

    async def test_pressing_unsubscribes_the_person_and_answers_in_russian(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """Нажатие на письме продаж закрывает адрес лида без этапа, а не сайт (решение
        владельца 12.10.2026): продажи не подключены, и адрес называет модуль продаж по
        адресам сайта (`optout._person`)."""
        letter, lead = await _sales_letter(session)

        response = await client.post(_link(letter.domain_id, letter.id))

        assert response.status_code == 200
        assert "Вы отписаны" in response.text
        assert "Мы больше не будем вам писать." in response.text
        assert SALES_FOOTER in response.text
        rows = (await session.execute(select(SuppressionModel))).scalars().all()
        assert [(row.domain_id, row.email, row.stage, row.created_by) for row in rows] == [
            (None, lead, None, unsubscribe_routes.SOURCE)
        ]

    async def test_an_english_lead_reads_english_signed_by_the_sales_sender(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        letter, _ = await _sales_letter(session, language="en")

        response = await client.get(_link(letter.domain_id, letter.id))

        assert '<html lang="en">' in response.text
        assert '<button type="submit">Unsubscribe</button>' in response.text
        assert SALES_FOOTER in response.text
        assert OURS_ADDRESS not in response.text

    async def test_the_signature_is_escaped(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        letter, _ = await _sales_letter(session)
        settings = await session.get(SalesSettingsModel, SETTINGS_ROW)
        assert settings is not None
        settings.sender_name = '<script>alert("x")</script>'
        settings.physical_address = "Lane <b>7</b> & Sons"
        await session.commit()
        await session.refresh(settings)  # время правки ставит база — сессия у теста и страницы одна

        response = await client.get(_link(letter.domain_id, letter.id))

        assert "<script>" not in response.text
        assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in response.text
        assert "Lane &lt;b&gt;7&lt;/b&gt; &amp; Sons" in response.text


class TestDonorsAsBefore:
    async def test_a_donor_letter_to_a_lead_site_keeps_the_donor_page(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """Сайт — и лид продаж, и донор; «Отправитель» продаж в базе есть. Письмо доноров —
        подпись доноров по-английски: и меткой донора, и меткой с номером письма доноров."""
        sales, _ = await _sales_letter(session)
        donor = await _donor_letter(session, sales.domain_id)
        # Добивка продаж тому же сайту — после письма доноров: этап не «по сайту» и не
        # «по последнему письму сайта», а по письму метки.
        session.add(
            MessageModel(
                campaign_id=sales.campaign_id,
                thread_id=sales.thread_id,
                domain_id=sales.domain_id,
                step=1,
                status=MessageStatus.SENT,
                subject="Re",
                body="Re",
                idempotency_key="test:acme.example.test:followup:1",
            )
        )
        await session.commit()

        pages = [
            (await client.get(link)).text
            for link in (_link(donor.domain_id), _link(donor.domain_id, donor.id))
        ]

        for page in pages:
            assert DONORS_FOOTER in page
            assert '<html lang="en">' in page
            assert '<button type="submit">Unsubscribe</button>' in page
            assert SALES_NAME not in page
            assert "Made-up Agency" not in page

    async def test_an_unknown_label_keeps_the_donor_signature(self, client: AsyncClient) -> None:
        """Проверка соседней сессии после выкатки ждёт адрес на `/api/unsubscribe/x`."""
        response = await client.get("/api/unsubscribe/x")

        assert response.status_code == 404
        assert DONORS_FOOTER in response.text
        assert '<html lang="en">' in response.text

    async def test_a_letter_of_another_site_is_not_read(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        """Метка подписана, но письмо её — другого сайта (подписал не этот сервис: стенд, база
        из копии): страница сайта метки с подписью доноров, письмо не читается."""
        letter, _ = await _sales_letter(session)
        other = await make_donor(session, "other.example.test")
        await session.commit()

        response = await client.get(_link(other.id, letter.id))

        assert response.status_code == 200
        assert "other.example.test" in response.text
        assert DONORS_FOOTER in response.text
        assert SALES_NAME not in response.text


class TestBrokenSalesModule:
    async def test_the_page_and_the_button_survive_a_base_error_of_the_module(
        self,
        client: AsyncClient,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Запрос модуля продаж упал в базе — страница без подписи, по-английски, а нажатие
        пишет стоп-лист: модуль спрошен в своей точке сохранения, транзакция отписки цела.
        Адрес лида называет переписка (`stages.sales_lead_threads`) — «Отправитель» ей не нужен."""
        letter, lead = await _sales_letter(session)

        async def broken(session: AsyncSession) -> object:
            await session.execute(text("SELECT 1 FROM made_up_table_of_the_sales_sender"))
            return None

        monkeypatch.setattr(signer.sender, "read", broken)

        with caplog.at_level(logging.WARNING, logger=signer.__name__):
            shown = await client.get(_link(letter.domain_id, letter.id))
            pressed = await client.post(_link(letter.domain_id, letter.id))

        assert shown.status_code == pressed.status_code == 200
        assert '<button type="submit">Unsubscribe</button>' in shown.text
        assert "<footer>" not in shown.text
        assert OURS_ADDRESS not in shown.text
        rows = (await session.execute(select(SuppressionModel.email))).scalars().all()
        assert rows == [lead]
        assert "модуль продаж не ответил" in caplog.text


class TestTheLetterCarriesItsLabel:
    @pytest.fixture
    def fake(self, monkeypatch: pytest.MonkeyPatch) -> FakeSalesMail:
        """Подставной модуль продаж подключён к мосту почты."""
        monkeypatch.setattr(stages._SALES, "load", None)
        found = FakeSalesMail()
        stages.register_sales(lambda: found)
        return found

    async def test_a_sales_letter_goes_with_the_label_of_the_letter(
        self, session: AsyncSession, filled_legal: None, fake: FakeSalesMail
    ) -> None:
        """У письма продаж в `List-Unsubscribe` — метка с номером письма; письмо доноров
        несёт метку донора (`tests/test_unsubscribe.py`, `test_transport_gets_the_same_link`)."""
        world = await sales_world(session)
        source = _transports()

        await Sending(session, source, now=NOW).send(world.letter.id)

        [outgoing] = _seen(source)
        label = outgoing.unsubscribe_url.removeprefix(f"{FILLED['OUTREACH_UNSUBSCRIBE_URL']}/")
        found = unsubscribe.parse(label, secret=FILLED["OUTREACH_INBOUND_SECRET"])
        assert found == Label(world.letter.domain_id, world.letter.id)
