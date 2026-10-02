"""T-60: список не уходит за край узкой колонки.

Живой прогон T-58 (вопрос T22): слайд «Волны запуска» лёг на макет в две
колонки, тело — четыре длинных пункта — заняло одну левую, у варианта B
шириной 0,21 холста, и четвёртый пункт ушёл за нижний край. Вёрстка не
уменьшила кегль, проверка переполнения промолчала. Причины общие, а не
свойства одного шаблона:

1. **Вместимость считалась по всей рамке слота.** Отступ под маркер (`marL`,
   там — полдюйма) отнимал четверть ширины узкой колонки, межстрочный
   интервал 115% — седьмую часть высоты, а перенос строки по словам в узкой
   колонке оставляет больше пустоты, чем деление числа знаков на ширину.
   Ни вёрстка, ни аудит, считающий той же метрикой, этого не видели.
2. **Содержимое клалось в первое место под тело, а не в самое просторное.**
   Выбор макета оценивает макет по самому просторному слоту, вёрстка клала
   в первый — на VK Tech это полоска высотой 5% холста, и тело обрезалось до
   7 pt. Того же рода сплющенные диаграммы вариантов B и C в T-59.
3. **Тело занимало одну колонку из нескольких равных**, остальные пустовали.
4. **Смещение варианта уводило визуализацию на тесный макет**, хотя
   просторный того же типа был: смещение перебирало все макеты типа, а не
   только пригодные.

Шаблоны здесь синтетические — тест проверяет правило, а не подгонку под
файл. Единственное исключение — отладочная Jessica в конце, на которой
дефект найден: она проверяет, что правило закрывает исходный случай.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree
from pptx import Presentation

from dpd.audit.checks.layout import check_text_overflow
from dpd.layout import compose
from dpd.layout.overflow import plan_compensations, text_fits
from dpd.layout.selector import select
from dpd.layout.variants import compose_variants
from dpd.models import (
    Bounds,
    Canvas,
    ChartSeries,
    ChartSpec,
    Layout,
    PresentationStructure,
    RenderedElement,
    RenderedPresentation,
    Slide,
    SlideBody,
    Slot,
    StructureSlide,
    TemplateSchema,
    TemplateSource,
    TextFrame,
    TextRun,
    TextStyle,
    Visualization,
)
from dpd.parsing import parse_template

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"

ROOT = Path(__file__).resolve().parents[1]
JESSICA = ROOT / "assets" / "templates" / "debug" / "Jessica · SlidesCarnival.pptx"

NARROW_CANVAS = Canvas(width_emu=9144000, height_emu=5143500)
"""10 × 5,625 дюйма — холст, на котором найден дефект."""

WAVES = [
    "Первая волна: I кв. 2027 — разработка, аналитика (140 пар)",
    "Вторая волна: II кв. 2027 — продукт, дизайн (110 пар)",
    "Третья волна: III кв. 2027 — поддержка и остальные (90 пар)",
    "Поволновый запуск позволяет остановить масштабирование после первой волны при снижении качества",
]
"""Тело слайда «Волны запуска» из живого прогона T-58 (`t58-live/structure.json`)."""

NARROW_COLUMN = Bounds(x=0.06, y=0.263, w=0.208, h=0.665)
"""Левая колонка макета в три колонки, куда легло тело у варианта B."""

BULLETED = TextFrame(indent=0.05, line_spacing=1.15)
"""Отступ под маркер в полдюйма и интервал 115%, без полей рамки."""


# --- 1. Вместимость учитывает то, что отнимает место у текста ---------------


def test_without_the_frame_the_column_looks_roomy_enough() -> None:
    """Отступ и интервал решают: по всей рамке список помещается, за их вычетом — нет."""
    assert text_fits(WAVES, NARROW_COLUMN, NARROW_CANVAS, 13.0)
    assert not text_fits(WAVES, NARROW_COLUMN, NARROW_CANVAS, 13.0, BULLETED)


def test_indent_and_line_spacing_reveal_the_overflow() -> None:
    """На рендере тот же список уходил за нижний край — метрика это видит."""
    assert not text_fits(WAVES, NARROW_COLUMN, NARROW_CANVAS, 14.0, BULLETED)


def test_words_are_not_split_between_lines() -> None:
    """Строка переносится по словам: остаток строки, куда слово не влезло, пуст.

    В строку входит одно слово из шести букв, двух уже нет: четыре слова —
    четыре строки, хотя по числу знаков (27 при десяти на строку) их три.
    """
    canvas = NARROW_CANVAS
    size = 10.0
    width = 51 / 720  # 51 pt при ширине холста 720 pt: 5,1 кегля, слово из шести букв — 3,3
    three_lines = 40 / 405  # 40 pt высоты при высоте холста 405 pt: 3 строки по 12,5 pt
    bounds = Bounds(x=0.1, y=0.1, w=width, h=three_lines)

    assert not text_fits(["абвгде абвгде абвгде абвгде"], bounds, canvas, size)
    assert text_fits(["абвгде абвгде абвгде"], bounds, canvas, size)


def test_long_word_is_not_broken_by_the_chosen_size() -> None:
    """Кегль, при котором самое длинное слово не помещается в строку, не годится.

    Сборка T-60 на Jessica: в колонке шириной 0,21 холста «масштабирование»
    разорвалось на «масштабировани» и «е» — по высоте текст помещался.
    Буква кириллицы в ходовых гарнитурах — 0,53–0,54 кегля (замер на
    контент-пакете: Arial, Segoe UI, Tahoma), а не 0,5.
    """
    text = ["Поволновый запуск позволяет остановить масштабирование"]
    width = 98 / 720  # 98 pt: при 14 pt — 7 кеглей ширины, слово из 15 букв шире
    slot = Slot(
        id="body-1", kind="body", origin="placeholder", bounds=Bounds(x=0.06, y=0.2, w=width, h=0.7),
        text_style=TextStyle(font="Arial", size_pt=14.0, resolved_from="layout.lstStyle"),
    )
    compensations, runs = plan_compensations(text, slot, NARROW_CANVAS, [10.0, 12.0, 14.0])
    assert runs[0].size_pt == 10.0
    assert [item.kind for item in compensations] == ["fontScale"]
    assert "слово" in compensations[0].reason


def test_insets_take_room_from_the_text() -> None:
    bounds = Bounds(x=0.1, y=0.1, w=0.3, h=40 / 405)
    text = ["Короткий пункт"]
    assert text_fits(text, bounds, NARROW_CANVAS, 10.0)
    padded = TextFrame(inset_top=0.05, inset_bottom=0.05)
    assert not text_fits(text, bounds, NARROW_CANVAS, 10.0, padded)


def test_paragraph_spacing_takes_room_between_items() -> None:
    bounds = Bounds(x=0.1, y=0.1, w=0.5, h=40 / 405)
    items = ["Пункт", "Пункт", "Пункт"]
    assert text_fits(items, bounds, NARROW_CANVAS, 10.0)
    assert not text_fits(items, bounds, NARROW_CANVAS, 10.0, TextFrame(space_before_pt=12.0))


def test_exact_line_spacing_is_honoured() -> None:
    bounds = Bounds(x=0.1, y=0.1, w=0.5, h=40 / 405)
    items = ["Пункт", "Пункт", "Пункт"]
    assert not text_fits(items, bounds, NARROW_CANVAS, 10.0, TextFrame(line_spacing_pt=20.0))


def test_compensation_uses_the_frame() -> None:
    """Вёрстка уменьшает кегль, увидев отступ, — и результат помещается."""
    slot = Slot(
        id="body-1", kind="body", origin="placeholder", bounds=NARROW_COLUMN,
        text_style=TextStyle(font="Arial", size_pt=14.0, resolved_from="layout.lstStyle"),
        frame=BULLETED,
    )
    compensations, runs = plan_compensations(WAVES, slot, NARROW_CANVAS, [8.0, 10.0, 12.0, 14.0])
    assert compensations, "переполнение с учётом отступа не замечено"
    size = runs[0].size_pt
    assert text_fits([run.text for run in runs], NARROW_COLUMN, NARROW_CANVAS, size, BULLETED)


def test_overflow_check_measures_with_the_frame() -> None:
    """Аудит считает той же метрикой: рамка элемента едет вместе с ним."""
    element = RenderedElement(
        slot_id="body-1", kind="text", bounds=NARROW_COLUMN,
        runs=[TextRun(text=item, size_pt=14.0) for item in WAVES], frame=BULLETED,
    )
    deck = RenderedPresentation(
        variant="B", template_hash="sha256:" + "ab" * 32, canvas=NARROW_CANVAS,
        slides=[Slide(id="s12", layout_id="m/l7", elements=[element])],
    )
    findings = check_text_overflow(deck)
    assert [finding.check_id for finding in findings] == ["layout.text_overflow"]


def test_overflow_check_names_text_cut_by_the_layout() -> None:
    """Сокращённый вёрсткой текст помещается, но не поместился: проверка называет его.

    Честная метрика превращает выход за край в сокращение с многоточием, и
    без этой находки потеря содержания стала бы тихой — хуже, чем видимый
    на рендере хвост.
    """
    tiny = Slot(
        id="body-1", kind="body", origin="placeholder", bounds=Bounds(x=0.06, y=0.26, w=0.208, h=0.2),
        text_style=TextStyle(font="Arial", size_pt=14.0, resolved_from="layout.lstStyle"), frame=BULLETED,
    )
    layout = Layout(id="m/tiny", name="Колонка", family="content", slots=[TITLE, tiny])
    deck = compose(_text_slide(WAVES), _schema(layout))
    assert any(item.kind == "truncate" for item in deck.slides[0].applied_compensations)

    findings = [item for item in check_text_overflow(deck) if item.slot_id == "body-1"]
    assert len(findings) == 1
    assert findings[0].evidence.get("truncated") is True
    assert "сокращ" in findings[0].message


# --- Разбор кладёт в схему то, что отнимает место у текста -----------------


def _layout(prs, name: str):
    return next(layout for layout in prs.slide_layouts if layout.name == name)


def _body_placeholder(layout, idx: int = 1):
    return next(ph for ph in layout.placeholders if ph.placeholder_format.idx == idx)


def _paragraph_level(placeholder):
    list_style = placeholder._element.find(P + "txBody").find(A + "lstStyle")
    level = list_style.find(A + "lvl1pPr")
    if level is None:
        level = etree.SubElement(list_style, A + "lvl1pPr")
    return level


@pytest.fixture(scope="module")
def framed(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Layout]:
    prs = Presentation()

    # Свои значения у плейсхолдера макета: поля рамки, отступ, интервалы.
    own = _body_placeholder(_layout(prs, "Title and Content"))
    body = own._element.find(P + "txBody").find(A + "bodyPr")
    for side in ("lIns", "rIns", "tIns", "bIns"):
        body.set(side, "0")
    level = _paragraph_level(own)
    level.set("marL", "457200")
    level.set("indent", "-342900")
    spacing = etree.SubElement(level, A + "lnSpc")
    etree.SubElement(spacing, A + "spcPct", val="150000")
    before = etree.SubElement(level, A + "spcBef")
    etree.SubElement(before, A + "spcPts", val="600")

    # Интервал у плейсхолдера мастера: макеты без своего наследуют его.
    master_body = next(
        ph for ph in prs.slide_master.placeholders if ph.placeholder_format.idx == 1
    )
    master_level = _paragraph_level(master_body)
    spacing = etree.SubElement(master_level, A + "lnSpc")
    etree.SubElement(spacing, A + "spcPct", val="115000")

    path = tmp_path_factory.mktemp("frame") / "framed.pptx"
    prs.save(path)
    return {layout.name: layout for layout in parse_template(path, use_cache=False).layouts}


def _first_body(layout: Layout) -> Slot:
    return next(slot for slot in layout.slots if slot.kind == "body")


def test_parser_reads_the_placeholder_frame(framed: dict[str, Layout]) -> None:
    frame = _first_body(framed["Title and Content"]).frame
    assert frame is not None
    assert frame.indent == pytest.approx(457200 / 9144000)
    assert (frame.inset_left, frame.inset_right, frame.inset_top, frame.inset_bottom) == (0, 0, 0, 0)
    assert frame.line_spacing == pytest.approx(1.5)
    assert frame.space_before_pt == pytest.approx(6.0)


def test_parser_inherits_the_frame_from_the_master(framed: dict[str, Layout]) -> None:
    """Отступ — из стилей мастера, интервал — из плейсхолдера мастера, поля — по умолчанию."""
    frame = _first_body(framed["Two Content"]).frame
    assert frame is not None
    assert frame.indent == pytest.approx(342900 / 9144000)
    assert frame.line_spacing == pytest.approx(1.15)
    assert frame.inset_left == pytest.approx(91440 / 9144000)
    assert frame.inset_top == pytest.approx(45720 / 6858000)


def test_slots_without_placeholder_get_the_text_box_frame(framed: dict[str, Layout]) -> None:
    """Для слота без плейсхолдера экспорт создаёт текстовую рамку с полями по умолчанию."""
    derived = _first_body(framed["Blank"])
    assert derived.origin != "placeholder"
    assert derived.frame is not None
    assert derived.frame.inset_left == pytest.approx(91440 / 9144000)
    assert derived.frame.indent == 0


# --- 2. Содержимое — в самое просторное место -------------------------------


def _slot(slot_id: str, kind: str, x: float, y: float, w: float, h: float, size: float = 14.0) -> Slot:
    return Slot(
        id=slot_id, kind=kind, origin="placeholder", bounds=Bounds(x=x, y=y, w=w, h=h),
        text_style=TextStyle(font="Arial", size_pt=size, resolved_from="layout.lstStyle"),
    )


def _schema(*layouts: Layout) -> TemplateSchema:
    return TemplateSchema(
        source=TemplateSource(file="t.pptx", hash="sha256:" + "ab" * 32),
        canvas=NARROW_CANVAS,
        layouts=list(layouts),
    )


TITLE = _slot("title", "title", 0.05, 0.05, 0.43, 0.12, 24.0)

STRIP_FIRST = Layout(
    id="m/strip", name="Содержание", family="content",
    slots=[TITLE, _slot("body-1", "body", 0.51, 0.07, 0.42, 0.05), _slot("body-2", "body", 0.51, 0.13, 0.42, 0.73)],
)
"""Первое место под тело — полоска надзаголовка, второе — сама область."""


def _text_slide(items: list[str], role: str = "data") -> PresentationStructure:
    return PresentationStructure(
        slides=[StructureSlide(id="s1", role=role, headline="Волны запуска", body=SlideBody(items=items))]
    )


def _chart_slide() -> PresentationStructure:
    chart = ChartSpec(
        chart_type="column", categories=["I", "II", "III"],
        series=[ChartSeries(name="Пары", points=[140, 110, 90])],
    )
    return PresentationStructure(
        slides=[StructureSlide(id="s1", role="data", headline="Динамика",
                               visualization=Visualization(kind="chart", chart=chart))]
    )


def test_text_goes_into_the_roomiest_body_slot() -> None:
    slide = compose(_text_slide(WAVES), _schema(STRIP_FIRST)).slides[0]
    body = [element for element in slide.elements if element.slot_id != "title"]
    assert [element.slot_id for element in body] == ["body-2"]
    assert not any(item.kind == "truncate" for item in slide.applied_compensations)


def test_chart_goes_into_the_roomiest_body_slot() -> None:
    slide = compose(_chart_slide(), _schema(STRIP_FIRST)).slides[0]
    chart = next(element for element in slide.elements if element.kind == "chart")
    assert chart.slot_id == "body-2"
    assert chart.bounds.h == pytest.approx(0.73)


def test_element_carries_the_frame_of_its_slot() -> None:
    framed_column = _slot("body-1", "body", 0.06, 0.26, 0.6, 0.6).model_copy(update={"frame": BULLETED})
    layout = Layout(id="m/one", name="Контент", family="content", slots=[TITLE, framed_column])
    slide = compose(_text_slide(["Пункт"]), _schema(layout)).slides[0]
    body = next(element for element in slide.elements if element.slot_id == "body-1")
    assert body.frame == BULLETED


# --- 3. Равные колонки заполняются по порядку -------------------------------


def _columns(count: int, width: float = 0.208, frame: TextFrame | None = BULLETED) -> Layout:
    gap = 0.022
    slots = [TITLE] + [
        _slot(f"body-{n + 1}", "body", 0.06 + n * (width + gap), 0.263, width, 0.665).model_copy(
            update={"frame": frame}
        )
        for n in range(count)
    ]
    return Layout(id=f"m/cols{count}", name="Колонки", family="split", slots=slots)


def _body_texts(slide: Slide) -> dict[str, list[str]]:
    return {
        element.slot_id: [run.text for run in element.runs]
        for element in slide.elements
        if element.kind == "text" and element.slot_id != "title"
    }


def test_list_flows_across_equal_columns_in_reading_order() -> None:
    slide = compose(_text_slide(WAVES, "process"), _schema(_columns(3))).slides[0]
    texts = _body_texts(slide)
    assert list(texts) == ["body-1", "body-2", "body-3"]
    flowed = [item for column in texts.values() for item in column]
    assert flowed == WAVES


def test_flowed_columns_share_one_size() -> None:
    """Колонки одного слайда разным кеглем выглядят ошибкой вёрстки."""
    slide = compose(_text_slide(WAVES, "process"), _schema(_columns(3))).slides[0]
    sizes = {
        run.size_pt for element in slide.elements if element.slot_id != "title" for run in element.runs
    }
    assert len(sizes) == 1


def test_flowed_columns_fit_without_losing_content() -> None:
    slide = compose(_text_slide(WAVES, "process"), _schema(_columns(3))).slides[0]
    assert not any(item.kind == "truncate" for item in slide.applied_compensations)
    for element in slide.elements:
        if element.slot_id == "title":
            continue
        size = element.runs[0].size_pt
        assert text_fits([run.text for run in element.runs], element.bounds, NARROW_CANVAS, size, element.frame)


def test_single_item_stays_in_one_column() -> None:
    slide = compose(_text_slide(["Один пункт"], "process"), _schema(_columns(2))).slides[0]
    assert list(_body_texts(slide)) == ["body-1"]


def test_unequal_body_slots_are_not_columns() -> None:
    """Полоска рядом с областью — не колонка: тело целиком идёт в область."""
    slide = compose(_text_slide(WAVES), _schema(STRIP_FIRST)).slides[0]
    assert list(_body_texts(slide)) == ["body-2"]


# --- T-64: колонки — только рядом -------------------------------------------
#
# Правило T-60 брало равные места в порядке чтения — сверху вниз, слева
# направо — и продолжало список и в месте под первым: два блока с промежутком
# посередине читаются как два списка, а не как один (вопрос T25, решение
# владельца — вариант «а»). Колонка — место того же размера на той же высоте.


def _stacked() -> Layout:
    """Два равных места одно над другим."""
    return Layout(
        id="m/stacked", name="Два блока", family="content",
        slots=[
            TITLE,
            _slot("body-1", "body", 0.06, 0.2, 0.88, 0.34),
            _slot("body-2", "body", 0.06, 0.58, 0.88, 0.34),
        ],
    )


def _grid() -> Layout:
    """Сетка 2 × 2: две колонки в два ряда."""
    slots = [TITLE]
    for row, y in enumerate((0.2, 0.58)):
        for col, x in enumerate((0.06, 0.51)):
            slots.append(_slot(f"body-{row * 2 + col + 1}", "body", x, y, 0.43, 0.34))
    return Layout(id="m/grid", name="Четыре блока", family="split", slots=slots)


def test_list_does_not_continue_into_the_place_below() -> None:
    slide = compose(_text_slide(WAVES), _schema(_stacked())).slides[0]
    texts = _body_texts(slide)
    assert list(texts) == ["body-1"]
    assert texts["body-1"] == WAVES


def test_list_flows_only_across_its_own_row() -> None:
    """В сетке 2 × 2 список идёт по колонкам верхнего ряда, нижний не трогает."""
    slide = compose(_text_slide(WAVES, "process"), _schema(_grid())).slides[0]
    texts = _body_texts(slide)
    assert list(texts) == ["body-1", "body-2"]
    assert [item for column in texts.values() for item in column] == WAVES


def test_columns_drawn_slightly_off_line_flow_left_to_right() -> None:
    """Колонки, разошедшиеся по высоте на доли процента холста, — один ряд.

    Порядок в ряду — слева направо, а не по тому, чья рамка начинается
    чуть выше: иначе список начинался бы в правой колонке.
    """
    layout = _columns(2, width=0.42)
    left, right = layout.slots[1], layout.slots[2]
    raised = right.model_copy(update={"bounds": right.bounds.model_copy(update={"y": 0.25})})
    layout = layout.model_copy(update={"slots": [TITLE, left, raised]})
    slide = compose(_text_slide(WAVES, "process"), _schema(layout)).slides[0]
    texts = _body_texts(slide)
    assert list(texts) == ["body-1", "body-2"]
    assert [item for column in texts.values() for item in column] == WAVES


# --- 4. Смещение варианта не уводит визуализацию на тесный макет -----------


def test_variant_offset_keeps_a_chart_on_a_roomy_layout() -> None:
    roomy = Layout(
        id="m/roomy", name="Контент", family="content",
        slots=[TITLE, _slot("body-1", "body", 0.06, 0.2, 0.88, 0.7)],
    )
    cramped = Layout(
        id="m/cramped", name="Контент с полоской", family="content",
        slots=[TITLE, _slot("body-1", "body", 0.06, 0.2, 0.88, 0.1)],
    )
    slide = _chart_slide().slides[0]
    for offset in (0, 1, 2):
        chosen, _ = select(slide, [roomy, cramped], offset)
        assert chosen.id == "m/roomy", f"смещение {offset} увело диаграмму на тесный макет"


def test_wide_strip_is_not_room_for_a_chart() -> None:
    """Площади мало: полоса во всю ширину просторна по площади, но диаграмма в ней сплющена.

    Замер на двух калибровочных холстах (`D:/dpd-out/t60/heights-*`): при высоте
    рамки 0,3 холста у столбчатой диаграммы пропадает подпись категории, при
    0,1–0,15 остаётся один заголовок; читается она с 0,4. Полоса 0,9 × 0,3 по
    площади больше колонки 0,4 × 0,6, и прежнее правило выбирало её.
    """
    strip = Layout(
        id="m/strip", name="Полоса", family="content",
        slots=[TITLE, _slot("body-1", "body", 0.05, 0.2, 0.9, 0.3)],
    )
    column = Layout(
        id="m/column", name="Колонка", family="content",
        slots=[TITLE, _slot("body-1", "body", 0.05, 0.2, 0.4, 0.6)],
    )
    chosen, _ = select(_chart_slide().slides[0], [strip, column])
    assert chosen.id == "m/column"


# --- Исходный случай: отладочная Jessica ------------------------------------


@pytest.mark.skipif(not JESSICA.exists(), reason="отладочный шаблон не найден")
def test_waves_slide_fits_in_every_variant_on_jessica() -> None:
    """Слайд «Волны запуска» во всех трёх вариантах: ничего за краем, ничего не срезано."""
    schema = parse_template(JESSICA, use_cache=False)
    structure = _text_slide(WAVES, "process")
    for deck in compose_variants(structure, schema):
        slide = deck.slides[0]
        assert not any(item.kind == "truncate" for item in slide.applied_compensations), deck.variant
        for element in slide.elements:
            if element.kind != "text" or not element.runs:
                continue
            size = element.runs[0].size_pt
            texts = [run.text for run in element.runs]
            assert text_fits(texts, element.bounds, deck.canvas, size, element.frame), (deck.variant, element.slot_id)
        assert [item for column in _body_texts(slide).values() for item in column] == WAVES, deck.variant
