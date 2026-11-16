"""Переадресация в «Диалогах» (16.11.2026): экран называет письмо переадресации по шагу
(`ThreadFeed.letterTitle`) и знает каждое слово состояния — те же, что у сервера.

Без сверки сервер добавил бы состояние, и экран показал бы его голым словом серой меткой, а сменил
бы номер шага — письмо на новый адрес снова звалось бы «наш ответ»; сборка не заметила бы ни того,
ни другого. Тип экрана — своим файлом (`frontend/src/api/redirectTypes.ts`): `types.ts` у потолка
длины.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

from backend.features.letters.chain import REDIRECT_STEP
from backend.features.replies.redirect_shown import Kind

FRONT = Path(__file__).resolve().parent.parent / "frontend" / "src" / "api" / "redirectTypes.ts"


def test_the_screen_knows_the_redirect_step() -> None:
    found = re.search(r"export const REDIRECT_STEP = (\d+);", FRONT.read_text(encoding="utf-8"))

    assert found is not None
    assert int(found.group(1)) == REDIRECT_STEP


def test_the_screen_knows_every_redirect_state() -> None:
    found = re.search(r"export type RedirectState =([^;]+);", FRONT.read_text(encoding="utf-8"))

    assert found is not None
    assert set(re.findall(r"'([a-z_]+)'", found.group(1))) == set(get_args(Kind))
