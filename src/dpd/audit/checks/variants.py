"""Проверка различимости вариантов колоды.

Требование ТЗ «варианты должны быть визуально различимы» делается измеримым:
считается, сколько осей визуального регистра действительно сработало.

**Исправимость `none`.** Слабая различимость — свойство шаблона, а не дефект
сборки: система не может добавить шаблону цветовых вариаций, которых в нём
нет. Такую находку надо предъявить пользователю, а не скрыть.

Предсказание ADR-0004 проверяется здесь на живых данных: в шаблоне, где все
макеты одной цветовой схемы, эта ось не работает, и опора только на неё
дала бы три неразличимых варианта.
"""

from __future__ import annotations

from dpd.models import Finding, RenderedPresentation, TemplateSchema

CHECK_ID = "variants.low_distinction"

MIN_WORKING_AXES = 2
"""Сколько осей должно различать варианты, чтобы разница читалась.

Одной оси мало: если совпадают и макеты, и кегли, разными остаются лишь
цвета — а на шаблоне без вариаций не остаётся и их."""


def check_variant_distinction(
    variants: list[RenderedPresentation],
    template: TemplateSchema,
) -> list[Finding]:
    """Проверить, что три варианта действительно различаются."""
    if len(variants) < 2:
        return []

    schemes = {layout.id: layout.color_scheme for layout in template.layouts}
    axes = {
        "макетов": _distinct(variants, lambda slide: slide.layout_id),
        "цветовых схем": _distinct(variants, lambda slide: schemes.get(slide.layout_id)),
        "кеглей": _distinct(
            variants,
            lambda slide: tuple(
                run.size_pt for element in slide.elements for run in element.runs
            ),
        ),
    }

    working = [name for name, count in axes.items() if count > 1]
    missing = [name for name, count in axes.items() if count <= 1]
    if not missing:
        return []

    critical = len(working) < MIN_WORKING_AXES
    return [
        Finding(
            check_id=CHECK_ID,
            severity="critical" if critical else "warning",
            fixability="none",
            message=(
                f"Варианты не различаются по осям: {', '.join(missing)}. "
                f"Работает осей: {len(working)} из {len(axes)}. "
                "Это свойство шаблона — недостающих вариаций в нём нет."
            ),
        )
    ]


def _distinct(variants: list[RenderedPresentation], key) -> int:
    """Сколько различных значений признака дали варианты."""
    return len({tuple(key(slide) for slide in variant.slides) for variant in variants})
