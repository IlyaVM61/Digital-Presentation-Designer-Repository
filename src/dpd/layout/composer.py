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

from dpd.layout.selector import select
from dpd.models import (
    Layout,
    PresentationStructure,
    RenderedElement,
    RenderedPresentation,
    Slide,
    Slot,
    StructureSlide,
    TemplateSchema,
    TextRun,
)

DEFAULT_VARIANT = "A"


def compose(
    structure: PresentationStructure,
    template: TemplateSchema,
    variant: str = DEFAULT_VARIANT,
) -> RenderedPresentation:
    """Собрать колоду по замыслу и правилам шаблона."""
    return RenderedPresentation(
        variant=variant,
        template_hash=template.source.hash,
        canvas=template.canvas,
        slides=[_compose_slide(slide, template) for slide in structure.slides],
    )


def _compose_slide(slide: StructureSlide, template: TemplateSchema) -> Slide:
    layout, decision = select(slide, template.layouts)
    elements: list[RenderedElement] = []

    title_slot = _first_slot(layout, "title")
    if title_slot is not None:
        elements.append(_text_element(title_slot, [slide.headline]))

    body_items = slide.body.items if slide.body else []
    body_slot = _first_slot(layout, "body")
    if body_slot is not None and body_items:
        elements.append(_text_element(body_slot, body_items))

    return Slide(
        id=slide.id,
        layout_id=layout.id,
        layout_decision=decision,
        elements=elements,
    )


def _first_slot(layout: Layout, kind: str) -> Slot | None:
    return next((slot for slot in layout.slots if slot.kind == kind), None)


def _text_element(slot: Slot, paragraphs: list[str]) -> RenderedElement:
    """Уложить текст в слот.

    Геометрия берётся из слота без изменений: вёрстка не выдумывает
    координаты, а применяет правила шаблона. Переполнение здесь не
    обрабатывается — это задача T-22.

    Один `TextRun` соответствует одному абзацу. Свойства оформления не
    задаются намеренно: текст попадёт в плейсхолдер макета, и PowerPoint
    применит к нему оформление шаблона сам. Явные кегли и цвета появятся
    с применением дизайн-токенов (T-21).
    """
    return RenderedElement(
        slot_id=slot.id,
        kind="text",
        bounds=slot.bounds,
        runs=[TextRun(text=paragraph) for paragraph in paragraphs],
    )
