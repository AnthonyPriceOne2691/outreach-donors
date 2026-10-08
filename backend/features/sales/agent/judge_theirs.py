"""Предложение о собеседнике у судьи продаж: опора — его письмо, а не запись базы.

Судья-модель называет утверждения черновика с номерами записей базы, на которые они
опираются (`judge.py`). Пересказ письма собеседника — не утверждение: промпт судьи так и
говорит (кроме сумм денег). Но сослаться на письмо в её ответе нечем — опора только номер
записи, — и граница плавает: живой замер судьи v5 на синтетике в двух прогонах из трёх
задержал хороший черновик фразой «Thank you for the details about your 3 stores.» —
«утверждение без опоры на базу» при `kb: []`.

**Правило кодом.** Утверждение модели, процитированное внутри предложения черновика о
собеседнике (`about_them`), не нарушение: опора у него — письмо собеседника. Предложение
о собеседнике:

- обращено к нему: слово о его письме («рассказали», «вопрос», «details», «asking») или
  «вы», «ваш», «you», «your» — не то «вам» и «you», что в «спасибо вам» и «thank you»;
- каждое слово — либо из короткого словаря обращения и связок (`_OWN`), либо слово в слово
  из того, что собеседник сказал о себе: из частей утверждений письма от первого лица
  («у нас», «мы», «we», «our», «I») без «вы» и «you» — не из вопросов и не из слов о нас.
  Имени, названия или слова о наших услугах, которых он о себе не писал, в предложении нет;
- числа — те же, что в этих частях письма;
- ни первого лица («мы», «наш», «я», «we», «our», «I», глагол на -ем и -им: «работаем»,
  «обсудим») — в черновике это мы;
- ни денег (сумма, знак валюты, слово о цене), ни обещания, результата, срока и будущего
  времени (`judge_cta.MONEY`, `judge_cta.PLEDGE`) — даже словами собеседника: сумма из
  письма — путь подтвердить чужую цену (решение владельца 07.10), «трафик вырастет» —
  обещание;
- без ссылки.

Слово в слово, без основ: пересказ в другом падеже («о вашем магазине» на «у нас магазин»)
правило оставляет модели, как до него, — в сторону осторожности. Обещания модели правило
не трогает: их ловит только она.

Остаточный риск (в сторону пропуска): безличный пересказ того, что собеседник сам написал о
нас в части от первого лица («We were told the audit is certified» → «Thank you for the
details: the audit is certified»). Черновик всё равно читает человек: автопилот у продаж
выключен кодом.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from backend.features.sales.agent import judge_cta, reading

#: Благодарность: «вам» и «you» сразу за ней — часть «спасибо вам», а не обращение.
_THANKS = frozenset(
    {"спасибо", "благодарю", "благодарим", "благодарны", "thank", "thanks", "grateful",
     "appreciate"}
)  # fmt: skip
#: Второе лицо: в черновике — собеседник, в его письме — мы.
_YOU = frozenset(
    {"вы", "вас", "вам", "вами", "ваш", "ваша", "ваше", "ваши", "вашего", "вашей", "вашему",
     "вашим", "вашими", "вашем", "вашу", "ваших",
     "you", "your", "yours", "yourself", "yourselves"}
)  # fmt: skip
#: Слова о письме собеседника — обращение к нему и без «вы».
_TOLD = frozenset(
    {"рассказали", "рассказ", "поделились", "написали", "ответили", "ответ", "сообщили",
     "уточнили", "описали", "напомнили", "подробности", "подробно", "детали", "письмо",
     "вопрос", "вопросы", "интерес",
     "details", "detail", "sharing", "shared", "telling", "told", "writing", "mentioning",
     "mentioned", "explaining", "describing", "letter", "message", "note", "reply", "answer",
     "question", "questions", "asking", "interest", "information", "context", "update"}
)  # fmt: skip
#: Связки: ни о ком не говорят.
_GLUE = frozenset(
    {"что", "за", "про", "о", "об", "на", "в", "с", "у", "и", "это", "очень", "большое",
     "the", "a", "an", "for", "about", "of", "on", "in", "with", "to", "that", "this", "and",
     "it", "very", "much", "so"}
)  # fmt: skip
#: Словарь обращения: эти слова можно писать, не беря их из письма.
_OWN = _THANKS | _YOU | _TOLD | _GLUE
#: Первое лицо: в письме собеседника — он сам, в черновике — мы.
_WE = frozenset(
    {"мы", "нас", "нам", "нами", "наш", "наша", "наше", "наши", "нашего", "нашей", "нашему",
     "нашим", "нашими", "нашем", "нашу", "наших",
     "я", "меня", "мне", "мной", "мною", "мой", "моя", "мое", "мои", "моего", "моей",
     "моему", "моим", "моими", "моем", "мою", "моих",
     "we", "us", "our", "ours", "ourselves", "i", "me", "my", "mine", "myself"}
)  # fmt: skip
#: Глагол первого лица множественного числа без «мы»: «работаем», «продаём», «обсудим».
#: Ловит и иное слово на -ем и -им («всем», «объём») — в сторону осторожности.
_WE_VERB = re.compile(r"[а-я]{2,}(?:ем|им)(?:ся)?")
_FUTURE = r"\b(?:will|shall|ll|going|буд(?:у|ем|ет|ут|ешь|ете)|стан(?:у|ем|ет|ут))\b"
#: Деньги, обещание, результат, срок и будущее время: их не снимает и слово собеседника.
_RISKY = re.compile(
    "|".join((judge_cta.SIGNS, judge_cta.MONEY, judge_cta.PLEDGE, _FUTURE)), re.IGNORECASE
)
#: Части предложения письма: знаки и союзы; запятая между цифрами — часть числа.
_PARTS = re.compile(r"(?<!\d),|,(?!\d)|[;:()\[\]—–]|\s-\s|" + judge_cta.JOINS, re.IGNORECASE)


def _words(text: str) -> list[str]:
    """Слова без чисел: регистр и «ё» не в счёт, «let's» — «let us»."""
    plain = re.sub(r"\blet[’']s\b", "let us", text.casefold()).replace("ё", "е")
    return re.findall(r"[^\W\d_]+", plain)


def _of_self(part: str) -> bool:
    """Собеседник о себе: первое лицо есть, «вы» нет."""
    said = set(_words(part))
    return bool(said & _WE) and not said & _YOU


def told(letter: str) -> str:
    """Что собеседник сказал о себе: части утверждений письма от первого лица без «вы».

    Вопрос и часть со словом «вы» — о нас: «What does your audit include?», «I heard your
    audit is good». Части — строками: число на стыке двух частей не склеивается."""
    kept = [
        part
        for piece in reading.sentences(letter)
        if not piece.endswith("?")
        for part in _PARTS.split(piece)
        if _of_self(part)
    ]
    return "\n".join(kept)


def _addressed(words: list[str]) -> bool:
    """Обращено к собеседнику: слово о его письме или «вы» — не «вам» из «спасибо вам»."""
    thanked = {index + 1 for index, word in enumerate(words) if word in _THANKS}
    return any(
        word in _TOLD or (word in _YOU and index not in thanked) for index, word in enumerate(words)
    )


def _ours(words: Iterable[str]) -> bool:
    """Первое лицо в черновике — мы: местоимение или глагол на -ем и -им вне словаря."""
    return any(word in _WE or (word not in _OWN and _WE_VERB.fullmatch(word)) for word in words)


def _theirs(sentence: str, said: str) -> bool:
    if reading.links_in(sentence) or _RISKY.search(sentence) or reading.amounts_in(sentence):
        return False
    words = _words(sentence)
    if not _addressed(words) or _ours(words):
        return False
    known = _OWN | set(_words(said))
    if any(word not in known for word in words):
        return False
    return reading.numbers_in(sentence) <= reading.numbers_in(said)


def about_them(draft: str, letter: str) -> tuple[str, ...]:
    """Предложения черновика о собеседнике и его письме — ни слова о нас, деньгах и сроках.

    Пусто — таких предложений нет: каждое утверждение судит модель, как до правила."""
    said = told(letter)
    return tuple(piece for piece in reading.sentences(draft) if _theirs(piece, said))
