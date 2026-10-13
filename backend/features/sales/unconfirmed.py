"""Адрес не подтверждён: проверяльщик ответил `unknown` — к письмам такой лид не идёт.

Решение владельца 13.10 (ревью стыков: база и проверка адресов). `unknown` — почтовый сервер
не дал проверке определённого ответа: лежал, отложил ответ, не даёт проверять ящики. Письмо на
такой адрес чаще других возвращается отказом доставки, а отказы бьют по репутации молодых
доменов продаж — ресурсу, который не восстанавливается. `accept_all` («сервер принимает любой
адрес») к письмам идёт, как раньше: его отказы видны в недельном отчёте. Порога уверенности нет —
годность решает слово провайдера.

**Перепроверка — одна, не раньше чем через `verifier.RECHECK_DAYS` дней.** Hunter отвечает
`unknown` и временно, поэтому первый такой ответ не окончательный: вердикт записан лиду, слова
называют день перепроверки, до него очистка лида не берёт (`verifier.waits`), а в свой день
проверяет снова — той же платной проверкой под тем же месячным потолком (`verify_cap.py`).
Второй `unknown` того же проверяльщика окончателен: лид отклонён с причиной `unconfirmed`, и
больше его не проверяют. Лид стоит не больше двух проверок — вечного круга нет.

**Статус при первом `unknown` — прежний**, как у отказа сервиса (`cleaning._unverified`):
- `new` остаётся `new`: лид не решён — не в очереди писем и не в счёте «готовы»;
- `ready` (перепроверка выдуманного вердикта при живом проверяльщике, `verifier.recheck`)
  остаётся `ready`: снятый лид, которому первое письмо уже ушло, потерял бы цепочку навсегда —
  проход добивок её закрыл бы, черновик ответа не писался бы (`mail.unwritable`). Его первое
  письмо держит `first_letter`: сборка его не собирает, отправка не пускает, а в свой день
  очистка решит его (`verifier.again`). Письмо уже ушло — цепочка идёт без перепроверки: адрес
  его принял, а отказ доставки остановит её сам.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import ColumnElement, false, or_

from backend.features.sales.models import LeadStatus, RejectionReason, SalesLeadModel
from backend.features.sales.verifier import FIXTURE, RECHECK_DAYS, UNKNOWN, live, unproven

#: Второй `unknown` того же проверяльщика — причина отказа словами.
TWICE = (
    "адрес не подтверждён и при повторной проверке (unknown) — к письмам не идёт; "
    "другой адрес компании можно загрузить новым лидом"
)
#: Почему первое письмо лида не уходит — словами отчёта сборки и отказа отправки.
FIRST_LETTER = (
    "адрес не подтверждён проверкой (unknown) — первое письмо не уходит; "
    "очистка проверит адрес снова"
)


def recheck_on(moment: datetime) -> date:
    """День, не раньше которого очистка спросит снова про адрес с первым `unknown`, — по UTC,
    как у `verifier.waits`: один день у слов лида, итога консоли и строки задачи."""
    return (moment + timedelta(days=RECHECK_DAYS)).date()


def judged(
    lead: SalesLeadModel, verifier: str, moment: datetime
) -> tuple[LeadStatus, RejectionReason | None, str]:
    """Куда лид, чей адрес проверяльщик `verifier` назвал `unknown`: второй раз — отклонён;
    первый — статус прежний, а слова называют день перепроверки."""
    if lead.verification_status == f"{verifier}:{UNKNOWN}":
        return LeadStatus.REJECTED, RejectionReason.UNCONFIRMED, TWICE
    said = f"адрес не подтверждён: почтовый сервер не дал проверке ответа ({UNKNOWN})"
    day = f"очистка проверит снова не раньше {recheck_on(moment):%d.%m.%Y}"
    if lead.status is LeadStatus.READY:
        written = "если оно уже ушло, цепочка идёт без перепроверки"
        return LeadStatus.READY, None, f"{said} — первое письмо не уходит, {day}; {written}"
    return LeadStatus.NEW, None, f"{said} — к письмам не идёт; {day}"


def first_letter(lead: SalesLeadModel) -> str | None:
    """Почему первое письмо лида не уходит из-за адреса — словами; `None` — уходит: адрес
    проверен не выдуманным проверяльщиком при живом (`verifier.unproven`) и не `unknown`.
    Одно правило у сборки очереди (`queue._plan`) и отправки (`mail._first_problem`):
    добивки и ответы идут — цепочку с ушедшим первым письмом `unknown` не обрывает."""
    if (made_up := unproven(lead)) is not None:
        return made_up
    return FIRST_LETTER if (lead.verification_status or "").endswith(f":{UNKNOWN}") else None


def holds() -> ColumnElement[bool]:
    """Лиды, чьё первое письмо держит адрес, — то же правило, что `first_letter`, условием SQL:
    по нему автосборка не берёт гипотезу ради лидов, которых сборка всё равно пропустит
    (`autobuild.candidates`). Пустой вердикт — не держит."""
    status = SalesLeadModel.verification_status
    made_up = status.startswith(f"{FIXTURE}:") if live() else false()
    return status.is_not(None) & or_(status.endswith(f":{UNKNOWN}"), made_up)
