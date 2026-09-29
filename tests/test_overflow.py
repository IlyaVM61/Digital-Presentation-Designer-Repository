"""T-22: обработка переполнения с фиксацией компенсаций.

Критерий приёмки задачи: `appliedCompensations` заполнено, текст не выходит
за границы слота.

Поле обязательно по требованию FR-18: пользователь должен узнать, что его
замысел был изменён, чтобы поместиться. Молчаливое уменьшение кегля — то
самое поведение, за которое к презентациям и возникают претензии: слайд
выглядит нормально, а почему шрифт мельче соседнего, никто объяснить не
может.
"""

from __future__ import annotations

import pytest

from dpd.layout import compose
from dpd.layout.overflow import fits, plan_compensations
from dpd.models import (
    Bounds,
    Canvas,
    Layout,
    PresentationStructure,
    SlideBody,
    Slot,
    StructureSlide,
    TemplateSchema,
    TemplateSource,
    TextStyle,
)


def slot(kind: str, w: float, h: float, size: float) -> Slot:
    return Slot(
        id=f"{kind}-1" if kind != "title" else "title",
        kind=kind,
        origin="placeholder",
        bounds=Bounds(x=0.05, y=0.1, w=w, h=h),
        text_style=TextStyle(font="Play", size_pt=size, color="#000000", resolved_from="run"),
    )


def template(*slots: Slot) -> TemplateSchema:
    return TemplateSchema(
        source=TemplateSource(file="t.pptx", hash="sha256:" + "ab" * 32),
        canvas=Canvas(width_emu=12192000, height_emu=6858000),
        layouts=[Layout(id="m/l1", name="Контент", family="content", slots=list(slots))],
    )


LONG = ["Очень длинный пункт, который заведомо не помещается в отведённое место" * 3] * 6


def test_short_text_fits() -> None:
    assert fits(["Коротко"], slot("body", 0.8, 0.5, 14.0), Canvas(width_emu=12192000, height_emu=6858000))


def test_long_text_does_not_fit() -> None:
    tiny = slot("body", 0.2, 0.05, 28.0)
    assert not fits(LONG, tiny, Canvas(width_emu=12192000, height_emu=6858000))


def test_overflow_produces_compensations() -> None:
    """Критерий приёмки: компенсации фиксируются."""
    schema = template(slot("title", 0.9, 0.1, 40.0), slot("body", 0.3, 0.08, 28.0))
    structure = PresentationStructure(
        slides=[StructureSlide(id="s1", role="data", headline="З", body=SlideBody(items=LONG))]
    )
    slide = compose(structure, schema).slides[0]
    assert slide.applied_compensations, "переполнение не зафиксировано"


def test_compensation_records_what_changed() -> None:
    schema = template(slot("title", 0.9, 0.1, 40.0), slot("body", 0.3, 0.08, 28.0))
    structure = PresentationStructure(
        slides=[StructureSlide(id="s1", role="data", headline="З", body=SlideBody(items=LONG))]
    )
    compensation = compose(structure, schema).slides[0].applied_compensations[0]
    assert compensation.slot_id
    assert compensation.kind in {"fontScale", "truncate"}
    assert compensation.reason


def test_fitting_text_produces_no_compensations() -> None:
    """Ложная компенсация вводит в заблуждение не меньше молчания."""
    schema = template(slot("title", 0.9, 0.2, 24.0), slot("body", 0.8, 0.5, 14.0))
    structure = PresentationStructure(
        slides=[
            StructureSlide(id="s1", role="data", headline="Итоги", body=SlideBody(items=["Кратко"]))
        ]
    )
    assert compose(structure, schema).slides[0].applied_compensations == []


def test_font_scale_stays_within_the_template_scale() -> None:
    """Уменьшать кегль можно только до значений, которые есть в шаблоне."""
    schema = template(slot("title", 0.9, 0.1, 40.0), slot("body", 0.3, 0.08, 28.0))
    schema.design_tokens = None
    style = slot("body", 0.3, 0.08, 28.0)
    canvas = Canvas(width_emu=12192000, height_emu=6858000)
    compensations, runs = plan_compensations(LONG, style, canvas, [10.0, 12.0, 14.0, 28.0])
    for compensation in compensations:
        if compensation.kind == "fontScale":
            assert compensation.to_value in (10.0, 12.0, 14.0)
    assert runs


def test_text_is_truncated_only_as_a_last_resort() -> None:
    """Обрезка — потеря содержания, к ней переходят, когда кегль исчерпан."""
    canvas = Canvas(width_emu=12192000, height_emu=6858000)
    compensations, _ = plan_compensations(LONG, slot("body", 0.2, 0.05, 12.0), canvas, [10.0, 12.0])
    kinds = [compensation.kind for compensation in compensations]
    if "truncate" in kinds:
        assert kinds.index("truncate") == len(kinds) - 1


def test_result_fits_after_compensation() -> None:
    """Критерий приёмки: после компенсаций текст не выходит за границы."""
    canvas = Canvas(width_emu=12192000, height_emu=6858000)
    target = slot("body", 0.3, 0.1, 28.0)
    _, runs = plan_compensations(LONG, target, canvas, [8.0, 10.0, 12.0, 28.0])
    assert fits([run.text for run in runs], target, canvas, size_pt=runs[0].size_pt)


def test_compensations_are_deterministic() -> None:
    schema = template(slot("title", 0.9, 0.1, 40.0), slot("body", 0.3, 0.08, 28.0))
    structure = PresentationStructure(
        slides=[StructureSlide(id="s1", role="data", headline="З", body=SlideBody(items=LONG))]
    )
    first = compose(structure, schema).model_dump_json(by_alias=True)
    second = compose(structure, schema).model_dump_json(by_alias=True)
    assert first == second


@pytest.mark.parametrize("items", [["одна строка"], ["a"] * 3])
def test_normal_content_survives_untouched(items: list[str]) -> None:
    canvas = Canvas(width_emu=12192000, height_emu=6858000)
    compensations, runs = plan_compensations(items, slot("body", 0.8, 0.5, 14.0), canvas, [14.0])
    assert compensations == []
    assert [run.text for run in runs] == items
