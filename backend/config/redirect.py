"""Переадресация в диалогах рекламодателей: рубильник (`replies/lead_redirect.py`, 16.11.2026).

Свой модуль, а не `config/outreach.py`: настройка одна, и читают её только приём ответов, проход
переадресации и его команда.

**Включена по умолчанию** (слово владельца 16.11.2026: «да, делай сегодня»). Включённая — ответ
человека в диалоге рекламодателя, который называет чужой адрес, при приёме ставится на разбор, а
проход сам шлёт то же первое письмо на названный адрес. Выключенная — приём не ставит, проход не
идёт, письма, уже заведённые, ждут включения; разовый проход по ждущим человека диалогам —
командой `outreach lead-redirect`, она работает и при выключенной.

Опечатка в значении (`OUTREACH_LEAD_REDIRECT=да`) не роняет процессы, которые грузят настройку
(`_base.spared`): берётся «выключена», и при старте — громкая строка журнала с именем переменной.
"""

from __future__ import annotations

from pydantic import Field

from backend.config._base import DomainSettings, Fallback, spared


class _Redirect(DomainSettings):
    enabled: bool = Field(default=True, validation_alias="OUTREACH_LEAD_REDIRECT")


_s, _unread = spared(
    _Redirect,
    {
        "OUTREACH_LEAD_REDIRECT": Fallback(
            False,
            wants="true или false",
            means="переадресация в диалогах рекламодателей выключена, пока её не поправят",
        )
    },
)

ENABLED: bool = _s.enabled
