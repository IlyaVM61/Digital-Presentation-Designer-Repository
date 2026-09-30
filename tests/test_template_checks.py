"""T-29: шесть проверок шаблона.

Критерий приёмки задачи: на слайде с заведомым дефектом каждая срабатывает,
на чистом — нет; `template.too_many_faces` считает гарнитуры **на слайде**, а
не в шаблоне. Последнее проверяется отдельно: шаблон вправе содержать хоть
десять гарнитур, а слайд, набранный тремя, выглядит собранным наспех.

Проверки этой категории сверяют оформление слайда с правилами, выведенными
из разметки шаблона. Отсюда две особенности, закреплённые тестами:

- **Незаполненное свойство не нарушение.** До 59% текстовых прогонов реальных
  шаблонов не несут явной гарнитуры и кегля: их разрешает наследование, и
  требовать их значило бы объявить дефектом нормальный шаблон.
- **Уверенность правила передаётся находке.** Правило выведено нами из
  частотного анализа. Там, где шаблон размечен вразнобой и правило слабое,
  то же отклонение показывается рекомендацией, а не нарушением.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dpd.audit import REGISTRY, AuditContext, discover, run_checks
from dpd.layout import compose_variants
from dpd.models import (
    Bounds,
    Canvas,
    ChartSeries,
    ChartSpec,
    ColorToken,
    DesignTokens,
    FixedElement,
    FontToken,
    Layout,
    PresentationStructure,
    RenderedElement,
    RenderedPresentation,
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

# Классификация из `docs/07-testing/audit-checklist.md`.
CHECKLIST = {
    "template.font_not_in_set": ("file", "warning", "mechanical"),
    "template.too_many_faces": ("file", "warning", "mechanical"),
    "template.size_not_in_scale": ("file", "warning", "mechanical"),
    "template.color_not_in_palette": ("file", "warning", "mechanical"),
    "template.layout_not_from_template": ("file", "critical", "none"),
    "template.fixed_element_moved": ("file", "advice", "mechanical"),
}


def tokens(
    *,
    fonts: list[tuple[str, float]] = (("Play", 0.9), ("Consolas", 0.08), ("Arial", 0.02)),
    colors: list[tuple[str, float]] = (("#000000", 0.6), ("#0077FF", 0.3)),
    scale: list[float] = (12.0, 14.0, 18.0, 24.0, 48.0),
) -> DesignTokens:
    return DesignTokens(
        fonts=[FontToken(role="body", family=family, confidence=value) for family, value in fonts],
        colors=[ColorToken(role="text.primary", value=value, confidence=share) for value, share in colors],
        type_scale=TypeScale(values=list(scale)),
    )


def schema(
    *,
    design_tokens: DesignTokens | None = None,
    fixed_elements: list[FixedElement] | None = None,
    layout_id: str = "l1",
) -> TemplateSchema:
    return TemplateSchema(
        source=TemplateSource(file="synthetic.pptx", hash="0" * 8),
        canvas=CANVAS,
        design_tokens=design_tokens if design_tokens is not None else tokens(),
        layouts=[
            Layout(
                id=layout_id,
                name="Макет",
                family="content",
                slots=[Slot(id="body", kind="body", origin="placeholder", bounds=Bounds(x=0.1, y=0.2, w=0.6, h=0.5))],
                fixed_elements=list(fixed_elements or []),
            )
        ],
    )


def element(
    *runs: TextRun,
    slot_id: str = "body",
    x: float = 0.1,
    y: float = 0.2,
    w: float = 0.6,
    h: float = 0.5,
) -> RenderedElement:
    return RenderedElement(
        slot_id=slot_id,
        kind="text",
        bounds=Bounds(x=x, y=y, w=w, h=h),
        runs=list(runs),
    )


def run(text: str = "Итоги", font: str | None = "Play", size: float | None = 14.0, color: str | None = "#000000") -> TextRun:
    return TextRun(text=text, font=font, size_pt=size, color=color)


def deck(*elements: RenderedElement, layout_id: str = "l1") -> RenderedPresentation:
    return RenderedPresentation(
        variant="A",
        template_hash="0" * 8,
        canvas=CANVAS,
        slides=[Slide(id="s1", layout_id=layout_id, elements=list(elements))],
    )


def findings(check_id: str, context: AuditContext) -> list:
    discover()
    report = run_checks(context)
    assert check_id in report.checks_run, f"{check_id} не выполнилась: {report.checks_skipped}"
    return [item for item in report.findings if item.check_id == check_id]


# --- Реестр --------------------------------------------------------------


@pytest.mark.parametrize("check_id", sorted(CHECKLIST))
def test_check_is_registered_as_the_checklist_describes(check_id: str) -> None:
    discover()
    registered = REGISTRY.get(check_id)
    assert registered is not None, f"{check_id} не зарегистрирована"

    spec = registered.spec
    assert (spec.check_class, spec.severity, spec.fixability) == CHECKLIST[check_id]
    assert spec.category == "template"


# --- template.font_not_in_set ---------------------------------------------


def test_font_not_in_set_fires_on_a_font_absent_from_the_template() -> None:
    """Гарнитура не из шаблона выдаёт слайд, собранный мимо его правил."""
    found = findings(
        "template.font_not_in_set",
        AuditContext(deck=deck(element(run(font="Comic Sans MS"))), template=schema()),
    )

    assert len(found) == 1
    assert "Comic Sans MS" in found[0].message
    assert found[0].evidence["actual"] == "Comic Sans MS"


def test_font_not_in_set_is_silent_on_a_font_from_the_template() -> None:
    found = findings(
        "template.font_not_in_set",
        AuditContext(deck=deck(element(run(font="Consolas"))), template=schema()),
    )

    assert found == []


def test_font_not_in_set_is_silent_when_the_font_is_inherited() -> None:
    """Пустая гарнитура — не нарушение, а наследование.

    До 59% текстовых прогонов реальных шаблонов не несут явного шрифта:
    его разрешает цепочка прогон → абзац → макет → мастер → тема.
    """
    found = findings(
        "template.font_not_in_set",
        AuditContext(deck=deck(element(run(font=None))), template=schema()),
    )

    assert found == []


# --- template.too_many_faces ----------------------------------------------


def test_too_many_faces_counts_fonts_on_the_slide_not_in_the_template() -> None:
    """Критерий приёмки задачи: счёт идёт по слайду.

    В шаблоне гарнитур может быть сколько угодно — здесь их три, и все они
    его собственные. Дефект в том, что три сошлись на одном слайде.
    """
    found = findings(
        "template.too_many_faces",
        AuditContext(
            deck=deck(
                element(run(font="Play"), slot_id="title"),
                element(run(font="Consolas"), slot_id="body"),
                element(run(font="Arial"), slot_id="note"),
            ),
            template=schema(),
        ),
    )

    assert len(found) == 1
    assert found[0].evidence["actual"] == 3
    assert found[0].slide_number == 1


def test_too_many_faces_is_silent_on_two_fonts() -> None:
    """Две гарнитуры — норма: заголовочная и текстовая."""
    found = findings(
        "template.too_many_faces",
        AuditContext(
            deck=deck(
                element(run(font="Play"), slot_id="title"),
                element(run(font="Consolas"), slot_id="body"),
            ),
            template=schema(),
        ),
    )

    assert found == []


# --- template.size_not_in_scale -------------------------------------------


def test_size_not_in_scale_fires_on_a_size_outside_the_scale() -> None:
    """Кегль вне шкалы — след ручной правки или автоподгонки."""
    found = findings(
        "template.size_not_in_scale",
        AuditContext(deck=deck(element(run(size=16.2))), template=schema()),
    )

    assert len(found) == 1
    assert found[0].evidence["actual"] == 16.2
    assert 14.0 in found[0].evidence["expected"]


def test_size_not_in_scale_is_silent_within_the_tolerance() -> None:
    """Допуск 0,1 pt: кегли хранятся в сотых долях пункта, и точного совпадения не будет."""
    found = findings(
        "template.size_not_in_scale",
        AuditContext(deck=deck(element(run(size=14.05))), template=schema()),
    )

    assert found == []


# --- template.color_not_in_palette ----------------------------------------


def test_color_not_in_palette_fires_on_a_colour_outside_the_palette() -> None:
    found = findings(
        "template.color_not_in_palette",
        AuditContext(deck=deck(element(run(color="#FF00FF"))), template=schema()),
    )

    assert len(found) == 1
    assert found[0].evidence["actual"] == "#FF00FF"


def test_color_not_in_palette_is_silent_on_a_barely_different_colour() -> None:
    """Допуск ΔE ≤ 2: глаз такой разницы не видит, а экспорт округляет цвета."""
    found = findings(
        "template.color_not_in_palette",
        AuditContext(deck=deck(element(run(color="#0177FE"))), template=schema()),
    )

    assert found == []


def test_color_not_in_palette_fires_on_colours_of_tables_and_charts() -> None:
    """Проверяется всё оформление слайда, а не только текстовые прогоны.

    Диаграмма в чужих цветах выдаёт, что слайд собран не по шаблону, даже
    если весь остальной слайд безупречен.
    """
    from dpd.models import RenderedChart

    chart = RenderedElement(
        slot_id="body",
        kind="chart",
        bounds=Bounds(x=0.1, y=0.2, w=0.6, h=0.5),
        chart=RenderedChart(chart_type="column", colors=["#0077FF", "#12FF99"], font="Play", size_pt=14.0),
    )
    found = findings("template.color_not_in_palette", AuditContext(deck=deck(chart), template=schema()))

    assert len(found) == 1
    assert found[0].evidence["actual"] == "#12FF99"


# --- template.layout_not_from_template ------------------------------------


def test_layout_not_from_template_fires_on_an_unknown_layout() -> None:
    """Слайд на чужом макете — не слайд этого шаблона.

    Исправимость `none`: подобрать замену автоматически нельзя, это решение
    о композиции, а не о свойстве.
    """
    found = findings(
        "template.layout_not_from_template",
        AuditContext(deck=deck(element(run()), layout_id="чужой-макет"), template=schema()),
    )

    assert len(found) == 1
    assert found[0].severity == "critical" and found[0].fixability == "none"


def test_layout_not_from_template_is_silent_on_a_layout_of_the_template() -> None:
    found = findings(
        "template.layout_not_from_template",
        AuditContext(deck=deck(element(run())), template=schema()),
    )

    assert found == []


# --- template.fixed_element_moved -----------------------------------------


def logo(confidence: float = 0.8) -> FixedElement:
    return FixedElement(kind="logo", bounds=Bounds(x=0.85, y=0.03, w=0.12, h=0.08), confidence=confidence)


def test_fixed_element_moved_fires_when_content_takes_its_place() -> None:
    """Содержимое, занявшее место логотипа, — дефект, который был у нас на рендере."""
    found = findings(
        "template.fixed_element_moved",
        AuditContext(
            deck=deck(element(run(), x=0.80, y=0.01, w=0.19, h=0.15)),
            template=schema(fixed_elements=[logo()]),
        ),
    )

    assert len(found) == 1
    assert found[0].severity == "advice"
    assert found[0].confidence == pytest.approx(0.8)


def test_fixed_element_moved_is_silent_when_content_keeps_away() -> None:
    found = findings(
        "template.fixed_element_moved",
        AuditContext(
            deck=deck(element(run(), x=0.1, y=0.3, w=0.5, h=0.4)),
            template=schema(fixed_elements=[logo()]),
        ),
    )

    assert found == []


def test_fixed_element_moved_ignores_unreliable_canonical_positions() -> None:
    """Порог уверенности 0,7 из чек-листа.

    Канонические позиции постоянных элементов негде взять надёжно: оба
    мастера VK Tech пусты, изображения разбросаны по макетам. Обвинять в
    нарушении правила, выведенного с уверенностью 0,5, нельзя.
    """
    found = findings(
        "template.fixed_element_moved",
        AuditContext(
            deck=deck(element(run(), x=0.80, y=0.01, w=0.19, h=0.15)),
            template=schema(fixed_elements=[logo(confidence=0.5)]),
        ),
    )

    assert found == []


# --- Уверенность правила --------------------------------------------------


def test_weak_rule_is_reported_as_advice_not_as_a_violation() -> None:
    """Правило, выведенное из разнобоя, не может служить обвинением.

    Уверенность находки наследуется от ведущего токена: она говорит,
    насколько единообразно размечен шаблон. Там, где ведущая гарнитура
    набирает пятую часть разметки, шаблон о своих правилах почти ничего не
    сообщает, и то же отклонение — рекомендация, а не нарушение.
    """
    weak = tokens(fonts=[("Play", 0.2), ("Arial", 0.18), ("Consolas", 0.15)])
    found = findings(
        "template.font_not_in_set",
        AuditContext(deck=deck(element(run(font="Comic Sans MS"))), template=schema(design_tokens=weak)),
    )

    assert len(found) == 1
    assert found[0].severity == "advice", "слабое правило предъявлено как нарушение"
    assert found[0].confidence == pytest.approx(0.2)


def test_strong_rule_is_reported_as_a_violation() -> None:
    strong = tokens(fonts=[("Play", 0.95), ("Consolas", 0.05)])
    found = findings(
        "template.font_not_in_set",
        AuditContext(deck=deck(element(run(font="Comic Sans MS"))), template=schema(design_tokens=strong)),
    )

    assert found[0].severity == "warning"


# --- Вторая половина критерия на живых данных -----------------------------


@pytest.mark.parametrize(
    "name",
    [
        "VK Tech шаблон.pptx",
        "VK_WorkSpace_Клиентская_конференция_Шаблон_03.pptx",
        "Шаблон презентации VK Education.pptx",
    ],
)
def test_clean_deck_from_a_real_template_raises_no_template_findings(name: str) -> None:
    """На честно свёрстанной колоде шесть проверок этой задачи молчат.

    Колода собирается тем же кодом, что пойдёт на защиту, и включает
    таблицу и диаграмму: их оформление синтезируется из тех же токенов, и
    ошибка в синтезе проявилась бы именно здесь.

    Речь именно о шести проверках T-29, а не обо всей категории: задачей
    T-32 в неё добавился контраст, и на VK Education он находит настоящее
    нарушение — брендовый синий на белом даёт 4,13:1. Расширять это
    утверждение на всю категорию значило бы требовать молчания от проверки,
    которой есть что сказать.
    """
    template_file = CALIBRATION / name
    if not template_file.exists():
        pytest.skip(f"нет калибровочного шаблона {name}")

    template = parse_template(template_file)
    structure = PresentationStructure(
        slides=[
            StructureSlide(id="s1", role="title", headline="Итоги пилота"),
            StructureSlide(
                id="s2",
                role="data",
                headline="Что изменилось",
                body=SlideBody(items=["Сборка колоды втрое быстрее", "Правки переживают сохранение"]),
            ),
            StructureSlide(
                id="s3",
                role="data",
                headline="Сравнение",
                visualization=Visualization(
                    kind="table",
                    table=TableSpec(headers=["Метрика", "Было", "Стало"], rows=[["Срок", "9 дней", "3 дня"]]),
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
                    ),
                ),
            ),
        ]
    )

    discover()
    for variant in compose_variants(structure, template):
        report = run_checks(AuditContext(deck=variant, template=template), classes=("file",))
        template_findings = [item for item in report.findings if item.check_id in CHECKLIST]
        assert template_findings == [], (
            f"вариант {variant.variant}: "
            + "; ".join(f"{item.check_id} — {item.message}" for item in template_findings)
        )
