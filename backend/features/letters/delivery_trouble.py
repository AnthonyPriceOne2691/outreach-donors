"""Почему почта не доставляет письмо — видом причины и словами для человека (29.10.2026).

Письмо №404 рекламодателю шесть часов было «отправлено» без исхода: платформа прислала «принято»
и четырнадцать отсрочек с одним текстом — «error dialing remote address: dial tcp …:25: i/o
timeout», — а мы их принимали и отбрасывали. Сторож велел проверять вебхук, хотя вебхук работал:
не отвечал сервер получателя. Теперь текст ответа почты становится **видом причины и словами** —
одной чистой функцией (`reason_of`) с таблицей образцов, общей для отсрочки (`deferred`) и отказа
(`bounce`, `dropped`: отказ пишет текст в `failure_reason` письма).

**Образцы — под семейство, а не под одно письмо.** Текст пишет сервер получателя — Gmail, Outlook,
Яндекс, Mail.ru, Postfix, Exim, — его спам-фильтр или сама платформа, когда до сервера не дошло, —
и не всегда по-английски. Строка таблицы — вид и образец; порядок строк — порядок проверки, точное
выше общего: в «Recipient address rejected: Greylisted» отказ адреса — повод, а причина — серый
список, в «Gmail: this message has been blocked … spam» — спам, а не блокировка адреса.

**Код SMTP — запасной путь** (RFC 3463): текст не узнан — вид по расширенному коду (`4.2.2`,
`5.1.1`), его нет — по трёхзначному. Класс 4 — по определению «повторите позже».

**Неизвестное — исходным текстом**, обрезанным: человек видит, что сказала почта, а не «ошибка».
Короткий код — в скобках после слов: «сервер получателя не отвечает (порт 25, тайм-аут)».
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

#: Сколько знаков ответа почты держит письмо (`messages.deferred_reason`, `failure_reason`).
STORED_TEXT = 256

#: Сколько знаков ответа почты показать вместо слов, когда вид не узнан.
SHOWN_TEXT = 120

#: Текст отказа, когда платформа причины не назвала (`letters/events._bounced`).
NO_REASON = "отказ доставки"


class Trouble(StrEnum):
    """Вид причины, по которой почта не отдаёт письмо получателю."""

    UNREACHABLE = "unreachable"
    NO_MAIL_SERVER = "no_mail_server"
    TRY_LATER = "try_later"
    MAILBOX_FULL = "mailbox_full"
    RATE_LIMITED = "rate_limited"
    BLOCKED = "blocked"
    TLS = "tls"
    NO_MAILBOX = "no_mailbox"
    SPAM = "spam"
    SUPPRESSED = "suppressed"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


#: Вид словами — для человека, без технического мусора. Отсрочка и отказ ставят перед ними своё:
#: «Доставка откладывается: …», «Не доставлено: …».
WORDS: dict[Trouble, str] = {
    Trouble.UNREACHABLE: "сервер получателя не отвечает",
    Trouble.NO_MAIL_SERVER: "у домена получателя нет почтового сервера",
    Trouble.TRY_LATER: "сервер получателя просит повторить позже",
    Trouble.MAILBOX_FULL: "ящик получателя переполнен",
    Trouble.RATE_LIMITED: "сервер получателя ограничил частоту писем от нас",
    Trouble.BLOCKED: "сервер получателя отклоняет наш адрес или IP",
    Trouble.TLS: "сбой шифрования связи с сервером получателя",
    Trouble.NO_MAILBOX: "ящика получателя нет или он отключён",
    Trouble.SPAM: "сервер получателя счёл письмо спамом",
    Trouble.SUPPRESSED: "платформа не отправляла: адрес в её стоп-листе",
    Trouble.EXPIRED: "платформа перестала повторять доставку",
    Trouble.UNKNOWN: "ответ почты",
}


def _rx(*parts: str) -> re.Pattern[str]:
    return re.compile("|".join(parts), re.IGNORECASE)


#: Образцы по порядку проверки: первый совпавший называет вид.
RULES: tuple[tuple[Trouble, re.Pattern[str]], ...] = (
    # Платформа сама не отправляла (`dropped`) — раньше спама: в «Spam Reporting Address» есть
    # «spam», а речь об адресе, который жаловался.
    (Trouble.SUPPRESSED, _rx(r"^(bounced|unsubscribed|spam reporting) address$")),
    # Тариф учётки платформы, а не ящик получателя: «quota» здесь — не «переполнен».
    (Trouble.UNKNOWN, _rx(r"package quota")),
    # Шифрование — раньше «срок вышел»: «x509: certificate has expired» — о сертификате сервера.
    (Trouble.TLS, _rx(r"\btls\b", r"starttls", r"\bssl\b", r"x509", r"certificate", r"handshake")),
    (
        Trouble.EXPIRED,
        _rx(
            r"(message|delivery|queue|time) (has )?expired|expired (in|after|while)|queue\.expired",
            r"retry time(out)? (not reached|exceeded)",
            r"too long in (the )?queue",
            r"giving up|gave up",
            r"max(imum)? (number of )?(retries|attempts|delivery attempts)",
        ),
    ),
    # Наш адрес или IP — раньше «нет домена»: «Sender address rejected: Domain not found» — о домене
    # отправителя, а не получателя.
    (
        Trouble.BLOCKED,
        _rx(
            r"sender address rejected",
            r"client host rejected",
            r"cannot find your (reverse )?hostname",
            r"does not have a ptr record",
            r"reverse (dns|lookup|hostname)",
        ),
    ),
    (
        Trouble.NO_MAIL_SERVER,
        _rx(
            r"mx info",
            r"\bno (valid )?mx\b",
            r"null ?mx",
            r"mx records?",
            r"nxdomain",
            r"no such (host|domain)",
            r"host (or domain name )?not found",
            r"domain (name )?(not found|does not exist|doesn't exist|unknown)",
            r"(non-?existent|unknown) domain",
            r"name service error|name or service not known|name resolution",
            r"unroute?able",
            r"does not accept mail",
            r"dns (error|lookup|failure)",
            r"could not resolve|can'?t resolve|cannot resolve|failed to resolve",
            r"домен\w* не (найден|существует)",
            r"нет mx",
        ),
    ),
    (
        Trouble.UNREACHABLE,
        _rx(
            r"i/o timeout",
            r"timed out",
            r"\btime ?out\b",
            r"connection refused|refused to connect",
            r"no route to host",
            r"unreachable",
            r"connection reset|reset by peer|broken pipe",
            r"lost connection|connection (was )?(lost|closed|dropped)",
            r"(could not|couldn'?t|unable to|cannot|can'?t|failed to) connect",
            r"error dialing|dial tcp",
            r"no answer from host|not responding",
            r"время ожидания|не отвечает",
            r"zeitüberschreitung|délai d'attente|tiempo de espera",
        ),
    ),
    (
        Trouble.MAILBOX_FULL,
        _rx(
            r"mailbox (is )?full|full mailbox|inbox (is )?full",
            r"mailbox.{0,20}(size|storage) limit|mailbox size",
            r"over ?quota",
            r"quota (exceeded|full|reached)",
            r"exceed\w* (the |their |his |her )?(storage |disk |mailbox )?(quota|storage)",
            r"insufficient (disk )?(space|storage)|out of (disk )?space",
            r"storage (allocation|limit)",
            r"переполн",
            r"превыш\w*.{0,20}(квот|размер|объ[её]м)",
            r"ящик\w*.{0,20}(полон|заполнен)",
            r"postfach.{0,30}voll|quota.{0,20}überschritten",
            r"bo[iî]te.{0,40}pleine|quota.{0,20}d[ée]pass",
            r"buz[oó]n.{0,30}lleno|cuota.{0,20}(excedida|superada)",
            r"casella.{0,30}piena|quota.{0,20}superat",
            r"caixa.{0,30}(cheia|lotada)|cota.{0,20}excedida",
            r"skrzynk\w*.{0,20}pe[łl]n|przepe[łl]nion",
            r"(postvak|postbus|mailbox) (is )?vol\b",
            r"kutusu dolu",
        ),
    ),
    (
        Trouble.RATE_LIMITED,
        _rx(
            r"too many (concurrent |simultaneous |smtp )*"
            r"(connections|sessions|messages|mails|emails|recipients|requests)",
            r"too much mail",
            r"rate[ -]?limit",
            r"throttl",
            r"unusual rate|receiving mail (at|too)|sending rate",
            r"rate (exceeded|too high)|exceeded (the )?(message |sending )?rate",
            r"unexpected volume|volume too high",
            r"слишком много",
            r"превышен\w* (лимит|частот)\w* (отправ|писем|сообщ|соедин)",
            r"zu viele",
            r"trop de (connexions|messages|courriels)",
            r"demasiad[oa]s (conexiones|mensajes|correos)",
            r"troppe (connessioni|email|mail)",
        ),
    ),
    # Списки блокировок и репутация — раньше спама: в «blocked using zen.spamhaus.org» есть «spam».
    (
        Trouble.BLOCKED,
        _rx(
            r"spamhaus|spamcop|sorbs|barracuda|uceprotect",
            r"\bdnsbl\b|\brbl\b|blocked using",
            r"black ?list|block ?list|deny ?list|blacklisted|blocklisted",
            r"listed (on|in|at|by)",
            r"reputation",
            r"\bbanned\b",
            r"\byour ip\b|\bsending ip\b|ip address (is )?(blocked|banned|listed)",
            r"unauthenticated|not authenticated|\bspf\b|\bdkim\b|\bdmarc\b",
            r"complaints?\b",
            r"policy (reasons|restriction|violation)",
            r"ч[её]рн\w* спис",
            r"liste noire|lista negra|lista nera|schwarze liste",
        ),
    ),
    (
        Trouble.SPAM,
        _rx(
            r"spam(?!haus|cop|rats|\.)",
            r"\bjunk\b|unsolicited|\bube\b",
            r"content (rejected|filter|policy|restriction)|message content|its content",
            r"suspicious|malicious|virus|malware|phish",
            r"спам|нежелательн",
            r"unerwünscht|indésirable|no deseado|indesiderat|indesejad",
        ),
    ),
    (
        Trouble.TRY_LATER,
        _rx(
            r"try (again )?later|try again|retry later|come back later|please retry",
            r"gr[ae]y ?list",
            r"temporar(y|ily)|transient",
            r"(server|system) (is )?busy",
            r"service (is )?(currently |temporarily )?(unavailable|not available)",
            r"resources? temporarily|local problem",
            r"попробуйте|повтор\w* (попытку )?позже|временн",
            r"später|vorübergehend",
            r"plus tard|réessay|temporairement",
            r"m[aá]s tarde|int[eé]ntelo|intente|temporalmente",
            r"più tardi|riprov|temporaneamente",
            r"mais tarde|tente novamente|temporariamente",
            r"później|spróbuj",
            r"later opnieuw|daha sonra|zkuste",
        ),
    ),
    (
        Trouble.NO_MAILBOX,
        _rx(
            r"user unknown|unknown (user|recipient|mailbox|address)|recipient unknown",
            r"no such (user|recipient|mailbox|account|address|person)|no mailbox",
            r"(mailbox|recipient|user|address) (not found|unavailable)",
            r"does not exist|doesn'?t exist|doesn'?t have an? .{1,40} account",
            r"(account|mailbox|address)\b.{0,30}\b(disabled|deactivated|inactive|suspended|closed)",
            r"invalid (recipient|mailbox|address|user)|bad destination mailbox",
            r"recipient (address )?rejected|relay (access )?(denied|not permitted)",
            r"нет такого (пользовател|ящик|адрес)",
            r"(пользовател|ящик|адрес|получател)\w* не (найден|существует)",
            r"учётн\w* запис\w* (отключ|заблокир)|учетн\w* запис\w* (отключ|заблокир)",
            r"unbekannt|existiert nicht",
            r"inconnu|inexistant|n'existe pas",
            r"desconocid|no existe|sconosciut|non esiste|desconhecid|n[ãa]o existe",
            r"nieznan|nie istnieje|onbekend|bestaat niet",
        ),
    ),
    (
        Trouble.BLOCKED,
        _rx(
            r"\bblocked\b|\bdenied\b|not allowed|not permitted|prohibited|forbidden|\bpolicy\b",
            r"заблокир|блокир|запрещ",
            r"blockiert|gesperrt|bloqu|bloccat",
        ),
    ),
)

#: Расширенный код RFC 3463 — «4.2.2»; не кусок адреса IP: ни цифра с точкой до, ни после.
ENHANCED = re.compile(r"(?<![\d.])([245])\.(\d{1,3})\.(\d{1,3})(?!\.?\d)")
#: Трёхзначный код SMTP в начале ответа или после «said:» у Postfix.
BASIC = re.compile(r"(?:^|said:\s*)([245]\d\d)(?![\d.])", re.IGNORECASE)

#: Вид по теме и подробности расширенного кода (RFC 3463, «X.1.1»), когда текст не узнан.
CODES: dict[tuple[str, str], Trouble] = {
    ("1", "1"): Trouble.NO_MAILBOX,
    ("1", "3"): Trouble.NO_MAILBOX,
    ("1", "6"): Trouble.NO_MAILBOX,
    ("2", "1"): Trouble.NO_MAILBOX,
    ("1", "2"): Trouble.NO_MAIL_SERVER,
    ("1", "10"): Trouble.NO_MAIL_SERVER,
    ("4", "4"): Trouble.NO_MAIL_SERVER,
    ("2", "2"): Trouble.MAILBOX_FULL,
    ("4", "1"): Trouble.UNREACHABLE,
    ("4", "2"): Trouble.UNREACHABLE,
    ("4", "7"): Trouble.EXPIRED,
}
#: Вид по трёхзначному коду, когда нет ни слов, ни расширенного кода.
BASIC_CODES: dict[str, Trouble] = {"552": Trouble.MAILBOX_FULL, "553": Trouble.NO_MAILBOX}

#: Короткий код в скобках — по образцу в тексте: что именно случилось у вида.
HINTS: dict[Trouble, tuple[tuple[re.Pattern[str], str], ...]] = {
    Trouble.UNREACHABLE: (
        (_rx(r"time ?out|timed out|время ожидания"), "тайм-аут"),
        (_rx(r"refused"), "соединение отклонено"),
        (_rx(r"no route"), "нет маршрута"),
        (_rx(r"unreachable"), "сеть недоступна"),
        (
            _rx(r"reset|broken pipe|lost connection|connection (was )?(lost|closed|dropped)"),
            "связь оборвалась",
        ),
    ),
    Trouble.NO_MAIL_SERVER: (
        (_rx(r"\bmx\b"), "нет MX"),
        (
            _rx(r"nxdomain|no such (host|domain)|not found|does not exist|unknown"),
            "домен не найден",
        ),
    ),
    Trouble.TRY_LATER: ((_rx(r"gr[ae]y ?list"), "серый список"),),
    Trouble.SUPPRESSED: (
        (_rx(r"^bounced"), "раньше отказывал"),
        (_rx(r"^unsubscribed"), "отписался"),
        (_rx(r"^spam"), "жаловался на спам"),
    ),
}

#: Порт сервера получателя — после адреса IP: «192.0.2.10:25:», «[2001:db8::1]:25», «…]:25:».
PORT = re.compile(r"(?:\d{1,3}(?:\.\d{1,3}){3}|\]):(\d{2,5})\b")


@dataclass(frozen=True, slots=True)
class Reason:
    """Причина словами: вид, слова для человека и ответ почты как он был — без лишних пробелов
    и обрезанный под колонку письма."""

    kind: Trouble
    words: str
    text: str


def reason_of(text: str | None, *, status: str | None = None) -> Reason:
    """Вид причины и слова по ответу почты `text` и коду `status` события («4.4.1»)."""
    said = clean(text)
    kind = kind_of(said, status=status)
    return Reason(kind=kind, words=words_of(kind, said, status=status), text=said[:STORED_TEXT])


def clean(text: str | None) -> str:
    """Ответ почты одной строкой: многострочный ответ Gmail («550-5.1.1 …») — пробелами."""
    return " ".join((text or "").split())


def kind_of(text: str, *, status: str | None = None) -> Trouble:
    """Вид причины: первый совпавший образец таблицы, иначе — по коду SMTP, иначе неизвестное."""
    for kind, pattern in RULES:
        if pattern.search(text):
            return kind
    return _by_code(text, status)


def _by_code(text: str, status: str | None) -> Trouble:
    """Вид по коду SMTP, когда слова не узнаны: расширенный — по теме и подробности, иначе
    трёхзначный; кода нет — неизвестное."""
    code = ENHANCED.search(status or "") or ENHANCED.search(text)
    if code is not None:
        return _by_enhanced(*code.groups())
    basic = BASIC.search(text)
    return Trouble.UNKNOWN if basic is None else _by_basic(basic.group(1))


def _by_enhanced(klass: str, subject: str, detail: str) -> Trouble:
    """Расширенный код: по своей таблице, тема 7 — политика и безопасность, класс 4 —
    «повторите позже» (RFC 3463)."""
    found = CODES.get((subject, detail))
    if found is not None:
        return found
    if subject == "7":
        return Trouble.BLOCKED
    return Trouble.TRY_LATER if klass == "4" else Trouble.UNKNOWN


def _by_basic(code: str) -> Trouble:
    """Трёхзначный код: по своей таблице, класс 4 — «повторите позже»."""
    return BASIC_CODES.get(code, Trouble.TRY_LATER if code.startswith("4") else Trouble.UNKNOWN)


def words_of(kind: Trouble, text: str | None, *, status: str | None = None) -> str:
    """Слова вида с коротким кодом в скобках; у неизвестного — ответ почты, обрезанный."""
    said = clean(text)
    if kind is Trouble.UNKNOWN:
        if not said or said == NO_REASON:
            return "причину почта не назвала"
        return f"{WORDS[kind]}: «{_cut(said)}»"
    parts = _hints(kind, said)
    code = _code(said, status)
    if code is not None and kind is not Trouble.SUPPRESSED:
        parts.append(code)
    return WORDS[kind] if not parts else f"{WORDS[kind]} ({', '.join(parts)})"


def _hints(kind: Trouble, text: str) -> list[str]:
    """Что именно случилось у вида — порт и первый подошедший образец подсказки."""
    found = ["TLS"] if kind is Trouble.TLS else []
    if kind is Trouble.UNREACHABLE and (port := PORT.search(text)) is not None:
        found.append(f"порт {port.group(1)}")
    hint = next((said for pattern, said in HINTS.get(kind, ()) if pattern.search(text)), None)
    if hint is not None:
        found.append(hint)
    return found


def _code(text: str, status: str | None) -> str | None:
    """Код SMTP для скобок: расширенный из текста или события, иначе трёхзначный."""
    code = ENHANCED.search(text) or ENHANCED.search(status or "")
    if code is not None:
        return code.group(0)
    basic = BASIC.search(text)
    return None if basic is None else basic.group(1)


def _cut(text: str) -> str:
    return text if len(text) <= SHOWN_TEXT else f"{text[: SHOWN_TEXT - 1].rstrip()}…"
