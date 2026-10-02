"""Счётчики прогона контактов — строки отчёта, а не состояние лестницы.

Вынесено отдельным модулем: лестница отвечает на вопрос «где взять адрес»,
счётчики — на вопрос «сколько это стоило и что дало». Второе читают и там,
где лестницы нет: поиск пачкой печатает по ним отчёт, и в прогоне по файлу
они же говорят, работала ступень или молчала.

`entered` и `found` по каждой ступени считаются раздельно намеренно: их
отношение и есть отдача ступени, ради которой выбран порядок.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class StepCounters:
    """Счётчики по ступеням — строки отчёта прогона.

    `entered` и `found` считаются раздельно по каждой ступени: их отношение
    и есть отдача ступени, ради которой выбран порядок.
    """

    mx_checked: int = 0
    # Домен не принимает почту: ни MX, ни A (спуск прекращён, если так велено)
    # или нулевой MX (спуск идёт за сторонними адресами — не прекращается).
    mx_stopped: int = 0
    # DNS не ответил. Отдельное число, потому что ступень, не отвечающая
    # ни по одному домену, выглядит как работающая: она ничего не отсеяла.
    mx_unknown: int = 0
    pages_entered: int = 0
    pages_found: int = 0
    pages_fetched: int = 0  # всего запросов к сайтам: цена ступени
    pages_blocked: int = 0  # сайтов, закрывшихся от нас (401/403/429)
    browser_entered: int = 0
    browser_found: int = 0
    rdap_entered: int = 0
    rdap_found: int = 0
    rdap_failed: int = 0
    provider_entered: int = 0  # столько раз платили
    provider_found: int = 0
    #: Столько раз ступень отказала — отдельно от «не нашли». Живой прогон
    #: 22.09.2026 показал, зачем: учётка была закрыта, ступень отказывала
    #: на каждом домене, а отчёт печатал «вошло 2, нашли 0» — то есть
    #: неотличимо от «провайдер этих доменов не знает».
    provider_refused: int = 0
    provider_refusal: str = ""  # чем именно отказала, дословно
    form_only: int = 0
    manual_queued: int = 0
    not_found: int = 0
    #: Сайт не ответил — повтор по сроку. Отдельно от «не нашли»: ступень,
    #: которая молча не достучалась, иначе выглядела бы работающей.
    no_answer: int = 0
    rejected_emails: int = 0  # адреса, отсеянные фильтром качества

    def mark_found(self, step: str) -> None:
        """Записать, что адрес дала именно эта ступень."""
        if step == "pages":
            self.pages_found += 1
        elif step == "rdap":
            self.rdap_found += 1
        elif step == "provider":
            self.provider_found += 1
        elif step == "browser":
            self.browser_found += 1

    def as_report(self) -> dict[str, int]:
        return {
            "mx_checked": self.mx_checked,
            "mx_stopped": self.mx_stopped,
            "mx_unknown": self.mx_unknown,
            "pages_entered": self.pages_entered,
            "pages_found": self.pages_found,
            "pages_fetched": self.pages_fetched,
            "pages_blocked": self.pages_blocked,
            "browser_entered": self.browser_entered,
            "browser_found": self.browser_found,
            "rdap_entered": self.rdap_entered,
            "rdap_found": self.rdap_found,
            "rdap_failed": self.rdap_failed,
            "provider_entered": self.provider_entered,
            "provider_found": self.provider_found,
            "provider_refused": self.provider_refused,
            "form_only": self.form_only,
            "manual_queued": self.manual_queued,
            "not_found": self.not_found,
            "no_answer": self.no_answer,
            "rejected_emails": self.rejected_emails,
        }
