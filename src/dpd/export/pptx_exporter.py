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
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
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
        if element.kind == "chart" and element.chart is not None:
            _write_chart(pptx_slide, element, canvas)
            if slot and slot.placeholder_idx is not None:
                used.add(slot.placeholder_idx)
            continue
        if element.kind == "table" and element.table is not None:
            _write_table(pptx_slide, element, canvas)
            if slot and slot.placeholder_idx is not None:
                used.add(slot.placeholder_idx)
            continue
        placeholder = placeholders.get(slot.placeholder_idx) if slot else None
        if placeholder is not None:
            _write_runs(placeholder.text_frame, element.runs)
            used.add(slot.placeholder_idx)
        else:
            textbox = _new_textbox(pptx_slide, element, canvas)
            _write_runs(textbox.text_frame, element.runs, slot.text_style if slot else None)

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


CHART_TYPES = {
    "column": XL_CHART_TYPE.COLUMN_CLUSTERED,
    "bar": XL_CHART_TYPE.BAR_CLUSTERED,
    "line": XL_CHART_TYPE.LINE_MARKERS,
    "pie": XL_CHART_TYPE.PIE,
}


def _write_chart(pptx_slide, element, canvas: Canvas) -> None:
    """Вставить нативную диаграмму PowerPoint.

    Именно нативную: диаграмма, вставленная картинкой, не редактируется и
    не пересчитывается при правке данных, а ТЗ требует редактируемых
    объектов.
    """
    spec = element.chart
    data = CategoryChartData()
    data.categories = spec.categories
    for series in spec.series:
        data.add_series(series.name, series.points)

    frame = pptx_slide.shapes.add_chart(
        CHART_TYPES.get(spec.chart_type, XL_CHART_TYPE.COLUMN_CLUSTERED),
        Emu(round(element.bounds.x * canvas.width_emu)),
        Emu(round(element.bounds.y * canvas.height_emu)),
        Emu(round(element.bounds.w * canvas.width_emu)),
        Emu(round(element.bounds.h * canvas.height_emu)),
        data,
    )
    chart = frame.chart

    chart.has_legend = spec.has_legend
    if spec.has_legend:
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False

    _colour_series(chart, spec)
    _title_axes(chart, spec)

    if spec.font or spec.size_pt or spec.text_color:
        text = chart.font
        if spec.font:
            text.name = spec.font
        if spec.size_pt:
            text.size = Pt(spec.size_pt)
        if spec.text_color:
            text.color.rgb = RGBColor.from_string(spec.text_color.lstrip("#"))
    _colour_labels(chart, spec)


def _colour_labels(chart, spec) -> None:
    """Окрасить подписи осей и легенду явно, а не только по умолчанию диаграммы.

    Цвет текста всей диаграммы — лишь умолчание, и программы просмотра читают
    его по-разному; подпись без собственного цвета рисуется чёрной и на
    чёрном фоне пропадает (T-56).
    """
    if not spec.text_color:
        return
    colour = RGBColor.from_string(spec.text_color.lstrip("#"))
    if spec.has_legend:
        chart.legend.font.color.rgb = colour
    if spec.chart_type == "pie":
        return
    for axis in (chart.category_axis, chart.value_axis):
        axis.tick_labels.font.color.rgb = colour
        if axis.has_title:
            for paragraph in axis.axis_title.text_frame.paragraphs:
                for run in paragraph.runs:
                    run.font.color.rgb = colour


def _colour_series(chart, spec) -> None:
    """Окрасить ряды цветами палитры шаблона.

    У круговой диаграммы ряд один, а цветом различаются точки — поэтому
    палитра раскладывается по ним, а не по рядам.
    """
    if not spec.colors:
        return

    if spec.chart_type == "pie" and chart.series:
        points = list(chart.series[0].points)
        for index, point in enumerate(points):
            point.format.fill.solid()
            point.format.fill.fore_color.rgb = RGBColor.from_string(
                spec.colors[index % len(spec.colors)].lstrip("#")
            )
        return

    for index, series in enumerate(chart.series):
        colour = spec.colors[index % len(spec.colors)]
        series.format.fill.solid()
        series.format.fill.fore_color.rgb = RGBColor.from_string(colour.lstrip("#"))


def _title_axes(chart, spec) -> None:
    """Подписать оси. У круговой диаграммы осей нет — подписи пропускаются."""
    if spec.chart_type == "pie":
        return
    if spec.axis_titles.category:
        chart.category_axis.has_title = True
        chart.category_axis.axis_title.text_frame.text = spec.axis_titles.category
    if spec.axis_titles.value:
        chart.value_axis.has_title = True
        chart.value_axis.axis_title.text_frame.text = spec.axis_titles.value


def _write_table(pptx_slide, element, canvas: Canvas) -> None:
    """Вставить нативную таблицу PowerPoint.

    Именно нативную: таблица, нарисованная линиями или вставленная
    картинкой, не редактируется, а ТЗ требует редактируемых объектов.
    Оформление приходит из вёрстки — синтезированное из токенов шаблона.
    """
    table = element.table
    rows, columns = len(table.rows) + 1, max(len(table.headers), 1)
    shape = pptx_slide.shapes.add_table(
        rows,
        columns,
        Emu(round(element.bounds.x * canvas.width_emu)),
        Emu(round(element.bounds.y * canvas.height_emu)),
        Emu(round(element.bounds.w * canvas.width_emu)),
        Emu(round(element.bounds.h * canvas.height_emu)),
    )
    grid = shape.table

    # Заливку каждой ячейки задаёт вёрстка. Без явной заливки ячейку красит
    # стиль таблицы по умолчанию — цветом акцента темы и его светлыми
    # оттенками, которых вёрстка не видит: так шапка сливалась с текстом,
    # а белые строки — со светлой полосой (T-56).
    for column, title in enumerate(table.headers):
        _fill_cell(grid.cell(0, column), title, table, table.header_color, table.header_fill, bold=True)

    for row_index, row in enumerate(table.rows, start=1):
        for column, value in enumerate(row):
            _fill_cell(grid.cell(row_index, column), value, table, table.body_color, None)


def _fill_cell(cell, text: str, table, colour: str | None, fill: str | None, bold: bool = False) -> None:
    if fill:
        cell.fill.solid()
        cell.fill.fore_color.rgb = RGBColor.from_string(fill.lstrip("#"))
    else:
        cell.fill.background()
    cell.text = ""
    paragraph = cell.text_frame.paragraphs[0]
    run = paragraph.add_run()
    run.text = text
    if bold:
        run.font.bold = True
    if table.font:
        run.font.name = table.font
    if table.size_pt:
        run.font.size = Pt(table.size_pt)
    if colour:
        run.font.color.rgb = RGBColor.from_string(colour.lstrip("#"))


def _write_runs(text_frame, runs: list[TextRun], slot_style=None) -> None:
    """Записать абзацы. Один `TextRun` — один абзац.

    `slot_style` применяется только к рамкам, созданным нами: у родного
    плейсхолдера оформление уже есть, и навязывать ему своё значило бы
    подменять правила шаблона собственными.
    """
    text_frame.clear()
    text_frame.word_wrap = True
    for number, run in enumerate(runs):
        paragraph = text_frame.paragraphs[0] if number == 0 else text_frame.add_paragraph()
        written = paragraph.add_run()
        written.text = run.text
        if slot_style is not None:
            _apply_slot_style(written, slot_style)
        _apply_style(written, run)


def _apply_slot_style(written_run, style) -> None:
    """Применить оформление слота: гарнитуру, кегль и цвет из шаблона."""
    if style.font:
        written_run.font.name = style.font
    if style.size_pt:
        written_run.font.size = Pt(style.size_pt)
    if style.color:
        written_run.font.color.rgb = RGBColor.from_string(style.color.lstrip("#"))


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
