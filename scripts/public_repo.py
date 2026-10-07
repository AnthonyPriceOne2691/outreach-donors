"""Закрытое не называется в публичном: признаки и откуда брать текст.

Признаки — имена закрытых документов, идентификаторы строк закрытого чеклиста
и одно слово. Текст — файлы, которые уедут в репозиторий, и сообщения коммитов.
Судит и печатает `scripts/gates.py` (правило `public-repo`), здесь только поиск.

Только стандартная библиотека и ни одного соседа: гейт текста запускают
и голым `python3 -I`, без окружения проекта.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

#: Имена закрытых документов — те, что лежат в гитигноре.
PRIVATE_DOCUMENTS = ("TZ", "PHASES", "VPS", "HETZNER_LINKS", "ENTITIES", "REUSE")


def document_names(names: Iterable[str]) -> re.Pattern[str]:
    """Имя документа из списка — с `.md` и без, целым словом, с учётом регистра.

    Только с расширением гейт пропускал голое имя: в docstring оно прошло
    зелёным, а называет документ так же. Регистр различается нарочно: имена
    пишутся заглавными, а те же слова строчными — обычный текст, ключи JSON и
    аргументы функций (замер по дереву main: строчных совпадений 23 строки,
    заглавных вне гитигнора и гейта — ноль). Граница слова латинская
    (`re.ASCII`): латиница и подчёркивание слово продолжают — `HTML_…` это
    идентификатор, а не имя, — а русское окончание вплотную не продолжает.
    """
    alternatives = "|".join(map(re.escape, names))
    return re.compile(rf"\b(?:{alternatives})(?:\.md)?\b", re.ASCII)


#: Чего не должно быть в публичном репозитории. Это не стиль, а утечка:
#: документ требований и план лежат в гитигноре целиком, и ссылка на них
#: из опубликованного файла рассказывает и про их существование, и про их
#: содержимое — номером строки, которую цитирует комментарий.
PRIVATE_MARKERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\u042d[12]-\d+"), "идентификатор строки закрытого чеклиста"),
    (document_names(PRIVATE_DOCUMENTS), "имя закрытого документа"),
    (
        re.compile(r"\u0437\u0430\u043a\u0430\u0437\u0447\u0438\u043a", re.IGNORECASE),
        "слово «заказчик»",
    ),
)

#: Где эти слова законны. Гитигнор и докеригнор обязаны называть файлы
#: по именам — иначе они их не исключат; гейт, его признаки и его тест обязаны
#: содержать образцы, иначе им нечего искать.
PUBLIC_EXEMPT = frozenset(
    {".gitignore", ".dockerignore", "scripts/gates.py", "scripts/public_repo.py",
     "tests/test_gates.py"}
)  # fmt: skip

#: Расширения, которые человек читает. Двоичное содержимое не проверяем:
#: совпадение в нём означало бы не утечку, а случайные байты.
TEXT_SUFFIXES = frozenset(
    {".py", ".md", ".txt", ".yml", ".yaml", ".json", ".sh", ".toml", ".cfg", ".example", ".ts",
     ".tsx", ".css", ".html", ".sql"}
)  # fmt: skip


def tracked_text_files(root: Path) -> Iterator[Path]:
    """Файлы, которые уедут в публичный репозиторий, — по списку git.

    Не обходом дерева: уедет то, что git отслеживает, и спрашивать
    об этом надо его. Нет гита — гейт молчит, а не врёт зелёным.

    **Новые файлы считаются наравне с отслеживаемыми.** Один `ls-files`
    показывает только то, что уже добавлено, — и гейт, запущенный
    в середине работы, отвечал зелёным про файлы, которых ещё нет
    в индексе. Именно так закрытый документ был назван по имени
    в четырёх строках нового статуса, и нашлось это только после
    коммита. Файлы из гитигнора сюда не попадают: `--exclude-standard`
    именно об этом.
    """
    seen: set[str] = set()
    for names in _git_lists(root):
        for name in names:
            if not name or name in PUBLIC_EXEMPT or name in seen:
                continue
            seen.add(name)
            path = root / name
            if path.suffix in TEXT_SUFFIXES and path.is_file():
                yield path


#: Что уедет в репозиторий: добавленное и ещё не добавленное. Второй
#: список без первого не обходится — `--others` показывает только новое.
_GIT_LISTINGS = (
    ("ls-files", "-z"),
    ("ls-files", "-z", "--others", "--exclude-standard"),
)


def _git_lists(root: Path) -> Iterator[list[str]]:
    """Имена файлов от git. Молчит вместо зелёного, если спросить не вышло."""
    git = shutil.which("git")
    if git is None:
        print("public-repo: git не найден — гейт пропущен", file=sys.stderr)
        return
    for arguments in _GIT_LISTINGS:
        try:
            # Аргументы заданы здесь целиком, снаружи не приходит ничего:
            # `root` — путь самого репозитория, вычисленный от этого файла.
            listed = subprocess.run(  # noqa: S603 — фиксированная команда, путь к git разрешён
                [git, "-C", str(root), *arguments],
                capture_output=True,
                check=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"public-repo: список файлов не получен ({exc}) — гейт пропущен", file=sys.stderr)
            return
        yield listed.stdout.decode("utf-8").split("\0")


def private_lines(text: str) -> Iterator[tuple[int, str]]:
    """Строки текста, называющие закрытое: номер строки и что названо."""
    for number, line in enumerate(text.splitlines(), start=1):
        for pattern, what in PRIVATE_MARKERS:
            if pattern.search(line):
                yield number, what
                break


class NotJudgedError(Exception):
    """Вход проверки не прочитан: гейт не судил, и это не «чисто»."""


#: Сообщения коммитов: записи через ноль, в записи — sha строкой и само сообщение.
_GIT_LOG = ("log", "-z", "--format=%H%n%B", "--end-of-options")


def commit_messages(root: Path, base: str) -> list[tuple[str, str]]:
    """(sha, сообщение) коммитов `base..HEAD`.

    Не прочитаны — `NotJudgedError` с причиной, а не пустой список: диапазон задают
    явно (CI, pre-push), и непрочитанный диапазон — «гейт не судил», а не «чисто».
    Тот же класс канон закрыл у гейта покрытия (cqg@2.47): недоступная база —
    красное, а не пропуск.
    """
    unread = f"сообщения коммитов {base}..HEAD не прочитаны"
    git = shutil.which("git")
    if git is None:
        raise NotJudgedError(f"{unread} (git не найден)")
    try:
        # Снаружи приходит только имя ревизии, и стоит оно после `--end-of-options`:
        # ключом git его не прочтёт.
        listed = subprocess.run(  # noqa: S603 — фиксированная команда, путь к git разрешён
            [git, "-C", str(root), *_GIT_LOG, f"{base}..HEAD", "--"],
            capture_output=True,
            check=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise NotJudgedError(f"{unread} ({_why(exc)})") from exc
    records = listed.stdout.decode("utf-8", errors="replace").split("\0")
    return [(sha, message) for sha, _, message in (r.partition("\n") for r in records) if sha]


def _why(exc: Exception) -> str:
    """Причина одной строкой: последняя строка stderr git, иначе само исключение."""
    stderr = getattr(exc, "stderr", None) or b""
    lines = stderr.decode("utf-8", errors="replace").strip().splitlines()
    return lines[-1] if lines else str(exc)
