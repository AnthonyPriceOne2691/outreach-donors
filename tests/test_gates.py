"""Тесты на сами гейты.

Гейт, который никогда не краснеет, неотличим от выключенного. Поэтому
каждое правило проверяется заведомо плохим кодом: если гейт его пропустит,
он не работает, и узнать об этом лучше здесь, чем через полгода на ревью.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts.gates import (
    MAX_LINES_PROD,
    check_config_access,
    check_env_example,
    check_file_length,
    check_grab_bag,
    check_layers,
    check_public_repo,
    check_silent_except,
    run,
)


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
        import subprocess  # noqa: PLC0415 — нужен только здесь, ради списка файлов

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
