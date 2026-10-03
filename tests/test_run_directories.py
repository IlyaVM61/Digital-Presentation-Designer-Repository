"""T-74: два прогона на общем сервере не делят один каталог.

Страница называла каталог прогона секундой запуска. На машине разработчика
это безопасно, а на сервере для экспертов два сеанса, нажавшие «Собрать» в
одну секунду, писали бы колоды, превью и профиль LibreOffice в одно место:
файлы одного перезаписали бы файлы другого.

Критерий: каталоги двух прогонов, начатых в одну секунду, различаются, а имя
по-прежнему начинается с даты и времени — по нему прогоны сортируются.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from dpd import orchestrator
from dpd.orchestrator import run_directory


def test_runs_started_in_the_same_second_get_their_own_folders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(orchestrator.time, "strftime", lambda *_: "20261003-221500")

    first = run_directory(tmp_path)
    second = run_directory(tmp_path)

    assert first != second, "два прогона одной секунды получили один каталог"
    assert first.parent == second.parent == tmp_path


def test_run_folder_name_starts_with_the_time(tmp_path: Path) -> None:
    folder = run_directory(tmp_path)

    assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{6}", folder.name), folder.name


def test_page_names_run_folders_through_run_directory() -> None:
    """Страница не собирает имя каталога сама — иначе дефект вернётся."""
    source = (Path(__file__).parents[1] / "ui" / "app.py").read_text(encoding="utf-8")

    assert "run_directory(" in source
    assert 'strftime("%Y%m%d-%H%M%S")' not in source
