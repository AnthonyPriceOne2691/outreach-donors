"""Страница отписки: разметка и ничего больше.

**Страница говорит на языке письма, а комментарии по-русски** — и это не
разнобой. Пришедший по ссылке из письма должен узнать на странице то, что
видел в письме: иначе он решит, что попал не туда, и нажмёт «спам» вместо
кнопки. Письма доноров уходят на английском — и страница по-английски;
письмо продаж лиду с языком `ru` — по-русски (`Voice.language`).

**Внизу — кто прислал письмо, тем же именем и адресом, что в письме.** У доноров
и рекламодателей — имя и адрес из окружения (`OUTREACH_*`), у письма продаж —
«Отправитель» продаж из базы (`signer.py`). Адрес — одной строкой: так его
требует закон о рассылках и так он стоит внизу письма.

**Ни одного внешнего файла.** Ни шрифтов, ни скриптов, ни картинок:
страница обязана открыться у кого угодно и через год, а каждая внешняя
ссылка — это ещё один способ не открыться. По той же причине здесь нет
фронта: одностраничное приложение упало бы вместе со сборкой, и донор
увидел бы белый экран вместо отписки, которую мы обещали в письме.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

from backend.config import outreach as cfg

_STYLE = """
:root { color-scheme: light dark; }
body {
  margin: 0; min-height: 100vh; display: grid; place-items: center;
  font: 16px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  background: #f4f6f8; color: #1d2430; padding: 24px;
}
main { max-width: 34rem; background: #fff; border-radius: 14px;
       border: 1px solid #ccd4de; padding: 32px;
       box-shadow: 0 1px 3px rgba(20,30,50,.12); }
h1 { font-size: 1.35rem; margin: 0 0 .75rem; }
p { margin: 0 0 1rem; }
.host { font-weight: 600; }
button {
  font: inherit; font-weight: 600; cursor: pointer; color: #fff;
  background: #0d6b6e; border: 0; border-radius: 9px; padding: 12px 22px;
}
button:hover { background: #0a5457; }
footer { margin-top: 1.5rem; font-size: .82rem; color: #5c6775; }
@media (prefers-color-scheme: dark) {
  body { background: #11161d; color: #e6ebf2; }
  main { background: #1a212b; border-color: #2f3a49; box-shadow: none; }
  footer { color: #97a3b4; }
  button { background: #2aa5a8; color: #07131a; }
  button:hover { background: #46bcbf; }
}
"""

#: Слова страницы по языку письма; язык без своих слов — английский.
_WORDS: dict[str, dict[str, str]] = {
    "en": {
        "ask": "Unsubscribe",
        "question": "Stop sending emails about {host}?",
        "scope": "This covers every address we have for this site, "
        "including messages already scheduled.",
        "button": "Unsubscribe",
        "done": "Unsubscribed",
        "done_head": "You're unsubscribed",
        "done_line": "We will not email {host} again.",
        "done_tail": "Nothing else is needed from you.",
    },
    "ru": {
        "ask": "Отписка",
        "question": "Больше не присылать письма на адреса {host}?",
        "scope": "Отписка касается всех адресов этого сайта, которые у нас есть, "
        "и уже запланированных писем.",
        "button": "Отписаться",
        "done": "Вы отписаны",
        "done_head": "Вы отписаны",
        "done_line": "Мы больше не будем писать на адреса {host}.",
        "done_tail": "Больше ничего делать не нужно.",
    },
}


@dataclass(frozen=True, slots=True)
class Voice:
    """Чьим именем подписана страница и на каком языке она говорит."""

    name: str | None
    #: Физический адрес; в несколько строк — на странице одной строкой.
    address: str | None
    #: Язык письма (`ru`, `en`): у языка без своих слов страница по-английски.
    language: str = "en"

    @property
    def lang(self) -> str:
        """Язык страницы — тот, слова которого есть."""
        return self.language if self.language in _WORDS else "en"


def donors() -> Voice:
    """Подпись доноров и рекламодателей: имя и адрес из окружения, по-английски."""
    return Voice(cfg.SENDER_NAME, cfg.POSTAL_ADDRESS)


def _shell(title: str, body: str, voice: Voice) -> str:
    return (
        "<!doctype html>\n"
        f'<html lang="{voice.lang}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        # Робот поисковика на этой странице не нужен: у неё один читатель,
        # и он пришёл по ссылке из письма.
        '<meta name="robots" content="noindex, nofollow">'
        f"<title>{escape(title)}</title><style>{_STYLE}</style></head>"
        f"<body><main>{body}{_signature(voice)}</main></body></html>"
    )


def _one_line(address: str | None) -> str:
    """Адрес одной строкой: строки адреса — через запятую, пустые выпадают."""
    return ", ".join(line.strip() for line in (address or "").splitlines() if line.strip())


def _signature(voice: Voice) -> str:
    """Кто прислал письмо — тем же составом, что и подпись внизу письма.

    Страница без отправителя и адреса выглядит как чужая: человек пришёл
    с письма, и здесь он должен увидеть то же имя, что стояло под ним.
    """
    parts = [part for part in ((voice.name or "").strip(), _one_line(voice.address)) if part]
    if not parts:
        return ""
    return f"<footer>{escape(' · '.join(parts))}</footer>"


def confirm(host: str, *, action: str, voice: Voice | None = None) -> str:
    """Спросить, прежде чем отписывать.

    Кнопка, а не срабатывание на переходе: по ссылкам в письмах ходят
    почтовые сканеры и предпросмотры, и отписка одним переходом
    сработала бы за донора, который её не нажимал.
    """
    said = voice or donors()
    words = _WORDS[said.lang]
    site = f'<span class="host">{escape(host)}</span>'
    return _shell(
        words["ask"],
        (
            f"<h1>{words['ask']}</h1>"
            f"<p>{words['question'].format(host=site)}</p>"
            f"<p>{words['scope']}</p>"
            f'<form method="post" action="{escape(action)}">'
            f'<button type="submit">{words["button"]}</button></form>'
        ),
        said,
    )


def done(host: str, voice: Voice | None = None) -> str:
    """Готово. Тот же текст и на повторное нажатие: для донора разницы нет."""
    said = voice or donors()
    words = _WORDS[said.lang]
    site = f'<span class="host">{escape(host)}</span>'
    return _shell(
        words["done"],
        (
            f"<h1>{words['done_head']}</h1>"
            f"<p>{words['done_line'].format(host=site)}</p>"
            f"<p>{words['done_tail']}</p>"
        ),
        said,
    )


def gone() -> str:
    """Метки нет, подпись не сходится или донора удалили.

    Все три случая выглядят одинаково намеренно: по разным ответам метку
    подбирают, а донору разница всё равно ни о чём не говорит. Чьё письмо,
    здесь не узнать — подпись доноров и английский, как всегда.
    """
    return _shell(
        "Link expired",
        (
            "<h1>This link no longer works</h1>"
            "<p>Reply to any of our emails with the word "
            "<strong>unsubscribe</strong> and we will take care of it.</p>"
        ),
        donors(),
    )


def too_often() -> str:
    """Слишком часто. Страницу дёргают не люди, а роботы."""
    return _shell(
        "Too many requests",
        "<h1>Too many requests</h1><p>Please try again in a minute.</p>",
        donors(),
    )
