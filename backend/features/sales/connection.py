"""Подключены ли продажи к почте: что должно быть задано, чтобы письмо продаж ушло.

**Одно правило у отправки, прохода добивок, сборки очереди и экрана.** Отказ — тот же,
что у среза 1.1b (`core.stages.SalesNotConnectedError`, «продажи к почте ещё не
подключены»), и называет всё, чего не хватает, сразу — чинить по пункту за раз долго:

- выключатель направления `SALES_ENABLED`: продажи пишут живым людям от имени компании,
  и включение — решение человека для развёртывания, а не умолчание кода (`config/sales.py`);
- своя учётка почтовой платформы продаж (`config.outreach.mail_account`): домены продаж
  подтверждены в их учётке, а без своего ключа общий механизм отдал бы общую учётку —
  письмо продаж ушло бы ключом доноров. Свой список разрешённых — как требует механизм;
- ссылка отписки для `List-Unsubscribe`: холодное письмо без рабочей отписки нарушает
  правила почтовых платформ и закон о рассылках. У доноров её отсутствие письмо не
  останавливает (юридический блок снят 23.09.2026), у продаж — останавливает;
- отправитель: физический адрес, подпись и имя (`sender.REQUIRED`).

Цепочка писем — своя у каждого лида (гипотеза и язык): её готовность проверяют сборка
(лид без готовой цепочки в очередь не встаёт) и отправка (набор письма — в `sales_threads`).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import outreach as outreach_cfg
from backend.config import sales as sales_cfg
from backend.features.core.domain import Stage
from backend.features.core.stages import SalesNotConnectedError
from backend.features.letters import unsubscribe
from backend.features.sales import sender
from backend.features.sales.sender import Sender

#: Переменная своего ключа учётки продаж — так её называет `mail_account`.
OWN_KEY = f"OUTREACH_{Stage.SALES.value.upper()}_SENDGRID_API_KEY"
#: Номер домена для пробы ссылки отписки: сама ссылка никуда не ведёт — проверяется,
#: собирается ли она вообще (как `compose.missing_settings`).
_PROBE_DOMAIN_ID = 0


def account_missing() -> str | None:
    """Чего не хватает учётке продаж. `None` — своя учётка собрана.

    Своя учётка узнаётся по имени переменной ключа: без своего ключа механизм #175
    берёт общий и называет обе строки (`… (или общий …)`).
    """
    found = outreach_cfg.mail_account(Stage.SALES.value)
    if found.key_setting != OWN_KEY:
        return (
            f"нет своей учётки почты продаж: не задан {OWN_KEY} — "
            "письма продаж общей учёткой не уходят"
        )
    if not found.api_key:
        return f"{OWN_KEY} пуст — ключ учётки продаж не задан"
    return found.refusal


def unsubscribe_missing() -> str | None:
    """Чего не хватает ссылке отписки. `None` — `List-Unsubscribe` у письма будет."""
    if unsubscribe.url_for(_PROBE_DOMAIN_ID):
        return None
    return f"нет ссылки отписки для List-Unsubscribe: не задан {unsubscribe.missing_setting()}"


def reasons(found: Sender) -> list[str]:
    """Чего не хватает продажам — словами, в порядке важности. Пусто — подключены."""
    said = [] if sales_cfg.ENABLED else ["продажи выключены: SALES_ENABLED не включён"]
    said += [why for why in (account_missing(), unsubscribe_missing()) if why]
    if found.missing:
        said.append(f"{'; '.join(found.missing)} — {sender.WHERE}")
    return said


async def missing(session: AsyncSession) -> list[str]:
    """Чего не хватает продажам сейчас. Пусто — подключены."""
    return reasons(await sender.read(session))


async def check(session: AsyncSession, what: str) -> Sender:
    """Отправитель продаж, если продажи подключены; иначе отказ 1.1b с причинами."""
    found = await sender.read(session)
    said = reasons(found)
    if said:
        raise SalesNotConnectedError(what, "; ".join(said))
    return found
