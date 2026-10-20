"""Страна по доменной зоне — запасная подсказка, где бизнес, когда текст сайта её не показал.

Судья сегмента v4 берёт страну бизнеса из текста сайта (адрес, телефон, валюта — с дословной цитатой,
`segment_facts`); не видна — по зоне домена. Зона страны и есть её код ISO 3166-1 alpha-2, с
исключениями данными ниже. Зона «бренд» — код страны, который массово берут как общий (`.io`, `.co`,
`.tv`…), — и зоны союзов (`.eu`) страны не дают: угадывание там вредит.

Источники: IANA Root Zone Database (зоны стран и их IDN-зоны, punycode); список «брендов» — зоны,
которые регистраторы продают как общие (данные, дописываются строкой). Код страны годен, только если
он есть в таблице стран модуля (`geo.COUNTRIES`, CLDR).
"""

from __future__ import annotations

from backend.features.sales.hypothesis_countries import known_codes

#: Зона, у которой код страны не совпадает с зоной: Великобритания — `.uk`, а не `.gb`; `.su` — зона
#: бывшего СССР, которую держит и продаёт российский регистратор.
EXCEPTIONS: dict[str, str] = {"uk": "GB", "su": "RU"}
#: IDN-зоны стран (punycode → код): имя в базе хранится в ASCII (IDNA).
IDN: dict[str, str] = {
    "xn--p1ai": "RU",  # .рф
    "xn--j1amh": "UA",  # .укр
    "xn--90ais": "BY",  # .бел
    "xn--80ao21a": "KZ",  # .қаз
    "xn--90a3ac": "RS",  # .срб
    "xn--d1alf": "MK",  # .мкд
    "xn--l1acc": "MN",  # .мон
    "xn--90ae": "BG",  # .бг
    "xn--y9a3aq": "AM",  # .հայ
    "xn--node": "GE",  # .გე
    "xn--qxam": "GR",  # .ελ
    "xn--fiqs8s": "CN",  # .中国
    "xn--fiqz9s": "CN",  # .中國
    "xn--j6w193g": "HK",  # .香港
    "xn--kprw13d": "TW",  # .台灣
    "xn--kpry57d": "TW",  # .台湾
    "xn--3e0b707e": "KR",  # .한국
    "xn--o3cw4h": "TH",  # .ไทย
    "xn--h2brj9c": "IN",  # .भारत
    "xn--yfro4i67o": "SG",  # .新加坡
    "xn--mgba3a4f16a": "IR",  # .ایران
    "xn--wgbh1c": "EG",  # .مصر
    "xn--mgberp4a5d4ar": "SA",  # .السعودية
    "xn--mgbaam7a8h": "AE",  # .امارات
    "xn--wgbl6a": "QA",  # .قطر
    "xn--mgbayh7gpa": "JO",  # .الاردن
    "xn--mgbc0a9azcg": "MA",  # .المغرب
    "xn--lgbbat1ad8j": "DZ",  # .الجزائر
    "xn--pgbs0dh": "TN",  # .تونس
    "xn--mgbai9azgqp6j": "PK",  # .پاکستان
}
#: Зоны-«бренды» и зоны союзов: страны не дают.
NO_COUNTRY: frozenset[str] = frozenset(
    {
        "ac", "ag", "ai", "am", "bz", "cc", "cf", "co", "cx", "eu", "fm", "fo", "ga", "gd", "gg",
        "gq", "im", "io", "la", "ly", "me", "ml", "ms", "nu", "pw", "sc", "sh", "st", "tk", "to",
        "tv", "vc", "ws",
    }
)  # fmt: skip


def country_of_host(host: str) -> str:
    """Код страны по зоне домена; пусто — зона общая, «бренд», союз или неизвестна."""
    zone = host.strip().rstrip(".").rsplit(".", 1)[-1].lower()
    if zone in NO_COUNTRY:
        return ""
    code = EXCEPTIONS.get(zone) or IDN.get(zone) or zone.upper()
    return code if len(code) == 2 and code in known_codes() else ""
