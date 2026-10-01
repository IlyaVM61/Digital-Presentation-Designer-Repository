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

**Выгружаются все три варианта** (T-39). Пользователь выбирает вариант,
посмотрев на все три рядом, — значит, к концу прогона готовы файлы каждого,
и выбор не стоит второго прогона. Три колоды конвертируются одним запуском
LibreOffice: 16,6 с против 8,8 с на одну и около 26 с тремя запусками.

**Находки с потерями решает пользователь** (T-40, решение D5). Прогон
подбирает для них применимые способы, а `revise` применяет выбранные,
выполняет аудит заново и перевыгружает файлы одного варианта — второй прогон
всех трёх ради одного слайда не нужен. Исправление, которого нет в скачанном
файле, пользователю ничего не дало бы.

**Промпты читаются на старте прогона** (T-48), и их версии попадают в отчёт
каждого варианта: какими промптами получен результат, иначе не узнать (ТЗ,
п. 2.4). Битый файл промпта останавливает прогон до разбора шаблона, а не
посреди генерации.

**Аудит текста приходит готовым** (T-51). Он выполняется один раз на колоду
до вёрстки — там же, где генерация, — а прогон дописывает его находки в отчёт
каждого варианта: пользователь смотрит на вариант, и замечания к тексту
должны быть там же, где замечания к вёрстке.
"""

from __future__ import annotations

import time
import uuid
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path

from dpd.audit import AuditContext, discover
from dpd.audit.checks import check_variant_distinction
from dpd.audit.fixer import repair
from dpd.audit.remedies import Choice, Remedy, apply_remedies, options
from dpd.audit.textual import attach
from dpd.export import export_html, export_pdf, export_pptx
from dpd.layout import compose_variants
from dpd.layout.variants import load_profiles
from dpd.llm import load_prompts
from dpd.models import (
    AuditReport,
    Finding,
    PresentationStructure,
    RenderedPresentation,
    TemplateSchema,
)
from dpd.models.audit import SEVERITY_ORDER
from dpd.parsing import parse_template
from dpd.render import convert_to_pdfs, render_pdf_pages

STAGES: tuple[str, ...] = ("parse", "layout", "audit", "render", "export")
"""Порядок этапов. Рендер идёт перед экспортом: его конвертация нужна и
превью, и PDF, и делать её дважды нельзя."""

STAGE_TITLES = {
    "parse": "Читаем шаблон",
    "layout": "Собираем три варианта",
    "audit": "Проверяем и исправляем",
    "render": "Готовим превью",
    "export": "Сохраняем файлы",
}
"""Названия для интерфейса: пользователь ждёт десятки секунд и должен видеть,
что именно происходит. Словами пользователя, а не вёрстки (T-55)."""

Progress = Callable[[str, int, int], None]
"""Событие прогресса: этап, сколько сделано, сколько всего."""


@dataclass(frozen=True)
class Revision:
    """Итог исправления по выбору пользователя: что сделано и что стало.

    `before` и `after` — открытые находки варианта до исправления и после
    повторного аудита, по критичности. Одного числа мало: обмен
    предупреждения на рекомендацию — улучшение по правилу D5, а общий счёт
    при этом не меняется, и «было 2, стало 2» выглядело бы как ничего не
    сделанное. `rejected` — выбранное, но не применённое: на
    момент применения оно ухудшило бы колоду. `skipped` — выбранное, чья
    находка исчезла раньше, чем до неё дошла очередь.
    """

    variant: str = ""
    applied: list[Choice] = field(default_factory=list)
    rejected: list[Choice] = field(default_factory=list)
    skipped: list[Choice] = field(default_factory=list)
    before: dict[str, int] = field(default_factory=dict)
    after: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class RunResult:
    """Итог прогона: всё, что интерфейсу нужно показать и отдать.

    `exports` и `previews` — файлы выбранного по умолчанию варианта;
    `variant_exports` и `variant_previews` — всех трёх, по идентификатору.
    """

    run_id: str
    template: TemplateSchema
    variants: list[RenderedPresentation]
    chosen: RenderedPresentation
    reports: dict[str, AuditReport]
    exports: dict[str, Path] = field(default_factory=dict)
    previews: list[Path] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    seconds: float = 0.0
    variant_names: dict[str, str] = field(default_factory=dict)
    variant_exports: dict[str, dict[str, Path]] = field(default_factory=dict)
    variant_previews: dict[str, list[Path]] = field(default_factory=dict)
    distinction: list[Finding] = field(default_factory=list)
    """Находки `variants.low_distinction`: три совпавшие колоды нельзя молча
    выдать за три варианта (стратегия вариантов, «Деградация»)."""
    remedies: dict[str, dict[int, list[Remedy]]] = field(default_factory=dict)
    """Вариант → номер находки в его отчёте → применимые способы (T-40)."""
    revision: Revision | None = None
    """Итог последнего исправления по выбору пользователя."""
    revisions: int = 0
    """Сколько раз колоду исправляли по выбору: по нему интерфейс отличает
    свежие находки от тех, что уже видел."""
    template_path: Path | None = None
    structure: PresentationStructure | None = None
    out_dir: Path | None = None
    formats: tuple[str, ...] = ()
    """Входы прогона: без них `revise` нечем перевыгрузить исправленный
    вариант."""
    text_audit: AuditReport | None = None
    """Аудит текста колоды (T-51): `revise` дописывает его в свежий отчёт."""


def run_pipeline(
    template_path: str | Path,
    structure: PresentationStructure,
    out_dir: str | Path,
    *,
    formats: Sequence[str] = ("pptx",),
    variant: str | None = None,
    previews: bool = False,
    progress: Progress | None = None,
    text_audit: AuditReport | None = None,
) -> RunResult:
    """Пройти путь от файла шаблона до выгруженной колоды.

    `variant` выбирает, какой из трёх вариантов считать выбранным; по
    умолчанию первый. Аудит и выгрузка при этом выполняются для всех трёх:
    пользователь сравнивает их рядом и выбирает после прогона.

    `text_audit` — отчёт аудита текста этой же структуры (`audit_text`), его
    находки входят в отчёт каждого варианта.
    """
    template_path, out_dir = Path(template_path), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    discover()
    prompt_versions = load_prompts().versions()

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
        profiles = load_profiles()
        variants = compose_variants(structure, schema, profiles)
        report_progress("layout", 1, 1)

    chosen = _chosen(variants, variant)

    reports: dict[str, AuditReport] = {}
    remedies: dict[str, dict[int, list[Remedy]]] = {}
    with _timed(timings, "audit"):
        for index, deck in enumerate(variants):
            report_progress("audit", index, len(variants))
            context = AuditContext(deck=deck, template=schema, structure=structure)
            result = repair(context)
            variants[index] = result.deck
            # Находки текста дописываются до подбора способов исправления:
            # способы адресуются номером находки в отчёте.
            reports[deck.variant] = attach(result.report, text_audit, result.deck)
            remedies[deck.variant] = options(replace(context, deck=result.deck), reports[deck.variant])
        # Различимость меряется после починки: сравниваются колоды, которые
        # пользователь увидит, а не черновики.
        distinction = check_variant_distinction(variants, schema)
        report_progress("audit", len(variants), len(variants))
    chosen = next(deck for deck in variants if deck.variant == chosen.variant)

    exports, images = _export(
        variants, schema, template_path, out_dir, formats, previews, timings, report_progress
    )

    # Выгрузка `.pptx` начинается раньше рендера, и по ходу прогона словарь
    # заполняется не в том порядке, в каком его читает человек. Неизвестный
    # этап роняет сортировку: без места в `STAGES` у него нет ни названия,
    # ни доли в полосе прогресса.
    timings = dict(sorted(timings.items(), key=lambda item: STAGES.index(item[0])))
    # Разбивка времени принадлежит отчёту (NFR-1а): он остаётся от прогона,
    # когда интерфейс закрыт. Этапы общие для трёх вариантов — разбор один,
    # конвертация одна, — и делить время между вариантами значило бы его
    # выдумывать; каждый отчёт несёт разбивку прогона целиком.
    reports = {
        key: report.model_copy(update={"timings": dict(timings), "prompt_versions": dict(prompt_versions)})
        for key, report in reports.items()
    }

    return RunResult(
        run_id=run_id,
        template=schema,
        variants=variants,
        chosen=chosen,
        reports=reports,
        exports=exports[chosen.variant],
        previews=images.get(chosen.variant, []),
        timings=timings,
        seconds=time.perf_counter() - started,
        variant_names={profile.id: profile.name for profile in profiles},
        variant_exports=exports,
        variant_previews=images,
        distinction=distinction,
        remedies=remedies,
        template_path=template_path,
        structure=structure,
        out_dir=out_dir,
        formats=tuple(formats),
        text_audit=text_audit,
    )


def revise(result: RunResult, variant: str, choices: Mapping[int, str]) -> RunResult:
    """Исправить вариант по выбору пользователя, проверить заново, перевыгрузить.

    `choices` — номер находки в отчёте варианта → способ из `result.remedies`.
    Пустой выбор ничего не меняет и ничего не пересобирает: «оставить как
    есть» — тоже решение, и стоить времени оно не должно.

    **Аудит после исправления выполняется заново** (правило D5), и заодно
    автопочинка: новый слайд может принести механическую находку, которую
    система чинит сама, как при первом прогоне. Автоисправления первого
    прогона остаются в отчёте — иначе пользователь не узнал бы о них.

    Остальные варианты не трогаются, различимость пересчитывается: она
    зависит от всех трёх.
    """
    if not choices:
        return replace(result, revision=Revision(variant=variant))
    if result.template_path is None or result.structure is None or result.out_dir is None:
        raise ValueError("прогон не сохранил своих входов — исправлять не по чему")

    deck = _chosen(result.variants, variant)
    report = result.reports[variant]
    context = AuditContext(deck=deck, template=result.template, structure=result.structure)
    outcome = apply_remedies(context, report, choices)
    repaired = repair(replace(context, deck=outcome.deck))
    earlier = [finding for finding in report.findings if finding.status == "autofixed"]
    # Разбивка остаётся разбивкой прогона: время исправления в бюджет цикла не
    # входит, между прогоном и исправлением лежит решение пользователя.
    fresh = repaired.report.model_copy(
        update={
            "findings": earlier + list(repaired.report.findings),
            "timings": report.timings,
            "prompt_versions": report.prompt_versions,
        }
    )
    fresh = attach(fresh, result.text_audit, repaired.deck)

    variants = [repaired.deck if item.variant == variant else item for item in result.variants]
    exports, images = _export(
        [repaired.deck],
        result.template,
        result.template_path,
        result.out_dir,
        result.formats,
        bool(result.variant_previews),
        {},
        lambda *_: None,
    )
    variant_exports = {**result.variant_exports, **exports}
    variant_previews = {**result.variant_previews, **images}
    chosen = next(item for item in variants if item.variant == result.chosen.variant)

    return replace(
        result,
        variants=variants,
        chosen=chosen,
        reports={**result.reports, variant: fresh},
        exports=variant_exports.get(chosen.variant, {}),
        previews=variant_previews.get(chosen.variant, []),
        variant_exports=variant_exports,
        variant_previews=variant_previews,
        distinction=check_variant_distinction(variants, result.template),
        remedies={**result.remedies, variant: options(replace(context, deck=repaired.deck), fresh)},
        revision=Revision(
            variant=variant,
            applied=outcome.applied,
            rejected=outcome.rejected,
            skipped=outcome.skipped,
            before=_open(report),
            after=_open(fresh),
        ),
        revisions=result.revisions + 1,
    )


def _open(report: AuditReport) -> dict[str, int]:
    """Открытые находки по критичности, от самой тяжёлой."""
    counts = Counter(finding.severity for finding in report.findings if finding.status != "autofixed")
    return {severity: counts[severity] for severity in SEVERITY_ORDER if counts[severity]}


def _export(
    variants: list[RenderedPresentation],
    schema: TemplateSchema,
    template_path: Path,
    out_dir: Path,
    formats: Sequence[str],
    previews: bool,
    timings: dict[str, float],
    report_progress: Progress,
) -> tuple[dict[str, dict[str, Path]], dict[str, list[Path]]]:
    """Выгрузить варианты во все форматы и, если просили, отрисовать превью.

    Возвращает файлы и изображения по идентификатору варианта.
    """
    exports: dict[str, dict[str, Path]] = {deck.variant: {} for deck in variants}
    images: dict[str, list[Path]] = {}
    total = len(formats) * len(variants)
    done = 0
    decks_pptx: dict[str, Path] = {}

    def exported(deck: RenderedPresentation, key: str, path: Path) -> None:
        nonlocal done
        exports[deck.variant][key] = path
        done += 1
        report_progress("export", done, total)

    with _timed(timings, "export"):
        report_progress("export", done, total)
        # `.pptx` нужен сам по себе и как единственный источник конвертации в
        # PDF и в превью. Если его не просили и конвертировать нечего, он не
        # собирается вовсе: снятая галочка должна что-то значить, а лишняя
        # работа — стоить времени бюджета.
        if {"pptx", "pdf"} & set(formats) or previews:
            for deck in variants:
                decks_pptx[deck.variant] = export_pptx(
                    deck, schema, template_path, out_dir / f"{_base(template_path, deck)}.pptx"
                )
                if "pptx" in formats:
                    exported(deck, "pptx", decks_pptx[deck.variant])

    # Конвертация одна на прогон: её результат нужен и превью, и PDF, а колоды
    # всех трёх вариантов идут в неё одной пачкой.
    pdf_sources: dict[str, Path] = {}
    if decks_pptx and (previews or "pdf" in formats):
        with _timed(timings, "render"):
            report_progress("render", 0, 1)
            pdfs = convert_to_pdfs(list(decks_pptx.values()), out_dir)
            pdf_sources = dict(zip(decks_pptx, pdfs, strict=True))
            if previews:
                for key, pdf in pdf_sources.items():
                    images[key] = render_pdf_pages(pdf, out_dir / "previews" / key)
            report_progress("render", 1, 1)

    with _timed(timings, "export", add=True):
        for deck in variants:
            base = _base(template_path, deck)
            if "pdf" in formats and deck.variant in decks_pptx:
                pdf = export_pdf(
                    decks_pptx[deck.variant],
                    out_dir / f"{base}.pdf",
                    source_pdf=pdf_sources.get(deck.variant),
                )
                exported(deck, "pdf", pdf)
            if "html" in formats:
                exported(deck, "html", export_html(deck, schema, out_dir / f"{base}.html"))
        report_progress("export", total, total)

    return exports, images


def _base(template_path: Path, deck: RenderedPresentation) -> str:
    """Имя файлов варианта: по нему видно и шаблон, и вариант."""
    return f"{template_path.stem}-{deck.variant}"


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
