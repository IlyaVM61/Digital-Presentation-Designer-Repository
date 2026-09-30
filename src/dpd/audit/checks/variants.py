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

from dpd.audit.registry import CheckSpec, check, param
from dpd.models import Finding, RenderedPresentation, TemplateSchema

SPEC = CheckSpec(
    id="variants.low_distinction",
    category="variants",
    check_class="file",
    severity="warning",
    fixability="none",
    sublayer="variants",
    title="Варианты вёрстки различаются слабо",
)

CHECK_ID = SPEC.id


@check(SPEC)
def check_variant_distinction(
    variants: list[RenderedPresentation],
    template: TemplateSchema,
    min_working_axes: int | None = None,
) -> list[Finding]:
    """Проверить, что три варианта действительно различаются."""
    if len(variants) < 2:
        return []

    if min_working_axes is None:
        min_working_axes = param(SPEC.id, "min_working_axes")

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

    return [
        SPEC.finding(
            f"Варианты не различаются по осям: {', '.join(missing)}. "
            f"Работает осей: {len(working)} из {len(axes)}. "
            "Это свойство шаблона — недостающих вариаций в нём нет.",
            severity="critical" if len(working) < min_working_axes else "warning",
            evidence={"working": working, "missing": missing},
        )
    ]


def _distinct(variants: list[RenderedPresentation], key) -> int:
    """Сколько различных значений признака дали варианты."""
    return len({tuple(key(slide) for slide in variant.slides) for variant in variants})
