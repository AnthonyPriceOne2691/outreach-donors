"""Тревоги с хоста: `scripts/alert.sh`, настройки кронов и сторож здоровья.

Скрипты запускаются настоящим bash с подставными `docker` и `curl`
(`tests/ops_fakes.py`). Сторож проверяется последовательностью проходов,
как его зовёт крон: тревога — только на переходе, одна на разницу,
и не теряется, если не ушла.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest
import yaml
from tests.ops_fakes import ROOT, Host

TOKEN = "777:watch-token-for-tests"

#: Все сервисы компоуза, как их называет `docker compose config --services`.
SERVICES = "postgres\nredis\nmigrate\napi\nworker\nreaper\nfollowups\nweb\n"

HEALTHY = {
    "postgres": "running|healthy|0",
    "redis": "running|healthy|0",
    "migrate": "exited||0",
    "api": "running|healthy|0",
    "worker": "running|healthy|0",
    "reaper": "running|healthy|0",
    "followups": "running|healthy|0",
    "web": "running|healthy|0",
}


@pytest.fixture
def host(tmp_path: Path) -> Host:
    machine = Host(tmp_path).install()
    machine.write_settings(
        ".env", f"ALERT_TELEGRAM_BOT_TOKEN={TOKEN}\nALERT_TELEGRAM_CHAT_ID=-42\n"
    )
    machine.env["HEALTHWATCH_STATE"] = str(tmp_path / "state")
    machine.scenario["compose config"] = {"out": SERVICES}
    return machine


def _stack(host: Host, **changed: str | None) -> None:
    """Что покажет `docker compose ps`: здоровый стек с правками. `None` —
    контейнера нет вовсе."""
    rows = {**HEALTHY, **changed}
    host.scenario["compose ps"] = {
        "out": "".join(f"{name}|{row}\n" for name, row in rows.items() if row is not None)
    }


class TestAlertScript:
    def test_token_goes_through_stdin_not_arguments(self, host: Host) -> None:
        """Аргументы видны в `ps` любому на машине: адрес с токеном уходит
        в curl через stdin."""
        done = host.run("alert.sh", "бэкап не снят")

        assert done.returncode == 0, done.stderr
        [call] = host.calls("curl")
        assert call["url"] == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        assert not any(TOKEN in arg for arg in call["argv"])
        assert call["data"] == {"chat_id": "-42", "text": "outreach-donors: бэкап не снят"}

    def test_unconfigured_bot_is_loud_and_distinct(self, host: Host) -> None:
        host.write_settings(".env", "")

        done = host.run("alert.sh", "бэкап не снят")

        assert done.returncode == 3
        assert "ТРЕВОГА НЕ ОТПРАВЛЕНА" in done.stderr
        assert "outreach-donors: бэкап не снят" in done.stderr
        assert host.calls("curl") == []

    def test_refusal_says_what_to_do_and_hides_the_token(self, host: Host) -> None:
        """Ответ с ошибкой печатается в журнал крона — и в нём не должно
        быть токена, даже если его вернул сам сервер."""
        host.scenario["telegram"] = {
            "out": f'{{"ok":false,"error_code":401,"description":"Unauthorized /bot{TOKEN}/"}}'
        }

        done = host.run("alert.sh", "проверка")

        assert done.returncode == 1
        assert "@BotFather" in done.stderr
        assert TOKEN not in done.stderr + done.stdout
        assert "<токен>" in done.stderr

    def test_long_text_is_cut_between_letters(self, host: Host) -> None:
        """У крона локаль C, и срез строки посреди кириллической буквы дал бы
        текст, который Telegram не примет вовсе."""
        host.run("alert.sh", "я" * 5000)

        [text] = host.alerts()
        encoded = text.encode("utf-8")
        assert len(encoded) <= 3600
        assert text.endswith("…")
        assert encoded.decode("utf-8") == text


class TestSettingsFiles:
    def test_own_names_only_first_file_wins_environment_wins_over_both(
        self, host: Host, tmp_path: Path
    ) -> None:
        host.write_settings(
            ".env.ops",
            'BACKUP_S3_BUCKET="в кавычках"\n'
            "ALERT_TELEGRAM_CHAT_ID=-100 # мой чат\n"
            "BACKUP_S3_PREFIX=из-файла\n"
            f"BACKUP_S3_REGION=$(touch {tmp_path}/исполнено)\n",
        )
        host.write_settings(".env", "ALERT_TELEGRAM_CHAT_ID=-200\r\nPOSTGRES_PASSWORD=секрет\n")
        probe = (
            f'. "{ROOT}/scripts/ops_env.sh"; ops_env_load /нигде; '
            'printf "%s|" "$BACKUP_S3_BUCKET" "$ALERT_TELEGRAM_CHAT_ID" "$BACKUP_S3_PREFIX" '
            '"$BACKUP_S3_REGION" "${POSTGRES_PASSWORD-не задан}"'
        )

        done = subprocess.run(
            ["bash", "-c", probe],
            capture_output=True,
            text=True,
            env={
                "PATH": os.environ["PATH"],
                "OPS_ENV_DIR": str(host.settings),
                "BACKUP_S3_PREFIX": "из-окружения",
            },
            check=True,
        )

        bucket, chat, prefix, region, password, _ = done.stdout.split("|")
        assert bucket == "в кавычках"
        assert chat == "-100", ".env.ops читается первым, комментарий после значения — не значение"
        assert prefix == "из-окружения", "окружение сильнее файла: KEEP=10 перед выкаткой"
        assert region == f"$(touch {tmp_path}/исполнено)"
        assert not (tmp_path / "исполнено").exists(), "строки файла не исполняются"
        assert password == "не задан", "чужие имена из .env скрипту хоста не нужны"


class TestHealthwatch:
    def test_healthy_stack_is_quiet(self, host: Host) -> None:
        _stack(host)

        done = host.run("healthwatch.sh")

        assert done.returncode == 0, done.stderr
        assert host.alerts() == []
        assert done.stdout == ""
        assert Path(host.env["HEALTHWATCH_STATE"]).read_text() == ""

    def test_unhealthy_is_told_once_and_recovery_once(self, host: Host) -> None:
        _stack(host, worker="running|unhealthy|0")
        host.run("healthwatch.sh")
        host.run("healthwatch.sh")
        host.run("healthwatch.sh")
        _stack(host)
        host.run("healthwatch.sh")
        host.run("healthwatch.sh")

        broke, healed = host.alerts()
        assert broke.startswith(
            "outreach-donors: сторож здоровья\nСломалось:\n• worker — unhealthy"
        )
        assert "docker inspect" in broke, "тревога называет, куда смотреть"
        assert healed == "outreach-donors: сторож здоровья\nВосстановилось: worker"

    @pytest.mark.parametrize(
        ("row", "said"),
        [
            ("exited||137", "exited, код 137"),
            ("restarting||1", "restarting"),
            ("dead||0", "dead, код 0"),
            (None, "нет контейнера"),
        ],
    )
    def test_what_counts_as_broken(self, host: Host, row: str | None, said: str) -> None:
        _stack(host, followups=row)

        host.run("healthwatch.sh")

        [alert] = host.alerts()
        assert f"• followups — {said}" in alert

    def test_migrations_may_finish_but_not_fail(self, host: Host) -> None:
        _stack(host, migrate="exited||0")
        host.run("healthwatch.sh")
        assert host.alerts() == [], "миграции завершились — так и задумано"

        _stack(host, migrate="exited||1")
        host.run("healthwatch.sh")
        [alert] = host.alerts()
        assert "• migrate — exited, код 1" in alert

    def test_starting_is_neither_broken_nor_healed(self, host: Host) -> None:
        """Перезапуск в цикле падений: сломан → поднят (`starting`) → снова
        сломан. Одна тревога, а не пара на каждый круг."""
        _stack(host, worker="exited||1")
        host.run("healthwatch.sh")
        _stack(host, worker="running|starting|0")
        host.run("healthwatch.sh")
        _stack(host, worker="exited||1")
        host.run("healthwatch.sh")
        assert len(host.alerts()) == 1

        _stack(host, worker="exited||1", api="running|starting|0")
        host.run("healthwatch.sh")
        assert len(host.alerts()) == 1, "здоровый, который перезапускается, — не поломка"

    def test_silent_compose_keeps_what_was_known(self, host: Host) -> None:
        _stack(host, worker="running|unhealthy|0")
        host.run("healthwatch.sh")
        host.scenario["compose ps"] = {
            "code": 1,
            "err": "Cannot connect to the Docker daemon at unix:///var/run/docker.sock\n",
        }
        host.run("healthwatch.sh")
        _stack(host)
        host.run("healthwatch.sh")

        worker_broke, docker_broke, both_healed = host.alerts()
        assert "• worker — unhealthy" in worker_broke
        assert "• docker — docker compose не отвечает: Cannot connect" in docker_broke
        assert "worker" not in docker_broke, "про сервисы без компоуза не известно ничего нового"
        assert both_healed.endswith("Восстановилось: docker, worker")

    def test_undelivered_alert_is_repeated(self, host: Host) -> None:
        """Тревога не ушла — состояние не записано, следующий проход скажет
        о той же разнице ещё раз, а не промолчит навсегда."""
        _stack(host, worker="running|unhealthy|0")
        host.scenario["telegram"] = {"code": 6, "err": "curl: (6) Could not resolve host\n"}

        failed = host.run("healthwatch.sh")
        host.scenario.pop("telegram")
        retried = host.run("healthwatch.sh")
        host.run("healthwatch.sh")

        assert failed.returncode == 1
        assert "следующий проход повторит" in failed.stderr
        assert retried.returncode == 0
        assert len(host.alerts()) == 2, "первая не ушла, вторая ушла, третьей нет"

    def test_unconfigured_bot_is_one_loud_line_not_one_every_five_minutes(self, host: Host) -> None:
        host.write_settings(".env", "")
        _stack(host, worker="running|unhealthy|0")

        first = host.run("healthwatch.sh")
        second = host.run("healthwatch.sh")

        assert first.returncode == 0
        assert "ТРЕВОГА НЕ ОТПРАВЛЕНА" in first.stderr
        assert "ТРЕВОГА НЕ ОТПРАВЛЕНА" not in second.stderr
        assert "по-прежнему сломано: worker" in second.stdout

    def test_token_is_not_in_the_output(self, host: Host) -> None:
        _stack(host, worker="running|unhealthy|0")

        done = host.run("healthwatch.sh")

        assert TOKEN not in done.stdout + done.stderr

    def test_stale_backup_is_a_broken_backup(self, host: Host, tmp_path: Path) -> None:
        """Бэкап, который не запустился вовсе, сам о себе не скажет: его
        видно только по возрасту последней копии."""
        _stack(host)
        copies = tmp_path / "копии"
        old = copies / "2026-09-13-030000"
        old.mkdir(parents=True)
        ten_days_ago = time.time() - 10 * 24 * 3600
        os.utime(old, (ten_days_ago, ten_days_ago))
        (copies / "2026-09-27-030000.partial").mkdir()
        host.env["BACKUP_DIR"] = str(copies)

        host.run("healthwatch.sh")
        (copies / "2026-09-28-030000").mkdir()
        host.run("healthwatch.sh")

        broke, healed = host.alerts()
        assert "• backup — свежей копии нет дольше 8 дней" in broke
        assert "/var/log/outreach-backup.log" in broke
        assert "docker inspect" not in broke, "подсказка — к тому, что сломалось"
        assert healed.endswith("Восстановилось: backup")

    def test_every_service_of_the_compose_is_watchable(self) -> None:
        """Сторож видит только то, что докер умеет пометить: у долгоживущего
        сервиса без проверки здоровья «вставший» не отличим от «идущего».
        А одноразовые — те, кому положено завершиться, — названы в сторже."""
        compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
        watch = (ROOT / "scripts" / "healthwatch.sh").read_text(encoding="utf-8")

        oneshot = {n for n, s in compose["services"].items() if str(s.get("restart")) == "no"}
        blind = [
            name
            for name, service in compose["services"].items()
            if name not in oneshot and "healthcheck" not in service
        ]

        assert blind == [], f"у {blind} нет healthcheck — сторож не увидит, что они встали"
        assert oneshot == {"migrate"}
        assert "HEALTHWATCH_ONESHOT:-migrate}" in watch
