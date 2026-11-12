"""Итог перевода кандидатов в рекламодатели — числами по причинам (`crawl/promote.py`).

Своим модулем: перевод упёрся в потолок длины файла, а итог — отдельная вещь. Его читают журнал
действий (запись «перевод в рекламодатели»), консоль (`outreach advertisers-promote`) и ответ
кнопки «Перевести».

**Чьё «пишем» — человека или судьи** (12.11.2026). С 11.11.2026 решение «пишем» ставит и судья
(`judge_apply.py`, автор решения — `судья:<партия>`), а итог считал любое «пишем» в «подтверждено
человеком»: журнал и консоль говорили о руках человека там, где решал судья. Теперь они считаются
порознь, и отметка «прошёл через руки человека» у строки рекламодателя
(`AdvertiserModel.confirmed_by_human`) ставится только решением человека.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.features.core.models.advertiser import CandidateModel
from backend.features.crawl.judge_mark import batch_of


@dataclass(slots=True)
class PromotionReport:
    """Что случилось при переводе. Числа, а не «готово».

    Отказы считаются по причинам: «ни одного рекламодателя» и «все
    отсеяны стоп-листом поставщиков» — разные новости, и вторая
    означает, что список стоит перечитать.
    """

    considered: int = 0
    promoted: int = 0
    updated: int = 0
    skipped_supplier: int = 0
    skipped_suppressed: int = 0
    removed: int = 0
    by_human: int = 0
    #: «Пишем» поставил судья, а не человек (`judge_mark.py`).
    by_judge: int = 0
    #: Домены, ждущие DR: к переводу готовы, но крупный ли сайт, неизвестно.
    waiting_dr: int = 0
    hosts: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, int]:
        return {
            "рассмотрено": self.considered,
            "заведено": self.promoted,
            "обновлено": self.updated,
            "донор в стоп-листе поставщиков": self.skipped_supplier,
            "адресат в общем стоп-листе": self.skipped_suppressed,
            "снято с уже заведённых": self.removed,
            "подтверждено человеком": self.by_human,
            "подтверждено судьёй": self.by_judge,
            "ждут проверки DR": self.waiting_dr,
        }

    def count_decision(self, candidate: CandidateModel) -> bool:
        """Учесть, чьё «пишем» у переведённого кандидата, и сказать, человека ли оно: «куплено»
        без решения и «пишем» судьи — не руки человека."""
        if candidate.confirmed is not True:
            return False
        by_judge = batch_of(candidate.decided_by) is not None
        self.by_judge += int(by_judge)
        self.by_human += int(not by_judge)
        return not by_judge
