"""T-54: чему верить, когда шаблон сам себе противоречит.

Правило общее для всех наследуемых свойств оформления, а не про шрифт:
**свойство, доставшееся с уровня мастера или темы, слабее частотного
токена.** Мастер и тема — объявление шаблона; токены собраны частотным
анализом того, чем он набран на деле.

Основание измерено: в VK Tech из 774 прогонов Play задан явно в 610 и
унаследован от макета ещё в 93, Consolas — в 70, Arial — **ровно в одном**.
Этого хватило, чтобы Arial попал в состав шаблона, а оттуда — в основной
текст наших слайдов, потому что макет «13_Контент» своего шрифта не
объявляет и значение приходит от мастера, повторяющего тему.

**Решение записывается и переключается.** Система не вправе молча менять
оформление: она сообщает, что выбрала и из чего, а правило переключается в
`configs/layout.yaml`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dpd.generation import structure_from_outline
from dpd.layout import compose_variants
from dpd.layout.styling import style_for
from dpd.models import (
    Bounds,
    ColorToken,
    DesignTokens,
    FontToken,
    Slot,
    TextStyle,
    TypeScale,
)
from dpd.parsing import parse_template

CALIBRATION = Path(__file__).resolve().parents[1] / "assets" / "templates" / "calibration"


def tokens() -> DesignTokens:
    return DesignTokens(
        fonts=[
            FontToken(role="body", family="Play", confidence=0.91),
            FontToken(role="secondary", family="Arial", confidence=0.01),
        ],
        colors=[ColorToken(role="text.primary", value="#000000", confidence=0.6)],
        type_scale=TypeScale(values=[12.0, 14.0, 18.0, 24.0, 36.0, 48.0]),
    )


def slot(*, font: str, size: float, level: str, kind: str = "body") -> Slot:
    return Slot(
        id="body",
        kind=kind,
        origin="placeholder",
        bounds=Bounds(x=0.1, y=0.2, w=0.6, h=0.5),
        text_style=TextStyle(font=font, size_pt=size, resolved_from=level, font_resolved_from=level),
    )


# --- Правило ---------------------------------------------------------------


def test_property_inherited_from_the_master_gives_way_to_the_markup() -> None:
    """Мастер повторяет тему, а тема врёт — значит верим разметке."""
    решения: list = []

    style = style_for(slot(font="Arial", size=14.0, level="master.txStyles"), tokens(), decisions=решения)

    assert style.font == "Play"
    assert [решение.kind for решение in решения] == ["font", "size"] or "font" in [
        решение.kind for решение in решения
    ]


def test_property_stated_by_the_layout_is_left_alone() -> None:
    """Макет объявил шрифт для этого места — это и есть замысел автора.

    Правило касается только унаследованного с самого верха: то, что макет
    говорит о себе сам, трогать нельзя.
    """
    решения: list = []

    style = style_for(slot(font="Arial", size=18.0, level="layout.lstStyle"), tokens(), decisions=решения)

    assert style.font == "Arial"
    assert style.size_pt == 18.0
    assert решения == []


def test_size_inherited_from_the_master_comes_from_the_scale() -> None:
    """Кегль 14pt у заголовка — след того же наследования, что и Arial.

    Это дефект T16: заголовок VK Education набирался кеглем основного
    текста, потому что макет своего кегля не объявляет.
    """
    решения: list = []

    style = style_for(
        slot(font="Play", size=14.0, level="master.txStyles", kind="title"), tokens(), decisions=решения
    )

    assert style.size_pt is not None and style.size_pt > 14.0
    assert any(решение.kind == "size" for решение in решения)


def test_decision_says_what_was_chosen_and_what_was_declared() -> None:
    """Пользователь должен видеть выбор и альтернативу, а не результат."""
    решения: list = []

    style_for(slot(font="Arial", size=14.0, level="theme.fontScheme"), tokens(), decisions=решения)

    решение = next(item for item in решения if item.kind == "font")
    assert решение.chosen == "Play"
    assert решение.declared == "Arial"
    assert "91" in решение.reason or "разметк" in решение.reason.lower()


def test_rule_can_be_switched_to_trust_the_template() -> None:
    """Переключатель есть: пользователь вправе решить иначе.

    Правило — данные, а не код: `configs/layout.yaml`.
    """
    решения: list = []

    style = style_for(
        slot(font="Arial", size=14.0, level="master.txStyles"),
        tokens(),
        decisions=решения,
        rule="template",
    )

    assert style.font == "Arial"
    assert решения == []


# --- Живые шаблоны ---------------------------------------------------------


def test_vk_tech_body_is_set_in_the_font_of_the_template() -> None:
    """Регрессия на дефект, найденный владельцем в HTML-выгрузке.

    Основной текст набирался Arial — шрифтом, который встречается в шаблоне
    один раз из 774 прогонов.
    """
    template_file = CALIBRATION / "VK Tech шаблон.pptx"
    if not template_file.exists():
        pytest.skip("нет калибровочного шаблона")

    schema = parse_template(template_file)
    план = structure_from_outline("Итоги пилота\nЧто изменилось\n- Первый пункт\n- Второй пункт")
    deck = compose_variants(план, schema)[0]

    шрифты = {run.font for slide in deck.slides for element in slide.elements for run in element.runs}
    assert "Arial" not in шрифты, f"текст набран не тем шрифтом: {шрифты}"
    assert "Play" in шрифты


def test_decisions_reach_the_deck() -> None:
    """Решения видны в колоде: интерфейсу есть что показать пользователю."""
    template_file = CALIBRATION / "VK Tech шаблон.pptx"
    if not template_file.exists():
        pytest.skip("нет калибровочного шаблона")

    schema = parse_template(template_file)
    план = structure_from_outline("Итоги пилота\nЧто изменилось\n- Первый пункт")
    deck = compose_variants(план, schema)[0]

    assert deck.decisions, "колода не рассказывает о принятых решениях"
    assert all(решение.reason for решение in deck.decisions)
