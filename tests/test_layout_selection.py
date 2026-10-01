"""T-20: выбор макета под тип слайда с деградацией типа.

Критерий приёмки задачи: при отсутствии макета выбирается ближайший,
решение попадает в отчёт.

Отказ здесь недопустим. Шаблон комиссии может не иметь макета под нужный
тип слайда — в VK WorkSpace, например, нет ни одного макета с двумя местами
под содержимое. Вёрстка обязана собрать слайд на ближайшем подходящем и
сказать об этом, а не остановить прогон.
"""

from __future__ import annotations

import pytest

from dpd.layout import compose
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
)
from dpd.models.structure import TableSpec, Visualization


def slot(slot_id: str, kind: str, y: float) -> Slot:
    return Slot(id=slot_id, kind=kind, origin="placeholder", bounds=Bounds(x=0.1, y=y, w=0.8, h=0.2))


def template(*layouts: Layout) -> TemplateSchema:
    return TemplateSchema(
        source=TemplateSource(file="t.pptx", hash="sha256:" + "ab" * 32),
        canvas=Canvas(width_emu=12192000, height_emu=6858000),
        layouts=list(layouts),
    )


TITLE_ONLY = Layout(
    id="m/l1", name="Титул", family="title", slots=[slot("title", "title", 0.4)]
)
CONTENT = Layout(
    id="m/l2",
    name="Контент",
    family="content",
    slots=[slot("title", "title", 0.06), slot("body-1", "body", 0.3)],
)
SPLIT = Layout(
    id="m/l3",
    name="Две колонки",
    family="split",
    slots=[slot("title", "title", 0.06), slot("body-1", "body", 0.3), slot("body-2", "body", 0.6)],
)


def structure(role: str, items: int = 1) -> PresentationStructure:
    body = SlideBody(kind="bullets", items=[f"пункт {n}" for n in range(items)]) if items else None
    return PresentationStructure(
        slides=[StructureSlide(id="s1", role=role, headline="Заголовок", body=body)]
    )


def test_title_slide_takes_a_title_layout() -> None:
    rendered = compose(structure("title", items=0), template(TITLE_ONLY, CONTENT))
    assert rendered.slides[0].layout_id == "m/l1"


def test_content_slide_takes_a_content_layout() -> None:
    rendered = compose(structure("data"), template(TITLE_ONLY, CONTENT, SPLIT))
    assert rendered.slides[0].layout_id == "m/l2"


@pytest.mark.parametrize("role", ["section", "closing"])
def test_title_like_slide_with_a_table_moves_to_a_content_layout(role: str) -> None:
    """Найдено рендером T-50: заставка с таблицей волн легла на макет раздела
    без слота содержимого, и таблица пропала молча. Визуализация — такое же
    содержимое, как тело."""
    table = Visualization(kind="table", table=TableSpec(headers=["Волна", "Пар"], rows=[["Первая", "140"]]))
    slide = StructureSlide(id="s1", role=role, headline="План", visualization=table)
    rendered = compose(PresentationStructure(slides=[slide]), template(TITLE_ONLY, CONTENT))

    assert rendered.slides[0].layout_id == "m/l2"
    assert [element.kind for element in rendered.slides[0].elements] == ["text", "table"]


def test_missing_family_degrades_to_the_nearest() -> None:
    """Критерий приёмки: макета нужного типа нет — берётся ближайший."""
    rendered = compose(structure("data"), template(TITLE_ONLY))
    slide = rendered.slides[0]
    assert slide.layout_id == "m/l1"
    assert slide.layout_decision.degraded is True


def test_degradation_is_recorded_with_a_reason() -> None:
    """Решение попадает в отчёт: пользователь должен понимать, что произошло."""
    slide = compose(structure("data"), template(TITLE_ONLY)).slides[0]
    decision = slide.layout_decision
    assert decision.requested_family == "content"
    assert decision.chosen_family == "title"
    assert decision.reason


def test_exact_match_is_not_marked_as_degraded() -> None:
    slide = compose(structure("data"), template(CONTENT)).slides[0]
    assert slide.layout_decision.degraded is False


def test_every_slide_carries_a_decision() -> None:
    deck = PresentationStructure(
        slides=[
            StructureSlide(id="s1", role="title", headline="А"),
            StructureSlide(id="s2", role="data", headline="Б", body=SlideBody(items=["х"])),
        ]
    )
    for slide in compose(deck, template(TITLE_ONLY, CONTENT)).slides:
        assert slide.layout_decision is not None


def test_selection_is_deterministic() -> None:
    schema = template(TITLE_ONLY, CONTENT, SPLIT)
    first = compose(structure("data"), schema).model_dump_json(by_alias=True)
    second = compose(structure("data"), schema).model_dump_json(by_alias=True)
    assert first == second


def test_unknown_role_still_produces_a_slide() -> None:
    """Неизвестная роль не повод отказать: слайд собирается, решение пишется."""
    slide = compose(structure("невиданная-роль"), template(CONTENT)).slides[0]
    assert slide.elements
    assert slide.layout_decision is not None


@pytest.mark.parametrize("role", ["title", "agenda", "data", "quote", "process", "summary"])
def test_known_roles_are_mapped(role: str) -> None:
    slide = compose(structure(role), template(TITLE_ONLY, CONTENT, SPLIT)).slides[0]
    assert slide.layout_id
    assert slide.layout_decision.requested_family
