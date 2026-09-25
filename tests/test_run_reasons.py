"""Причина прогона словами человека — без имени класса исключения.

Замечание 25.09.2026: в колонке состояния экрана прогонов печаталось
«остановлен разбором: задача упала: backend.features.runs.budget.
CapExceededError: Прогон обойдётся в 3480 юнитов…» — в три-пять строк.
Имя класса нужно журналу; человеку — что случилось и что делать.

Проверяется на разных исключениях, а не на одном потолке: правило общее
для любого сбоя, а старые записи в базе остаются как были и обязаны
читаться так же, как новые.
"""

from __future__ import annotations

import traceback

import httpx
import pytest
from backend.config.startup_checks import ConfigError
from backend.features.ahrefs.client import AhrefsError
from backend.features.runs.budget import CapExceededError
from backend.features.runs.reasons import UNKNOWN, explained, explained_line, readable
from backend.features.serp.dataforseo import SerpError
from backend.shared.queue import last_error_line

CAP = (
    "Прогон обойдётся в 3480 юнитов, доступно 3000. Новых доменов 61 из 62; "
    "сократите список ключей или поднимите кап."
)

#: Сообщения, написанные человеку: наши отказы и наш же текст во встроенном
#: исключении. Показываются как есть.
FOR_A_PERSON: list[BaseException] = [
    CapExceededError(CAP),
    ConfigError("Не задано для «сбор»:\n  SERP_LOGIN — логин провайдера выдачи"),
    SerpError("Провайдер выдачи ответил 402: недостаточно средств", permanent=True),
    RuntimeError("выдача: повторы кончились без ответа"),
]

#: Чужие сообщения — для разработчика, по-английски, — и наш класс с сырым
#: ответом провайдера. Вместо них — общие слова и имя класса.
FOR_A_DEVELOPER: list[tuple[BaseException, str]] = [
    (httpx.ReadTimeout("timed out"), "ReadTimeout"),
    (KeyError("hosts"), "KeyError"),
    (TimeoutError(), "TimeoutError"),
    (AhrefsError("batch_metrics: 403 forbidden", permanent=True), "AhrefsError"),
]


def _trace_line(error: BaseException) -> str:
    """Последняя строка трассировки — ровно то, что разбор мёртвых берёт
    из очереди (`job_failure`)."""
    line = last_error_line("".join(traceback.format_exception_only(error)))
    assert line is not None
    return line


class TestWhatIsWrittenNow:
    @pytest.mark.parametrize("error", FOR_A_PERSON, ids=lambda error: type(error).__name__)
    def test_message_for_a_person_is_kept_without_the_class(self, error: BaseException) -> None:
        said = explained(error)

        assert said == str(error).strip()
        assert type(error).__name__ not in said

    @pytest.mark.parametrize(
        ("error", "name"), FOR_A_DEVELOPER, ids=[name for _, name in FOR_A_DEVELOPER]
    )
    def test_foreign_message_becomes_general_words_and_the_code(
        self, error: BaseException, name: str
    ) -> None:
        """Как у любого незнакомого кода на экране: общие слова и код
        в скобках — по нему сбой находят в журнале."""
        assert explained(error) == f"{UNKNOWN} ({name})"

    @pytest.mark.parametrize(
        "error",
        # Без многострочного отказа настроек: последняя строка его трассировки —
        # хвост сообщения, а не класс, и сравнивать там нечего.
        [error for error in FOR_A_PERSON if "\n" not in str(error)]
        + [error for error, _ in FOR_A_DEVELOPER],
        ids=lambda error: type(error).__name__,
    )
    def test_trace_line_is_read_like_the_exception_itself(self, error: BaseException) -> None:
        """Разбор мёртвых видит не исключение, а строку трассировки с полным
        путём класса. Сказать он обязан то же самое."""
        line = _trace_line(error)
        assert type(error).__name__ in line, "строка трассировки называет класс"

        assert explained_line(line) == explained(error)

    def test_line_that_is_not_an_exception_is_general_words(self) -> None:
        """Так очередь описывает убитый процесс: класса нет, гадать о нём незачем."""
        assert explained_line("Work-horse terminated unexpectedly; waitpid returned 9") == UNKNOWN


class TestWhatWasWrittenBefore:
    """Причины до 25.09.2026 лежат в базе с именами классов. Запись не
    переписывается — показ обязан быть чистым."""

    @pytest.mark.parametrize(
        ("stored", "shown"),
        [
            # Прогон №22 на рабочем сервере: разбор мёртвых и потолок.
            (
                "остановлен разбором: задача упала: backend.features.runs.budget."
                f"CapExceededError: {CAP}, продолжений 2 из 2, молчание 901 с",
                f"остановлен разбором: задача упала: {CAP}, продолжений 2 из 2, молчание 901 с",
            ),
            # Задача прогона, отказ по потолку сразу.
            (f"остановлен: CapExceededError: {CAP}", f"остановлен: {CAP}"),
            # Прогоны №19 и №20: чужое исключение, продолжение и итог после него.
            (
                "продолжен после сбоя (2 раз): задача упала: sqlalchemy.exc.MissingGreenlet: "
                "greenlet_spawn has not been called; can't call await_only() here. "
                "Was IO attempted in an unexpected place?; "
                "прогон продолжен по сохранённой выдаче",
                f"продолжен после сбоя (2 раз): задача упала: {UNKNOWN} (MissingGreenlet); "
                "прогон продолжен по сохранённой выдаче",
            ),
            (
                "сбой, будет продолжен: RuntimeError: провайдер лёг",
                "сбой, будет продолжен: провайдер лёг",
            ),
            (
                "остановлен разбором: задача упала: asyncio.exceptions.CancelledError, "
                "продолжений 2 из 2, молчание 950 с",
                f"остановлен разбором: задача упала: {UNKNOWN} (CancelledError), "
                "продолжений 2 из 2, молчание 950 с",
            ),
        ],
    )
    def test_class_name_is_gone_and_the_rest_is_intact(self, stored: str, shown: str) -> None:
        assert readable(stored) == shown

    @pytest.mark.parametrize(
        "error",
        [*FOR_A_PERSON, *(error for error, _ in FOR_A_DEVELOPER)],
        ids=lambda error: type(error).__name__,
    )
    def test_old_record_reads_like_a_new_one(self, error: BaseException) -> None:
        """Любое исключение, записанное по-старому, показывается так же,
        как его записали бы сейчас, — во всех трёх старых видах."""
        old_job = f"остановлен: {type(error).__name__}: {error}"
        old_retry = f"сбой, будет продолжен: {type(error).__name__}: {error}"
        old_reaper = f"задача упала: {_trace_line(error)}; прогон продолжен по сохранённой выдаче"

        assert readable(old_job) == f"остановлен: {explained(error)}"
        assert readable(old_retry) == f"сбой, будет продолжен: {explained(error)}"
        assert readable(old_reaper) == (
            f"задача упала: {explained_line(_trace_line(error))}; "
            "прогон продолжен по сохранённой выдаче"
        )

    @pytest.mark.parametrize(
        "text",
        [
            "остановлен разбором: воркер умер, продолжений 2 из 2, молчание 901 с",
            f"остановлен: {CAP}",
            f"сбой, будет продолжен: {UNKNOWN} (ReadTimeout)",
            # Латиница в начале фразы — ещё не класс: ни двоеточия, ни окончания.
            "остановлен: Ahrefs, ответил 403",
            # Строчное слово с двоеточием — текст сообщения, а не класс.
            "сбой, будет продолжен: batch_metrics: не удалось за 4 попыток",
            "сбой, будет продолжен: модель, отказ (чинить): HTTP 401: ключ не принят",
        ],
    )
    def test_text_without_class_names_passes_untouched(self, text: str) -> None:
        assert readable(text) == text

    def test_reading_twice_changes_nothing(self) -> None:
        once = readable("остановлен разбором: задача упала: KeyError: 'hosts', продолжений 1 из 2")

        assert once is not None
        assert readable(once) == once

    @pytest.mark.parametrize("empty", [None, "", "   ", 42, {"текст": "не строка"}])
    def test_no_reason_is_none(self, empty: object) -> None:
        assert readable(empty) is None
