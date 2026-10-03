"""T-24: генерация диаграмм по токенам шаблона.

Критерий приёмки задачи: диаграмма нативная, цвета из палитры, есть подписи
осей и легенда.

Диаграмм в шаблонах нет ни одной — 138 слайдов-примеров, ноль диаграмм, ноль
SmartArt. Копировать оформление не из чего, и оно синтезируется из токенов.

Подписи осей и легенда обязательны по проверке `integrity.chart_no_labels`:
правило наше, эталона в шаблонах нет, но диаграмма без единиц измерения не
сообщает ничего.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from pptx import Presentation

from dpd.export import export_pptx
from dpd.layout import compose
from dpd.models import (
    AxisTitles,
    ChartSeries,
    ChartSpec,
    PresentationStructure,
    StructureSlide,
    Visualization,
)
from dpd.parsing import parse_template

CALIBRATION = Path(__file__).resolve().parents[1] / "assets" / "templates" / "calibration"
TEMPLATE = "VK Tech шаблон.pptx"

CATEGORIES = ["IV кв. 2025", "I кв. 2026", "II кв. 2026", "III кв. 2026"]
POINTS = [18.0, 34.0, 61.0, 96.0]


@pytest.fixture
def template_path() -> Path:
    path = CALIBRATION / TEMPLATE
    if not path.exists():
        pytest.skip(f"нет калибровочного шаблона {TEMPLATE}")
    return path


@pytest.fixture
def out_dir() -> Iterator[Path]:
    base = Path(os.environ.get("DPD_OUTPUT_DIR", "D:/dpd-out")) / "tests" / "charts"
    base.mkdir(parents=True, exist_ok=True)
    yield base


def deck(chart_type: str = "column", series_count: int = 1) -> PresentationStructure:
    series = [
        ChartSeries(name=f"Ряд {n + 1}", points=[value + n * 5 for value in POINTS])
        for n in range(series_count)
    ]
    return PresentationStructure(
        slides=[
            StructureSlide(
                id="s1",
                role="data",
                headline="Рост числа активных пар",
                visualization=Visualization(
                    kind="chart",
                    chart=ChartSpec(
                        chart_type=chart_type,
                        categories=CATEGORIES,
                        series=series,
                        axis_titles=AxisTitles(category="квартал", value="пар"),
                    ),
                ),
            )
        ]
    )


def chart_of(structure, schema):
    rendered = compose(structure, schema)
    return next(e for s in rendered.slides for e in s.elements if e.kind == "chart")


def test_chart_element_is_produced(template_path: Path) -> None:
    element = chart_of(deck(), parse_template(template_path))
    assert element.chart.categories == CATEGORIES
    assert element.chart.series[0].points == POINTS


def test_colors_come_from_the_palette(template_path: Path) -> None:
    """Критерий приёмки: цвета из палитры шаблона."""
    schema = parse_template(template_path)
    element = chart_of(deck(series_count=3), schema)
    palette = {token.value for token in schema.design_tokens.colors}
    assert element.chart.colors
    assert set(element.chart.colors) <= palette, "в диаграмме цвета не из шаблона"


def test_series_get_distinct_colors(template_path: Path) -> None:
    """Ряды одного цвета неразличимы — диаграмма перестаёт что-либо сообщать."""
    element = chart_of(deck(series_count=3), parse_template(template_path))
    assert len(set(element.chart.colors)) == len(element.chart.series)


def test_axis_titles_and_legend_are_present(template_path: Path) -> None:
    """Критерий приёмки: подписи осей и легенда."""
    element = chart_of(deck(series_count=2), parse_template(template_path))
    assert element.chart.axis_titles.category
    assert element.chart.axis_titles.value
    assert element.chart.has_legend is True


def test_single_series_hides_the_legend(template_path: Path) -> None:
    """Легенда из одного пункта занимает место и не объясняет ничего."""
    element = chart_of(deck(series_count=1), parse_template(template_path))
    assert element.chart.has_legend is False


def test_exported_chart_is_native(template_path: Path, out_dir: Path) -> None:
    """Критерий приёмки: диаграмма — объект PowerPoint, а не картинка."""
    schema = parse_template(template_path)
    exported = export_pptx(
        compose(deck(series_count=2), schema), schema, template_path, out_dir / "chart.pptx"
    )
    slide = Presentation(str(exported)).slides[0]
    charts = [shape for shape in slide.shapes if shape.has_chart]
    assert charts, "нативной диаграммы на слайде нет"

    chart = charts[0].chart
    assert chart.has_legend is True
    assert [str(c) for c in chart.plots[0].categories] == CATEGORIES
    assert list(chart.series[0].values) == POINTS


def test_exported_chart_has_axis_titles(template_path: Path, out_dir: Path) -> None:
    schema = parse_template(template_path)
    exported = export_pptx(
        compose(deck(), schema), schema, template_path, out_dir / "axes.pptx"
    )
    chart = next(
        shape for shape in Presentation(str(exported)).slides[0].shapes if shape.has_chart
    ).chart
    assert chart.category_axis.has_title
    assert chart.value_axis.axis_title.text_frame.text == "пар"


@pytest.mark.parametrize("chart_type", ["column", "line", "pie"])
def test_exported_chart_title_is_explicitly_off(
    chart_type: str, template_path: Path, out_dir: Path
) -> None:
    """T-75: у диаграммы с одним рядом заголовок не должен зависеть от программы.

    Без явного запрета LibreOffice и PowerPoint подставляют заголовком имя ряда,
    а OnlyOffice и редакторы на его движке — нет: заказчик видел бы другую
    диаграмму, чем мы.
    Заголовка в замысле вёрстки нет — его роль играет заголовок слайда.
    """
    schema = parse_template(template_path)
    exported = export_pptx(
        compose(deck(chart_type), schema), schema, template_path, out_dir / f"title-{chart_type}.pptx"
    )
    chart = next(
        shape for shape in Presentation(str(exported)).slides[0].shapes if shape.has_chart
    ).chart
    ns = {"c": "http://schemas.openxmlformats.org/drawingml/2006/chart"}
    deleted = chart._chartSpace.chart.find("c:autoTitleDeleted", ns)
    assert deleted is not None and deleted.get("val") in ("1", "true")
    assert chart.has_title is False


@pytest.mark.parametrize("chart_type", ["column", "bar", "line", "pie"])
def test_supported_types_render(chart_type: str, template_path: Path, out_dir: Path) -> None:
    schema = parse_template(template_path)
    exported = export_pptx(
        compose(deck(chart_type), schema), schema, template_path, out_dir / f"{chart_type}.pptx"
    )
    assert any(shape.has_chart for shape in Presentation(str(exported)).slides[0].shapes)


def test_generation_is_deterministic(template_path: Path) -> None:
    schema = parse_template(template_path)
    first = compose(deck(), schema).model_dump_json(by_alias=True)
    second = compose(deck(), schema).model_dump_json(by_alias=True)
    assert first == second
