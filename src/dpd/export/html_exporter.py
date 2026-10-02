"""Экспорт колоды в HTML (T-37).

Третий обязательный формат ТЗ. Статическое представление колоды: слайды
размечены как блоки, текст остаётся текстом, таблицы — таблицами, диаграммы
рисуются векторно.

**Слайды не растеризуются.** Картинка слайда вместо разметки сделала бы
экспорт нечитаемым для поиска, копирования и перевода, а по ТЗ растровый
слайд не засчитывается вовсе. Это же требование записано в архитектуре слоя
экспорта — «не растеризует слайды», без оговорок про формат.

**Файл самодостаточен:** стили внутри, внешних ссылок нет. Экспорт, который
без сети выглядит поломанным, заказчику не передать.

**Графика самого шаблона не воспроизводится.** Она живёт в макетах `.pptx`, а
в контракте вёрстки её нет: там координаты, текст и токены. Поэтому HTML
выглядит строже оригинала — точный вид дают `.pptx` и `.pdf`.

Отсюда правило про фон. Сплошной берётся из макета как есть. Когда фоном
служит изображение, цвет выбирается по цветовой схеме макета: в VK Tech так
устроены 24 макета из 39, и текст на тёмных макетах белый — на белой странице
он исчез бы полностью, и экспорт отдал бы пустые с виду слайды.

Слой детерминированный: моделей не вызывает.
"""

from __future__ import annotations

import html
import math
from pathlib import Path

from dpd.models import (
    Bounds,
    Layout,
    RenderedChart,
    RenderedElement,
    RenderedPresentation,
    RenderedTable,
    Slide,
    TemplateSchema,
)

SCHEME_BACKGROUNDS = {"dark": "#111418", "light": "#FFFFFF", "unknown": "#FFFFFF"}
"""Фон слайда, когда в макете его цвета нет: по цветовой схеме макета."""

SCHEME_TEXT = {"dark": "#F5F7FA", "light": "#111418", "unknown": "#111418"}
"""Цвет текста, если вёрстка его не задала: наследование в HTML не работает."""

SLIDE_WIDTH_PX = 1100
"""Ширина слайда на странице. Высота считается по холсту шаблона: холсты
различаются — 9144000 против 12192000 EMU при одном 16:9."""

CHART_PADDING = 0.12
"""Доля площади диаграммы под подписи осей."""


def export_html(
    deck: RenderedPresentation,
    template: TemplateSchema,
    output_path: str | Path,
) -> Path:
    """Выгрузить колоду в самодостаточную HTML-страницу."""
    output_path = Path(output_path)
    if output_path.suffix.lower() != ".html":
        output_path = output_path.with_suffix(".html")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    layouts = {layout.id: layout for layout in template.layouts}
    height = round(SLIDE_WIDTH_PX * deck.canvas.height_emu / deck.canvas.width_emu)

    slides = "\n".join(_slide(slide, layouts.get(slide.layout_id), height) for slide in deck.slides)
    title = html.escape(f"{Path(template.source.file).stem} — вариант {deck.variant}")

    output_path.write_text(_page(title, height, slides), encoding="utf-8")
    return output_path


def _page(title: str, height: int, slides: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
body {{ margin: 0; padding: 24px; background: #E9ECF1; font-family: sans-serif; }}
.slide {{ position: relative; width: {SLIDE_WIDTH_PX}px; height: {height}px; margin: 0 auto 24px;
          overflow: hidden; box-shadow: 0 2px 12px rgba(0,0,0,.18); }}
.block {{ position: absolute; overflow: hidden; }}
.block p {{ margin: 0 0 .35em; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid rgba(128,128,128,.45); padding: .35em .5em; text-align: left; }}
</style>
</head>
<body>
{slides}
</body>
</html>
"""


def _slide(slide: Slide, layout: Layout | None, height: int) -> str:
    scheme = layout.color_scheme if layout else "unknown"
    background = layout.background.value if layout and layout.background.value else None
    if background is None:
        background = SCHEME_BACKGROUNDS.get(scheme, "#FFFFFF")

    blocks = "\n".join(_block(element, scheme) for element in slide.elements)
    return (
        f'<section class="slide" id="{html.escape(slide.id)}" '
        f'style="background: {background}; height: {height}px">\n{blocks}\n</section>'
    )


def _block(element: RenderedElement, scheme: str) -> str:
    inner = (
        _table(element.table)
        if element.table is not None
        else _chart(element.chart, scheme)
        if element.chart is not None
        else _paragraphs(element, scheme)
    )
    return f'<div class="block" style="{_position(element.bounds)}">{inner}</div>'


def _position(bounds: Bounds) -> str:
    """Координаты в процентах: доли контракта ложатся на блок один к одному."""
    return (
        f"left: {bounds.x * 100:.3f}%; top: {bounds.y * 100:.3f}%; "
        f"width: {bounds.w * 100:.3f}%; height: {bounds.h * 100:.3f}%"
    )


def _paragraphs(element: RenderedElement, scheme: str) -> str:
    """Абзацы текста; маркер, отбивка и положение по высоте — как в `.pptx` (T-69)."""
    spacing = element.frame.space_before_pt if element.frame else 0.0
    bullet = element.bullet
    lines = []
    for run in element.runs:
        if not run.text.strip():
            continue
        style = [f"font-family: '{run.font}', sans-serif"] if run.font else []
        if run.size_pt:
            style.append(f"font-size: {run.size_pt:g}pt")
        style.append(f"color: {run.color or SCHEME_TEXT.get(scheme, '#111418')}")
        if run.bold:
            style.append("font-weight: 700")
        if spacing:
            style.append(f"margin-top: {spacing:g}pt")
        marker = ""
        if bullet is not None:
            style.append("padding-left: 1.2em; text-indent: -1.2em")
            look = f"font-family: '{bullet.font}'; " if bullet.font else ""
            look += f"color: {bullet.color}; " if bullet.color else ""
            marker = f'<span style="{look}display: inline-block; width: 1.2em; text-indent: 0">{html.escape(bullet.char)}</span>'
        lines.append(f'<p style="{"; ".join(style)}">{marker}{html.escape(run.text)}</p>')
    text = "\n".join(lines)
    if element.anchor == "middle":
        return f'<div style="display: flex; flex-direction: column; justify-content: center; height: 100%">{text}</div>'
    return text


def _table(table: RenderedTable) -> str:
    font = f"font-family: '{table.font}', sans-serif; " if table.font else ""
    size = f"font-size: {table.size_pt:g}pt; " if table.size_pt else ""
    fill = f"background: {table.header_fill}; " if table.header_fill else ""
    head = "".join(
        f'<th style="{fill}color: {table.header_color or "inherit"}">{html.escape(cell)}</th>'
        for cell in table.headers
    )
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in row) + "</tr>"
        for row in table.rows
    )
    return (
        f'<table style="{font}{size}color: {table.body_color or "inherit"}">'
        f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    )


def _chart(chart: RenderedChart, scheme: str) -> str:
    """Нарисовать диаграмму вектором, сохранив её подписи текстом.

    Картинкой диаграмма стала бы нечитаемой для поиска, а таблицей потеряла
    бы смысл визуализации, ради которого её и выбрали.
    """
    colour = chart.text_color or SCHEME_TEXT.get(scheme, "#111418")
    font = f"font-family: '{chart.font}', sans-serif;" if chart.font else ""
    width, height = 100.0, 100.0
    pad = CHART_PADDING * 100

    if chart.chart_type == "pie":
        marks = _pie(chart, width, height, pad)
    elif chart.chart_type == "line":
        marks = _line(chart, width, height, pad)
    elif chart.chart_type == "bar":
        marks = _bars(chart, width, height, pad, horizontal=True)
    else:
        marks = _bars(chart, width, height, pad, horizontal=False)

    labels = _chart_labels(chart, colour, width, height, pad)
    legend = _legend(chart, colour) if chart.has_legend else ""
    return (
        f'<svg viewBox="0 0 {width:g} {height:g}" preserveAspectRatio="none" '
        f'style="width: 100%; height: 100%; {font}">{_names(chart)}{marks}{labels}</svg>{legend}'
    )


def _names(chart: RenderedChart) -> str:
    """Имена рядов в заголовке диаграммы.

    Легенда из одного пункта не показывается — она занимает место и ничего
    не объясняет, — но имя ряда от этого не перестаёт быть текстом колоды.
    В `.pptx` оно лежит в данных диаграммы; здесь ему место в `<title>`:
    оно ищется, читается экранной программой и всплывает подсказкой, не
    загромождая слайд.
    """
    names = ", ".join(series.name for series in chart.series if series.name)
    return f"<title>{html.escape(names)}</title>" if names else ""


def _values(chart: RenderedChart) -> list[list[float]]:
    return [list(series.points) for series in chart.series if series.points]


def _peak(values: list[list[float]]) -> float:
    highest = max((max(row) for row in values if row), default=1.0)
    return highest if highest > 0 else 1.0


def _colour_of(chart: RenderedChart, index: int) -> str:
    return chart.colors[index % len(chart.colors)] if chart.colors else "#0077FF"


def _bars(chart: RenderedChart, width: float, height: float, pad: float, *, horizontal: bool) -> str:
    values = _values(chart)
    if not values:
        return ""

    peak = _peak(values)
    columns = max(len(row) for row in values)
    span = (width - 2 * pad) if not horizontal else (height - 2 * pad)
    group = span / max(columns, 1)
    thickness = group / (len(values) + 1)
    # Группа столбцов центрируется в своей доле: иначе подпись категории,
    # стоящая по центру доли, не совпадает с тем, что под ней нарисовано.
    inset = (group - thickness * len(values)) / 2

    marks = []
    for series_index, row in enumerate(values):
        for point_index, value in enumerate(row):
            length = max(value, 0) / peak * (height - 2 * pad if not horizontal else width - 2 * pad)
            offset = pad + point_index * group + inset + series_index * thickness
            if horizontal:
                marks.append(
                    f'<rect x="{pad:.2f}" y="{offset:.2f}" width="{length:.2f}" '
                    f'height="{thickness * 0.8:.2f}" fill="{_colour_of(chart, series_index)}"/>'
                )
            else:
                marks.append(
                    f'<rect x="{offset:.2f}" y="{height - pad - length:.2f}" '
                    f'width="{thickness * 0.8:.2f}" height="{length:.2f}" '
                    f'fill="{_colour_of(chart, series_index)}"/>'
                )
    return "".join(marks)


def _line(chart: RenderedChart, width: float, height: float, pad: float) -> str:
    values = _values(chart)
    if not values:
        return ""

    peak = _peak(values)
    marks = []
    for series_index, row in enumerate(values):
        step = (width - 2 * pad) / max(len(row) - 1, 1)
        points = " ".join(
            f"{pad + index * step:.2f},{height - pad - max(value, 0) / peak * (height - 2 * pad):.2f}"
            for index, value in enumerate(row)
        )
        marks.append(
            f'<polyline points="{points}" fill="none" stroke="{_colour_of(chart, series_index)}" '
            f'stroke-width="1.5"/>'
        )
    return "".join(marks)


def _pie(chart: RenderedChart, width: float, height: float, pad: float) -> str:
    """Круговая: цветом различаются точки первого ряда, а не ряды."""
    values = _values(chart)
    if not values:
        return ""

    row = [max(value, 0) for value in values[0]]
    total = sum(row) or 1.0
    centre_x, centre_y = width / 2, height / 2
    radius = min(width, height) / 2 - pad / 2

    marks = []
    angle = -math.pi / 2
    for index, value in enumerate(row):
        sweep = 2 * math.pi * value / total
        start = (centre_x + radius * math.cos(angle), centre_y + radius * math.sin(angle))
        angle += sweep
        end = (centre_x + radius * math.cos(angle), centre_y + radius * math.sin(angle))
        large = 1 if sweep > math.pi else 0
        marks.append(
            f'<path d="M {centre_x:.2f} {centre_y:.2f} L {start[0]:.2f} {start[1]:.2f} '
            f'A {radius:.2f} {radius:.2f} 0 {large} 1 {end[0]:.2f} {end[1]:.2f} Z" '
            f'fill="{_colour_of(chart, index)}"/>'
        )
    return "".join(marks)


def _chart_labels(chart: RenderedChart, colour: str, width: float, height: float, pad: float) -> str:
    """Подписи осей и категорий — текстом, а не частью картинки."""
    parts = []
    if chart.axis_titles.value:
        parts.append(
            f'<text x="1" y="{pad * 0.6:.2f}" fill="{colour}" font-size="4">'
            f"{html.escape(chart.axis_titles.value)}</text>"
        )
    if chart.axis_titles.category:
        parts.append(
            f'<text x="{width / 2:.2f}" y="{height - 0.5:.2f}" fill="{colour}" font-size="3.6" '
            f'text-anchor="middle">{html.escape(chart.axis_titles.category)}</text>'
        )

    if not chart.categories:
        return "".join(parts)

    if chart.chart_type == "column":
        # Подписи стоят над линией основания столбцов, а подпись оси — ниже
        # всех: на пробном рендере они наложились друг на друга и не читались.
        step = (width - 2 * pad) / len(chart.categories)
        parts.extend(
            f'<text x="{pad + step * (index + 0.5):.2f}" y="{height - pad * 0.3:.2f}" '
            f'fill="{colour}" font-size="3.2" text-anchor="middle">{html.escape(name)}</text>'
            for index, name in enumerate(chart.categories)
        )
    else:
        # Горизонтальные столбцы, линия и круговая: подписи идут слева
        # столбиком — под ними у этих типов места нет.
        step = (height - 2 * pad) / len(chart.categories)
        parts.extend(
            f'<text x="1" y="{pad + step * (index + 0.5):.2f}" fill="{colour}" font-size="3.2">'
            f"{html.escape(name)}</text>"
            for index, name in enumerate(chart.categories)
        )
    return "".join(parts)


def _legend(chart: RenderedChart, colour: str) -> str:
    """Легенда — обычный текст под диаграммой: её тоже надо искать и читать."""
    items = "".join(
        f'<span style="margin-right: 1em; white-space: nowrap">'
        f'<span style="display: inline-block; width: .7em; height: .7em; '
        f'background: {_colour_of(chart, index)}"></span> {html.escape(series.name)}</span>'
        for index, series in enumerate(chart.series)
    )
    size = f"font-size: {chart.size_pt:g}pt; " if chart.size_pt else ""
    return f'<div style="{size}color: {colour}">{items}</div>'
