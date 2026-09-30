"""T-30: пять проверок плотности.

Критерий приёмки: на слайде с заведомым дефектом каждая срабатывает, на
чистом — нет.

**Заполнение измеряется занятым местом, а не размером рамок.** Это решено не
рассуждением, а рендером: на контентном слайде VK Tech текстовый блок занимает
верхнюю четверть, а рамка слота — три четверти холста. Метрика по рамкам
объявила бы такой слайд переполненным, метрика по содержимому дала 23% —
столько же, сколько видно глазом.

**Отсчёт идёт от места, отведённого шаблоном, а не от всего холста.** На том
же рендере нижнюю половину слайда занимает графика шаблона: слотов там нет и
содержимому там не место. Считая от холста, мы обвиняли бы вёрстку в решениях
автора шаблона — ошибка, от которой уже пришлось избавляться в T-28.

Титульные слайды и разделители из проверки исключены: они пусты по замыслу
жанра, и находка о них была бы претензией к самому жанру.
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
    TableSpec,
    TemplateSchema,
    TemplateSource,
    TextRun,
    Visualization,
)
from dpd.parsing import parse_template

CALIBRATION = Path(__file__).resolve().parents[1] / "assets" / "templates" / "calibration"
CANVAS = Canvas(width_emu=12192000, height_emu=6858000)

CHECKLIST = {
    "density.too_many_bullets": ("file", "warning", "lossy"),
    "density.bullet_too_long": ("file", "warning", "lossy"),
    "density.table_too_big": ("file", "warning", "lossy"),
    "density.too_many_series": ("file", "warning", "lossy"),
    "density.fill_out_of_range": ("file", "advice", "lossy"),
}

TITLE_BOUNDS = Bounds(x=0.06, y=0.08, w=0.60, h=0.12)
BODY_BOUNDS = Bounds(x=0.06, y=0.26, w=0.60, h=0.50)


def schema(layout_id: str = "l1") -> TemplateSchema:
    """Шаблон с заголовком и содержимым: слоты занимают 40% холста."""
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


def text(slot_id: str, *paragraphs: str, bounds: Bounds, size: float = 18) -> RenderedElement:
    return RenderedElement(
        slot_id=slot_id,
        kind="text",
        bounds=bounds,
        runs=[TextRun(text=item, font="Play", size_pt=size) for item in paragraphs],
    )


def title(text_value: str = "Итоги пилота") -> RenderedElement:
    return text("title", text_value, bounds=TITLE_BOUNDS, size=24)


def bullets(count: int, words: int = 5) -> RenderedElement:
    item = " ".join(["слово"] * words)
    return text("body", *[item] * count, bounds=BODY_BOUNDS)


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


def density_of(*elements: RenderedElement, family: str = "content") -> list:
    return findings(
        "density.fill_out_of_range",
        AuditContext(deck=deck(*elements, family=family), template=schema()),
    )


# --- Реестр --------------------------------------------------------------


@pytest.mark.parametrize("check_id", sorted(CHECKLIST))
def test_check_is_registered_as_the_checklist_describes(check_id: str) -> None:
    discover()
    registered = REGISTRY.get(check_id)
    assert registered is not None, f"{check_id} не зарегистрирована"

    spec = registered.spec
    assert (spec.check_class, spec.severity, spec.fixability) == CHECKLIST[check_id]
    assert spec.category == "density"


# --- density.too_many_bullets ---------------------------------------------


def test_too_many_bullets_fires_above_the_limit() -> None:
    """Семь пунктов на слайде — список, который никто не дочитает."""
    found = findings(
        "density.too_many_bullets",
        AuditContext(deck=deck(title(), bullets(7)), template=schema()),
    )

    assert len(found) == 1
    assert found[0].evidence["actual"] == 7 and found[0].evidence["expected"] == 6


def test_too_many_bullets_is_silent_at_the_limit() -> None:
    assert findings(
        "density.too_many_bullets",
        AuditContext(deck=deck(title(), bullets(6)), template=schema()),
    ) == []


def test_too_many_bullets_does_not_count_the_headline() -> None:
    """Заголовок не буллет.

    Иначе слайд с шестью пунктами и заголовком давал бы находку о семи, и
    предельным числом на деле оказалось бы пять.
    """
    assert findings(
        "density.too_many_bullets",
        AuditContext(deck=deck(title("Что изменилось"), bullets(6)), template=schema()),
    ) == []


# --- density.bullet_too_long ----------------------------------------------


def test_bullet_too_long_fires_above_the_limit() -> None:
    """Буллет в шестнадцать слов — абзац, выданный за пункт списка."""
    found = findings(
        "density.bullet_too_long",
        AuditContext(deck=deck(title(), bullets(2, words=16)), template=schema()),
    )

    assert len(found) == 2
    assert found[0].evidence["actual"] == 16


def test_bullet_too_long_is_silent_at_the_limit() -> None:
    assert findings(
        "density.bullet_too_long",
        AuditContext(deck=deck(title(), bullets(2, words=15)), template=schema()),
    ) == []


def test_bullet_too_long_does_not_count_the_headline() -> None:
    """Длинный заголовок — отдельный разговор, и правило у него другое."""
    long_title = title(" ".join(["слово"] * 20))
    assert findings(
        "density.bullet_too_long",
        AuditContext(deck=deck(long_title, bullets(2)), template=schema()),
    ) == []


# --- density.table_too_big ------------------------------------------------


def table(rows: int, columns: int) -> RenderedElement:
    return RenderedElement(
        slot_id="body",
        kind="table",
        bounds=BODY_BOUNDS,
        table=RenderedTable(
            headers=[f"к{index}" for index in range(columns)],
            rows=[[f"з{index}" for index in range(columns)] for _ in range(rows)],
            font="Play",
            size_pt=12,
        ),
    )


def test_table_too_big_fires_on_too_many_rows() -> None:
    found = findings(
        "density.table_too_big",
        AuditContext(deck=deck(title(), table(rows=8, columns=3)), template=schema()),
    )

    assert len(found) == 1
    assert found[0].evidence["rows"] == 8


def test_table_too_big_fires_on_too_many_columns() -> None:
    found = findings(
        "density.table_too_big",
        AuditContext(deck=deck(title(), table(rows=3, columns=6)), template=schema()),
    )

    assert len(found) == 1
    assert found[0].evidence["columns"] == 6


def test_table_too_big_is_silent_at_the_limit() -> None:
    """Предел 7 × 5 из чек-листа: вёрстка сама режет таблицу до него (T-23)."""
    assert findings(
        "density.table_too_big",
        AuditContext(deck=deck(title(), table(rows=7, columns=5)), template=schema()),
    ) == []


# --- density.too_many_series ----------------------------------------------


def chart(series: int) -> RenderedElement:
    return RenderedElement(
        slot_id="body",
        kind="chart",
        bounds=BODY_BOUNDS,
        chart=RenderedChart(
            chart_type="column",
            categories=["I", "II"],
            series=[ChartSeries(name=f"ряд {index}", points=[1.0, 2.0]) for index in range(series)],
            colors=["#0077FF"],
            font="Play",
            size_pt=12,
        ),
    )


def test_too_many_series_fires_above_the_limit() -> None:
    """Шесть рядов на диаграмме нельзя различить по цвету."""
    found = findings(
        "density.too_many_series",
        AuditContext(deck=deck(title(), chart(6)), template=schema()),
    )

    assert len(found) == 1
    assert found[0].evidence["actual"] == 6


def test_too_many_series_is_silent_at_the_limit() -> None:
    assert findings(
        "density.too_many_series",
        AuditContext(deck=deck(title(), chart(5)), template=schema()),
    ) == []


# --- density.fill_out_of_range --------------------------------------------


def test_fill_fires_on_an_almost_empty_slide() -> None:
    """Два слова в слоте на половину холста — слайд, который нечем смотреть."""
    found = density_of(title(), text("body", "Два слова", bounds=BODY_BOUNDS))

    assert len(found) == 1
    assert found[0].severity == "advice"
    assert "менее" in found[0].message


def test_fill_fires_on_an_overcrowded_slide() -> None:
    """Текст, занявший всё отведённое место, не оставляет слайду воздуха."""
    found = density_of(title(), bullets(14, words=12))

    assert len(found) == 1
    assert "более" in found[0].message


def test_fill_is_silent_on_a_well_filled_slide() -> None:
    found = density_of(title(), bullets(5, words=10))

    assert found == []


def test_fill_does_not_call_a_visualisation_overcrowded() -> None:
    """Диаграмма занимает свой слот целиком — место под неё и отводилось.

    Теснота — это когда текст не оставил слайду воздуха. Визуализация,
    заполнившая отведённое ей место, исполняет замысел шаблона, и находка о
    ней срабатывала бы на каждом слайде с диаграммой или таблицей.
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
            font="Play",
            size_pt=12,
        ),
    )

    assert density_of(title(), chart) == []


def test_fill_ignores_title_slides() -> None:
    """Титул и разделитель пусты по замыслу жанра, а не по недосмотру."""
    assert density_of(title(), family="title") == []
    assert density_of(title(), family="section") == []


def test_fill_is_measured_against_the_space_the_template_offers() -> None:
    """Отсчёт от слотов, а не от холста.

    Слоты синтетического макета занимают 40% холста: остальное в реальном
    шаблоне — его собственная графика. Слайд, заполнивший половину
    отведённого места, считается нормальным, хотя от холста это лишь пятая
    часть, и метрика по холсту объявила бы его пустым.
    """
    found = density_of(title(), bullets(5, words=10))
    assert found == []

    report = run_checks(
        AuditContext(deck=deck(title(), bullets(5, words=10)), template=schema()),
    )
    assert report.findings == [], "слайд, нормальный по меркам шаблона, объявлен дефектным"


# --- Вторая половина критерия на живых данных -----------------------------


@pytest.mark.parametrize(
    "name",
    [
        "VK Tech шаблон.pptx",
        "VK_WorkSpace_Клиентская_конференция_Шаблон_03.pptx",
        "Шаблон презентации VK Education.pptx",
    ],
)
def test_clean_deck_from_a_real_template_raises_no_density_warnings(name: str) -> None:
    """На честно свёрстанной колоде проверки плотности не дают предупреждений.

    Единственное, что здесь допускается, — совет о заполнении: он зависит от
    того, сколько текста дал контент, а не от того, правильно ли собран
    слайд. Скудный контент в крупном слоте действительно оставляет слайд
    пустоватым, и сказать об этом — работа проверки, а не её ошибка.
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
        density = [item for item in report.findings if item.category == "density"]

        warnings = [item for item in density if item.severity != "advice"]
        assert warnings == [], "; ".join(f"{item.check_id} — {item.message}" for item in warnings)
        assert {item.check_id for item in density} <= {"density.fill_out_of_range"}
