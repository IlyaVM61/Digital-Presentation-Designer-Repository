"""Рендер слайдов и подсветка находок на изображениях.

Путь рендера состоит из двух шагов: LibreOffice в headless-режиме
конвертирует `.pptx` в PDF, затем `pymupdf` рендерит страницы в PNG —
`pdftoppm` в системе отсутствует.

Узкое место — конвертация LibreOffice: 28 секунд на колоду из 29 слайдов.
Она плохо параллелится, процесс один на колоду, а не задача на слайд.

Обёртка скрывает LibreOffice за интерфейсом: смена рендерера не затрагивает
остальные слои.

Подсветка находок (T-35) наносится на копию рендера: исходный нужен для
проверки контраста на фоне-изображении, и рамка поверх фона испортила бы
измерение.
"""

from dpd.render.highlight import (
    SEVERITY_COLORS,
    Highlighted,
    area_label,
    highlight_slides,
)
from dpd.render.renderer import (
    DEFAULT_DPI,
    DEFAULT_TIMEOUT_SEC,
    convert_to_pdf,
    convert_to_pdfs,
    render_pdf_pages,
    render_slides,
    soffice_path,
)

__all__ = [
    "DEFAULT_DPI",
    "DEFAULT_TIMEOUT_SEC",
    "SEVERITY_COLORS",
    "Highlighted",
    "area_label",
    "convert_to_pdf",
    "convert_to_pdfs",
    "highlight_slides",
    "render_pdf_pages",
    "render_slides",
    "soffice_path",
]
