"""«Пишите на X@» в автоответе мёртвого ящика: куда писать донору дальше.

Правило «мёртвый ящик» (`classify.DEAD_MAILBOX`) хоронит адрес, которому
мы писали, и открывает следующий адрес донора. Но автоответ такого ящика
часто сам называет следующий: «John has left the company, please contact
editor@…», «Diese Adresse wird nicht mehr gelesen, schreiben Sie an …».
Не взять его — перебирать найденные лестницей адреса вслепую, когда донор
уже сказал, куда писать.

Адреса берутся из написанного рукой (без цитаты: в ней наше письмо и наш
адрес) и из заголовка `Reply-To`. Отбрасывается, и каждое правило — чей-то
провал:

* адрес отправителя — это и есть мёртвый ящик;
* робот (`robots`): noreply и почтовая служба;
* наш адрес — с меткой ответа (`+m<номер>.`) или на домене отправки
  (`senders`): автоответ, повторивший наше «куда отвечать», позвал бы нас
  писать самим себе;
* адрес, который у донора уже есть;
* адрес, не прошедший проверку годности (`contacts.quality`): сборка писем
  его всё равно пропустит.

**Адрес на домене донора — сразу в контакты, следующим по порядку
адресов** (`contacts.preference`): с оценкой вписанного человеком — выше
найденных лестницей и выше похороненного (у того оценка 0). Источник —
`manual`, как у адреса, с которого донор ответил: он пришёл от самого
донора, лестница его не улучшит, а её последняя ступень платная. Домен
сравнивается по зарегистрированному имени (`donors.host`): `mail.donor.com`
и `donor.com` — один сайт.

**Адрес на чужом домене — человеку, а не в контакты.** `jane@gmail.com`
в автоответе редакции — и новый редактор, и частный ящик бывшего
сотрудника: написать туда без человека значит написать незнакомому
от имени нашего домена. Адрес уходит в причину ответа словами, а вписать
его — одно поле в карточке донора (`contacts.manual`).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.contacts.extract import EMAIL_RE
from backend.features.contacts.manual import MANUAL_SCORE
from backend.features.contacts.quality import rejection_reason
from backend.features.core.domain import ContactSource
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import SenderModel
from backend.features.donors.host import normalize_host
from backend.features.replies import robots
from backend.features.replies.inbound import Incoming, masked_for_log
from backend.features.replies.quoting import written_by_hand

logger = logging.getLogger(__name__)

#: Отметка у адреса, который назвал автоответ донора: откуда он взялся,
#: видно и через год, когда автоответа никто не помнит.
FROM_AUTOREPLY = "from_autoreply"

#: Наша метка в адресе ответа: `anna+m417.7d3a91c2e5@replies.…`.
_OUR_LABEL = re.compile(r"\+m\d+\.", re.I)


@dataclass(frozen=True, slots=True)
class Redirect:
    """Что нашлось в автоответе и что с этим сделано."""

    #: На домене донора — записаны в контакты.
    added: tuple[str, ...] = ()
    #: На чужом домене — ждут человека.
    elsewhere: tuple[str, ...] = ()
    #: Отброшенные — с причиной словами.
    dropped: tuple[tuple[str, str], ...] = ()

    @property
    def review_reason(self) -> str | None:
        """Почему ответ ждёт человека. `None` — не ждёт."""
        if not self.elsewhere:
            return None
        return (
            f"автоответ предлагает писать на {', '.join(self.elsewhere)} — "
            "добавить руками в карточке донора"
        )

    @property
    def as_report(self) -> dict[str, int]:
        return {
            "записаны": len(self.added),
            "человеку": len(self.elsewhere),
            "отброшены": len(self.dropped),
        }


def named_in(incoming: Incoming) -> tuple[str, ...]:
    """Адреса, которые назвал автоответ: в написанном рукой и в `Reply-To`."""
    found: Iterable[str] = (
        *EMAIL_RE.findall(written_by_hand(incoming.for_model)),
        *EMAIL_RE.findall(incoming.header("reply-to")),
    )
    # Точки перед адресом выражение забирает в имя ящика («...editor@») — срезаем.
    return tuple(dict.fromkeys(address.strip(".").lower() for address in found))


@dataclass(frozen=True, slots=True)
class _Sieve:
    """Что не годится адресом донора — у одного письма и одного донора."""

    sender: str
    ours: frozenset[str]
    known: frozenset[str]

    def _our_own(self, address: str) -> bool:
        local, _, domain = address.partition("@")
        return bool(_OUR_LABEL.search(local)) or any(
            domain == own or domain.endswith(f".{own}") for own in self.ours
        )

    def why_not(self, address: str) -> str | None:
        """Почему адрес не берём — словами. `None` — берём."""
        checks = (
            (address == self.sender, "это адрес отправителя — тот самый ящик"),
            (robots.robot(address), "робот: письма туда не читают"),
            (self._our_own(address), "наш собственный адрес"),
            (address in self.known, "у донора уже есть"),
        )
        return next((why for failed, why in checks if failed), None) or rejection_reason(address)


def same_site(address: str, host: str) -> bool:
    """Адрес на домене донора — том же зарегистрированном или его поддомене."""
    site = normalize_host(host)
    return bool(site) and normalize_host(address.partition("@")[2]) == site


def sort_out(
    addresses: Iterable[str],
    *,
    sender: str,
    host: str,
    ours: Collection[str],
    known: Collection[str],
) -> Redirect:
    """Разложить названные адреса: донору, человеку, в отброшенные.

    Решение без базы: что уже известно и что наше, приносит вызывающий.
    """
    sieve = _Sieve(
        sender=sender.strip().lower(),
        ours=frozenset(domain.strip().lower() for domain in ours),
        known=frozenset(email.strip().lower() for email in known),
    )
    added: list[str] = []
    elsewhere: list[str] = []
    dropped: list[tuple[str, str]] = []
    for address in addresses:
        why = sieve.why_not(address)
        if why is not None:
            dropped.append((address, why))
        else:
            (added if same_site(address, host) else elsewhere).append(address)
    return Redirect(added=tuple(added), elsewhere=tuple(elsewhere), dropped=tuple(dropped))


async def follow(
    session: AsyncSession, incoming: Incoming, *, domain_id: int, host: str
) -> Redirect:
    """Адреса из автоответа мёртвого ящика — донору в контакты или человеку."""
    named = named_in(incoming)
    if not named:
        return Redirect()
    ours = await session.scalars(select(SenderModel.domain).distinct())
    known = await session.scalars(
        select(ContactModel.email).where(ContactModel.domain_id == domain_id)
    )
    found = sort_out(
        named, sender=incoming.from_email, host=host, ours=ours.all(), known=known.all()
    )
    for address in found.added:
        session.add(
            ContactModel(
                domain_id=domain_id,
                email=address,
                source=ContactSource.MANUAL,
                verification_status=FROM_AUTOREPLY,
                verification_score=MANUAL_SCORE,
            )
        )
    await session.flush()
    _log(found, domain_id)
    return found


def _log(found: Redirect, domain_id: int) -> None:
    for address in found.added:
        logger.info(
            "приём: мёртвый ящик домена №%s назвал адрес %s — записан следующим",
            domain_id,
            masked_for_log(address),
        )
    for address, why in found.dropped:
        logger.info(
            "приём: адрес %s из автоответа домена №%s не взят — %s",
            masked_for_log(address),
            domain_id,
            why,
        )
    if found.review_reason is not None:
        # Адрес целиком: это поручение человеку, и вписать его в карточку
        # донора по маске нельзя.
        logger.warning("приём: домен №%s — %s", domain_id, found.review_reason)
