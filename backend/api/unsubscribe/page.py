"""Страница отписки: разметка и ничего больше.

**Страница по-английски, а комментарии по-русски** — и это не разнобой.
Письма уходят на английском, значит и страница, на которую ведёт ссылка
из письма, встречает донора на языке письма. Пришедший с русской
страницы решит, что попал не туда, и нажмёт «спам» вместо кнопки.

**Ни одного внешнего файла.** Ни шрифтов, ни скриптов, ни картинок:
страница обязана открыться у кого угодно и через год, а каждая внешняя
ссылка — это ещё один способ не открыться. По той же причине здесь нет
фронта: одностраничное приложение упало бы вместе со сборкой, и донор
увидел бы белый экран вместо отписки, которую мы обещали в письме.
"""

from __future__ import annotations

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


def _shell(title: str, body: str) -> str:
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        # Робот поисковика на этой странице не нужен: у неё один читатель,
        # и он пришёл по ссылке из письма.
        '<meta name="robots" content="noindex, nofollow">'
        f"<title>{escape(title)}</title><style>{_STYLE}</style></head>"
        f"<body><main>{body}{_signature()}</main></body></html>"
    )


def _signature() -> str:
    """Кто прислал письмо — тем же составом, что и юридический блок в нём.

    Страница без отправителя и адреса выглядит как чужая: донор пришёл
    с письма, и здесь он должен увидеть то же имя, что стояло под ним.
    """
    parts = [part for part in (cfg.SENDER_NAME, cfg.POSTAL_ADDRESS) if part]
    if not parts:
        return ""
    return f"<footer>{escape(' · '.join(parts))}</footer>"


def confirm(host: str, *, action: str) -> str:
    """Спросить, прежде чем отписывать.

    Кнопка, а не срабатывание на переходе: по ссылкам в письмах ходят
    почтовые сканеры и предпросмотры, и отписка одним переходом
    сработала бы за донора, который её не нажимал.
    """
    return _shell(
        "Unsubscribe",
        (
            "<h1>Unsubscribe</h1>"
            f'<p>Stop sending emails about <span class="host">{escape(host)}</span>?</p>'
            "<p>This covers every address we have for this site, "
            "including messages already scheduled.</p>"
            f'<form method="post" action="{escape(action)}">'
            '<button type="submit">Unsubscribe</button></form>'
        ),
    )


def done(host: str) -> str:
    """Готово. Тот же текст и на повторное нажатие: для донора разницы нет."""
    return _shell(
        "Unsubscribed",
        (
            "<h1>You're unsubscribed</h1>"
            f'<p>We will not email <span class="host">{escape(host)}</span> again.</p>'
            "<p>Nothing else is needed from you.</p>"
        ),
    )


def gone() -> str:
    """Метки нет, подпись не сходится или донора удалили.

    Все три случая выглядят одинаково намеренно: по разным ответам метку
    подбирают, а донору разница всё равно ни о чём не говорит.
    """
    return _shell(
        "Link expired",
        (
            "<h1>This link no longer works</h1>"
            "<p>Reply to any of our emails with the word "
            "<strong>unsubscribe</strong> and we will take care of it.</p>"
        ),
    )


def too_often() -> str:
    """Слишком часто. Страницу дёргают не люди, а роботы."""
    return _shell(
        "Too many requests",
        "<h1>Too many requests</h1><p>Please try again in a minute.</p>",
    )
