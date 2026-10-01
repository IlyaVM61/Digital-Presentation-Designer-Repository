"""T-14: классификация макетов по структуре, поле `family`.

Критерий приёмки задачи: на VK WorkSpace 11 макетов «Титульный слайд»
получают разные `family`.

Имена макетов ненадёжны. В VK WorkSpace 11 из 15 называются одинаково —
типичный след работы через «Дублировать макет»: имя наследуется от
исходного и назначения не отражает. Определять тип макета по имени значило
бы считать одиннадцать разных макетов одним.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dpd.parsing import parse_template

CALIBRATION = Path(__file__).resolve().parents[1] / "assets" / "templates" / "calibration"

WORKSPACE = "VK_WorkSpace_Клиентская_конференция_Шаблон_03.pptx"
VK_TECH = "VK Tech шаблон.pptx"
EDUCATION = "Шаблон презентации VK Education.pptx"
ALL = [WORKSPACE, VK_TECH, EDUCATION]


def requires(name: str) -> Path:
    path = CALIBRATION / name
    if not path.exists():
        pytest.skip(f"нет калибровочного шаблона {name}")
    return path


def test_identically_named_layouts_get_different_families() -> None:
    """Критерий приёмки: одно имя на одиннадцать макетов — не один тип."""
    layouts = parse_template(requires(WORKSPACE)).layouts
    same_name = [layout for layout in layouts if "Титульный" in layout.name]
    assert len(same_name) >= 10, "в шаблоне не нашлось массового дублирования имён"

    families = {layout.family for layout in same_name}
    assert len(families) > 1, f"все {len(same_name)} макетов отнесены к одному типу: {families}"


@pytest.mark.parametrize("name", ALL)
def test_every_layout_is_classified(name: str) -> None:
    for layout in parse_template(requires(name)).layouts:
        assert layout.family, f"макет {layout.name} остался без типа"


@pytest.mark.parametrize("name", ALL)
def test_family_source_is_structure_not_name(name: str) -> None:
    """Тип выводится из геометрии слотов, а не из имени макета."""
    for layout in parse_template(requires(name)).layouts:
        assert layout.family_source == "structure"


@pytest.mark.parametrize("name", ALL)
def test_layout_without_content_slots_is_not_content(name: str) -> None:
    """Макет без места под содержимое не может быть контентным.

    Вёрстка выбирает макет под тип слайда; отнести к `content` макет, куда
    нечего положить, значило бы гарантировать пустой слайд.
    """
    for layout in parse_template(requires(name)).layouts:
        # Номер слайда и колонтитулы (`other`) — не место под содержимое (T-57).
        has_content = any(slot.kind == "body" for slot in layout.slots)
        if not has_content:
            assert layout.family in {"section", "blank"}


@pytest.mark.parametrize("name", ALL)
def test_layouts_with_several_content_slots_are_split(name: str) -> None:
    """Без слота заголовка макет запасной (`blank`), даже с местами под тело (T-57)."""
    for layout in parse_template(requires(name)).layouts:
        content = [slot for slot in layout.slots if slot.kind == "body"]
        titled = any(slot.kind == "title" for slot in layout.slots)
        if len(content) >= 2:
            assert layout.family == ("split" if titled else "blank")


def test_classification_is_deterministic() -> None:
    first = [layout.family for layout in parse_template(requires(WORKSPACE)).layouts]
    second = [layout.family for layout in parse_template(requires(WORKSPACE)).layouts]
    assert first == second


def test_template_offers_more_than_one_family() -> None:
    """Шаблон, где все макеты одного типа, вёрстке бесполезен."""
    for name in ALL:
        families = {layout.family for layout in parse_template(requires(name)).layouts}
        assert len(families) > 1, f"{name}: все макеты одного типа"
