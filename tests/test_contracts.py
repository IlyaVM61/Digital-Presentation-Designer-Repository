"""T-05: контракты `TemplateSchema` и `RenderedPresentation` — круговое преобразование.

Критерий приёмки задачи: модели сериализуются в JSON и обратно.

Проверяется не только равенство объекта самому себе после круга, но и форма
JSON. Контракты описаны в `docs/04-architecture/template-schema.md` и
`pipeline-architecture.md` в camelCase, и по ним же будет генерироваться
JSON Schema для валидации ответов моделей. Если Python-сериализация даст
`width_emu` вместо `widthEmu`, нормативные документы и код разойдутся молча.

Значения в фикстурах вымышленные. Брать их из калибровочных шаблонов нельзя:
тест, настроенный на VK Tech, перестанет отличать работающий контракт от
контракта, подогнанного под один файл.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from dpd.models import (
    Bounds,
    Canvas,
    Layout,
    RenderedElement,
    RenderedPresentation,
    Slide,
    Slot,
    TemplateSchema,
    TemplateSource,
    TextRun,
)


@pytest.fixture
def template() -> TemplateSchema:
    return TemplateSchema(
        schema_version="1.0",
        source=TemplateSource(file="deck-template.pptx", hash="sha256:" + "ab" * 32),
        canvas=Canvas(width_emu=12192000, height_emu=6858000),
        layouts=[
            Layout(
                id="master1/layout2",
                name="Заголовок и контент",
                slots=[
                    Slot(
                        id="title",
                        kind="title",
                        origin="placeholder",
                        bounds=Bounds(x=0.06, y=0.08, w=0.88, h=0.18),
                    ),
                    Slot(
                        id="body-1",
                        kind="body",
                        origin="shape",
                        bounds=Bounds(x=0.06, y=0.30, w=0.88, h=0.58),
                    ),
                ],
            )
        ],
    )


@pytest.fixture
def rendered() -> RenderedPresentation:
    return RenderedPresentation(
        variant="A",
        template_hash="sha256:" + "ab" * 32,
        canvas=Canvas(width_emu=12192000, height_emu=6858000),
        slides=[
            Slide(
                id="s1",
                layout_id="master1/layout2",
                elements=[
                    RenderedElement(
                        slot_id="title",
                        kind="text",
                        bounds=Bounds(x=0.06, y=0.08, w=0.88, h=0.18),
                        runs=[TextRun(text="Итоги пилота", size_pt=32.0, bold=True)],
                    ),
                    RenderedElement(
                        slot_id="body-1",
                        kind="text",
                        bounds=Bounds(x=0.06, y=0.30, w=0.88, h=0.58),
                        runs=[TextRun(text="Первый абзац содержания.")],
                    ),
                ],
            )
        ],
    )


def test_template_schema_round_trip(template: TemplateSchema) -> None:
    restored = TemplateSchema.model_validate_json(template.model_dump_json(by_alias=True))
    assert restored == template


def test_rendered_presentation_round_trip(rendered: RenderedPresentation) -> None:
    restored = RenderedPresentation.model_validate_json(rendered.model_dump_json(by_alias=True))
    assert restored == rendered


def test_json_keys_are_camel_case_as_documented(template: TemplateSchema) -> None:
    payload = json.loads(template.model_dump_json(by_alias=True))
    assert "schemaVersion" in payload
    assert {"widthEmu", "heightEmu"} <= payload["canvas"].keys()
    assert "parsedAt" in payload["source"]


def test_rendered_json_keys_are_camel_case(rendered: RenderedPresentation) -> None:
    payload = json.loads(rendered.model_dump_json(by_alias=True))
    assert "templateHash" in payload
    slide = payload["slides"][0]
    assert "layoutId" in slide
    assert "slotId" in slide["elements"][0]
    assert "sizePt" in slide["elements"][0]["runs"][0]


def test_python_names_also_accepted(template: TemplateSchema) -> None:
    """Код внутри пайплайна оперирует snake_case, а не алиасами."""
    restored = TemplateSchema.model_validate(template.model_dump())
    assert restored == template


@pytest.mark.parametrize(
    "bad",
    [
        {"x": -0.01, "y": 0.0, "w": 0.5, "h": 0.5},
        {"x": 0.0, "y": 0.0, "w": 1.5, "h": 0.5},
        {"x": 0.0, "y": 1.2, "w": 0.5, "h": 0.5},
    ],
)
def test_bounds_outside_canvas_are_rejected(bad: dict[str, float]) -> None:
    """Геометрия — только доли [0, 1]: нормативное требование template-schema.md."""
    with pytest.raises(ValidationError):
        Bounds(**bad)


def test_bounds_allow_element_to_overflow_the_canvas_edge() -> None:
    """Выход за правый край — дефект вёрстки, а не невалидный контракт.

    Проверка `layout.out_of_bounds` должна иметь возможность его увидеть,
    поэтому контракт запрещает только координаты вне [0, 1] по отдельности.
    """
    assert Bounds(x=0.9, y=0.9, w=0.5, h=0.5)


def test_colors_must_be_hex() -> None:
    assert TextRun(text="ок", color="#0077FF")
    with pytest.raises(ValidationError):
        TextRun(text="не ок", color="синий")


def test_template_requires_at_least_one_layout() -> None:
    """Схема без макетов невалидна: пайплайн на ней не запускается (EF-6)."""
    with pytest.raises(ValidationError):
        TemplateSchema(
            schema_version="1.0",
            source=TemplateSource(file="empty.pptx", hash="sha256:" + "00" * 32),
            canvas=Canvas(width_emu=12192000, height_emu=6858000),
            layouts=[],
        )
