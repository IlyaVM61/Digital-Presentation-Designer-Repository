"""T-25: три варианта по профилям визуального регистра.

Критерий приёмки задачи: три `RenderedPresentation` различаются макетами,
цветовой схемой и подачей данных.

Требование ТЗ о трёх визуально различимых вариантах — один из семи пунктов,
не подлежащих упрощению. Ось различий выбрана решением D2 и обоснована в
ADR-0004: плотность контента отвергнута, потому что при неизменном контенте
достижима только перераспределением текста, то есть меняет генерацию;
цветовая схема отвергнута как единственная ось, потому что в шаблоне её
вариаций может не быть вовсе.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dpd.layout import compose_variants, load_profiles
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
                body=SlideBody(items=["Экономия 9,4 млн", "Затраты 2,8 млн", "Срок 4 квартала"]),
            ),
            StructureSlide(
                id="s3",
                role="process",
                headline="Этапы внедрения",
                body=SlideBody(items=["Отбор", "Обучение", "Сопровождение"]),
            ),
        ]
    )


def test_profiles_live_outside_the_code() -> None:
    """Профили — данные, а не код: их правка не требует изменений в исходниках."""
    profiles = load_profiles()
    assert len(profiles) == 3
    assert {profile.id for profile in profiles} == {"A", "B", "C"}


def test_three_variants_are_produced() -> None:
    variants = compose_variants(deck(), parse_template(requires(ALL[0])))
    assert len(variants) == 3
    assert [variant.variant for variant in variants] == ["A", "B", "C"]


def test_variants_differ_in_layouts() -> None:
    """Критерий приёмки: варианты различаются макетами."""
    variants = compose_variants(deck(), parse_template(requires(ALL[0])))
    layout_sets = [tuple(slide.layout_id for slide in variant.slides) for variant in variants]
    assert len(set(layout_sets)) > 1, f"макеты совпали во всех вариантах: {layout_sets[0]}"


def test_variants_differ_in_type_size() -> None:
    """Критерий приёмки: различается кегль — признак визуального регистра."""
    variants = compose_variants(deck(), parse_template(requires(ALL[0])))
    sizes = [
        tuple(
            run.size_pt
            for slide in variant.slides
            for element in slide.elements
            for run in element.runs
        )
        for variant in variants
    ]
    assert len(set(sizes)) > 1, "кегли одинаковы во всех вариантах"


def test_variants_use_colour_schemes_when_template_has_them() -> None:
    """Критерий приёмки: различается цветовая схема — там, где она есть.

    В шаблоне без вариаций схемы совпадут, и это не дефект: ADR-0004 прямо
    предупреждает, что опираться на одну эту ось нельзя.
    """
    schema = parse_template(requires(ALL[0]))
    by_id = {layout.id: layout for layout in schema.layouts}
    variants = compose_variants(deck(), schema)
    schemes = [
        tuple(by_id[slide.layout_id].color_scheme for slide in variant.slides)
        for variant in variants
    ]
    assert len(set(schemes)) > 1, f"цветовые схемы совпали: {schemes[0]}"


@pytest.mark.parametrize("name", ALL)
def test_variants_are_distinguishable_on_every_template(name: str) -> None:
    """Различие обязано сохраняться на любом шаблоне, а не на удобном."""
    variants = compose_variants(deck(), parse_template(requires(name)))
    fingerprints = {
        tuple(
            (slide.layout_id, tuple(run.size_pt for e in slide.elements for run in e.runs))
            for slide in variant.slides
        )
        for variant in variants
    }
    assert len(fingerprints) > 1, f"{name}: варианты неразличимы"


@pytest.mark.parametrize("name", ALL)
def test_content_is_not_substituted_across_variants(name: str) -> None:
    """Варианты меняют форму, а не содержание: ТЗ требует один контент.

    Побуквенного совпадения требовать нельзя: крупный кегль варианта B
    переполняет слот раньше, и обрезка срабатывает сильнее. Обрезка —
    законная компенсация, и она фиксируется. Недопустима подмена: каждая
    строка обязана оставаться началом исходной.
    """
    source = {
        text
        for slide in deck().slides
        for text in [slide.headline, *(slide.body.items if slide.body else [])]
    }
    for variant in compose_variants(deck(), parse_template(requires(name))):
        for slide in variant.slides:
            for element in slide.elements:
                for run in element.runs:
                    stem = run.text.rstrip("…").rstrip()
                    assert any(original.startswith(stem) for original in source), (
                        f"{variant.variant}: текст «{run.text[:30]}» не из контент-пакета"
                    )


@pytest.mark.parametrize("name", ALL)
def test_shortening_is_always_recorded(name: str) -> None:
    """Если содержание сокращено, об этом сказано в компенсациях."""
    for variant in compose_variants(deck(), parse_template(requires(name))):
        for slide in variant.slides:
            shortened = any(
                run.text.endswith("…") for e in slide.elements for run in e.runs
            )
            if shortened:
                kinds = {c.kind for c in slide.applied_compensations}
                assert "truncate" in kinds, f"{variant.variant}/{slide.id}: обрезка не записана"


def test_variants_are_deterministic() -> None:
    schema = parse_template(requires(ALL[0]))
    first = [v.model_dump_json(by_alias=True) for v in compose_variants(deck(), schema)]
    second = [v.model_dump_json(by_alias=True) for v in compose_variants(deck(), schema)]
    assert first == second


@pytest.mark.parametrize("name", ALL)
def test_every_variant_fills_its_slides(name: str) -> None:
    for variant in compose_variants(deck(), parse_template(requires(name))):
        for slide in variant.slides:
            assert slide.elements, f"{variant.variant}/{slide.id}: пустой слайд"
