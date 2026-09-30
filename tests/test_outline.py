"""T-38: план колоды, введённый человеком, — в контракт `PresentationStructure`.

**Это не генерация.** Смысл приносит пользователь: он пишет заголовки и
пункты, а функция лишь переводит написанное в контракт. Настоящая генерация
структуры по свободному брифу — задача T-49, и она работает моделью.

Путь нужен раньше неё по двум причинам. Интерфейс (T-38) требует чего-то
работающего от файла до экспорта уже сейчас, а детерминированный ввод
останется полезным и потом: на защите он даёт воспроизводимую колоду без
обращения к модели, а пользователю — способ поправить план вручную.
"""

from __future__ import annotations

import pytest

from dpd.generation import structure_from_outline


def test_line_without_a_dash_starts_a_slide() -> None:
    structure = structure_from_outline("Итоги пилота\nЧто изменилось")

    assert [slide.headline for slide in structure.slides] == ["Итоги пилота", "Что изменилось"]


def test_dashed_lines_become_bullets_of_the_slide_above() -> None:
    structure = structure_from_outline(
        "Что изменилось\n- Срок сборки сократился втрое\n- Правки переживают сохранение"
    )

    assert len(structure.slides) == 1
    assert structure.slides[0].body is not None
    assert structure.slides[0].body.items == [
        "Срок сборки сократился втрое",
        "Правки переживают сохранение",
    ]


def test_first_slide_gets_the_title_role() -> None:
    """Первый слайд — титульный: так его и подаст вёрстка.

    Роль определяет выбор макета, и титульный слайд с контентным макетом
    выглядел бы как оборванный раздел.
    """
    structure = structure_from_outline("Итоги пилота\nЧто изменилось\n- Пункт")

    assert structure.slides[0].role == "title"
    assert structure.slides[1].role != "title"


def test_blank_lines_and_spaces_are_ignored() -> None:
    structure = structure_from_outline("\n  Итоги пилота  \n\n  -   Первый пункт  \n\n")

    assert structure.slides[0].headline == "Итоги пилота"
    assert structure.slides[0].body.items == ["Первый пункт"]


@pytest.mark.parametrize("dash", ["-", "–", "—", "*", "•"])
def test_common_bullet_marks_are_understood(dash: str) -> None:
    """Пункт можно пометить любым привычным знаком.

    Человек печатает то, что у него под рукой, и отказ понимать тире вместо
    дефиса выглядел бы придиркой машины.
    """
    structure = structure_from_outline(f"Заголовок\n{dash} Пункт")

    assert structure.slides[0].body.items == ["Пункт"]


def test_bullet_before_any_headline_is_not_lost() -> None:
    """Пункт без заголовка выше становится слайдом, а не исчезает.

    Потерять введённый человеком текст хуже, чем показать его не в том виде:
    пропажу он заметит не сразу, а разбираться будет долго.
    """
    structure = structure_from_outline("- Одинокий пункт")

    assert [slide.headline for slide in structure.slides] == ["Одинокий пункт"]


def test_empty_outline_is_refused() -> None:
    """Пустой план — не колода из нуля слайдов, а несделанная работа."""
    with pytest.raises(ValueError, match="план"):
        structure_from_outline("   \n\n  ")
