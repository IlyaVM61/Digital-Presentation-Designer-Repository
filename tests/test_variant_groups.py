"""T-18: группировка макетов в `variantGroup` по цветовой схеме.

Критерий приёмки задачи: в VK Tech семейство «Контент» делится на 7 светлых
и 7 тёмных.

Цветовые вариации одного макета — готовый механизм визуального различения
вариантов колоды, не выходящий за правила шаблона: используются родные
макеты. Но опираться на него как на единственную ось нельзя — в VK WorkSpace
вариаций нет вовсе (ADR-0004).
"""

from __future__ import annotations

from collections import Counter
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


def test_content_family_splits_into_light_and_dark() -> None:
    """Критерий приёмки: 7 светлых и 7 тёмных."""
    layouts = parse_template(requires(VK_TECH)).layouts
    content = [layout for layout in layouts if "Контент" in layout.name]
    schemes = Counter(layout.color_scheme for layout in content)
    assert schemes["light"] == 7, f"светлых {schemes['light']}, всего {len(content)}"
    assert schemes["dark"] == 7, f"тёмных {schemes['dark']}, всего {len(content)}"


def test_scheme_is_read_from_text_colour_when_background_is_an_image() -> None:
    """Под картинкой яркость фона неизвестна, но цвет текста её выдаёт.

    Светлый текст означает, что дизайнер рассчитывал на тёмный фон. Это
    единственный доступный статический признак: все 14 макетов семейства
    стоят на изображении.
    """
    layouts = parse_template(requires(VK_TECH)).layouts
    on_image = [layout for layout in layouts if layout.background.kind == "image"]
    assert on_image
    assert any(layout.color_scheme == "dark" for layout in on_image)


@pytest.mark.parametrize("name", ALL)
def test_every_layout_has_a_scheme(name: str) -> None:
    for layout in parse_template(requires(name)).layouts:
        assert layout.color_scheme in {"light", "dark", "unknown"}


def test_variant_groups_link_siblings() -> None:
    """Макеты, различающиеся только схемой, знают друг о друге."""
    layouts = parse_template(requires(VK_TECH)).layouts
    grouped = [layout for layout in layouts if layout.variant_group is not None]
    assert grouped, "ни одной группы вариаций не найдено"
    by_id = {layout.id: layout for layout in layouts}
    for layout in grouped:
        for sibling in layout.variant_group.siblings:
            assert sibling in by_id, f"ссылка на несуществующий макет {sibling}"
            assert sibling != layout.id


def test_siblings_differ_by_scheme_not_by_family() -> None:
    layouts = parse_template(requires(VK_TECH)).layouts
    by_id = {layout.id: layout for layout in layouts}
    for layout in layouts:
        if layout.variant_group is None:
            continue
        for sibling in layout.variant_group.siblings:
            assert by_id[sibling].family == layout.family
            assert by_id[sibling].color_scheme != layout.color_scheme


def test_template_without_variations_gets_no_groups() -> None:
    """Предсказание ADR-0004: в VK WorkSpace цветовых вариаций нет.

    Если проверка однажды упадёт — значит предсказание было неверным, и
    стратегия вариантов заслуживает пересмотра, а не тест правки.
    """
    layouts = parse_template(requires(WORKSPACE)).layouts
    schemes = {layout.color_scheme for layout in layouts}
    assert len(schemes) == 1, f"в шаблоне нашлись разные схемы: {schemes}"


def test_grouping_is_deterministic() -> None:
    first = [layout.color_scheme for layout in parse_template(requires(VK_TECH)).layouts]
    second = [layout.color_scheme for layout in parse_template(requires(VK_TECH)).layouts]
    assert first == second
