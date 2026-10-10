"""Чем был ответ: человеком, автоответчиком, отказом доставки, отпиской.

От этого зависит, останавливать ли цепочку, и обе ошибки стоят дорого
в разные стороны.

**Автоответчик, принятый за ответ, обрывает цепочку ни на чём.**
«Я в отпуске до понедельника» не значит «мне неинтересно», а добивки
после него не уйдут — и половина цепочек умрёт молча.

**Ответ, принятый за служебное письмо, теряет цену молча.** Цену модель
разбирает только у ответов людей; автоответ и отписка человека не ждут,
отказ доставки хоронит адрес, отписка кладёт его в стоп-лист. Эта
ошибка дороже обратной: служебное письмо, принятое за живое, уходит
модели, цены в нём нет, и ответ ждёт человека — минута разбора.
Поэтому **живой ответ важнее**: признак, который встречается и в живом
ответе, служебным письмо не делает.

Отсюда три правила, которых нет в очевидном «ищем слова»:

* **отказ доставки по тексту — только у писем почты.** «550» в живом
  ответе — цена, «does not exist» — про страницу. Код и формулировку
  сервера смотрим только у письма, которое написала почта: от
  mailer-daemon, отчётом о доставке, с пустым обратным путём. Строгость
  здесь ничего не стоит: настоящий отказ платформа ловит на своём
  обратном адресе и сообщает событием доставки, сюда он почти не доходит;
* **фразы, а не слова** (`phrases`). «Vacation» пишет о себе тревел-блог,
  а «не пишите статьи про казино» — условие публикации;
* **сумма с валютой важнее фразы без служебных заголовков.** «I'm out of
  the office until Monday, but the price is $100» — живой ответ: его
  написал человек, и цену в нём надо разобрать. Автомат, подписавший
  письмо заголовками, фразой не перевешивается: его подпись — факт,
  а не слово.

**Письмо с адреса робота** (noreply, почтовая служба — `robots`) без суммы
и без файла — автоответчик: уведомление службы о настройках или «заявка
принята» не ответ человека, и добивки по нему не обрываются (проверка прода
10.10.2026: уведомление с `…-noreply@` стояло «ответом человека»). Сумма или
файл перевешивают адрес, как фразу: прайс от робота тикет-системы — цена
донора, и её разбирает модель.

Правила лежат списком и разбираются по порядку, а не цепочкой `if`:
список видно целиком, и переставить в нём строку — значит изменить
правило осознанно. Каждое правило называет себя, и название попадает
в отчёт: по нему видно, чем именно мы отличаем одно от другого, и какая
доля писем не подошла ни под одно.

**Смотрим на то, что написал человек, а не на письмо целиком.**
В цитате лежит наше собственное письмо — поиск по всему тексту находил
бы в нём наши же слова и приписывал их донору.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from backend.features.core.domain import ReplyKind
from backend.features.replies import phrases, robots
from backend.features.replies.inbound import Incoming
from backend.features.replies.money import amounts_in
from backend.features.replies.quoting import written_by_hand


@dataclass(frozen=True, slots=True)
class Verdict:
    """Чем был ответ и по какому признаку мы это решили."""

    kind: ReplyKind
    #: Название сработавшего правила. Пусто — не сработало ни одно,
    #: и письмо считается написанным человеком.
    rule: str | None = None

    @property
    def guessed(self) -> bool:
        """Решено по умолчанию, а не по признаку.

        Отдельное число в отчёте: доля «не знаю» у ступени, которая ничего
        не отсеивает, — единственный способ заметить, что она сломалась.
        """
        return self.rule is None


_Check = Callable[[Incoming, str], bool]

#: Имя правила «мёртвый ящик»: по нему приём ищет в автоответе новый
#: адрес донора (`redirect`) — строка в двух местах разошлась бы молча.
DEAD_MAILBOX = "мёртвый ящик"

#: Чем автомат подписывает своё письмо: заголовок и значения, из которых
#: хватит любого. Пустая строка — хватит самого заголовка.
_MACHINE_SIGNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Auto-Submitted", ("auto-generated", "auto-replied", "auto-notified")),
    ("Precedence", ("auto_reply", "bulk", "junk")),
    ("X-Autoreply", ("",)),
    ("X-Autorespond", ("",)),
)

#: Адрес сайта в теме: «vacationguide.com». Последняя часть — буквы,
#: чтобы «5.1.1» и «e.g.» адресом не считались.
_HOST = re.compile(r"\b[\w-]+(?:\.[\w-]+)*\.[^\W\d_]{2,}\b")


def _header_says(incoming: Incoming, name: str, *values: str) -> bool:
    got = incoming.header(name).strip().lower()
    return bool(got) and any(v in got for v in values)


def _has_header(incoming: Incoming, name: str) -> bool:
    return bool(incoming.header(name).strip())


def _content_type(incoming: Incoming) -> str:
    """Тип письма без кавычек и пробелов: `report-type="delivery-status"`
    пишут и так, и подстрока без кавычек его не находила."""
    return re.sub(r"[\s\"']", "", incoming.header("Content-Type")).lower()


def _subject(incoming: Incoming) -> str:
    """Тема без адресов сайтов.

    Тема ответа — наша тема с «Re:» спереди, а в нашей теме стоит адрес
    донора: «Guest article on vacationguide.com». Без этой чистки домен
    тревел-блога делал автоответом каждый ответ с него.
    """
    return _HOST.sub(" ", incoming.subject or "")


def _signed_by_machine(incoming: Incoming, _body: str = "") -> bool:
    """Письмо подписал автомат: заголовки, которых живое письмо не несёт."""
    return any(_header_says(incoming, name, *values) for name, values in _MACHINE_SIGNS)


def _from_mail_system(incoming: Incoming) -> bool:
    """Письмо написала почта, а не человек.

    Пустой обратный путь сюда, а не в отказ по заголовкам: с ним же уходят
    и автоответы — отпускной ответ по Sieve отправляется именно так, — и
    отпуск, принятый за отказ, хоронил бы живой адрес.
    """
    return (
        robots.mail_system(incoming.from_email)
        or _content_type(incoming).startswith("multipart/report")
        or incoming.header("Return-Path").strip() == "<>"
    )


def _bounce_by_headers(incoming: Incoming, _body: str) -> bool:
    """Отчёт о доставке или список не доставленных — это пишет только почта."""
    return _has_header(incoming, "X-Failed-Recipients") or (
        "report-type=delivery-status" in _content_type(incoming)
    )


def _bounce_by_text(incoming: Incoming, body: str) -> bool:
    return _from_mail_system(incoming) and bool(phrases.BOUNCE_TEXT_RE.search(body))


def _robot_sender(incoming: Incoming) -> bool:
    """Отправитель сам говорит, что он робот: обратный адрес noreply или
    просьба Exchange не отвечать ему автоматически — её ставит автоответ,
    чтобы два автоответчика не переписывались до бесконечности."""
    return robots.no_reply(incoming.from_email) or _has_header(incoming, "X-Auto-Response-Suppress")


def _looks_automatic(incoming: Incoming) -> bool:
    """Автоответ по признакам, которых не пишет человек: заголовки
    автомата, отправитель-робот, тема автоответчика."""
    return _signed_by_machine(incoming) or _robot_sender(incoming) or _auto_subject(incoming)


def _unread_here(incoming: Incoming, body: str) -> bool:
    """«Ящик не читается» — но не от робота noreply: он говорит о себе,
    а адрес, которому мы писали, жив — по нему уже завели заявку."""
    return not robots.no_reply(incoming.from_email) and bool(phrases.NOT_READ_RE.search(body))


def _dead_mailbox(incoming: Incoming, body: str) -> bool:
    """Автоответ «ящик больше не читается», «человек ушёл».

    Вид — отказ доставки, потому что нужны его последствия: адрес негоден,
    и открывается следующий адрес донора. Как автоответ такое письмо
    оставляло добивки мёртвому ящику, а другие адреса донора
    не пробовались никогда. Живой ответ сюда не попадает: «больше
    не активна» в нём — про страницу, поэтому смотрим только письма
    автомата.
    """
    return _looks_automatic(incoming) and (
        bool(phrases.NO_LONGER_RE.search(body)) or _unread_here(incoming, body)
    )


def _sent_to_a_list(incoming: Incoming) -> bool:
    """Заголовки рассылки: слово «unsubscribe» в таком письме — её подвал."""
    return _has_header(incoming, "List-Unsubscribe") or _has_header(incoming, "List-Id")


def _without_footer_lines(body: str) -> str:
    return "\n".join(line for line in body.splitlines() if not phrases.FOOTER_LINE_RE.search(line))


def _asks_to_unsubscribe(incoming: Incoming, body: str) -> bool:
    """Просьба больше не писать — фразой, а слово «unsubscribe» — только
    вне подвала рассылки."""
    if phrases.UNSUBSCRIBE_RE.search(body):
        return True
    return not _sent_to_a_list(incoming) and bool(
        phrases.UNSUBSCRIBE_WORD_RE.search(_without_footer_lines(body))
    )


def _auto_subject(incoming: Incoming, _body: str = "") -> bool:
    return bool(phrases.AUTO_SUBJECT_RE.search(_subject(incoming)))


def _absent_in_text(_incoming: Incoming, body: str) -> bool:
    return bool(phrases.ABSENCE_RE.search(body))


def _service_phrase(incoming: Incoming, body: str) -> bool:
    return (
        _asks_to_unsubscribe(incoming, body)
        or _auto_subject(incoming)
        or _absent_in_text(incoming, body)
    )


def _sum_outweighs_phrase(incoming: Incoming, body: str) -> bool:
    """Фраза автоответчика или отписки — или адрес робота — рядом с суммой
    в валюте: живой ответ.

    «Я в отпуске до понедельника, но цена — $100»: цену назвал человек,
    и отдать её модели дешевле, чем потерять. Автоответ с прайсом тоже
    бывает — и цена в нём тоже цена донора. Письмо с подписью автомата
    фразой не перевешивается: заголовки — факт, а не слово.
    """
    return (
        not _signed_by_machine(incoming)
        and (_service_phrase(incoming, body) or robots.robot(incoming.from_email))
        and bool(amounts_in(body))
    )


def _robot_without_file(incoming: Incoming, _body: str) -> bool:
    """Письмо с адреса робота без файла: уведомление службы, а не ответ.

    Сумму в нём проверило правило выше; файл — прайс, который приходит файлом
    чаще, чем текстом, и его разбирает модель (`inbound.has_price_file`).
    """
    return robots.robot(incoming.from_email) and not incoming.has_price_file


#: Правило: имя, вид ответа и проверка. Порядок и есть договорённость.
#:
#: Отказы доставки первыми: уведомление почты несёт и признаки
#: автоответчика — служебные заголовки у них общие.
#:
#: Мёртвый ящик — сразу за отказами и раньше отписки: «ящик больше
#: не читается, уберите его из списка» — про ящик, а не про донора,
#: и следующий адрес донора надо пробовать, а не закрывать.
#:
#: Сумма в тексте — раньше отписки и автоответчика по тексту и теме:
#: против неё стоят только слова. Против заголовков автомата она не
#: стоит — поэтому проверка сама их исключает.
#:
#: Отписка раньше автоответчика: требование «больше не пишите», присланное
#: автоматом, остаётся требованием, и последствие у него сильнее.
#:
#: Адрес робота — последним: всё, что он мог бы перебить, уже решено выше.
RULES: tuple[tuple[str, ReplyKind, _Check], ...] = (
    ("отказ доставки по заголовкам", ReplyKind.BOUNCE, _bounce_by_headers),
    (
        "отказ доставки по теме",
        ReplyKind.BOUNCE,
        lambda inc, _: bool(phrases.BOUNCE_SUBJECT_RE.search(_subject(inc))),
    ),
    ("отказ доставки по тексту письма почты", ReplyKind.BOUNCE, _bounce_by_text),
    (DEAD_MAILBOX, ReplyKind.BOUNCE, _dead_mailbox),
    ("сумма в тексте важнее фразы", ReplyKind.HUMAN, _sum_outweighs_phrase),
    ("отписка в тексте", ReplyKind.UNSUBSCRIBE, _asks_to_unsubscribe),
    ("автоответчик по заголовкам", ReplyKind.AUTO_REPLY, _signed_by_machine),
    ("автоответчик по теме", ReplyKind.AUTO_REPLY, _auto_subject),
    ("автоответчик по тексту", ReplyKind.AUTO_REPLY, _absent_in_text),
    ("письмо робота без суммы и файла", ReplyKind.AUTO_REPLY, _robot_without_file),
)


def classify(incoming: Incoming) -> Verdict:
    """Чем был ответ. Ни одно правило не сработало — значит, человек.

    Правила читают тот же текст, что и модель, — начало письма, а не
    двести тысяч знаков: ответ и автоответ пишут сверху, а словари девяти
    языков на письме предельной длины стоили бы секунды внутри вебхука.
    """
    body = written_by_hand(incoming.for_model)
    for name, kind, matches in RULES:
        if matches(incoming, body):
            return Verdict(kind=kind, rule=name)
    return Verdict(kind=ReplyKind.HUMAN)


#: Виды, которые останавливают цепочку добивок.
#:
#: Автоответчик не останавливает намеренно: «я в отпуске до понедельника»
#: не значит «мне неинтересно», и оборвать на нём цепочку — потерять
#: донора ни на чём. Отказ доставки останавливает, но по другой причине:
#: писать на несуществующий адрес значит жечь репутацию своего домена.
STOPS_THE_CHAIN = frozenset({ReplyKind.HUMAN, ReplyKind.UNSUBSCRIBE, ReplyKind.BOUNCE})


def stops_chain(kind: ReplyKind) -> bool:
    return kind in STOPS_THE_CHAIN
