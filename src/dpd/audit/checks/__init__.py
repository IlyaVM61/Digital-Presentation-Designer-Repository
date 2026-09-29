"""Проверки как самостоятельные единицы.

Каждая проверка несёт класс (`file`, `rendered`, `model`), категорию,
критичность и исправимость. Добавление проверки не требует правок в других
слоях — это точка расширения, заявленная архитектурой.

Класс `rendered` введён решением фазы 6: контраст на фоне-изображении
считается по пикселям, а не по кодам цветов, но алгоритмом, а не моделью.
В 62% макетов одного из калибровочных шаблонов фоном служит изображение,
поэтому проверка 4.5:1 гибридная.

Реализованы `integrity.raster_slide` (T-10) и `variants.low_distinction`
(T-26). Общий каркас проверки как единицы — задача T-27; до неё проверки
вызываются напрямую.
"""

from dpd.audit.checks.integrity import (
    DEFAULT_COVERAGE_THRESHOLD,
    check_raster_slide,
)
from dpd.audit.checks.variants import check_variant_distinction

__all__ = [
    "DEFAULT_COVERAGE_THRESHOLD",
    "check_raster_slide",
    "check_variant_distinction",
]
