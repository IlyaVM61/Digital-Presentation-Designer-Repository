"""T-40: исправления с потерями — выбор пользователя и повторный аудит.

Критерий приёмки: **цикл «находка → исправление → повторный аудит»
проходится**.

Механические находки система чинит сама (T-33, T-34). Находка с потерями
так не лечится: способов несколько, и каждый меняет слайд по-своему —
поэтому решение D5 отдаёт выбор пользователю, а FR-31 требует показывать
**только применимые** действия.

**Применимость измеряется, а не предполагается.** Действие пробуется на
копии колоды, аудит выполняется заново, и действие предлагается, только если
находка исчезла и ничего не ухудшилось. Предложить «перенести пункты» слайду,
которому это не поможет, — значит заставить пользователя проверять систему
вместо того, чтобы доверять ей.

Проверяется на живой колоде: восемь пунктов на слайде дают настоящую находку
`density.too_many_bullets`, а не подстроенную.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from test_ui_app import JARGON

from dpd.audit import REGISTRY, AuditContext, discover
from dpd.audit.fixer import repair, slide_of
from dpd.audit.remedies import REMEDIES, apply_remedies, options, plain_title, remedy
from dpd.generation import structure_from_outline
from dpd.layout import compose_variants
from dpd.layout.variants import load_profiles
from dpd.models import AuditReport, Finding, TemplateSchema
from dpd.parsing import parse_template

CALIBRATION = Path(__file__).resolve().parents[1] / "assets" / "templates" / "calibration"
TEMPLATE = "VK Tech шаблон.pptx"

POINTS = [
    "Разбор шаблона занимает меньше секунды",
    "Три варианта собираются одним нажатием",
    "Правки текста переживают сохранение",
    "Таблицы остаются таблицами",
    "Диаграммы остаются диаграммами",
    "Проверка оформления идёт до выгрузки",
    "Механические дефекты исправляются сами",
    "Файлы готовы в трёх форматах",
]

OUTLINE = "\n".join(
    [
        "Итоги пилота",
        "Что изменилось за квартал",
        "- Срок сборки колоды сократился с девяти дней до трёх",
        "- Правки текста переживают сохранение файла",
        "Что умеет система",
        *(f"- {point}" for point in POINTS),
    ]
)
CROWDED_SLIDE = 3


@pytest.fixture(scope="module")
def schema() -> TemplateSchema:
    path = CALIBRATION / TEMPLATE
    if not path.exists():
        pytest.skip(f"нет калибровочного шаблона {TEMPLATE}")
    return parse_template(path)


@pytest.fixture
def repaired(schema: TemplateSchema) -> tuple[AuditContext, AuditReport]:
    """Колода в том виде, в каком её видит пользователь: после автопочинки."""
    structure = structure_from_outline(OUTLINE)
    deck = compose_variants(structure, schema, load_profiles())[0]
    context = AuditContext(deck=deck, template=schema, structure=structure)
    result = repair(context)
    return replace(context, deck=result.deck), result.report


@pytest.fixture
def isolated_remedies() -> Iterator[dict]:
    """Реестр исправлений глобален: пробное исправление не должно пережить тест."""
    discover()
    saved = {check_id: dict(items) for check_id, items in REMEDIES.items()}
    try:
        yield REMEDIES
    finally:
        REMEDIES.clear()
        REMEDIES.update(saved)


def index_of(report: AuditReport, check_id: str, slide: int | None = None) -> int:
    for index, finding in enumerate(report.findings):
        if finding.check_id == check_id and (slide is None or finding.slide_number == slide):
            return index
    raise AssertionError(f"нет находки {check_id} на слайде {slide}: {[f.check_id for f in report.findings]}")


def texts(deck) -> list[str]:
    return [run.text for slide in deck.slides for element in slide.elements for run in element.runs]


# --- Что предлагается -------------------------------------------------------


def test_crowded_slide_is_offered_to_move_points(repaired) -> None:
    """Восемь пунктов при норме шесть: предлагается перенос на новый слайд."""
    context, report = repaired
    index = index_of(report, "density.too_many_bullets", CROWDED_SLIDE)

    offered = options(context, report)

    assert [item.id for item in offered.get(index, [])] == ["split"]


def test_action_that_would_not_help_is_not_offered(repaired) -> None:
    """Слабо заполненный слайд переносом пунктов не вылечить — стало бы хуже.

    Обработчик переноса здесь сработал бы: пунктов два, делить есть что.
    Но два полупустых слайда вместо одного — ухудшение, и измерение это
    видит. Поэтому действие не показывается вовсе, а не показывается и
    отклоняется после нажатия.
    """
    context, report = repaired
    sparse = next(
        index
        for index, finding in enumerate(report.findings)
        if finding.check_id == "density.fill_out_of_range" and finding.evidence.get("actual", 1) < 0.5
    )

    assert options(context, report).get(sparse, []) == []


def test_fixed_findings_are_not_offered_again(repaired) -> None:
    """Исправленное системой не предлагается исправлять второй раз."""
    context, report = repaired
    offered = options(context, report)

    assert all(report.findings[index].status != "autofixed" for index in offered)


# --- Что происходит после выбора ------------------------------------------


def test_moving_points_keeps_every_point_and_repeats_the_title(repaired) -> None:
    """Перенос не теряет ни одного пункта и не выдумывает новых."""
    context, report = repaired
    index = index_of(report, "density.too_many_bullets", CROWDED_SLIDE)
    before = len(context.deck.slides)

    outcome = apply_remedies(context, report, {index: "split"})

    assert len(outcome.deck.slides) == before + 1
    after = texts(outcome.deck)
    for point in POINTS:
        assert after.count(point) == 1, f"пункт «{point}» потерян или размножен"
    assert after.count("Что умеет система") == 2, "заголовок должен повториться на новом слайде"
    order = [after.index(point) for point in POINTS]
    assert order == sorted(order), "порядок пунктов нарушен"


def test_audit_is_repeated_after_the_fix(repaired) -> None:
    """Повторный аудит — правило D5: отчёт описывает колоду после правки."""
    context, report = repaired
    index = index_of(report, "density.too_many_bullets", CROWDED_SLIDE)

    outcome = apply_remedies(context, report, {index: "split"})

    assert [item.remedy.id for item in outcome.applied] == ["split"]
    assert not outcome.rejected
    assert "density.too_many_bullets" not in {finding.check_id for finding in outcome.report.findings}
    assert len(context.deck.slides) == len(outcome.deck.slides) - 1, "исходная колода изменена"


def test_leaving_as_is_changes_nothing(repaired) -> None:
    context, report = repaired

    outcome = apply_remedies(context, report, {})

    assert outcome.deck == context.deck
    assert not outcome.applied


def test_unknown_action_is_an_error(repaired) -> None:
    context, report = repaired
    index = index_of(report, "density.too_many_bullets", CROWDED_SLIDE)

    with pytest.raises(ValueError, match="нет"):
        apply_remedies(context, report, {index: "no-such-action"})


def test_two_choices_on_one_slide_do_not_split_it_twice() -> None:
    """Две находки на одном слайде, одна причина: второй перенос лишний.

    Переполненный слайд даёт и «слишком много пунктов», и «слишком плотно».
    Выбор переноса для обеих не должен порезать слайд дважды: после первого
    переноса вторая находка уже исчезла, и чинить нечего.

    Шаблон другой, потому что на VK Tech восемь пунктов не переполняют
    слайд по площади: там находка одна, и случай не воспроизводится.
    """
    path = CALIBRATION / "Шаблон презентации VK Education.pptx"
    if not path.exists():
        pytest.skip("нет калибровочного шаблона VK Education")
    schema = parse_template(path)
    structure = structure_from_outline(OUTLINE)
    deck = next(item for item in compose_variants(structure, schema, load_profiles()) if item.variant == "B")
    context = AuditContext(deck=deck, template=schema, structure=structure)
    result = repair(context)
    context, report = replace(context, deck=result.deck), result.report
    crowded = [index for index in options(context, report) if report.findings[index].slide_number == CROWDED_SLIDE]
    assert len(crowded) == 2, [report.findings[index].check_id for index in crowded]

    outcome = apply_remedies(context, report, {index: "split" for index in crowded})

    assert len(outcome.deck.slides) == len(context.deck.slides) + 1
    assert len(outcome.applied) == 1 and len(outcome.skipped) == 1


# --- Защита от ухудшения --------------------------------------------------


def test_action_that_makes_things_worse_is_refused(repaired, isolated_remedies) -> None:
    """Правило D5: обмен предупреждения на критическую находку — не починка.

    Пробное действие снимает находку честно — выбрасывает лишние пункты, — но
    выталкивает блок за край слайда. Аудит это видит, и действие не
    предлагается и не применяется, даже если его попросили.
    """
    context, report = repaired
    index = index_of(report, "density.too_many_bullets", CROWDED_SLIDE)

    @remedy(REGISTRY.get("density.too_many_bullets").spec, id="drop", title="Убрать лишнее", consequence="Часть пунктов пропадёт")
    def drop(deck, finding: Finding, template) -> bool:
        body = max(slide_of(deck, finding).elements, key=lambda element: len(element.runs))
        body.runs = body.runs[:3]
        body.bounds = body.bounds.model_copy(update={"x": 0.9, "w": 0.5})
        return True

    assert "drop" not in [item.id for item in options(context, report).get(index, [])]

    outcome = apply_remedies(context, report, {index: "drop"})

    assert [item.remedy.id for item in outcome.rejected] == ["drop"]
    assert outcome.deck == context.deck, "отклонённое действие всё же изменило колоду"


# --- Слова ------------------------------------------------------------------


def test_everything_the_user_reads_is_free_of_jargon() -> None:
    """Названия находок и действий видны на странице, значит, проходят словарь T-55.

    Проверяются все, а не только сработавшие на тестовой колоде: иначе
    жаргон всплыл бы на первом же шаблоне, где сработает другая проверка.
    """
    import re

    discover()
    words = [plain_title(Finding.model_validate(
        {"checkId": item.spec.id, "category": item.spec.category, "class": item.spec.check_class,
         "severity": item.spec.severity, "fixability": item.spec.fixability, "message": ""}
    )) for item in REGISTRY]
    for items in REMEDIES.values():
        for item in items.values():
            words += [item.title, item.consequence]

    found = [
        f"{name}: «{text}»"
        for text in words
        for name, pattern in JARGON.items()
        if re.search(pattern, text, flags=re.IGNORECASE)
    ]
    assert not found, found
