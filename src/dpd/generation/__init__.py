"""Слой 2: структура колоды, содержание слайдов, план визуализаций.

Единственный слой, обращающийся к модели за смыслом. Все ответы валидируются
по JSON Schema; невалидный ответ вызывает повторный запрос.

Слой не принимает решений о форме: ни координат, ни цветов, ни кеглей в его
выходе нет. Результат — `PresentationStructure`.

Два пути. Детерминированный: план, написанный человеком, переводится в
`PresentationStructure` (T-38) — это не генерация, смысл приносит
пользователь. Генерация моделью: структура колоды по брифу и контент-пакету
(T-49), затем содержание слайдов со ссылками на источник (T-50).
"""

from dpd.generation.content import (
    CONTENT_STAGE,
    fact_sections,
    generate_content,
    generate_presentation,
    numbers_in,
)
from dpd.generation.content_pack import ContentPack, load_content_pack
from dpd.generation.outline import structure_from_outline
from dpd.generation.structure import (
    DEFAULT_SLIDE_RANGE,
    STRUCTURE_STAGE,
    generate_structure,
)

__all__ = [
    "CONTENT_STAGE",
    "DEFAULT_SLIDE_RANGE",
    "STRUCTURE_STAGE",
    "ContentPack",
    "fact_sections",
    "generate_content",
    "generate_presentation",
    "generate_structure",
    "load_content_pack",
    "numbers_in",
    "structure_from_outline",
]
