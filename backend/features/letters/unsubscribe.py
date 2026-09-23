"""Ссылка отписки в письме и метка, по которой узнают, кто отписался.

**Метка кодирует донора, а не адрес.** Отписка закрывает сайт целиком:
донор просит перестать ему писать, а не перестать писать на один ящик.
Метка по адресу дала бы обход, которого никто не задумывал, — цепочка
продолжилась бы на втором найденном адресе того же сайта, и выглядело бы
это как рассылка в обход отписки.

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
from hashlib import sha256

from backend.config import outreach as cfg

#: Длина подписи в шестнадцатеричных знаках — как у меток ответа.
#: Сорок бит: перебрать нельзя, ссылка остаётся глазу обозримой.
SIGNATURE_LEN = 10

LABEL_RE = re.compile(rf"^u(\d+)\.([0-9a-f]{{{SIGNATURE_LEN}}})$")


def _sign(domain_id: int, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), f"u{domain_id}".encode(), sha256)
    return digest.hexdigest()[:SIGNATURE_LEN]


def label_for(domain_id: int, *, secret: str | None = None) -> str:
    """Подписанная метка донора. Пусто — подписать нечем."""
    key = secret if secret is not None else cfg.INBOUND_SECRET
    if not key:
        return ""
    return f"u{domain_id}.{_sign(domain_id, key)}"


def url_for(
    domain_id: int | None,
    *,
    base: str | None = None,
    secret: str | None = None,
) -> str:
    """Адрес страницы отписки для конкретного донора.

    Пустая строка — ссылку собрать нечем: не задан адрес страницы, не
    задан секрет или письмо готовится без донора (предпросмотр шаблона).
    Пустоту назовёт `compose.missing()`, и отправка откажет.
    """
    page = base if base is not None else cfg.UNSUBSCRIBE_URL
    if not page or domain_id is None:
        return ""
    label = label_for(domain_id, secret=secret)
    if not label:
        return ""
    return f"{page.rstrip('/')}/{label}"


def missing_setting(secret: str | None = None) -> str:
    """Чего не хватает для рабочей ссылки, именем в окружении."""
    key = secret if secret is not None else cfg.INBOUND_SECRET
    if not cfg.UNSUBSCRIBE_URL:
        return "OUTREACH_UNSUBSCRIBE_URL"
    if not key:
        return "OUTREACH_INBOUND_SECRET"
    return "OUTREACH_UNSUBSCRIBE_URL"


def domain_id_from(label: str, *, secret: str | None = None) -> int | None:
    """Номер донора из метки. `None` — метки нет или подпись не сходится.

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
    if not hmac.compare_digest(match.group(2), _sign(domain_id, key)):
        return None
    return domain_id
