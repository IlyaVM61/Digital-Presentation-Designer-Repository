"""Общая настройка тестов: временные файлы — в каталоге результатов (T-63).

Тесты копируют шаблоны и выгружают колоды по 16–20 МБ во `tmp_path`, а pytest
держит каталоги трёх последних прогонов во временном каталоге системы — на
диске C, где места почти нет (вопрос T26). Корень этих каталогов переносится
в `DPD_OUTPUT_DIR`, как и всё тяжёлое в проекте; нумерация прогонов и чистка
старых остаются за pytest. Если каталог результатов недоступен — например,
на машине без диска D, — тесты пишут туда, куда писали прежде.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def pytest_configure(config: pytest.Config) -> None:
    if config.option.basetemp or os.environ.get("PYTEST_DEBUG_TEMPROOT"):
        return  # корень задан явно — ключом `--basetemp` или переменной
    root = Path(os.environ.get("DPD_OUTPUT_DIR", "D:/dpd-out")) / "tests" / "tmp"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    # Документированная переменная pytest: корень для `tmp_path`.
    os.environ["PYTEST_DEBUG_TEMPROOT"] = str(root)
