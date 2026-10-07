"""Очистка переписки для агента — одна на все этапы.

Письмо собеседника идёт в модель как недоверенные данные (`writer.py`), но
до этого его надо привести к тому, что человек видит глазами:

- **NFKC** — «полноширинные» буквы, лигатуры и составные знаки — к обычным:
  слово, набранное знаками другого вида, не проходит мимо проверок;
- **невидимые знаки** — нулевой ширины, смена направления письма, мягкий
  перенос — прочь, управляющие — в пробел: текст, который человек не видит,
  модель читать не должна;
- **разметка ролей модели** — `<|im_start|>`, `[INST]`, `<<SYS>>` — в
  нейтральную метку: собеседник не открывает «системный» блок своим письмом;
- **цитата нашего письма и подпись** — прочь (`replies/quoting.written_by_hand`):
  в цитате наши же слова, и модель ответила бы на свой вопрос.

Что найдено — словами в `notes`: черновик несёт их в `meta`, а бриф этапа
может отдать такой ответ человеку. Каталог сигнатур инъекций — решение
этапа (его бриф), а не очистки: она меняет только то, что не меняет смысла.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from backend.features.replies.quoting import written_by_hand

#: Нулевой ширины, смена направления письма, мягкий перенос, монгольский
#: разделитель — то, что не видно глазами. Кодами, а не знаками: невидимый
#: знак в исходнике не виден и на ревью.
_INVISIBLE: dict[int, None] = dict.fromkeys(
    (
        *(0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0x00AD, 0x180E),
        *range(0x202A, 0x202F),
        *range(0x2066, 0x206A),
    )
)
#: Управляющие, кроме переноса строки, возврата каретки и табуляции.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
#: Разметка ролей моделей: ChatML, Llama, «системный» блок.
_ROLE_MARKUP = re.compile(
    r"<\|\s*(?:im_start|im_end|system|user|assistant|endoftext)\s*\|>"
    r"|\[\s*/?\s*(?:INST|SYS)\s*\]|<<\s*/?\s*SYS\s*>>",
    re.IGNORECASE,
)
ROLE_PLACEHOLDER = "[разметка убрана]"


@dataclass(frozen=True, slots=True)
class Cleaned:
    text: str
    #: Что убрано — словами; пусто — письмо было чистым.
    notes: tuple[str, ...] = ()


def clean(text: str) -> Cleaned:
    """Письмо собеседника — тем, что человек видит, без нашей цитаты."""
    notes: list[str] = []
    normal = unicodedata.normalize("NFKC", text)
    visible = _CONTROL.sub(" ", normal.translate(_INVISIBLE))
    if visible != normal:
        notes.append("невидимые и управляющие знаки")
    plain, markup = _ROLE_MARKUP.subn(ROLE_PLACEHOLDER, visible)
    if markup:
        notes.append(f"разметка ролей модели ({markup})")
    return Cleaned(text=written_by_hand(plain), notes=tuple(notes))
