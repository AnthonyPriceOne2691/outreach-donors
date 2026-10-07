"""Тревоги сторожа — человеку в Telegram, по смене состояния (Ф4, срез 4.5b).

Тревоги сторожа тишины шли в журнал и на экран (`GET /api/watchdog`), но не в Telegram.
Теперь каждая новая и каждая прошедшая — одно сообщение общим каналом (`shared/alerts.py`,
`ALERT_TELEGRAM_*`), как у `scripts/healthwatch.sh`: о разнице с прошлым проходом, а не на
каждом — иначе не читают ни одного. **Бот не настроен** — громкая строка «ТРЕВОГА НЕ
ОТПРАВЛЕНА» в журнале, тоже по смене состояния. **Telegram не принял** — следующий проход
скажет ещё раз. Сказанное помнит процесс сторожа: после перезапуска действующие тревоги
приходят снова — это и есть сводка «что сломано сейчас».
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from backend.config import alerts as alerts_cfg
from backend.features.ops.alarms import Alarm
from backend.shared.alerts import send_alert


@dataclass
class Feed:
    """Тревоги, о которых уже сказано: код → заголовок."""

    told: dict[str, str] = field(default_factory=dict)

    async def tell(self, found: Sequence[Alarm]) -> None:
        """Сказать о новых тревогах и о прошедших — по разнице с прошлым проходом."""
        now = {alarm.code: alarm for alarm in found}
        for code, alarm in now.items():
            if code not in self.told and await _said(f"тревога: {alarm.title}. {alarm.detail}"):
                self.told[code] = alarm.title
        for code in [code for code in self.told if code not in now]:
            if await _said(f"прошло: {self.told[code]}"):
                del self.told[code]


async def _said(text: str) -> bool:
    """Ушло ли слово: в Telegram — или громкой строкой журнала, если бот не настроен."""
    sent = await send_alert(text)
    return sent or not (alerts_cfg.TELEGRAM_BOT_TOKEN and alerts_cfg.TELEGRAM_CHAT_ID)
