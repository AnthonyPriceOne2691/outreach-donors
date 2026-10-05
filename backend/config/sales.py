"""Модуль «Продажи»: выключатель, проверяльщик адресов и CRM для лидов.

**По умолчанию выключен.** Продажи пишут живым людям от имени компании,
и включение — решение человека для конкретного развёртывания, а не умолчание
кода. Сейчас выключать нечего: в модуле только данные (гипотезы и лиды)
и команды консоли. Выключатель читают срезы, которые приносят работу
продаж; первой — проверка ключей продаж на старте.

**Проверяльщик адресов по умолчанию выдуманный (`fixture`).** Живой стоит
денег с ключа, общего с соседней системой, и на него переключает человек:
`SALES_VERIFIER_PROVIDER=live`. Ключ — тот же `CONTACTS_HUNTER_API_KEY`,
что у поиска адресов: сервис один, второй переменной для него не заводим.

**Kommo по умолчанию тоже выдуманный (`fixture`).** Живой пишет сделки
в чужую CRM, и отозвать записанное нельзя: на `live` переключает человек,
а без поддомена, ключа, воронки, этапа или ответственного клиент не
собирается (`features/sales/kommo.build_kommo`).
"""

from __future__ import annotations

from pydantic import Field

from backend.config._base import DomainSettings


class _Sales(DomainSettings):
    enabled: bool = Field(default=False, validation_alias="SALES_ENABLED")
    # `fixture` — вердикты по правилам из адреса, без сети и денег;
    # `live` — Hunter Email Verifier тем же ключом, что ступень 3 поиска.
    verifier_provider: str = Field(default="fixture", validation_alias="SALES_VERIFIER_PROVIDER")
    # `fixture` — сделки с выдуманными номерами, без сети; `live` — закрытая
    # интеграция аккаунта Kommo долгоживущим ключом (секрет: в `.env`).
    kommo_provider: str = Field(default="fixture", validation_alias="SALES_KOMMO_PROVIDER")
    kommo_subdomain: str = Field(default="", validation_alias="SALES_KOMMO_SUBDOMAIN")
    kommo_token: str = Field(default="", validation_alias="SALES_KOMMO_TOKEN")
    # Номера из Kommo — строкой: пустое значение образца не роняет чтение
    # настроек, а негодное называет фабрика клиента словами до первого запроса.
    kommo_pipeline_id: str = Field(default="", validation_alias="SALES_KOMMO_PIPELINE_ID")
    kommo_status_id: str = Field(default="", validation_alias="SALES_KOMMO_STATUS_ID")
    kommo_responsible_user_id: str = Field(
        default="", validation_alias="SALES_KOMMO_RESPONSIBLE_USER_ID"
    )


_s = _Sales()

ENABLED: bool = _s.enabled
VERIFIER_PROVIDER: str = _s.verifier_provider

KOMMO_PROVIDER: str = _s.kommo_provider
KOMMO_SUBDOMAIN: str = _s.kommo_subdomain.strip().lower()
KOMMO_TOKEN: str = _s.kommo_token.strip()
KOMMO_PIPELINE_ID: str = _s.kommo_pipeline_id.strip()
KOMMO_STATUS_ID: str = _s.kommo_status_id.strip()
KOMMO_RESPONSIBLE_USER_ID: str = _s.kommo_responsible_user_id.strip()

#: Не больше стольких запросов в секунду. Предел Kommo из его документации —
#: семь в секунду с одного IP для любой интеграции; чаще — 429, а частые 429
#: закрывают доступ целиком (403).
KOMMO_RATE_PER_SEC = 7
#: Сколько ждать ответа Kommo на один запрос.
KOMMO_TIMEOUT_SEC = 20.0
