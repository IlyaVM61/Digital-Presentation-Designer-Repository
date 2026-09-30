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
import yaml

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


def test_only_requested_formats_are_offered(template: Path, out_dir: Path) -> None:
    """Выгружается ровно то, что попросили.

    `.pptx` собирается всегда — он основа колоды и источник для конвертации
    в PDF, — но попадать в выдачу он не должен, если его не просили.
    Пользователь снял галочку PowerPoint и всё равно получал файл: интерфейс
    обещал выбор, которого не было.
    """
    result = run_pipeline(template, structure_from_outline(OUTLINE), out_dir / "formats", formats=("html",))

    assert set(result.exports) == {"html"}


def test_libreoffice_is_started_once_for_previews_and_pdf(
    template: Path, out_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Превью и PDF всех трёх вариантов довольствуются одной конвертацией.

    Это самая дорогая операция пайплайна — 13–28 с, — и второй запуск стоил
    бы столько же на тот же результат. При бюджете в пять минут на полный
    цикл такая трата недопустима. С T-39 в конвертацию идут все три
    варианта: их показывают рядом, и колоды передаются одной пачкой.
    """
    if soffice_path() is None:
        pytest.skip("LibreOffice не найден")

    from dpd import orchestrator

    calls: list[list[Path]] = []
    original = orchestrator.convert_to_pdfs

    def counted(pptx_paths, out, **kwargs):
        calls.append([Path(path) for path in pptx_paths])
        return original(pptx_paths, out, **kwargs)

    monkeypatch.setattr(orchestrator, "convert_to_pdfs", counted)

    result = run_pipeline(
        template,
        structure_from_outline(OUTLINE),
        out_dir / "once",
        formats=("pptx", "pdf"),
        previews=True,
    )

    assert len(calls) == 1, f"LibreOffice запущен {len(calls)} раз"
    assert len(calls[0]) == 3, "в конвертацию попали не все варианты"
    assert result.exports["pdf"].is_file()
    assert len(result.previews) == len(result.chosen.slides)
    assert result.timings["render"] > 0

    # T-39: превью каждого варианта свои, и в одну папку они не сваливаются.
    assert set(result.variant_previews) == {deck.variant for deck in result.variants}
    for deck in result.variants:
        images = result.variant_previews[deck.variant]
        assert len(images) == len(deck.slides), f"вариант {deck.variant}: превью не на каждый слайд"
    assert len({image for images in result.variant_previews.values() for image in images}) == 3 * len(
        result.chosen.slides
    ), "превью одного варианта перезаписали превью другого"


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


# --- T-39: три варианта рядом и выбор после прогона -------------------------
#
# Пользователь выбирает вариант, посмотрев на все три. Значит, к концу прогона
# готовы файлы каждого: выбор не должен стоить второго прогона и второго
# запуска LibreOffice. Цена измерена: +1–2 с на `.pptx` двух лишних
# вариантов, +8 с на их конвертацию, HTML почти бесплатен.


def test_every_variant_is_exported_for_choice_after_the_run(template: Path, out_dir: Path) -> None:
    result = run_pipeline(
        template, structure_from_outline(OUTLINE), out_dir / "all", formats=("pptx", "html")
    )

    assert set(result.variant_exports) == {"A", "B", "C"}
    for variant, files in result.variant_exports.items():
        assert set(files) == {"pptx", "html"}, f"вариант {variant}: не все форматы"
        assert all(path.is_file() for path in files.values())
        assert all(f"-{variant}." in path.name for path in files.values()), "имя файла не называет вариант"
    assert result.exports == result.variant_exports[result.chosen.variant]


def test_variant_names_come_from_the_config(template: Path, out_dir: Path) -> None:
    """Интерфейс называет варианты по-человечески, а имена живут в конфиге."""
    from dpd.layout.variants import load_profiles

    result = run_pipeline(template, structure_from_outline(OUTLINE), out_dir / "names", formats=("html",))

    assert result.variant_names == {profile.id: profile.name for profile in load_profiles()}


def test_identical_variants_are_reported(
    template: Path, out_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Три одинаковые колоды нельзя молча выдать за три варианта.

    Стратегия вариантов прямо требует предъявить этот факт пользователю: на
    бедном шаблоне оси регистра могут не сработать. Одинаковые профили
    воспроизводят такой шаблон без подбора файла.
    """
    same = {"prefer_scheme": "any", "prefer_families": ["content"], "size_shift": 0, "layout_offset": 0}
    config = tmp_path / "variants.yaml"
    config.write_text(
        yaml.safe_dump(
            {"version": 1, "profiles": [{"id": name, "name": f"Вариант {name}", **same} for name in "ABC"]},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DPD_VARIANTS_CONFIG", str(config))

    result = run_pipeline(template, structure_from_outline(OUTLINE), out_dir / "same", formats=("html",))

    assert [finding.check_id for finding in result.distinction] == ["variants.low_distinction"]


def test_distinct_variants_raise_no_alarm(template: Path, out_dir: Path) -> None:
    """На нормальном шаблоне проверка молчит — иначе предупреждению не поверят."""
    result = run_pipeline(template, structure_from_outline(OUTLINE), out_dir / "distinct", formats=("html",))

    assert result.distinction == []


# --- T-40: работа с находками ------------------------------------------------
#
# Выбор исправления стоит одного шага, а не второго прогона: пользователь уже
# смотрит на варианты, и пересобирать все три ради одного слайда незачем.
# Но файлы выбранного варианта обязаны измениться — исправление, которого нет
# в скачанном файле, пользователю ничего не дало.

CROWDED = """Итоги пилота
Что изменилось за квартал
- Срок сборки колоды сократился с девяти дней до трёх
- Правки текста переживают сохранение файла
Что умеет система
- Разбор шаблона занимает меньше секунды
- Три варианта собираются одним нажатием
- Правки текста переживают сохранение
- Таблицы остаются таблицами
- Диаграммы остаются диаграммами
- Проверка оформления идёт до выгрузки
- Механические дефекты исправляются сами
- Файлы готовы в трёх форматах
"""


def test_finding_fix_reaudit_cycle_goes_through(template: Path, out_dir: Path) -> None:
    """Критерий приёмки T-40: находка → исправление → повторный аудит → файлы."""
    from pptx import Presentation

    from dpd.orchestrator import revise

    result = run_pipeline(template, structure_from_outline(CROWDED), out_dir / "revise", formats=("pptx", "html"))
    variant = result.variants[0].variant
    report = result.reports[variant]
    index = next(i for i, f in enumerate(report.findings) if f.check_id == "density.too_many_bullets")
    assert [item.id for item in result.remedies[variant][index]] == ["split"]
    slides = len(Presentation(result.variant_exports[variant]["pptx"]).slides)
    autofixed = [f for f in report.findings if f.status == "autofixed"]
    untouched = {key: value for key, value in result.reports.items() if key != variant}

    revised = revise(result, variant, {index: "split"})

    after = revised.reports[variant]
    assert "density.too_many_bullets" not in {f.check_id for f in after.findings}
    assert len(Presentation(revised.variant_exports[variant]["pptx"]).slides) == slides + 1
    html = revised.variant_exports[variant]["html"].read_text(encoding="utf-8")
    assert html.count('<section class="slide"') == slides + 1
    assert "Файлы готовы в трёх форматах" in html
    assert [item.remedy.id for item in revised.revision.applied] == ["split"]
    assert revised.revision.before.get("warning", 0) > revised.revision.after.get("warning", 0)
    assert [f for f in after.findings if f.status == "autofixed"][: len(autofixed)] == autofixed, (
        "автоисправления первого прогона пропали из отчёта"
    )
    assert {key: revised.reports[key] for key in untouched} == untouched, "задеты другие варианты"
    assert revised.chosen.variant == result.chosen.variant
    assert len(revised.chosen.slides) == slides + 1


def test_revision_without_choices_changes_nothing(template: Path, out_dir: Path) -> None:
    from dpd.orchestrator import revise

    result = run_pipeline(template, structure_from_outline(CROWDED), out_dir / "keep", formats=("html",))
    variant = result.variants[1].variant

    revised = revise(result, variant, {})

    assert revised.reports[variant] == result.reports[variant]
    assert not revised.revision.applied
