"""Читаемый цвет текста из палитры шаблона (T-56).

Таблица и диаграмма — единственные места, где вёрстка сама решает, на чём
лежит текст: заливку шапки и мелкий кегль подписей задаёт она, а не шаблон.
Поэтому и пару «текст — то, что под ним» она обязана сверить сама, тем же
порогом, которым её потом проверит аудит (`template.contrast_low`).

Слой детерминированный: моделей не вызывает.
"""

from __future__ import annotations

MIN_CONTRAST = 4.5
"""Порог WCAG AA для обычного текста — тот же, что у `template.contrast_low`.
Кегль таблиц и подписей диаграмм мельче основного текста, и сниженный порог
крупного текста к ним не относится."""


def readable(candidates: list[str | None], under: str | None) -> str | None:
    """Первый из кандидатов, читаемый на `under`; `None`, если такого нет.

    Порядок кандидатов — предпочтение: сначала цвет, которым шаблон набирает
    это место, потом палитра по частоте. Цвет не из палитры не предлагается:
    чужой цвет выдал бы, что слайд собран мимо шаблона.
    """
    if under is None:
        return next((colour for colour in candidates if colour), None)
    return next(
        (colour for colour in candidates if colour and contrast(colour, under) >= MIN_CONTRAST),
        None,
    )


def contrast(first: str, second: str) -> float:
    """Отношение контраста по WCAG: (светлее + 0,05) / (темнее + 0,05)."""
    lighter, darker = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _luminance(colour: str) -> float:
    channels = [int(colour[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
