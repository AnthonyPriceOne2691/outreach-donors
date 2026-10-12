"""Цикл докрутки агента продаж одной командой — `scripts/sales_replay.py cycle` (срез 6.4).

Живые черновики продаж с решением человека → прежняя версия (промпты пакета, база знаний из
таблицы) и новая (каталог версии) на тех же случаях → ворота «не хуже» с числами. Модель
ситуации и судьи — подставной HTTP, писатель — подставной (`test_sales_replay`). Проверяется:

- новая версия молчит там, где человек ответил, — «ХУЖЕ» и код 1; та же — «НЕ ХУЖЕ» и 0;
- база знаний версии — файлом `kb.json` вместо таблицы: бриф и версия прогона видят её, а
  после прогона агент снова берёт таблицу;
- негодная версия, база или каталог прогонов — отказ словами до базы и модели;
- `kb-export` выгружает таблицу базы файлом версии — тот же разбор читает его обратно.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
from backend.config import llm as llm_cfg
from backend.features.sales import kb
from backend.features.sales.agent import moves, replay, replay_kb, replay_sets
from backend.features.sales.models import KbKind
from scripts import sales_replay
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_agent_brief import world
from tests.test_sales_model import ROOT
from tests.test_sales_replay import (
    LABELS,
    NEW,
    WORSE,
    Plug,
    Writer,
    live_world,
    model,
    offline,
    version_dir,
)

__all__ = ["model", "offline"]  # фикстуры подставной модели и «без базы» — отсюда их видит pytest

#: Запись базы версии: правленая политика цен — её должен увидеть бриф новой версии.
PRICE = "Цену называем после короткого звонка, пишите в Telegram."


def cycle_args(*extra: str) -> argparse.Namespace:
    return sales_replay.parser().parse_args(["cycle", *extra])


def kb_file(folder: Path, *, price: str = PRICE, broken: bool = False) -> Path:
    """База версии файлом — записи `world` с правленой политикой цен."""
    folder.mkdir(exist_ok=True)
    records: list[dict[str, object]] = [
        {"kind": "brief", "language": "ru", "title": "Кто мы", "text": "Делаем аудит сайтов."},
        {"kind": "price_policy", "language": "ru", "title": "Цены", "text": price,
         "tags": ["аудит"]},
        {"kind": "case", "language": "ru", "title": "Магазин",
         "text": "Трафик вырос в 3,7 раза за 41 день.", "tags": ["аудит"]},
        {"kind": "forbidden", "language": "ru", "title": "Нельзя", "text": "Не обещать позиции."},
        {"kind": "case", "language": "ru", "title": "Выключенный кейс", "text": "Старый кейс.",
         "active": False},
    ]  # fmt: skip
    if broken:
        records.append({"kind": "lunch", "language": "ru", "title": "Обед", "text": "Нет."})
    path = folder / replay.KB_FILE
    path.write_text(json.dumps(records, ensure_ascii=False), "utf-8")
    return path


# --- цикл --------------------------------------------------------------------------------------


async def test_cycle_new_version_silent_where_the_human_answered_is_worse(
    session: AsyncSession, model: Plug, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    await world(session)
    await live_world(session)
    found = model(LABELS, new=WORSE)
    args = cycle_args("--version", str(version_dir(tmp_path / "new", marked=True)))

    code = await sales_replay.cycle(session, Writer(), args)

    out = capsys.readouterr().out
    assert code == 1
    assert "Живые продажи: случаев с исходом 5; ждут решения 1; пропусков моложе 3 дн. 1" in out
    assert "ВОРОТА ЗАКРЫТЫ: ложного молчания больше: было 0, стало 1" in out
    assert "ХУЖЕ: общих случаев 5: ложное молчание 0 → 1, черновиков с нарушениями 0 → 0" in out
    # Прежняя версия — пакет: её промпт ситуации без пометки новой.
    assert sum(NEW in system for system in found.situations) == 5
    assert sum(NEW not in system for system in found.situations) == 5


async def test_cycle_same_behaviour_is_not_worse_and_both_runs_are_saved(
    session: AsyncSession, model: Plug, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    await world(session)
    await live_world(session)
    model(LABELS)
    folder = tmp_path / "v"
    folder.mkdir()
    table = moves.TABLE.read_text(encoding="utf-8")
    (folder / "moves.toml").write_text(
        table.replace('"sales-moves-v1"', '"sales-moves-x"'), "utf-8"
    )
    runs = tmp_path / "runs"
    runs.mkdir()

    code = await sales_replay.cycle(
        session,
        Writer(),
        cycle_args("--version", str(folder), "--out", str(runs)),
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "ВОРОТА ОТКРЫТЫ: новая версия не хуже прежней" in out
    assert "НЕ ХУЖЕ: общих случаев 5: ложное молчание 0 → 0, черновиков с нарушениями 0 → 0" in out
    old, new = (
        replay_sets.loaded(json.loads((runs / name).read_text("utf-8")), name)
        for name in (sales_replay.CYCLE_OLD, sales_replay.CYCLE_NEW)
    )
    assert old.version["declared"]["moves"] == "sales-moves-v1"
    assert new.version["declared"]["moves"] == "sales-moves-x"
    assert len(old.results) == len(new.results) == 5


async def test_cycle_runs_the_new_version_on_its_knowledge_base_file(
    session: AsyncSession, model: Plug, tmp_path: Path
) -> None:
    await world(session)
    await live_world(session)
    model(LABELS)
    folder = tmp_path / "v"
    kb_file(folder)
    runs = tmp_path / "runs"
    runs.mkdir()
    writer = Writer()
    table = kb.facts

    code = await sales_replay.cycle(
        session,
        writer,
        cycle_args("--version", str(folder), "--out", str(runs)),
    )

    assert code == 0
    facts = ["\n".join(request.facts) for request in writer.seen]
    assert any("Цены в письме не называем" in said for said in facts)  # прежняя — таблица
    assert any(PRICE in said for said in facts)  # новая — файл
    assert not any("Старый кейс" in said for said in facts)  # выключенная запись не видна
    old, new = (
        replay_sets.loaded(json.loads((runs / name).read_text("utf-8")), name)
        for name in (sales_replay.CYCLE_OLD, sales_replay.CYCLE_NEW)
    )
    assert old.version["kb"] == await kb.version(session)
    assert new.version["kb"] == kb.version_of(replay.kb_facts(folder / replay.KB_FILE))
    assert new.version["kb"] != old.version["kb"]
    assert "kb" in new.version["prompts"]
    assert kb.facts is table  # после прогона агент снова берёт таблицу


async def test_cycle_without_a_model_key_could_not_compare_rather_than_worse(
    session: AsyncSession,
    model: Plug,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await world(session)
    await live_world(session)
    model(LABELS)
    monkeypatch.setattr(llm_cfg, "API_KEY", "")
    args = cycle_args("--version", str(version_dir(tmp_path / "v", marked=True)))

    assert await sales_replay.cycle(session, Writer(), args) == 1

    out = capsys.readouterr().out
    assert "ВОРОТА ЗАКРЫТЫ: прежний прогон неполный: модель отказала насовсем" in out
    assert "НЕ СРАВНИТЬ: общих случаев 0" in out
    assert "ХУЖЕ:" not in out


async def test_cycle_without_live_decisions_is_refused(
    session: AsyncSession, model: Plug, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    found = model()
    args = cycle_args("--version", str(version_dir(tmp_path / "v", marked=True)))

    assert await sales_replay.cycle(session, Writer(), args) == 1

    assert "НАБОР ПУСТ: у черновиков продаж ещё нет решений людей" in capsys.readouterr().out
    assert found.calls == 0


@pytest.mark.usefixtures("offline")
def test_cycle_refuses_a_bad_version_before_the_base_and_the_model(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert sales_replay.main(["cycle", "--version", str(empty)]) == 1
    assert "нет ни одного из situation.md, reply.md, judge.md, moves.toml, kb.json" in (
        capsys.readouterr().out
    )
    broken = tmp_path / "broken"
    kb_file(broken, broken=True)
    assert sales_replay.main(["cycle", "--version", str(broken)]) == 1
    assert "база знаний версии" in (out := capsys.readouterr().out)
    assert "записей с ошибкой 1: №6" in out
    good = tmp_path / "good"
    kb_file(good)
    assert sales_replay.main(["cycle", "--version", str(good), "--out", str(ROOT)]) == 1
    assert "файл прогона — вне репозитория" in capsys.readouterr().out
    nowhere = tmp_path / "нет"
    assert sales_replay.main(["cycle", "--version", str(good), "--out", str(nowhere)]) == 1
    assert f"каталога для файла прогона нет: {nowhere}" in capsys.readouterr().out


@pytest.mark.usefixtures("offline")
def test_knowledge_base_file_inside_the_repository_is_not_read(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Каталог версии в копии репозитория с `kb.json` — факты компании в публичной истории."""
    folder = ROOT / "scripts" / "data" / "sales_replay"
    assert not (folder / replay.KB_FILE).exists()
    with pytest.raises(replay.SetError, match="база знаний версии не читается: файл"):
        replay.kb_facts(folder / "manifest.toml")


# --- база знаний версии ------------------------------------------------------------------------


def test_knowledge_base_file_gives_active_records_in_table_order(tmp_path: Path) -> None:
    found = replay.kb_facts(kb_file(tmp_path / "v"))

    # Порядок таблицы: вид — по перечислению видов, затем язык и заголовок.
    assert [(fact.id, fact.kind, fact.title) for fact in found] == [
        (1, KbKind.BRIEF, "Кто мы"),
        (2, KbKind.CASE, "Магазин"),
        (3, KbKind.PRICE_POLICY, "Цены"),
        (4, KbKind.FORBIDDEN, "Нельзя"),
    ]
    assert found[2].tags == ("аудит",)


async def test_a_narrowed_selection_from_a_knowledge_base_file_is_refused(
    session: AsyncSession, tmp_path: Path
) -> None:
    facts = replay_kb.substitute(replay.kb_facts(kb_file(tmp_path / "v")))

    assert len(await facts(session)) == 4
    with pytest.raises(replay_kb.KbVersionError, match="выборки нет"):
        await facts(session, kinds=[KbKind.CASE])


async def test_a_bad_moves_table_hands_the_knowledge_base_back_too(
    session: AsyncSession, model: Plug, tmp_path: Path
) -> None:
    found = model(LABELS)
    folder = tmp_path / "v"
    kb_file(folder)
    (folder / "moves.toml").write_text('version = "x"\n', "utf-8")
    table = kb.facts

    with pytest.raises(replay.SetError, match="таблица ходов версии"):
        await replay.run(session, Writer(), [], source="тест", prompts=replay.from_folder(folder))

    assert kb.facts is table
    assert found.calls == 0


# --- выгрузка базы ------------------------------------------------------------------------------


async def test_kb_export_writes_the_table_as_a_version_file(
    session: AsyncSession, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    await world(session)
    path = tmp_path / replay.KB_FILE

    assert await sales_replay.export_kb(session, path) == 0

    records = json.loads(path.read_text("utf-8"))
    assert {record["title"] for record in records} == {"Кто мы", "Цены", "Магазин", "Нельзя"}
    assert set(records[0]) == {"kind", "language", "title", "text", "tags", "active"}
    version = await kb.version(session)
    assert kb.version_of(replay.kb_facts(path)) == version
    assert f"База знаний выгружена: {path} — записей 4, версия {version}" in (
        capsys.readouterr().out
    )


async def test_kb_export_of_an_empty_table_is_refused(
    session: AsyncSession, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / replay.KB_FILE

    assert await sales_replay.export_kb(session, path) == 1

    assert "БАЗА ЗНАНИЙ ПУСТА" in capsys.readouterr().out
    assert not path.exists()


@pytest.mark.usefixtures("offline")
def test_kb_export_refuses_before_the_base_where_it_must_not_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    taken = tmp_path / "kb.json"
    taken.write_text("[]", "utf-8")
    assert sales_replay.main(["kb-export", str(taken)]) == 1
    assert "уже есть — выгрузка его не перепишет" in capsys.readouterr().out
    assert sales_replay.main(["kb-export", str(tmp_path / "нет" / "kb.json")]) == 1
    assert "каталога для файла базы нет" in capsys.readouterr().out
    inside = ROOT / "kb-export-test.json"
    assert sales_replay.main(["kb-export", str(inside)]) == 1
    assert "файл базы — вне репозитория" in capsys.readouterr().out
    assert not inside.exists()
