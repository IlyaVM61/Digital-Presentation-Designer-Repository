"""Проверки целостности колоды.

Выполняются на готовом файле `.pptx`: до экспорта проверять нечего.

Реализована `integrity.raster_slide` (T-10). Остальные проверки категории —
задача T-31.
"""

from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from dpd.models.audit import Finding

CHECK_ID = "integrity.raster_slide"

DEFAULT_COVERAGE_THRESHOLD = 0.95
"""Доля холста, при которой изображение считается подменой слайда.

Порог по умолчанию; с каркасом проверок (T-27) он уходит в `configs/audit.yaml`
вместе с остальными. Выражен долей, а не дюймами, потому что холсты шаблонов
различаются, и абсолютный порог, откалиброванный на одном, был бы неверен на
другом."""


def check_raster_slide(
    pptx_path: str | Path,
    coverage_threshold: float = DEFAULT_COVERAGE_THRESHOLD,
) -> list[Finding]:
    """Найти слайды, выгруженные растровым изображением вместо объектов.

    Прямое требование ТЗ (FR-38): такой слайд не засчитывается.

    Срабатывает при совпадении двух условий: на слайде нет ни одного непустого
    текста и изображение занимает почти весь холст. Одного условия мало ни в
    ту, ни в другую сторону. Изображение во весь холст с текстом поверх —
    обычный слайд на фоне-картинке, а в одном из калибровочных шаблонов такой
    фон у 62% макетов; слайд же без текста и без большой картинки просто пуст,
    и это другая находка — `integrity.empty_slide` — с другой исправимостью.
    """
    pptx_path = Path(pptx_path)
    if not pptx_path.is_file():
        raise FileNotFoundError(f"файл не найден: {pptx_path}")

    presentation = Presentation(str(pptx_path))
    canvas_area = presentation.slide_width * presentation.slide_height

    findings: list[Finding] = []
    for number, slide in enumerate(presentation.slides, start=1):
        shapes = list(_walk(slide.shapes))
        if any(_has_text(shape) for shape in shapes):
            continue

        coverage = max((_coverage(shape, canvas_area) for shape in shapes if _is_picture(shape)), default=0.0)
        if coverage >= coverage_threshold:
            findings.append(
                Finding(
                    check_id=CHECK_ID,
                    severity="critical",
                    fixability="none",
                    slide_number=number,
                    message=(
                        f"Слайд {number} выгружен изображением на {coverage:.0%} холста "
                        f"и не содержит текста: такой слайд не засчитывается по ТЗ."
                    ),
                )
            )
    return findings


def _walk(shapes):
    """Обойти фигуры, заглядывая внутрь групп: текст может лежать и там."""
    for shape in shapes:
        yield shape
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _walk(shape.shapes)


def _has_text(shape) -> bool:
    return bool(shape.has_text_frame and shape.text_frame.text.strip())


def _is_picture(shape) -> bool:
    return shape.shape_type in (MSO_SHAPE_TYPE.PICTURE, MSO_SHAPE_TYPE.LINKED_PICTURE)


def _coverage(shape, canvas_area: int) -> float:
    if not shape.width or not shape.height or not canvas_area:
        return 0.0
    return min(shape.width * shape.height / canvas_area, 1.0)
