"""Страна и часовой пояс лида — таблицами данных.

Базы пишут страну как попало: «DE», «Germany», «Германия». Код нужен для
отчётов и для пояса, а пояс — для окон отправки: письмо уходит в рабочие
часы получателя. Таблица — данные, а не ветки кода: новое написание страны
добавляется строкой в `ALIASES`.

**Откуда строки.** Коды и список стран — `iso3166.tab` базы часовых поясов;
названия — CLDR (babel 2.18) по-английски и по-русски; пояс — из `zone.tab`
по правилу «у страны один пояс»: все её зоны дают один сдвиг и зимой, и летом
(тогда берётся первая, столичная). Две поправки к правилу: Китай — по закону
один пояс на всю страну; Украина — зона Крыма в таблице стоит под её кодом,
пояс страны — киевский.

**У страны с несколькими поясами — пояс столицы** (`CAPITAL_ZONES`; решение
владельца 04.10): пустой пояс оставил бы лида без окна отправки, а столичный
верен для большей части базы — Испания без Канар, Португалия без Азор.
Лид получает замечание «пояс по столице, в файле не задан»: точнее скажет
только колонка файла, и она побеждает. Территории без столицы (Антарктида,
необитаемые острова) остаются без пояса.

Сверка названий — без регистра, диакритики, «ё» и знаков: «Соединённые
Штаты», «U.S.A.» и «United States of America» — одна страна.
"""

from __future__ import annotations

import unicodedata
from functools import lru_cache
from zoneinfo import available_timezones

#: Код ISO-2, название по-английски, по-русски, пояс IANA («» — поясов несколько).
COUNTRIES: tuple[tuple[str, str, str, str], ...] = (
    ("ad", "Andorra", "Андорра", "Europe/Andorra"),
    ("ae", "United Arab Emirates", "ОАЭ", "Asia/Dubai"),
    ("af", "Afghanistan", "Афганистан", "Asia/Kabul"),
    ("ag", "Antigua & Barbuda", "Антигуа и Барбуда", "America/Antigua"),
    ("ai", "Anguilla", "Ангилья", "America/Anguilla"),
    ("al", "Albania", "Албания", "Europe/Tirane"),
    ("am", "Armenia", "Армения", "Asia/Yerevan"),
    ("ao", "Angola", "Ангола", "Africa/Luanda"),
    ("aq", "Antarctica", "Антарктида", ""),
    ("ar", "Argentina", "Аргентина", "America/Argentina/Buenos_Aires"),
    ("as", "American Samoa", "Американское Самоа", "Pacific/Pago_Pago"),
    ("at", "Austria", "Австрия", "Europe/Vienna"),
    ("au", "Australia", "Австралия", ""),
    ("aw", "Aruba", "Аруба", "America/Aruba"),
    ("ax", "Åland Islands", "Аландские о-ва", "Europe/Mariehamn"),
    ("az", "Azerbaijan", "Азербайджан", "Asia/Baku"),
    ("ba", "Bosnia & Herzegovina", "Босния и Герцеговина", "Europe/Sarajevo"),
    ("bb", "Barbados", "Барбадос", "America/Barbados"),
    ("bd", "Bangladesh", "Бангладеш", "Asia/Dhaka"),
    ("be", "Belgium", "Бельгия", "Europe/Brussels"),
    ("bf", "Burkina Faso", "Буркина-Фасо", "Africa/Ouagadougou"),
    ("bg", "Bulgaria", "Болгария", "Europe/Sofia"),
    ("bh", "Bahrain", "Бахрейн", "Asia/Bahrain"),
    ("bi", "Burundi", "Бурунди", "Africa/Bujumbura"),
    ("bj", "Benin", "Бенин", "Africa/Porto-Novo"),
    ("bl", "St. Barthélemy", "Сен-Бартелеми", "America/St_Barthelemy"),
    ("bm", "Bermuda", "Бермудские о-ва", "Atlantic/Bermuda"),
    ("bn", "Brunei", "Бруней", "Asia/Brunei"),
    ("bo", "Bolivia", "Боливия", "America/La_Paz"),
    ("bq", "Caribbean Netherlands", "Бонэйр, Синт-Эстатиус и Саба", "America/Kralendijk"),
    ("br", "Brazil", "Бразилия", ""),
    ("bs", "Bahamas", "Багамы", "America/Nassau"),
    ("bt", "Bhutan", "Бутан", "Asia/Thimphu"),
    ("bv", "Bouvet Island", "о-в Буве", ""),
    ("bw", "Botswana", "Ботсвана", "Africa/Gaborone"),
    ("by", "Belarus", "Беларусь", "Europe/Minsk"),
    ("bz", "Belize", "Белиз", "America/Belize"),
    ("ca", "Canada", "Канада", ""),
    ("cc", "Cocos (Keeling) Islands", "Кокосовые о-ва", "Indian/Cocos"),
    ("cd", "Congo - Kinshasa", "Конго - Киншаса", ""),
    ("cf", "Central African Republic", "Центрально-Африканская Республика", "Africa/Bangui"),
    ("cg", "Congo - Brazzaville", "Конго - Браззавиль", "Africa/Brazzaville"),
    ("ch", "Switzerland", "Швейцария", "Europe/Zurich"),
    ("ci", "Côte d’Ivoire", "Кот-д’Ивуар", "Africa/Abidjan"),
    ("ck", "Cook Islands", "о-ва Кука", "Pacific/Rarotonga"),
    ("cl", "Chile", "Чили", ""),
    ("cm", "Cameroon", "Камерун", "Africa/Douala"),
    ("cn", "China", "Китай", "Asia/Shanghai"),
    ("co", "Colombia", "Колумбия", "America/Bogota"),
    ("cr", "Costa Rica", "Коста-Рика", "America/Costa_Rica"),
    ("cu", "Cuba", "Куба", "America/Havana"),
    ("cv", "Cape Verde", "Кабо-Верде", "Atlantic/Cape_Verde"),
    ("cw", "Curaçao", "Кюрасао", "America/Curacao"),
    ("cx", "Christmas Island", "о-в Рождества", "Indian/Christmas"),
    ("cy", "Cyprus", "Кипр", "Asia/Nicosia"),
    ("cz", "Czechia", "Чехия", "Europe/Prague"),
    ("de", "Germany", "Германия", "Europe/Berlin"),
    ("dj", "Djibouti", "Джибути", "Africa/Djibouti"),
    ("dk", "Denmark", "Дания", "Europe/Copenhagen"),
    ("dm", "Dominica", "Доминика", "America/Dominica"),
    ("do", "Dominican Republic", "Доминиканская Республика", "America/Santo_Domingo"),
    ("dz", "Algeria", "Алжир", "Africa/Algiers"),
    ("ec", "Ecuador", "Эквадор", ""),
    ("ee", "Estonia", "Эстония", "Europe/Tallinn"),
    ("eg", "Egypt", "Египет", "Africa/Cairo"),
    ("eh", "Western Sahara", "Западная Сахара", "Africa/El_Aaiun"),
    ("er", "Eritrea", "Эритрея", "Africa/Asmara"),
    ("es", "Spain", "Испания", ""),
    ("et", "Ethiopia", "Эфиопия", "Africa/Addis_Ababa"),
    ("fi", "Finland", "Финляндия", "Europe/Helsinki"),
    ("fj", "Fiji", "Фиджи", "Pacific/Fiji"),
    ("fk", "Falkland Islands", "Фолклендские о-ва", "Atlantic/Stanley"),
    ("fm", "Micronesia", "Федеративные Штаты Микронезии", ""),
    ("fo", "Faroe Islands", "Фарерские о-ва", "Atlantic/Faroe"),
    ("fr", "France", "Франция", "Europe/Paris"),
    ("ga", "Gabon", "Габон", "Africa/Libreville"),
    ("gb", "United Kingdom", "Великобритания", "Europe/London"),
    ("gd", "Grenada", "Гренада", "America/Grenada"),
    ("ge", "Georgia", "Грузия", "Asia/Tbilisi"),
    ("gf", "French Guiana", "Французская Гвиана", "America/Cayenne"),
    ("gg", "Guernsey", "Гернси", "Europe/Guernsey"),
    ("gh", "Ghana", "Гана", "Africa/Accra"),
    ("gi", "Gibraltar", "Гибралтар", "Europe/Gibraltar"),
    ("gl", "Greenland", "Гренландия", ""),
    ("gm", "Gambia", "Гамбия", "Africa/Banjul"),
    ("gn", "Guinea", "Гвинея", "Africa/Conakry"),
    ("gp", "Guadeloupe", "Гваделупа", "America/Guadeloupe"),
    ("gq", "Equatorial Guinea", "Экваториальная Гвинея", "Africa/Malabo"),
    ("gr", "Greece", "Греция", "Europe/Athens"),
    ("gs", "South Georgia & South Sandwich Islands", "Южная Георгия и Южные Сандвичевы о-ва", "Atlantic/South_Georgia"),
    ("gt", "Guatemala", "Гватемала", "America/Guatemala"),
    ("gu", "Guam", "Гуам", "Pacific/Guam"),
    ("gw", "Guinea-Bissau", "Гвинея-Бисау", "Africa/Bissau"),
    ("gy", "Guyana", "Гайана", "America/Guyana"),
    ("hk", "Hong Kong SAR China", "Гонконг (САР)", "Asia/Hong_Kong"),
    ("hm", "Heard & McDonald Islands", "о-ва Херд и Макдональд", ""),
    ("hn", "Honduras", "Гондурас", "America/Tegucigalpa"),
    ("hr", "Croatia", "Хорватия", "Europe/Zagreb"),
    ("ht", "Haiti", "Гаити", "America/Port-au-Prince"),
    ("hu", "Hungary", "Венгрия", "Europe/Budapest"),
    ("id", "Indonesia", "Индонезия", ""),
    ("ie", "Ireland", "Ирландия", "Europe/Dublin"),
    ("il", "Israel", "Израиль", "Asia/Jerusalem"),
    ("im", "Isle of Man", "о-в Мэн", "Europe/Isle_of_Man"),
    ("in", "India", "Индия", "Asia/Kolkata"),
    ("io", "British Indian Ocean Territory", "Британская территория в Индийском океане", "Indian/Chagos"),
    ("iq", "Iraq", "Ирак", "Asia/Baghdad"),
    ("ir", "Iran", "Иран", "Asia/Tehran"),
    ("is", "Iceland", "Исландия", "Atlantic/Reykjavik"),
    ("it", "Italy", "Италия", "Europe/Rome"),
    ("je", "Jersey", "Джерси", "Europe/Jersey"),
    ("jm", "Jamaica", "Ямайка", "America/Jamaica"),
    ("jo", "Jordan", "Иордания", "Asia/Amman"),
    ("jp", "Japan", "Япония", "Asia/Tokyo"),
    ("ke", "Kenya", "Кения", "Africa/Nairobi"),
    ("kg", "Kyrgyzstan", "Киргизия", "Asia/Bishkek"),
    ("kh", "Cambodia", "Камбоджа", "Asia/Phnom_Penh"),
    ("ki", "Kiribati", "Кирибати", ""),
    ("km", "Comoros", "Коморы", "Indian/Comoro"),
    ("kn", "St. Kitts & Nevis", "Сент-Китс и Невис", "America/St_Kitts"),
    ("kp", "North Korea", "КНДР", "Asia/Pyongyang"),
    ("kr", "South Korea", "Республика Корея", "Asia/Seoul"),
    ("kw", "Kuwait", "Кувейт", "Asia/Kuwait"),
    ("ky", "Cayman Islands", "о-ва Кайман", "America/Cayman"),
    ("kz", "Kazakhstan", "Казахстан", "Asia/Almaty"),
    ("la", "Laos", "Лаос", "Asia/Vientiane"),
    ("lb", "Lebanon", "Ливан", "Asia/Beirut"),
    ("lc", "St. Lucia", "Сент-Люсия", "America/St_Lucia"),
    ("li", "Liechtenstein", "Лихтенштейн", "Europe/Vaduz"),
    ("lk", "Sri Lanka", "Шри-Ланка", "Asia/Colombo"),
    ("lr", "Liberia", "Либерия", "Africa/Monrovia"),
    ("ls", "Lesotho", "Лесото", "Africa/Maseru"),
    ("lt", "Lithuania", "Литва", "Europe/Vilnius"),
    ("lu", "Luxembourg", "Люксембург", "Europe/Luxembourg"),
    ("lv", "Latvia", "Латвия", "Europe/Riga"),
    ("ly", "Libya", "Ливия", "Africa/Tripoli"),
    ("ma", "Morocco", "Марокко", "Africa/Casablanca"),
    ("mc", "Monaco", "Монако", "Europe/Monaco"),
    ("md", "Moldova", "Молдова", "Europe/Chisinau"),
    ("me", "Montenegro", "Черногория", "Europe/Podgorica"),
    ("mf", "St. Martin", "Сен-Мартен", "America/Marigot"),
    ("mg", "Madagascar", "Мадагаскар", "Indian/Antananarivo"),
    ("mh", "Marshall Islands", "Маршалловы о-ва", "Pacific/Majuro"),
    ("mk", "North Macedonia", "Северная Македония", "Europe/Skopje"),
    ("ml", "Mali", "Мали", "Africa/Bamako"),
    ("mm", "Myanmar (Burma)", "Мьянма (Бирма)", "Asia/Yangon"),
    ("mn", "Mongolia", "Монголия", ""),
    ("mo", "Macao SAR China", "Макао (САР)", "Asia/Macau"),
    ("mp", "Northern Mariana Islands", "Северные Марианские о-ва", "Pacific/Saipan"),
    ("mq", "Martinique", "Мартиника", "America/Martinique"),
    ("mr", "Mauritania", "Мавритания", "Africa/Nouakchott"),
    ("ms", "Montserrat", "Монтсеррат", "America/Montserrat"),
    ("mt", "Malta", "Мальта", "Europe/Malta"),
    ("mu", "Mauritius", "Маврикий", "Indian/Mauritius"),
    ("mv", "Maldives", "Мальдивы", "Indian/Maldives"),
    ("mw", "Malawi", "Малави", "Africa/Blantyre"),
    ("mx", "Mexico", "Мексика", ""),
    ("my", "Malaysia", "Малайзия", "Asia/Kuala_Lumpur"),
    ("mz", "Mozambique", "Мозамбик", "Africa/Maputo"),
    ("na", "Namibia", "Намибия", "Africa/Windhoek"),
    ("nc", "New Caledonia", "Новая Каледония", "Pacific/Noumea"),
    ("ne", "Niger", "Нигер", "Africa/Niamey"),
    ("nf", "Norfolk Island", "о-в Норфолк", "Pacific/Norfolk"),
    ("ng", "Nigeria", "Нигерия", "Africa/Lagos"),
    ("ni", "Nicaragua", "Никарагуа", "America/Managua"),
    ("nl", "Netherlands", "Нидерланды", "Europe/Amsterdam"),
    ("no", "Norway", "Норвегия", "Europe/Oslo"),
    ("np", "Nepal", "Непал", "Asia/Kathmandu"),
    ("nr", "Nauru", "Науру", "Pacific/Nauru"),
    ("nu", "Niue", "Ниуэ", "Pacific/Niue"),
    ("nz", "New Zealand", "Новая Зеландия", ""),
    ("om", "Oman", "Оман", "Asia/Muscat"),
    ("pa", "Panama", "Панама", "America/Panama"),
    ("pe", "Peru", "Перу", "America/Lima"),
    ("pf", "French Polynesia", "Французская Полинезия", ""),
    ("pg", "Papua New Guinea", "Папуа — Новая Гвинея", ""),
    ("ph", "Philippines", "Филиппины", "Asia/Manila"),
    ("pk", "Pakistan", "Пакистан", "Asia/Karachi"),
    ("pl", "Poland", "Польша", "Europe/Warsaw"),
    ("pm", "St. Pierre & Miquelon", "Сен-Пьер и Микелон", "America/Miquelon"),
    ("pn", "Pitcairn Islands", "о-ва Питкэрн", "Pacific/Pitcairn"),
    ("pr", "Puerto Rico", "Пуэрто-Рико", "America/Puerto_Rico"),
    ("ps", "Palestinian Territories", "Палестинские территории", "Asia/Gaza"),
    ("pt", "Portugal", "Португалия", ""),
    ("pw", "Palau", "Палау", "Pacific/Palau"),
    ("py", "Paraguay", "Парагвай", "America/Asuncion"),
    ("qa", "Qatar", "Катар", "Asia/Qatar"),
    ("re", "Réunion", "Реюньон", "Indian/Reunion"),
    ("ro", "Romania", "Румыния", "Europe/Bucharest"),
    ("rs", "Serbia", "Сербия", "Europe/Belgrade"),
    ("ru", "Russia", "Россия", ""),
    ("rw", "Rwanda", "Руанда", "Africa/Kigali"),
    ("sa", "Saudi Arabia", "Саудовская Аравия", "Asia/Riyadh"),
    ("sb", "Solomon Islands", "Соломоновы о-ва", "Pacific/Guadalcanal"),
    ("sc", "Seychelles", "Сейшельские о-ва", "Indian/Mahe"),
    ("sd", "Sudan", "Судан", "Africa/Khartoum"),
    ("se", "Sweden", "Швеция", "Europe/Stockholm"),
    ("sg", "Singapore", "Сингапур", "Asia/Singapore"),
    ("sh", "St. Helena", "о-в Св. Елены", "Atlantic/St_Helena"),
    ("si", "Slovenia", "Словения", "Europe/Ljubljana"),
    ("sj", "Svalbard & Jan Mayen", "Шпицберген и Ян-Майен", "Arctic/Longyearbyen"),
    ("sk", "Slovakia", "Словакия", "Europe/Bratislava"),
    ("sl", "Sierra Leone", "Сьерра-Леоне", "Africa/Freetown"),
    ("sm", "San Marino", "Сан-Марино", "Europe/San_Marino"),
    ("sn", "Senegal", "Сенегал", "Africa/Dakar"),
    ("so", "Somalia", "Сомали", "Africa/Mogadishu"),
    ("sr", "Suriname", "Суринам", "America/Paramaribo"),
    ("ss", "South Sudan", "Южный Судан", "Africa/Juba"),
    ("st", "São Tomé & Príncipe", "Сан-Томе и Принсипи", "Africa/Sao_Tome"),
    ("sv", "El Salvador", "Сальвадор", "America/El_Salvador"),
    ("sx", "Sint Maarten", "Синт-Мартен", "America/Lower_Princes"),
    ("sy", "Syria", "Сирия", "Asia/Damascus"),
    ("sz", "Eswatini", "Эсватини", "Africa/Mbabane"),
    ("tc", "Turks & Caicos Islands", "Тёркс и Кайкос", "America/Grand_Turk"),
    ("td", "Chad", "Чад", "Africa/Ndjamena"),
    ("tf", "French Southern Territories", "Французские Южные территории", "Indian/Kerguelen"),
    ("tg", "Togo", "Того", "Africa/Lome"),
    ("th", "Thailand", "Таиланд", "Asia/Bangkok"),
    ("tj", "Tajikistan", "Таджикистан", "Asia/Dushanbe"),
    ("tk", "Tokelau", "Токелау", "Pacific/Fakaofo"),
    ("tl", "Timor-Leste", "Восточный Тимор", "Asia/Dili"),
    ("tm", "Turkmenistan", "Туркменистан", "Asia/Ashgabat"),
    ("tn", "Tunisia", "Тунис", "Africa/Tunis"),
    ("to", "Tonga", "Тонга", "Pacific/Tongatapu"),
    ("tr", "Türkiye", "Турция", "Europe/Istanbul"),
    ("tt", "Trinidad & Tobago", "Тринидад и Тобаго", "America/Port_of_Spain"),
    ("tv", "Tuvalu", "Тувалу", "Pacific/Funafuti"),
    ("tw", "Taiwan", "Тайвань", "Asia/Taipei"),
    ("tz", "Tanzania", "Танзания", "Africa/Dar_es_Salaam"),
    ("ua", "Ukraine", "Украина", "Europe/Kyiv"),
    ("ug", "Uganda", "Уганда", "Africa/Kampala"),
    ("um", "U.S. Outlying Islands", "Внешние малые о-ва (США)", ""),
    ("us", "United States", "Соединенные Штаты", ""),
    ("uy", "Uruguay", "Уругвай", "America/Montevideo"),
    ("uz", "Uzbekistan", "Узбекистан", "Asia/Samarkand"),
    ("va", "Vatican City", "Ватикан", "Europe/Vatican"),
    ("vc", "St. Vincent & Grenadines", "Сент-Винсент и Гренадины", "America/St_Vincent"),
    ("ve", "Venezuela", "Венесуэла", "America/Caracas"),
    ("vg", "British Virgin Islands", "Виргинские о-ва (Великобритания)", "America/Tortola"),
    ("vi", "U.S. Virgin Islands", "Виргинские о-ва (США)", "America/St_Thomas"),
    ("vn", "Vietnam", "Вьетнам", "Asia/Ho_Chi_Minh"),
    ("vu", "Vanuatu", "Вануату", "Pacific/Efate"),
    ("wf", "Wallis & Futuna", "Уоллис и Футуна", "Pacific/Wallis"),
    ("ws", "Samoa", "Самоа", "Pacific/Apia"),
    ("ye", "Yemen", "Йемен", "Asia/Aden"),
    ("yt", "Mayotte", "Майотта", "Indian/Mayotte"),
    ("za", "South Africa", "Южно-Африканская Республика", "Africa/Johannesburg"),
    ("zm", "Zambia", "Замбия", "Africa/Lusaka"),
    ("zw", "Zimbabwe", "Зимбабве", "Africa/Harare"),
)  # fmt: skip

#: Как ещё пишут страну в базах. Новое написание — строка здесь.
ALIASES: dict[str, str] = {
    "usa": "us", "u.s.": "us", "u.s.a.": "us", "united states of america": "us",
    "america": "us", "сша": "us", "америка": "us",
    "uk": "gb", "u.k.": "gb", "great britain": "gb", "britain": "gb", "england": "gb",
    "англия": "gb", "британия": "gb",
    "russian federation": "ru", "рф": "ru", "российская федерация": "ru",
    "czech republic": "cz", "чешская республика": "cz",
    "holland": "nl", "the netherlands": "nl", "голландия": "nl",
    "south korea": "kr", "korea": "kr", "republic of korea": "kr", "южная корея": "kr",
    "корея": "kr",
    "uae": "ae", "emirates": "ae", "эмираты": "ae", "объединённые арабские эмираты": "ae",
    "turkey": "tr", "turkiye": "tr",
    "belarus": "by", "белоруссия": "by",
    "moldova": "md", "молдова": "md",
    "macedonia": "mk", "македония": "mk",
    "viet nam": "vn",
    "ivory coast": "ci", "cote d'ivoire": "ci",
    "burma": "mm",
    "swaziland": "sz",
    "cape verde": "cv",
    "east timor": "tl",
    "slovak republic": "sk",
    "kyrgyzstan": "kg", "киргизия": "kg",
    "hong kong": "hk", "гонконг": "hk",
    "taiwan": "tw", "тайвань": "tw",
    "vatican": "va", "ватикан": "va",
    "bosnia": "ba", "босния": "ba",
    "congo": "cg", "конго": "cg",
    "drc": "cd", "dr congo": "cd", "democratic republic of the congo": "cd",
    "palestine": "ps", "палестина": "ps",
    "tanzania": "tz", "танзания": "tz",
}  # fmt: skip

#: Страна с несколькими поясами → пояс столицы. Только для кодов, у которых
#: в `COUNTRIES` пояс пуст: один источник правды на страну.
CAPITAL_ZONES: dict[str, str] = {
    "au": "Australia/Sydney",  # Канберра живёт в поясе Сиднея
    "br": "America/Sao_Paulo",  # Бразилиа — в поясе Сан-Паулу
    "ca": "America/Toronto",  # Оттава
    "cd": "Africa/Kinshasa",
    "cl": "America/Santiago",
    "ec": "America/Guayaquil",  # Кито
    "es": "Europe/Madrid",
    "fm": "Pacific/Pohnpei",  # Паликир
    "gl": "America/Nuuk",
    "id": "Asia/Jakarta",
    "ki": "Pacific/Tarawa",
    "mn": "Asia/Ulaanbaatar",
    "mx": "America/Mexico_City",
    "nz": "Pacific/Auckland",  # Веллингтон
    "pf": "Pacific/Tahiti",  # Папеэте
    "pg": "Pacific/Port_Moresby",
    "pt": "Europe/Lisbon",
    "ru": "Europe/Moscow",
    "us": "America/New_York",  # Вашингтон
}


def _key(text: str) -> str:
    """Только буквы, строчные, без диакритики и «ё»: `Côte d'Ivoire` → `cotedivoire`."""
    flat = unicodedata.normalize("NFKD", text.replace("ё", "е").replace("Ё", "Е"))
    return "".join(ch for ch in flat.lower() if ch.isalpha())


@lru_cache(maxsize=1)
def _by_name() -> dict[str, str]:
    index = {_key(name): code for code, *names, _zone in COUNTRIES for name in names}
    index |= {_key(name): code for name, code in ALIASES.items()}
    return index


@lru_cache(maxsize=1)
def _zones() -> dict[str, str]:
    """Зона по имени без регистра: `europe/berlin` → `Europe/Berlin`.

    Имена — базы часовых поясов машины и зоны таблицы стран: без базы
    (голый образ) пояс по стране всё равно не отвергался бы.
    """
    names = available_timezones() | {zone for *_, zone in COUNTRIES if zone}
    names |= set(CAPITAL_ZONES.values())
    return {name.lower(): name for name in names}


@lru_cache(maxsize=1)
def _timezones() -> dict[str, str]:
    return {code: zone for code, _en, _ru, zone in COUNTRIES}


def country_code(text: str) -> str | None:
    """`DE`, `Germany`, `Германия` → `de`. Непонятное — `None`."""
    key = _key(text)
    if len(key) == 2 and key in _timezones():
        return key
    return _by_name().get(key)


def timezone_for(code: str) -> str | None:
    """Пояс страны: единственный или столичный. `None` — страна неизвестна
    или у неё нет ни единого пояса, ни столицы."""
    return _timezones().get(code) or CAPITAL_ZONES.get(code)


def by_capital(code: str) -> bool:
    """Пояс страны — столичный, а не единственный: лиду нужно замечание."""
    return code in CAPITAL_ZONES


def zone_name(text: str) -> str | None:
    """Название пояса из файла в каноническом написании. Непонятное — `None`."""
    return _zones().get(" ".join(text.split()).lower())
