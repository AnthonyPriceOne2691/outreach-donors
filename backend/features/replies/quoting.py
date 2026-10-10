"""Что в ответе написал человек, а что процитировала почта.

Файл существует из-за одной ловушки, и она дороже, чем кажется.
**В цитате лежит наше собственное письмо** — с вопросом про цену,
со списком ниш и «5–10 articles per month». Правила вида ответа нашли бы
в нём наши же слова и приписали их донору, а модель, получившая письмо
целиком, читает оба текста и отвечает на наш вопрос вместо ответа донора.

**Отрезаем по началу цитаты, а не по её содержимому.** Признаков цитаты
немного, они устойчивы и их видно целиком — список внизу. Угадывать,
какой абзац «похож на наш», значило бы отрезать написанное человеком.

**Шапку цитаты почта пишет на языке отправителя.** Outlook у немца
пишет «Von: / Gesendet: / An: / Betreff:», Gmail у француза — «… a écrit :».
Узнавая только английский и русский, мы отдавали модели наше письмо
как слова донора на шести рынках из восьми.

**Шапка — строка, которую написала почта, а не фраза, с которой она
начинается.** «El precio es 80 USD» начинается так же, как испанская
шапка «El lun, 28 sept 2026 … escribió:», а «De: …» — как французская
шапка Outlook. Поэтому шапка узнаётся по концу («написал:»), а блок
Outlook — по двум строкам-заголовкам подряд, а не по одной.

**Если отрезать нечего, берём текст целиком.** Ответ снизу под цитатой —
законный обычай, и письмо, из которого мы вырезали всё, хуже письма
с лишней цитатой.
"""

from __future__ import annotations

import re

#: «Написал» на языках рынков — чем кончается шапка цитаты: «… wrote:»,
#: «… a écrit :», «… napisał(a):», «Folgendes geschrieben:».
_WROTE = (
    r"wrote|a\s+écrit|escribió|ha\s+scritto|escreveu|napisał\(-?a\)|napisała|napisał|pisze"
    r"|geschrieben|geschreven|написал\(а\)|написала|написал|пишет"
)
#: Немецкий и нидерландский ставят глагол перед именем: «schrieb Anna Ro <…>:».
_WROTE_BEFORE_NAME = r"schrieb|schreef"
#: Пробелы и не больше одного переноса строки — внутри угловых скобок адреса.
_BREAK = r"[^\S\n]*(?:\n[^\S\n]*)?"
#: Адрес в угловых скобках: им кончается шапка Gmail по-русски, Яндекса
#: и Mail.ru — там глагола нет вовсе. Перенос строки внутри скобок — та же шапка:
#: Gmail рвёт длинную строку и там — «… Alex <адрес» и «>:» строкой ниже. До
#: проверки прода 10.10.2026 цитата резалась со второй строки (она начинается
#: с «>»), и первая строка шапки оставалась в ответе донора.
_ADDRESS = rf"<{_BREAK}[^<>\s@]+@[^<>\s]+{_BREAK}>\)?"
#: Двоеточие в конце строки. Пробел перед ним — французская типографика,
#: часто неразрывный.
_COLON_END = r"[^\S\n]*:[^\S\n]*$"
#: Время или дата: в шапке, перенесённой на две строки, они есть всегда,
#: в строке ответа — редко.
_WHEN = r"(?:\d{1,2}:\d{2}|\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\b(?:19|20)\d{2}\b)"
#: Строка шапки — не длиннее этого. Граница нужна не для точности, а для
#: времени: два неограниченных «сколько угодно символов» подряд на одной
#: строке в двести тысяч знаков (текст из HTML бывает одной строкой)
#: разбирались минуту — внутри вебхука, который платформа повторит.
_LINE = r"[^\n]{0,300}"

_HEADER_END = (
    rf"(?:\b(?:{_WROTE})|\b(?:{_WROTE_BEFORE_NAME})\b[^\n]{{0,200}}?|{_ADDRESS}){_COLON_END}"
)

#: Шапка Outlook: первая строка «от кого», за ней сразу вторая строка
#: заголовка на том же языке. Одна строка «De: …» — ещё не шапка: так
#: начинается и обычная фраза.
_OUTLOOK_FIELDS: dict[str, str] = {
    "From": r"Sent|Date|To|Cc|Subject",
    "Von": r"Gesendet|Datum|An|Cc|Betreff",
    # Французский, испанский и португальский начинают одинаково.
    "De": r"Envoyé|Date|À|A|Cc|Objet|Enviado(?:\s+el)?|Fecha|Para|Asunto|Enviad[oa](?:\s+em)?|Data|Assunto",
    "Da": r"Inviato|Data|A|Cc|Oggetto",
    "Van": r"Verzonden|Datum|Aan|Cc|Onderwerp",
    "Od": r"Wysłano|Wysłane|Data|Do|DW|Temat",
    "От": r"Отправлено|Дата|Кому|Копия|Тема",
}


def _field(names: str) -> str:
    """Строка-заголовок: «From:», «De :», «*Von:*» — жирный Outlook после
    перевода в текст приходит звёздочками."""
    return rf"[^\S\n]*\*?(?:{names})\*?[^\S\n]*:\*?"


def _outlook_block(first: str, rest: str) -> re.Pattern[str]:
    return re.compile(rf"^{_field(first)}[^\S\n]*\S[^\n]*\n{_field(rest)}", re.M | re.I)


#: Разделители перед процитированным или пересланным письмом.
_SEPARATORS = (
    r"Original\s+Message|Forwarded\s+message|Ursprüngliche\s+Nachricht|Weitergeleitete\s+Nachricht"
    r"|Message\s+d['’]origine|Message\s+transféré|Mensaje\s+original|Mensaje\s+reenviado"
    r"|Messaggio\s+originale|Messaggio\s+inoltrato|Mensagem\s+original|Mensagem\s+encaminhada"
    r"|Oorspronkelijk\s+bericht|Doorgestuurd\s+bericht|Wiadomość\s+oryginalna|Oryginalna\s+wiadomość"
    r"|Przekazana\s+wiadomość|Wiadomość\s+przekazana(?:\s+dalej)?|Исходное\s+сообщение"
    r"|Пересылаемое\s+сообщение|Пересланное\s+сообщение|Перенаправленное\s+сообщение"
)

#: Начало пересланного письма у Apple Mail — на языке почты.
_FORWARDED = (
    r"Begin\s+forwarded\s+message|Anfang\s+der\s+weitergeleiteten\s+Nachricht"
    r"|Début\s+du\s+message\s+(?:réexpédié|transféré)|Inicio\s+del\s+mensaje\s+reenviado"
    r"|Inizio\s+(?:del\s+)?messaggio\s+inoltrato|Início\s+da\s+mensagem\s+(?:re)?encaminhada"
    r"|Begin\s+doorgestuurd\s+bericht|Początek\s+przekazywanej\s+wiadomości"
    r"|Начало\s+(?:переадресованного|пересылаемого)\s+сообщения"
)

#: Строка, с которой начинается цитата. Порядок значения не имеет:
#: берём самое раннее совпадение по тексту.
_QUOTE_STARTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^>", re.M), "строка с «>»"),
    (
        re.compile(rf"^[^\S\n]*-{{2,}}[^\S\n]*(?:{_SEPARATORS})[^\S\n]*-{{2,}}", re.M | re.I),
        "разделитель почты",
    ),
    (re.compile(rf"^[^\S\n]*(?:{_FORWARDED}){_COLON_END}", re.M | re.I), "пересланное письмо"),
    # «On Mon, 28 Sep 2026 at 10:04, Anna Ro <…> wrote:», «Anna Ro a écrit :».
    (
        re.compile(rf"^[^\S\n]*\S[^\n]{{0,300}}?\b(?:{_WROTE}){_COLON_END}", re.M | re.I),
        "… написал:",
    ),
    # «Am Mo., 28. Sept. 2026 um 10:04 schrieb Anna Ro <…>:».
    (
        re.compile(
            rf"^[^\S\n]*\S[^\n]{{0,300}}?\b(?:{_WROTE_BEFORE_NAME})\b[^\n]{{0,200}}?{_COLON_END}",
            re.M | re.I,
        ),
        "… schrieb …:",
    ),
    # «пн, 28 сент. 2026 г. в 10:04, Anna Ro <…>:» — Gmail, Яндекс, Mail.ru.
    (
        re.compile(rf"^(?={_LINE}\d){_LINE}?{_ADDRESS}{_COLON_END}", re.M | re.I),
        "… <адрес>:",
    ),
    # Шапка, перенесённая почтой на вторую строку: «On Mon, … Anna Ro <…>» /
    # «wrote:». Первая строка — с датой или временем, не конец фразы и
    # стоит после пустой строки, как всякая шапка; во второй даты нет —
    # она осталась в первой. Иначе строка ответа «Price: $100, live on
    # 05.10.2026» над настоящей шапкой стала бы её началом, и цена ушла бы
    # вместе с цитатой.
    (
        re.compile(
            rf"(?:\A|(?<=\n\n)|(?<=\n\r\n))(?={_LINE}{_WHEN}){_LINE}[^.!?\s][^\S\n]*\n"
            rf"(?!{_LINE}{_WHEN}){_LINE}?{_HEADER_END}",
            re.M | re.I,
        ),
        "шапка в две строки",
    ),
    *((_outlook_block(first, rest), f"блок {first}:") for first, rest in _OUTLOOK_FIELDS.items()),
    (re.compile(r"^_{5,}\s*$", re.M), "черта из подчёркиваний"),
)

#: Подпись отправителя: всё после неё принадлежит ему, а не разговору.
#: Отрезается отдельно от цитаты, потому что стоит до неё.
_SIGNATURE = re.compile(r"^-- \s*$", re.M)


def quote_starts_at(text: str) -> tuple[int | None, str | None]:
    """Где начинается цитата и по какому признаку её узнали.

    Признак возвращается не для ветвления, а для отчёта: если цитаты
    вдруг перестали узнаваться, это видно по числу, а не по жалобе.
    """
    earliest: int | None = None
    reason: str | None = None
    for pattern, title in _QUOTE_STARTS:
        found = pattern.search(text)
        if found is not None and (earliest is None or found.start() < earliest):
            earliest, reason = found.start(), title
    return earliest, reason


def written_by_hand(text: str) -> str:
    """Та часть письма, которую написал человек.

    Пустой результат не возвращается никогда: письмо, из которого мы
    вырезали всё, хуже письма с лишней цитатой.
    """
    if not text:
        return ""

    at, _ = quote_starts_at(text)
    head = text[:at] if at is not None else text

    signature = _SIGNATURE.search(head)
    if signature is not None:
        head = head[: signature.start()]

    head = head.strip()
    return head or text.strip()
