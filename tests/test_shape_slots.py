"""T-13: распознавание слотов из обычных фигур макета.

Критерий приёмки задачи: на VK WorkSpace у макетов находится более одного
слота.

Это основной режим работы, а не фолбэк: в двух калибровочных шаблонах из трёх
у большинства макетов есть только плейсхолдер заголовка, а остальное
размечено обычными фигурами. Без распознавания фигур колода на таком шаблоне
вырождается в набор заголовков — это подтверждено прогоном вёрстки на T-07,
а не предположением.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dpd.parsing import parse_template

CALIBRATION = Path(__file__).resolve().parents[1] / "assets" / "templates" / "calibration"

WORKSPACE = "VK_WorkSpace_Клиентская_конференция_Шаблон_03.pptx"
VK_TECH = "VK Tech шаблон.pptx"
EDUCATION = "Шаблон презентации VK Education.pptx"


def requires(name: str) -> Path:
    path = CALIBRATION / name
    if not path.exists():
        pytest.skip(f"нет калибровочного шаблона {name}")
    return path


def test_workspace_layouts_get_more_than_one_slot() -> None:
    """Критерий приёмки задачи, уточнённый по измерению.

    Бэклог требовал «у макетов находится более одного слота». Буквально для
    всех пятнадцати это недостижимо, и не из-за слабости распознавания: два
    макета VK WorkSpace целиком заняты иллюстрацией во всю высоту холста,
    ещё один — почти целиком. Места под текст там физически нет, и
    сконструировать его значило бы положить текст поверх картинки — то есть
    породить дефект вместо слота.

    Поэтому проверяется, что место под содержимое находится у подавляющего
    большинства макетов. Расхождение с формулировкой бэклога зафиксировано
    в `tasks.md` и в `open-questions.md` (T13).
    """
    layouts = parse_template(requires(WORKSPACE)).layouts
    rich = [layout for layout in layouts if len(layout.slots) > 1]
    assert len(rich) / len(layouts) >= 0.8, (
        f"только {len(rich)} из {len(layouts)} макетов получили больше одного слота"
    )


def test_shape_slots_are_marked_as_such() -> None:
    """Происхождение слота влияет на доверие к нему и должно быть видно."""
    slots = [slot for layout in parse_template(requires(WORKSPACE)).layouts for slot in layout.slots]
    origins = {slot.origin for slot in slots}
    assert "shape" in origins, "ни один слот не выведен из фигуры"
    assert origins <= {"placeholder", "shape", "derived"}


@pytest.mark.parametrize("name", [WORKSPACE, VK_TECH, EDUCATION])
def test_slot_geometry_stays_normalised(name: str) -> None:
    for layout in parse_template(requires(name)).layouts:
        for slot in layout.slots:
            assert 0 <= slot.bounds.x <= 1
            assert 0 <= slot.bounds.y <= 1
            assert slot.bounds.w > 0
            assert slot.bounds.h > 0


@pytest.mark.parametrize("name", [WORKSPACE, VK_TECH, EDUCATION])
def test_slot_ids_are_unique_within_a_layout(name: str) -> None:
    """Слот адресуется при вёрстке и при экспорте — дубликаты недопустимы."""
    for layout in parse_template(requires(name)).layouts:
        ids = [slot.id for slot in layout.slots]
        assert len(ids) == len(set(ids)), f"дубли в макете {layout.name}: {ids}"


def test_decorations_are_not_mistaken_for_slots() -> None:
    """Логотипы и мелкие подписи слотами не становятся.

    Ложный слот хуже пропущенного: вёрстка положит в него текст, и он уедет
    в угол поверх логотипа.
    """
    layouts = parse_template(requires(WORKSPACE)).layouts
    shape_slots = [slot for layout in layouts for slot in layout.slots if slot.origin == "shape"]
    assert shape_slots
    for slot in shape_slots:
        area = slot.bounds.w * slot.bounds.h
        assert area >= 0.01, f"слот {slot.id} занимает {area:.3%} холста — это декорация"


def test_background_is_not_a_slot() -> None:
    """Фигура во весь холст — фон, а не место для содержимого."""
    for name in (WORKSPACE, VK_TECH):
        for layout in parse_template(requires(name)).layouts:
            for slot in layout.slots:
                if slot.origin != "shape":
                    continue
                assert slot.bounds.w * slot.bounds.h < 0.9, f"{slot.id} накрывает весь холст"


def test_placeholders_are_still_preferred() -> None:
    """Плейсхолдер надёжнее выведенной фигуры и не должен ею подменяться."""
    layouts = parse_template(requires(VK_TECH)).layouts
    with_title = [
        layout for layout in layouts if any(slot.kind == "title" for slot in layout.slots)
    ]
    assert with_title
    for layout in with_title:
        title = next(slot for slot in layout.slots if slot.kind == "title")
        if any(slot.origin == "placeholder" for slot in layout.slots):
            assert title.origin == "placeholder"
