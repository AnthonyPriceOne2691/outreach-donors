"""Подставные `docker` и `curl` для тестов скриптов хоста.

Скрипты хоста (`scripts/backup.sh`, `offsite.sh`, `alert.sh`,
`healthwatch.sh`) зовут `docker` и `curl` по имени из PATH. Тест кладёт
в начало PATH свои: они записывают, чем их позвали, и отвечают по сценарию.
Так проверяется логика скриптов — что сочтено отказом, что ушло в тревогу,
чего нет в выводе и в аргументах. Настоящий докер, хранилище и Bot API
проверяются живым прогоном, а не здесь: подделка отвечает правдоподобно,
и это один угол зрения, а не доказательство.

Сценарий — JSON в файле: ключ — вызов, значение — `{"code", "out", "err"}`.
Журнал — строка JSON на вызов.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

_DOCKER = r"""
import json, os, sys
from pathlib import Path

scenario = json.loads(Path(os.environ["FAKE_SCENARIO"]).read_text(encoding="utf-8"))
argv = sys.argv[1:]
record = {"tool": "docker", "argv": argv}
record["env"] = {
    name: os.environ.get(name)
    for name in ("RCLONE_CONFIG_OFFSITE_PROVIDER", "RCLONE_CONFIG_OFFSITE_REGION",
                 "RCLONE_CONFIG_OFFSITE_ENDPOINT")
}
record["secret_in_env"] = os.environ.get("RCLONE_CONFIG_OFFSITE_SECRET_ACCESS_KEY") or ""

def key_of(argv):
    if argv[:1] == ["compose"]:
        rest = argv[1:]
        if rest[:1] == ["ps"]:
            return "compose ps running" if "--status" in rest else "compose ps"
        if rest[:1] == ["config"]:
            return "compose config"
        if rest[:1] == ["exec"]:
            return "compose exec " + ("pg_dump" if "pg_dump" in rest else "psql")
        return "compose " + " ".join(rest[:1])
    if argv[:1] == ["run"]:
        image = next(i for i, a in enumerate(argv) if a.startswith("rclone/"))
        return "rclone " + argv[image + 1]
    return " ".join(argv[:1])

#: Исправная машина: postgres запущен, дамп — архив pg_dump, схема известна.
DEFAULTS = {
    "compose ps running": {"out": "postgres\nredis\nworker\n"},
    "compose exec pg_dump": {"out": "PGDMP-подставной дамп"},
    "compose exec psql": {"out": "5946299ccd9a\n"},
}

key = key_of(argv)
record["key"] = key
if key.startswith("compose exec"):
    # `exec -T` пересылает в контейнер stdin вызвавшего — как настоящий докер.
    record["stdin"] = sys.stdin.read()
answer = scenario.get(key, DEFAULTS.get(key, {}))
code = answer.get("code", 0)

if key == "rclone copy" and code == 0:
    # Скачивание в смонтированный каталог — создать то, что скачал бы rclone.
    # Ключи докера — до имени образа; `-v` после него — уже rclone (подробный вывод).
    image = next(i for i, a in enumerate(argv) if a.startswith("rclone/"))
    mounts = [argv[i + 1] for i, a in enumerate(argv[:image]) if a == "-v"]
    for arg in argv[image:]:
        if arg.startswith("/restore/"):
            host = next(m for m in mounts if m.endswith(":/restore"))[: -len(":/restore")]
            where = Path(host) / arg[len("/restore/"):]
            where.mkdir(parents=True, exist_ok=True)
            (where / "outreach.dump").write_bytes(b"PGDMP-from-storage")
            (where / "alembic_version.txt").write_text("5946299ccd9a\n")
    record["mounted"] = mounts
    for mount in mounts:
        host = mount.rsplit(":/", 1)[0]
        if Path(host).is_dir():
            record.setdefault("listed", {})[mount] = sorted(p.name for p in Path(host).iterdir())

with Path(os.environ["FAKE_LOG"]).open("a", encoding="utf-8") as log:
    log.write(json.dumps(record, ensure_ascii=False) + "\n")

sys.stdout.write(answer.get("out", ""))
sys.stderr.write(answer.get("err", ""))
sys.exit(code)
"""

_CURL = r"""
import json, os, sys
from pathlib import Path

config = sys.stdin.read()
argv = sys.argv[1:]
url = None
for line in config.splitlines():
    name, _, value = line.partition("=")
    if name.strip() == "url":
        url = value.strip().strip('"')
data = {}
for i, arg in enumerate(argv):
    if arg == "--data-urlencode":
        name, _, value = argv[i + 1].partition("=")
        data[name] = value
record = {"tool": "curl", "argv": argv, "stdin": config, "url": url, "data": data}
with Path(os.environ["FAKE_LOG"]).open("a", encoding="utf-8") as log:
    log.write(json.dumps(record, ensure_ascii=False) + "\n")

scenario = json.loads(Path(os.environ["FAKE_SCENARIO"]).read_text(encoding="utf-8"))
answer = scenario.get("telegram", {"out": '{"ok":true,"result":{"message_id":1}}'})
sys.stdout.write(answer.get("out", ""))
sys.stderr.write(answer.get("err", ""))
sys.exit(answer.get("code", 0))
"""


@dataclass
class Host:
    """Подставная машина: каталог с фальшивыми `docker` и `curl`, сценарий,
    журнал вызовов и каталог с `.env.ops`/`.env` вместо настоящих."""

    root: Path
    scenario: dict[str, Any] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    #: Локаль скрипта. У крона обычно C, у человека в терминале — UTF-8,
    #: и bash 3.2 в UTF-8 разбирает `$ИМЯ»` иначе (`test_backup_offsite`).
    locale: str = "C"

    @property
    def bin(self) -> Path:
        return self.root / "bin"

    @property
    def log(self) -> Path:
        return self.root / "calls.jsonl"

    @property
    def settings(self) -> Path:
        """Каталог, где скрипты ищут `.env.ops` и `.env` (OPS_ENV_DIR)."""
        return self.root / "settings"

    def install(self) -> Host:
        self.bin.mkdir(parents=True, exist_ok=True)
        self.settings.mkdir(parents=True, exist_ok=True)
        for name, source in (("docker", _DOCKER), ("curl", _CURL)):
            code = self.root / f"fake_{name}.py"
            code.write_text(source, encoding="utf-8")
            wrapper = self.bin / name
            # Обёртка на sh: путь к питону проекта с пробелом, а строка
            # `#!` пробелов в пути не понимает.
            wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" -I "{code}" "$@"\n')
            wrapper.chmod(0o755)
        return self

    def write_settings(self, name: str, text: str) -> None:
        (self.settings / name).write_text(text, encoding="utf-8")

    def calls(self, tool: str | None = None) -> list[dict[str, Any]]:
        if not self.log.exists():
            return []
        rows = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]
        return [row for row in rows if tool is None or row["tool"] == tool]

    def alerts(self) -> list[str]:
        """Тексты тревог, которые скрипты отдали curl."""
        return [row["data"].get("text", "") for row in self.calls("curl")]

    def run(
        self, script: str, *args: str, stdin: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        scenario = self.root / "scenario.json"
        scenario.write_text(json.dumps(self.scenario, ensure_ascii=False), encoding="utf-8")
        environment = {
            # Окружение с нуля: переменные разработчика (его бот, его
            # хранилище) не должны доехать до скрипта под тестом.
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(self.root),
            "TMPDIR": str(self.root),
            "LANG": self.locale,
            "OPS_ENV_DIR": str(self.settings),
            "FAKE_SCENARIO": str(scenario),
            "FAKE_LOG": str(self.log),
            **self.env,
        }
        # stdin — пустой, если тест не задал свой: подделка докера читает его
        # до конца, и терминал разработчика подвесил бы её навсегда.
        return subprocess.run(
            ["bash", str(ROOT / "scripts" / script), *args],
            input=stdin if stdin is not None else "",
            capture_output=True,
            text=True,
            # Байт, разрезавший букву, — не повод ронять тест до проверок.
            errors="replace",
            env=environment,
            cwd=self.root,
            check=False,
            timeout=60,
        )
