"""Проверки плотности — раздел «Плотность» Приложения 1 ТЗ (T-30).

Пять проверок класса `file`. Четыре считают элементы — пункты, слова,
строки таблицы, ряды диаграммы — и потому просты и бесспорны. Пятая
измеряет заполнение слайда, и вот она потребовала измерений.

**Заполнение измеряется занятым местом, а не размером рамок.** На контентном
слайде VK Tech рамки слотов занимают 77% холста, а текстовый блок — верхнюю
четверть. Метрика по рамкам объявила бы такой слайд переполненным; метрика по
содержимому дала 23% — столько же, сколько видно на рендере. Решение принято
по рендеру, а не по рассуждению: данные о таких вещах молчат.

**Отсчёт идёт от места, отведённого шаблоном, а не от всего холста.** На том
же рендере нижнюю половину слайда занимает графика шаблона: слотов там нет,
содержимому там не место, и считать эту площадь незаполненной значило бы
предъявлять нашей вёрстке решения автора шаблона.

**Верхний предел считается по тексту.** Диаграмма и таблица заполняют свой
слот целиком по определению — место под них и отводилось, — и общий счёт
объявлял бы переполненным каждый слайд с визуализацией. Нижний предел считает
всё содержимое: слайд с диаграммой пустым не бывает.

**Титульные слайды и разделители исключены.** Они пусты по замыслу жанра:
замеры дают 7–20% заполнения на титулах всех трёх калибровочных шаблонов, и
находка о них была бы претензией к жанру, а не к слайду.
"""

from __future__ import annotations

from math import ceil

from dpd.audit.fixer import slide_of
from dpd.audit.registry import CheckSpec, check, param
from dpd.audit.remedies import remedy
from dpd.layout.overflow import (
    AVERAGE_GLYPH_WIDTH,
    EMU_PER_INCH,
    LINE_HEIGHT,
    POINTS_PER_INCH,
)
from dpd.models import (
    Canvas,
    Finding,
    RenderedElement,
    RenderedPresentation,
    Slide,
    TemplateSchema,
)

SPARSE_FAMILIES = frozenset({"title", "section"})
"""Семейства макетов, для которых пустота — замысел, а не дефект."""

TOO_MANY_BULLETS = CheckSpec(
    id="density.too_many_bullets",
    category="density",
    check_class="file",
    severity="warning",
    fixability="lossy",
    sublayer="4b",
    title="Больше шести буллетов на слайде",
    plain="Слишком много пунктов на слайде",
)

BULLET_TOO_LONG = CheckSpec(
    id="density.bullet_too_long",
    category="density",
    check_class="file",
    severity="warning",
    fixability="lossy",
    sublayer="4b",
    title="Буллет длиннее пятнадцати слов",
    plain="Слишком длинный пункт списка",
)

TABLE_TOO_BIG = CheckSpec(
    id="density.table_too_big",
    category="density",
    check_class="file",
    severity="warning",
    fixability="lossy",
    sublayer="4b",
    title="Таблица больше семи строк или пяти колонок",
    plain="Таблица слишком большая для слайда",
)

TOO_MANY_SERIES = CheckSpec(
    id="density.too_many_series",
    category="density",
    check_class="file",
    severity="warning",
    fixability="lossy",
    sublayer="4b",
    title="Больше пяти рядов на диаграмме",
    plain="На диаграмме слишком много рядов данных",
)

FILL_OUT_OF_RANGE = CheckSpec(
    id="density.fill_out_of_range",
    category="density",
    check_class="file",
    severity="advice",
    fixability="lossy",
    sublayer="4b",
    title="Слайд заполнен слишком слабо или слишком плотно",
)


@check(TOO_MANY_BULLETS)
def check_too_many_bullets(
    deck: RenderedPresentation,
    template: TemplateSchema,
    max_bullets: int | None = None,
) -> list[Finding]:
    """Найти слайды, на которых пунктов больше, чем удержит внимание.

    Заголовок не считается: иначе слайд с шестью пунктами давал бы находку о
    семи, и предельным числом на деле оказалось бы пять.
    """
    max_bullets = int(_threshold(max_bullets, TOO_MANY_BULLETS.id, "max_bullets"))
    titles = _title_slots(template)

    findings: list[Finding] = []
    for number, slide in enumerate(deck.slides, start=1):
        count = sum(len(element.runs) for element in _body_text(slide, titles))
        if count <= max_bullets:
            continue
        findings.append(
            TOO_MANY_BULLETS.finding(
                f"Слайд {number}: пунктов {count} — это больше {max_bullets}, "
                f"и список перестаёт читаться.",
                slide_number=number,
                evidence={"actual": count, "expected": max_bullets},
            )
        )
    return findings


@check(BULLET_TOO_LONG)
def check_bullet_too_long(
    deck: RenderedPresentation,
    template: TemplateSchema,
    max_words: int | None = None,
) -> list[Finding]:
    """Найти пункты, выросшие в абзацы.

    Заголовок снова не в счёт: длинный заголовок — отдельный дефект с другим
    правилом, и смешивать их значило бы предлагать неверное исправление.
    """
    max_words = int(_threshold(max_words, BULLET_TOO_LONG.id, "max_words"))
    titles = _title_slots(template)

    findings: list[Finding] = []
    for number, slide in enumerate(deck.slides, start=1):
        for element in _body_text(slide, titles):
            for run in element.runs:
                words = len(run.text.split())
                if words <= max_words:
                    continue
                findings.append(
                    BULLET_TOO_LONG.finding(
                        f"Слайд {number}: пункт из {words} слов — это больше {max_words}, "
                        f"и он читается как абзац, а не как пункт.",
                        slide_number=number,
                        slot_id=element.slot_id,
                        evidence={"actual": words, "expected": max_words, "text": run.text[:80]},
                    )
                )
    return findings


@check(TABLE_TOO_BIG)
def check_table_too_big(
    deck: RenderedPresentation,
    max_rows: int | None = None,
    max_columns: int | None = None,
) -> list[Finding]:
    """Найти таблицы, переросшие предел читаемости.

    Вёрстка режет таблицу до этого предела сама и фиксирует сокращение
    компенсацией `tableTrim` (T-23). Проверка нужна для колод, собранных
    иначе, и как страховка от ошибки в самой вёрстке.
    """
    max_rows = int(_threshold(max_rows, TABLE_TOO_BIG.id, "max_rows"))
    max_columns = int(_threshold(max_columns, TABLE_TOO_BIG.id, "max_columns"))

    findings: list[Finding] = []
    for number, slide in enumerate(deck.slides, start=1):
        for element in slide.elements:
            if element.table is None:
                continue
            rows = len(element.table.rows)
            columns = max([len(element.table.headers), *(len(row) for row in element.table.rows)], default=0)
            if rows <= max_rows and columns <= max_columns:
                continue
            findings.append(
                TABLE_TOO_BIG.finding(
                    f"Слайд {number}: таблица {rows} × {columns} больше предела "
                    f"{max_rows} × {max_columns}.",
                    slide_number=number,
                    slot_id=element.slot_id,
                    evidence={
                        "rows": rows,
                        "columns": columns,
                        "expected": {"rows": max_rows, "columns": max_columns},
                    },
                )
            )
    return findings


@check(TOO_MANY_SERIES)
def check_too_many_series(deck: RenderedPresentation, max_series: int | None = None) -> list[Finding]:
    """Найти диаграммы, ряды которых уже не различить.

    Предел здесь не про место, а про цвет: палитра шаблона даёт считанные
    насыщенные цвета, и шестой ряд неизбежно повторит чужой.
    """
    max_series = int(_threshold(max_series, TOO_MANY_SERIES.id, "max_series"))

    findings: list[Finding] = []
    for number, slide in enumerate(deck.slides, start=1):
        for element in slide.elements:
            if element.chart is None:
                continue
            count = len(element.chart.series)
            if count <= max_series:
                continue
            findings.append(
                TOO_MANY_SERIES.finding(
                    f"Слайд {number}: рядов на диаграмме {count} — это больше {max_series}, "
                    f"и цветов палитры на них не хватит.",
                    slide_number=number,
                    slot_id=element.slot_id,
                    evidence={"actual": count, "expected": max_series},
                )
            )
    return findings


@check(FILL_OUT_OF_RANGE)
def check_fill_out_of_range(
    deck: RenderedPresentation,
    template: TemplateSchema,
    min_fill: float | None = None,
    max_fill: float | None = None,
) -> list[Finding]:
    """Найти слайды, заполненные слишком слабо или слишком плотно.

    Заполнение — доля **отведённого шаблоном места**, занятая содержимым.
    Текст занимает столько, сколько занимают его строки; таблица и диаграмма
    занимают свою рамку целиком.

    Обе особенности метрики выведены из рендера, а не из рассуждений: рамка
    слота втрое больше текста, который в ней стоит, а половину холста может
    занимать графика шаблона, куда содержимому хода нет.

    **Верхний предел считается по тексту.** Теснота — это когда текст не
    оставил слайду воздуха. Диаграмма и таблица заполняют свой слот целиком
    по определению: место под них и отводилось, и общий счёт объявлял бы
    переполненным каждый слайд с визуализацией. Нижний предел, наоборот,
    считает всё содержимое: слайд с диаграммой пустым не бывает.
    """
    min_fill = _threshold(min_fill, FILL_OUT_OF_RANGE.id, "min_fill")
    max_fill = _threshold(max_fill, FILL_OUT_OF_RANGE.id, "max_fill")

    layouts = {layout.id: layout for layout in template.layouts}

    findings: list[Finding] = []
    for number, slide in enumerate(deck.slides, start=1):
        if _family(slide) in SPARSE_FAMILIES:
            continue

        layout = layouts.get(slide.layout_id)
        if layout is None or not layout.slots:
            continue

        available = sum(slot.bounds.w * slot.bounds.h for slot in layout.slots)
        if available <= 0:
            continue

        occupied = sum(_occupied(element, deck.canvas) for element in slide.elements)
        crowding = sum(
            _occupied(element, deck.canvas) for element in slide.elements if element.kind == "text"
        )
        fill = min(occupied / available, 1.0)
        text_fill = min(crowding / available, 1.0)

        if fill >= min_fill and text_fill <= max_fill:
            continue

        sparse = fill < min_fill
        actual, side, limit = (fill, "менее", min_fill) if sparse else (text_fill, "более", max_fill)
        findings.append(
            FILL_OUT_OF_RANGE.finding(
                f"Слайд {number} заполнен на {actual:.0%} отведённого шаблоном места — "
                f"это {side} {limit:.0%}.",
                slide_number=number,
                evidence={
                    "actual": round(actual, 3),
                    "expected": [min_fill, max_fill],
                    "measured": "всё содержимое" if sparse else "текст",
                },
            )
        )
    return findings


# --- Исправления с потерями (T-40) ------------------------------------------


@remedy(
    TOO_MANY_BULLETS,
    FILL_OUT_OF_RANGE,
    id="split",
    title="Перенести часть пунктов на новый слайд",
    consequence="Слайдов станет больше, заголовок повторится на новом слайде",
)
def split_points(deck: RenderedPresentation, finding: Finding, template: TemplateSchema | None) -> bool:
    """Разделить список поровну между слайдом и его продолжением.

    Частей столько, чтобы в каждой уложиться в норму проверки, если она
    известна, иначе две. Продолжение собирается на том же макете и несёт
    только заголовок и перенесённые пункты: таблица или диаграмма, скопированная
    на второй слайд, была бы дублем, а не продолжением.

    Текст не меняется ни в одном пункте, порядок сохраняется. Ничего не
    выдумывается: продолжение повторяет заголовок, а не получает новый.
    """
    slide = slide_of(deck, finding)
    if slide is None or template is None:
        return False
    titles = _title_slots(template)
    body = _body_text(slide, titles)
    if not body:
        return False
    main = max(body, key=lambda element: len(element.runs))
    limit = finding.evidence.get("expected")
    parts = ceil(len(main.runs) / limit) if isinstance(limit, int) and limit > 0 else 2
    parts = min(max(parts, 2), len(main.runs))
    if parts < 2:
        return False

    size, extra = divmod(len(main.runs), parts)
    chunks, start = [], 0
    for part in range(parts):
        end = start + size + (1 if part < extra else 0)
        chunks.append(main.runs[start:end])
        start = end

    heading = [element for element in slide.elements if (slide.layout_id, element.slot_id) in titles]
    continuation = [
        slide.model_copy(
            deep=True,
            update={
                "id": f"{slide.id}-{number}",
                "elements": [
                    *(element.model_copy(deep=True) for element in heading),
                    main.model_copy(deep=True, update={"runs": chunk}),
                ],
                "applied_compensations": [],
            },
        )
        for number, chunk in enumerate(chunks[1:], start=2)
    ]
    main.runs = chunks[0]
    position = deck.slides.index(slide) + 1
    deck.slides[position:position] = continuation
    return True


# --- Общее для проверок ---------------------------------------------------


def _threshold(value: float | None, check_id: str, name: str) -> float:
    """Порог из прогона, а при прямом вызове — из конфигурации."""
    return float(value) if value is not None else float(param(check_id, name))


def _title_slots(template: TemplateSchema) -> set[tuple[str, str]]:
    """Пары «макет, слот» для заголовочных слотов шаблона."""
    return {
        (layout.id, slot.id)
        for layout in template.layouts
        for slot in layout.slots
        if slot.kind == "title"
    }


def _body_text(slide: Slide, titles: set[tuple[str, str]]) -> list[RenderedElement]:
    """Текстовые элементы слайда, кроме заголовочных."""
    return [
        element
        for element in slide.elements
        if element.kind == "text" and element.runs and (slide.layout_id, element.slot_id) not in titles
    ]


def _family(slide: Slide) -> str:
    return slide.layout_decision.chosen_family if slide.layout_decision else ""


def _occupied(element: RenderedElement, canvas: Canvas) -> float:
    """Сколько места на холсте содержимое элемента занимает на самом деле.

    Таблица и диаграмма заполняют свою рамку целиком — их и видно во всю
    рамку. Текст занимает столько строк, сколько ему нужно, и пустая часть
    рамки остаётся пустой: именно это видно на рендере.
    """
    frame = element.bounds.w * element.bounds.h
    if element.kind != "text" or not element.runs:
        return frame

    size = max((run.size_pt for run in element.runs if run.size_pt), default=14.0)
    width_pt = element.bounds.w * canvas.width_emu / EMU_PER_INCH * POINTS_PER_INCH
    height_pt = element.bounds.h * canvas.height_emu / EMU_PER_INCH * POINTS_PER_INCH

    per_line = max(int(width_pt / (size * AVERAGE_GLYPH_WIDTH)), 1)
    available = max(int(height_pt / (size * LINE_HEIGHT)), 1)
    needed = sum(max(1, -(-len(run.text) // per_line)) for run in element.runs)

    return frame * min(needed / available, 1.0)
