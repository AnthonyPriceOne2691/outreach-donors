"""Цитата судьи сегмента: есть ли слова модели в строке текста сайта.

Цитата — доказательство, что модель прочла сайт, а не сочинила (`segment_judge`). Сверка идёт
внутри ОДНОЙ строки сайта и прощает три вещи, которые честная цитата делает сама:

1. **Типографику** (`_plain`, поверх общего `keywords/hygiene.normalize`): кавычки, многоточие-
   пропуск, знаки по краям, невидимые знаки, ё/е, регистр — модель ставит их иначе, чем сайт.
2. **Нашу подпись строки в начале** (`unlabeled`): модель переписывает строку целиком —
   «Заголовок в выдаче: …», — хотя промпт просит подпись не цитировать. Подпись — наши слова,
   а не сайта: снимается, остальное сверяется как обычно. Список подписей даёт тот, кто строит
   текст сайта (`segment_judge.LABELS`), — своей копии здесь нет.
3. **Опечатку переписывания** (`_nearly`): модель переписала длинное слово — чаще капсом — с
   ошибкой в одну-две буквы. Мера — по словам, без правил отдельных языков: каждое слово цитаты
   совпадает со словом строки на том же месте точно или не больше чем одной правкой (замена,
   вставка, пропуск знака) на `LETTERS_PER_EDIT` букв слова сайта; неточных слов — не больше одного
   на `WORDS_PER_MISS` слов цитаты; слов — не меньше `MIN_QUOTE_WORDS`; число и порядок слов —
   как в строке. Порог высокий нарочно: слово короче семи букв — только точно (в коротком слове
   одна буква — уже другое слово: «мебель» и «мебели», «now» и «not»), а пересказ меняет слова,
   их число и порядок — и не проходит. Письменности без пробелов (иероглифы, тайский) — только
   точно: знак там — уже слово, длинных слов, где видна опечатка, у них нет.

Слово — буквы со своими знаками (огласовки, вирамы, ударения) и цифры подряд (`_tokens`):
`\\w` регулярных выражений рвёт слово хинди или арабское с огласовками на куски.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

from backend.features.keywords.hygiene import normalize

#: Цитата короче — не доказательство: одно-два слова найдутся в любом меню. Столько же просит
#: промпт («3 to 30 words»).
MIN_QUOTE_WORDS = 3
#: Опечатка: не больше одной правки на столько букв слова сайта (7–13 букв — одна, 14–20 — две);
#: мерило — слово сайта, а не цитаты: лишняя буква не делает короткое слово длинным.
LETTERS_PER_EDIT = 7
#: Неточных слов — не больше одного на столько слов цитаты (3–5 слов — одно, 6–8 — два).
WORDS_PER_MISS = 3

#: Двойные кавычки всех видов, одинарные ёлочки и уголки японского письма: сайт пишет «ёлочки»,
#: модель — прямые, а то и никаких.
_QUOTES = str.maketrans("", "", '"«»“”„‟″〝〞‹›「」『』')
#: Многоточие внутри цитаты — пропуск: модель выкинула кусок строки. После NFKC «…» — три точки.
_GAP = re.compile(r"\.{3,}")
#: Письменности без пробелов между словами: слово в них — около двух знаков.
_UNSPACED = ("CJK", "HIRAGANA", "KATAKANA", "IDEOGRAPHIC", "THAI", "LAO", "KHMER", "MYANMAR")
#: Чем модель оборачивает строку вместе с подписью: кавычки и скобки — и точка после них.
_OPENING = "\"'«“„‘‚「『(["
_CLOSING = "\"'»”“’」』)].,;:"


def _plain(text: str) -> str:
    """Строка для сверки цитаты — поверх общего `normalize` (NFKC, тире и апострофы одним
    знаком, пробелы свёрнуты, нижний регистр): без невидимых знаков (мягкий перенос, нулевой
    ширины — категория Cf) и кавычек, `casefold`, ё = е."""
    visible = "".join(char for char in text if unicodedata.category(char) != "Cf")
    bare = normalize(visible).translate(_QUOTES)
    return " ".join(bare.split()).casefold().replace("ё", "е")


def _in_word(char: str) -> bool:
    """Знак слова: буква, знак при букве (огласовка, вирама, ударение) или цифра."""
    return unicodedata.category(char)[0] in "LMN"


def _tokens(text: str) -> list[str]:
    """Слова строки: знаки слова подряд."""
    words: list[str] = []
    word: list[str] = []
    for char in text:
        if _in_word(char):
            word.append(char)
        elif word:
            words.append("".join(word))
            word = []
    if word:
        words.append("".join(word))
    return words


def _trim(part: str) -> str:
    """Знаки по краям куска цитаты — хвостовая точка, запятая, скобка модели — вон."""
    start, end = 0, len(part)
    while start < end and not _in_word(part[start]):
        start += 1
    while end > start and not _in_word(part[end - 1]):
        end -= 1
    return part[start:end]


def _pieces(quote: str) -> list[str]:
    """Цитата → куски между многоточиями-пропусками, без знаков по краям; пустые — вон."""
    return [piece for part in _GAP.split(_plain(quote)) if (piece := _trim(part))]


def _spaced(text: str) -> str:
    """Слова через пробел и пробелы по краям: так кусок ищется целыми словами, а знаки между
    словами — запятая, тире, кавычка, пробел у скобки — не в счёт."""
    return f" {' '.join(_tokens(text))} "


def _in_order(pieces: Sequence[str], line: str) -> bool:
    """Куски — в строке по порядку, каждый после предыдущего (у слов — общий пробел на стыке)."""
    start = 0
    for piece in pieces:
        found = line.find(piece, start)
        if found < 0:
            return False
        start = found + len(piece) - piece.endswith(" ")
    return True


def _unspaced(word: str) -> bool:
    return any(unicodedata.name(char, "").startswith(_UNSPACED) for char in word)


def _edits(first: str, second: str, cap: int) -> int:
    """Расстояние Левенштейна (замена, вставка, пропуск знака); больше `cap` — `cap + 1`:
    дальше считать незачем."""
    previous = list(range(len(second) + 1))
    for row, char in enumerate(first, 1):
        current = [row]
        for column, other in enumerate(second, 1):
            current.append(
                min(previous[column] + 1, current[-1] + 1, previous[column - 1] + (char != other))
            )
        if min(current) > cap:
            return cap + 1
        previous = current
    return previous[-1]


def _miss(word: str, site: str) -> int | None:
    """Слово цитаты против слова сайта: 0 — то же слово, 1 — опечатка переписывания, `None` —
    другое слово."""
    if word == site:
        return 0
    allowed = len(site) // LETTERS_PER_EDIT
    if not allowed or _unspaced(word) or _unspaced(site):
        return None
    return 1 if _edits(word, site, allowed) <= allowed else None


def _window(piece: Sequence[str], words: Sequence[str], start: int) -> tuple[int, int] | None:
    """Первое место строки с `start`, где слова куска стоят подряд, точно или с опечаткой:
    где кончился кусок и сколько в нём неточных слов."""
    for first in range(start, len(words) - len(piece) + 1):
        misses = [_miss(word, words[first + n]) for n, word in enumerate(piece)]
        if None not in misses:
            return first + len(piece), sum(miss or 0 for miss in misses)
    return None


def _nearly(pieces: Sequence[str], line: str) -> bool:
    """Цитата — в строке по словам, с опечаткой переписывания (мера — в шапке модуля)."""
    split = [_tokens(piece) for piece in pieces]
    total = sum(len(words) for words in split)
    if total < MIN_QUOTE_WORDS:
        return False
    words, start, misses = _tokens(line), 0, 0
    for piece in split:
        found = _window(piece, words, start)
        if found is None:
            return False
        start, missed = found
        misses += missed
    return misses <= max(1, total // WORDS_PER_MISS)


def quote_holds(quote: str, line: str) -> bool:
    """Цитата — в одной строке сайта: дословно с точностью до типографики — строкой (иероглифы,
    часть слова) или целыми словами (знаки между словами не в счёт), — или по словам с опечаткой
    переписывания. Многоточие — пропуск: куски по обе его стороны — в той же строке и по порядку."""
    pieces = _pieces(quote)
    if not pieces:
        return False
    plain = _plain(line)
    exact = _in_order(pieces, plain) or _in_order([_spaced(p) for p in pieces], _spaced(plain))
    return exact or _nearly(pieces, plain)


def _size(word: str) -> int:
    return max(1, len(word) // 2) if _unspaced(word) else 1


def quote_words(quote: str) -> int:
    """Сколько слов в цитате; знаки и многоточие — не слова. Слово письменности без пробелов
    (иероглифы, тайский) — около двух знаков: иначе японская цитата всегда была бы «одним
    словом» и уходила бы человеку."""
    return sum(_size(word) for word in _tokens(_plain(quote)))


def unlabeled(quote: str, labels: Sequence[str]) -> str:
    """Цитата без нашей подписи строки в начале («Заголовок в выдаче: …»), регистр подписи не
    важен; кавычки, которыми модель обернула строку вместе с подписью, — тоже вон. Подписи нет —
    цитата как есть."""
    bare = quote.strip()
    head = bare.lstrip(_OPENING + " ")
    for label in labels:
        if head[: len(label)].casefold() != label.casefold():
            continue
        rest = head[len(label) :].lstrip()
        if rest.startswith(":"):
            words = rest[1:].strip()
            return words.rstrip(_CLOSING + " ") if head != bare else words
    return bare
