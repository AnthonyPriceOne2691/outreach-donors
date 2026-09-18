"""Ступень 2: RDAP регистратора.

RDAP пришёл на смену WHOIS: тот же реестр, но ответ в JSON, а не текстом,
который каждый регистратор форматирует по-своему. Запрос бесплатный.

Отдача у ступени низкая и будет падать: контакты владельца в большинстве
доменных зон скрыты, вместо них отдают адрес-пересылку или контакт службы
приватности. Ступень оставлена именно потому, что бесплатна: на доменах
старых зон и на корпоративных регистрациях адрес владельца всё ещё
встречается, а стоит эта попытка одного запроса.

Адреса регистратора и служб скрытия отсеиваются фильтром качества:
письмо в `abuse@` регистратора к владельцу сайта не попадёт.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from backend.config import contacts as cfg
from backend.features.contacts.quality import Candidate
from backend.features.core.domain import ContactSource

logger = logging.getLogger(__name__)

# Точка входа, которая сама перенаправляет на RDAP нужного реестра.
RDAP_BOOTSTRAP = "https://rdap.org/domain/"

# Роли, чей адрес нам не нужен: это контакт регистратора, а не сайта.
IGNORED_ROLES = frozenset({"abuse", "registrar", "reseller", "sponsor", "proxy"})


class RdapUnavailableError(RuntimeError):
    """RDAP не ответил. Это не «контакта нет», а «мы не спросили»."""


def _vcard_emails(entity: dict[str, Any]) -> list[str]:
    """Адреса из vCard одной сущности.

    Формат жёсткий и неудобный: `["vcard", [["email", {}, "text", "a@b.c"], ...]]`.
    Разбираем защитно — чужая структура однажды приедет другой формы,
    и падать посреди прогона из-за этого нельзя.
    """
    vcard = entity.get("vcardArray")
    if not isinstance(vcard, list) or len(vcard) < 2 or not isinstance(vcard[1], list):
        return []

    out: list[str] = []
    for field in vcard[1]:
        if not isinstance(field, list) or len(field) < 4 or field[0] != "email":
            continue
        value = field[3]
        if isinstance(value, str) and value.strip():
            out.append(value.strip().lower())
    return out


def _walk(entities: Any, *, depth: int = 0) -> list[str]:
    """Обойти сущности вместе с вложенными: владелец часто лежит внутри
    записи регистратора."""
    if depth > 3 or not isinstance(entities, list):
        return []

    out: list[str] = []
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        roles = {str(role).lower() for role in entity.get("roles") or []}
        if roles & IGNORED_ROLES:
            continue
        out.extend(_vcard_emails(entity))
        out.extend(_walk(entity.get("entities"), depth=depth + 1))
    return out


async def find_emails(client: httpx.AsyncClient, host: str) -> list[Candidate]:
    """Адреса владельца домена по RDAP. Пустой список — законный исход."""
    try:
        response = await client.get(
            f"{RDAP_BOOTSTRAP}{host}",
            timeout=cfg.RDAP_TIMEOUT_SEC,
            follow_redirects=True,
            headers={"Accept": "application/rdap+json"},
        )
    except httpx.HTTPError as exc:
        raise RdapUnavailableError(f"RDAP по {host} не ответил: {exc!r}") from exc

    if response.status_code == 404:
        logger.debug("RDAP: реестр не знает домена %s", host)
        return []
    if response.status_code >= 400:
        raise RdapUnavailableError(f"RDAP по {host} ответил {response.status_code}")

    try:
        body = response.json()
    except ValueError as exc:
        # «Не поняли ответ» обязано быть громким: молча вернув пустоту,
        # мы получили бы прогон, где RDAP не находит ничего и никогда.
        raise RdapUnavailableError(
            f"RDAP по {host} вернул не JSON: {response.text[:120]!r}"
        ) from exc

    if not isinstance(body, dict):
        raise RdapUnavailableError(f"RDAP по {host} вернул {type(body).__name__}, ждали объект")

    emails = dict.fromkeys(_walk(body.get("entities")))
    return [Candidate(email=email, source=ContactSource.WHOIS) for email in emails]
