"""Метрика качества разметки шаблона и выбор стратегии разбора.

**Ни макеты, ни примеры сами по себе не достаточны.** В одном калибровочном
шаблоне 36 слайдов-примеров из 54 сидят на единственном макете-пустышке, и
стратегия «учиться на примерах» дала бы там 36 почти одинаковых макетов —
ровно то, что делает presenton. В другом примеры честно покрывают 14 макетов
из 15 и оказываются лучшим сигналом, какой есть. В третьем две трети макетов
размечены плейсхолдерами, и надёжнее читать их.

Отсюда метрика: парсер измеряет, на что в этом файле можно опереться, и
выбирает подход. Ни один из семи изученных конкурентов такой метрики не
имеет.

Слой детерминированный: моделей не вызывает.
"""

from __future__ import annotations

from collections import Counter

from dpd.models import MarkupQuality, ParsingStrategy

PLACEHOLDER_MARKUP_THRESHOLD = 0.7
"""Доля макетов с несколькими плейсхолдерами, при которой им можно верить."""

EXAMPLE_COVERAGE_THRESHOLD = 0.8
"""Доля макетов, имеющих хотя бы один пример применения."""

EXAMPLE_CONCENTRATION_LIMIT = 0.3
"""Доля примеров, осевших на одном макете, выше которой они бесполезны.

Покрытие без этого ограничения обманывает: примеры могут формально касаться
многих макетов, но две трети сидеть на одном."""


def measure(presentation) -> MarkupQuality:
    """Измерить качество разметки и выбрать стратегию разбора."""
    layouts = [
        layout for master in presentation.slide_masters for layout in master.slide_layouts
    ]
    total = len(layouts)
    if not total:
        return MarkupQuality(strategy="geometry-first")

    with_multiple = sum(1 for layout in layouts if len(list(layout.placeholders)) > 1)
    unique_names = len({layout.name for layout in layouts})

    usage = Counter(slide.slide_layout.name for slide in presentation.slides)
    with_examples = sum(1 for layout in layouts if usage.get(layout.name, 0) > 0)
    examples_total = sum(usage.values())
    concentration = (max(usage.values()) / examples_total) if examples_total else 0.0

    masters_empty = all(
        len(list(master.placeholders)) == 0 for master in presentation.slide_masters
    )
    has_picture_placeholders = any(
        placeholder.placeholder_format.type is not None
        and "PICTURE" in str(placeholder.placeholder_format.type)
        for layout in layouts
        for placeholder in layout.placeholders
    )

    placeholder_share = with_multiple / total
    example_share = with_examples / total

    return MarkupQuality(
        layouts_total=total,
        layouts_with_multiple_slots=with_multiple,
        layouts_with_unique_name=unique_names,
        layouts_with_examples=with_examples,
        example_concentration=round(concentration, 3),
        has_picture_placeholders=has_picture_placeholders,
        masters_empty=masters_empty,
        score=_score(placeholder_share, example_share, concentration, unique_names / total),
        strategy=_strategy(placeholder_share, example_share, concentration),
    )


def _strategy(
    placeholder_share: float, example_share: float, concentration: float
) -> ParsingStrategy:
    """Выбрать, на что опираться при разборе.

    Порядок проверок отражает убывающую надёжность источника: явная разметка
    плейсхолдерами надёжнее примеров, а примеры — надёжнее вывода из чистой
    геометрии. Геометрия остаётся ответом по умолчанию: она работает всегда,
    просто даёт меньше уверенности.
    """
    if placeholder_share >= PLACEHOLDER_MARKUP_THRESHOLD:
        return "placeholder-first"
    if example_share >= EXAMPLE_COVERAGE_THRESHOLD and concentration <= EXAMPLE_CONCENTRATION_LIMIT:
        return "examples-first"
    return "geometry-first"


def _score(
    placeholder_share: float,
    example_share: float,
    concentration: float,
    name_uniqueness: float,
) -> float:
    """Обобщённая оценка качества разметки в [0, 1].

    Нужна не для выбора стратегии, а для отчёта пользователю: по ней видно,
    насколько шаблон вообще пригоден как набор правил. Концентрация примеров
    входит со знаком минус — она ухудшает качество, а не улучшает.
    """
    value = (
        0.4 * placeholder_share
        + 0.3 * example_share * (1 - concentration)
        + 0.3 * name_uniqueness
    )
    return round(min(max(value, 0.0), 1.0), 2)
