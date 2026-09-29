"""Минимальная вёрстка: укладка заголовка и текста в слоты макета.

**Минимальный срез задачи T-07.** Не делается: применение дизайн-токенов
(T-21), обработка переполнения с фиксацией компенсаций (T-22), таблицы и
диаграммы (T-23, T-24), три варианта визуального регистра (T-25),
полноценная деградация типа слайда (T-20).

Слой детерминированный: моделей не вызывает ни для выбора макета, ни для
сокращения текста. Это не догма, а следствие — если выбор макета станет
вероятностным, проверки «слайд собран не на макете из шаблона» и «блоки не
выровнены» перестанут быть воспроизводимыми, и детерминированный аудит
потеряет смысл.
"""

from __future__ import annotations

from dpd.layout.charts import build as build_chart
from dpd.layout.overflow import plan_compensations
from dpd.layout.selector import select
from dpd.layout.styling import apply, style_for
from dpd.layout.tables import build as build_table
from dpd.models import (
    Compensation,
    Layout,
    PresentationStructure,
    RenderedElement,
    RenderedPresentation,
    Slide,
    Slot,
    StructureSlide,
    TemplateSchema,
)
from dpd.models import (
    RenderedElement as _RE,
)

DEFAULT_VARIANT = "A"


def compose(
    structure: PresentationStructure,
    template: TemplateSchema,
    variant: str = DEFAULT_VARIANT,
    size_shift: int = 0,
    layout_offset: int = 0,
) -> RenderedPresentation:
    """Собрать колоду по замыслу и правилам шаблона."""
    return RenderedPresentation(
        variant=variant,
        template_hash=template.source.hash,
        canvas=template.canvas,
        slides=[
            _compose_slide(slide, template, size_shift, layout_offset)
            for slide in structure.slides
        ],
    )


def _compose_slide(
    slide: StructureSlide,
    template: TemplateSchema,
    size_shift: int = 0,
    layout_offset: int = 0,
) -> Slide:
    layout, decision = select(slide, template.layouts, layout_offset)
    elements: list[RenderedElement] = []

    tokens = template.design_tokens
    scale = tokens.type_scale.values if tokens else []
    compensations: list[Compensation] = []

    title_slot = _first_slot(layout, "title")
    if title_slot is not None:
        element, applied = _text_element(
            title_slot, [slide.headline], tokens, template.canvas, scale, size_shift
        )
        elements.append(element)
        compensations.extend(applied)

    body_slot = _first_slot(layout, "body")

    visual = slide.visualization
    if body_slot is not None and visual is not None and visual.kind == "chart" and visual.chart:
        style = style_for(body_slot, tokens, size_shift)
        styled = body_slot.model_copy(update={"text_style": style})
        chart = build_chart(visual.chart, styled, tokens)
        elements.append(
            _RE(slot_id=body_slot.id, kind="chart", bounds=body_slot.bounds, chart=chart)
        )
        return Slide(
            id=slide.id,
            layout_id=layout.id,
            layout_decision=decision,
            elements=elements,
            applied_compensations=compensations,
        )

    if body_slot is not None and visual is not None and visual.kind == "table" and visual.table:
        # Визуализация занимает место содержимого: таблица и текст в одном
        # слоте наложились бы друг на друга.
        style = style_for(body_slot, tokens, size_shift)
        styled = body_slot.model_copy(update={"text_style": style})
        table, applied = build_table(visual.table, styled, tokens)
        elements.append(
            _RE(slot_id=body_slot.id, kind="table", bounds=body_slot.bounds, table=table)
        )
        compensations.extend(applied)
        return Slide(
            id=slide.id,
            layout_id=layout.id,
            layout_decision=decision,
            elements=elements,
            applied_compensations=compensations,
        )

    body_items = slide.body.items if slide.body else []
    if body_slot is not None and body_items:
        element, applied = _text_element(
            body_slot, body_items, tokens, template.canvas, scale, size_shift
        )
        elements.append(element)
        compensations.extend(applied)

    return Slide(
        id=slide.id,
        layout_id=layout.id,
        layout_decision=decision,
        elements=elements,
        applied_compensations=compensations,
    )


def _first_slot(layout: Layout, kind: str) -> Slot | None:
    return next((slot for slot in layout.slots if slot.kind == kind), None)


def _text_element(slot: Slot, paragraphs: list[str], tokens, canvas, scale, size_shift=0):
    """Уложить текст в слот.

    Геометрия берётся из слота без изменений: вёрстка не выдумывает
    координаты, а применяет правила шаблона. Переполнение здесь не
    обрабатывается — это задача T-22.

    Один `TextRun` соответствует одному абзацу. Оформление задаётся явно и
    берётся из правил шаблона: стиль слота, разрешённый по цепочке
    наследования, с опорой на дизайн-токены там, где слот молчит.
    """
    style = style_for(slot, tokens, size_shift)
    styled = slot.model_copy(update={"text_style": style})
    compensations, runs = plan_compensations(
        paragraphs, styled, canvas, scale or [style.size_pt]
    )
    if not runs:
        runs = [apply(style, paragraph) for paragraph in paragraphs]
    return (
        RenderedElement(slot_id=slot.id, kind="text", bounds=slot.bounds, runs=runs),
        compensations,
    )
