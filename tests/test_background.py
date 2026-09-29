"""T-17: определение вида фона, поле `contrastComputable`.

Критерий приёмки задачи: в VK Tech 24 макета помечаются как
фон-изображение.

Из этого следует, что проверка контраста 4.5:1 из Приложения 1 ТЗ не может
быть полностью детерминированной. Когда фоном служит изображение, цвет под
текстом неизвестен до рендера: коды цветов молчат. Поэтому проверка
гибридная — класс `file` там, где фон сплошной, и класс `rendered` там, где
он картинка (решение фазы 6, вопрос T8).
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


def test_image_backgrounds_are_counted() -> None:
    """Критерий приёмки: 24 макета VK Tech стоят на изображении."""
    layouts = parse_template(requires(VK_TECH)).layouts
    on_image = [layout for layout in layouts if layout.background.kind == "image"]
    assert len(on_image) == 24, f"найдено {len(on_image)} из {len(layouts)}"


def test_image_background_makes_contrast_incomputable() -> None:
    """Под картинкой цвет неизвестен до рендера — коды цветов молчат."""
    for layout in parse_template(requires(VK_TECH)).layouts:
        if layout.background.kind == "image":
            assert layout.background.contrast_computable is False


@pytest.mark.parametrize("name", ALL)
def test_solid_background_keeps_contrast_computable(name: str) -> None:
    """Сплошной фон даёт цвет, по которому контраст считается алгоритмом."""
    for layout in parse_template(requires(name)).layouts:
        if layout.background.kind == "solid":
            assert layout.background.contrast_computable is True
            assert layout.background.value is not None


@pytest.mark.parametrize("name", ALL)
def test_every_layout_has_a_background(name: str) -> None:
    for layout in parse_template(requires(name)).layouts:
        assert layout.background.kind in {"solid", "gradient", "image", "inherited"}


@pytest.mark.parametrize("name", ALL)
def test_background_source_is_recorded(name: str) -> None:
    """Откуда взят фон — нужно для отчёта и для отладки на чужом шаблоне."""
    for layout in parse_template(requires(name)).layouts:
        assert layout.background.source


def test_templates_differ_in_background_kinds() -> None:
    """Шаблоны различаются по природе фона — на этом стоит гибридность проверки."""
    kinds = {
        name: {layout.background.kind for layout in parse_template(requires(name)).layouts}
        for name in ALL
    }
    assert kinds[VK_TECH] != kinds[EDUCATION]


def test_detection_is_deterministic() -> None:
    first = [layout.background.kind for layout in parse_template(requires(VK_TECH)).layouts]
    second = [layout.background.kind for layout in parse_template(requires(VK_TECH)).layouts]
    assert first == second
