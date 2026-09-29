"""T-06: минимальный парсер — холст, список макетов, слоты из плейсхолдеров.

Критерий приёмки задачи: на трёх калибровочных шаблонах возвращается верное
число макетов — 39, 15 и 30. Числа взяты из `docs/01-research/template-analysis.md`,
где они получены независимым инструментальным разбором.

Файлы шаблонов в репозиторий не включены: они принадлежат организаторам.
Поэтому тесты, которым они нужны, пропускаются, а не падают — иначе любой,
кто клонирует публичный репозиторий, увидит девять красных тестов и решит,
что проект сломан. Тесты, не требующие файлов, работают всегда.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dpd.models import TemplateSchema
from dpd.parsing import parse_template

CALIBRATION = Path(__file__).resolve().parents[1] / "assets" / "templates" / "calibration"

# (файл, число макетов по template-analysis.md)
TEMPLATES = [
    ("VK Tech шаблон.pptx", 39),
    ("VK_WorkSpace_Клиентская_конференция_Шаблон_03.pptx", 15),
    ("Шаблон презентации VK Education.pptx", 30),
]


def requires(name: str) -> Path:
    path = CALIBRATION / name
    if not path.exists():
        pytest.skip(f"нет калибровочного шаблона {name} — см. {CALIBRATION / 'README.md'}")
    return path


@pytest.mark.parametrize(("name", "expected"), TEMPLATES)
def test_layout_count_matches_the_template(name: str, expected: int) -> None:
    schema = parse_template(requires(name))
    assert len(schema.layouts) == expected


@pytest.mark.parametrize(("name", "_expected"), TEMPLATES)
def test_canvas_is_extracted(name: str, _expected: int) -> None:
    canvas = parse_template(requires(name)).canvas
    assert canvas.width_emu > 0
    assert canvas.height_emu > 0


@pytest.mark.parametrize(("name", "_expected"), TEMPLATES)
def test_result_is_a_valid_contract(name: str, _expected: int) -> None:
    """Парсер отдаёт контракт, а не словарь: дальше по пайплайну идёт он."""
    schema = parse_template(requires(name))
    assert isinstance(schema, TemplateSchema)
    assert TemplateSchema.model_validate_json(schema.model_dump_json(by_alias=True)) == schema


@pytest.mark.parametrize(("name", "_expected"), TEMPLATES)
def test_placeholder_slots_are_recognised(name: str, _expected: int) -> None:
    """Плейсхолдеры распознаются и остаются самым надёжным источником слотов.

    Проверка «все слоты — плейсхолдеры» была верна для минимального парсера
    и перестала быть верной с T-13: слоты выводятся ещё из фигур и из
    свободных областей. Здесь остаётся то, что верно всегда.
    """
    schema = parse_template(requires(name))
    slots = [slot for layout in schema.layouts for slot in layout.slots]
    assert slots, "не найдено ни одного слота"
    assert any(slot.origin == "placeholder" for slot in slots)
    assert {slot.origin for slot in slots} <= {"placeholder", "shape", "derived"}


@pytest.mark.parametrize(("name", "_expected"), TEMPLATES)
def test_geometry_is_normalised(name: str, _expected: int) -> None:
    """Координаты — доли холста: EMU живут только в `canvas`."""
    schema = parse_template(requires(name))
    for layout in schema.layouts:
        for slot in layout.slots:
            assert 0 <= slot.bounds.x <= 1
            assert 0 <= slot.bounds.y <= 1


@pytest.mark.parametrize(("name", "_expected"), TEMPLATES)
def test_layout_ids_are_unique(name: str, _expected: int) -> None:
    """Идентификатор макета адресует его при вёрстке — дубликаты недопустимы.

    Проверка небесполезна: имена макетов в шаблонах дублируются массово,
    поэтому идентификатор строится не из имени.
    """
    ids = [layout.id for layout in parse_template(requires(name)).layouts]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize(("name", "_expected"), TEMPLATES)
def test_source_records_file_hash(name: str, _expected: int) -> None:
    """По хешу работает кеш разбора (T-19) и привязка колоды к шаблону."""
    schema = parse_template(requires(name))
    assert schema.source.hash.startswith("sha256:")
    assert len(schema.source.hash) == len("sha256:") + 64


def test_missing_file_raises_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        parse_template(tmp_path / "нет-такого.pptx")
