"""Рендер слайдов и подсветка находок на изображениях.

Путь рендера состоит из двух шагов: LibreOffice в headless-режиме
конвертирует `.pptx` в PDF, затем `pymupdf` рендерит страницы в PNG —
`pdftoppm` в системе отсутствует.

Узкое место — конвертация LibreOffice: 28 секунд на колоду из 29 слайдов.
Она плохо параллелится, процесс один на колоду, а не задача на слайд.

Обёртка скрывает LibreOffice за интерфейсом: смена рендерера не затрагивает
остальные слои.

Реализован рендер в изображения (T-09). Подсветка находок — задача T-35.
"""

from dpd.render.renderer import (
    DEFAULT_DPI,
    DEFAULT_TIMEOUT_SEC,
    convert_to_pdf,
    render_pdf_pages,
    render_slides,
    soffice_path,
)

__all__ = [
    "DEFAULT_DPI",
    "DEFAULT_TIMEOUT_SEC",
    "convert_to_pdf",
    "render_pdf_pages",
    "render_slides",
    "soffice_path",
]
