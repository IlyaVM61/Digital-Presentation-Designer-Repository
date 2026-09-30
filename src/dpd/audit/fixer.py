"""Применение исправлений по политике D5, с откатом (T-33).

Механические находки исправляются автоматически, находки с потерями
требуют подтверждения, смысловые — предлагают перегенерацию слайда.

**Правится только `mechanical`.** Это не упрощение, а граница
ответственности: находка с потерями требует выбора между способами, и выбор
меняет смысл слайда; смысловую находку вёрсткой не вылечить вовсе. Система,
которая правит их сама, меняет замысел автора без его ведома. Находки с
потерями правятся по выбору пользователя — это `remedies` (T-40).

**Значение для исправления берётся из шаблона.** Кегль вне шкалы заменяется
ближайшим из шкалы этого шаблона, цвет вне палитры — ближайшим из его
палитры, чужая гарнитура — его основной. Придуманное значение было бы не
исправлением, а второй ошибкой поверх первой.

**Правка идёт по копии, исходная колода остаётся нетронутой.** Иначе откат
нечем выполнить: исходного состояния уже не существует. Автоматическая
правка без возврата — потеря работы пользователя, и доверия такой системе
нет.

**Исправление живёт рядом со своей проверкой.** Обработчик регистрируется
декоратором `@fixes(SPEC)` в том же файле, что и проверка: добавление
проверки вместе с её исправлением остаётся правкой одного файла — то же
свойство, которое каркас T-27 обещает для самих проверок.

**Защита от ухудшения — в `repair`.** Исправление проверяется измерением, а
не доверием: применяется к копии, аудит выполняется заново, и появившиеся
находки сравниваются с исправленной. Обмен дефекта на равный ему дефект
починкой не считается.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Literal

from dpd.audit.registry import (
    AuditContext,
    CheckSpec,
    discover,
    load_section,
    run_checks,
)
from dpd.models import AuditReport, Finding, RenderedPresentation, TemplateSchema

if TYPE_CHECKING:
    from dpd.models import Slide

FixHandler = Callable[[RenderedPresentation, Finding, TemplateSchema | None], bool]
"""Обработчик исправления: правит колоду на месте, возвращает признак успеха.

`False` означает, что механического исправления не нашлось — находка
остаётся открытой. Это честнее, чем отчитаться о правке, которой не было."""

Touches = Literal["style", "geometry"]
"""Что меняет исправление. Разделение нужно потому, что правки оформления
независимы — гарнитура, кегль и цвет не спорят друг с другом, — а правки
геометрии спорят: каждая следующая опиралась бы на устаревшее положение
блока."""


@dataclass(frozen=True)
class RegisteredFix:
    handler: FixHandler
    touches: Touches


FIXERS: dict[str, RegisteredFix] = {}


def fixes(spec: CheckSpec | str, *, touches: Touches = "style") -> Callable[[FixHandler], FixHandler]:
    """Объявить функцию исправлением для находок этой проверки."""
    check_id = spec if isinstance(spec, str) else spec.id

    def decorate(handler: FixHandler) -> FixHandler:
        FIXERS[check_id] = RegisteredFix(handler=handler, touches=touches)
        return handler

    return decorate


@dataclass(frozen=True)
class FixResult:
    """Итог применения исправлений."""

    deck: RenderedPresentation
    report: AuditReport
    applied: list[str] = field(default_factory=list)
    _original: RenderedPresentation | None = None

    def rollback(self) -> RenderedPresentation:
        """Вернуть колоду в состояние до исправлений."""
        return self._original if self._original is not None else self.deck


def apply_fixes(
    deck: RenderedPresentation,
    report: AuditReport,
    template: TemplateSchema | None = None,
) -> FixResult:
    """Исправить механические находки и пометить их `autofixed`.

    **Одно исправление геометрии на блок за проход.** Находка описывает то
    положение блока, которое было на момент аудита; если блок уже переставлен
    другим исправлением, её доказательство устарело, и вторая правка поверх
    первой ужимает блок, а не чинит его. Такая находка остаётся открытой и
    попадёт в следующий проход, когда аудит пересчитает её по новому
    состоянию.

    Правки оформления этого ограничения не требуют: гарнитура, кегль и цвет
    независимы и за одно свойство не спорят.
    """
    discover()

    fixed_deck = deck.model_copy(deep=True)
    applied: list[str] = []
    findings: list[Finding] = []
    moved: set[tuple[int | None, str | None]] = set()

    for finding in report.findings:
        registered = FIXERS.get(finding.check_id) if finding.fixability == "mechanical" else None
        place = (finding.slide_number, finding.slot_id)

        if registered is None or (registered.touches == "geometry" and place in moved):
            findings.append(finding)
            continue

        if not registered.handler(fixed_deck, finding, template):
            findings.append(finding)
            continue

        if registered.touches == "geometry":
            moved.add(place)
        applied.append(finding.id or finding.check_id)
        findings.append(finding.model_copy(update={"status": "autofixed"}))

    return FixResult(
        deck=fixed_deck,
        report=report.model_copy(update={"findings": findings}),
        applied=applied,
        _original=deck.model_copy(deep=True),
    )


# --- Общее для обработчиков -----------------------------------------------


def slide_of(deck: RenderedPresentation, finding: Finding) -> Slide | None:
    """Слайд, к которому относится находка."""
    number = finding.slide_number
    if number is None or not 1 <= number <= len(deck.slides):
        return None
    return deck.slides[number - 1]


def elements_of(deck: RenderedPresentation, finding: Finding):
    """Элементы, к которым относится находка: названный слот или весь слайд."""
    slide = slide_of(deck, finding)
    if slide is None:
        return []
    if finding.slot_id is None:
        return list(slide.elements)
    return [element for element in slide.elements if element.slot_id == finding.slot_id]


def slot_bounds(template: TemplateSchema | None, layout_id: str, slot_id: str):
    """Место, отведённое элементу шаблоном, — если оно известно."""
    if template is None:
        return None
    for layout in template.layouts:
        if layout.id != layout_id:
            continue
        for slot in layout.slots:
            if slot.id == slot_id:
                return slot.bounds
    return None


# --- Починка с защитой от ухудшения (T-34) ---------------------------------


SEVERITY_WEIGHT = {"advice": 1, "warning": 2, "critical": 3}
"""Вес критичности для сравнения находок между собой."""

DEFAULT_MAX_ITERATIONS = 10
"""Предел на случай, если раздел `fixer` из конфигурации недоступен."""


@dataclass(frozen=True)
class RepairResult:
    """Итог починки: что исправлено, что отклонено, что осталось."""

    deck: RenderedPresentation
    report: AuditReport
    applied: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    audits: int = 0
    _original: RenderedPresentation | None = None

    def rollback(self) -> RenderedPresentation:
        """Вернуть колоду в состояние до починки."""
        return self._original if self._original is not None else self.deck


def repair(
    context: AuditContext,
    *,
    max_iterations: int | None = None,
    classes: Sequence[str] | None = ("file",),
) -> RepairResult:
    """Чинить колоду, пока это её улучшает.

    Реализует три защитных правила D5.

    **Исправление, порождающее находку не ниже по критичности, не
    применяется.** Проверяется не рассуждением, а измерением: исправление
    применяется к копии, аудит выполняется заново, и появившиеся находки
    сравниваются с той, ради которой всё затевалось. Обмен критической
    находки на критическую — не починка, а перестановка проблемы с места на
    место; обмен на рекомендацию — улучшение, и он проходит.

    **После применения исправлений аудит выполняется повторно.** Иначе узнать,
    что натворило исправление, попросту нечем: находки описывают состояние, а
    оно изменилось.

    **Число итераций ограничено.** Итерация — одно применённое исправление с
    пересчётом аудита. Предел защищает от исправления, которое не устраняет
    собственную находку: без него система чинила бы по кругу. Остаточные
    находки показываются как есть.

    Итоговый отчёт складывается из исправленного и оставшегося: находки,
    устранённые починкой, из свежего аудита исчезают, и без этого пользователь
    не узнал бы, что система сделала с его колодой.
    """
    limit = int(
        max_iterations
        if max_iterations is not None
        else load_section("fixer").get("max_iterations", DEFAULT_MAX_ITERATIONS)
    )

    original = context.deck.model_copy(deep=True) if context.deck is not None else None
    current = context.deck
    if current is None:
        raise ValueError("починка требует свёрстанной колоды")

    report = run_checks(replace(context, deck=current), classes=classes)
    audits = 1

    autofixed: list[Finding] = []
    rejected: list[str] = []
    refused: set[tuple] = set()

    while len(autofixed) < limit:
        candidate = next(
            (
                finding
                for finding in report.findings
                if finding.fixability == "mechanical" and _key(finding) not in refused
            ),
            None,
        )
        if candidate is None:
            break

        attempt = apply_fixes(current, report.model_copy(update={"findings": [candidate]}), context.template)
        if not attempt.applied:
            refused.add(_key(candidate))
            continue

        trial = run_checks(replace(context, deck=attempt.deck), classes=classes)
        audits += 1

        worsened = _worsening(report, trial, candidate)
        if worsened is not None:
            rejected.append(candidate.check_id)
            refused.add(_key(candidate))
            continue

        current, report = attempt.deck, trial
        autofixed.append(candidate.model_copy(update={"status": "autofixed"}))

    return RepairResult(
        deck=current,
        report=report.model_copy(update={"findings": autofixed + list(report.findings)}),
        applied=[finding.check_id for finding in autofixed],
        rejected=rejected,
        audits=audits,
        _original=original,
    )


def _key(finding: Finding) -> tuple:
    """Чем находка отличается от других: проверка, место и сообщение."""
    return (finding.check_id, finding.slide_number, finding.slot_id, finding.message)


def _worsening(before: AuditReport, after: AuditReport, fixed: Finding) -> Finding | None:
    """Находка, появившаяся после исправления и не легче исправленной.

    Сравнение идёт по составу, а не по числу находок: исправление могло
    устранить одну и породить другую, и счёт остался бы прежним.
    """
    known = {_key(finding) for finding in before.findings}
    weight = SEVERITY_WEIGHT[fixed.severity]
    return next(
        (
            finding
            for finding in after.findings
            if _key(finding) not in known and SEVERITY_WEIGHT[finding.severity] >= weight
        ),
        None,
    )
