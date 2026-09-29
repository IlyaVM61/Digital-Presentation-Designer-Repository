"""Сборка диаграмм по дизайн-токенам шаблона.

**Диаграмм в шаблонах нет ни одной** — 138 слайдов-примеров, ноль диаграмм,
ноль SmartArt. Копировать оформление не из чего, и оно целиком синтезируется
из токенов: цвета рядов берутся из палитры, гарнитура и кегль — те же, что у
текста.

Диаграмма в чужих цветах выдаёт, что слайд собран не по шаблону, даже если
всё остальное безупречно.

Подписи осей и легенда — требование проверки `integrity.chart_no_labels`.
Правило наше, эталона нет, но диаграмма без единиц измерения не сообщает
ничего.

Слой детерминированный: моделей не вызывает.
"""

from __future__ import annotations

from dpd.models import ChartSpec, DesignTokens, RenderedChart, Slot

CHART_SIZE_STEPS_DOWN = 2
"""На сколько ступеней шкалы кегль подписей мельче основного текста."""

NEUTRAL_SPREAD = 30
"""Разброс каналов RGB, ниже которого цвет считается нейтральным.

Серый и чёрный годятся для текста, но ряды, окрашенные в них, сливаются с
подписями и сеткой."""


def build(spec: ChartSpec, slot: Slot, tokens: DesignTokens | None) -> RenderedChart:
    """Собрать диаграмму с оформлением из правил шаблона."""
    colours = _series_colours(len(spec.series), tokens, slot)
    return RenderedChart(
        chart_type=spec.chart_type,
        categories=list(spec.categories),
        series=list(spec.series),
        colors=colours,
        axis_titles=spec.axis_titles,
        # Легенда из одного пункта занимает место и ничего не объясняет.
        has_legend=len(spec.series) > 1,
        font=_font(tokens, slot),
        size_pt=_size(tokens, slot),
    )


def _series_colours(count: int, tokens: DesignTokens | None, slot: Slot) -> list[str]:
    """Подобрать ряду свой цвет из палитры шаблона.

    Сначала идут насыщенные цвета: нейтральные сливаются с подписями и
    сеткой. Если палитра беднее числа рядов, цвета повторяются по кругу —
    это хуже, чем различимые ряды, но лучше, чем цвет, которого в шаблоне
    нет: чужой цвет сразу выдаёт, что слайд собран мимо шаблона.
    """
    palette = [token.value for token in tokens.colors] if tokens else []
    saturated = [value for value in palette if _is_saturated(value)]
    ordered = saturated + [value for value in palette if value not in saturated]

    if not ordered:
        fallback = slot.text_style.color if slot.text_style else None
        return [fallback] * count if fallback else []

    return [ordered[index % len(ordered)] for index in range(count)]


def _is_saturated(value: str) -> bool:
    red, green, blue = (int(value[index : index + 2], 16) for index in (1, 3, 5))
    return max(red, green, blue) - min(red, green, blue) > NEUTRAL_SPREAD


def _font(tokens: DesignTokens | None, slot: Slot) -> str | None:
    if tokens and tokens.fonts:
        return tokens.fonts[0].family
    return slot.text_style.font if slot.text_style else None


def _size(tokens: DesignTokens | None, slot: Slot) -> float | None:
    base = slot.text_style.size_pt if slot.text_style else None
    scale = tokens.type_scale.values if tokens else []
    if base is None or base not in scale:
        return base
    return scale[max(scale.index(base) - CHART_SIZE_STEPS_DOWN, 0)]
