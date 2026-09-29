"""Применение дизайн-токенов шаблона к укладываемому тексту.

**Вёрстка не выбирает оформление — она применяет прочитанные правила.**
Цвет или гарнитура, которых в шаблоне нет, означали бы, что система сочинила
дизайн вместо того, чтобы его соблюсти.

Источник оформления — сам слот: он несёт стиль, разрешённый по цепочке
наследования, и это самое точное, что известно о конкретном месте макета.
Токены шаблона нужны там, где слот молчит, и как страховка: кегль,
отсутствующий в очищенной шкале, заменяется ближайшим из неё.

Слой детерминированный: моделей не вызывает.
"""

from __future__ import annotations

from dpd.models import DesignTokens, Slot, TextRun, TextStyle

TITLE_SCALE_POSITION = 0.75
"""Доля шкалы снизу, откуда берётся кегль заголовка, если слот его не знает."""


def style_for(slot: Slot, tokens: DesignTokens | None) -> TextStyle:
    """Оформление для содержимого слота: из слота, с опорой на токены."""
    base = slot.text_style
    font = _font_within_template(base.font if base else None, tokens)
    colour = base.color if base and base.color else _primary_colour(tokens)
    size = _snap_to_scale(base.size_pt if base else None, slot, tokens)

    return TextStyle(
        font=font,
        size_pt=size,
        color=colour,
        resolved_from=base.resolved_from if base else "designTokens",
        font_resolved_from=base.font_resolved_from if base else "designTokens",
    )


def apply(style: TextStyle, text: str) -> TextRun:
    """Собрать прогон с явным оформлением.

    Оформление задаётся явно, а не оставляется на усмотрение экспорта:
    иначе одно и то же содержимое выглядело бы по-разному в зависимости от
    того, попало оно в родной плейсхолдер или в созданную рамку.
    """
    return TextRun(text=text, font=style.font, size_pt=style.size_pt, color=style.color)


def _font_within_template(font: str | None, tokens: DesignTokens | None) -> str | None:
    """Гарнитура слота, если она есть в составе шаблона; иначе основная.

    Слот может унаследовать гарнитуру от мастера или темы, а тема врёт: во
    всех проверенных шаблонах она объявляет Arial, тогда как размечено Play.
    Сконструированный слот наследует именно оттуда — и получил бы Arial,
    которого на слайдах шаблона почти нет.

    Токены собраны частотным анализом фактической разметки и в этом споре
    правы: побеждает то, чем шаблон действительно набран.
    """
    families = [token.family for token in tokens.fonts] if tokens and tokens.fonts else []
    if font and (not families or font in families):
        return font
    return families[0] if families else font


def _primary_colour(tokens: DesignTokens | None) -> str | None:
    if not tokens or not tokens.colors:
        return None
    text_colour = next(
        (token for token in tokens.colors if token.role == "text.primary"), tokens.colors[0]
    )
    return text_colour.value


def _snap_to_scale(size: float | None, slot: Slot, tokens: DesignTokens | None) -> float | None:
    """Привести кегль к очищенной шкале шаблона.

    Кегль слота может оказаться среди исключённых: слот наследует свойства
    у разметки, а в ней есть следы автоподгонки. Ставить такой кегль нельзя —
    ради этого шкала и чистилась (T-16). Берётся ближайший из шкалы, а при
    полном её отсутствии значение остаётся как есть.
    """
    scale = tokens.type_scale.values if tokens else []
    if not scale:
        return size

    if size is not None and size in scale:
        return size
    if size is not None:
        return min(scale, key=lambda value: abs(value - size))

    # Слот не знает кегля: заголовку — крупный конец шкалы, тексту — средний.
    position = TITLE_SCALE_POSITION if slot.kind == "title" else 0.4
    return scale[min(int(len(scale) * position), len(scale) - 1)]
