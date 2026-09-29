"""T-21: укладка содержимого с применением дизайн-токенов.

Критерий приёмки задачи: цвета и гарнитуры результата принадлежат составу
шаблона.

Это то, ради чего извлекались токены. Вёрстка не выбирает оформление — она
применяет правила, прочитанные из файла. Цвет или гарнитура, которых в
шаблоне нет, означали бы, что система сочинила дизайн вместо того, чтобы
его соблюсти, и проверка «слайд собран не по шаблону» обязана такое ловить.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dpd.layout import compose
from dpd.models import PresentationStructure, SlideBody, StructureSlide
from dpd.parsing import parse_template

CALIBRATION = Path(__file__).resolve().parents[1] / "assets" / "templates" / "calibration"
ALL = [
    "VK Tech шаблон.pptx",
    "VK_WorkSpace_Клиентская_конференция_Шаблон_03.pptx",
    "Шаблон презентации VK Education.pptx",
]


def requires(name: str) -> Path:
    path = CALIBRATION / name
    if not path.exists():
        pytest.skip(f"нет калибровочного шаблона {name}")
    return path


def deck() -> PresentationStructure:
    return PresentationStructure(
        slides=[
            StructureSlide(id="s1", role="title", headline="Программа наставничества"),
            StructureSlide(
                id="s2",
                role="data",
                headline="Пилот окупился за четыре квартала",
                body=SlideBody(kind="bullets", items=["Экономия 9,4 млн", "Затраты 2,8 млн"]),
            ),
        ]
    )


def rendered_runs(name: str):
    schema = parse_template(requires(name))
    rendered = compose(deck(), schema)
    runs = [run for slide in rendered.slides for element in slide.elements for run in element.runs]
    return schema, runs


@pytest.mark.parametrize("name", ALL)
def test_fonts_belong_to_the_template(name: str) -> None:
    """Критерий приёмки: гарнитуры — из состава шаблона."""
    schema, runs = rendered_runs(name)
    allowed = {token.family for token in schema.design_tokens.fonts}
    used = {run.font for run in runs if run.font}
    assert used, "ни один прогон не получил гарнитуру"
    assert used <= allowed, f"чужие гарнитуры: {used - allowed}"


@pytest.mark.parametrize("name", ALL)
def test_colors_belong_to_the_template(name: str) -> None:
    """Критерий приёмки: цвета — из состава шаблона.

    Допускаются и цвета токенов, и цвета, разрешённые для слотов по цепочке
    наследования: и то и другое прочитано из файла, а не придумано.
    """
    schema, runs = rendered_runs(name)
    allowed = {token.value for token in schema.design_tokens.colors}
    allowed |= {
        slot.text_style.color
        for layout in schema.layouts
        for slot in layout.slots
        if slot.text_style and slot.text_style.color
    }
    used = {run.color for run in runs if run.color}
    assert used <= allowed, f"чужие цвета: {used - allowed}"


@pytest.mark.parametrize("name", ALL)
def test_sizes_belong_to_the_cleaned_scale(name: str) -> None:
    """Кегли — из очищенной шкалы, без следов автоподгонки.

    Именно поэтому шкала чистилась в T-16: непочищенная разрешила бы
    поставить 6,75 как «родной для шаблона».
    """
    schema, runs = rendered_runs(name)
    allowed = set(schema.design_tokens.type_scale.values)
    excluded = set(schema.design_tokens.type_scale.excluded)
    used = {run.size_pt for run in runs if run.size_pt}
    assert not (used & excluded), f"использованы кегли автоподгонки: {used & excluded}"
    assert used <= allowed | {None}, f"кегли вне шкалы: {used - allowed}"


@pytest.mark.parametrize("name", ALL)
def test_every_run_is_styled(name: str) -> None:
    """Оформление задаётся явно: экспорт не должен угадывать."""
    _, runs = rendered_runs(name)
    for run in runs:
        assert run.font
        assert run.size_pt


def test_title_and_body_differ_in_size() -> None:
    """Заголовок и текст не могут быть одного кегля — иерархия потеряется."""
    _, runs = rendered_runs(ALL[0])
    sizes = {run.size_pt for run in runs}
    assert len(sizes) > 1, "весь текст одного кегля"


def test_application_is_deterministic() -> None:
    schema = parse_template(requires(ALL[0]))
    first = compose(deck(), schema).model_dump_json(by_alias=True)
    second = compose(deck(), schema).model_dump_json(by_alias=True)
    assert first == second
