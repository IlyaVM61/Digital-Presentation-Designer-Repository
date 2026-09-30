"""T-27: каркас проверки как самостоятельной единицы.

Критерий приёмки задачи: **добавление проверки не требует правок в других
слоях**. Здесь он проверяется буквально — файл кладётся в каталог проверок,
и находка появляется в отчёте прогона; ни реестр, ни `__init__.py`, ни
вызывающий код при этом не меняются.

Остальные тесты закрепляют то, из чего состоит единица проверки по
`audit-architecture.md`: идентификатор, категория, класс источника данных,
критичность, исправимость, уверенность и параметры. Метаданные объявляются
один раз в спецификации и проставляются каркасом, иначе находка и реестр
разойдутся при первой же правке.

Пороги живут в `configs/audit.yaml`, а не в коде: архитектура называет их
данными, и на защите настраиваемость показывается файлом, а не диффом.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from dpd.audit import (
    REGISTRY,
    AuditContext,
    CheckSpec,
    Registry,
    check,
    discover,
    load_params,
    run_checks,
)
from dpd.export import export_pptx
from dpd.layout import compose_variants
from dpd.models import (
    AuditReport,
    Canvas,
    Finding,
    PresentationStructure,
    RenderedPresentation,
    Slide,
    SlideBody,
    StructureSlide,
)
from dpd.parsing import parse_template

CHECKS_DIR = Path(__file__).resolve().parents[1] / "src" / "dpd" / "audit" / "checks"
AUDIT_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "audit.yaml"


def spec(check_id: str = "probe.sample", **overrides) -> CheckSpec:
    values = {
        "id": check_id,
        "category": "layout",
        "check_class": "file",
        "severity": "warning",
        "fixability": "mechanical",
        "sublayer": "4b",
        "title": "Пробная проверка",
    }
    values.update(overrides)
    return CheckSpec(**values)


def deck(variant: str = "A") -> RenderedPresentation:
    return RenderedPresentation(
        variant=variant,
        template_hash="0" * 8,
        canvas=Canvas(width_emu=12192000, height_emu=6858000),
        slides=[Slide(id="s1", layout_id="l1")],
    )


@pytest.fixture
def registry() -> Iterator[Registry]:
    """Реестр глобален; тест обязан вернуть его в исходное состояние.

    Слепок снимается после обхода каталога: модули проверок регистрируют
    себя один раз за процесс, и слепок, снятый раньше, вернул бы пустой
    реестр навсегда.
    """
    discover()
    saved = REGISTRY.snapshot()
    try:
        yield REGISTRY
    finally:
        REGISTRY.restore(saved)


# --- Единица проверки: метаданные объявляются один раз ---------------------


def test_spec_stamps_its_metadata_on_every_finding() -> None:
    """Проверка не повторяет свою классификацию в каждой находке.

    Иначе реестр и находки разойдутся: поправят спецификацию, забудут
    конструктор, и находка соврёт о своём классе.
    """
    finding = spec().finding("кегль вне шкалы", slide_number=4, slot_id="title")

    assert finding.check_id == "probe.sample"
    assert finding.category == "layout"
    assert finding.check_class == "file"
    assert finding.severity == "warning"
    assert finding.fixability == "mechanical"
    assert finding.slide_number == 4 and finding.slot_id == "title"
    assert finding.status == "open"
    assert finding.confidence == 1.0


def test_severity_may_be_lowered_per_finding() -> None:
    """Критичность зависит и от обстоятельств: та же проверка, разный вес.

    `variants.low_distinction` предупреждает, когда не работает одна ось, и
    становится критичной, когда не работают две.
    """
    finding = spec().finding("оси не сработали", severity="critical")

    assert finding.severity == "critical"
    assert finding.check_id == "probe.sample"


def test_confidence_lowers_the_finding_without_hiding_it() -> None:
    """Уверенность наследуется от токенов схемы и передаётся в находку.

    Без этого система обвиняла бы пользователя в нарушении правила, которое
    сама вывела предположительно.
    """
    finding = spec().finding("цвет вне палитры", confidence=0.6, evidence={"actual": "#123456"})

    assert finding.confidence == 0.6
    assert finding.evidence == {"actual": "#123456"}


def test_finding_serializes_class_under_the_documented_key() -> None:
    """Контракт находки описан в архитектуре: ключ называется `class`."""
    payload = spec().finding("сообщение").model_dump(by_alias=True)

    assert payload["class"] == "file"
    assert payload["checkId"] == "probe.sample"


# --- Реестр ---------------------------------------------------------------


def test_implemented_checks_are_in_the_registry() -> None:
    """Две написанные ранее проверки вобраны каркасом, а не стоят рядом."""
    discover()

    assert REGISTRY.get("integrity.raster_slide") is not None
    assert REGISTRY.get("variants.low_distinction") is not None


def test_duplicate_check_id_is_rejected(registry: Registry) -> None:
    """Идентификатор — ключ в чек-листе: два владельца у него невозможны."""

    @check(spec("probe.duplicate"))
    def first(deck):
        return []

    with pytest.raises(ValueError, match="probe.duplicate"):

        @check(spec("probe.duplicate"))
        def second(deck):
            return []


# --- Вход проверки: связывание по именам параметров ------------------------


def test_check_receives_inputs_by_parameter_name(registry: Registry) -> None:
    """Проверка объявляет, что ей нужно, именами параметров.

    Каркас не навязывает единую сигнатуру: у проверки формы вход —
    свёрстанная колода, у проверки целостности — файл после экспорта.
    """
    seen: dict[str, object] = {}

    @check(spec("probe.inputs"))
    def probe(deck, template=None):
        seen["deck"] = deck
        seen["template"] = template
        return []

    run_checks(AuditContext(deck=deck()), registry=registry)

    assert seen["deck"] is not None and seen["deck"].variant == "A"
    assert seen["template"] is None


def test_check_is_skipped_when_its_input_is_absent(registry: Registry) -> None:
    """Отсутствие входа — не ошибка, но и не тишина: пропуск назван в отчёте.

    Пустой список находок иначе неотличим от «проверка не выполнялась», а
    это ровно та подмена, на которой аудит теряет доверие.
    """
    ran: list[str] = []

    @check(spec("probe.needs_file"))
    def probe(pptx_path):
        ran.append("да")
        return [spec("probe.needs_file").finding("не должно было выполниться")]

    report = run_checks(AuditContext(deck=deck()), registry=registry)

    assert ran == []
    assert report.findings == []
    assert "probe.needs_file" in report.checks_skipped
    assert "probe.needs_file" not in report.checks_run


def test_unbound_parameter_without_default_is_a_loud_error(registry: Registry) -> None:
    """Опечатка в имени параметра не должна тихо отключать проверку."""

    @check(spec("probe.typo"))
    def probe(deck, tolerans):
        return []

    with pytest.raises(ValueError, match="tolerans"):
        run_checks(AuditContext(deck=deck()), registry=registry)


# --- Пороги как данные ----------------------------------------------------


def test_thresholds_come_from_configuration_not_from_code(registry: Registry) -> None:
    """Порог передаётся параметром из конфигурации, а не зашит в проверке."""
    got: list[float] = []

    @check(spec("probe.threshold"))
    def probe(deck, tolerance=0.5):
        got.append(tolerance)
        return []

    run_checks(
        AuditContext(deck=deck()),
        params={"probe.threshold": {"tolerance": 0.01}},
        registry=registry,
    )

    assert got == [0.01]


def test_audit_config_is_readable_and_versioned() -> None:
    """`configs/audit.yaml` существует и несёт версию набора порогов."""
    raw = yaml.safe_load(AUDIT_CONFIG.read_text(encoding="utf-8"))

    assert raw["version"] >= 1
    assert isinstance(raw["checks"], dict) and raw["checks"]


def test_every_configured_parameter_reaches_its_check() -> None:
    """Ключи конфигурации сверяются с сигнатурами: мёртвых порогов нет.

    Порог, имя которого не совпало с параметром, молча не применяется — и
    выглядит это как настроенная система, которая на самом деле работает на
    умолчаниях.
    """
    discover()
    for check_id, values in load_params().items():
        registered = REGISTRY.get(check_id)
        assert registered is not None, f"порог задан для несуществующей проверки {check_id}"
        for name in values:
            assert name in registered.parameters, (
                f"{check_id}: параметра {name} нет в сигнатуре проверки"
            )


# --- Отчёт ----------------------------------------------------------------


def test_report_sorts_findings_by_severity_and_numbers_them(registry: Registry) -> None:
    """Критичное показывается первым, у каждой находки есть свой номер."""

    @check(spec("probe.mixed"))
    def probe(deck):
        base = spec("probe.mixed")
        return [
            base.finding("совет", severity="advice"),
            base.finding("критично", severity="critical"),
            base.finding("предупреждение", severity="warning"),
        ]

    report = run_checks(AuditContext(deck=deck()), registry=registry)

    assert isinstance(report, AuditReport)
    assert [f.severity for f in report.findings] == ["critical", "warning", "advice"]
    assert [f.id for f in report.findings] == ["f1", "f2", "f3"]
    assert report.variant == "A"
    assert "probe.mixed" in report.checks_run


def test_rendered_findings_carry_the_render_environment(registry: Registry) -> None:
    """Оговорка архитектуры: воспроизводимость класса `rendered` условна.

    Если гарнитур шаблона нет в системе, рендерер подставит замену и метрики
    изменятся. Находка обязана нести сведения об окружении, иначе её
    предъявляют как полностью детерминированную, а это неправда.
    """

    @check(spec("probe.rendered", check_class="rendered", sublayer="4c"))
    def probe(deck):
        return [spec("probe.rendered", check_class="rendered").finding("контраст 3,1:1")]

    report = run_checks(
        AuditContext(deck=deck(), environment={"fontsAvailable": False}),
        registry=registry,
    )

    assert report.findings[0].environment == {"fontsAvailable": False}


def test_classes_filter_selects_the_sublayer(registry: Registry) -> None:
    """Подслои 4b и 4c запускаются раздельно: у них разный вход и кратность."""

    @check(spec("probe.file_class", check_class="file"))
    def by_file(deck):
        return [spec("probe.file_class").finding("по файлу")]

    @check(spec("probe.rendered_class", check_class="rendered"))
    def by_render(deck):
        return [spec("probe.rendered_class", check_class="rendered").finding("по рендеру")]

    report = run_checks(AuditContext(deck=deck()), classes=("file",), registry=registry)

    assert [f.check_id for f in report.findings] == ["probe.file_class"]
    assert "probe.rendered_class" not in report.checks_run
    assert "probe.rendered_class" not in report.checks_skipped


# --- Критерий приёмки -----------------------------------------------------


NEW_CHECK_SOURCE = '''"""Пробная проверка: положена одним файлом, больше ничего не правилось."""

from dpd.audit import CheckSpec, check

SPEC = CheckSpec(
    id="probe.dropped_in",
    category="density",
    check_class="file",
    severity="advice",
    fixability="none",
    sublayer="4b",
    title="Проверка, добавленная одним файлом",
)


@check(SPEC)
def check_dropped_in(deck, limit=6):
    return [SPEC.finding(f"выполнилась, предел {limit}")]
'''


def test_adding_a_check_requires_no_edits_in_other_layers(registry: Registry) -> None:
    """Критерий приёмки T-27, проверенный буквально.

    В каталог проверок кладётся файл. Не меняются ни `checks/__init__.py`,
    ни реестр, ни вызывающий код — проверка сама объявляет себя, получает
    порог из конфигурации и попадает в отчёт прогона.
    """
    module = CHECKS_DIR / "_probe_dropped_in.py"
    module.write_text(NEW_CHECK_SOURCE, encoding="utf-8")
    try:
        discover()
        report = run_checks(
            AuditContext(deck=deck()),
            params={"probe.dropped_in": {"limit": 3}},
            registry=registry,
        )
    finally:
        module.unlink()
        sys.modules.pop("dpd.audit.checks._probe_dropped_in", None)
        importlib.invalidate_caches()

    found = [f for f in report.findings if f.check_id == "probe.dropped_in"]
    assert found, "проверка, добавленная файлом, не выполнилась"
    assert isinstance(found[0], Finding)
    assert found[0].category == "density"
    assert found[0].message.endswith("предел 3")


# --- Каркас на живых данных ------------------------------------------------


def test_frame_drives_the_real_checks_on_a_real_template(tmp_path: Path) -> None:
    """Каркас вобрал написанные ранее проверки, а не встал рядом с ними.

    Пробные проверки доказывают устройство каркаса, но не то, что через него
    работают настоящие. Здесь колода собирается из шаблона и выгружается, а
    проверки получают входы и пороги от прогона — ни один порог в вызове не
    назван.

    Прогон делается дважды намеренно: до экспорта файла ещё нет, и проверка
    целостности обязана оказаться в пропущенных, а не промолчать как чистая.
    """
    calibration = (
        Path(__file__).resolve().parents[1]
        / "assets"
        / "templates"
        / "calibration"
        / "VK Tech шаблон.pptx"
    )
    if not calibration.exists():
        pytest.skip("нет калибровочного шаблона")

    schema = parse_template(calibration)
    structure = PresentationStructure(
        slides=[
            StructureSlide(id="s1", role="title", headline="Итоги пилота"),
            StructureSlide(
                id="s2",
                role="data",
                headline="Что изменилось",
                body=SlideBody(items=["Сборка колоды втрое быстрее", "Правки переживают сохранение"]),
            ),
        ]
    )
    variants = compose_variants(structure, schema)

    before = run_checks(AuditContext(deck=variants[0], template=schema, variants=variants))
    assert "variants.low_distinction" in before.checks_run
    assert "integrity.raster_slide" in before.checks_skipped, "файла ещё нет — и это надо сказать"

    exported = export_pptx(variants[0], schema, calibration, tmp_path / "variant-a.pptx")
    after = run_checks(
        AuditContext(deck=variants[0], template=schema, variants=variants, pptx_path=exported),
        run_id="t27",
    )

    assert "integrity.raster_slide" not in after.checks_skipped
    assert "integrity.raster_slide" in after.checks_run
    assert after.by_severity("critical") == [], "нативный экспорт не должен давать растровых слайдов"
    assert after.run_id == "t27" and after.variant == variants[0].variant
