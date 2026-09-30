"""T-33: фиксер — автоисправление механических находок с откатом.

Критерий приёмки: **находка исправляется, помечается `autofixed`, откат
возвращает исходное**.

Политика D5 определяет поведение по полю `fixability`, и фиксер её исполняет
буквально: `mechanical` правится сам, `lossy` ждёт выбора пользователя,
`semantic` предлагает перегенерацию, `none` только сообщается. Тронуть
находку не своего класса фиксер не вправе — иначе система меняет смысл
слайда без ведома автора.

**Исправление берёт значение из шаблона, а не из головы.** Кегль вне шкалы
заменяется ближайшим из шкалы этого шаблона, цвет вне палитры — ближайшим из
его палитры, чужая гарнитура — его основной. Придуманное значение было бы не
исправлением, а второй ошибкой поверх первой.

**Откат обязателен.** Автоматическая правка без возврата к исходному — это
потеря работы пользователя, и доверия такой системе нет.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dpd.audit import AuditContext, discover, run_checks
from dpd.audit.fixer import apply_fixes
from dpd.layout import compose_variants
from dpd.models import (
    AuditReport,
    AxisTitles,
    Background,
    Bounds,
    Canvas,
    ChartSeries,
    ColorToken,
    DesignTokens,
    Finding,
    FixedElement,
    FontToken,
    Layout,
    PresentationStructure,
    RenderedChart,
    RenderedElement,
    RenderedPresentation,
    Slide,
    SlideBody,
    Slot,
    StructureSlide,
    TemplateSchema,
    TemplateSource,
    TextRun,
    TypeScale,
)
from dpd.parsing import parse_template

CALIBRATION = Path(__file__).resolve().parents[1] / "assets" / "templates" / "calibration"
CANVAS = Canvas(width_emu=12192000, height_emu=6858000)
TITLE_BOUNDS = Bounds(x=0.06, y=0.08, w=0.60, h=0.12)
BODY_BOUNDS = Bounds(x=0.06, y=0.30, w=0.60, h=0.40)


def tokens() -> DesignTokens:
    return DesignTokens(
        fonts=[
            FontToken(role="body", family="Play", confidence=0.9),
            FontToken(role="secondary", family="Consolas", confidence=0.1),
        ],
        colors=[
            ColorToken(role="text.primary", value="#000000", confidence=0.6),
            ColorToken(role="brand.primary", value="#0077FF", confidence=0.3),
        ],
        type_scale=TypeScale(values=[12.0, 14.0, 18.0, 24.0, 48.0]),
    )


def schema(*, fixed_elements: list[FixedElement] | None = None) -> TemplateSchema:
    return TemplateSchema(
        source=TemplateSource(file="synthetic.pptx", hash="0" * 8),
        canvas=CANVAS,
        design_tokens=tokens(),
        layouts=[
            Layout(
                id="l1",
                name="Макет",
                family="content",
                background=Background(kind="solid", value="#FFFFFF", contrast_computable=True),
                slots=[
                    Slot(id="title", kind="title", origin="placeholder", bounds=TITLE_BOUNDS),
                    Slot(id="body", kind="body", origin="placeholder", bounds=BODY_BOUNDS),
                    Slot(id="aside", kind="body", origin="placeholder", bounds=Bounds(x=0.06, y=0.75, w=0.60, h=0.10)),
                ],
                fixed_elements=list(fixed_elements or []),
            )
        ],
    )


def element(
    *runs: TextRun,
    slot_id: str = "body",
    bounds: Bounds | None = None,
    chart: RenderedChart | None = None,
) -> RenderedElement:
    return RenderedElement(
        slot_id=slot_id,
        kind="chart" if chart else "text",
        bounds=bounds or BODY_BOUNDS,
        runs=list(runs),
        chart=chart,
    )


def run(text: str = "Итоги пилота", font: str = "Play", size: float = 14.0, color: str = "#000000") -> TextRun:
    return TextRun(text=text, font=font, size_pt=size, color=color)


def deck(*elements: RenderedElement) -> RenderedPresentation:
    return RenderedPresentation(
        variant="A",
        template_hash="0" * 8,
        canvas=CANVAS,
        slides=[Slide(id="s1", layout_id="l1", elements=list(elements))],
    )


def audited(source: RenderedPresentation, template: TemplateSchema) -> AuditReport:
    discover()
    return run_checks(AuditContext(deck=source, template=template), classes=("file",))


def fixed(source: RenderedPresentation, template: TemplateSchema):
    """Прогнать аудит и применить исправления."""
    return apply_fixes(source, audited(source, template), template)


def only(report: AuditReport, check_id: str) -> Finding:
    found = [item for item in report.findings if item.check_id == check_id]
    assert len(found) == 1, f"ожидалась одна находка {check_id}, получено {len(found)}"
    return found[0]


# --- Критерий приёмки -----------------------------------------------------


def test_mechanical_finding_is_fixed_marked_and_can_be_rolled_back() -> None:
    """Три требования критерия приёмки разом, на кегле вне шкалы."""
    source = deck(element(run(size=16.2)))
    template = schema()

    result = fixed(source, template)

    # Ближайшая ступень шкалы к 16,2 — это 18, а не 14: правка идёт к ближайшему
    # значению шаблона, а не к меньшему.
    assert result.deck.slides[0].elements[0].runs[0].size_pt == 18.0, "кегль не приведён к шкале"
    assert only(result.report, "template.size_not_in_scale").status == "autofixed"
    assert result.rollback().slides[0].elements[0].runs[0].size_pt == 16.2, "откат не вернул исходное"


def test_the_original_deck_is_not_touched() -> None:
    """Исходная колода остаётся нетронутой: правка идёт по копии.

    Иначе откат нечем выполнить — исходного состояния уже не существует.
    """
    source = deck(element(run(size=16.2)))

    fixed(source, schema())

    assert source.slides[0].elements[0].runs[0].size_pt == 16.2


def test_audit_after_the_fix_no_longer_finds_the_defect() -> None:
    """Проверка того, что исправление настоящее, а не пометка в отчёте."""
    source = deck(element(run(size=16.2, font="Comic Sans MS", color="#12FF99")))
    template = schema()

    result = fixed(source, template)
    again = audited(result.deck, template)

    mechanical = [item for item in again.findings if item.fixability == "mechanical"]
    assert mechanical == [], "; ".join(f"{item.check_id} — {item.message}" for item in mechanical)


# --- Что фиксер трогать не вправе ------------------------------------------


@pytest.mark.parametrize(
    ("fixability", "check_id"),
    [
        ("lossy", "density.too_many_bullets"),
        ("semantic", "integrity.placeholder_text"),
        ("none", "template.layout_not_from_template"),
    ],
)
def test_findings_of_other_classes_are_left_alone(fixability: str, check_id: str) -> None:
    """Политика D5: сам правится только `mechanical`.

    Находка с потерями требует выбора пользователя, смысловая — перегенерации,
    `none` не правится вовсе. Тронуть их автоматически значило бы менять
    смысл слайда без ведома автора.
    """
    findings = [
        Finding(
            check_id=check_id,
            category="density",
            check_class="file",
            severity="warning",
            fixability=fixability,
            message="проверочная находка",
            slide_number=1,
            slot_id="body",
        )
    ]
    source = deck(element(run()))

    result = apply_fixes(source, AuditReport(run_id="t", findings=findings), schema())

    assert result.report.findings[0].status == "open"
    assert result.applied == []


# --- Оформление: значение берётся из шаблона -------------------------------


def test_colour_outside_the_palette_becomes_the_nearest_of_it() -> None:
    """Ближайший по ΔE, а не первый попавшийся: правка не должна менять замысел."""
    result = fixed(deck(element(run(color="#0F7AF5"))), schema())

    assert result.deck.slides[0].elements[0].runs[0].color == "#0077FF"
    assert only(result.report, "template.color_not_in_palette").status == "autofixed"


def test_font_outside_the_template_becomes_the_main_one() -> None:
    result = fixed(deck(element(run(font="Comic Sans MS"))), schema())

    assert result.deck.slides[0].elements[0].runs[0].font == "Play"


def test_third_face_on_a_slide_gives_way_to_the_main_one() -> None:
    """Лишние гарнитуры уступают основной, две самые частые остаются."""
    source = deck(
        element(run(font="Play"), slot_id="title", bounds=TITLE_BOUNDS),
        element(run(font="Consolas"), slot_id="body"),
        element(run(font="Consolas"), slot_id="aside", bounds=Bounds(x=0.06, y=0.75, w=0.60, h=0.10)),
    )
    source.slides[0].elements[1].runs.append(TextRun(text="ещё", font="Courier New", size_pt=14, color="#000000"))

    result = fixed(source, schema())

    faces = {run.font for item in result.deck.slides[0].elements for run in item.runs}
    assert faces == {"Play", "Consolas"}


# --- Геометрия -------------------------------------------------------------


def test_element_past_the_canvas_returns_to_its_slot() -> None:
    """Блок возвращается туда, где ему место по макету.

    Прижать к краю можно было бы и без шаблона, но правильное положение он
    знает: это слот, из которого блок уехал. Придумывать своё значило бы
    решать за автора макета.
    """
    source = deck(element(run(), bounds=Bounds(x=0.7, y=0.3, w=0.5, h=0.2)))

    result = fixed(source, schema())
    bounds = result.deck.slides[0].elements[0].bounds

    assert bounds.x + bounds.w <= 1.0 and bounds.y + bounds.h <= 1.0
    assert (bounds.x, bounds.y, bounds.w, bounds.h) == pytest.approx(
        (BODY_BOUNDS.x, BODY_BOUNDS.y, BODY_BOUNDS.w, BODY_BOUNDS.h)
    )
    assert only(result.report, "layout.out_of_bounds").status == "autofixed"


def test_element_past_the_canvas_without_a_slot_is_pressed_to_the_edge() -> None:
    """Когда места по макету нет, блок прижимается к краю.

    Хуже, чем по макету, но лучше, чем за кадром: пользователь видит блок
    целиком и может переставить его сам.
    """
    source = deck(element(run(), slot_id="нет-такого", bounds=Bounds(x=0.7, y=0.3, w=0.5, h=0.2)))

    result = fixed(source, schema())
    bounds = result.deck.slides[0].elements[0].bounds

    assert bounds.x + bounds.w == pytest.approx(1.0)
    assert bounds.w == pytest.approx(0.5), "ширина не меняется, если блок помещается"


def test_element_inside_the_margins_is_pushed_out_of_them() -> None:
    source = deck(element(run(), bounds=Bounds(x=0.004, y=0.30, w=0.40, h=0.40)))

    result = fixed(source, schema())

    assert result.deck.slides[0].elements[0].bounds.x >= 0.06 - 1e-6


def test_block_off_the_guide_is_snapped_to_it() -> None:
    """Направляющая макета — 0,06: её разделяют все три слота."""
    source = deck(element(run(), bounds=Bounds(x=0.09, y=0.30, w=0.40, h=0.40)))

    result = fixed(source, schema())

    assert result.deck.slides[0].elements[0].bounds.x == pytest.approx(0.06, abs=1e-6)


def test_content_over_a_fixed_element_returns_to_its_slot() -> None:
    """Блок, уехавший с отведённого места на логотип, возвращается в слот.

    Место, куда его вернуть, известно из шаблона. Придумывать другое —
    значит решать за автора макета.
    """
    logo = FixedElement(kind="logo", bounds=Bounds(x=0.06, y=0.34, w=0.12, h=0.08), confidence=0.8)
    # Блок съехал вниз со своего слота: поля и направляющая при этом не нарушены,
    # и находка о логотипе остаётся единственной.
    source = deck(element(run(), bounds=Bounds(x=0.06, y=0.35, w=0.30, h=0.20)))

    result = fixed(source, schema(fixed_elements=[logo]))

    assert result.deck.slides[0].elements[0].bounds.x == pytest.approx(BODY_BOUNDS.x)
    assert result.deck.slides[0].elements[0].bounds.y == pytest.approx(BODY_BOUNDS.y)


def test_second_geometry_fix_on_the_same_block_waits_for_the_next_pass() -> None:
    """Одно исправление геометрии на блок за проход.

    Находка описывает то положение блока, которое было на момент аудита.
    Если блок уже переставлен, её доказательство устарело: вторая правка
    поверх первой ужимает блок, а не чинит его — на пробном прогоне ширина
    падала с 0,5 до 0,16. Такая находка остаётся открытой и попадёт в
    следующий проход, когда аудит пересчитает её заново.
    """
    source = deck(element(run(), bounds=Bounds(x=0.7, y=0.3, w=0.5, h=0.2)))

    result = fixed(source, schema())

    geometry = [
        item
        for item in result.report.findings
        if item.check_id in {"layout.out_of_bounds", "layout.margin_violation", "layout.guide_misalign"}
    ]
    autofixed = [item for item in geometry if item.status == "autofixed"]
    assert len(autofixed) == 1, "за один проход блок переставляется один раз"
    assert result.deck.slides[0].elements[0].bounds.w == pytest.approx(BODY_BOUNDS.w)


# --- Где механическое исправление невозможно -------------------------------


def test_missing_legend_is_switched_on() -> None:
    chart = RenderedChart(
        chart_type="column",
        categories=["I", "II"],
        series=[ChartSeries(name="Выручка", points=[1.0, 2.0]), ChartSeries(name="Затраты", points=[1.0, 2.0])],
        colors=["#0077FF", "#000000"],
        axis_titles=AxisTitles(category="Квартал", value="млн ₽"),
        has_legend=False,
        font="Play",
        size_pt=12,
    )
    result = fixed(deck(element(chart=chart)), schema())

    assert result.deck.slides[0].elements[0].chart.has_legend is True
    assert only(result.report, "integrity.chart_no_labels").status == "autofixed"


def test_missing_axis_titles_are_not_invented() -> None:
    """Единицы измерения — смысл, а не форма: выдумать их фиксер не вправе.

    Находка остаётся открытой, и это честнее, чем подписать ось словом
    «значение» и отчитаться об исправлении.
    """
    chart = RenderedChart(
        chart_type="column",
        categories=["I", "II"],
        series=[ChartSeries(name="Выручка", points=[1.0, 2.0])],
        colors=["#0077FF"],
        axis_titles=AxisTitles(),
        has_legend=False,
        font="Play",
        size_pt=12,
    )
    result = fixed(deck(element(chart=chart)), schema())

    finding = only(result.report, "integrity.chart_no_labels")
    assert finding.status == "open"
    assert result.deck.slides[0].elements[0].chart.axis_titles.value is None


# --- Живой шаблон ----------------------------------------------------------


def test_broken_deck_from_a_real_template_is_repaired_and_can_be_rolled_back() -> None:
    """Сквозной путь: дефект → аудит → исправление → повторный аудит → откат."""
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
        ]
    )
    source = compose_variants(structure, template)[0]

    broken = source.model_copy(deep=True)
    spoiled = broken.slides[1].elements[-1].runs[0]
    spoiled.font, spoiled.size_pt, spoiled.color = "Comic Sans MS", 16.2, "#12FF99"

    before = audited(broken, template)
    assert {item.check_id for item in before.findings} >= {
        "template.font_not_in_set",
        "template.size_not_in_scale",
        "template.color_not_in_palette",
    }

    result = apply_fixes(broken, before, template)
    after = audited(result.deck, template)

    assert not [item for item in after.findings if item.fixability == "mechanical"], (
        "; ".join(f"{item.check_id} — {item.message}" for item in after.findings)
    )
    assert all(item.status == "autofixed" for item in result.report.findings if item.check_id in {
        "template.font_not_in_set",
        "template.size_not_in_scale",
        "template.color_not_in_palette",
    })

    restored = result.rollback()
    assert restored.slides[1].elements[-1].runs[0].font == "Comic Sans MS"
