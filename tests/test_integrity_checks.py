"""T-31: шесть проверок целостности.

Критерий приёмки: на слайде с заведомым дефектом каждая срабатывает, на
чистом — нет; `integrity.file_broken` и `integrity.raster_slide` работают
после экспорта.

`raster_slide` написана задачей T-10 и вобрана каркасом T-27 — здесь она
только сверяется с чек-листом заодно с остальными: категория должна быть
собрана целиком, а не наполовину.

Две проверки требуют оговорок, и они закреплены тестами:

- **Титульный слайд не пуст, а таков по жанру.** Вопрос 5 валидации контента
  («есть ли на слайде содержание, а не только заголовок») для титула и
  разделителя отвечается «нет» по замыслу, и находка о них была бы
  претензией к жанру.
- **Подписи осей — смысл, а не форма.** Вёрстка их не выдумывает: единицы
  измерения приходят из структуры колоды, то есть от модели. Проверка ловит
  диаграмму, до которой этот смысл не дошёл.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pptx import Presentation as PptxPresentation

from dpd.audit import REGISTRY, AuditContext, discover, run_checks
from dpd.layout import compose_variants
from dpd.models import (
    AxisTitles,
    Bounds,
    Canvas,
    ChartSeries,
    ChartSpec,
    Layout,
    LayoutDecision,
    PresentationStructure,
    RenderedChart,
    RenderedElement,
    RenderedPresentation,
    RenderedTable,
    Slide,
    SlideBody,
    Slot,
    StructureSlide,
    TemplateSchema,
    TemplateSource,
    TextRun,
    Visualization,
)
from dpd.parsing import parse_template

CALIBRATION = Path(__file__).resolve().parents[1] / "assets" / "templates" / "calibration"
CANVAS = Canvas(width_emu=12192000, height_emu=6858000)

TITLE_BOUNDS = Bounds(x=0.06, y=0.08, w=0.60, h=0.12)
BODY_BOUNDS = Bounds(x=0.06, y=0.26, w=0.60, h=0.50)

CHECKLIST = {
    "integrity.file_broken": ("file", "critical", "none"),
    "integrity.placeholder_text": ("file", "critical", "semantic"),
    "integrity.empty_slide": ("file", "critical", "semantic"),
    "integrity.raster_slide": ("file", "critical", "none"),
    "integrity.chart_no_labels": ("file", "warning", "mechanical"),
    "integrity.duplicate_slides": ("file", "warning", "none"),
}


def schema(layout_id: str = "l1") -> TemplateSchema:
    return TemplateSchema(
        source=TemplateSource(file="synthetic.pptx", hash="0" * 8),
        canvas=CANVAS,
        layouts=[
            Layout(
                id=layout_id,
                name="Макет",
                family="content",
                slots=[
                    Slot(id="title", kind="title", origin="placeholder", bounds=TITLE_BOUNDS),
                    Slot(id="body", kind="body", origin="placeholder", bounds=BODY_BOUNDS),
                ],
            )
        ],
    )


def text(slot_id: str, *paragraphs: str, bounds: Bounds | None = None) -> RenderedElement:
    return RenderedElement(
        slot_id=slot_id,
        kind="text",
        bounds=bounds or BODY_BOUNDS,
        runs=[TextRun(text=item, font="Play", size_pt=18) for item in paragraphs],
    )


def title(value: str = "Итоги пилота") -> RenderedElement:
    return text("title", value, bounds=TITLE_BOUNDS)


def chart_element(
    *,
    series: int = 2,
    legend: bool = True,
    axis: AxisTitles | None = None,
    chart_type: str = "column",
) -> RenderedElement:
    return RenderedElement(
        slot_id="body",
        kind="chart",
        bounds=BODY_BOUNDS,
        chart=RenderedChart(
            chart_type=chart_type,
            categories=["I", "II"],
            series=[ChartSeries(name=f"ряд {index}", points=[1.0, 2.0]) for index in range(series)],
            colors=["#0077FF", "#FFD91D"],
            axis_titles=axis or AxisTitles(),
            has_legend=legend,
            font="Play",
            size_pt=12,
        ),
    )


def deck(*elements: RenderedElement, family: str = "content", layout_id: str = "l1") -> RenderedPresentation:
    return RenderedPresentation(
        variant="A",
        template_hash="0" * 8,
        canvas=CANVAS,
        slides=[
            Slide(
                id="s1",
                layout_id=layout_id,
                layout_decision=LayoutDecision(
                    requested_family="content", chosen_family=family, layout_id=layout_id
                ),
                elements=list(elements),
            )
        ],
    )


def findings(check_id: str, context: AuditContext) -> list:
    discover()
    report = run_checks(context)
    assert check_id in report.checks_run, f"{check_id} не выполнилась: {report.checks_skipped}"
    return [item for item in report.findings if item.check_id == check_id]


# --- Реестр --------------------------------------------------------------


@pytest.mark.parametrize("check_id", sorted(CHECKLIST))
def test_check_is_registered_as_the_checklist_describes(check_id: str) -> None:
    """Категория целостности собрана целиком, включая написанную в T-10."""
    discover()
    registered = REGISTRY.get(check_id)
    assert registered is not None, f"{check_id} не зарегистрирована"

    spec = registered.spec
    assert (spec.check_class, spec.severity, spec.fixability) == CHECKLIST[check_id]
    assert spec.category == "integrity"


# --- integrity.file_broken ------------------------------------------------


def test_file_broken_fires_on_a_file_that_does_not_open(tmp_path: Path) -> None:
    """Выгруженный файл, который не открывается, обесценивает весь прогон."""
    broken = tmp_path / "broken.pptx"
    broken.write_bytes("PK\x03\x04 не презентация, а мусор".encode())

    found = findings("integrity.file_broken", AuditContext(pptx_path=broken))

    assert len(found) == 1
    assert found[0].severity == "critical" and found[0].fixability == "none"
    assert found[0].evidence["error"]


def test_file_broken_is_silent_on_a_valid_package(tmp_path: Path) -> None:
    valid = tmp_path / "valid.pptx"
    PptxPresentation().save(str(valid))

    assert findings("integrity.file_broken", AuditContext(pptx_path=valid)) == []


def test_file_broken_runs_after_export_not_before() -> None:
    """Обе файловые проверки без файла не выполняются и молчать не должны.

    Пропуск называется в отчёте: пустой список находок иначе неотличим от
    проверенного файла, которого на самом деле ещё нет.
    """
    discover()
    report = run_checks(AuditContext(deck=deck(title(), text("body", "Содержание")), template=schema()))

    assert "integrity.file_broken" in report.checks_skipped
    assert "integrity.raster_slide" in report.checks_skipped


# --- integrity.placeholder_text -------------------------------------------


def test_placeholder_text_fires_on_a_leftover_stub() -> None:
    """Заглушка в выгруженной колоде — след недоделанной работы."""
    found = findings(
        "integrity.placeholder_text",
        AuditContext(deck=deck(title(), text("body", "Lorem ipsum dolor sit amet")), template=schema()),
    )

    assert len(found) == 1
    assert found[0].evidence["marker"] == "lorem ipsum"
    assert found[0].fixability == "semantic"


def test_placeholder_text_fires_on_an_unfilled_template_bracket() -> None:
    """Незакрытые шаблонные скобки — след промпта, а не текст слайда."""
    found = findings(
        "integrity.placeholder_text",
        AuditContext(deck=deck(title(), text("body", "Выручка выросла на {{growth}}")), template=schema()),
    )

    assert len(found) == 1


def test_placeholder_text_looks_inside_tables() -> None:
    """Заглушка в ячейке таблицы так же видна зрителю, как и в абзаце."""
    table = RenderedElement(
        slot_id="body",
        kind="table",
        bounds=BODY_BOUNDS,
        table=RenderedTable(headers=["Метрика", "Значение"], rows=[["Срок", "TODO"]], font="Play", size_pt=12),
    )
    found = findings(
        "integrity.placeholder_text",
        AuditContext(deck=deck(title(), table), template=schema()),
    )

    assert len(found) == 1
    assert found[0].evidence["marker"] == "todo"


def test_placeholder_text_is_silent_on_real_content() -> None:
    found = findings(
        "integrity.placeholder_text",
        AuditContext(
            deck=deck(title(), text("body", "Срок сборки колоды сократился втрое")),
            template=schema(),
        ),
    )

    assert found == []


# --- integrity.empty_slide ------------------------------------------------


def test_empty_slide_fires_on_a_slide_with_only_a_headline() -> None:
    """Вопрос 5 валидации контента, решённый алгоритмом, а не моделью."""
    found = findings(
        "integrity.empty_slide",
        AuditContext(deck=deck(title()), template=schema()),
    )

    assert len(found) == 1
    assert found[0].severity == "critical" and found[0].fixability == "semantic"


def test_empty_slide_fires_on_a_slide_whose_content_is_blank() -> None:
    """Слот есть, содержимого нет: пустая строка — не содержание."""
    found = findings(
        "integrity.empty_slide",
        AuditContext(deck=deck(title(), text("body", "   ")), template=schema()),
    )

    assert len(found) == 1


def test_empty_slide_is_silent_when_the_slide_has_content() -> None:
    found = findings(
        "integrity.empty_slide",
        AuditContext(deck=deck(title(), text("body", "Срок сборки сократился втрое")), template=schema()),
    )

    assert found == []


def test_empty_slide_counts_a_visualisation_as_content() -> None:
    """Диаграмма — содержание слайда, хотя текстовых прогонов в ней нет."""
    found = findings(
        "integrity.empty_slide",
        AuditContext(
            deck=deck(title(), chart_element(axis=AxisTitles(category="Квартал", value="млн ₽"))),
            template=schema(),
        ),
    )

    assert found == []


def test_empty_slide_ignores_title_slides() -> None:
    """Титул и разделитель состоят из одного заголовка по замыслу жанра."""
    assert findings("integrity.empty_slide", AuditContext(deck=deck(title(), family="title"), template=schema())) == []
    assert findings("integrity.empty_slide", AuditContext(deck=deck(title(), family="section"), template=schema())) == []


# --- integrity.chart_no_labels --------------------------------------------


def test_chart_no_labels_fires_without_axis_titles() -> None:
    """Диаграмма без единиц измерения не сообщает ничего.

    Правило наше: эталона в шаблонах нет — 138 слайдов-примеров, ноль
    диаграмм. Обоснование в чек-листе, QR-4.
    """
    found = findings(
        "integrity.chart_no_labels",
        AuditContext(deck=deck(title(), chart_element()), template=schema()),
    )

    assert len(found) == 1
    assert "оси" in found[0].evidence["missing"]


def test_chart_no_labels_fires_when_several_series_have_no_legend() -> None:
    """Два ряда без легенды неразличимы: цвет ни о чём не говорит."""
    found = findings(
        "integrity.chart_no_labels",
        AuditContext(
            deck=deck(
                title(),
                chart_element(series=2, legend=False, axis=AxisTitles(category="Квартал", value="млн ₽")),
            ),
            template=schema(),
        ),
    )

    assert len(found) == 1
    assert "легенда" in found[0].evidence["missing"]


def test_chart_no_labels_is_silent_on_a_labelled_chart() -> None:
    found = findings(
        "integrity.chart_no_labels",
        AuditContext(
            deck=deck(title(), chart_element(axis=AxisTitles(category="Квартал", value="млн ₽"))),
            template=schema(),
        ),
    )

    assert found == []


def test_chart_no_labels_does_not_demand_axes_from_a_pie() -> None:
    """У круговой диаграммы осей нет, и требовать их подписи бессмысленно.

    Вёрстка их и не ставит (T-24): цветом там различаются точки, а не ряды.
    """
    found = findings(
        "integrity.chart_no_labels",
        AuditContext(
            deck=deck(title(), chart_element(chart_type="pie", series=1, legend=True)),
            template=schema(),
        ),
    )

    assert found == []


# --- integrity.duplicate_slides -------------------------------------------


def structure(*slides: StructureSlide) -> PresentationStructure:
    return PresentationStructure(slides=list(slides))


def test_duplicate_slides_fires_on_two_near_identical_slides() -> None:
    """Дубль — работа впустую и на показе, и при правке."""
    found = findings(
        "integrity.duplicate_slides",
        AuditContext(
            structure=structure(
                StructureSlide(
                    id="s1",
                    role="data",
                    headline="Итоги пилота",
                    body=SlideBody(items=["Срок сборки сократился втрое", "Правки переживают сохранение"]),
                ),
                StructureSlide(
                    id="s2",
                    role="data",
                    headline="Итоги пилота",
                    body=SlideBody(items=["Срок сборки сократился втрое", "Правки переживают сохранение."]),
                ),
            )
        ),
    )

    assert len(found) == 1
    assert found[0].fixability == "none", "решение о слиянии принимает человек"
    assert found[0].evidence["similarity"] > 0.9


def test_duplicate_slides_is_silent_on_different_slides() -> None:
    found = findings(
        "integrity.duplicate_slides",
        AuditContext(
            structure=structure(
                StructureSlide(id="s1", role="title", headline="Итоги пилота"),
                StructureSlide(
                    id="s2",
                    role="data",
                    headline="Что изменилось за квартал",
                    body=SlideBody(items=["Аудит ловит дефекты формы до показа заказчику"]),
                ),
            )
        ),
    )

    assert found == []


# --- Вторая половина критерия на живых данных -----------------------------


@pytest.mark.parametrize(
    "name",
    [
        "VK Tech шаблон.pptx",
        "VK_WorkSpace_Клиентская_конференция_Шаблон_03.pptx",
        "Шаблон презентации VK Education.pptx",
    ],
)
def test_clean_deck_from_a_real_template_raises_no_integrity_findings(name: str) -> None:
    """На честно свёрстанной колоде проверки целостности молчат.

    Колода содержит титул, текстовый слайд и диаграмму с подписанными осями —
    то есть все случаи, которые проверки этой категории разбирают.
    """
    template_file = CALIBRATION / name
    if not template_file.exists():
        pytest.skip(f"нет калибровочного шаблона {name}")

    template = parse_template(template_file)
    deck_structure = PresentationStructure(
        slides=[
            StructureSlide(id="s1", role="title", headline="Итоги пилота"),
            StructureSlide(
                id="s2",
                role="data",
                headline="Что изменилось за квартал",
                body=SlideBody(
                    items=[
                        "Срок сборки колоды сократился с девяти рабочих дней до трёх",
                        "Правки текста переживают сохранение файла и повторное открытие",
                        "Аудит ловит дефекты формы до того, как их увидит заказчик",
                    ]
                ),
            ),
            StructureSlide(
                id="s3",
                role="data",
                headline="Динамика выручки",
                visualization=Visualization(
                    kind="chart",
                    chart=ChartSpec(
                        chart_type="column",
                        categories=["I квартал", "II квартал"],
                        series=[ChartSeries(name="Выручка", points=[1.0, 2.0])],
                        axis_titles=AxisTitles(category="Квартал", value="млн ₽"),
                    ),
                ),
            ),
        ]
    )

    discover()
    for variant in compose_variants(deck_structure, template):
        report = run_checks(
            AuditContext(deck=variant, template=template, structure=deck_structure), classes=("file",)
        )
        integrity = [item for item in report.findings if item.category == "integrity"]
        assert integrity == [], (
            f"вариант {variant.variant}: "
            + "; ".join(f"{item.check_id} — {item.message}" for item in integrity)
        )
