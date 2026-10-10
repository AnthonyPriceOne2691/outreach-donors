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

**Человеку — слова, администратору — имена настроек.** Пункты готовности уходят на экраны
(«Очередь писем», отказ сборки и отправки) без кодов настроек: их задаёт администратор, а
человек у экрана читает, чего не хватает и кто это чинит (находка QA соседей на проде:
«продажи выключены: SALES_ENABLED не включён» в отказе сборки). Имена настроек — в журнале:
`check` пишет их строкой, когда отказывает.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import outreach as outreach_cfg
from backend.config import sales as sales_cfg
from backend.features.core.domain import Stage
from backend.features.core.stages import SalesNotConnectedError
from backend.features.letters import unsubscribe
from backend.features.sales import sender
from backend.features.sales.sender import Sender

logger = logging.getLogger(__name__)

#: Переменная своего ключа учётки продаж — так её называет `mail_account`.
OWN_KEY = f"OUTREACH_{Stage.SALES.value.upper()}_SENDGRID_API_KEY"
#: Номер домена для пробы ссылки отписки: сама ссылка никуда не ведёт — проверяется,
#: собирается ли она вообще (как `compose.missing_settings`).
_PROBE_DOMAIN_ID = 0

#: Модуль выключен (`SALES_ENABLED`) — словами человека: так начинаются и пункт готовности,
#: и причины разбора ответа и передачи лида, и строка под шапкой раздела на экране.
MODULE_OFF = "модуль продаж выключен"


@dataclass(frozen=True, slots=True)
class Gap:
    """Чего не хватает продажам: словами — человеку, именем настройки — журналу."""

    words: str
    #: Пусто — чинится на экране, а не настройкой (поля «Отправителя»).
    setting: str = ""


def _account_gap() -> Gap | None:
    """Чего не хватает учётке продаж. `None` — своя учётка собрана.

    Своя учётка узнаётся по имени переменной ключа: без своего ключа механизм #175
    берёт общий и называет обе строки (`… (или общий …)`).
    """
    found = outreach_cfg.mail_account(Stage.SALES.value)
    if found.key_setting != OWN_KEY:
        words = "нет своей учётки почты продаж — общей учёткой письма продаж не уходят"
        return Gap(f"{words}; заводит администратор", OWN_KEY)
    if not found.api_key:
        return Gap("ключ учётки почты продаж не задан — задаёт администратор", OWN_KEY)
    if found.refusal:
        return Gap(
            "у учётки почты продаж не задан свой список разрешённых адресов — задаёт администратор",
            found.allowlist_setting,
        )
    return None


def _unsubscribe_gap() -> Gap | None:
    """Чего не хватает ссылке отписки. `None` — `List-Unsubscribe` у письма будет."""
    if unsubscribe.url_for(_PROBE_DOMAIN_ID):
        return None
    words = "нет ссылки отписки — без неё письмо продаж не уходит; задаёт администратор"
    return Gap(words, unsubscribe.missing_setting())


def gaps(found: Sender) -> list[Gap]:
    """Чего не хватает продажам, в порядке важности: выключатель модуля — первым."""
    found_gaps = (
        []
        if sales_cfg.ENABLED
        else [Gap(f"{MODULE_OFF} — включает администратор", "SALES_ENABLED")]
    )
    found_gaps += [gap for gap in (_account_gap(), _unsubscribe_gap()) if gap is not None]
    if found.missing:
        found_gaps.append(Gap(f"{'; '.join(found.missing)} — {sender.WHERE}"))
    return found_gaps


def reasons(found: Sender) -> list[str]:
    """Чего не хватает продажам — словами человека, в порядке важности. Пусто — подключены."""
    return [gap.words for gap in gaps(found)]


def blockers(found: Sender) -> list[str]:
    """Чего не хватает продажам — для «Отправителя» и письма глазами адресата: настройки
    администратора словами (модуль первым), затем поля отправителя по одному — без «заполните
    на экране…»: человек уже на нём. Правило то же, что у отказа отправки (`gaps`)."""
    return [gap.words for gap in gaps(found) if gap.setting] + found.missing


async def missing(session: AsyncSession) -> list[str]:
    """Чего не хватает продажам сейчас. Пусто — подключены."""
    return reasons(await sender.read(session))


async def check(session: AsyncSession, what: str) -> Sender:
    """Отправитель продаж, если продажи подключены; иначе отказ 1.1b с причинами словами.
    Имена настроек, которые задаёт администратор, — строкой журнала."""
    found = await sender.read(session)
    said = gaps(found)
    if said:
        logger.warning(
            "продажи к почте не подключены — что задать администратору",
            extra={"what": what, "settings": [gap.setting for gap in said if gap.setting]},
        )
        raise SalesNotConnectedError(what, "; ".join(gap.words for gap in said))
    return found
