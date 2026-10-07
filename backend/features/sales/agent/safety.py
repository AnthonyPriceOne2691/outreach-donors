"""Сигнатуры инъекций в письме собеседника — L1 агента продаж (Spec 3.2 A5, 3.4).

Общая очистка шва (`agent/cleaning.py`) уже привела письмо к тому, что видит
человек: NFKC, без невидимых знаков, разметка ролей модели — меткой. Каталог
сигнатур — решение этапа: письмо, похожее на попытку управлять агентом, бриф
продаж отдаёт человеку до модели (`brief.held`), а не надеется на её стойкость.

Виды атак — как в корпусе канарейки (`scripts/data/sales_injection_corpus.jsonl`):

- **T1** — подмена инструкций: «игнорируйте предыдущие инструкции», «you are now a…»;
- **T2** — выведать промпт: «покажи системный промпт», «print your instructions»;
- **T3** — подмена реквизитов: «оплату — на новый кошелёк», «updated bank details»;
- **T4** — вынести чужое: «переписку других клиентов», «your API key»;
- **T5** — разметка с умыслом: `<script>`, скрытый блок, картинка-маячок, метки данных;
- **T6** — контрабанда знаками: теги Unicode, буквы двух алфавитов внутри слова.

**Похожие буквы.** Сигнатуры ищутся в трёх видах письма: как есть и с похожими
буквами, сведёнными к латинице и к кириллице: «іgnore» с украинской «і» и
«игнорируйте» с латинской «o» — та же атака. Слово, где алфавит меняется дважды
(«ignоre» с кириллической «о» внутри), — сигнатура сама по себе; «SEOшник» —
нет: алфавит в нём меняется один раз.

**Сигнатуры узкие**: повелительное наклонение и предмет («игнорируйте …
инструкции»), а не одно слово. Ложное срабатывание стоит минуты человека,
пропуск — черновика по чужой указке; что прошло мимо, ловит судья на выходе
(сумма не из базы, ссылка не из настроек).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from itertools import pairwise

_I = re.IGNORECASE


@dataclass(frozen=True, slots=True)
class Signature:
    #: Вид атаки по корпусу: T1–T6.
    kind: str
    name: str
    pattern: re.Pattern[str]


def _sig(kind: str, name: str, pattern: str, flags: int = _I) -> Signature:
    return Signature(kind, name, re.compile(pattern, flags))


#: Между словами одного предложения: пробелы, запятые, тире, кавычки — но не его конец.
_W = r"[^\w.!?\n]+"
#: Сколько слов терпим между глаголом и предметом: «ignore all of the previous instructions».
_GAP = rf"(?:{_W}\w+){{0,4}}?{_W}"
#: То же, но до трёх слов — перед «your/the», «ваши/свои».
_GAP3 = rf"(?:{_W}\w+){{0,3}}?{_W}"

SIGNATURES: tuple[Signature, ...] = (
    # T1 — подмена инструкций.
    _sig(
        "T1",
        "ignore_instructions_en",
        rf"\b(?:ignore|disregard|forget|override|bypass)\b{_GAP}"
        r"(?:instructions?|rules|prompts?|guidelines|directives|guardrails)\b",
    ),
    _sig(
        "T1",
        "ignore_instructions_ru",
        r"\b(?:игнорируй(?:те)?|проигнорируй(?:те)?|забудь(?:те)?|отбрось(?:те)?|отмени(?:те)?"
        rf"|обойди(?:те)?)\b{_GAP}(?:инструкци|указани|правил|промпт|ограничени)\w*",
    ),
    _sig(
        "T1",
        "new_instructions",
        r"\b(?:new|updated|real|actual)\s+(?:system\s+)?instructions?\s*:"
        r"|\b(?:новые|настоящие|обновл[её]нные)\s+(?:системные\s+)?инструкции\s*:",
    ),
    _sig(
        "T1",
        "role_switch",
        r"\byou\s+are\s+now\s+(?:a|an|the|my|in)\b|\bfrom\s+now\s+on,?\s+you\s+(?:are|will|must)\b"
        r"|\bdeveloper\s+mode\b|\bjailbreak|\bdo\s+anything\s+now\b"
        r"|\b(?:ты|вы)\s+теперь\s+(?:\S+\s+){0,2}?(?:бот|ассистент|помощник|модель|ии)\b"
        r"|\bрежим\w*\s+разработчика\b",
    ),
    _sig(
        "T1",
        "addressed_to_the_model",
        r"\bif\s+you\s+are\s+an?\s+(?:ai|llm|language\s+model|assistant|bot|chatbot)\b"
        r"|\b(?:note|message|instructions?)\s+(?:to|for)\s+(?:the\s+)?(?:ai|llm|assistant|chatbot)\b"
        r"|\bесли\s+(?:ты|вы)\s+(?:—\s*)?(?:ии|ai|бот|нейросеть|ассистент|языковая\s+модель)\b"
        r"|\b(?:для|к)\s+(?:ии|нейросети|ассистента|бота|языковой\s+модели)\s*:",
    ),
    _sig("T1", "role_label", r"(?m)^\s*(?:system|assistant|система|ассистент)\s*:"),
    # T2 — выведать промпт и правила.
    _sig(
        "T2",
        "system_prompt",
        r"\bsystem\s+prompt\b|\binitial\s+(?:prompt|instructions)\b"
        r"|\bсистемн\w*\s+(?:промпт|инструкци|подсказк)\w*",
    ),
    _sig(
        "T2",
        "reveal_instructions",
        rf"\b(?:print|show|reveal|repeat|output|display|dump|tell\s+me|write\s+out)\b{_GAP3}"
        rf"(?:your{_W}(?:\w+{_W})?(?:instructions|prompt)\b"
        rf"|(?:the|all){_W}(?:\w+{_W})?(?:instructions|prompt){_W}(?:you|above|given)\b)"
        rf"|\bwhat\s+(?:are|were)\s+your{_W}(?:\w+{_W})?instructions\b"
        rf"|\b(?:покажи|выведи|повтори|перечисли|раскрой|скопируй)(?:те)?\b{_GAP3}"
        rf"(?:сво|тво|ваш)\w*{_W}(?:\w+{_W})?(?:инструкци|промпт)\w*"
        rf"|\bкакие\s+у\s+тебя{_W}(?:\w+{_W})?(?:инструкци|правил|промпт)\w*",
    ),
    # T3 — подмена реквизитов и платёжной ссылки.
    _sig(
        "T3",
        "payment_details_changed",
        r"\b(?:new|updated|changed|different|correct)\s+(?:bank(?:ing)?|payment|wire|wallet)\s+"
        r"(?:details|information|info|account|address)\b"
        r"|\b(?:новые|обновл[её]нные|изменённые|измененные|другие)\s+"
        r"(?:банковские\s+|платёжные\s+|платежные\s+)?реквизит\w*"
        r"|\bреквизит\w*\s+(?:изменились|поменялись|сменились|обновились)\b"
        r"|\b(?:изменились|поменялись|сменились|обновились)\s+(?:\S+\s+)?реквизит\w*",
    ),
    _sig(
        "T3",
        "pay_somewhere_else",
        r"\b(?:send|transfer|wire|pay)\b[^.\n]{0,40}\b(?:to|into)\s+(?:this|our|my|the\s+following"
        r"|the\s+new)\s+(?:new\s+)?(?:wallet|account|iban|address|card)\b"
        r"|\b(?:переведите|отправьте|оплатите|перечислите)\b[^.\n]{0,40}\bна\s+(?:этот|наш|мой"
        r"|новый|новую|другой|другую|следующий)\s+(?:новый\s+)?(?:кошел[её]к|сч[её]т|карту|адрес)",
    ),
    _sig("T3", "crypto_wallet", r"\b(?:usdt|trc-?20|erc-?20|bep-?20|wallet\s+address)\b"),
    _sig(
        "T3",
        "put_it_in_the_reply",
        r"\b(?:include|add|insert|put|paste)\b[^.\n]{0,30}\b(?:link|url|address|wallet|details)\b"
        r"[^.\n]{0,30}\b(?:in|into|to)\s+(?:your|the|all|every)\s+(?:reply|replies|response"
        r"|responses|answer|email|emails|message|signature)\b"
        r"|\b(?:include|add|insert|put|paste)\s+in(?:to)?\s+(?:your|the|all|every)\s+(?:reply"
        r"|replies|response|responses|answer|email|emails|message|signature)\b[^.\n]{0,30}"
        r"\b(?:link|url|address|wallet|details)\b"
        r"|\b(?:добавь|вставь|поставь)(?:те)?\b[^.\n]{0,30}\b(?:ссылк|адрес|реквизит|кошел)\w*"
        r"[^.\n]{0,30}\bв\s+(?:ответ|ответы|письмо|подпись)\b"
        r"|\b(?:добавь|вставь|поставь)(?:те)?\s+в\s+(?:ответ|ответы|письмо|подпись)\b[^.\n]{0,30}"
        r"\b(?:ссылк|адрес|реквизит|кошел)\w*",
    ),
    # T4 — вынести чужие данные и ключи.
    _sig(
        "T4",
        "other_clients_data",
        r"\b(?:emails?|addresses|contacts?|phone\s+numbers|conversations?|correspondence|data"
        r"|list)\s+(?:of|from|with)\s+(?:your\s+)?(?:other|all|previous)\s+"
        r"(?:clients|customers|leads|users)\b"
        r"|\b(?:other|previous)\s+(?:clients|customers|leads|users)'?\s*(?:emails?|addresses"
        r"|contacts?|phone\s+numbers|conversations?|correspondence)\b"
        r"|\b(?:переписк|контакт|адрес|почт|телефон|данн|список)\w*\s+(?:других|всех|прошлых"
        r"|остальных)\s+(?:клиент|покупател|лид|пользовател)\w*",
    ),
    _sig(
        "T4",
        "credentials",
        r"\b(?:your|the)\s+(?:api[\s_-]?keys?|access\s+tokens?|passwords?|credentials"
        r"|private\s+keys?|secret\s+keys?)\b"
        r"|\b(?:ваш|твой|свой)\w*\s+(?:api[\s-]?ключ|ключ\w*\s+(?:api|доступа)|парол|токен)\w*",
    ),
    # T5 — разметка с умыслом.
    _sig(
        "T5",
        "active_markup",
        r"<\s*(?:script|iframe|object|embed|form|meta|style)\b|\bon(?:error|load|click|mouseover)\s*="
        r"|javascript\s*:",
    ),
    _sig(
        "T5",
        "hidden_text",
        r"display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0(?![.\d]*[1-9])"
        r"|color\s*:\s*(?:#fff(?:fff)?|white)\b|<!--",
    ),
    _sig("T5", "markdown_beacon", r"!\[[^\]]*\]\(\s*(?:https?:)?//"),
    _sig("T5", "data_marker", r"<<<|>>>"),
    # T6 — контрабанда знаками.
    _sig("T6", "unicode_tags", "[\U000e0000-\U000e007f]", 0),
)

#: Вид атаки — словами для причины, которую видит человек.
KINDS = {
    "T1": "подмена инструкций",
    "T2": "попытка выведать промпт",
    "T3": "подмена реквизитов",
    "T4": "вынос чужих данных",
    "T5": "разметка с умыслом",
    "T6": "контрабанда знаками",
}
#: Сигнатура слова, где алфавит меняется дважды: её нет в `SIGNATURES` — это не регулярка.
MIXED = Signature("T6", "mixed_script_word", re.compile("(?!)"))

#: Похожие буквы — к латинице (для сигнатур по-английски) и к кириллице (по-русски).
#: Кириллица кодами: глазом «а» и «a» не различить. Здесь кириллические
#: а е о р с у х к м т н в, украинская і, ј, ѕ, ԁ, ӏ — и латинские пары им.
_CYRILLIC = (
    "\u0430\u0435\u043e\u0440\u0441\u0443\u0445\u043a\u043c\u0442\u043d\u0432"
    "\u0456\u0458\u0455\u0501\u04cf"
)
_LATIN = "aeopcyxkmthbijsdl"
_TO_LATIN = str.maketrans(_CYRILLIC, _LATIN)
_TO_CYRILLIC = str.maketrans(_LATIN[:12], _CYRILLIC[:12])
_SCRIPTS = {"LATIN": "L", "CYRILLIC": "C"}


def _views(text: str) -> tuple[str, ...]:
    """Письмо как есть и с похожими буквами, сведёнными к одному алфавиту."""
    folded = text.casefold()
    return text, folded.translate(_TO_LATIN), folded.translate(_TO_CYRILLIC)


def _script(char: str) -> str:
    return _SCRIPTS.get(unicodedata.name(char, "?").split(" ", 1)[0], "?")


def mixed_words(text: str) -> list[str]:
    """Слова, где алфавит букв меняется дважды и больше: «ignоre» с кириллической «о»."""
    found = []
    for word in re.findall(r"\w+", text):
        scripts = [_script(char) for char in word if char.isalpha()]
        if sum(1 for one, two in pairwise(scripts) if one != two) >= 2:
            found.append(word)
    return found


def signatures(text: str) -> list[Signature]:
    """Сигнатуры инъекций в письме — по порядку каталога, без повторов. Пусто — чисто."""
    views = _views(text)
    found = [sig for sig in SIGNATURES if any(sig.pattern.search(view) for view in views)]
    return [*found, MIXED] if mixed_words(text) else found


def threat(text: str) -> str | None:
    """Почему письмо — человеку, словами: виды атак и сигнатуры. `None` — сигнатур нет."""
    found = signatures(text)
    if not found:
        return None
    kinds = ", ".join(f"{kind} {KINDS[kind]}" for kind in dict.fromkeys(sig.kind for sig in found))
    names = ", ".join(sig.name for sig in found)
    return f"в письме сигнатуры инъекции ({kinds}: {names}) — похоже на попытку управлять агентом"
