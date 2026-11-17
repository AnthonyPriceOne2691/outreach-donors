"""Автокруг рекламодателей: рубильник (`crawl/auto_round.py`, 17.11.2026).

Свой модуль, а не `config/advertisers.py`: там пороги отбора, а здесь — можно ли сервису самому
переводить, искать адреса, собирать и отправлять. Настройка одна, и читают её только автокруг,
сторож и экран «Рекламодатели».

**Рубильник, а не включатель** — как `OUTREACH_AUTOSEND`. Включён по умолчанию, но сам автокруг
этим не включается: его включает человек выключателем на экране, с суточным потолком писем.
Выключенный рубильник — автокруг не идёт и выключателя на экране нет, начатый круг стоит на своём
шаге, пока рубильник не включат; всё, что круг делал, делается кнопками, как до него.

Опечатка в значении (`ADVERTISERS_AUTO_ROUND=да`) не роняет процессы, которые грузят настройку
(`_base.spared`): берётся «выключен», и при старте — громкая строка журнала с именем переменной.
"""

from __future__ import annotations

from pydantic import Field

from backend.config._base import DomainSettings, Fallback, spared


class _AutoRound(DomainSettings):
    enabled: bool = Field(default=True, validation_alias="ADVERTISERS_AUTO_ROUND")


_s, _unread = spared(
    _AutoRound,
    {
        "ADVERTISERS_AUTO_ROUND": Fallback(
            False,
            wants="true или false",
            means="автокруг рекламодателей не идёт, пока настройку не поправят",
        )
    },
)

ENABLED: bool = _s.enabled
