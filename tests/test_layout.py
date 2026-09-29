"""T-07: минимальная вёрстка — заголовок и текст в слотах выбранного макета.

Критерий приёмки задачи: `RenderedPresentation` содержит элементы с
координатами в долях.

Основная часть проверок идёт на синтетической `TemplateSchema`: вёрстка
обязана работать с любой схемой, а не только с той, что вышла из наших
шаблонов. Два теста дополнительно прогоняются на реальных файлах и
пропускаются, когда файлов нет.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dpd.layout import compose
from dpd.models import (
    Bounds,
    Canvas,
    Layout,
    PresentationStructure,
    RenderedPresentation,
    SlideBody,
    Slot,
    StructureSlide,
    TemplateSchema,
    TemplateSource,
)
from dpd.parsing import parse_template

CALIBRATION = Path(__file__).resolve().parents[1] / "assets" / "templates" / "calibration"


def slot(slot_id: str, kind: str, y: float) -> Slot:
    return Slot(id=slot_id, kind=kind, origin="placeholder", bounds=Bounds(x=0.1, y=y, w=0.8, h=0.2))


@pytest.fixture
def template() -> TemplateSchema:
    return TemplateSchema(
        source=TemplateSource(file="t.pptx", hash="sha256:" + "cd" * 32),
        canvas=Canvas(width_emu=12192000, height_emu=6858000),
        layouts=[
            Layout(id="master1/layout1", name="Только заголовок", slots=[slot("title", "title", 0.1)]),
            Layout(
                id="master1/layout2",
                name="Заголовок и контент",
                slots=[slot("title", "title", 0.1), slot("body-1", "body", 0.4)],
            ),
        ],
    )


@pytest.fixture
def structure() -> PresentationStructure:
    return PresentationStructure(
        slides=[
            StructureSlide(
                id="s1",
                role="data",
                headline="Пилот окупился за четыре квартала",
                body=SlideBody(kind="bullets", items=["Экономия 9,4 млн", "Затраты 2,8 млн"]),
            )
        ]
    )


def test_produces_a_rendered_presentation(structure, template) -> None:
    assert isinstance(compose(structure, template), RenderedPresentation)


def test_slide_count_matches_the_structure(structure, template) -> None:
    assert len(compose(structure, template).slides) == len(structure.slides)


def test_coordinates_are_fractions_of_the_canvas(structure, template) -> None:
    """Критерий приёмки: элементы несут координаты в долях."""
    elements = [e for s in compose(structure, template).slides for e in s.elements]
    assert elements
    for element in elements:
        assert 0 <= element.bounds.x <= 1
        assert 0 <= element.bounds.y <= 1
        assert 0 <= element.bounds.w <= 1
        assert 0 <= element.bounds.h <= 1


def test_element_geometry_comes_from_the_slot(structure, template) -> None:
    """Вёрстка не выдумывает координаты: она берёт их из слота макета."""
    slide = compose(structure, template).slides[0]
    layout = {layout.id: layout for layout in template.layouts}[slide.layout_id]
    slots = {slot.id: slot for slot in layout.slots}
    for element in slide.elements:
        assert element.bounds == slots[element.slot_id].bounds


def test_headline_goes_into_the_title_slot(structure, template) -> None:
    slide = compose(structure, template).slides[0]
    title = next(e for e in slide.elements if e.slot_id == "title")
    assert "".join(run.text for run in title.runs) == structure.slides[0].headline


def test_body_items_go_into_the_body_slot(structure, template) -> None:
    slide = compose(structure, template).slides[0]
    body = next(e for e in slide.elements if e.slot_id == "body-1")
    assert [run.text for run in body.runs] == structure.slides[0].body.items


def test_layout_chosen_from_the_template(structure, template) -> None:
    """Слайд собирается на макете шаблона, а не на выдуманном."""
    slide = compose(structure, template).slides[0]
    assert slide.layout_id in {layout.id for layout in template.layouts}


def test_layout_with_body_preferred_when_there_is_body(structure, template) -> None:
    assert compose(structure, template).slides[0].layout_id == "master1/layout2"


def test_degrades_when_no_layout_has_a_body_slot(structure) -> None:
    """У двух шаблонов из трёх большинство макетов несёт только заголовок.

    Вёрстка обязана собрать слайд и в этом случае — без текста, но собрать,
    а не упасть. Полноценная деградация типа — задача T-20.
    """
    title_only = TemplateSchema(
        source=TemplateSource(file="t.pptx", hash="sha256:" + "ef" * 32),
        canvas=Canvas(width_emu=12192000, height_emu=6858000),
        layouts=[Layout(id="master1/layout1", name="Заголовок", slots=[slot("title", "title", 0.1)])],
    )
    slide = compose(structure, title_only).slides[0]
    assert slide.layout_id == "master1/layout1"
    assert [e.slot_id for e in slide.elements] == ["title"]


def test_is_deterministic(structure, template) -> None:
    """Воспроизводимость — основание всего детерминированного аудита."""
    first = compose(structure, template)
    second = compose(structure, template)
    assert first.model_dump_json(by_alias=True) == second.model_dump_json(by_alias=True)


def test_binds_result_to_the_template(structure, template) -> None:
    assert compose(structure, template).template_hash == template.source.hash


def test_canvas_is_carried_over(structure, template) -> None:
    assert compose(structure, template).canvas == template.canvas


def test_round_trip_survives_serialisation(structure, template) -> None:
    rendered = compose(structure, template)
    assert RenderedPresentation.model_validate_json(rendered.model_dump_json(by_alias=True)) == rendered


@pytest.mark.parametrize(
    "name",
    [
        "VK Tech шаблон.pptx",
        "VK_WorkSpace_Клиентская_конференция_Шаблон_03.pptx",
        "Шаблон презентации VK Education.pptx",
    ],
)
def test_composes_on_real_templates(structure, name: str) -> None:
    path = CALIBRATION / name
    if not path.exists():
        pytest.skip(f"нет калибровочного шаблона {name}")
    rendered = compose(structure, parse_template(path))
    assert rendered.slides
    assert rendered.slides[0].elements, "ни один слот не заполнен"
