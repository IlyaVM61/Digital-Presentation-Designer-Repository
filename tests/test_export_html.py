"""T-37: экспорт колоды в HTML.

Критерий приёмки: **открывается в браузере, содержит весь текст колоды**.

Вторая половина проверяется буквально: каждый абзац, каждая ячейка таблицы,
каждое имя ряда и подпись оси ищутся в готовом файле. HTML, в котором текста
нет, — это картинка слайда, выданная за экспорт, а слой экспорта слайды не
растеризует: это прямое требование архитектуры и ТЗ.

Файл самодостаточен: ни одной внешней ссылки, стили внутри. Экспорт, который
без сети выглядит поломанным, не годится для передачи заказчику.

**Графика самого шаблона в HTML не воспроизводится** — она живёт в макетах
`.pptx`, а не в контракте вёрстки. Отсюда правило про фон: сплошной берётся
из макета, а когда фоном служит изображение, цвет выбирается по цветовой
схеме макета. Иначе белый текст тёмного макета лёг бы на белую страницу и
исчез.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from dpd.export import export_html
from dpd.layout import compose_variants
from dpd.models import (
    AxisTitles,
    Background,
    Bounds,
    Canvas,
    ChartSeries,
    ChartSpec,
    ColorToken,
    DesignTokens,
    FontToken,
    Layout,
    PresentationStructure,
    RenderedChart,
    RenderedElement,
    RenderedPresentation,
    RenderedTable,
    Slide,
    SlideBody,
    Slot,
    StructureSlide,
    TableSpec,
    TemplateSchema,
    TemplateSource,
    TextRun,
    TypeScale,
    Visualization,
)
from dpd.parsing import parse_template

CALIBRATION = Path(__file__).resolve().parents[1] / "assets" / "templates" / "calibration"
CANVAS = Canvas(width_emu=12192000, height_emu=6858000)
TITLE_BOUNDS = Bounds(x=0.06, y=0.08, w=0.60, h=0.12)
BODY_BOUNDS = Bounds(x=0.06, y=0.30, w=0.60, h=0.40)


def schema(
    *,
    background: Background | None = None,
    color_scheme: str = "light",
) -> TemplateSchema:
    return TemplateSchema(
        source=TemplateSource(file="synthetic.pptx", hash="0" * 8),
        canvas=CANVAS,
        design_tokens=DesignTokens(
            fonts=[FontToken(role="body", family="Play", confidence=0.9)],
            colors=[ColorToken(role="text.primary", value="#000000", confidence=0.6)],
            type_scale=TypeScale(values=[12.0, 14.0, 18.0, 24.0, 48.0]),
        ),
        layouts=[
            Layout(
                id="l1",
                name="Макет",
                family="content",
                color_scheme=color_scheme,
                background=background or Background(kind="solid", value="#FFFFFF", contrast_computable=True),
                slots=[
                    Slot(id="title", kind="title", origin="placeholder", bounds=TITLE_BOUNDS),
                    Slot(id="body", kind="body", origin="placeholder", bounds=BODY_BOUNDS),
                ],
            )
        ],
    )


def text_element(*paragraphs: str, slot_id: str = "body", colour: str = "#000000") -> RenderedElement:
    return RenderedElement(
        slot_id=slot_id,
        kind="text",
        bounds=BODY_BOUNDS if slot_id == "body" else TITLE_BOUNDS,
        runs=[TextRun(text=item, font="Play", size_pt=14, color=colour) for item in paragraphs],
    )


def deck(*elements: RenderedElement) -> RenderedPresentation:
    return RenderedPresentation(
        variant="A",
        template_hash="0" * 8,
        canvas=CANVAS,
        slides=[Slide(id="s1", layout_id="l1", elements=list(elements))],
    )


def exported(source: RenderedPresentation, template: TemplateSchema, path: Path) -> str:
    return export_html(source, template, path).read_text(encoding="utf-8")


# --- Критерий приёмки -----------------------------------------------------


def test_html_opens_as_a_standalone_page(tmp_path: Path) -> None:
    """Файл самодостаточен: разметка целая, внешних ссылок нет.

    Экспорт, который без сети выглядит поломанным, заказчику не передать.
    """
    page = exported(deck(text_element("Итоги пилота")), schema(), tmp_path / "deck.html")

    assert page.lstrip().startswith("<!DOCTYPE html>")
    assert '<meta charset="utf-8">' in page
    assert page.rstrip().endswith("</html>")
    assert "<style>" in page, "стили должны лежать внутри файла"
    assert not re.search(r'(src|href)="https?://', page), "внешняя ссылка ломает автономность"


def test_every_word_of_the_deck_is_in_the_page(tmp_path: Path) -> None:
    """Вторая половина критерия, проверенная буквально."""
    source = deck(
        text_element("Итоги пилота", slot_id="title"),
        text_element("Срок сборки сократился втрое", "Правки переживают сохранение"),
    )

    page = exported(source, schema(), tmp_path / "deck.html")

    for paragraph in ("Итоги пилота", "Срок сборки сократился втрое", "Правки переживают сохранение"):
        assert paragraph in page


def test_slides_are_not_rasterised(tmp_path: Path) -> None:
    """Слой экспорта слайды не растеризует — ни в одном формате.

    Картинка слайда вместо разметки сделала бы HTML нечитаемым для поиска,
    копирования и перевода, а по ТЗ растровый слайд не засчитывается вовсе.
    """
    page = exported(deck(text_element("Итоги пилота")), schema(), tmp_path / "deck.html")

    assert "<img" not in page
    assert "data:image" not in page


def test_one_section_per_slide(tmp_path: Path) -> None:
    source = RenderedPresentation(
        variant="A",
        template_hash="0" * 8,
        canvas=CANVAS,
        slides=[
            Slide(id=f"s{number}", layout_id="l1", elements=[text_element(f"Слайд {number}")])
            for number in (1, 2, 3)
        ],
    )

    page = exported(source, schema(), tmp_path / "deck.html")

    assert page.count('class="slide"') == 3


# --- Оформление берётся из шаблона -----------------------------------------


def test_typography_and_colour_come_from_the_deck(tmp_path: Path) -> None:
    """Гарнитура, кегль и цвет — те, что приняла вёрстка по токенам шаблона."""
    page = exported(deck(text_element("Итоги", colour="#0077FF")), schema(), tmp_path / "deck.html")

    assert "Play" in page
    assert "#0077FF" in page
    assert "14" in page


def test_solid_background_of_the_layout_is_used(tmp_path: Path) -> None:
    template = schema(background=Background(kind="solid", value="#EBF3F9", contrast_computable=True))

    page = exported(deck(text_element("Итоги")), template, tmp_path / "deck.html")

    assert "#EBF3F9" in page


def test_dark_layout_keeps_light_text_readable(tmp_path: Path) -> None:
    """Фон-изображение в HTML не воспроизвести — цвет берётся по схеме макета.

    В VK Tech фоном служит изображение у 24 макетов из 39, и текст на тёмных
    макетах белый. На белой странице он исчез бы полностью: экспорт отдал бы
    пустые с виду слайды.
    """
    template = schema(
        background=Background(kind="image", value=None, contrast_computable=False),
        color_scheme="dark",
    )

    page = exported(deck(text_element("Итоги пилота", colour="#FAFCFF")), template, tmp_path / "deck.html")

    assert "#FAFCFF" in page
    fon = re.search(r'class="slide"[^>]*background:\s*(#[0-9A-Fa-f]{6})', page)
    assert fon is not None, "фон слайда не задан"
    assert fon.group(1).upper() not in ("#FFFFFF", "#FAFCFF"), "светлый фон под светлым текстом"


# --- Таблицы и диаграммы ---------------------------------------------------


def test_table_becomes_a_real_table(tmp_path: Path) -> None:
    """Таблица остаётся таблицей: её содержимое ищется и копируется."""
    table = RenderedElement(
        slot_id="body",
        kind="table",
        bounds=BODY_BOUNDS,
        table=RenderedTable(
            headers=["Метрика", "Было", "Стало"],
            rows=[["Срок", "9 дней", "3 дня"]],
            font="Play",
            size_pt=12,
            header_color="#0077FF",
        ),
    )

    page = exported(deck(table), schema(), tmp_path / "deck.html")

    assert "<table" in page and "<th" in page and "<td" in page
    for cell in ("Метрика", "Было", "Стало", "Срок", "9 дней", "3 дня"):
        assert cell in page


def test_chart_becomes_svg_and_keeps_its_words(tmp_path: Path) -> None:
    """Диаграмма рисуется векторно, а её подписи остаются текстом.

    Картинкой диаграмма стала бы нечитаемой для поиска, а таблицей — потеряла
    бы смысл визуализации, ради которого её и выбрали.
    """
    chart = RenderedElement(
        slot_id="body",
        kind="chart",
        bounds=BODY_BOUNDS,
        chart=RenderedChart(
            chart_type="column",
            categories=["I квартал", "II квартал"],
            series=[ChartSeries(name="Выручка", points=[1.0, 2.0])],
            colors=["#0077FF"],
            axis_titles=AxisTitles(category="Квартал", value="млн ₽"),
            has_legend=False,
            font="Play",
            size_pt=12,
        ),
    )

    page = exported(deck(chart), schema(), tmp_path / "deck.html")

    assert "<svg" in page and "<rect" in page
    for word in ("I квартал", "II квартал", "Выручка", "Квартал", "млн ₽"):
        assert word in page


@pytest.mark.parametrize("kind", ["column", "bar", "line", "pie"])
def test_every_chart_type_is_drawn(tmp_path: Path, kind: str) -> None:
    """Все четыре типа, которые умеет вёрстка, рисуются и в HTML."""
    chart = RenderedElement(
        slot_id="body",
        kind="chart",
        bounds=BODY_BOUNDS,
        chart=RenderedChart(
            chart_type=kind,
            categories=["I", "II", "III"],
            series=[ChartSeries(name="Выручка", points=[1.0, 2.0, 1.5])],
            colors=["#0077FF", "#FFD91D", "#FF6C6C"],
            axis_titles=AxisTitles(category="Квартал", value="млн ₽"),
            has_legend=True,
            font="Play",
            size_pt=12,
        ),
    )

    page = exported(deck(chart), schema(), tmp_path / f"{kind}.html")

    assert "<svg" in page
    assert re.search(r"<(rect|polyline|path)", page), f"{kind}: ничего не нарисовано"


def test_category_labels_sit_under_their_own_bars(tmp_path: Path) -> None:
    """Подпись категории стоит ровно под своей группой столбцов.

    Дефект найден рендером в браузере, а не тестом: группа из двух рядов
    занимала две трети отведённой доли и прижималась влево, а подпись стояла
    по центру доли — получалось, что «I» подписывает промежуток.
    """
    chart = RenderedElement(
        slot_id="body",
        kind="chart",
        bounds=BODY_BOUNDS,
        chart=RenderedChart(
            chart_type="column",
            categories=["I", "II"],
            series=[
                ChartSeries(name="Выручка", points=[1.0, 2.0]),
                ChartSeries(name="Затраты", points=[1.0, 1.0]),
            ],
            colors=["#0077FF", "#FFD91D"],
            axis_titles=AxisTitles(category="Квартал", value="млн ₽"),
            has_legend=True,
            font="Play",
            size_pt=12,
        ),
    )

    page = exported(deck(chart), schema(), tmp_path / "deck.html")

    rects = [
        (float(match.group(1)), float(match.group(2)))
        for match in re.finditer(r'<rect x="([\d.]+)"[^>]*width="([\d.]+)"', page)
    ]
    labels = sorted(
        float(match.group(1))
        for match in re.finditer(r'<text x="([\d.]+)"[^>]*text-anchor="middle">(?:I|II)</text>', page)
    )
    assert len(rects) == 4 and len(labels) == 2

    for index, label in enumerate(labels):
        группа = sorted(rects)[index * 2 : index * 2 + 2]
        левый, правый = группа[0][0], группа[1][0] + группа[1][1]
        assert левый <= label <= правый, "подпись стоит не под своими столбцами"


def test_axis_title_does_not_collide_with_category_labels(tmp_path: Path) -> None:
    """Подпись оси ниже подписей категорий и не налезает на них.

    На пробном рендере обе строки стояли на одной высоте, и слово «Квартал»
    было перечёркнуто римскими цифрами.
    """
    chart = RenderedElement(
        slot_id="body",
        kind="chart",
        bounds=BODY_BOUNDS,
        chart=RenderedChart(
            chart_type="column",
            categories=["I", "II"],
            series=[ChartSeries(name="Выручка", points=[1.0, 2.0])],
            colors=["#0077FF"],
            axis_titles=AxisTitles(category="Квартал", value="млн ₽"),
            has_legend=False,
            font="Play",
            size_pt=12,
        ),
    )

    page = exported(deck(chart), schema(), tmp_path / "deck.html")

    ось = float(re.search(r'<text x="[\d.]+" y="([\d.]+)"[^>]*>Квартал</text>', page).group(1))
    категории = [
        float(match.group(1))
        for match in re.finditer(r'<text x="[\d.]+" y="([\d.]+)"[^>]*>(?:I|II)</text>', page)
    ]
    assert категории, "подписи категорий не найдены"
    assert ось - max(категории) >= 3, "подпись оси наложилась на подписи категорий"


# --- Безопасность разметки -------------------------------------------------


def test_text_cannot_break_the_markup(tmp_path: Path) -> None:
    """Угловые скобки и амперсанд экранируются.

    Содержание колоды — чужой текст: он может содержать что угодно, и
    сломанная разметка означала бы потерю остальных слайдов.
    """
    page = exported(
        deck(text_element("Рост <b>втрое</b> & доля > 50%")),
        schema(),
        tmp_path / "deck.html",
    )

    assert "&lt;b&gt;" in page and "&amp;" in page
    assert "<b>втрое</b>" not in page


# --- Живой шаблон ----------------------------------------------------------


def test_deck_from_a_real_template_keeps_all_its_text(tmp_path: Path) -> None:
    """Сквозная проверка обеих половин критерия на живой колоде."""
    template_file = CALIBRATION / "VK Tech шаблон.pptx"
    if not template_file.exists():
        pytest.skip("нет калибровочного шаблона")

    template = parse_template(template_file)
    structure = PresentationStructure(
        slides=[
            StructureSlide(id="s1", role="title", headline="Итоги пилота"),
            StructureSlide(
                id="s2",
                role="data",
                headline="Что изменилось за квартал",
                body=SlideBody(items=["Срок сборки колоды сократился с девяти дней до трёх"]),
            ),
            StructureSlide(
                id="s3",
                role="data",
                headline="Сравнение",
                visualization=Visualization(
                    kind="table",
                    table=TableSpec(headers=["Метрика", "Было"], rows=[["Срок", "9 дней"]]),
                ),
            ),
            StructureSlide(
                id="s4",
                role="data",
                headline="Динамика",
                visualization=Visualization(
                    kind="chart",
                    chart=ChartSpec(
                        chart_type="column",
                        categories=["I", "II"],
                        series=[ChartSeries(name="Выручка", points=[1.0, 2.0])],
                        axis_titles=AxisTitles(category="Квартал", value="млн ₽"),
                    ),
                ),
            ),
        ]
    )

    for variant in compose_variants(structure, template):
        page = exported(variant, template, tmp_path / f"deck-{variant.variant}.html")

        assert page.count('class="slide"') == 4
        for slide in variant.slides:
            for element in slide.elements:
                for run in element.runs:
                    assert run.text in page, f"вариант {variant.variant}: текст «{run.text}» потерян"
