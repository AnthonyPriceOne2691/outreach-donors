"""Воронка отбора: где кончились адресаты этапа — ступенями и словами.

Ступени считает отбор (`recipients.py`), здесь — что они значат и как
читаются человеком. Пустая очередь при «все уже написаны» и при «ни у кого
нет адреса» выглядит одинаково, и различает их только воронка.

**«Ещё не писали» — и те, кому прежние письма не дошли.** Не дошедшее
письмо донор не видел, и следующее уходит на следующий адрес
(`attempts.py`). Сколько таких, и у скольких адреса кончились, говорят
две строки рядом с последней ступенью — когда есть о чём сказать.

**«Вне «кому не пишем»» — ступень, когда она кого-то вычеркнула.** Принятые
до 11.10.2026 крупные сайты (DR > 80) и СМИ писем не получают по ТЗ
(`runs.exclusions.not_writing`); новые до приёма не доходят. Ступень с тем же числом,
что «принятых», читалась бы местом отсева, где никого не отсеяли.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Ступень воронки доноров: принятые без «кому не пишем» ТЗ — словами экрана
#: (`frontend/src/letters/targets.ts`).
NOT_WRITING_STEP = "вне «кому не пишем»"


def with_earlier(steps: dict[str, int], *, next_address: int, exhausted: int) -> dict[str, int]:
    """Две строки о прежних письмах — только когда есть о чём сказать.

    Это не ступени, а разбор последней: «из них на следующий адрес» — часть
    «ещё не писали», «адреса кончились» — часть тех, кто её не прошёл.
    Нули в каждой воронке читались бы как ступени, на которых кто-то отсеялся.
    """
    if next_address:
        steps["из них на следующий адрес"] = next_address
    if exhausted:
        steps["адреса кончились"] = exhausted
    return steps


@dataclass(frozen=True, slots=True)
class Funnel:
    """Сколько доноров отсеялось на каждой ступени отбора."""

    suitable: int
    accepted: int
    #: Из принятых — вне «кому не пишем» ТЗ: без крупных сайтов и СМИ.
    writable: int
    with_contact: int
    not_suppressed: int
    not_written: int
    #: Из «ещё не писали» — те, кому прежние письма не дошли.
    next_address: int = 0
    #: Прежние письма не дошли, а писать больше некуда: живые адреса
    #: кончились или остались только в стоп-листе, или выбран потолок.
    exhausted: int = 0

    def as_report(self) -> dict[str, int]:
        steps = {"подходящих": self.suitable, "принятых": self.accepted}
        if self.writable < self.accepted:
            steps[NOT_WRITING_STEP] = self.writable
        steps |= {
            "с адресом": self.with_contact,
            "вне стоп-листа": self.not_suppressed,
            "ещё не писали": self.not_written,
        }
        return with_earlier(steps, next_address=self.next_address, exhausted=self.exhausted)


@dataclass(frozen=True, slots=True)
class AdvertiserFunnel:
    """Воронка Этапа 2: где кончились рекламодатели.

    Ступени свои, потому что и вопросы свои. «Нет ссылки» значит, что
    письмо не под что писать; «цена донора протухла» — что оффер «мы
    дешевле» держится на числе, которому больше 150 дней, и сначала
    нужен перезапрос цены, а не письмо.
    """

    advertisers: int
    with_link: int
    fresh_price: int
    with_contact: int
    not_suppressed: int
    not_written: int
    next_address: int = 0
    exhausted: int = 0

    def as_report(self) -> dict[str, int]:
        return with_earlier(
            {
                "рекламодателей": self.advertisers,
                "со ссылкой": self.with_link,
                "цена донора свежая": self.fresh_price,
                "с адресом": self.with_contact,
                "вне стоп-листа": self.not_suppressed,
                "ещё не писали": self.not_written,
            },
            next_address=self.next_address,
            exhausted=self.exhausted,
        )
