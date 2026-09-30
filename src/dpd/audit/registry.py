"""Каркас проверки как самостоятельной единицы (T-27).

Архитектура объявляет расширяемость точкой проекта: «проверка — идентификатор,
класс, категория, критичность, исправимость, параметры; добавление проверки не
требует изменений в других слоях». Здесь это сделано выполнимым.

**Как добавляется проверка.** В `dpd/audit/checks/` кладётся файл: он
объявляет `CheckSpec`, вешает `@check(SPEC)` на функцию и возвращает находки
через `SPEC.finding(...)`. Больше ничего править не нужно — ни списка импортов,
ни вызывающего кода: модули каталога обходятся `pkgutil`, а не перечисляются.

**Почему проверки не приведены к единой сигнатуре.** Вход у них разный:
проверке формы нужна свёрстанная колода, проверке целостности — файл после
экспорта, проверке различимости — все три варианта сразу. Единая сигнатура
заставила бы каждую проверку разбирать общий объект и молча работать не с тем
полем. Вместо этого проверка называет свои входы именами параметров, а каркас
связывает их с контекстом прогона; параметр без значения в контексте берётся
из `configs/audit.yaml`.

**Порог, не совпавший с именем параметра, — ошибка, а не умолчание.** Молча
не применённый порог выглядит как настроенная система, которая работает на
значениях по умолчанию; такая ошибка не обнаруживается никогда.

Исключение внутри проверки не подавляется. Проверка, падающая на живых
данных, — дефект, который надо увидеть сразу, а не пустой список находок,
неотличимый от чистого слайда.
"""

from __future__ import annotations

import importlib
import inspect
import os
import pkgutil
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Literal

import yaml

from dpd.models import (
    AuditReport,
    Category,
    CheckClass,
    Finding,
    Fixability,
    PresentationStructure,
    RenderedPresentation,
    Severity,
    TemplateSchema,
)
from dpd.models.audit import SEVERITY_ORDER

CHECKS_PACKAGE = "dpd.audit.checks"

CONFIG_ENV = "DPD_AUDIT_CONFIG"
DEFAULT_CONFIG = Path(__file__).resolve().parents[3] / "configs" / "audit.yaml"

Sublayer = Literal["4a", "4b", "4c", "export", "variants"]
"""Когда выполняется проверка: 4a — аудит текста до вёрстки, 4b — аудит формы,
4c — аудит визуала, `export` — после экспорта файла, `variants` — после сборки
всех вариантов."""


@dataclass(frozen=True)
class CheckSpec:
    """Паспорт проверки: то, что чек-лист хранит о ней как о единице.

    Классификация объявляется здесь один раз и проставляется в каждую находку
    каркасом. Если бы находка несла её сама, правка паспорта разошлась бы с
    конструкторами находок при первом же изменении.
    """

    id: str
    category: Category
    check_class: CheckClass
    severity: Severity
    fixability: Fixability
    sublayer: Sublayer
    title: str = ""

    def finding(
        self,
        message: str,
        *,
        severity: Severity | None = None,
        check_class: CheckClass | None = None,
        slide_number: int | None = None,
        slot_id: str | None = None,
        evidence: dict[str, Any] | None = None,
        confidence: float = 1.0,
        environment: dict[str, Any] | None = None,
    ) -> Finding:
        """Собрать находку, проставив классификацию проверки.

        `severity` переопределяется потому, что вес нарушения зависит и от
        обстоятельств: одна неработающая ось различимости — предупреждение,
        две — критично.

        `check_class` переопределяется ради единственной гибридной проверки
        набора — контраста. На сплошном фоне ответ даёт арифметика по кодам
        цветов, на фоне-изображении — пиксели рендера, и класс зависит не от
        проверки, а от того, как получен ответ на конкретном слайде.
        Архитектура требует называть его явно: находку по пикселям нельзя
        выдавать за полностью детерминированную.
        """
        return Finding(
            check_id=self.id,
            category=self.category,
            check_class=check_class or self.check_class,
            severity=severity or self.severity,
            fixability=self.fixability,
            message=message,
            slide_number=slide_number,
            slot_id=slot_id,
            evidence=evidence or {},
            confidence=confidence,
            environment=environment,
        )


@dataclass(frozen=True)
class AuditContext:
    """Всё, что прогон может предложить проверкам на вход.

    Поля необязательны намеренно: на разных шагах пайплайна доступно разное.
    До экспорта нет файла, до рендера нет изображений, до сборки всех
    вариантов нет `variants`. Проверка, чей вход отсутствует, пропускается и
    называется в отчёте пропущенной.
    """

    deck: RenderedPresentation | None = None
    template: TemplateSchema | None = None
    structure: PresentationStructure | None = None
    variants: list[RenderedPresentation] | None = None
    pptx_path: Path | None = None
    images: list[Path] | None = None
    environment: dict[str, Any] | None = None

    def available(self) -> dict[str, Any]:
        """Входы, которыми контекст располагает сейчас."""
        return {name: value for name, value in vars(self).items() if value is not None}


@dataclass(frozen=True)
class RegisteredCheck:
    """Проверка в реестре: паспорт, функция и имена её входов."""

    spec: CheckSpec
    run: Callable[..., Sequence[Finding]]
    parameters: dict[str, Any] = field(default_factory=dict)
    """Имя параметра → значение по умолчанию либо `inspect.Parameter.empty`."""

    def required(self) -> list[str]:
        """Входы, без которых проверку выполнять нечем."""
        return [
            name
            for name, default in self.parameters.items()
            if default is inspect.Parameter.empty
        ]


class Registry:
    """Реестр проверок. Заполняется декоратором при импорте модулей."""

    def __init__(self) -> None:
        self._checks: dict[str, RegisteredCheck] = {}

    def add(self, registered: RegisteredCheck) -> None:
        existing = self._checks.get(registered.spec.id)
        if existing is not None and existing.run is not registered.run:
            raise ValueError(
                f"проверка {registered.spec.id} уже зарегистрирована: "
                "идентификатор — ключ в чек-листе, двух владельцев у него нет"
            )
        self._checks[registered.spec.id] = registered

    def get(self, check_id: str) -> RegisteredCheck | None:
        return self._checks.get(check_id)

    def __iter__(self) -> Iterator[RegisteredCheck]:
        return iter(sorted(self._checks.values(), key=lambda item: item.spec.id))

    def __len__(self) -> int:
        return len(self._checks)

    def snapshot(self) -> dict[str, RegisteredCheck]:
        return dict(self._checks)

    def restore(self, snapshot: dict[str, RegisteredCheck]) -> None:
        self._checks = dict(snapshot)


REGISTRY = Registry()


def check(
    spec: CheckSpec,
    *,
    registry: Registry = REGISTRY,
) -> Callable[[Callable[..., Sequence[Finding]]], Callable[..., Sequence[Finding]]]:
    """Объявить функцию проверкой.

    Функция остаётся обычной и вызываемой напрямую: тесты проверки не обязаны
    поднимать прогон целиком, а каркас не превращается в условие её работы.
    """

    def decorate(fn: Callable[..., Sequence[Finding]]) -> Callable[..., Sequence[Finding]]:
        parameters = {
            name: parameter.default
            for name, parameter in inspect.signature(fn).parameters.items()
        }
        registry.add(RegisteredCheck(spec=spec, run=fn, parameters=parameters))
        return fn

    return decorate


def discover(package: str = CHECKS_PACKAGE) -> Registry:
    """Импортировать модули проверок, чтобы они себя зарегистрировали.

    Каталог обходится, а не перечисляется: перечисление означало бы правку
    чужого файла при добавлении проверки, то есть ровно то, чего критерий
    приёмки T-27 требует избежать.
    """
    importlib.invalidate_caches()
    module = importlib.import_module(package)
    for info in pkgutil.iter_modules(module.__path__, module.__name__ + "."):
        importlib.import_module(info.name)
    return REGISTRY


def config_path() -> Path:
    configured = os.environ.get(CONFIG_ENV)
    return Path(configured) if configured else DEFAULT_CONFIG


def load_section(name: str, path: Path | None = None) -> dict[str, Any]:
    """Прочитать раздел конфигурации аудита.

    Кроме порогов проверок в файле живут настройки самого слоя — например,
    предел итераций починки. Они такие же данные, и место им там же.
    """
    source = path or config_path()
    if not source.is_file():
        return {}
    raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    return dict(raw.get(name) or {})


def load_params(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Прочитать пороги проверок из конфигурации.

    Пороги — данные, а не код: архитектура требует, чтобы их правка не
    трогала исходники.
    """
    return {str(key): dict(value or {}) for key, value in load_section("checks", path).items()}


def param(check_id: str, name: str) -> Any:
    """Взять порог проверки из конфигурации.

    Нужен для прямого вызова проверки, вне прогона: в прогоне порог связывает
    каркас. Второго значения порога в коде при этом не заводится — источник
    истины один, `configs/audit.yaml`, и расходиться ему не с чем.
    """
    values = load_params().get(check_id, {})
    if name not in values:
        raise KeyError(f"в {config_path().name} нет порога {name} для проверки {check_id}")
    return values[name]


def run_checks(
    context: AuditContext,
    *,
    run_id: str | None = None,
    classes: Sequence[CheckClass] | None = None,
    sublayers: Sequence[Sublayer] | None = None,
    params: Mapping[str, Mapping[str, Any]] | None = None,
    registry: Registry | None = None,
) -> AuditReport:
    """Выполнить подходящие проверки и собрать отчёт.

    `classes` и `sublayers` разделяют подслои: 4b выполняется для всех трёх
    вариантов по данным файла, 4c — только для выбранного варианта и по
    рендеру. Без фильтров выполняется всё, чему хватает входов.
    """
    registry = registry if registry is not None else discover()
    settings = dict(params) if params is not None else load_params()

    findings: list[Finding] = []
    ran: list[str] = []
    skipped: list[str] = []

    for registered in registry:
        spec = registered.spec
        if classes is not None and spec.check_class not in classes:
            continue
        if sublayers is not None and spec.sublayer not in sublayers:
            continue

        inputs = context.available()
        if any(name not in inputs for name in registered.required() if name in _INPUT_NAMES):
            skipped.append(spec.id)
            continue

        arguments = _bind(registered, inputs, settings.get(spec.id, {}))
        produced = registered.run(**arguments)
        ran.append(spec.id)
        findings.extend(_stamped(produced, context))

    return AuditReport(
        run_id=run_id or uuid.uuid4().hex[:8],
        variant=context.deck.variant if context.deck is not None else None,
        findings=_ordered(findings),
        checks_run=ran,
        checks_skipped=skipped,
    )


_INPUT_NAMES = frozenset(item.name for item in fields(AuditContext))


def _bind(
    registered: RegisteredCheck,
    inputs: Mapping[str, Any],
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    """Связать параметры проверки с входами прогона и порогами конфигурации."""
    arguments: dict[str, Any] = {}
    for name, default in registered.parameters.items():
        if name in inputs:
            arguments[name] = inputs[name]
        elif name in settings:
            arguments[name] = settings[name]
        elif default is inspect.Parameter.empty:
            raise ValueError(
                f"{registered.spec.id}: параметру {name} нечего передать. "
                "Он не совпадает ни с входом прогона, ни с порогом из "
                "configs/audit.yaml — вероятно, опечатка в имени"
            )
    return arguments


def _stamped(findings: Sequence[Finding], context: AuditContext) -> list[Finding]:
    """Проставить находкам класса `rendered` сведения об окружении.

    Оговорка архитектуры: воспроизводимость такой находки условна. Если
    гарнитур шаблона нет в системе, рендерер подставит замену, метрики текста
    изменятся, и результат может отличаться. Находка обязана это нести.
    """
    if context.environment is None:
        return list(findings)
    return [
        replace_environment(finding, context.environment)
        if finding.check_class == "rendered" and finding.environment is None
        else finding
        for finding in findings
    ]


def replace_environment(finding: Finding, environment: dict[str, Any]) -> Finding:
    return finding.model_copy(update={"environment": environment})


def _ordered(findings: Sequence[Finding]) -> list[Finding]:
    """Критичное первым, у каждой находки — свой номер в отчёте."""
    ordered = sorted(findings, key=lambda item: SEVERITY_ORDER.index(item.severity))
    return [
        finding.model_copy(update={"id": f"f{number}"})
        for number, finding in enumerate(ordered, start=1)
    ]


__all__ = [
    "REGISTRY",
    "AuditContext",
    "CheckSpec",
    "RegisteredCheck",
    "Registry",
    "Sublayer",
    "check",
    "config_path",
    "discover",
    "load_params",
    "load_section",
    "param",
    "run_checks",
]
