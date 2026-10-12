"""Ссылка отписки в письме и метка, по которой узнают, кто отписался.

**Метка кодирует донора, а не адрес.** Отписка закрывает сайт целиком:
донор просит перестать ему писать, а не перестать писать на один ящик.
Метка по адресу дала бы обход, которого никто не задумывал, — цепочка
продолжилась бы на втором найденном адресе того же сайта, и выглядело бы
это как рассылка в обход отписки.

**У письма продаж метка несёт и номер письма** (`u<донор>m<письмо>`, `letter_url`):
по нему страница отписки узнаёт этап письма — номер → письмо → рассылка → этап — и
подписывается «Отправителем» продаж на языке письма (`api/unsubscribe/signer.py`), а не
именем и адресом доноров. Отписывает такая метка так же — сайт целиком. Письма доноров
и рекламодателей несут прежнюю метку донора, и ссылки, ушедшие до номера, работают как
работали. Подпись закрывает оба номера: метку не переставить ни на другой сайт, ни на
чужое письмо.

**Подпись обязательна.** Без неё метку подбирают перебором, и отписать
можно кого угодно: чужой донор молча перестаёт получать письма, а
выглядит это как «он не отвечает». Секрет тот же, что у меток ответа
(`OUTREACH_INBOUND_SECRET`), нового хранилища не появляется.

**Нечем подписать — ссылки нет вовсе.** Тогда письмо уходит без заголовка
`List-Unsubscribe`. Подставить адрес без метки было бы хуже всего: кнопка
отписки в почтовом клиенте есть, а не работает ни для кого.

Ссылки в тексте письма больше нет — юридический блок снят 23.09.2026, —
а заголовок остался: он невидим адресату, почтовые платформы судят по нему
о рассылке, и отписка через него — меньшее зло, чем кнопка «спам».
"""

from __future__ import annotations

import hmac
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING

from backend.config import outreach as cfg
from backend.features.core.domain import Stage

if TYPE_CHECKING:
    from backend.features.core.models.outreach import MessageModel

#: Длина подписи в шестнадцатеричных знаках — как у меток ответа.
#: Сорок бит: перебрать нельзя, ссылка остаётся глазу обозримой.
SIGNATURE_LEN = 10

LABEL_RE = re.compile(rf"^u(\d+)(?:m(\d+))?\.([0-9a-f]{{{SIGNATURE_LEN}}})$")


@dataclass(frozen=True, slots=True)
class Label:
    """Что говорит подписанная метка: чей сайт и — у письма продаж — какое письмо."""

    domain_id: int
    #: Номер письма. Пусто — метка донора: письма доноров и рекламодателей, ссылки до номера.
    letter_id: int | None = None


def _signed(domain_id: int, letter_id: int | None) -> str:
    """Что подписывается: номер донора и, если есть, номер письма."""
    return f"u{domain_id}" if letter_id is None else f"u{domain_id}m{letter_id}"


def _sign(text: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), text.encode(), sha256)
    return digest.hexdigest()[:SIGNATURE_LEN]


def label_for(domain_id: int, *, letter_id: int | None = None, secret: str | None = None) -> str:
    """Подписанная метка донора — с номером письма, если он дан. Пусто — подписать нечем."""
    key = secret if secret is not None else cfg.INBOUND_SECRET
    if not key:
        return ""
    text = _signed(domain_id, letter_id)
    return f"{text}.{_sign(text, key)}"


def url_for(
    domain_id: int | None,
    *,
    letter_id: int | None = None,
    base: str | None = None,
    secret: str | None = None,
) -> str:
    """Адрес страницы отписки для конкретного донора (и письма, если номер дан).

    Пустая строка — ссылку собрать нечем: не задан адрес страницы, не
    задан секрет или письмо готовится без донора (предпросмотр шаблона).
    Пустоту назовёт `compose.missing()`, и отправка откажет.
    """
    page = base if base is not None else cfg.UNSUBSCRIBE_URL
    if not page or domain_id is None:
        return ""
    label = label_for(domain_id, letter_id=letter_id, secret=secret)
    if not label:
        return ""
    return f"{page.rstrip('/')}/{label}"


def letter_url(message: MessageModel, stage: Stage) -> str:
    """Ссылка отписки письма для `List-Unsubscribe`: у письма продаж — с номером письма,
    у доноров и рекламодателей — метка донора, как была."""
    letter_id = message.id if stage is Stage.SALES else None
    return url_for(message.domain_id, letter_id=letter_id)


def missing_setting(secret: str | None = None) -> str:
    """Чего не хватает для рабочей ссылки, именем в окружении."""
    key = secret if secret is not None else cfg.INBOUND_SECRET
    if not cfg.UNSUBSCRIBE_URL:
        return "OUTREACH_UNSUBSCRIBE_URL"
    if not key:
        return "OUTREACH_INBOUND_SECRET"
    return "OUTREACH_UNSUBSCRIBE_URL"


def parse(label: str, *, secret: str | None = None) -> Label | None:
    """Что говорит метка. `None` — метки нет или подпись не сходится.

    Оба случая для страницы одинаковы: отписывать некого, и разницу
    наружу не выносим — по разным ответам метку подбирают.
    """
    key = secret if secret is not None else cfg.INBOUND_SECRET
    if not key:
        return None
    match = LABEL_RE.match(label.strip())
    if match is None:
        return None
    domain_id = int(match.group(1))
    letter_id = int(match.group(2)) if match.group(2) is not None else None
    if not hmac.compare_digest(match.group(3), _sign(_signed(domain_id, letter_id), key)):
        return None
    return Label(domain_id, letter_id)


def domain_id_from(label: str, *, secret: str | None = None) -> int | None:
    """Номер донора из метки. `None` — метки нет или подпись не сходится."""
    found = parse(label, secret=secret)
    return None if found is None else found.domain_id
