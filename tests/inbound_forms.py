"""Оснастка тестов приёма: форма платформы по байтам, как её шлёт SendGrid.

Тесты приёма до сих пор слали форму словарём, и клиент кодировал её
в UTF-8 сам — поэтому ни один из них не видел, что поле в windows-1251
приходит кракозябрами, а поле больше мегабайта роняет вебхук. Здесь форма
собирается руками, байт в байт: поле — в той кодировке, какую назвал
`charsets`, файл — как есть, заголовок части — как его пишет платформа.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import pytest
from backend.api.inbound import routes as inbound_routes
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import ContactSource, DonorStatus, MessageStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.outreach import CampaignModel, MessageModel, ThreadModel
from backend.features.letters import reply_to
from backend.shared.sliding_window import SlidingWindow
from sqlalchemy.ext.asyncio import AsyncSession

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
SECRET = "s" * 32
REPLY_HOST = "replies.ours.test"
HOST = "donor.example.test"
BOUNDARY = "xYzZY"
URL = "/api/inbound/replies"
AUTH = {"X-Inbound-Secret": SECRET}

#: Кодировки полей по умолчанию — как их называет платформа.
UTF8 = {"to": "UTF-8", "from": "UTF-8", "subject": "UTF-8", "text": "UTF-8", "html": "UTF-8"}


class NoQueue:
    """Очередь, которая ничего не ставит: тест проверяет приём, а не rq."""

    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[Any, ...]]] = []

    def enqueue(self, job: str, *args: Any, **_: Any) -> object:
        self.jobs.append((job, args))
        return type("Job", (), {"id": "job-1"})()


def setup_inbound(monkeypatch: pytest.MonkeyPatch) -> NoQueue:
    """Секрет, очередь-заглушка и чистое окно частоты."""
    monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", SECRET)
    queue = NoQueue()
    monkeypatch.setattr(inbound_routes, "runs_queue", lambda: queue)
    monkeypatch.setattr(inbound_routes, "_throttle", SlidingWindow())
    return queue


async def make_sent(session: AsyncSession) -> MessageModel:
    """Наше письмо донору, уже ушедшее."""
    domain = DomainModel(host=HOST)
    campaign = CampaignModel(stage=Stage.DONORS, name="Проверка", status="running")
    session.add_all([domain, campaign])
    await session.flush()

    session.add(DonorModel(domain_id=domain.id, status=DonorStatus.SUITABLE, dr=40))
    contact = ContactModel(domain_id=domain.id, email=f"editor@{HOST}", source=ContactSource.PAGE)
    session.add(contact)
    await session.flush()

    thread = ThreadModel(domain_id=domain.id, campaign_id=campaign.id, contact_id=contact.id)
    session.add(thread)
    await session.flush()

    message = MessageModel(
        campaign_id=campaign.id,
        thread_id=thread.id,
        domain_id=domain.id,
        contact_id=contact.id,
        step=0,
        status=MessageStatus.SENT,
        subject="Advertising rates",
        body="Good afternoon,",
        sent_at=NOW,
        provider_message_id="<ours-1@mail.test>",
        idempotency_key=f"donors:{HOST}:0",
    )
    session.add(message)
    await session.commit()
    return message


def label_for(message: MessageModel) -> str:
    """Адрес для ответа с подписанной меткой нашего письма.

    Собирается из самой метки, а не через `address_for`: привязке важна
    только метка после «+», а правила домена ответов меняются отдельно
    от приёма — тест приёма не должен падать от их правки.
    """
    return f"anna+{reply_to.label_for(message.id, secret=SECRET)}@{REPLY_HOST}"


def multipart(
    fields: Sequence[tuple[str, bytes]],
    files: Sequence[tuple[str, str, str, bytes]] = (),
) -> tuple[bytes, dict[str, str]]:
    """Тело `multipart/form-data` и заголовок к нему.

    Файл — «поле, параметры имени файла, тип, байты»: параметры пишутся
    в заголовок части как есть (`filename="…"` или `filename*=UTF-8''…`).
    """
    parts: list[bytes] = []
    for name, value in fields:
        head = f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
        parts.append(head.encode() + value + b"\r\n")
    for name, filename, kind, data in files:
        head = (
            f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="{name}"; {filename}\r\n'
            f"Content-Type: {kind}\r\n\r\n"
        )
        parts.append(head.encode("utf-8") + data + b"\r\n")
    body = b"".join(parts) + f"--{BOUNDARY}--\r\n".encode()
    return body, {"Content-Type": f"multipart/form-data; boundary={BOUNDARY}"}


def headers_blob(message_id: str, extra: str = "") -> bytes:
    """Поле `headers`: сырой блок заголовков письма."""
    return (
        "Received: by mx.sendgrid.net with SMTP id 6WCVv7KAWn\n"
        f"Message-ID: <{message_id}@mail.donor.test>\n"
        "In-Reply-To: <some-other@mail.test>\n"
        "Subject: Re: Advertising rates\n"
        f"From: Elena <editor@{HOST}>\n" + extra
    ).encode()


def letter(
    message: MessageModel,
    *,
    text: bytes | None = b"Placement is 250 EUR.",
    charsets: dict[str, str] | None = None,
    html: bytes | None = None,
    to: bytes | None = None,
    envelope_to: str | None = None,
    message_id: str = "in-1",
    extra: Sequence[tuple[str, bytes]] = (),
) -> list[tuple[str, bytes]]:
    """Поля разобранного письма так, как их шлёт платформа. Копия (`cc`)
    и прочее необязательное — полями в `extra`."""
    label = label_for(message)
    fields = [
        ("headers", headers_blob(message_id)),
        ("dkim", b"{@donor.example.test : pass}"),
        ("to", label.encode() if to is None else to),
        ("from", f"Elena <editor@{HOST}>".encode()),
        ("sender_ip", b"209.85.223.172"),
        ("envelope", json.dumps({"to": [envelope_to or label], "from": f"editor@{HOST}"}).encode()),
        ("subject", b"Re: Advertising rates"),
        ("charsets", json.dumps(charsets or UTF8).encode()),
        ("SPF", b"pass"),
    ]
    if text is not None:
        fields.append(("text", text))
    if html is not None:
        fields.append(("html", html))
    fields.extend(extra)
    return fields


def attachment_info(*names: tuple[str, str]) -> tuple[str, bytes]:
    """Поле `attachment-info`: имя и тип каждого файла, размера нет."""
    info = {
        f"attachment{number}": {"filename": name, "name": name, "type": kind}
        for number, (name, kind) in enumerate(names, start=1)
    }
    return "attachment-info", json.dumps(info, ensure_ascii=False).encode()


def raw_mode(message: MessageModel, email: bytes) -> list[tuple[str, bytes]]:
    """Поля сырого режима: письмо целиком и немного разобранного вокруг."""
    label = label_for(message)
    return [
        ("to", label.encode()),
        ("from", f"Elena <editor@{HOST}>".encode()),
        ("subject", b"Re: Advertising rates"),
        ("envelope", json.dumps({"to": [label], "from": f"editor@{HOST}"}).encode()),
        ("charsets", json.dumps({"to": "UTF-8", "from": "UTF-8", "subject": "UTF-8"}).encode()),
        ("email", email),
    ]
