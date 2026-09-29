"""T-04: каркас пакета `src/dpd/` повторяет слои архитектуры.

Критерий приёмки задачи: пакет импортируется, каталоги соответствуют слоям.
Соответствие проверяется в обе стороны — не только «каждый слой на месте»,
но и «лишних каталогов нет»: каркас, который расходится с
`project-architecture.md`, перестаёт быть картой и начинает вводить в
заблуждение.

Отдельно проверяется, что каждый модуль каркаса объясняет своё назначение.
Проект уже обжигался на пустых файлах: десять пустых команд в
`.claude/commands/` молча ломали фазы конвейера, пока это не обнаружили.
Пустой `__init__.py` — та же ловушка: каталог есть, смысла в нём нет.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
PACKAGE_ROOT = SRC / "dpd"

# Слои пайплайна из `docs/04-architecture/project-architecture.md`.
# Ключ — импортируемое имя, значение — каталог относительно `src/dpd/`.
LAYER_PACKAGES = {
    "dpd": "",
    "dpd.models": "models",
    "dpd.parsing": "parsing",
    "dpd.generation": "generation",
    "dpd.layout": "layout",
    "dpd.audit": "audit",
    "dpd.audit.checks": "audit/checks",
    "dpd.export": "export",
    "dpd.llm": "llm",
    "dpd.render": "render",
    "dpd.config": "config",
}

# Модули, названные архитектурой поимённо: подслои аудита и оркестратор.
NAMED_MODULES = {
    "dpd.orchestrator",
    "dpd.audit.deterministic",
    "dpd.audit.textual",
    "dpd.audit.visual",
    "dpd.audit.fixer",
}

EXPECTED_DIRECTORIES = {path for path in LAYER_PACKAGES.values() if path}


def test_package_is_importable() -> None:
    assert importlib.import_module("dpd") is not None


@pytest.mark.parametrize("module", sorted(LAYER_PACKAGES))
def test_every_layer_is_importable(module: str) -> None:
    assert importlib.import_module(module) is not None


@pytest.mark.parametrize("module", sorted(NAMED_MODULES))
def test_named_modules_are_importable(module: str) -> None:
    assert importlib.import_module(module) is not None


@pytest.mark.parametrize("module", sorted(set(LAYER_PACKAGES) | NAMED_MODULES))
def test_every_module_states_its_purpose(module: str) -> None:
    doc = importlib.import_module(module).__doc__
    assert doc and doc.strip(), f"{module} не объясняет своё назначение"


def test_directories_match_the_architecture() -> None:
    """Соответствие в обе стороны: ни пропущенных слоёв, ни лишних каталогов."""
    actual = {
        str(path.relative_to(PACKAGE_ROOT)).replace("\\", "/")
        for path in PACKAGE_ROOT.rglob("*")
        if path.is_dir() and "__pycache__" not in path.parts
    }
    assert actual == EXPECTED_DIRECTORIES, (
        f"пропущены: {sorted(EXPECTED_DIRECTORIES - actual)}; "
        f"лишние: {sorted(actual - EXPECTED_DIRECTORIES)}"
    )


def test_package_lives_under_src_layout() -> None:
    """src-layout: пакет не импортируется случайно из рабочего каталога."""
    assert PACKAGE_ROOT.is_dir(), f"нет каталога {PACKAGE_ROOT}"
    assert not (SRC.parent / "dpd").exists(), "пакет продублирован в корне проекта"
