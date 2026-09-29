"""T-16: очистка типографической шкалы от автоподгонки.

Критерий приёмки задачи: дробные кегли VK Tech (8.12, 6.75, 14.06) попадают
в `excluded`.

PowerPoint при автоподгонке текста (`normAutofit`) умножает кегль на
дробный коэффициент. Получившиеся значения — след того, что текст не
поместился, а не решение дизайнера. Принять их за шкалу шаблона значит
разрешить вёрстке ставить кегль 6,75 как «родной для шаблона» и получить
нечитаемый слайд, формально соответствующий правилам.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dpd.parsing import parse_template

CALIBRATION = Path(__file__).resolve().parents[1] / "assets" / "templates" / "calibration"

VK_TECH = "VK Tech шаблон.pptx"
WORKSPACE = "VK_WorkSpace_Клиентская_конференция_Шаблон_03.pptx"
EDUCATION = "Шаблон презентации VK Education.pptx"
ALL = [VK_TECH, WORKSPACE, EDUCATION]


def requires(name: str) -> Path:
    path = CALIBRATION / name
    if not path.exists():
        pytest.skip(f"нет калибровочного шаблона {name}")
    return path


def scale(name: str):
    return parse_template(requires(name)).design_tokens.type_scale


@pytest.mark.parametrize("value", [8.12, 6.75, 14.06])
def test_autofit_sizes_are_excluded(value: float) -> None:
    """Критерий приёмки, названными значениями."""
    excluded = scale(VK_TECH).excluded
    assert value in excluded, f"{value} осталось в шкале; исключено: {sorted(excluded)[:8]}"


def test_exclusion_reason_is_recorded() -> None:
    """Пользователь должен понимать, почему значения выброшены."""
    assert scale(VK_TECH).exclusion_reason == "normAutofit"


@pytest.mark.parametrize("name", ALL)
def test_kept_sizes_are_plausible_design_choices(name: str) -> None:
    """В шкале остаются кегли, которые дизайнер мог выбрать осознанно."""
    for value in scale(name).values:
        assert abs(value * 2 - round(value * 2)) < 1e-9, f"{value} не кратен половине пункта"


@pytest.mark.parametrize("name", [WORKSPACE, EDUCATION])
def test_clean_templates_lose_nothing(name: str) -> None:
    """Шаблон без автоподгонки не должен терять ни одного кегля.

    Проверка против чрезмерно жадного правила: если очистка выбрасывает
    что-то из чистой шкалы, она выбрасывает лишнее.
    """
    assert scale(name).excluded == []
    assert scale(name).exclusion_reason is None


def test_excluded_and_kept_do_not_overlap() -> None:
    cleaned = scale(VK_TECH)
    assert not set(cleaned.values) & set(cleaned.excluded)


def test_half_point_sizes_survive() -> None:
    """10,5 пункта — законный кегль, а не след автоподгонки."""
    assert 10.5 in scale(VK_TECH).values


def test_scale_is_not_emptied() -> None:
    """Очистка не должна оставить вёрстку без единого кегля."""
    cleaned = scale(VK_TECH)
    assert len(cleaned.values) >= 5
    assert cleaned.values == sorted(cleaned.values)
