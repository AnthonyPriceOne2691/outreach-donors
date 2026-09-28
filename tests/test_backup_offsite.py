"""Бэкап с копией вне машины: `scripts/backup.sh` и `scripts/offsite.sh`.

Скрипты запускаются настоящим bash с подставными `docker` и `curl`
(`tests/ops_fakes.py`). Вопросы теста: что сочтено отказом, что ушло
в тревогу, где лежит копия после каждого исхода и не утёк ли ключ
хранилища или токен бота в аргументы и вывод. Живая выгрузка в хранилище
и восстановление из неё проверяются отдельно — настоящим rclone и базой.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from tests.ops_fakes import ROOT, Host

SECRET = "s3-secret-value-for-tests"
TOKEN = "555:bot-token-for-tests"

STORAGE = f"""
# хранилище копий
BACKUP_S3_ENDPOINT=https://acc123.r2.cloudflarestorage.com
BACKUP_S3_BUCKET=outreach-backups
BACKUP_S3_ACCESS_KEY_ID=key-id-for-tests
BACKUP_S3_SECRET="{SECRET}"
"""

BOT = f"""
ALERT_TELEGRAM_BOT_TOKEN={TOKEN}
ALERT_TELEGRAM_CHAT_ID=-100500
"""


@pytest.fixture
def host(tmp_path: Path) -> Host:
    """Машина, на которой всё настроено: хранилище в `.env.ops`, бот в `.env`.
    Каталог копий — с пробелом в пути, как у рабочей копии разработки."""
    machine = Host(tmp_path).install()
    machine.write_settings(".env.ops", STORAGE)
    machine.write_settings(".env", BOT)
    machine.env["BACKUP_DIR"] = str(tmp_path / "копии бэкапа")
    return machine


def _copies(host: Host) -> list[str]:
    folder = Path(host.env["BACKUP_DIR"])
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


def _rclone(host: Host) -> list[dict[str, object]]:
    return [c for c in host.calls("docker") if str(c["key"]).startswith("rclone")]


def _leaks(host: Host, done: object, *secrets: str) -> list[str]:
    """Где нашёлся секрет: в выводе скрипта или в аргументах команд."""
    found = []
    for secret in secrets:
        if secret in str(getattr(done, "stdout", "")) + str(getattr(done, "stderr", "")):
            found.append(f"{secret} в выводе")
        for call in host.calls():
            if any(secret in arg for arg in call["argv"]):
                found.append(f"{secret} в аргументах {call['tool']}")
    return found


class TestUpload:
    def test_copy_goes_to_the_storage_and_is_checked_there(self, host: Host) -> None:
        done = host.run("backup.sh")

        assert done.returncode == 0, done.stderr
        [stamp] = _copies(host)
        calls = _rclone(host)
        assert [c["key"] for c in calls] == ["rclone lsf", "rclone copy", "rclone check"], (
            "проба хранилища, копирование и отдельная сверка с хранилищем"
        )
        remote = f"offsite:outreach-backups/outreach-donors/{stamp}"
        assert calls[1]["argv"][-4:-2] == ["/backup", remote]
        assert calls[2]["argv"][-4:] == ["check", "/backup", remote, "--one-way"]
        assert calls[1]["listed"][f"{host.env['BACKUP_DIR']}/{stamp}:/backup:ro"] == [
            "README.txt",
            "alembic_version.txt",
            "outreach.dump",
        ], "в хранилище уезжает готовая копия, а не недоснятая"
        assert host.alerts() == [], "удачный бэкап молчит"
        assert "Копия ушла во внешнее хранилище" in done.stdout

    def test_storage_key_travels_by_name_not_by_value(self, host: Host) -> None:
        """Аргументы `docker run` видны в `ps` любому на машине: ключ едет
        в контейнер окружением, а в аргументах — только имя переменной."""
        done = host.run("backup.sh")

        assert done.returncode == 0, done.stderr
        for call in _rclone(host):
            assert "RCLONE_CONFIG_OFFSITE_SECRET_ACCESS_KEY" in call["argv"]
            assert call["secret_in_env"] == SECRET
        assert _leaks(host, done, SECRET, TOKEN) == []

    @pytest.mark.parametrize("locale", ["C", "C.UTF-8"])
    @pytest.mark.parametrize("failing", ["rclone copy", "rclone check"])
    def test_upload_or_check_failure_is_a_failed_backup_and_one_alert(
        self, host: Host, failing: str, locale: str
    ) -> None:
        """Копия легла не целиком — это не «бэкап с замечанием», а бэкап,
        которого вне машины нет. Локальная копия при этом остаётся.

        В двух локалях: у крона C, у человека UTF-8. 28.09.2026 живая
        проверка в UTF-8 нашла, что тревога об отказе падала сама —
        bash 3.2 читал `$STEP»` как другое имя."""
        host.locale = locale
        host.scenario[failing] = {"code": 1, "err": "ERROR : outreach.dump: file not found\n"}

        done = host.run("backup.sh")

        assert done.returncode != 0
        assert len(_copies(host)) == 1, "локальная копия остаётся — ей откатываются"
        [alert] = host.alerts()
        assert alert.startswith("outreach-donors: бэкап НЕ снят или не уехал с машины")
        assert "шаг «выгрузка»" in alert
        assert "file not found" in alert, "причина из вывода rclone — в тревоге"
        assert _leaks(host, done, SECRET, TOKEN) == []

    def test_silent_storage_is_named_by_its_own_reason(self, host: Host) -> None:
        """При недоступном адресе копирование у rclone кончается словами
        «is a file not a directory» — тревога с чужой причиной. Проба перед
        копированием называет настоящую."""
        host.scenario["rclone lsf"] = {
            "code": 1,
            "err": "NOTICE: Failed to lsf: dial tcp: lookup acc123.r2.cloudflarestorage.com: no such host\n",
        }

        done = host.run("backup.sh")

        assert done.returncode != 0
        assert [c["key"] for c in _rclone(host)] == ["rclone lsf"], (
            "копировать некуда — и не пробуем"
        )
        [alert] = host.alerts()
        assert "хранилище не отвечает или не пускает" in alert
        assert "no such host" in alert

    def test_without_storage_the_copy_stays_and_says_so(self, host: Host) -> None:
        """Хранилище не настроено: бэкап снят, выгрузки нет — и об этом
        говорится вслух, а не молчанием."""
        host.write_settings(".env.ops", "")

        done = host.run("backup.sh")

        assert done.returncode == 0
        assert _rclone(host) == []
        assert "Выгрузка не настроена — копия осталась на этой же машине" in done.stderr
        [alert] = host.alerts()
        assert "остался на этой же машине" in alert

    def test_empty_upload_in_the_environment_switches_it_off_once(self, host: Host) -> None:
        host.env["BACKUP_UPLOAD"] = ""

        done = host.run("backup.sh")

        assert done.returncode == 0
        assert _rclone(host) == []
        assert "копия осталась на этой же машине" in done.stderr

    def test_own_upload_command_gets_the_path_whole(self, host: Host, tmp_path: Path) -> None:
        """Своя команда выгрузки заменяет хранилище, а путь с пробелом
        приходит ей одним аргументом."""
        seen = tmp_path / "выгружено.txt"
        host.env["BACKUP_UPLOAD"] = f"printf '%s' {{}} > '{seen}'"

        done = host.run("backup.sh")

        assert done.returncode == 0, done.stderr
        [stamp] = _copies(host)
        assert seen.read_text() == f"{host.env['BACKUP_DIR']}/{stamp}"
        assert _rclone(host) == []

    def test_dump_does_not_swallow_the_callers_script(self, host: Host) -> None:
        """`exec -T` пересылает в контейнер stdin скрипта. Бэкап, запущенный
        из выкатки, которую кормят через stdin (`ssh … bash -s < выкатка.sh`),
        отдавал pg_dump её остаток, и всё после бэкапа молча не выполнялось.
        Найдено живой проверкой 28.09.2026."""
        done = host.run("backup.sh", stdin="docker compose pull && docker compose up -d\n")

        assert done.returncode == 0, done.stderr
        execs = [c for c in host.calls("docker") if str(c["key"]).startswith("compose exec")]
        assert [c["key"] for c in execs] == ["compose exec pg_dump", "compose exec psql"]
        assert [c["stdin"] for c in execs] == ["", ""], "остаток вызывающего не ушёл в контейнер"


class TestFailuresBeforeUpload:
    def test_failed_dump_is_an_alert_and_leaves_nothing_half_made(self, host: Host) -> None:
        host.scenario["compose exec pg_dump"] = {
            "code": 1,
            "err": "pg_dump: error: connection to server failed: FATAL: the database system is starting up\n",
        }

        done = host.run("backup.sh")

        assert done.returncode != 0
        assert _copies(host) == [], "ни копии, ни недоснятого каталога"
        assert _rclone(host) == [], "выгружать нечего"
        [alert] = host.alerts()
        assert "шаг «дамп базы»" in alert
        assert "the database system is starting up" in alert

    def test_text_instead_of_an_archive_is_not_a_backup(self, host: Host) -> None:
        """`exec` отдал в stdout текст ошибки, а код — ноль: без проверки
        подписи PGDMP такой «бэкап» нашёлся бы в день восстановления."""
        host.scenario["compose exec pg_dump"] = {"code": 0, "out": "Error: no such service\n"}

        done = host.run("backup.sh")

        assert done.returncode != 0
        assert _copies(host) == []
        [alert] = host.alerts()
        assert "не похож на архив pg_dump" in alert

    def test_silent_compose_is_named_as_such(self, host: Host) -> None:
        host.scenario["compose ps running"] = {
            "code": 1,
            "err": "Cannot connect to the Docker daemon at unix:///var/run/docker.sock\n",
        }

        done = host.run("backup.sh")

        assert done.returncode != 0
        [alert] = host.alerts()
        assert "docker compose не отвечает" in alert
        assert "Cannot connect to the Docker daemon" in alert

    def test_unconfigured_bot_does_not_hide_the_failure(self, host: Host) -> None:
        """Бот не настроен: бэкап всё равно падает с кодом, а о несостоявшейся
        тревоге — громкая строка в журнале крона."""
        host.write_settings(".env", "")
        host.scenario["rclone check"] = {"code": 1}

        done = host.run("backup.sh")

        assert done.returncode != 0
        assert host.alerts() == []
        assert "ТРЕВОГА НЕ ОТПРАВЛЕНА" in done.stderr
        assert "бэкап НЕ снят" in done.stderr, "текст тревоги не пропадает вместе с ней"


def test_no_variable_runs_into_a_letter() -> None:
    """`$ИМЯ` вплотную к букве не латиницей — в скобки: bash 3.2 в локали UTF-8
    берёт первый байт буквы в имя, и под `set -u` скрипт падает на пустом
    месте. Правило проверяется здесь, а не помнится."""
    pattern = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*(?=[^\x00-\x7f])")
    found = [
        f"{script.name}:{number}: {line.strip()[:80]}"
        for script in sorted((ROOT / "scripts").glob("*.sh"))
        for number, line in enumerate(script.read_text(encoding="utf-8").splitlines(), 1)
        if not line.lstrip().startswith("#") and pattern.search(line)
    ]
    assert found == [], "взять в скобки: ${ИМЯ}"


class TestFetch:
    def test_fetch_downloads_checks_and_names_the_next_step(
        self, host: Host, tmp_path: Path
    ) -> None:
        into = tmp_path / "восстановление"

        done = host.run("offsite.sh", "fetch", "2026-09-27-030000", str(into))

        assert done.returncode == 0, done.stderr
        assert (into / "2026-09-27-030000" / "outreach.dump").read_bytes().startswith(b"PGDMP")
        assert not (into / "2026-09-27-030000.partial").exists()
        calls = _rclone(host)
        assert [c["key"] for c in calls] == ["rclone lsf", "rclone copy", "rclone check"]
        assert calls[2]["argv"][-3:-1] == [
            "offsite:outreach-backups/outreach-donors/2026-09-27-030000",
            "/restore/2026-09-27-030000.partial",
        ]
        assert f"scripts/restore.sh {into}/2026-09-27-030000" in done.stdout

    def test_fetch_that_does_not_check_out_is_not_offered_for_restore(
        self, host: Host, tmp_path: Path
    ) -> None:
        host.scenario["rclone check"] = {"code": 1, "err": "ERROR : 1 differences found\n"}
        into = tmp_path / "восстановление"

        done = host.run("offsite.sh", "fetch", "2026-09-27-030000", str(into))

        assert done.returncode != 0
        assert not (into / "2026-09-27-030000").exists()
        assert "не восстанавливать" in done.stderr

    def test_fetch_never_overwrites_a_copy(self, host: Host, tmp_path: Path) -> None:
        into = tmp_path / "восстановление"
        (into / "2026-09-27-030000").mkdir(parents=True)

        done = host.run("offsite.sh", "fetch", "2026-09-27-030000", str(into))

        assert done.returncode != 0
        assert "уже есть" in done.stderr
        assert _rclone(host) == []

    def test_list_shows_the_dumps(self, host: Host) -> None:
        host.scenario["rclone lsl"] = {
            "out": "  1048576 2026-09-27 03:00:12.000000000 2026-09-27-030000/outreach.dump\n"
        }

        done = host.run("offsite.sh", "list")

        assert done.returncode == 0
        assert "2026-09-27-030000/outreach.dump" in done.stdout


class TestStorageSettings:
    @pytest.mark.parametrize(
        ("endpoint", "provider", "region"),
        [
            ("https://acc123.r2.cloudflarestorage.com", "Cloudflare", "auto"),
            ("https://s3.eu-central-003.backblazeb2.com", "Other", "eu-central-003"),
        ],
    )
    def test_provider_and_region_follow_the_endpoint(
        self, host: Host, endpoint: str, provider: str, region: str
    ) -> None:
        """Адрес человек копирует из кабинета целиком, а поставщика и регион
        легко перепутать: B2 подписывает запросы своим регионом из адреса."""
        host.env["BACKUP_S3_ENDPOINT"] = endpoint

        host.run("offsite.sh", "list")

        [call] = _rclone(host)
        assert call["env"]["RCLONE_CONFIG_OFFSITE_PROVIDER"] == provider
        assert call["env"]["RCLONE_CONFIG_OFFSITE_REGION"] == region

    def test_unconfigured_storage_names_what_is_missing(self, host: Host) -> None:
        host.write_settings(".env.ops", "BACKUP_S3_BUCKET=outreach-backups\n")

        done = host.run("offsite.sh", "list")

        assert done.returncode != 0
        assert "BACKUP_S3_ENDPOINT" in done.stderr
        assert "BACKUP_S3_SECRET" in done.stderr
        assert "BACKUP_S3_BUCKET" not in done.stderr.split("не заданы:")[1]
        assert _rclone(host) == []
