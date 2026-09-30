"""Исправления с потерями: выбор пользователя и повторный аудит (T-40).

Механические находки система чинит сама (`fixer`). Находка с потерями так не
лечится: способов несколько, и каждый меняет слайд по-своему — перенос пунктов
добавляет слайд, уменьшение текста делает его мельче. Решение D5 отдаёт выбор
пользователю, и этот модуль даёт ему, из чего выбирать.

**Исправление живёт рядом со своей проверкой** — то же правило, что у
`@fixes`. Модуль проверки объявляет способ декоратором `@remedy(SPEC, ...)`
с названием и последствием словами пользователя: последствие — ровно то, ради
чего выбор вообще отдан человеку, и без него выбирать не из чего.

**Применимость измеряется, а не предполагается** (FR-31). Способ пробуется на
копии колоды, аудит выполняется заново, и способ предлагается, только если
находка исчезла и ничего не ухудшилось. Предложить действие, которое не
поможет, — заставить пользователя проверять систему вместо неё.

**Сравнение идёт по счёту находок, а не по их месту.** Способ может добавить
слайд, и номера всех следующих слайдов сдвинутся: сравнение по месту приняло
бы каждую сдвинутую находку за новую. Счёт по проверке и критичности
перестановку переживает.

**Выбранное применяется с той же защитой.** Между предложением и нажатием
могло примениться другое исправление, поэтому каждый выбор измеряется заново,
и действие, которое на этот раз ухудшило бы колоду, отклоняется, а не
применяется молча.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from dpd.audit.fixer import SEVERITY_WEIGHT, FixHandler
from dpd.audit.registry import REGISTRY, AuditContext, CheckSpec, discover, run_checks
from dpd.models import AuditReport, Finding, RenderedPresentation

CLASSES: tuple[str, ...] = ("file",)
"""Классы проверок для повторного аудита — те же, что у автопочинки: колода
до экспорта, без изображений и без моделей."""


@dataclass(frozen=True)
class Remedy:
    """Способ исправить находку, отданный на выбор пользователю."""

    id: str
    title: str
    consequence: str
    handler: FixHandler


REMEDIES: dict[str, dict[str, Remedy]] = {}
"""Проверка → способ → исправление. Порядок способов — порядок показа."""


def remedy(
    *specs: CheckSpec | str, id: str, title: str, consequence: str
) -> Callable[[FixHandler], FixHandler]:
    """Объявить функцию способом исправить находки этих проверок.

    Обработчик получает копию колоды и правит её на месте, как обработчик
    `@fixes`; `False` означает, что к этой находке способ неприменим.
    """

    def decorate(handler: FixHandler) -> FixHandler:
        for spec in specs:
            check_id = spec if isinstance(spec, str) else spec.id
            REMEDIES.setdefault(check_id, {})[id] = Remedy(id, title, consequence, handler)
        return handler

    return decorate


def plain_title(finding: Finding) -> str:
    """Название находки для пользователя: без жаргона и без чисел порога."""
    discover()
    registered = REGISTRY.get(finding.check_id)
    if registered is None:
        return finding.message
    return registered.spec.plain or registered.spec.title or finding.message


def options(
    context: AuditContext,
    report: AuditReport,
    *,
    classes: Sequence[str] = CLASSES,
) -> dict[int, list[Remedy]]:
    """Применимые способы для каждой открытой находки, по её номеру в отчёте.

    Находка без применимых способов в ответ не попадает: показать ей нечего,
    кроме «оставить как есть».
    """
    discover()
    if context.deck is None:
        raise ValueError("способы исправления подбираются для свёрстанной колоды")

    baseline: AuditReport | None = None
    offered: dict[int, list[Remedy]] = {}
    for index, finding in enumerate(report.findings):
        candidates = REMEDIES.get(finding.check_id, {})
        if finding.status != "open" or not candidates:
            continue
        baseline = baseline or run_checks(context, classes=classes)
        helpful = [
            item
            for item in candidates.values()
            if _trial(context, context.deck, baseline, finding, item, classes) is not None
        ]
        if helpful:
            offered[index] = helpful
    return offered


@dataclass(frozen=True)
class Choice:
    """Находка и выбранный для неё способ."""

    finding: Finding
    remedy: Remedy


@dataclass(frozen=True)
class RemedyOutcome:
    """Итог применения выбора.

    `skipped` — выборы, чья находка исчезла раньше, чем до неё дошла очередь:
    переполненный слайд даёт две находки, и один перенос снимает обе.
    """

    deck: RenderedPresentation
    report: AuditReport
    applied: list[Choice] = field(default_factory=list)
    rejected: list[Choice] = field(default_factory=list)
    skipped: list[Choice] = field(default_factory=list)


def apply_remedies(
    context: AuditContext,
    report: AuditReport,
    choices: Mapping[int, str],
    *,
    classes: Sequence[str] = CLASSES,
) -> RemedyOutcome:
    """Применить выбранные способы и выполнить аудит заново.

    `choices` — номер находки в `report` → идентификатор способа. Исходная
    колода не меняется: правка идёт по копии, как у автопочинки.

    **Выборы применяются от последнего слайда к первому.** Способ может
    вставить слайд, и номера следующих сдвинутся; идя с конца, каждый
    следующий выбор застаёт свой слайд на прежнем месте.
    """
    discover()
    if context.deck is None:
        raise ValueError("исправление требует свёрстанной колоды")

    picked: list[Choice] = []
    for index, remedy_id in choices.items():
        finding = report.findings[index]
        found = REMEDIES.get(finding.check_id, {}).get(remedy_id)
        if found is None:
            raise ValueError(f"для находки {finding.check_id} нет способа «{remedy_id}»")
        picked.append(Choice(finding, found))
    picked.sort(key=lambda choice: choice.finding.slide_number or 0, reverse=True)

    current = context.deck.model_copy(deep=True)
    audit = run_checks(replace(context, deck=current), classes=classes)
    applied: list[Choice] = []
    rejected: list[Choice] = []
    skipped: list[Choice] = []

    for choice in picked:
        if not _still_there(audit, choice.finding):
            skipped.append(choice)
            continue
        trial = _trial(context, current, audit, choice.finding, choice.remedy, classes)
        if trial is None:
            rejected.append(choice)
            continue
        current, audit = trial
        applied.append(choice)

    return RemedyOutcome(deck=current, report=audit, applied=applied, rejected=rejected, skipped=skipped)


def _trial(
    context: AuditContext,
    deck: RenderedPresentation,
    before: AuditReport,
    finding: Finding,
    item: Remedy,
    classes: Sequence[str],
) -> tuple[RenderedPresentation, AuditReport] | None:
    """Попробовать способ на копии; вернуть результат, только если он помог."""
    candidate = deck.model_copy(deep=True)
    if not item.handler(candidate, finding, context.template):
        return None
    after = run_checks(replace(context, deck=candidate), classes=classes)
    return (candidate, after) if _helps(before, after, finding) else None


def _helps(before: AuditReport, after: AuditReport, finding: Finding) -> bool:
    """Находок этой проверки стало меньше, а не легче её — не больше.

    Правило D5 о защите от ухудшения, сформулированное через счёт: обмен
    предупреждения на критическую находку не проходит, на рекомендацию —
    проходит.
    """
    was = Counter((item.check_id, item.severity) for item in before.findings)
    now = Counter((item.check_id, item.severity) for item in after.findings)
    if _of_check(now, finding.check_id) >= _of_check(was, finding.check_id):
        return False
    weight = SEVERITY_WEIGHT[finding.severity]
    return all(count <= was[key] for key, count in now.items() if SEVERITY_WEIGHT[key[1]] >= weight)


def _of_check(tally: Counter, check_id: str) -> int:
    return sum(count for (key, _), count in tally.items() if key == check_id)


def _still_there(audit: AuditReport, finding: Finding) -> bool:
    """Есть ли ещё находка этой проверки на том же месте."""
    return any(
        item.check_id == finding.check_id
        and item.slide_number == finding.slide_number
        and item.slot_id == finding.slot_id
        for item in audit.findings
    )
