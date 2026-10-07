"""Тесты на сами гейты.

Гейт, который никогда не краснеет, неотличим от выключенного. Поэтому
каждое правило проверяется заведомо плохим кодом: если гейт его пропустит,
он не работает, и узнать об этом лучше здесь, чем через полгода на ревью.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from scripts import gates
from scripts.gates import (
    MAX_LINES_PROD,
    check_commit_messages,
    check_config_access,
    check_env_example,
    check_file_length,
    check_grab_bag,
    check_layers,
    check_public_repo,
    check_silent_except,
    public_repo,
    run,
)

#: Признаки — из модуля, которым пользуется гейт: тот же объект, что у `gates`.
PRIVATE_DOCUMENTS = public_repo.PRIVATE_DOCUMENTS
Capture = pytest.CaptureFixture[str]


def gate(
    monkeypatch: pytest.MonkeyPatch, capsys: Capture, root: Path, *argv: str
) -> tuple[int, str, str]:
    """Гейт целиком, как его зовут CI и pre-push, над деревом `root`."""
    monkeypatch.setattr(gates, "ROOT", root)
    code = gates.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def _rules(check, path: str, source: str) -> list[str]:
    return [v.rule for v in check(Path(path), source)]


class TestSilentExcept:
    def test_swallowed_exception_is_caught(self) -> None:
        bad = "try:\n    x()\nexcept ValueError:\n    pass\n"
        assert _rules(check_silent_except, "backend/a.py", bad) == ["silent-except"]

    def test_logging_is_enough(self) -> None:
        ok = "try:\n    x()\nexcept ValueError:\n    logger.debug('не вышло')\n"
        assert _rules(check_silent_except, "backend/a.py", ok) == []

    def test_reraise_is_enough(self) -> None:
        ok = "try:\n    x()\nexcept ValueError:\n    raise RuntimeError('ой')\n"
        assert _rules(check_silent_except, "backend/a.py", ok) == []

    def test_silent_return_is_still_silent(self) -> None:
        """Ранний выход без сообщения — то же самое проглатывание."""
        bad = (
            "def f():\n    try:\n        return x()\n    except ValueError:\n        return None\n"
        )
        assert _rules(check_silent_except, "backend/a.py", bad) == ["silent-except"]


class TestConfigAccess:
    def test_env_read_outside_config_is_caught(self) -> None:
        bad = "import os\nKEY = os.getenv('SECRET')\n"
        assert _rules(check_config_access, "backend/features/a.py", bad) == ["config-access"]

    def test_config_package_may_read_env(self) -> None:
        bad = "import os\nKEY = os.getenv('SECRET')\n"
        assert _rules(check_config_access, "backend/config/a.py", bad) == []

    def test_tests_may_choose_their_database(self) -> None:
        """Выбор тестовой базы через окружение — про запуск, а не про
        конфигурацию сервиса."""
        bad = "import os\nDSN = os.getenv('TEST_DSN')\n"
        assert _rules(check_config_access, "tests/conftest.py", bad) == []

    def test_pr_text_source_reads_its_input_from_env(self) -> None:
        """Текст PR приходит в шаг CI только окружением: для его источника это
        вход, а не конфигурация. Любой другой скрипт по-прежнему краснеет."""
        read = "import os\nTITLE = os.environ.get('PR_TITLE', '')\n"
        assert _rules(check_config_access, "scripts/public_repo.py", read) == []
        assert _rules(check_config_access, "scripts/other.py", read) == ["config-access"]


class TestLayers:
    def test_web_framework_in_domain_module_is_caught(self) -> None:
        """Ядро не знает про веб — иначе его нельзя протестировать без сервера."""
        bad = "from fastapi import Depends\n"
        assert _rules(check_layers, "backend/features/donors/x.py", bad) == ["layers"]

    def test_api_layer_may_import_web(self) -> None:
        bad = "from fastapi import Depends\n"
        assert _rules(check_layers, "backend/features/donors/api/routes.py", bad) == []


class TestFileLength:
    def test_long_file_is_caught(self) -> None:
        long_source = "x = 1\n" * (MAX_LINES_PROD + 1)
        assert _rules(check_file_length, "backend/a.py", long_source) == ["file-length"]

    def test_tests_get_a_higher_limit(self) -> None:
        """Тесты длиннее по природе: параметризация и данные."""
        source = "x = 1\n" * (MAX_LINES_PROD + 1)
        assert _rules(check_file_length, "tests/test_a.py", source) == []


class TestGrabBag:
    @pytest.mark.parametrize("name", ["utils.py", "helpers.py", "common.py", "misc.py"])
    def test_module_without_a_topic_is_caught(self, name: str) -> None:
        assert _rules(check_grab_bag, f"backend/{name}", "") == ["grab-bag"]

    def test_topical_helper_is_fine(self) -> None:
        assert _rules(check_grab_bag, "backend/_price_helpers.py", "") == []


def test_gate_actually_walks_the_tree(tmp_path: Path) -> None:
    """Зелёный результат при нуле просмотренных файлов означает «гейт не
    настроен», а не «нарушений нет». Проверяем, что файлы действительно
    читаются."""
    bad = tmp_path / "backend"
    bad.mkdir()
    (bad / "broken.py").write_text("try:\n    x()\nexcept Exception:\n    pass\n", encoding="utf-8")

    violations = run([bad])
    assert [v.rule for v in violations] == ["silent-except"]


class TestSecretInExample:
    """Ключ в образце окружения. Правка руками эту ошибку не ловит —
    за вечер она случилась дважды, поэтому её ловит гейт."""

    def test_filled_secret_is_caught(self, tmp_path: Path) -> None:
        example = tmp_path / ".env.example"
        example.write_text("AHREFS_API_KEY=abc123\n", encoding="utf-8")
        assert [v.rule for v in check_env_example(example)] == ["secret-in-example"]

    def test_empty_placeholder_is_fine(self, tmp_path: Path) -> None:
        example = tmp_path / ".env.example"
        example.write_text("AHREFS_API_KEY=\n# комментарий\nLLM_MODEL=gpt\n", encoding="utf-8")
        assert list(check_env_example(example)) == []

    def test_connection_strings_stay(self, tmp_path: Path) -> None:
        """DSN с логином разработки — это образец, а не секрет: без него
        никто не поймёт, какой порт у базы."""
        example = tmp_path / ".env.example"
        example.write_text(
            "STORAGE_DSN=postgresql+asyncpg://outreach:outreach@localhost:5442/outreach\n",
            encoding="utf-8",
        )
        assert list(check_env_example(example)) == []

    def test_missing_file_is_not_a_failure(self, tmp_path: Path) -> None:
        assert list(check_env_example(tmp_path / "нет-такого")) == []


class TestPublicRepo:
    """Закрытое не называется в публичном репозитории.

    Правило было записано словами и продержалось ровно до первого среза,
    который его не помнил: четырнадцать файлов уехали в `main` со ссылками
    на закрытый документ и с идентификаторами его строк. Правило, которое
    обязан помнить агент, не исполняется — исполняется то, что роняет пуш.
    """

    @staticmethod
    def _repo(tmp_path: Path, name: str, text: str) -> Path:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        return tmp_path

    def test_checklist_id_is_caught(self, tmp_path: Path) -> None:
        repo = self._repo(tmp_path, "docs/note.md", "Требование Э1-24 просит ручную очередь.\n")
        assert [v.rule for v in check_public_repo(repo)] == ["public-repo"]

    def test_private_document_name_is_caught(self, tmp_path: Path) -> None:
        repo = self._repo(tmp_path, "backend/a.py", "# смета описана в TZ.md\n")
        assert [v.rule for v in check_public_repo(repo)] == ["public-repo"]

    @pytest.mark.parametrize(
        "name", PRIVATE_DOCUMENTS, ids=[f"doc{i}" for i in range(len(PRIVATE_DOCUMENTS))]
    )
    def test_bare_document_name_is_caught(self, tmp_path: Path, name: str) -> None:
        """Имя без расширения называет документ так же, как с ним: голым,
        в docstring, оно проходило зелёным."""
        text = f'"""Решение о рассылке ({name}): отписавшимся не писать."""\n'
        repo = self._repo(tmp_path, "backend/a.py", text)
        assert [v.rule for v in check_public_repo(repo)] == ["public-repo"]

    def test_ordinary_words_are_not_document_names(self, tmp_path: Path) -> None:
        """Регистр различается: имя пишется заглавными, а то же слово строчными
        или с заглавной буквы — обычный текст (ключ JSON, аргумент функции).
        Часть идентификатора — тоже не имя."""
        forms = (str.lower, str.capitalize, "HTML_{}".format, "{}_PATH".format, "{}S".format)
        text = "".join(f"# {form(name)}\n" for name in PRIVATE_DOCUMENTS for form in forms)
        repo = self._repo(tmp_path, "backend/a.py", text)
        assert list(check_public_repo(repo)) == []

    @pytest.mark.parametrize(
        ("text", "caught"),
        [
            pytest.param("см. FAKEDOC, раздел о рассылке", True, id="comma"),
            pytest.param("смета описана в FAKEDOC.md", True, id="extension"),
            pytest.param("(FAKEDOC): правило", True, id="parentheses"),
            pytest.param("FAKEDOC-документ", True, id="hyphen"),
            pytest.param("по FAKEDOCу", True, id="russian-ending"),
            pytest.param("fakedoc", False, id="lowercase"),
            pytest.param("Fakedoc", False, id="capitalized"),
            pytest.param("HTML_FAKEDOC", False, id="identifier-tail"),
            pytest.param("FAKEDOC_PATH", False, id="identifier-head"),
            pytest.param("FAKEDOCS", False, id="longer-word"),
        ],
    )
    def test_document_name_is_a_whole_uppercase_word(self, text: str, caught: bool) -> None:
        """Образец из выдуманного имени: с расширением и без, целым словом,
        заглавными. Русское окончание вплотную слова не продолжает."""
        assert (public_repo.document_names(["FAKEDOC"]).search(text) is not None) is caught

    def test_impersonal_wording_passes(self, tmp_path: Path) -> None:
        repo = self._repo(
            tmp_path, "docs/note.md", "Требование просит ручную очередь ниже порога.\n"
        )
        assert list(check_public_repo(repo)) == []

    def test_an_ignored_file_is_not_checked(self, tmp_path: Path) -> None:
        """Проверяется то, что уедет, а не лежащее рядом: сам закрытый
        документ в гитигноре, и краснеть на нём гейт не должен."""
        repo = self._repo(tmp_path, ".gitignore", "TZ.md\n")
        (repo / "TZ.md").write_text("Э1-24: строка требования\n", encoding="utf-8")
        assert list(check_public_repo(repo)) == []

    def test_a_new_file_is_checked_before_it_is_added(self, tmp_path: Path) -> None:
        """Файл, который ещё не в индексе, уедет вместе со всеми —
        и гейт обязан его видеть.

        Один `ls-files` его не показывал, и гейт, запущенный в середине
        работы, отвечал зелёным. Так закрытый документ был назван
        по имени в четырёх строках нового статуса, а нашлось это
        только после коммита.
        """
        repo = self._repo(tmp_path, "docs/note.md", "чисто\n")
        (repo / "docs" / "fresh.md").write_text("смета описана в TZ.md\n", encoding="utf-8")
        assert [v.rule for v in check_public_repo(repo)] == ["public-repo"]


class TestCommitMessages:
    """Сообщение коммита уходит в публичную историю так же, как файл, а гейт
    по файлам его не видит."""

    @staticmethod
    def _history(tmp_path: Path, base: str, *messages: str) -> str:
        """Коммит-база и поверх него коммиты PR с этими сообщениями. Возвращает sha базы."""

        def git(*args: str) -> str:
            command = ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args]
            done = subprocess.run(command, cwd=tmp_path, check=True, capture_output=True, text=True)
            return done.stdout

        git("init", "-q")
        git("commit", "-q", "--allow-empty", "-m", base)
        sha = git("rev-parse", "HEAD").strip()
        for message in messages:
            git("commit", "-q", "--allow-empty", "-m", message)
        return sha

    def test_private_name_in_a_pr_commit_is_caught(self, tmp_path: Path) -> None:
        leak = f"Отписка: правила рассылки\n\nРешение — в {PRIVATE_DOCUMENTS[-1]}, раздел о ней"
        base = self._history(tmp_path, "база", "Письма: очередь пачкой", leak)
        found = [(v.rule, v.line) for v in check_commit_messages(tmp_path, base)]
        assert found == [("public-repo", 3)]

    def test_history_under_the_base_is_not_judged(self, tmp_path: Path) -> None:
        """Сообщение базы уже в main: переписать его нельзя, и PR на нём не краснеет."""
        base = self._history(tmp_path, f"Старое: {PRIVATE_DOCUMENTS[-1]}.md", "Письма: очередь")
        assert list(check_commit_messages(tmp_path, base)) == []

    def test_unreadable_range_is_a_refusal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Capture
    ) -> None:
        """Диапазон задан явно, а сообщения не прочитаны — гейт не судил, и это
        красное: непрочитанное не должно выглядеть чистым."""
        self._history(tmp_path, "база", "Письма: очередь")
        code, _, err = gate(monkeypatch, capsys, tmp_path, "--commits", "нет-такой-ветки")
        assert code != 0
        assert "гейт не судил: сообщения коммитов нет-такой-ветки..HEAD не прочитаны" in err

    def test_readable_clean_range_is_green(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Capture
    ) -> None:
        base = self._history(tmp_path, "база", "Письма: очередь", "Чистка: прогоны")
        code, out, _ = gate(monkeypatch, capsys, tmp_path, "--commits", base)
        assert code == 0
        assert f"сообщений коммитов {base}..HEAD — 2" in out


class TestPrText:
    """Заголовок и тело PR: из них GitHub собирает squash-коммит, который уйдёт
    в main, а гейт по коммитам их не видит. Текст приходит окружением шага."""

    @staticmethod
    def _env(monkeypatch: pytest.MonkeyPatch, title: str | None, body: str | None) -> None:
        for name, value in (("PR_TITLE", title), ("PR_BODY", body)):
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)

    def test_name_in_title_is_red(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Capture
    ) -> None:
        self._env(monkeypatch, f"Отписка по {PRIVATE_DOCUMENTS[0]}", "Что сделано.")
        code, _, err = gate(monkeypatch, capsys, tmp_path, "--pr-env")
        assert code == 1
        assert "заголовок PR:1 [public-repo] имя закрытого документа в заголовке PR" in err

    @pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
    def test_name_in_body_is_red_with_its_line(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Capture, newline: str
    ) -> None:
        """Тело из веб-формы GitHub приходит с переводом строки `\\r\\n` — номер тот же."""
        body = newline.join(["Что сделано", "", f"Решение — в {PRIVATE_DOCUMENTS[0]}"])
        self._env(monkeypatch, "Письма: очередь пачкой", body)
        code, _, err = gate(monkeypatch, capsys, tmp_path, "--pr-env")
        assert code == 1
        assert "тело PR:3 [public-repo] имя закрытого документа в теле PR" in err

    @pytest.mark.parametrize(
        "body", ["Что сделано: очередь.\nТесты зелёные.", "", None], ids=["clean", "empty", "null"]
    )
    def test_clean_text_or_empty_body_is_green(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Capture, body: str | None
    ) -> None:
        """Тела нет (`null` у GitHub — пустая строка или нет переменной) — не ошибка."""
        self._env(monkeypatch, "Письма: очередь пачкой", body)
        code, out, _ = gate(monkeypatch, capsys, tmp_path, "--pr-env")
        assert code == 0
        assert "нарушений нет" in out

    def test_missing_title_is_not_judged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Capture
    ) -> None:
        """У PR заголовок есть всегда: пустой — непереданный вход, а не «чисто»."""
        self._env(monkeypatch, None, "Что сделано.")
        code, _, err = gate(monkeypatch, capsys, tmp_path, "--pr-env")
        assert code == 1
        assert "гейт не судил: заголовок PR пуст" in err

    def test_text_alone_does_not_walk_the_tree(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Capture
    ) -> None:
        """Один `--pr-env` — секунды: ни обхода кода, ни списка файлов от git."""

        def walk(*_: object) -> None:
            raise AssertionError("гейт текста PR обошёл дерево")

        monkeypatch.setattr(gates, "_python_files", walk)
        monkeypatch.setattr(public_repo, "tracked_text_files", walk)
        self._env(monkeypatch, "Письма: очередь пачкой", "")
        code, _, _ = gate(monkeypatch, capsys, tmp_path, "--pr-env")
        assert code == 0
