"""Выбор источника выдачи по настройке.

Провайдеров два, и это не абстракция ради абстракции. Основной дешевле
запасного примерно в двадцать раз; запасной нужен, чтобы отладка
не упиралась в чужой баланс, а падение одного не останавливало прогон.

Выбор живёт здесь, а не в месте запуска: иначе каждая новая точка входа
заводит свою ветку «если dataforseo, то...», и однажды одна из них
отстаёт.
"""

from __future__ import annotations

import logging

from backend.config import serp as cfg
from backend.features.ahrefs.client import AhrefsClient
from backend.features.serp.ahrefs_serp import AhrefsSerpProvider
from backend.features.serp.dataforseo import DataForSeoProvider
from backend.features.serp.protocol import SerpProvider

logger = logging.getLogger(__name__)

DATAFORSEO = "dataforseo"
AHREFS = "ahrefs"
KNOWN = (DATAFORSEO, AHREFS)


class UnknownProviderError(RuntimeError):
    """В настройке указан источник, которого нет. Молча взять другой нельзя:
    они отличаются ценой в двадцать раз."""


def build_provider(ahrefs_client: AhrefsClient) -> SerpProvider:
    """Источник выдачи по настройке `SERP_PROVIDER`.

    Клиент Ahrefs передаётся всегда: он нужен запасному источнику, а
    заводить его внутри значит создавать второе подключение к тому же
    провайдеру.
    """
    name = (cfg.PROVIDER or "").strip().lower()

    if name == DATAFORSEO:
        if not cfg.LOGIN or not cfg.PASSWORD:
            raise UnknownProviderError(
                "SERP_PROVIDER=dataforseo, но SERP_LOGIN или SERP_PASSWORD пусты. "
                "Заполнить или переключить на ahrefs — он дороже в двадцать раз, "
                "но работает на том же ключе, что и метрики"
            )
        if cfg.SANDBOX:
            logger.warning(
                "выдача: включена ПЕСОЧНИЦА провайдера — ответы выдуманные, "
                "доноры из них не годятся ни для чего, кроме проверки проводки"
            )
        return DataForSeoProvider()

    if name == AHREFS:
        logger.info("выдача: запасной источник, дороже основного примерно в двадцать раз")
        return AhrefsSerpProvider(ahrefs_client)

    raise UnknownProviderError(
        f"SERP_PROVIDER=«{cfg.PROVIDER}» — такого источника нет. Известные: {', '.join(KNOWN)}"
    )
