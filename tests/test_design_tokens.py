"""T-12: извлечение дизайн-токенов частотным анализом разметки.

Критерий приёмки задачи: на трёх шаблонах основной гарнитурой определяется
Play, а не Arial из темы.

Это главный вывод фазы 2, выраженный в коде: **тема файла врёт**. Во всех
четырёх проверенных шаблонах `theme1.xml` расходится с фактическим
оформлением — объявляет Arial, тогда как размечено Play. Парсер, считающий
тему источником истины, объявил бы дизайн-системой Arial для всех трёх
калибровочных шаблонов и забраковал бы корректный Play.

Поэтому токены извлекаются частотным анализом фактической разметки, а тема
остаётся лишь одним из сигналов — и расхождение с ней фиксируется как
свойство шаблона, о котором пользователь должен знать.
"""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import pytest

from dpd.parsing import extract_design_tokens, parse_template

CALIBRATION = Path(__file__).resolve().parents[1] / "assets" / "templates" / "calibration"

TEMPLATES = [
    "VK Tech шаблон.pptx",
    "VK_WorkSpace_Клиентская_конференция_Шаблон_03.pptx",
    "Шаблон презентации VK Education.pptx",
]


def requires(name: str) -> Path:
    path = CALIBRATION / name
    if not path.exists():
        pytest.skip(f"нет калибровочного шаблона {name}")
    return path


@pytest.mark.parametrize("name", TEMPLATES)
def test_primary_font_comes_from_markup_not_theme(name: str) -> None:
    """Критерий приёмки: побеждает фактическая гарнитура, а не объявленная."""
    tokens = extract_design_tokens(requires(name))
    primary = tokens.fonts[0]
    assert primary.family == "Play", f"основной гарнитурой определён {primary.family}"


@pytest.mark.parametrize("name", TEMPLATES)
def test_conflict_with_theme_is_recorded(name: str) -> None:
    """Расхождение с темой — свойство шаблона, а не ошибка разбора.

    Пользователь должен знать, что файл заявляет одно, а размечен другим.
    """
    primary = extract_design_tokens(requires(name)).fonts[0]
    assert primary.conflicts_with, "расхождение с темой не зафиксировано"
    assert "Arial" in primary.conflicts_with.values()


@pytest.mark.parametrize("name", TEMPLATES)
def test_tokens_carry_source_and_confidence(name: str) -> None:
    """Каждый токен несёт источник и уверенность: аудит использует её как порог.

    Находка, опирающаяся на токен с низкой уверенностью, показывается как
    рекомендация, а не как нарушение.
    """
    tokens = extract_design_tokens(requires(name))
    for token in [*tokens.fonts, *tokens.colors]:
        assert token.sources, f"{token} без источника"
        assert 0 <= token.confidence <= 1
        assert token.occurrences > 0


@pytest.mark.parametrize("name", TEMPLATES)
def test_frequency_is_the_leading_source(name: str) -> None:
    assert "shapes.frequency" in extract_design_tokens(requires(name)).fonts[0].sources


@pytest.mark.parametrize("name", TEMPLATES)
def test_fonts_are_ordered_by_occurrences(name: str) -> None:
    counts = [font.occurrences for font in extract_design_tokens(requires(name)).fonts]
    assert counts == sorted(counts, reverse=True)


@pytest.mark.parametrize("name", TEMPLATES)
def test_colors_are_extracted(name: str) -> None:
    colors = extract_design_tokens(requires(name)).colors
    assert colors
    for color in colors:
        assert color.value.startswith("#")
        assert len(color.value) == 7


@pytest.mark.parametrize("name", TEMPLATES)
def test_type_scale_is_collected(name: str) -> None:
    """Шкала кеглей собирается по фактической разметке.

    Очистка от кеглей, порождённых автоподгонкой, — отдельная задача T-16;
    здесь фиксируется то, что реально встречается.
    """
    values = extract_design_tokens(requires(name)).type_scale.values
    assert values
    assert values == sorted(values)
    assert all(value > 0 for value in values)


@pytest.mark.parametrize("name", TEMPLATES)
def test_schema_carries_the_tokens(name: str) -> None:
    """Токены попадают в схему: вёрстка берёт правила только оттуда."""
    schema = parse_template(requires(name))
    assert schema.design_tokens is not None
    assert schema.design_tokens.fonts[0].family == "Play"


def test_brand_colour_is_separated_from_neutrals() -> None:
    """Насыщенный цвет отделяется от чёрного и серого.

    Без этого «основным цветом шаблона» всегда оказывался бы чёрный цвет
    текста, а брендовый — теряться в хвосте.
    """
    tokens = extract_design_tokens(requires(TEMPLATES[1]))
    roles = {color.role for color in tokens.colors}
    assert "brand.primary" in roles
    brand = next(color for color in tokens.colors if color.role == "brand.primary")
    assert brand.value == "#0077FF"


@pytest.mark.parametrize("name", TEMPLATES)
def test_confidence_follows_frequency(name: str) -> None:
    """Частый токен не может быть менее уверенным, чем редкий.

    Порядок ломался, пока подтверждение темой было слагаемым: гарнитура с
    одним вхождением, случайно совпавшая с темой, обгоняла гарнитуру с
    семьюдесятью. Тема — наименее надёжный источник, перевешивать факты
    разметки она не должна.
    """
    fonts = extract_design_tokens(requires(name)).fonts
    for earlier, later in pairwise(fonts):
        assert earlier.confidence >= later.confidence, (
            f"{earlier.family} ({earlier.occurrences} вхождений, {earlier.confidence}) "
            f"менее уверен, чем {later.family} ({later.occurrences}, {later.confidence})"
        )
