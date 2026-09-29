"""T-03: `scripts/requirements.txt` закрепляет состав окружения.

Критерий приёмки задачи: `pip install -r` на чистом окружении воспроизводит
состав. Проверить это буквально — значит создать чистое окружение прямо в
тесте, а это минуты сетевых загрузок на каждый прогон. Поэтому проверяются
свойства файла, из которых воспроизводимость следует: каждая строка закреплена
точной версией, версии совпадают с фактически установленными, и в файле нет
пропусков относительно окружения.
"""

from __future__ import annotations

import re
from importlib.metadata import distributions
from pathlib import Path

import pytest

REQUIREMENTS = Path(__file__).resolve().parents[1] / "scripts" / "requirements.txt"

# Пакеты самой упаковочной машинерии: pip их не выводит в `pip freeze`
# и закреплять их в requirements не нужно.
PACKAGING_TOOLING = {"pip", "setuptools", "wheel", "pkg-resources", "distribute"}

# Сам проект ставится в окружение как editable (`pip install -e .`, задача
# T-04) и зависимостью себе не является: закрепить его в requirements.txt
# означало бы требовать установки проекта до установки проекта.
SELF_DISTRIBUTION = {"dpd"}

EXCLUDED_FROM_PINS = PACKAGING_TOOLING | SELF_DISTRIBUTION

# Библиотеки, назначенные слоям в `docs/04-architecture/project-architecture.md`.
# Их отсутствие означает, что окружение не соответствует архитектуре.
ARCHITECTURE_LIBRARIES = {
    "python-pptx",
    "lxml",
    "pydantic",
    "httpx",
    "pillow",
    "numpy",
    "pymupdf",
    "jinja2",
    "langdetect",
    "streamlit",
    "pytest",
    "ruff",
}

PIN = re.compile(r"^(?P<name>[A-Za-z0-9._-]+)==(?P<version>[A-Za-z0-9._+!-]+)$")


def normalize(name: str) -> str:
    """Каноническая форма имени пакета по PEP 503."""
    return re.sub(r"[-_.]+", "-", name).lower()


def read_pins() -> dict[str, str]:
    """Закреплённые версии из requirements.txt, ключ — каноническое имя."""
    pins: dict[str, str] = {}
    for number, raw in enumerate(REQUIREMENTS.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = PIN.match(line)
        assert match, f"строка {number} не закреплена точной версией: {line!r}"
        name = normalize(match["name"])
        assert name not in pins, f"строка {number}: пакет {name} указан повторно"
        pins[name] = match["version"]
    return pins


def installed() -> dict[str, str]:
    """Фактически установленные пакеты текущего интерпретатора."""
    return {
        normalize(dist.metadata["Name"]): dist.version
        for dist in distributions()
        if dist.metadata["Name"] and normalize(dist.metadata["Name"]) not in EXCLUDED_FROM_PINS
    }


def test_requirements_file_is_not_empty() -> None:
    assert REQUIREMENTS.exists(), f"нет файла {REQUIREMENTS}"
    assert REQUIREMENTS.read_text(encoding="utf-8").strip(), "файл пуст"


def test_every_line_is_pinned_to_exact_version() -> None:
    pins = read_pins()
    assert pins, "в файле нет ни одного закрепления"


def test_pinned_versions_match_the_environment() -> None:
    pins, env = read_pins(), installed()
    mismatched = {
        name: (version, env[name])
        for name, version in pins.items()
        if name in env and env[name] != version
    }
    assert not mismatched, f"версии расходятся с окружением (файл, окружение): {mismatched}"
    missing = sorted(set(pins) - set(env))
    assert not missing, f"закреплены пакеты, которых нет в окружении: {missing}"


def test_requirements_cover_the_whole_environment() -> None:
    """Без этого `pip install -r` даст состав, зависящий от разрешения зависимостей."""
    uncovered = sorted(set(installed()) - set(read_pins()))
    assert not uncovered, f"пакеты окружения не закреплены: {uncovered}"


@pytest.mark.parametrize("library", sorted(ARCHITECTURE_LIBRARIES))
def test_architecture_libraries_are_present(library: str) -> None:
    assert normalize(library) in read_pins(), f"{library} назначена слою архитектурой, но не закреплена"
