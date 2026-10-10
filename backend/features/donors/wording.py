"""Слова экрана для того, что сервер хранит кодами: страны, вердикты, исходы.

Экран переводит коды сам (`frontend/src/api/labels.ts`), но две вещи
собирает сервер, и говорить они обязаны теми же словами:

* **выгрузку доноров файлом.** До 25.09.2026 в ней стояли `unsuitable`,
  `found`, `us` и доля региона `0.19888…` — рядом с экраном, где те же
  доноры названы «не подходит», «адрес найден», «США · 20%»;
* **причину отсева.** Правило региона писало код страны («ng не входит
  в топ-5…»), и на трёх экранах он выходил как есть. Причина хранится
  свидетельством и не переписывается — чинится её показ, и старые записи
  читаются словами так же, как новые (тот же довод у причин прогона,
  `runs/reasons.py`).

Копий слов две — здесь и во фронте, — и расходиться им нельзя: тест
сверяет их построчно (`tests/test_donor_wording.py`).
"""

from __future__ import annotations

import math
import re

from backend.features.core.domain import ContactStatus, DonorStatus
from backend.features.donors.regions_ru import REGION_NAMES

#: Названия рынков — те же, что `COUNTRY_TITLES` в `labels.ts`.
COUNTRY_TITLES: dict[str, str] = {
    "us": "США", "gb": "Великобритания", "de": "Германия", "fr": "Франция",
    "es": "Испания", "it": "Италия", "nl": "Нидерланды", "pl": "Польша",
    "ca": "Канада", "au": "Австралия", "in": "Индия", "br": "Бразилия",
    "mx": "Мексика", "id": "Индонезия", "ph": "Филиппины", "za": "ЮАР",
    "ae": "ОАЭ", "sa": "Саудовская Аравия", "tr": "Турция", "ua": "Украина",
    "kz": "Казахстан", "sg": "Сингапур", "my": "Малайзия", "th": "Таиланд",
    "vn": "Вьетнам", "jp": "Япония", "se": "Швеция", "no": "Норвегия",
    "dk": "Дания", "fi": "Финляндия", "cz": "Чехия", "ro": "Румыния",
    "gr": "Греция", "pt": "Португалия", "ie": "Ирландия", "nz": "Новая Зеландия",
    "il": "Израиль", "eg": "Египет", "ng": "Нигерия", "ke": "Кения",
    "ar": "Аргентина", "cl": "Чили", "co": "Колумбия", "pe": "Перу",
    "ch": "Швейцария", "at": "Австрия", "be": "Бельгия", "hu": "Венгрия",
    "bg": "Болгария", "hr": "Хорватия", "sk": "Словакия", "si": "Словения",
    "lt": "Литва", "lv": "Латвия", "ee": "Эстония",
}  # fmt: skip

#: Те же рынки по-английски — для текста письма рекламодателю: язык письма
#: английский, а аудитория площадки называется словами, не кодом (ISO-2 → имя
#: с артиклем, где он нужен: «in the United States»).
COUNTRY_NAMES_EN: dict[str, str] = {
    "us": "the United States", "gb": "the United Kingdom", "de": "Germany", "fr": "France",
    "es": "Spain", "it": "Italy", "nl": "the Netherlands", "pl": "Poland", "ca": "Canada",
    "au": "Australia", "in": "India", "br": "Brazil", "mx": "Mexico", "id": "Indonesia",
    "ph": "the Philippines", "za": "South Africa", "ae": "the UAE", "sa": "Saudi Arabia",
    "tr": "Turkey", "ua": "Ukraine", "kz": "Kazakhstan", "sg": "Singapore", "my": "Malaysia",
    "th": "Thailand", "vn": "Vietnam", "jp": "Japan", "se": "Sweden", "no": "Norway",
    "dk": "Denmark", "fi": "Finland", "cz": "the Czech Republic", "ro": "Romania",
    "gr": "Greece", "pt": "Portugal", "ie": "Ireland", "nz": "New Zealand", "il": "Israel",
    "eg": "Egypt", "ng": "Nigeria", "ke": "Kenya", "ar": "Argentina", "cl": "Chile",
    "co": "Colombia", "pe": "Peru", "ch": "Switzerland", "at": "Austria", "be": "Belgium",
    "hu": "Hungary", "bg": "Bulgaria", "hr": "Croatia", "sk": "Slovakia", "si": "Slovenia",
    "lt": "Lithuania", "lv": "Latvia", "ee": "Estonia",
}  # fmt: skip

#: Вердикт по донору — те же слова, что `DONOR_STATUSES` в `labels.ts`.
DONOR_STATUS_TITLES: dict[DonorStatus, str] = {
    DonorStatus.SUITABLE: "подходит",
    DonorStatus.UNSUITABLE: "не подходит",
    DonorStatus.UNCHECKED: "не проверен",
}

#: Исход поиска адреса — те же слова, что `CONTACT_STATUSES` в `labels.ts`.
CONTACT_STATUS_TITLES: dict[ContactStatus, str] = {
    ContactStatus.FOUND: "адрес найден",
    ContactStatus.NOT_FOUND: "адреса нет",
    ContactStatus.FORM_ONLY: "только форма",
    ContactStatus.NO_QUOTA: "кончилась квота",
    ContactStatus.RATE_LIMITED: "предел запросов",
    ContactStatus.BLOCKED: "учётка закрыта",
    ContactStatus.ERROR: "ошибка поиска",
    ContactStatus.NO_ANSWER: "сайт не ответил",
}

#: Адрес ещё не искали: «не нашли» и «не искали» решаются по-разному.
NOT_SEARCHED = "не искали"

#: Код страны в начале причины отсева — так её пишет правило региона
#: (`donors/geo.py`): «ng не входит в топ-5…» до 25.09.2026, «NG не входит…»
#: после. Заменяется только известный код и только перед этими словами:
#: «DR 12 ниже 20» тоже начинается с двух латинских букв. Что формулировка
#: правила не уехала от этого образца, держит тест.
_LEADING_COUNTRY = re.compile(r"^(?P<code>[A-Za-z]{2})(?= не входит в топ-)")


def _country_name(code: str) -> str | None:
    """Имя страны: рынок — нашим словом, другая страна — именем CLDR (`regions_ru.py`,
    на экране — `Intl.DisplayNames`); `None` — это не страна."""
    key = code.lower()
    return COUNTRY_TITLES.get(key) or REGION_NAMES.get(key)


def country_title(code: str | None) -> str:
    """Страна одним видом с экраном: русское имя, а не страна — кодом заглавными.

    Страна вне 55 рынков шла кодом — «NP» (проверка прода 10.10.2026).
    """
    if not code:
        return ""
    return _country_name(code) or code.upper()


def reject_reason_text(reason: str | None) -> str | None:
    """Причина отсева словами: код страны в начале — её названием.

    «ng не входит в топ-5 и даёт меньше 20%» → «Нигерия не входит…».
    Запись в базе не меняется: сырой текст — свидетельство.
    """
    if reason is None:
        return None
    found = _LEADING_COUNTRY.match(reason)
    if found is None:
        return reason
    name = _country_name(found["code"])
    return reason if name is None else name + reason[found.end() :]


def share_text(share: float | None) -> str:
    """Доля 0…1 процентами, как на экране: 0.19888 → «20%».

    Половина округляется вверх, как `Math.round` у экрана: встроенный
    `round` округляет её к чётному, и 12,5% стали бы «12%» в файле при
    «13%» на экране.
    """
    return "" if share is None else f"{math.floor(share * 100 + 0.5)}%"


def country_name_en(code: str | None) -> str:
    """Английское имя рынка по ISO-2; неизвестный код — пусто, не код."""
    return COUNTRY_NAMES_EN.get((code or "").lower(), "")
