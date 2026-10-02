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
from dpd.layout.selector import roomiest_slot, select, visual_slot
from dpd.layout.styling import apply, style_for
from dpd.layout.tables import build as build_table
from dpd.models import (
    Compensation,
    Decision,
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
    """Собрать колоду по замыслу и правилам шаблона.

    Решения, принятые там, где у свойства не было однозначного значения,
    собираются в одном списке: пользователь должен видеть, что система
    выбрала за него, а не узнавать об этом из выгруженного файла.
    """
    decisions: list[Decision] = []
    slides = [
        _compose_slide(slide, template, size_shift, layout_offset, decisions)
        for slide in structure.slides
    ]
    return RenderedPresentation(
        variant=variant,
        template_hash=template.source.hash,
        canvas=template.canvas,
        slides=slides,
        decisions=_unique(decisions),
    )


def _unique(decisions: list[Decision]) -> list[Decision]:
    """Одно решение на свойство и слот: оно повторяется на каждом слайде."""
    видели: dict[tuple[str, str, str], Decision] = {}
    for decision in decisions:
        видели.setdefault((decision.kind, decision.slot_id, decision.declared), decision)
    return list(видели.values())


def _compose_slide(
    slide: StructureSlide,
    template: TemplateSchema,
    size_shift: int = 0,
    layout_offset: int = 0,
    decisions: list[Decision] | None = None,
) -> Slide:
    layout, decision = select(slide, template.layouts, layout_offset)
    elements: list[RenderedElement] = []

    tokens = template.design_tokens
    scale = tokens.type_scale.values if tokens else []
    compensations: list[Compensation] = []

    title_slot = _first_slot(layout, "title")
    if title_slot is not None:
        element, applied = _text_element(
            title_slot, [slide.headline], tokens, template.canvas, scale, size_shift, decisions
        )
        elements.append(element)
        compensations.extend(applied)

    # Самое просторное место, а не первое: по нему выбор макета оценил
    # макет, и туда же должно лечь содержимое. Первым бывает полоска
    # надзаголовка — на VK Tech тело резалось в ней до 7 pt (T-60).
    body_slot = roomiest_slot(layout)

    visual = slide.visualization
    if body_slot is not None and visual is not None and visual.kind == "chart" and visual.chart:
        style = style_for(body_slot, tokens, size_shift, decisions)
        styled = body_slot.model_copy(update={"text_style": style})
        chart = build_chart(visual.chart, styled, tokens, _plain_background(layout))
        elements.append(
            _RE(slot_id=body_slot.id, kind="chart", bounds=body_slot.bounds, chart=chart, frame=body_slot.frame)
        )
        return Slide(
            id=slide.id,
            layout_id=layout.id,
            layout_decision=decision,
            elements=elements,
            applied_compensations=compensations,
        )

    table_slot = visual_slot(layout, "table")
    if table_slot is not None and visual is not None and visual.kind == "table" and visual.table:
        # Визуализация занимает место содержимого: таблица и текст в одном
        # слоте наложились бы друг на друга. У таблицы со своим полем (T-62)
        # текстовое поле свободно, и тело ложится в него.
        style = style_for(table_slot, tokens, size_shift, decisions)
        styled = table_slot.model_copy(update={"text_style": style})
        table, applied = build_table(visual.table, styled, tokens, _plain_background(layout))
        elements.append(
            _RE(slot_id=table_slot.id, kind="table", bounds=table_slot.bounds, table=table, frame=table_slot.frame)
        )
        compensations.extend(applied)
        if table_slot.kind == "table" and body_slot is not None and slide.body and slide.body.items:
            placed, applied = _text_columns(
                _columns(layout, body_slot), slide.body.items, tokens, template.canvas, scale, size_shift, decisions
            )
            elements.extend(placed)
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
        columns = _columns(layout, body_slot)
        placed, applied = _text_columns(
            columns, body_items, tokens, template.canvas, scale, size_shift, decisions
        )
        elements.extend(placed)
        compensations.extend(applied)

    return Slide(
        id=slide.id,
        layout_id=layout.id,
        layout_decision=decision,
        elements=elements,
        applied_compensations=compensations,
    )


def _plain_background(layout: Layout) -> str | None:
    """Цвет фона макета, если он известен; под изображением — `None`."""
    background = layout.background
    return background.value if background.contrast_computable and background.value else None


def _first_slot(layout: Layout, kind: str) -> Slot | None:
    return next((slot for slot in layout.slots if slot.kind == kind), None)


COLUMN_TOLERANCE = 0.1
"""На какую долю места под содержимое могут различаться по ширине и высоте —
и расходиться верхним краем, — чтобы считаться колонками одного ряда, а не
полоской рядом с областью или местом под ней."""


def _columns(layout: Layout, main: Slot) -> list[Slot]:
    """Колонки, по которым идёт тело: верхний ряд мест размера самого просторного.

    Колонка — место под содержимое того же размера, что и самое просторное,
    на той же высоте: так шаблон размечает «две колонки» и «три колонки».
    Полоска надзаголовка рядом с областью — не колонка. Место того же размера
    над областью или под ней — тоже (T-64, вопрос T25): продолженный в нём
    список разрывается промежутком и читается как два списка, а колонки
    рядом — как один.

    Ряд — верхний из рядов таких мест, а не ряд самого просторного: равные
    места различаются по площади на шум разметки, и на VK Education
    («Цитата без фото») нижнее из двух равных просторнее верхнего на
    десятимиллионную долю — список уходил бы вниз под пустой блок. Порядок
    в ряду — слева направо: верхние края колонок, нарисованных от руки,
    расходятся на доли процента, и порядок по ним начинал бы список в правой
    колонке.
    """
    def same_size(slot: Slot) -> bool:
        return (
            abs(slot.bounds.w - main.bounds.w) <= COLUMN_TOLERANCE * main.bounds.w
            and abs(slot.bounds.h - main.bounds.h) <= COLUMN_TOLERANCE * main.bounds.h
        )

    alike = [slot for slot in layout.slots if slot.kind == "body" and same_size(slot)]
    top = min(alike, key=lambda slot: (slot.bounds.y, slot.bounds.x))
    row = [slot for slot in alike if abs(slot.bounds.y - top.bounds.y) <= COLUMN_TOLERANCE * main.bounds.h]
    return sorted(row, key=lambda slot: slot.bounds.x)


def _split(items: list[str], parts: int) -> list[list[str]]:
    """Разложить пункты по колонкам подряд, поровну; лишние — в первые."""
    parts = max(min(parts, len(items)), 1)
    base, extra = divmod(len(items), parts)
    chunks, start = [], 0
    for index in range(parts):
        size = base + (1 if index < extra else 0)
        chunks.append(items[start : start + size])
        start += size
    return chunks


def _text_columns(columns: list[Slot], items: list[str], tokens, canvas, scale, size_shift=0, decisions=None):
    """Уложить тело в колонки ряда слева направо (T-60, T-64).

    Тело, занявшее одну колонку из нескольких, оставляло соседние пустыми, а
    в своей — уходило за нижний край: живой прогон T-58, колонка шириной
    0,21 холста. Пункты раскладываются по колонкам подряд, а кегль во всех
    колонках один — наименьший из понадобившихся: колонки одного слайда
    разным кеглем выглядят ошибкой вёрстки. Выравнивание кегля записывается
    компенсацией, как любое отклонение от замысла.
    """
    chunks = _split(items, len(columns))
    planned = [
        _text_element(column, chunk, tokens, canvas, scale, size_shift, decisions)
        for column, chunk in zip(columns, chunks)
    ]
    sizes = [element.runs[0].size_pt for element, _ in planned if element.runs and element.runs[0].size_pt]
    if len(set(sizes)) <= 1:
        return [element for element, _ in planned], [item for _, applied in planned for item in applied]

    smallest = min(sizes)
    elements, compensations = [], []
    for column, chunk, (element, applied) in zip(columns, chunks, planned):
        size = element.runs[0].size_pt if element.runs else None
        if size is None or size == smallest:
            elements.append(element)
            compensations.extend(applied)
            continue
        original = applied[0].from_value if applied else size
        runs = [run.model_copy(update={"size_pt": smallest}) for run in element.runs]
        elements.append(element.model_copy(update={"runs": runs}))
        compensations.append(
            Compensation(
                kind="fontScale",
                slot_id=column.id,
                from_value=original,
                to_value=smallest,
                reason="кегль выровнен по соседней колонке",
            )
        )
    return elements, compensations


def _text_element(slot: Slot, paragraphs: list[str], tokens, canvas, scale, size_shift=0, decisions=None):
    """Уложить текст в слот.

    Геометрия берётся из слота без изменений: вёрстка не выдумывает
    координаты, а применяет правила шаблона. Переполнение здесь не
    обрабатывается — это задача T-22.

    Один `TextRun` соответствует одному абзацу. Оформление задаётся явно и
    берётся из правил шаблона: стиль слота, разрешённый по цепочке
    наследования, с опорой на дизайн-токены там, где слот молчит.
    """
    style = style_for(slot, tokens, size_shift, decisions)
    styled = slot.model_copy(update={"text_style": style})
    compensations, runs = plan_compensations(
        paragraphs, styled, canvas, scale or [style.size_pt]
    )
    if not runs:
        runs = [apply(style, paragraph) for paragraph in paragraphs]
    return (
        RenderedElement(slot_id=slot.id, kind="text", bounds=slot.bounds, runs=runs, frame=slot.frame),
        compensations,
    )
