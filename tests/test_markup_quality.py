"""T-15: метрика `markupQuality` и выбор стратегии разбора.

Критерий приёмки задачи: три калибровочных шаблона получают разные
стратегии.

Ни макеты, ни слайды-примеры сами по себе не являются достаточным
источником. В одном шаблоне 36 примеров из 54 сидят на единственном
макете-пустышке, и «учиться на примерах» там бессмысленно; в другом
примеры честно покрывают 14 макетов из 15, и это лучший сигнал, какой есть.
Нужна метрика, по которой парсер решает, на что опираться — ни один из семи
изученных конкурентов такой метрики не имеет.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dpd.parsing import parse_template

CALIBRATION = Path(__file__).resolve().parents[1] / "assets" / "templates" / "calibration"

WORKSPACE = "VK_WorkSpace_Клиентская_конференция_Шаблон_03.pptx"
VK_TECH = "VK Tech шаблон.pptx"
EDUCATION = "Шаблон презентации VK Education.pptx"
ALL = [WORKSPACE, VK_TECH, EDUCATION]


def requires(name: str) -> Path:
    path = CALIBRATION / name
    if not path.exists():
        pytest.skip(f"нет калибровочного шаблона {name}")
    return path


def test_three_templates_get_three_strategies() -> None:
    """Критерий приёмки: разметка разного качества требует разного подхода."""
    strategies = {
        name: parse_template(requires(name)).markup_quality.strategy for name in ALL
    }
    assert len(set(strategies.values())) == 3, f"стратегии совпали: {strategies}"


@pytest.mark.parametrize("name", ALL)
def test_metric_is_filled(name: str) -> None:
    quality = parse_template(requires(name)).markup_quality
    assert quality.layouts_total > 0
    assert 0 <= quality.score <= 1
    assert quality.strategy


@pytest.mark.parametrize("name", ALL)
def test_counters_do_not_exceed_the_total(name: str) -> None:
    quality = parse_template(requires(name)).markup_quality
    assert quality.layouts_with_multiple_slots <= quality.layouts_total
    assert quality.layouts_with_unique_name <= quality.layouts_total
    assert quality.layouts_with_examples <= quality.layouts_total


def test_template_without_placeholder_markup_is_not_placeholder_first() -> None:
    """Там, где у макетов один плейсхолдер, опираться на плейсхолдеры нельзя.

    Это ровно случай VK WorkSpace: все 15 макетов несут только заголовок.
    """
    quality = parse_template(requires(WORKSPACE)).markup_quality
    assert quality.layouts_with_multiple_slots == 0
    assert quality.strategy != "placeholder-first"


def test_concentrated_examples_do_not_become_the_strategy() -> None:
    """Примеры, сгрудившиеся на одном макете, — плохой сигнал, а не хороший.

    В VK Tech две трети слайдов-примеров собраны на одном макете-пустышке.
    Стратегия «учиться на примерах» дала бы там 36 почти одинаковых макетов —
    ровно то, что делает presenton.
    """
    quality = parse_template(requires(VK_TECH)).markup_quality
    assert quality.example_concentration > 0.5
    assert quality.strategy != "examples-first"


@pytest.mark.parametrize("name", ALL)
def test_strategy_is_from_the_known_set(name: str) -> None:
    strategy = parse_template(requires(name)).markup_quality.strategy
    assert strategy in {"placeholder-first", "geometry-first", "examples-first"}


@pytest.mark.parametrize("name", ALL)
def test_empty_masters_are_recorded(name: str) -> None:
    """Пустой мастер меняет способ искать постоянные элементы.

    Оба мастера одного из шаблонов пусты: логотипы придётся собирать из
    макетов, а не читать из мастера.
    """
    quality = parse_template(requires(name)).markup_quality
    assert isinstance(quality.masters_empty, bool)


def test_metric_is_deterministic() -> None:
    first = parse_template(requires(WORKSPACE)).markup_quality
    second = parse_template(requires(WORKSPACE)).markup_quality
    assert first == second
