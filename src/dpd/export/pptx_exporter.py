"""Экспорт колоды в `.pptx` нативными объектами.

**Главное требование ТЗ:** слайд, выгруженный растровым изображением, не
засчитывается. Текст остаётся текстом, его можно выделить и отредактировать
в PowerPoint.

Колода собирается **на самом файле шаблона**, а не с нуля: открывается
исходный `.pptx`, из него удаляются слайды-примеры, и новые слайды создаются
на его же макетах. Отсюда следует главное — текст ложится в родные
плейсхолдеры макета, и оформление (гарнитура, кегль, цвет, маркеры списка)
применяется шаблоном само. Собранная с нуля презентация потребовала бы
воспроизводить всю дизайн-систему вручную и всё равно расходилась бы с
оригиналом.

**Единственное место, где доли пересчитываются в EMU.** Во всех контрактах
координаты нормализованы, потому что холсты шаблонов различаются.

Слой детерминированный: моделей не вызывает.
"""

from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.util import Emu, Pt

from dpd.models import (
    Canvas,
    RenderedElement,
    RenderedPresentation,
    Slot,
    TemplateSchema,
    TextRun,
)
from dpd.parsing import layout_id


def export_pptx(
    rendered: RenderedPresentation,
    template: TemplateSchema,
    template_path: str | Path,
    output_path: str | Path,
) -> Path:
    """Выгрузить колоду в `.pptx`, собрав её на файле шаблона."""
    template_path, output_path = Path(template_path), Path(output_path)
    if not template_path.is_file():
        raise FileNotFoundError(f"шаблон не найден: {template_path}")

    presentation = Presentation(str(template_path))
    _remove_existing_slides(presentation)

    layouts = _index_layouts(presentation)
    slots_by_layout = {
        layout.id: {slot.id: slot for slot in layout.slots} for layout in template.layouts
    }

    for slide in rendered.slides:
        pptx_layout = layouts.get(slide.layout_id)
        if pptx_layout is None:
            raise KeyError(f"в шаблоне нет макета {slide.layout_id}")
        pptx_slide = presentation.slides.add_slide(pptx_layout)
        _fill_slide(pptx_slide, slide.elements, slots_by_layout.get(slide.layout_id, {}), rendered.canvas)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    presentation.save(str(output_path))
    return output_path


def _remove_existing_slides(presentation) -> None:
    """Убрать слайды-примеры шаблона, сохранив мастера, макеты и тему.

    Калибровочные шаблоны несут от 29 до 55 слайдов-примеров. Без удаления
    колода вышла бы чужой презентацией с нашими слайдами в конце.
    """
    slide_id_list = presentation.slides._sldIdLst
    for slide_id in list(slide_id_list):
        presentation.part.drop_rel(slide_id.rId)
        slide_id_list.remove(slide_id)


def _index_layouts(presentation) -> dict:
    """Сопоставить идентификаторы схемы с макетами открытого файла."""
    return {
        layout_id(layout, master_number): layout
        for master_number, master in enumerate(presentation.slide_masters, start=1)
        for layout in master.slide_layouts
    }


def _fill_slide(pptx_slide, elements: list[RenderedElement], slots: dict[str, Slot], canvas: Canvas) -> None:
    placeholders = {
        placeholder.placeholder_format.idx: placeholder for placeholder in pptx_slide.placeholders
    }

    used: set[int] = set()
    for element in elements:
        slot = slots.get(element.slot_id)
        placeholder = placeholders.get(slot.placeholder_idx) if slot else None
        if placeholder is not None:
            _write_runs(placeholder.text_frame, element.runs)
            used.add(slot.placeholder_idx)
        else:
            _write_runs(_new_textbox(pptx_slide, element, canvas).text_frame, element.runs)

    _remove_empty_placeholders(pptx_slide, used)


def _new_textbox(pptx_slide, element: RenderedElement, canvas: Canvas):
    """Текстовая рамка по координатам — для слотов без родного плейсхолдера.

    Понадобится слотам, выведенным из обычных фигур макета (T-13): в двух
    шаблонах из трёх это основной способ разметки контента.
    """
    return pptx_slide.shapes.add_textbox(
        Emu(round(element.bounds.x * canvas.width_emu)),
        Emu(round(element.bounds.y * canvas.height_emu)),
        Emu(round(element.bounds.w * canvas.width_emu)),
        Emu(round(element.bounds.h * canvas.height_emu)),
    )


def _write_runs(text_frame, runs: list[TextRun]) -> None:
    """Записать абзацы. Один `TextRun` — один абзац."""
    text_frame.clear()
    for number, run in enumerate(runs):
        paragraph = text_frame.paragraphs[0] if number == 0 else text_frame.add_paragraph()
        written = paragraph.add_run()
        written.text = run.text
        _apply_style(written, run)


def _apply_style(written_run, run: TextRun) -> None:
    """Применить оформление, заданное явно.

    Незаданное не трогается намеренно: тогда действует оформление макета,
    то есть правила шаблона. Явные значения появятся с применением
    дизайн-токенов (T-21).
    """
    if run.font:
        written_run.font.name = run.font
    if run.size_pt:
        written_run.font.size = Pt(run.size_pt)
    if run.bold:
        written_run.font.bold = True


def _remove_empty_placeholders(pptx_slide, used: set[int]) -> None:
    """Убрать незаполненные плейсхолдеры.

    Оставленный пустым плейсхолдер показывает в PowerPoint подсказку
    «Текст заголовка» — на слайде её не видно, но при показе и в PDF она
    может проявиться, и проверка аудита на пустые слоты сочтёт её дефектом.
    """
    for placeholder in list(pptx_slide.placeholders):
        if placeholder.placeholder_format.idx in used:
            continue
        if placeholder.has_text_frame and placeholder.text_frame.text.strip():
            continue
        placeholder._element.getparent().remove(placeholder._element)
