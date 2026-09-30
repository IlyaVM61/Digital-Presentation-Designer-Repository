"""T-34: защита от ухудшения и предел итераций.

Критерий приёмки: **исправление, порождающее находку не ниже по критичности,
не применяется**.

Это первое из трёх защитных правил D5. Остальные два — повторный аудит после
исправлений и ограниченное число итераций — проверяются здесь же: без них
защита не работает. Узнать, что натворило исправление, можно только заново
прогнав аудит, а без предела система чинит по кругу.

Правила проверяются на пробных проверках, а не на настоящих. Причина простая:
настоящие исправления написаны так, чтобы не ухудшать, и на них защита
молчит — то есть ничего не доказывает. Пробная проверка позволяет устроить
ухудшение намеренно.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from dpd.audit import (
    REGISTRY,
    AuditContext,
    CheckSpec,
    Registry,
    check,
    discover,
    run_checks,
)
from dpd.audit.fixer import FIXERS, fixes, repair
from dpd.layout import compose_variants
from dpd.models import (
    Bounds,
    Canvas,
    Layout,
    PresentationStructure,
    RenderedElement,
    RenderedPresentation,
    Slide,
    SlideBody,
    Slot,
    StructureSlide,
    TemplateSchema,
    TemplateSource,
    TextRun,
)
from dpd.parsing import parse_template

CALIBRATION = Path(__file__).resolve().parents[1] / "assets" / "templates" / "calibration"
CANVAS = Canvas(width_emu=12192000, height_emu=6858000)
BODY_BOUNDS = Bounds(x=0.30, y=0.30, w=0.40, h=0.30)


@pytest.fixture
def isolated() -> Iterator[Registry]:
    """Реестр проверок и реестр исправлений глобальны: тест их возвращает.

    Слепок снимается **после** обхода каталога проверок. Модули проверок
    регистрируют себя один раз за процесс, и слепок, снятый до обхода,
    вернул бы пустые реестры навсегда: повторный импорт ничего не
    зарегистрирует, потому что модуль уже загружен.

    На время теста реестры очищаются: правила защиты проверяются на пробных
    проверках, и настоящие, работая рядом, чинили бы синтетический слайд
    заодно — результат зависел бы не от защиты, а от того, что ещё нашлось.
    """
    discover()
    checks = REGISTRY.snapshot()
    handlers = dict(FIXERS)
    REGISTRY.restore({})
    FIXERS.clear()
    try:
        yield REGISTRY
    finally:
        REGISTRY.restore(checks)
        FIXERS.clear()
        FIXERS.update(handlers)


def spec(check_id: str, *, severity: str = "critical") -> CheckSpec:
    return CheckSpec(
        id=check_id,
        category="layout",
        check_class="file",
        severity=severity,
        fixability="mechanical",
        sublayer="4b",
        title="Пробная проверка",
    )


def schema() -> TemplateSchema:
    return TemplateSchema(
        source=TemplateSource(file="synthetic.pptx", hash="0" * 8),
        canvas=CANVAS,
        layouts=[
            Layout(
                id="l1",
                name="Макет",
                family="content",
                slots=[Slot(id="body", kind="body", origin="placeholder", bounds=BODY_BOUNDS)],
            )
        ],
    )


def deck(x: float = 0.70) -> RenderedPresentation:
    return RenderedPresentation(
        variant="A",
        template_hash="0" * 8,
        canvas=CANVAS,
        slides=[
            Slide(
                id="s1",
                layout_id="l1",
                elements=[
                    RenderedElement(
                        slot_id="body",
                        kind="text",
                        bounds=Bounds(x=x, y=0.30, w=0.25, h=0.30),
                        runs=[TextRun(text="Итоги пилота", font="Play", size_pt=14, color="#000000")],
                    )
                ],
            )
        ],
    )


def register_pair(*, right_severity: str, left_severity: str) -> None:
    """Пара проверок, спорящих друг с другом.

    Первая срабатывает, когда блок стоит справа, и её исправление двигает
    блок влево. Вторая срабатывает, когда блок оказался слева. Исправляя
    первую, система неизбежно порождает вторую — на этом и проверяется
    защита.
    """
    right, left = spec("probe.too_right", severity=right_severity), spec("probe.too_left", severity=left_severity)

    @check(right)
    def check_right(deck):
        return [
            right.finding("блок стоит справа", slide_number=1, slot_id=element.slot_id)
            for slide in deck.slides
            for element in slide.elements
            if element.bounds.x > 0.5
        ]

    @check(left)
    def check_left(deck):
        return [
            left.finding("блок стоит слева", slide_number=1, slot_id=element.slot_id)
            for slide in deck.slides
            for element in slide.elements
            if element.bounds.x < 0.1
        ]

    @fixes(right, touches="geometry")
    def fix_right(deck, finding, template):
        element = deck.slides[0].elements[0]
        element.bounds = Bounds(x=0.05, y=element.bounds.y, w=element.bounds.w, h=element.bounds.h)
        return True


def audit(source: RenderedPresentation, template: TemplateSchema):
    discover()
    return run_checks(AuditContext(deck=source, template=template), classes=("file",))


# --- Критерий приёмки: защита от ухудшения --------------------------------


def test_fix_creating_an_equally_severe_finding_is_not_applied(isolated: Registry) -> None:
    """Один дефект не меняется на другой такой же.

    Исправление, после которого критическая находка сменилась критической,
    не улучшает колоду — оно переставляет проблему с места на место и
    создаёт видимость работы.
    """
    register_pair(right_severity="critical", left_severity="critical")

    result = repair(AuditContext(deck=deck(), template=schema()))

    assert result.deck.slides[0].elements[0].bounds.x == pytest.approx(0.70), "исправление применилось"
    assert "probe.too_right" in result.rejected
    assert [item.check_id for item in result.report.findings if item.status == "autofixed"] == []


def test_fix_creating_a_lighter_finding_is_applied(isolated: Registry) -> None:
    """Обмен критической находки на рекомендацию — улучшение, и он проходит.

    Правило D5 запрещает исправление, порождающее находку **не ниже** по
    критичности. Более лёгкая находка запретом не покрыта: слайд стал лучше,
    а остаток пользователю показан.
    """
    register_pair(right_severity="critical", left_severity="advice")

    result = repair(AuditContext(deck=deck(), template=schema()))

    assert result.deck.slides[0].elements[0].bounds.x == pytest.approx(0.05)
    assert result.rejected == []
    assert "probe.too_left" in [item.check_id for item in result.report.findings]


def test_rejected_fix_leaves_the_finding_open_and_says_why(isolated: Registry) -> None:
    """Отклонённое исправление не выдаётся за выполненное."""
    register_pair(right_severity="warning", left_severity="critical")

    result = repair(AuditContext(deck=deck(), template=schema()))

    finding = next(item for item in result.report.findings if item.check_id == "probe.too_right")
    assert finding.status == "open"
    assert result.applied == []


# --- Предел итераций -------------------------------------------------------


def test_iteration_limit_stops_endless_repair(isolated: Registry) -> None:
    """Исправление, не устраняющее собственную находку, не чинится по кругу.

    Без предела система переставляла бы блок вечно: проверка срабатывает
    снова и снова, а исправление каждый раз «применяется».
    """
    stubborn = spec("probe.stubborn", severity="warning")

    @check(stubborn)
    def check_stubborn(deck):
        return [stubborn.finding("всегда срабатывает", slide_number=1, slot_id="body")]

    @fixes(stubborn)
    def fix_stubborn(deck, finding, template):
        run = deck.slides[0].elements[0].runs[0]
        run.text = run.text + "."
        return True

    result = repair(AuditContext(deck=deck(x=0.30), template=schema()), max_iterations=3)

    assert len(result.applied) == 3, "предел итераций не сработал"
    assert result.report.findings, "остаточные находки должны быть показаны как есть"


def test_repair_stops_as_soon_as_nothing_is_left_to_fix(isolated: Registry) -> None:
    """Лишних проходов аудита не делается: работа заканчивается на чистом.

    Прогон аудита — не бесплатная операция, и каждый лишний проход умножается
    на три варианта колоды.
    """
    result = repair(AuditContext(deck=deck(x=0.30), template=schema()), max_iterations=5)

    assert result.applied == []
    assert result.audits == 1, "чистой колоде хватает одного прогона"


# --- Повторный аудит -------------------------------------------------------


def test_report_shows_what_was_fixed_and_what_remains() -> None:
    """Итоговый отчёт складывается из исправленного и оставшегося.

    Иначе пользователь не узнает, что система сделала с его колодой: находки,
    устранённые исправлением, из свежего аудита просто исчезают.
    """
    template_file = CALIBRATION / "VK Tech шаблон.pptx"
    if not template_file.exists():
        pytest.skip("нет калибровочного шаблона")

    template = parse_template(template_file)
    structure = PresentationStructure(
        slides=[
            StructureSlide(id="s1", role="title", headline="Итоги пилота"),
            StructureSlide(
                id="s2",
                role="data",
                headline="Что изменилось за квартал",
                body=SlideBody(items=["Срок сборки колоды сократился с девяти дней до трёх"]),
            ),
        ]
    )
    broken = compose_variants(structure, template)[0].model_copy(deep=True)
    spoiled = broken.slides[1].elements[-1]
    spoiled.runs[0].font, spoiled.runs[0].size_pt, spoiled.runs[0].color = "Comic Sans MS", 16.2, "#12FF99"
    spoiled.bounds = Bounds(x=0.72, y=0.30, w=0.40, h=0.30)

    result = repair(AuditContext(deck=broken, template=template))

    autofixed = {item.check_id for item in result.report.findings if item.status == "autofixed"}
    assert {"template.font_not_in_set", "template.size_not_in_scale", "template.color_not_in_palette"} <= autofixed
    assert not [
        item
        for item in result.report.findings
        if item.fixability == "mechanical" and item.status != "autofixed"
    ], "механических находок после починки остаться не должно"
    assert result.rollback().slides[1].elements[-1].runs[0].font == "Comic Sans MS"


def test_repair_never_worsens_a_real_deck() -> None:
    """Починка не имеет права ухудшить живую колоду.

    Сравниваются наборы находок до и после: критических не прибавилось,
    предупреждений тоже.
    """
    template_file = CALIBRATION / "Шаблон презентации VK Education.pptx"
    if not template_file.exists():
        pytest.skip("нет калибровочного шаблона")

    template = parse_template(template_file)
    structure = PresentationStructure(
        slides=[
            StructureSlide(id="s1", role="title", headline="Итоги пилота"),
            StructureSlide(
                id="s2",
                role="data",
                headline="Что изменилось за квартал",
                body=SlideBody(items=["Срок сборки колоды сократился с девяти дней до трёх"]),
            ),
        ]
    )

    for variant in compose_variants(structure, template):
        before = audit(variant, template)
        result = repair(AuditContext(deck=variant, template=template))
        after = audit(result.deck, template)

        for severity in ("critical", "warning"):
            assert len(after.by_severity(severity)) <= len(before.by_severity(severity)), (
                f"вариант {variant.variant}: находок «{severity}» стало больше"
            )
