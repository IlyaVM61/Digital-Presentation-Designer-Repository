"""T-63: копии загруженных шаблонов не копятся на системном диске.

Страница кладёт загруженный шаблон в файл: разбору и экспорту нужен путь, а
колода собирается на самом файле шаблона. Прежде копия ложилась во временный
каталог системы под ключом `hash()` от байтов — а встроенный `hash()` в
Python меняется от процесса к процессу. Каждый запуск страницы и каждый тест
через `AppTest` создавали новую папку: 99 копий, 1,3 ГБ за два дня, диск C
заполнен до 28 МБ, и полный прогон тестов упал на «No space left on device»
(вопрос T26).

Критерий приёмки: ключ копии устойчив, копия лежит в каталоге результатов, а
не во временном каталоге системы, повторная загрузка того же шаблона не
создаёт новой копии, полный прогон тестов не оставляет копий на C.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from dpd.orchestrator import keep_template

DATA = b"PK\x03\x04 template bytes"


def files(folder: Path) -> list[Path]:
    return sorted(path for path in folder.rglob("*") if path.is_file())


def test_same_template_is_kept_once(tmp_path: Path) -> None:
    """Повторная загрузка того же файла берёт прежнюю копию, а не делает новую."""
    first = keep_template(DATA, "Шаблон.pptx", tmp_path)
    second = keep_template(DATA, "Шаблон.pptx", tmp_path)

    assert first == second
    assert first.read_bytes() == DATA
    assert files(tmp_path) == [first], "повторная загрузка оставила вторую копию"


def test_copy_key_survives_a_new_process(tmp_path: Path) -> None:
    """Ключ копии не зависит от процесса.

    Ровно здесь сломался прежний ключ: `hash()` от байтов солится заново в
    каждом процессе (`PYTHONHASHSEED`), и перезапуск страницы давал новую
    папку для того же шаблона.
    """
    script = (
        "import sys; from pathlib import Path; "
        "from dpd.orchestrator import keep_template; "
        "print(keep_template(sys.stdin.buffer.read(), 'deck.pptx', Path(sys.argv[1])))"
    )
    paths = set()
    for seed in ("1", "2"):
        done = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path)],
            input=DATA,
            capture_output=True,
            env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONIOENCODING": "utf-8"},
            check=True,
        )
        paths.add(done.stdout.decode("utf-8").strip())

    assert len(paths) == 1, f"тот же шаблон в двух процессах лёг в разные папки: {paths}"
    assert len(files(tmp_path)) == 1


def test_different_templates_get_different_copies(tmp_path: Path) -> None:
    """Ключ — содержимое: другой файл под тем же именем не подменяет первый."""
    first = keep_template(DATA, "Шаблон.pptx", tmp_path)
    second = keep_template(DATA + b"!", "Шаблон.pptx", tmp_path)

    assert first != second
    assert first.read_bytes() == DATA
    assert second.read_bytes() == DATA + b"!"


def test_copy_keeps_the_users_file_name(tmp_path: Path) -> None:
    """Имя файла сохраняется: от него зависят имена готовых колод, и
    пользователь должен узнать в них свой шаблон. Путь из имени отбрасывается —
    копия не выходит из своей папки."""
    path = keep_template(DATA, "../../Квартальный отчёт.pptx", tmp_path)

    assert path.name == "Квартальный отчёт.pptx"
    assert tmp_path.resolve() in path.resolve().parents


def test_tests_keep_their_files_off_the_system_temp(tmp_path: Path) -> None:
    """Файлы тестов живут в каталоге результатов, а не во временном каталоге
    системы: прогон копирует шаблоны и выгружает колоды по 16–20 МБ, а pytest
    хранит каталоги трёх последних прогонов."""
    system = Path(tempfile.gettempdir()).resolve()

    assert system not in tmp_path.resolve().parents, f"tmp_path на системном диске: {tmp_path}"
