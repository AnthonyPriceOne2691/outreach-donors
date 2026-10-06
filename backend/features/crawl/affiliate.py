"""Партнёрская ссылка: метка в адресе и рекламодатель за сетью.

**Признак не зависит от ниши.** Партнёрская программа узнаёт своего
клиента по метке в адресе — и в ставках, и в финансах, и в софте,
и в путешествиях. Слова анкора у каждой ниши свои (скоринг до 06.10.2026
считал коммерческими слова ставок — «odds», «deposit», «bonus» — и на
финансовом блоге выдал «куплено» сайту основателя блога), а метка
`aff_id=` или переход через сеть партнёрок одинаковы везде.

Три вида метки, от самой надёжной:

1. **Сеть партнёрок** (awin, cj, rakuten, impact…): ссылка ведёт не к
   рекламодателю, а к сети, и та пересылает дальше. Адрес рекламодателя
   сеть обычно несёт в параметре — он и становится получателем письма.
   Не несёт — рекламодатель скрыт, и писать некому: письмо сети ушло бы
   не тому, кто платит донору.
2. **Параметр партнёрки** в адресе рекламодателя (`aff_id`, `irclickid`,
   `btag`…) или `utm_medium` платного канала (`affiliate`, `sponsored`…).
3. **Переход** — первый раздел адреса `/go/`, `/out/`, `/recommends/`:
   так рекламодатели отдают партнёрам ссылки со счётчиком.

**Чего здесь нет намеренно.** `?ref=` — Ghost и ряд платформ ставят его
на каждую внешнюю ссылку сами, и признак стал бы у всех. `utm_source`
и `utm_medium=email|social|referral` — так метит переходы кто угодно,
не только платящий.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qsl, unquote

from backend.features.donors.host import normalize_host
from backend.shared.net.url_parts import split_url

#: Параметры, по которым партнёрская программа узнаёт, чей это клиент.
AFFILIATE_PARAMS: frozenset[str] = frozenset(
    {
        "aff", "affid", "aff_id", "affiliate", "affiliate_id", "affiliateid", "aff_sub",
        "a_aid", "btag", "clickid", "click_id", "irclickid", "subid", "sub_id",
        "refid", "ref_id", "referral_code", "partner_id", "partnerid", "tracking_code",
        "awc", "cjevent", "ranmid", "ransiteid", "sscid", "pjid",
    }
)  # fmt: skip

#: `utm_medium`, которым метят платный канал. Остальные значения (email,
#: social, referral) ставят и бесплатным переходам.
PAID_MEDIUMS: frozenset[str] = frozenset(
    {
        "affiliate", "affiliates", "partner", "partners", "partnership", "sponsored",
        "sponsor", "sponsorship", "paid", "cpc", "cpa", "ppc", "advertorial", "native",
    }
)  # fmt: skip

#: Первый раздел адреса, через который рекламодатель отдаёт ссылку со счётчиком.
#: `/track/` нет: так выглядит и страница «где мой заказ».
REDIRECT_SECTIONS: frozenset[str] = frozenset(
    {"go", "out", "recommends", "refer", "aff", "affiliate", "click"}
)

#: Сети партнёрок — корни их доменов перехода. Письмо им не пишут никогда:
#: рекламодатель — тот, кого сеть называет в параметре.
NETWORK_ROOTS: frozenset[str] = frozenset(
    {
        "awin1.com", "shareasale.com", "anrdoezrs.net", "jdoqocy.com", "tkqlhce.com",
        "dpbolvw.net", "kqzyfj.com", "linksynergy.com", "prf.hn", "sjv.io", "pxf.io",
        "evyy.net", "skimresources.com", "viglink.com", "clickbank.net", "avantlink.com",
        "pjtra.com", "pjatr.com", "pntra.com", "gopjn.com", "webgains.com",
        "tradedoubler.com", "go2cloud.org", "flexlinkspro.com", "rstyle.me", "shopstyle.it",
        "howl.me", "admitad.com", "partnerize.com", "refersion.com", "linkby.com",
        # Счётчик партнёрок букмекеров (Income Access): «Hollywoodbets» с `btag`
        # вёл на eacdn.com (ставки, 06.10) — письмо ушло бы счётчику.
        "eacdn.com",
        # Рекламные сети: за переходом рекламодатель, но сеть его не называет —
        # «see a list of them at Experian.com» вела на doubleclick (финансы, 06.10).
        "doubleclick.net", "googleadservices.com", "adnxs.com", "taboola.com", "outbrain.com",
    }
)  # fmt: skip

#: Где сеть несёт адрес рекламодателя.
DESTINATION_PARAMS: tuple[str, ...] = (
    "url", "u", "murl", "ued", "urllink", "dest", "destination", "redirect",
    "redirect_url", "r", "to", "goto", "target", "link", "lnk", "p",
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class Affiliation:
    """Что выдало партнёрскую ссылку и кто за ней стоит.

    `network` — корень сети, если ссылка идёт через неё. `target_host`
    и `target_root` — рекламодатель, которого сеть назвала; `None` — сеть
    его не назвала, и писать некому.
    """

    mark: str
    network: str | None = None
    target_host: str | None = None
    target_root: str | None = None


def affiliation(url: str) -> Affiliation | None:
    """Партнёрская ли ссылка, по какой метке и кто рекламодатель.

    Корень берётся из самого адреса, а не из получателя ссылки: после
    раскрытия сети получатель — рекламодатель, а признак «через сеть»
    остаётся в адресе.
    """
    parts = split_url(url)
    if parts is None:
        return None
    params = [(key.lower(), value) for key, value in parse_qsl(parts.query)]
    host = (parts.hostname or "").lower()
    root = normalize_host(host) or host
    if root in NETWORK_ROOTS:
        return _through_network(root, params)
    found = _marked_param(params)
    if found is not None:
        return Affiliation(mark=found)
    sections = [section for section in parts.path.split("/") if section]
    if len(sections) > 1 and sections[0].lower() in REDIRECT_SECTIONS:
        return Affiliation(mark=f"переход /{sections[0].lower()}/")
    return None


def _marked_param(params: list[tuple[str, str]]) -> str | None:
    for key, value in params:
        if key in AFFILIATE_PARAMS:
            return f"метка «{key}» в адресе"
        if key == "utm_medium" and value.lower() in PAID_MEDIUMS:
            return f"utm_medium={value.lower()}"
    return None


def _through_network(network: str, params: list[tuple[str, str]]) -> Affiliation:
    """Ссылка через сеть: рекламодатель — адрес в её параметре, если он есть."""
    for key, value in params:
        if key not in DESTINATION_PARAMS:
            continue
        host = _host_of(value)
        if host is None:
            continue
        root = normalize_host(host) or host
        if root == network:
            continue
        return Affiliation(
            mark=f"через сеть партнёрок {network}",
            network=network,
            target_host=host,
            target_root=root,
        )
    return Affiliation(mark=f"через сеть партнёрок {network}", network=network)


def _host_of(value: str) -> str | None:
    """Хост из адреса в параметре. Сети кодируют его один или два раза."""
    text = value
    for _ in range(3):
        if text.lower().startswith(("http://", "https://")):
            break
        decoded = unquote(text)
        if decoded == text:
            return None
        text = decoded
    else:
        return None
    parts = split_url(text)
    host = (parts.hostname or "").lower() if parts is not None else ""
    return host or None
