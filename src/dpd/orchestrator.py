"""Последовательность этапов пайплайна, события прогресса, замеры времени.

Пайплайн синхронный, с внутренним параллелизмом (ADR-0002):
многопользовательность вне границ проекта, пользователь всё равно ждёт
результат.

Бюджет — пять минут на полный цикл, считая разбор незнакомого шаблона
(решение D4). Разбивка по этапам попадает в отчёт: без неё непонятно, что
именно не уложилось.

**Время замеряется здесь, а не в интерфейсе.** Интерфейс его только
показывает, и подменить замер показом нельзя: тогда измерялась бы отрисовка
страницы, а не работа.

**LibreOffice запускается один раз на прогон.** Превью слайдов и экспорт в
PDF нуждаются в одной и той же конвертации, а она самая дорогая операция
пайплайна: 13–28 с на колоду. Второй запуск потратил бы столько же на тот же
результат.

**Аудит выполняется для всех трёх вариантов, а починка — сразу за ним.**
Вёрстка у вариантов разная, и находки у них тоже разные; показывать
пользователю дефекты, которые система умеет исправить сама, незачем.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from dpd.audit import AuditContext, discover
from dpd.audit.fixer import repair
from dpd.export import export_html, export_pdf, export_pptx
from dpd.layout import compose_variants
from dpd.models import (
    AuditReport,
    PresentationStructure,
    RenderedPresentation,
    TemplateSchema,
)
from dpd.parsing import parse_template
from dpd.render import convert_to_pdf, render_pdf_pages

STAGES: tuple[str, ...] = ("parse", "layout", "audit", "render", "export")
"""Порядок этапов. Рендер идёт перед экспортом: его конвертация нужна и
превью, и PDF, и делать её дважды нельзя."""

STAGE_TITLES = {
    "parse": "Разбор шаблона",
    "layout": "Сборка трёх вариантов",
    "audit": "Аудит и починка",
    "render": "Рендер превью",
    "export": "Выгрузка файлов",
}
"""Названия для интерфейса: пользователь ждёт десятки секунд и должен видеть,
что именно происходит."""

Progress = Callable[[str, int, int], None]
"""Событие прогресса: этап, сколько сделано, сколько всего."""


@dataclass(frozen=True)
class RunResult:
    """Итог прогона: всё, что интерфейсу нужно показать и отдать."""

    run_id: str
    template: TemplateSchema
    variants: list[RenderedPresentation]
    chosen: RenderedPresentation
    reports: dict[str, AuditReport]
    exports: dict[str, Path] = field(default_factory=dict)
    previews: list[Path] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    seconds: float = 0.0


def run_pipeline(
    template_path: str | Path,
    structure: PresentationStructure,
    out_dir: str | Path,
    *,
    formats: Sequence[str] = ("pptx",),
    variant: str | None = None,
    previews: bool = False,
    progress: Progress | None = None,
) -> RunResult:
    """Пройти путь от файла шаблона до выгруженной колоды.

    `variant` выбирает, какой из трёх вариантов выгружать; по умолчанию
    первый. Аудит при этом выполняется для всех трёх: пользователь должен
    видеть состояние каждого, а не только выбранного.
    """
    template_path, out_dir = Path(template_path), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    discover()

    run_id = uuid.uuid4().hex[:8]
    timings: dict[str, float] = {}
    started = time.perf_counter()
    report_progress = progress or (lambda *_: None)

    with _timed(timings, "parse"):
        report_progress("parse", 0, 1)
        schema = parse_template(template_path)
        report_progress("parse", 1, 1)

    with _timed(timings, "layout"):
        report_progress("layout", 0, 1)
        variants = compose_variants(structure, schema)
        report_progress("layout", 1, 1)

    chosen = _chosen(variants, variant)

    reports: dict[str, AuditReport] = {}
    with _timed(timings, "audit"):
        for index, deck in enumerate(variants):
            report_progress("audit", index, len(variants))
            result = repair(AuditContext(deck=deck, template=schema, structure=structure))
            variants[index] = result.deck
            reports[deck.variant] = result.report
        report_progress("audit", len(variants), len(variants))
    chosen = next(deck for deck in variants if deck.variant == chosen.variant)

    exports: dict[str, Path] = {}
    images: list[Path] = []
    with _timed(timings, "export"):
        report_progress("export", 0, len(formats))
        deck_pptx = export_pptx(chosen, schema, template_path, out_dir / f"{template_path.stem}-{chosen.variant}.pptx")
        exports["pptx"] = deck_pptx
        report_progress("export", 1, len(formats))

    # Конвертация одна на прогон: её результат нужен и превью, и PDF.
    pdf_source: Path | None = None
    if previews or "pdf" in formats:
        with _timed(timings, "render"):
            report_progress("render", 0, 1)
            pdf_source = convert_to_pdf(deck_pptx, out_dir)
            if previews:
                images = render_pdf_pages(pdf_source, out_dir)
            report_progress("render", 1, 1)

    with _timed(timings, "export", add=True):
        done = 1
        if "pdf" in formats:
            exports["pdf"] = export_pdf(
                deck_pptx, out_dir / f"{deck_pptx.stem}.pdf", source_pdf=pdf_source
            )
            done += 1
            report_progress("export", done, len(formats))
        if "html" in formats:
            exports["html"] = export_html(chosen, schema, out_dir / f"{deck_pptx.stem}.html")
            done += 1
            report_progress("export", done, len(formats))
        report_progress("export", len(formats), len(formats))

    return RunResult(
        run_id=run_id,
        template=schema,
        variants=variants,
        chosen=chosen,
        reports=reports,
        exports=exports,
        previews=images,
        timings=timings,
        seconds=time.perf_counter() - started,
    )


def _chosen(variants: list[RenderedPresentation], variant: str | None) -> RenderedPresentation:
    if variant is None:
        return variants[0]
    found = next((deck for deck in variants if deck.variant == variant), None)
    if found is None:
        known = ", ".join(deck.variant for deck in variants)
        raise ValueError(f"вариант «{variant}» не собран; есть: {known}")
    return found


@contextmanager
def _timed(timings: dict[str, float], stage: str, *, add: bool = False):
    """Замерить этап. `add` дописывает время к уже учтённому этапу."""
    started = time.perf_counter()
    try:
        yield
    finally:
        spent = time.perf_counter() - started
        timings[stage] = timings.get(stage, 0.0) + spent if add else spent
