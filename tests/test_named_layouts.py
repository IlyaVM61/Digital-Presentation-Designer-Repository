"""T-62: шаблон с договорённостью об именах макетов и полей.

Договорённость называет назначение макета префиксом имени: `title*` —
обложка, `section*` — раздел, `slide*` — обычный слайд, `table*` — слайд с
таблицей, `last*` — финал. Поля размечены типизированными плейсхолдерами:
заголовок, тело, таблица, картинка.

Без поддержки договорённости разбор справлялся наполовину (проверка
2026-10-02): заголовок и тело находились по типу плейсхолдера, но обложка,
раздел и финал одной геометрии были неразличимы — варианты ставили обложку
на финальный макет; плейсхолдер таблицы считался прочим, и таблица ложилась
в текстовое поле; место под текст строилось поверх полей таблицы и
картинки; макет с пустым полем картинки брался под текстовые слайды.

Имя сильнее геометрии, только когда договорённости следуют все макеты
шаблона (ADR-0008): в калибровочном шаблоне 11 макетов из 15 называются
одинаково, и одно случайное совпадение префикса ничего не говорит.

Шаблон синтетический, из стандартного шаблона python-pptx: тест проверяет
правило, а не подгонку под конкретный файл.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pptx import Presentation
from pptx.enum.shapes import PP_PLACEHOLDER
from pptx.util import Emu

from dpd.export import export_pptx
from dpd.layout import compose
from dpd.layout.selector import select
from dpd.layout.variants import compose_variants
from dpd.models import (
    Bounds,
    Layout,
    PresentationStructure,
    SlideBody,
    StructureSlide,
    TemplateSchema,
)
from dpd.models.structure import TableSpec, Visualization
from dpd.parsing import parse_template

NAMES = {
    "Title Slide": "title1",
    "Section Header": "section1",
    "Title and Content": "slide1",
    "Two Content": "slide2",
    "Content with Caption": "table_text1",
    "Picture with Caption": "table_only1",
    "Title Only": "last1",
}
"""Макеты стандартного шаблона → имена по договорённости. Остальные удаляются:
договорённости должны следовать все макеты."""


def _placeholder(layout, idx: int):
    return next(shape for shape in layout.placeholders if shape.placeholder_format.idx == idx)


def _retype(layout, idx: int, kind: str) -> None:
    """Сменить тип плейсхолдера: `tbl` — поле таблицы, `pic` — поле картинки."""
    _placeholder(layout, idx)._element.ph.set("type", kind)


def _move(prs, shape, *, x: float, y: float, w: float, h: float) -> None:
    shape.left, shape.top = Emu(int(prs.slide_width * x)), Emu(int(prs.slide_height * y))
    shape.width, shape.height = Emu(int(prs.slide_width * w)), Emu(int(prs.slide_height * h))


def _build(path: Path, *, names: dict[str, str] | None = None) -> Path:
    """Шаблон с полями таблицы и картинки; имена — по договорённости или как заданы."""
    names = NAMES if names is None else names
    prs = Presentation()
    layouts = {layout.name: layout for layout in prs.slide_layouts}
    for name, layout in layouts.items():
        if name not in NAMES:
            prs.slide_layouts.remove(layout)

    # Обычный слайд с полем картинки справа — пара к макету без него.
    _retype(layouts["Two Content"], 2, "pic")
    # Слайд с таблицей: текст слева, таблица справа.
    _retype(layouts["Content with Caption"], 1, "tbl")
    # Слайд с таблицей и картинкой: поля делят ширину пополам, тела нет.
    table_only = layouts["Picture with Caption"]
    _retype(table_only, 2, "tbl")
    _move(prs, _placeholder(table_only, 0), x=0.05, y=0.03, w=0.9, h=0.1)
    _move(prs, _placeholder(table_only, 2), x=0.05, y=0.2, w=0.42, h=0.7)
    _move(prs, _placeholder(table_only, 1), x=0.5, y=0.2, w=0.45, h=0.7)

    for name, layout in layouts.items():
        if name in names:
            layout.name = names[name]
    prs.save(path)
    return path


@pytest.fixture(scope="module")
def named(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, TemplateSchema]:
    path = _build(tmp_path_factory.mktemp("named") / "named.pptx")
    return path, parse_template(path, use_cache=False)


def _by_name(schema: TemplateSchema) -> dict[str, Layout]:
    return {layout.name: layout for layout in schema.layouts}


def _names(schema: TemplateSchema) -> dict[str, str]:
    return {layout.id: layout.name for layout in schema.layouts}


TABLE = Visualization(
    kind="table",
    table=TableSpec(headers=["Волна", "Пар"], rows=[["Первая", "140"], ["Вторая", "110"]]),
)


def _slide(role: str, items: int = 0, visual: Visualization | None = None, sid: str = "s1") -> StructureSlide:
    body = SlideBody(items=[f"пункт {n}" for n in range(items)]) if items else None
    return StructureSlide(id=sid, role=role, headline="Заголовок", body=body, visualization=visual)


# --- Назначение макета из имени -------------------------------------------


def test_purpose_is_read_from_the_name(named) -> None:
    layouts = _by_name(named[1])

    assert {name: layout.purpose for name, layout in layouts.items()} == {
        "title1": "cover",
        "section1": "section",
        "slide1": "regular",
        "slide2": "regular",
        "table_text1": "table",
        "table_only1": "table",
        "last1": "closing",
    }
    assert {layout.family_source for layout in layouts.values()} == {"name"}


def test_cover_section_and_closing_get_distinct_families(named) -> None:
    """Обложка, раздел и финал одной геометрии были одним семейством «title»."""
    layouts = _by_name(named[1])

    assert layouts["title1"].family == "title"
    assert layouts["section1"].family == "section"
    assert layouts["last1"].family == "title"


def test_names_are_ignored_unless_every_layout_follows_the_convention(tmp_path: Path) -> None:
    """Один макет вне договорённости — и имена не сигнал: тип из геометрии."""
    partial = {**NAMES, "Title Only": "Только заголовок"}
    schema = parse_template(_build(tmp_path / "partial.pptx", names=partial), use_cache=False)

    assert {layout.purpose for layout in schema.layouts} == {None}
    assert {layout.family_source for layout in schema.layouts} == {"structure"}


def test_capitalised_office_names_are_not_the_convention(tmp_path: Path) -> None:
    """«Title Slide», «TITLE_AND_BODY» — имена редакторов, а не договорённость."""
    capitalised = {name: name.upper().replace(" ", "_") for name in NAMES}
    schema = parse_template(_build(tmp_path / "upper.pptx", names=capitalised), use_cache=False)

    assert {layout.purpose for layout in schema.layouts} == {None}


# --- Поля таблицы и картинки ----------------------------------------------


def test_table_and_picture_placeholders_are_typed_slots(named) -> None:
    layouts = _by_name(named[1])

    assert [slot.kind for slot in layouts["table_text1"].slots if slot.kind != "other"] == ["title", "table", "body"]
    assert "picture" in [slot.kind for slot in layouts["slide2"].slots]


def test_place_in_free_area_avoids_table_and_picture_fields(named) -> None:
    """Место под текст строилось поверх полей таблицы и картинки (рамка 0,88 × 0,86
    холста), и макет брался под все текстовые слайды."""
    layout = _by_name(named[1])["table_only1"]
    fields = [slot.bounds for slot in layout.slots if slot.kind in ("table", "picture")]
    bodies = [slot.bounds for slot in layout.slots if slot.kind == "body"]

    assert len(fields) == 2
    assert not any(_overlap(body, field) for body in bodies for field in fields)


def _overlap(first: Bounds, second: Bounds) -> bool:
    horizontal = min(first.x + first.w, second.x + second.w) - max(first.x, second.x)
    vertical = min(first.y + first.h, second.y + second.h) - max(first.y, second.y)
    return horizontal > 0.01 and vertical > 0.01


# --- Выбор макета по назначению -------------------------------------------


@pytest.mark.parametrize("offset", [0, 1, 2])
def test_cover_never_lands_on_the_closing_layout(named, offset: int) -> None:
    """Варианты B и C ставили обложку на макет раздела и на финальный."""
    layout, _ = select(_slide("title"), named[1].layouts, offset)
    assert layout.name == "title1"


@pytest.mark.parametrize(("role", "expected"), [("closing", "last1"), ("section", "section1")])
def test_section_and_closing_take_their_own_layouts(named, role: str, expected: str) -> None:
    for offset in (0, 1, 2):
        layout, decision = select(_slide(role), named[1].layouts, offset)
        assert layout.name == expected
        assert not decision.degraded


@pytest.mark.parametrize("offset", [0, 1, 2])
def test_text_slide_takes_the_regular_layout_without_a_picture_field(named, offset: int) -> None:
    """Пока картинок нет, макет с полем картинки уступает парному без него:
    иначе половина слайда пустует (решение владельца 2026-10-01)."""
    layout, _ = select(_slide("data", items=3), named[1].layouts, offset)
    assert layout.name == "slide1"


@pytest.mark.parametrize("offset", [0, 1, 2])
def test_table_slide_takes_a_table_layout(named, offset: int) -> None:
    layout, _ = select(_slide("data", visual=TABLE), named[1].layouts, offset)
    assert layout.name == "table_text1"


def test_section_with_content_goes_to_a_regular_layout(named) -> None:
    """Содержимое важнее номинальной роли — как и без имён: раздел со списком
    в узкую строку подзаголовка не помещается."""
    layout, _ = select(_slide("section", items=3), named[1].layouts)
    assert layout.name == "slide1"


def test_missing_purpose_degrades_to_a_regular_layout_and_says_so(tmp_path: Path) -> None:
    """Раздела в шаблоне нет — слайд раздела ложится на обычный, а не на обложку."""
    names = {**NAMES, "Section Header": "slide3"}
    schema = parse_template(_build(tmp_path / "nosection.pptx", names=names), use_cache=False)

    layout, decision = select(_slide("section"), schema.layouts)

    assert layout.purpose == "regular"
    assert decision.degraded
    assert "раздел" in decision.reason


def test_three_variants_keep_the_cover_on_the_cover_layout(named) -> None:
    structure = PresentationStructure(
        slides=[_slide("title", sid="s1"), _slide("data", items=3, sid="s2"), _slide("closing", sid="s3")]
    )
    for deck in compose_variants(structure, named[1]):
        chosen = [_names(named[1])[slide.layout_id] for slide in deck.slides]
        assert chosen == ["title1", "slide1", "last1"], deck.variant


# --- Таблица в поле таблицы -----------------------------------------------


def test_table_goes_into_the_table_field(named) -> None:
    rendered = compose(PresentationStructure(slides=[_slide("data", visual=TABLE)]), named[1])
    layout = _by_name(named[1])["table_text1"]
    table_slot = next(slot for slot in layout.slots if slot.kind == "table")

    [table] = [element for element in rendered.slides[0].elements if element.kind == "table"]
    assert table.slot_id == table_slot.id
    assert table.bounds == table_slot.bounds


def test_table_field_also_works_without_the_convention(tmp_path: Path) -> None:
    """Плейсхолдер таблицы — место под таблицу в любом шаблоне, а не только в именованном."""
    partial = {**NAMES, "Title Only": "Только заголовок"}
    schema = parse_template(_build(tmp_path / "partial.pptx", names=partial), use_cache=False)
    slide = _slide("data", visual=TABLE)

    rendered = compose(PresentationStructure(slides=[slide]), schema)
    layout = next(layout for layout in schema.layouts if layout.id == rendered.slides[0].layout_id)
    [table] = [element for element in rendered.slides[0].elements if element.kind == "table"]

    assert next(slot for slot in layout.slots if slot.id == table.slot_id).kind == "table"


def test_body_text_shares_the_slide_with_a_table_in_its_own_field(named) -> None:
    """Поле таблицы освобождает текстовое поле: план, где у слайда есть и тело, и
    таблица, теряет тело только там, где таблица заняла бы место текста."""
    rendered = compose(PresentationStructure(slides=[_slide("data", items=2, visual=TABLE)]), named[1])

    kinds = sorted(element.kind for element in rendered.slides[0].elements)
    assert kinds == ["table", "text", "text"]


def test_exported_table_fills_the_table_placeholder(named, tmp_path: Path) -> None:
    path, schema = named
    rendered = compose(PresentationStructure(slides=[_slide("data", visual=TABLE)]), schema)

    out = export_pptx(rendered, schema, path, tmp_path / "deck.pptx")

    shapes = list(Presentation(str(out)).slides[0].shapes)
    [frame] = [shape for shape in shapes if shape.has_table]
    assert frame.is_placeholder
    assert frame.placeholder_format.type == PP_PLACEHOLDER.TABLE
    assert frame.table.cell(0, 0).text == "Волна"
    assert frame.table.cell(2, 1).text == "110"
