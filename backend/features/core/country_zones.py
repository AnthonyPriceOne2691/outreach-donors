"""Пояс по стране: ISO-2 → пояс базы IANA — единственный пояс страны или пояс её столицы.

Почте нужен пояс получателя, когда о нём известна только страна (`letters/autosend_window.py`:
окно получателя у очереди, которая уходит сама). Таблица — из `zone.tab` базы поясов (2026), а у
стран с несколькими поясами — столичный: Россия — Москва, США — Нью-Йорк, Канада — Торонто,
Австралия — Сидней, Бразилия — Сан-Паулу, Украина — Киев. Таблица — здесь, а не чтением файлов
машины: в образе сервиса файла `zone.tab` может не быть, а ответ на одну и ту же страну должен
быть один. `uk` — то же, что `gb`: так страну пишут люди и часть провайдеров.

Своя таблица — у модуля продаж (`sales/geo.py`, с названиями стран): почта модуль продаж не
импортирует (контракт `mail-does-not-know-sales`), а пояс по стране нужен и ей.
"""

from __future__ import annotations

#: Пары «страна пояс» через пробел: так таблица в 245 стран занимает полсотни строк.
_TABLE = """
ad Europe/Andorra ae Asia/Dubai af Asia/Kabul ag America/Antigua ai America/Anguilla
al Europe/Tirane am Asia/Yerevan ao Africa/Luanda ar America/Argentina/Buenos_Aires
as Pacific/Pago_Pago at Europe/Vienna au Australia/Sydney aw America/Aruba
ax Europe/Mariehamn az Asia/Baku ba Europe/Sarajevo bb America/Barbados bd Asia/Dhaka
be Europe/Brussels bf Africa/Ouagadougou bg Europe/Sofia bh Asia/Bahrain bi Africa/Bujumbura
bj Africa/Porto-Novo bl America/St_Barthelemy bm Atlantic/Bermuda bn Asia/Brunei
bo America/La_Paz bq America/Kralendijk br America/Sao_Paulo bs America/Nassau
bt Asia/Thimphu bw Africa/Gaborone by Europe/Minsk bz America/Belize ca America/Toronto
cc Indian/Cocos cd Africa/Kinshasa cf Africa/Bangui cg Africa/Brazzaville ch Europe/Zurich
ci Africa/Abidjan ck Pacific/Rarotonga cl America/Santiago cm Africa/Douala cn Asia/Shanghai
co America/Bogota cr America/Costa_Rica cu America/Havana cv Atlantic/Cape_Verde
cw America/Curacao cx Indian/Christmas cy Asia/Nicosia cz Europe/Prague de Europe/Berlin
dj Africa/Djibouti dk Europe/Copenhagen dm America/Dominica do America/Santo_Domingo
dz Africa/Algiers ec America/Guayaquil ee Europe/Tallinn eg Africa/Cairo eh Africa/El_Aaiun
er Africa/Asmara es Europe/Madrid et Africa/Addis_Ababa fi Europe/Helsinki fj Pacific/Fiji
fk Atlantic/Stanley fm Pacific/Pohnpei fo Atlantic/Faroe fr Europe/Paris
ga Africa/Libreville gb Europe/London gd America/Grenada ge Asia/Tbilisi gf America/Cayenne
gg Europe/Guernsey gh Africa/Accra gi Europe/Gibraltar gl America/Nuuk gm Africa/Banjul
gn Africa/Conakry gp America/Guadeloupe gq Africa/Malabo gr Europe/Athens
gs Atlantic/South_Georgia gt America/Guatemala gu Pacific/Guam gw Africa/Bissau
gy America/Guyana hk Asia/Hong_Kong hn America/Tegucigalpa hr Europe/Zagreb
ht America/Port-au-Prince hu Europe/Budapest id Asia/Jakarta ie Europe/Dublin
il Asia/Jerusalem im Europe/Isle_of_Man in Asia/Kolkata io Indian/Chagos iq Asia/Baghdad
ir Asia/Tehran is Atlantic/Reykjavik it Europe/Rome je Europe/Jersey jm America/Jamaica
jo Asia/Amman jp Asia/Tokyo ke Africa/Nairobi kg Asia/Bishkek kh Asia/Phnom_Penh
ki Pacific/Tarawa km Indian/Comoro kn America/St_Kitts kp Asia/Pyongyang kr Asia/Seoul
kw Asia/Kuwait ky America/Cayman kz Asia/Almaty la Asia/Vientiane lb Asia/Beirut
lc America/St_Lucia li Europe/Vaduz lk Asia/Colombo lr Africa/Monrovia ls Africa/Maseru
lt Europe/Vilnius lu Europe/Luxembourg lv Europe/Riga ly Africa/Tripoli ma Africa/Casablanca
mc Europe/Monaco md Europe/Chisinau me Europe/Podgorica mf America/Marigot
mg Indian/Antananarivo mh Pacific/Majuro mk Europe/Skopje ml Africa/Bamako mm Asia/Yangon
mn Asia/Ulaanbaatar mo Asia/Macau mp Pacific/Saipan mq America/Martinique
mr Africa/Nouakchott ms America/Montserrat mt Europe/Malta mu Indian/Mauritius
mv Indian/Maldives mw Africa/Blantyre mx America/Mexico_City my Asia/Kuala_Lumpur
mz Africa/Maputo na Africa/Windhoek nc Pacific/Noumea ne Africa/Niamey nf Pacific/Norfolk
ng Africa/Lagos ni America/Managua nl Europe/Amsterdam no Europe/Oslo np Asia/Kathmandu
nr Pacific/Nauru nu Pacific/Niue nz Pacific/Auckland om Asia/Muscat pa America/Panama
pe America/Lima pf Pacific/Tahiti pg Pacific/Port_Moresby ph Asia/Manila pk Asia/Karachi
pl Europe/Warsaw pm America/Miquelon pn Pacific/Pitcairn pr America/Puerto_Rico
ps Asia/Hebron pt Europe/Lisbon pw Pacific/Palau py America/Asuncion qa Asia/Qatar
re Indian/Reunion ro Europe/Bucharest rs Europe/Belgrade ru Europe/Moscow rw Africa/Kigali
sa Asia/Riyadh sb Pacific/Guadalcanal sc Indian/Mahe sd Africa/Khartoum se Europe/Stockholm
sg Asia/Singapore sh Atlantic/St_Helena si Europe/Ljubljana sj Arctic/Longyearbyen
sk Europe/Bratislava sl Africa/Freetown sm Europe/San_Marino sn Africa/Dakar
so Africa/Mogadishu sr America/Paramaribo ss Africa/Juba st Africa/Sao_Tome
sv America/El_Salvador sx America/Lower_Princes sy Asia/Damascus sz Africa/Mbabane
tc America/Grand_Turk td Africa/Ndjamena tf Indian/Kerguelen tg Africa/Lome th Asia/Bangkok
tj Asia/Dushanbe tk Pacific/Fakaofo tl Asia/Dili tm Asia/Ashgabat tn Africa/Tunis
to Pacific/Tongatapu tr Europe/Istanbul tt America/Port_of_Spain tv Pacific/Funafuti
tw Asia/Taipei tz Africa/Dar_es_Salaam ua Europe/Kyiv ug Africa/Kampala us America/New_York
uy America/Montevideo uz Asia/Tashkent va Europe/Vatican vc America/St_Vincent
ve America/Caracas vg America/Tortola vi America/St_Thomas vn Asia/Ho_Chi_Minh
vu Pacific/Efate wf Pacific/Wallis ws Pacific/Apia ye Asia/Aden yt Indian/Mayotte
za Africa/Johannesburg zm Africa/Lusaka zw Africa/Harare
"""

_WORDS = _TABLE.split()

#: Страна (ISO-2, строчными) → пояс: единственный или столичный.
ZONES: dict[str, str] = dict(zip(_WORDS[0::2], _WORDS[1::2], strict=True)) | {"uk": "Europe/London"}


def zone_name(country: str | None) -> str | None:
    """Пояс страны (`de`, `DE`) — или `None`, если страна не названа или её нет в таблице."""
    if not country:
        return None
    return ZONES.get(country.strip().lower())
