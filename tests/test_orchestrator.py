"""T-38: последовательность этапов прогона.

Интерфейсу нужно что вызывать: до сих пор слои складывались вручную в тестах
и демонстрациях. Оркестратор собирает их в один путь — от файла шаблона до
выгруженной колоды, — и отдаёт наружу две вещи, без которых интерфейс
бесполезен: события прогресса и время каждого этапа.

**Время замеряется здесь, а не в интерфейсе.** Бюджет ТЗ — пять минут на
полный цикл (решение D4), и разбивка по этапам нужна, чтобы понимать, что
именно не уложилось. Интерфейс её только показывает.

**LibreOffice запускается один раз.** Он же самая дорогая операция пайплайна:
13–28 с на колоду. Превью и PDF нуждаются в одной и той же конвертации, и
второй запуск стоил бы столько же — это проверяется отдельно.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from dpd.generation import structure_from_outline
from dpd.orchestrator import STAGES, run_pipeline
from dpd.render import soffice_path

CALIBRATION = Path(__file__).resolve().parents[1] / "assets" / "templates" / "calibration"
TEMPLATE = "VK Tech шаблон.pptx"

OUTLINE = """Итоги пилота
Что изменилось за квартал
- Срок сборки колоды сократился с девяти дней до трёх
- Правки текста переживают сохранение файла
Как шли
- Разбор шаблона
- Вёрстка
- Аудит
"""


@pytest.fixture(scope="module")
def out_dir() -> Iterator[Path]:
    base = Path(os.environ.get("DPD_OUTPUT_DIR", "D:/dpd-out"))
    path = base / "tests" / "orchestrator"
    path.mkdir(parents=True, exist_ok=True)
    yield path


@pytest.fixture(scope="module")
def template() -> Path:
    path = CALIBRATION / TEMPLATE
    if not path.exists():
        pytest.skip(f"нет калибровочного шаблона {TEMPLATE}")
    return path


# --- Сквозной путь ---------------------------------------------------------


def test_run_goes_from_template_file_to_exported_deck(template: Path, out_dir: Path) -> None:
    """Критерий приёмки задачи: путь проходится целиком.

    Формат `.pptx` берётся один: он обязателен, быстр и именно его ТЗ
    требует собирать нативными объектами.
    """
    result = run_pipeline(template, structure_from_outline(OUTLINE), out_dir / "run", formats=("pptx",))

    assert len(result.variants) == 3, "три варианта — требование ТЗ"
    assert result.exports["pptx"].is_file()
    assert result.chosen.variant == result.variants[0].variant
    assert result.template.layouts, "схема шаблона не вернулась"


def test_every_stage_is_timed(template: Path, out_dir: Path) -> None:
    """Разбивка по этапам — то, ради чего считается бюджет в пять минут."""
    result = run_pipeline(template, structure_from_outline(OUTLINE), out_dir / "timings", formats=("pptx",))

    assert set(result.timings) == {"parse", "layout", "audit", "export"}
    assert all(value >= 0 for value in result.timings.values())
    assert result.seconds >= sum(result.timings.values()) * 0.9


def test_progress_is_reported_stage_by_stage(template: Path, out_dir: Path) -> None:
    """Интерфейсу нужно показывать, что происходит: прогон идёт десятки секунд."""
    events: list[tuple[str, int, int]] = []

    run_pipeline(
        template,
        structure_from_outline(OUTLINE),
        out_dir / "progress",
        formats=("pptx",),
        progress=lambda stage, done, total: events.append((stage, done, total)),
    )

    # Этап шлёт не одно событие, а несколько: начало, шаги, конец. Важно,
    # что этапы идут в каноническом порядке и не перемежаются друг другом.
    порядок = [
        name
        for index, (name, _, _) in enumerate(events)
        if index == 0 or events[index - 1][0] != name
    ]
    случившиеся = {name for name, _, _ in events}
    assert порядок == [stage for stage in STAGES if stage in случившиеся]
    assert events[-1][1] == events[-1][2], "последнее событие должно сообщать о завершении"


def test_audit_runs_for_every_variant(template: Path, out_dir: Path) -> None:
    """Аудит формы выполняется для всех трёх вариантов: вёрстка у них разная."""
    result = run_pipeline(template, structure_from_outline(OUTLINE), out_dir / "audit", formats=("pptx",))

    assert set(result.reports) == {variant.variant for variant in result.variants}
    for report in result.reports.values():
        assert not [item for item in report.findings if item.fixability == "mechanical"], (
            "механические находки должны быть исправлены починкой"
        )


# --- Форматы ---------------------------------------------------------------


def test_html_export_needs_no_libreoffice(template: Path, out_dir: Path) -> None:
    """HTML собирается из контракта: тяжёлой конвертации для него не нужно."""
    result = run_pipeline(
        template, structure_from_outline(OUTLINE), out_dir / "html", formats=("pptx", "html")
    )

    assert result.exports["html"].is_file()
    assert "Итоги пилота" in result.exports["html"].read_text(encoding="utf-8")


def test_libreoffice_is_started_once_for_previews_and_pdf(
    template: Path, out_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Превью и PDF довольствуются одной конвертацией.

    Это самая дорогая операция пайплайна — 13–28 с, — и второй запуск стоил
    бы столько же на тот же результат. При бюджете в пять минут на полный
    цикл такая трата недопустима.
    """
    if soffice_path() is None:
        pytest.skip("LibreOffice не найден")

    from dpd import orchestrator

    calls: list[Path] = []
    original = orchestrator.convert_to_pdf

    def counted(pptx_path, out, **kwargs):
        calls.append(Path(pptx_path))
        return original(pptx_path, out, **kwargs)

    monkeypatch.setattr(orchestrator, "convert_to_pdf", counted)

    result = run_pipeline(
        template,
        structure_from_outline(OUTLINE),
        out_dir / "once",
        formats=("pptx", "pdf"),
        previews=True,
    )

    assert len(calls) == 1, f"LibreOffice запущен {len(calls)} раз"
    assert result.exports["pdf"].is_file()
    assert len(result.previews) == len(result.chosen.slides)
    assert result.timings["render"] > 0


# --- Выбор варианта --------------------------------------------------------


def test_chosen_variant_can_be_named(template: Path, out_dir: Path) -> None:
    """Пользователь выбирает вариант — экспорт идёт для выбранного."""
    result = run_pipeline(
        template, structure_from_outline(OUTLINE), out_dir / "choice", formats=("pptx",), variant="C"
    )

    assert result.chosen.variant == "C"


def test_unknown_variant_is_a_loud_error(template: Path, out_dir: Path) -> None:
    with pytest.raises(ValueError, match="вариант"):
        run_pipeline(
            template, structure_from_outline(OUTLINE), out_dir / "bad", formats=("pptx",), variant="Я"
        )
