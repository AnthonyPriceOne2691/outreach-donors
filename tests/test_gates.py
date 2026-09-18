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
    check_file_length,
    check_grab_bag,
    check_layers,
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
