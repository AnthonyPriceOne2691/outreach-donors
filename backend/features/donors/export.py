"""Выгрузка доноров в CSV.

**Колонки те же, что на экране, и в том же порядке.** Файл — это
не другой отчёт, а тот же, вынесенный из браузера: разойдись они,
и первый же вопрос будет «почему в файле другое».

**Разделитель — точка с запятой, кодировка — UTF-8 с меткой.** Обе
уступки Excel: с запятой он раскладывает русские строки в одну
колонку, без метки — показывает кракозябры. Файл открывают в Excel,
а не в редакторе, и спорить с этим дороже, чем уступить.

**Слова — те же, что на экране.** До 25.09.2026 вердикт, исход поиска
и регион уходили в файл кодами (`unsuitable`, `found`, `us`), а доля
региона — сырым числом `0.19888…`. Экран называет их «не подходит»,
«адрес найден», «США» и «20%», и файл рядом с ним читался бы как
выгрузка из другой системы. Слова берутся там же, где их берёт причина
отсева (`donors/wording.py`).

**Потолок строк — один, и у отмеченных тоже** (26.09.2026). Файл —
не больше `EXPORT_LIMIT` строк: выгрузка всей базы одним ответом однажды
положит сервер ровно в тот момент, когда его попросят об отчёте. Номеров
отмеченных — не больше столько же, и отказ говорит это словами, а не
файлом, в котором молча не хватает строк.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable, Sequence
from typing import Any

from backend.features.core.domain import DonorStatus
from backend.features.donors.browse import DonorRow
from backend.features.donors.geo import stored_shares
from backend.features.donors.wording import (
    CONTACT_STATUS_TITLES,
    DONOR_STATUS_TITLES,
    NOT_SEARCHED,
    REVIEW_TITLES,
    SELLER_ANSWER_TITLES,
    countries_text,
    country_title,
    reject_reason_text,
    share_text,
)
from backend.shared.spreadsheet import inert

#: Потолок строк файла и числа отмеченных номеров. Экран получает его
#: с сервера (`DonorsPage.export_limit`) и не обещает больше.
EXPORT_LIMIT = 10_000


class PickRefusedError(ValueError):
    """Отмеченных не выгрузить: ни одного или больше потолка. Текст говорит почему."""


def _spaced(number: int) -> str:
    """Число с разрядами, как на экране: «10 000»."""
    return f"{number:,}".replace(",", "\u00a0")


def checked_picks(ids: Sequence[int]) -> list[int]:
    """Номера отмеченных — или отказ словами."""
    unique = list(dict.fromkeys(ids))
    if not unique:
        raise PickRefusedError("Не отмечено ни одного донора — выгружать нечего.")
    if len(unique) > EXPORT_LIMIT:
        raise PickRefusedError(
            f"Отмечено {_spaced(len(unique))} — в файл за раз идёт не больше "
            f"{_spaced(EXPORT_LIMIT)}. Снимите часть отметок или выгрузите найденных фильтром."
        )
    return unique


#: Заголовок и поле строки таблицы. Порядок — как на экране.
COLUMNS: tuple[tuple[str, str], ...] = (
    ("домен", "host"),
    ("вердикт", "status"),
    ("причина отсева", "reject_reason"),
    ("DR", "dr"),
    ("трафик", "org_traffic"),
    ("регион", "geo"),
    ("доля региона", "geo_top_share"),
    ("адресов", "contacts"),
    ("исход поиска", "contact_status"),
    ("цена", "price"),
    ("валюта", "last_price_currency"),
    ("метрики от", "metrics_refreshed_at"),
    # С 12.10.2026 — причина «не проверен» своей колонкой (до того она стояла в
    # «причине отсева»). Сразу за прежними колонками, а не рядом с причиной отсева:
    # прежние колонки не сдвигаются — правило ниже.
    ("почему не проверен", "unchecked_reason"),
    # С 12.10.2026 (сверка ТЗ: «белая цена, серая цена, способы оплаты») — решение
    # человека, вся цена и её срок. В конце, а не рядом с ценой: файл читают и
    # таблицы с формулами по номерам колонок, и прежние колонки не сдвигаются.
    ("решение", "review"),
    ("белая цена", "last_price_white"),
    ("серая цена", "last_price_grey"),
    ("цена от", "last_price_at"),
    ("цена свежая до", "price_fresh_until"),
    ("оплата", "last_payment_methods"),
    ("срок, дней", "last_placement_days"),
    ("ссылка", "last_link_type"),
    # С 16.10.2026 — все страны топ-5 с долями, как под верхней в колонке «Гео» экрана
    # (ТЗ: «org_traffic по топ-5 странам»). В конце — по тому же правилу.
    ("страны (топ-5)", "top_countries"),
)


def _top_countries(row: DonorRow) -> str:
    """«США 55%; Великобритания 12%; …» — разбивка донора; у неполной — с пометкой."""
    donor = row.donor
    return countries_text(stored_shares(donor.geo_breakdown), partial=donor.geo_partial)


#: Поля, которых нет ни у строки, ни у донора: файл собирает их из нескольких колонок.
_COMPUTED: dict[str, Callable[[DonorRow], Any]] = {"top_countries": _top_countries}


#: Причина вердикта — колонкой своего вердикта (12.10.2026): у «не проверен» своя
#: подпись, «причина отсева» у домена, которого никто не отсеивал, читалась бы приговором.
_REASON_OF: dict[str, DonorStatus] = {
    "reject_reason": DonorStatus.UNSUITABLE,
    "unchecked_reason": DonorStatus.UNCHECKED,
}


#: Поля, которые экран показывает словами, а не как хранятся. Остальные
#: уходят как есть: числа и даты; цена — числом, а без неё — словами ответа.
WORDS: dict[str, Callable[[Any], Any]] = {
    "status": lambda status: DONOR_STATUS_TITLES[status],
    "reject_reason": reject_reason_text,
    "unchecked_reason": reject_reason_text,
    "geo": country_title,
    "geo_top_share": share_text,
    # Пусто — не «пусто», а «не искали»: «не нашли» и «не искали» решаются
    # по-разному, и на экране у них разные слова.
    "contact_status": lambda status: (
        NOT_SEARCHED if status is None else CONTACT_STATUS_TITLES[status]
    ),
    # Цену снял ответ донора — его словами экрана: «не продаёт», «берёт бесплатно»
    # (`DonorRow.price`, решение Anthony 11.10.2026). Цена — числом, как была.
    "price": lambda price: SELLER_ANSWER_TITLES[price] if isinstance(price, str) else price,
    # Решения ещё не было — «не решали», а не пустая ячейка: пусто читалось бы «не знаем».
    "review": lambda review: REVIEW_TITLES.get(review or "undecided", review),
    "last_payment_methods": lambda methods: ", ".join(methods) if methods else None,
}


def to_csv(rows: Sequence[DonorRow]) -> bytes:
    """Строки таблицы доноров одним файлом."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow([title for title, _ in COLUMNS])
    for row in rows:
        writer.writerow([_cell(_said(field, _value(row, field))) for _, field in COLUMNS])
    # Метка порядка байтов — ради Excel, см. модуль.
    return buffer.getvalue().encode("utf-8-sig")


def _said(field: str, value: Any) -> Any:
    """Значение поля словами экрана, если экран говорит о нём словами."""
    words = WORDS.get(field)
    return value if words is None else words(value)


def _value(row: DonorRow, field: str) -> Any:
    """Поле строки: домен и число адресов — у самой строки, остальное — у донора.

    До 25.09.2026 значение бралось только у строки — с умолчанием «пусто»,
    а у неё из двенадцати колонок есть две. Файл выходил с доменами и
    пустыми вердиктами, причинами, DR и трафиком; тесты смотрели только на
    заголовок и на домены, а с экрана выгрузка не скачивалась вовсе, и
    содержимое файла никто не видел. Умолчания здесь больше нет: поле,
    которого нет ни у строки, ни у донора, — ошибка, а не пустая ячейка.
    """
    if field in _COMPUTED:
        return _COMPUTED[field](row)
    if field in _REASON_OF:
        return row.donor.reject_reason if row.donor.status is _REASON_OF[field] else None
    if hasattr(row, field):
        return getattr(row, field)
    return getattr(row.donor, field)


def _cell(value: Any) -> str:
    """Пусто — пустая ячейка, а не «None»: файл читает человек. Строка — такой, что
    таблица её не исполнит (`shared/spreadsheet.inert`): оплата и валюта — слова
    из письма донора или вписанные человеком, и `=HYPERLINK(...)` в них — формула."""
    if value is None:
        return ""
    if hasattr(value, "value"):  # перечисления отдаём их значением
        return str(inert(str(value.value)))
    if hasattr(value, "date"):  # даты — без часов, они здесь не нужны
        return str(value.date())
    if isinstance(value, str):
        return str(inert(value))
    return str(value)
