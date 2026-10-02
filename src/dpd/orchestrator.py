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

**Аудит визуала — после выбора варианта** (T-52, ADR-0002, п. 4). VLM
смотрит изображение каждого слайда, и смотреть все три варианта значило бы
втрое больше запросов при том, что выгружает пользователь один. Прогон
модель не зовёт: до просмотра отчёт каждого варианта называет проверки
визуала пропущенными, `look` смотрит выбранный и дописывает находки.
Исправление по выбору меняет слайды — замечания модели о прежних
изображениях после него сбрасываются, проверки снова названы пропущенными.

**Колоду по контент-пакету пишет `draft`** (T-58): структура и содержание
слайдов от модели, затем аудит текста — до вёрстки, как в батче. Шаг живёт
здесь, а не в интерфейсе, по той же причине, что и прогон: страница его
только вызывает. Время обоих шагов идёт в отчёт аудита текста, а прогон
ставит его первым в разбивку каждого варианта: бюджет D4 считается по
полному циклу, и генерация в нём самая долгая часть.

**Загруженный шаблон кладёт в файл `keep_template`** (T-63): разбору и
экспорту нужен путь, а колода собирается на самом файле шаблона. Копия одна
на шаблон и лежит там, куда укажет вызывающий, — у страницы это каталог
результатов, а не временный каталог системы на тесном диске C.
"""

from __future__ import annotations

import hashlib
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
from dpd.audit.textual import attach, audit_text
from dpd.audit.visual import audit_visual
from dpd.export import export_html, export_pdf, export_pptx
from dpd.generation import ContentPack, generate_presentation
from dpd.layout import compose_variants
from dpd.layout.variants import load_profiles
from dpd.llm import ModelClient, ModelError, PromptSet, load_prompts
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

DRAFT_STAGES: tuple[str, ...] = ("generate", "review")
"""Шаги до прогона, когда колоду пишет модель (T-58). Их нет в `STAGES`:
прогон по плану, написанному человеком, их не проходит."""

DRAFT_TITLES = {
    "generate": "Пишем текст слайдов",
    "review": "Проверяем текст",
}

Progress = Callable[[str, int, int], None]
"""Событие прогресса: этап, сколько сделано, сколько всего."""


@dataclass(frozen=True)
class Draft:
    """Колода от модели и аудит её текста; время шагов — в `text_audit.timings`.

    `review_error` — почему смысловая проверка текста не выполнена: колода от
    этого не останавливается, но молчать о пропуске нельзя.
    """

    structure: PresentationStructure
    text_audit: AuditReport
    review_error: str | None = None


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


def keep_template(data: bytes, name: str, folder: str | Path) -> Path:
    """Положить загруженный шаблон в файл, с которого его возьмёт прогон (T-63).

    Ключ копии — хеш содержимого, а не встроенный `hash()`: тот солится заново
    в каждом процессе, и прежде каждый перезапуск страницы оставлял новую копию
    того же шаблона — 99 копий и 1,3 ГБ на системном диске за два дня (T26).
    Повторная загрузка того же файла берёт готовую копию. Имя файла остаётся
    именем пользователя: по нему названы готовые колоды. Запись идёт через
    временный файл — прерванная не оставит под этим именем обрезанный шаблон,
    который потом считался бы готовым.
    """
    target = Path(folder) / hashlib.sha256(data).hexdigest()[:16] / Path(name).name
    if target.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f".{uuid.uuid4().hex}.part")
    partial.write_bytes(data)
    try:
        partial.replace(target)
    except OSError:
        # Соседний сеанс страницы успел положить ту же копию и держит её
        # открытой: содержимое то же, берётся его файл.
        partial.unlink(missing_ok=True)
        if not target.is_file():
            raise
    return target


def draft(
    pack: ContentPack,
    client: ModelClient,
    prompts: PromptSet,
    *,
    progress: Progress | None = None,
) -> Draft:
    """Колода по контент-пакету и аудит её текста — до вёрстки (T-58).

    Сбой генерации поднимает `ModelError`: частичной колоды дальше не уходит
    (EF-8). Сбой модели на проверке текста колоду не останавливает — как в
    батче, смысловые проверки названы пропущенными, а причина возвращается.
    """
    report_progress = progress or (lambda *_: None)
    timings: dict[str, float] = {}
    review_error = None

    with _timed(timings, "generate"):
        report_progress("generate", 0, 1)
        structure = generate_presentation(pack, client, prompts)
        report_progress("generate", 1, 1)

    with _timed(timings, "review"):
        report_progress("review", 0, 1)
        try:
            text_audit = audit_text(structure, pack, client, prompts)
        except ModelError as error:
            review_error = str(error)
            text_audit = audit_text(structure, pack)
        report_progress("review", 1, 1)

    return Draft(structure, text_audit.model_copy(update={"timings": timings}), review_error)


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
            reports[deck.variant] = _unseen(attach(result.report, text_audit, result.deck), result.deck)
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
    # Колоду, написанную моделью, прогон получает готовой, но её время —
    # часть цикла (D4): шаги черновика встают первыми, как шли.
    timings = {**(text_audit.timings if text_audit else {}), **timings}
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
    fresh = _unseen(attach(fresh, result.text_audit, repaired.deck), repaired.deck)

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


def look(result: RunResult, variant: str, client: ModelClient, prompts: PromptSet) -> RunResult:
    """Аудит визуала варианта через VLM (T-52): запрос на слайд, все вопросы разом.

    Изображения берутся из превью прогона, а если их не рисовали — вариант
    отрисовывается здесь: модели нужен рендер именно этой колоды. Находки
    дописываются в конец отчёта варианта и номеров прежних не сдвигают —
    по ним адресованы способы исправления. Время попадает в разбивку отчёта
    этапом `visual`: он входит в бюджет цикла (`models-strategy.md`), но
    идёт после выбора, и в разбивке прогона его нет.

    `ModelError` поднимается как есть: отчёт варианта при этом не меняется,
    и проверки визуала в нём остаются названными пропущенными.
    """
    deck = _chosen(result.variants, variant)
    started = time.perf_counter()

    previews = dict(result.variant_previews)
    images = previews.get(variant, [])
    if len(images) != len(deck.slides):
        if result.template_path is None or result.out_dir is None:
            raise ValueError("прогон не сохранил своих входов — отрисовать вариант не по чему")
        _, rendered = _export(
            [deck], result.template, result.template_path, result.out_dir, (), True, {}, lambda *_: None
        )
        images = previews[variant] = rendered[variant]

    visual = audit_visual(deck, images, client, prompts, run_id=result.run_id)
    report = attach(result.reports[variant], visual, deck)
    report = report.model_copy(update={"timings": {**report.timings, "visual": time.perf_counter() - started}})

    return replace(
        result,
        reports={**result.reports, variant: report},
        variant_previews=previews,
        previews=previews.get(result.chosen.variant, []),
    )


def _unseen(report: AuditReport, deck: RenderedPresentation) -> AuditReport:
    """Отчёт варианта, на который модель не смотрела: проверки визуала названы пропущенными."""
    return attach(report, audit_visual(deck), deck)


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
