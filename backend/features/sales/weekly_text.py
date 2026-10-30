"""Недельный отчёт словами: разделы экрана, сообщение в группу продаж и выгрузка CSV (срез 6.3).

Отчёт один (`weekly.build`) — видов три, и слова у них общие: разделы (`sections`) показывает
экран, из тех же строк собирается сообщение в группу (`message`), выгрузка (`to_csv`) — те же
числа строками таблицы. Разделов восемь, в порядке формата подрядчика, и пустой не пропадает:
каждое число названо, хоть и нулём («ответов за неделю — 0»).

**Сообщение — в пределах Telegram** (`telegram.MAX_CHARS`): длинные списки — лиды поимённо и
проблемы — первыми строками и «ещё N — на экране отчёта» со ссылкой на него.

**Выгрузка — для Excel**, как выгрузки лидов и доноров: точка с запятой, UTF-8 с меткой.
Ячейка, похожая на формулу (имя лида из чужого файла), начинается апострофом — общим правилом
выгрузок (`shared/spreadsheet.py`).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from dataclasses import dataclass

from backend.config import sales as cfg
from backend.features.sales.telegram import MAX_CHARS
from backend.features.sales.weekly import Numbers, Plan, Problem, Week, WeeklyReport
from backend.features.sales.weekly_counts import AUTO, KINDS, Handed, Mail
from backend.shared.spreadsheet import inert

#: Вид ответа — словами строки отчёта.
KIND_WORDS: dict[str, str] = {
    "wants_to_talk": "хочет говорить",
    "question": "вопрос",
    "interested": "интерес",
    "referral": "назвал другого",
    "not_now": "не сейчас",
    "not_interested": "не интересно",
    "unsubscribe": "просит не писать",
    "unparsed": "вид не разобран",
}
WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
#: Сколько строк длинного списка уходит в группу; остальное — «ещё N — на экране отчёта».
SHOWN = 10


@dataclass(frozen=True, slots=True)
class Section:
    """Раздел отчёта: первая строка — итог, следующие — подробности списком."""

    key: str
    title: str
    lines: list[str]


def _pairs(*pairs: tuple[str, int]) -> str:
    return ", ".join(f"{words} — {n}" for words, n in pairs)


def _list(items: list[str], shown: int | None) -> list[str]:
    """Строки списка; `shown` — сколько показать, остальное — одной строкой «ещё N»."""
    if shown is None or len(items) <= shown:
        return items
    return [*items[:shown], f"ещё {len(items) - shown} — на экране отчёта"]


def sections(report: WeeklyReport, *, shown: int | None = None) -> list[Section]:
    """Восемь разделов в порядке формата подрядчика. `shown` — потолок длинных списков
    (сообщение в группу); `None` — целиком (экран)."""
    total = report.total
    return [
        Section("contacts", "Контакты", [_contacts(total)]),
        Section("sends", "Отправки", [_sends(total)]),
        Section("delivery", "Доставляемость", _delivery(total, report.mail)),
        Section("replies", "Ответы", _replies(total)),
        Section("dialogs", "Диалоги", [_dialogs(total)]),
        Section("leads", "Лиды поимённо", _leads(report.leads, shown)),
        Section("problems", "Проблемы", _problems(report.problems, shown)),
        Section("plan", "План", _plan(total, report.plan)),
    ]


def _contacts(n: Numbers) -> str:
    rest = _pairs(("готово к письмам", n.ready), ("отсеяно очисткой", n.rejected))
    return f"загружено за неделю — {n.loaded}: {rest}, {_pairs(('ждут очистки', n.cleaning))}"


def _steps(steps: tuple[int, ...]) -> str:
    return _pairs(*((f"{step}-е", count) for step, count in enumerate(steps, start=1)))


def _sends(n: Numbers) -> str:
    return (
        f"первое письмо получили лидов — {n.funnel.sent}; писем по шагам цепочки: {_steps(n.steps)}"
    )


def _delivery(n: Numbers, mail: Mail) -> list[str]:
    """Доставляемость: воронка недели, жалобы, паузы и ящики. Какой ящик и почему встал на
    паузу — в проблемах: это тревога недели."""
    weekly = _pairs(("доставлено", n.funnel.delivered), ("отказ", n.funnel.bounced))
    boxes = _pairs(("жалоб на спам", mail.complaints), ("пауз ящиков", len(mail.pauses)))
    idle = f"сейчас не пишут ящиков — {mail.idle} из {mail.boxes}"
    return [f"лиды первого письма недели: {weekly}", f"{boxes}; {idle}"]


def _replies(n: Numbers) -> list[str]:
    kinds = _pairs(*((KIND_WORDS[kind], n.replies[kind]) for kind in KINDS))
    funnel = _pairs(("ответили", n.funnel.answered), ("переданы", n.funnel.handed_off))
    return [
        f"ответов за неделю — {n.answers}: {kinds}",
        f"автоответов — {n.replies[AUTO]} (ответом не считаются); "
        f"из лидов первого письма недели {funnel}",
    ]


def _dialogs(n: Numbers) -> str:
    talks = n.dialogs
    return (
        f"в диалоге лидов — {talks.active + talks.silent}: активных — {talks.active} (было "
        f"движение с начала недели), молчащих — {talks.silent}; ждут человека — {talks.waiting}"
    )


def lead_line(lead: Handed) -> str:
    """Лид поимённо: компания и сайт, человек, адрес — и ссылка, которую получил телемаркетолог."""
    company = f"{lead.company} ({lead.site})" if lead.company else lead.site
    person = " · ".join(part for part in (lead.name, lead.email) if part)
    return f"{company} · {person} — {lead.link}"


def _leads(leads: list[Handed], shown: int | None) -> list[str]:
    head = f"передано телемаркетологу за неделю — {len(leads)}"
    return [head, *_list([lead_line(lead) for lead in leads], shown)]


def problem_title(problem: Problem) -> str:
    """Что за проблема; состояние на момент отчёта, а не событие недели, — с пометкой."""
    return f"{problem.title} (сейчас)" if problem.now else problem.title


def problem_words(problem: Problem) -> str:
    """Подробности проблемы и, есть ли повод для беспокойства, — теми же словами, что у тревог."""
    concern = problem.concern
    return problem.detail if concern is None else f"{problem.detail.rstrip('.')}. {concern.words}"


def problem_line(problem: Problem) -> str:
    return f"{problem_title(problem)}: {problem_words(problem)}"


def _problems(problems: list[Problem], shown: int | None) -> list[str]:
    if not problems:
        return ["проблем — 0: тревог и сбоев нет"]
    lines = [problem_line(problem) for problem in problems]
    return [f"проблем — {len(problems)}", *_list(lines, shown)]


#: Лимит направления продаж не прочитан из настроек (`weekly_counts.Runway.unread`) — словами, без
#: имени настройки: отчёт читает группа продаж, а настройку правит администратор.
RUNWAY_UNREAD = (
    "лимит первых писем продаж не прочитан из настроек — первых писем ноль, пока его не "
    "поправит администратор"
)


def runway_line(plan: Plan) -> str:
    """Разгон на неделю плана: ящики и потолок первых писем по дням отправки."""
    head, runway = f"разгон на {plan.week.words()}", plan.runway
    if runway.unread is not None:
        return f"{head}: {RUNWAY_UNREAD}"
    if not runway.boxes:
        return f"{head}: включённых ящиков продаж — 0, писать нечем"
    days = _pairs(*((WEEKDAYS[day.day.weekday()], day.letters) for day in runway.days))
    letters = sum(day.letters for day in runway.days)
    counted = _pairs(("ящиков продаж", runway.boxes), ("первых писем до", letters))
    return f"{head}: {counted}" + (f" ({days})" if days else "")


def _plan(n: Numbers, plan: Plan) -> list[str]:
    queued = _pairs(
        ("первых писем ждут отправки", n.queued), ("готовых лидов без письма", n.unwritten)
    )
    return [f"очередь: {queued}", runway_line(plan)]


def link(week: Week) -> str:
    """Где отчёт недели целиком: экран продаж; без адреса сервиса — словами и имя настройки."""
    if cfg.APP_URL:
        return f"{cfg.APP_URL}/sales?tab=report&week={week.start.isoformat()}"
    return "Продажи → Отчёт (SALES_APP_URL не задан)"


def _by_hypothesis(report: WeeklyReport) -> list[str]:
    head = "По гипотезам — загружено · первых писем · ответов · передано:"
    lines = [
        f"— {row.name}: {row.numbers.loaded} · {row.numbers.funnel.sent} · "
        f"{row.numbers.answers} · {row.numbers.handed}"
        for row in report.rows
    ]
    return [head, *lines] if lines else [f"{head} гипотез нет"]


def _compose(report: WeeklyReport, shown: int) -> str:
    lines = [f"Продажи — недельный отчёт за {report.week.words()} (пн–вс, Москва)", ""]
    for number, section in enumerate(sections(report, shown=shown), start=1):
        first, *rest = section.lines
        lines += [f"{number}. {section.title}: {first}", *(f"— {line}" for line in rest)]
    lines += ["", *_by_hypothesis(report), "", f"Отчёт на экране и CSV: {link(report.week)}"]
    return "\n".join(lines)


def message(report: WeeklyReport) -> str:
    """Сообщение в группу продаж — не длиннее потолка Telegram: длинные списки сокращаются
    до строк, которые влезают; не влезло и так — обрезано, ссылка на экран остаётся."""
    text = ""
    for shown in range(SHOWN, -1, -1):
        text = _compose(report, shown)
        if len(text) <= MAX_CHARS:
            return text
    tail = f"\n…\nОтчёт на экране и CSV: {link(report.week)}"
    return text[: MAX_CHARS - len(tail)] + tail


# --- выгрузка CSV ----------------------------------------------------------------------------

#: Заголовки выгрузки.
CSV_HEAD = ("раздел", "гипотеза", "показатель", "значение")

Line = tuple[str, str, str, object]


def number_lines(name: str, n: Numbers) -> Iterator[Line]:
    """Числа гипотезы (или итога) строками выгрузки — те же, что в разделах."""
    yield from (
        ("контакты", name, "загружено за неделю", n.loaded),
        ("контакты", name, "готово к письмам", n.ready),
        ("контакты", name, "отсеяно очисткой", n.rejected),
        ("контакты", name, "ждут очистки", n.cleaning),
        ("отправки", name, "первое письмо получили лидов", n.funnel.sent),
        ("отправки", name, "в очереди лидов (воронка недели)", n.funnel.queued),
    )
    yield from (
        ("отправки", name, f"писем, {step}-й шаг", count)
        for step, count in enumerate(n.steps, start=1)
    )
    yield from (
        ("доставляемость", name, "доставлено (лиды недели)", n.funnel.delivered),
        ("доставляемость", name, "отказ (лиды недели)", n.funnel.bounced),
        ("ответы", name, "ответов за неделю", n.answers),
    )
    yield from (("ответы", name, KIND_WORDS[kind], n.replies[kind]) for kind in KINDS)
    yield from (
        ("ответы", name, "автоответов", n.replies[AUTO]),
        ("ответы", name, "ответили (лиды недели)", n.funnel.answered),
        ("ответы", name, "переданы (лиды недели)", n.funnel.handed_off),
        ("диалоги", name, "активных", n.dialogs.active),
        ("диалоги", name, "молчащих", n.dialogs.silent),
        ("диалоги", name, "ждут человека", n.dialogs.waiting),
        ("лиды поимённо", name, "передано за неделю", n.handed),
        ("план", name, "первых писем ждут отправки", n.queued),
        ("план", name, "готовых лидов без письма", n.unwritten),
    )


def _lines(report: WeeklyReport) -> Iterator[Line]:
    for row in report.rows:
        yield from number_lines(row.name, row.numbers)
    yield from number_lines("Всего", report.total)
    mail = report.mail
    yield from (
        ("доставляемость", "Всего", "жалоб на спам", mail.complaints),
        ("доставляемость", "Всего", "пауз ящиков", len(mail.pauses)),
        ("доставляемость", "Всего", "ящиков продаж", mail.boxes),
        ("доставляемость", "Всего", "сейчас не пишут ящиков", mail.idle),
    )
    yield from (
        ("лиды поимённо", lead.hypothesis, lead_line(lead), lead.link) for lead in report.leads
    )
    yield from (
        ("проблемы", "", problem_title(problem), problem_words(problem))
        for problem in report.problems
    )
    runway = report.plan.runway
    if runway.unread is not None:
        yield ("план", "Всего", RUNWAY_UNREAD, 0)
    yield ("план", "Всего", "включённых ящиков продаж", runway.boxes)
    yield from (
        (
            "план",
            "Всего",
            f"первых писем до, {WEEKDAYS[day.day.weekday()]} {day.day:%d.%m}",
            day.letters,
        )
        for day in runway.days
    )


def to_csv(report: WeeklyReport) -> bytes:
    """Выгрузка отчёта — для Excel: заголовки словами, точка с запятой, UTF-8 с меткой."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    writer.writerow(CSV_HEAD)
    for line in _lines(report):
        writer.writerow([inert(value) for value in line])
    return out.getvalue().encode("utf-8-sig")
