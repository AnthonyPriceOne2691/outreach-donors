"""Выгрузка рекламодателей в CSV — те же колонки, порядок и слова, что в списке.

По образцу выгрузки доноров (`donors/export.py`): файл — не другой отчёт, а тот же,
вынесенный из браузера; точка с запятой и UTF-8 с меткой — уступки Excel, пусто —
пустая ячейка, а не «None»; потолок строк — один, и сколько не легло, говорят
заголовки ответа, а не файл, в котором молча не хватает строк.

**Текст с чужих страниц в ячейке не исполняется.** Анкор и адрес страницы сняты с сайта
донора, и анкор вида `=HYPERLINK(…)` таблица выполнила бы при открытии файла (OWASP
«CSV Injection», как у выгрузки лидов, `replies/lead_handoff.py`): такой ячейке —
ведущий апостроф.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence

from backend.features.core.domain import ContactStatus
from backend.features.crawl.advertiser_list import AddressState, AdvertiserRow
from backend.features.crawl.niche import NICHE
from backend.features.crawl.offer_state import OFFER_TITLES
from backend.features.donors.wording import CONTACT_STATUS_TITLES, NOT_SEARCHED

#: Потолок строк файла. Экран получает его с сервера и не обещает больше.
EXPORT_LIMIT = 10_000

#: Заголовки — в порядке колонок экрана. «За что» — причины балла через «; »: ТЗ просит
#: найденных рекламодателей «со скорингом и причиной».
COLUMNS = (
    "рекламодатель",
    "откуда",
    "донор",
    "страница",
    "анкор",
    "балл",
    "за что",
    "адрес",
    "письмо",
)

#: С чего начинается формула в Excel и Google Sheets (OWASP «CSV Injection»).
_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def source_words(row: AdvertiserRow) -> str:
    """Откуда рекламодатель — словами экрана."""
    if row.source != NICHE:
        return "ссылка на доноре"
    return "бизнес ниши" if row.run_id is None else f"бизнес ниши, прогон №{row.run_id}"


def address_words(row: AdvertiserRow) -> str:
    """Есть ли куда писать — словами экрана: адрес в базе сильнее исхода поиска,
    а без адреса — что сказал последний поиск."""
    if row.address is AddressState.FOUND:
        return CONTACT_STATUS_TITLES[ContactStatus.FOUND]
    if row.address is AddressState.FORM:
        return CONTACT_STATUS_TITLES[ContactStatus.FORM_ONLY]
    if row.contact_status is None:
        return NOT_SEARCHED
    if row.contact_status in (ContactStatus.FOUND, ContactStatus.FORM_ONLY):
        # Исход поиска называет адрес или форму, которых в базе уже нет.
        return CONTACT_STATUS_TITLES[ContactStatus.NOT_FOUND]
    return CONTACT_STATUS_TITLES[row.contact_status]


def _values(row: AdvertiserRow) -> tuple[object, ...]:
    niche = row.source == NICHE
    return (
        row.host,
        source_words(row),
        row.donor_host,
        row.page_url,
        row.anchor,
        # У бизнеса ниши балла нет: его не скорили, а «0» читался бы приговором.
        None if niche else row.points,
        "; ".join(row.reasons),
        address_words(row),
        OFFER_TITLES[row.letter],
    )


def _cell(value: object) -> str:
    """Пусто — пустая ячейка; текст, похожий на формулу, — с апострофом впереди."""
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(_FORMULA) else text


def to_csv(rows: Sequence[AdvertiserRow]) -> bytes:
    """Строки списка рекламодателей одним файлом."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(COLUMNS)
    for row in rows:
        writer.writerow([_cell(value) for value in _values(row)])
    # Метка порядка байтов — ради Excel, см. модуль.
    return buffer.getvalue().encode("utf-8-sig")
