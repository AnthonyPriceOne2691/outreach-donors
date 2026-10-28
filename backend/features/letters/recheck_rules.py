"""Можно ли перезапросить цену донора — условия до плана письма и их слова.

Решается по фактам, прочитанным разом (`recheck_facts.load`), без базы. Условия идут от
того, что не лечится вовсе, к тому, что пройдёт само (`refusal`):

1. **Не в «кому не пишем» ТЗ** (`runs.exclusions`, решение владельца 28.10.2026) —
   крупный сайт по DR выше порога версии и домен из списков (зона, платформа, соцсеть,
   крупное СМИ, справочник): им перезапрос не уходит ни новым письмом, ни в переписку,
   даже принятым до правила 11.10.2026 или чей DR вырос после принятия. Первым — потому
   что не лечится ничем: принять такого нельзя (`NotWritingAcceptError`), цену руками
   не завести, и отказ «не донор» или «цены нет» звал бы к тому, что тоже откажет.
2. **Донор** — принятый человеком и годный по порогам, как у обхода (`crawl/targets`):
   другому свежая цена ни к чему, Этап 2 его не обходит.
3. **Цена протухла** — есть и старше `PRICE_TTL_DAYS`: свежую перезапрашивать незачем,
   а без цены перезапрашивать нечего. Граница та же, что у обхода, — «протухла» здесь
   и там значит одно.
4. **Не поставщик агентства** — его рекламодателей не трогаем, Этап 2 по нему не идёт.
5. **Не в стоп-листе целиком** — отписка кнопкой и запись человека закрывают сайт.
6. **Одно письмо донору за раз** — в очереди или в пути ничего нет; исключение —
   перезапрос в переписку, который однажды не ушёл: он и уйдёт.
7. **Не чаще раза в `PRICE_RECHECK_DAYS` дней** — по времени, когда ушёл последний
   перезапрос, любым путём.

**Чужой этап — без подробностей тому, кто его не видит** (проверка 12.10.2026): письмо
лиду продаж называется номером и этапом только с правом «Продажи» (`sales_visible`).

Куда уйдёт письмо и что ещё может ему помешать — `recheck_target.py`.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from backend.config import filters as filters_cfg
from backend.config import outreach as cfg
from backend.features.core.domain import DonorStatus, MessageStatus, Stage
from backend.features.core.models.donor import DonorModel
from backend.features.donors.standing import donor_now
from backend.features.letters.chain import FIRST_STEP, RECHECK_CAMPAIGN, kind_of
from backend.features.letters.recheck_facts import Facts, Waiting

#: Рассылка перезапросов новым первым письмом — по этой отметке (`campaigns.template_id`),
#: а не по имени: имя видит человек, и так же он может назвать свою рассылку. По ней же
#: отправка не ставит добивок (`settle._next_due`).
CAMPAIGN_TEMPLATE = RECHECK_CAMPAIGN


def refusal(facts: Facts, donor: DonorModel, host: str, *, sales_visible: bool) -> str | None:
    """Первое невыполненное условие перезапроса — словами; `None` — все выполнены."""
    said = _standing(facts, donor, host) or _not_stale(donor, host, facts.moment)
    if said is None and host.lower() in facts.suppliers:
        said = (
            f"{host} — донор-поставщик агентства: Этап 2 по нему не запускается, "
            "и перезапрашивать цену незачем."
        )
    if said is None and donor.domain_id in facts.stopped_domains:
        said = f"{host} в стоп-листе: писать ему нельзя. Снять запись — на экране «Стоп-лист»."
    if said is None:
        pending = _pending(facts, donor.domain_id)
        said = (
            _too_soon(host, facts.asked.get(donor.domain_id), facts.moment)
            if pending is None
            else _pending_words(host, pending, sales_visible=sales_visible)
        )
    return said


def _standing(facts: Facts, donor: DonorModel, host: str) -> str | None:
    """Донор ли это вообще — тем же отбором, что у обхода (`crawl/targets.choose`): не из
    «кому не пишем», принятый человеком и годный по порогам. «Кому не пишем» — причиной
    словами правила (`exclusions.not_writing_of`): перезапрос — наше письмо, а не ответ
    донору, и правило держит его на обоих путях."""
    off = facts.not_writing.get(host)
    if off is not None:
        return (
            f"{host} — из «кому не пишем» ({off}): цену ему не перезапрашивают ни новым "
            "письмом, ни в переписке."
        )
    if not donor_now(donor):
        return f"{host} — не донор: цену перезапрашивают только у донора, принятого человеком."
    if donor.status is not DonorStatus.SUITABLE:
        return f"{host} не прошёл пороги отбора: Этап 2 его не обходит, и цена ему ни к чему."
    return None


def _not_stale(donor: DonorModel, host: str, moment: datetime) -> str | None:
    """Цены нет или она свежая — перезапрашивать нечего. Граница — та же, что у обхода."""
    priced_at = donor.last_price_at
    if donor.last_price is None or priced_at is None:
        return (
            f"У {host} цены нет — перезапрашивать нечего. Цену спрашивает первое письмо "
            "Этапа 1; знаете её сами — укажите на карточке донора."
        )
    ttl = timedelta(days=filters_cfg.PRICE_TTL_DAYS)
    if priced_at >= moment - ttl:
        return (
            f"Цена {host} свежая: названа {priced_at:%d.%m.%Y}, перезапрос понадобится "
            f"с {priced_at + ttl:%d.%m.%Y}, когда ей исполнится {days(ttl.days)}."
        )
    return None


def _pending(facts: Facts, domain_id: int) -> Waiting | None:
    """Письмо донору в очереди или в пути — кроме перезапроса, который уйдёт сейчас."""
    queued = facts.queued.get(domain_id)
    return next(
        (
            letter
            for letter in facts.waiting.get(domain_id, [])
            if queued is None or letter.id != queued.id
        ),
        None,
    )


def _pending_words(host: str, letter: Waiting, *, sales_visible: bool) -> str:
    """Письмо донору уже в пути или в очереди — второе не уходит, словами о нём."""
    if letter.stage is Stage.SALES and not sales_visible:
        return f"Сайту {host} сейчас идёт другое письмо: второе не уходит, пока не уйдёт оно."
    what = f"Письмо {host} №{letter.id} ({kind_of(letter.step)})"
    if letter.status is MessageStatus.SENDING:
        return (
            f"{what} отправляется, и исход его неизвестен: второе письмо донору не уходит, "
            "пока исход не решат на экране «Письма»."
        )
    if letter.step == FIRST_STEP:
        return f"{what} ждёт отправки в очереди писем: второе не уходит, пока не уйдёт оно."
    return f"{what} ждёт отправки своим путём: второе не уходит, пока не уйдёт оно."


def _too_soon(host: str, asked_at: datetime | None, moment: datetime) -> str | None:
    """Перезапрос не чаще раза в `PRICE_RECHECK_DAYS` дней — словами, когда снова можно."""
    if asked_at is None:
        return None
    again = asked_at + timedelta(days=cfg.PRICE_RECHECK_DAYS)
    if moment >= again:
        return None
    return (
        f"Цену {host} уже перезапросили {asked_at:%d.%m.%Y} — повторно не раньше "
        f"{again:%d.%m.%Y}: перезапрос — не чаще раза в {days(cfg.PRICE_RECHECK_DAYS)}."
    )


def days(count: int) -> str:
    """«14 дней», «1 день», «3 дня» — число дней словами."""
    tail = count % 100
    if 11 <= tail <= 14:
        return f"{count} дней"
    match count % 10:
        case 1:
            return f"{count} день"
        case 2 | 3 | 4:
            return f"{count} дня"
        case _:
            return f"{count} дней"
