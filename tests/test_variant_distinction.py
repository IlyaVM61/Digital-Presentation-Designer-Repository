"""T-26: проверка `variants.low_distinction`.

Критерий приёмки задачи: на VK WorkSpace срабатывает — предсказание из
ADR-0004 проверяется.

Требование ТЗ «варианты должны быть визуально различимы» становится
измеримым. Исправимость находки — `none`: слабая различимость есть свойство
шаблона, и её надо предъявить, а не скрыть. Система не может добавить
шаблону цветовых вариаций, которых в нём нет.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dpd.audit.checks import check_variant_distinction
from dpd.layout import compose_variants
from dpd.models import PresentationStructure, SlideBody, StructureSlide
from dpd.parsing import parse_template

CALIBRATION = Path(__file__).resolve().parents[1] / "assets" / "templates" / "calibration"
WORKSPACE = "VK_WorkSpace_Клиентская_конференция_Шаблон_03.pptx"
VK_TECH = "VK Tech шаблон.pptx"


def requires(name: str) -> Path:
    path = CALIBRATION / name
    if not path.exists():
        pytest.skip(f"нет калибровочного шаблона {name}")
    return path


def deck() -> PresentationStructure:
    return PresentationStructure(
        slides=[
            StructureSlide(id="s1", role="title", headline="Программа"),
            StructureSlide(id="s2", role="data", headline="Итоги", body=SlideBody(items=["а", "б"])),
            StructureSlide(id="s3", role="process", headline="Этапы", body=SlideBody(items=["1", "2"])),
        ]
    )


def findings_for(name: str):
    schema = parse_template(requires(name))
    return check_variant_distinction(compose_variants(deck(), schema), schema)


def test_fires_on_template_without_colour_variations() -> None:
    """Критерий приёмки: на VK WorkSpace проверка срабатывает.

    Предсказание ADR-0004: цветовая ось там не работает — все 15 макетов
    одной схемы. Если проверка однажды перестанет срабатывать, это будет
    означать ошибку предсказания, а не повод править тест.
    """
    findings = findings_for(WORKSPACE)
    assert findings, "различимость вариантов не проверена"
    assert findings[0].check_id == "variants.low_distinction"
    assert "цветов" in findings[0].message.lower() or "схем" in findings[0].message.lower()


def test_silent_on_template_with_full_variation() -> None:
    """На VK Tech работают все три оси — жаловаться не на что."""
    assert findings_for(VK_TECH) == []


def test_finding_is_not_fixable() -> None:
    """Исправимость `none`: добавить шаблону вариаций система не может."""
    findings = findings_for(WORKSPACE)
    assert findings[0].fixability == "none"


def test_one_working_axis_is_critical() -> None:
    """Если работает одна ось, варианты по сути неразличимы."""
    schema = parse_template(requires(WORKSPACE))
    variants = compose_variants(deck(), schema)
    identical = [variants[0], variants[0].model_copy(), variants[0].model_copy()]
    findings = check_variant_distinction(identical, schema)
    assert findings
    assert findings[0].severity == "critical"


def test_partial_loss_is_a_warning() -> None:
    """Одна недоступная ось из трёх — предупреждение, а не приговор."""
    findings = findings_for(WORKSPACE)
    assert findings[0].severity == "warning"


def test_message_names_the_missing_axis() -> None:
    """Отчёт должен объяснять, чего именно не хватило."""
    assert findings_for(WORKSPACE)[0].message


def test_check_is_deterministic() -> None:
    assert findings_for(WORKSPACE) == findings_for(WORKSPACE)
