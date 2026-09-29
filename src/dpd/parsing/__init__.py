"""Слой 1: разбор OOXML — макеты, слоты, дизайн-токены, наследование.

Детерминированный слой, моделей не вызывает.

Три факта из `docs/01-research/template-analysis.md` определяют устройство
слоя: тема файла расходится с фактическим оформлением во всех проверенных
шаблонах, поэтому токены извлекаются частотным анализом разметки, а тема —
лишь один из сигналов; слот не равен плейсхолдеру, поэтому распознавание
слотов из обычных фигур — основной режим, а не фолбэк; до 59% текстовых
прогонов не несут явного кегля и шрифта, поэтому цепочка
прогон → абзац → макет → мастер → тема разрешается обязательно.

Реализован минимальный разбор (T-06): холст, макеты, слоты из плейсхолдеров.
"""

from dpd.parsing.inheritance import StyleResolver
from dpd.parsing.template_parser import PARSER_VERSION, layout_id, parse_template
from dpd.parsing.tokens import extract_design_tokens

__all__ = [
    "PARSER_VERSION",
    "StyleResolver",
    "extract_design_tokens",
    "layout_id",
    "parse_template",
]
