"""Обработка переполнения слота с фиксацией компенсаций.

**Любое отклонение от замысла фиксируется.** Молчаливое уменьшение кегля —
то самое поведение, за которое к презентациям возникают претензии: слайд
выглядит нормально, а почему шрифт мельче соседнего, объяснить никто не
может. Требование FR-18 прямое, и проверка QR-5 о выходе за типографическую
шкалу опирается на эту же запись.

Порядок компенсаций отражает возрастающую цену: сначала кегль в пределах
шкалы шаблона — оформление меняется, содержание цело; затем обрезка —
теряется содержание, и это крайняя мера.

Вместимость оценивается по метрике, а не по рендеру: рендер занял бы
секунды на слайд и сделал бы вёрстку недетерминированной. Оценка
приблизительна и намеренно осторожна — лучше уменьшить кегль лишний раз,
чем выпустить текст за край.

**Место считается за вычетом того, что его отнимает** (T-60): полей рамки,
отступа абзаца под маркер, межстрочного интервала и отбивок шаблона — они
приходят из `TextFrame` слота. Строка переносится по словам, а не делится
по числу знаков. Без этого в колонке шириной 0,21 холста четвёртый пункт
уходил за нижний край, а метрика считала, что всё помещается.

Слой детерминированный: моделей не вызывает.
"""

from __future__ import annotations

from math import ceil

from dpd.models import Bounds, Canvas, Compensation, Slot, TextFrame, TextRun

LETTER_WIDTH = 0.55
"""Ширина буквы в долях кегля.

Замер на тексте контент-пакета (T-60): буква кириллицы — 0,536 кегля в
Arial, 0,528 в Segoe UI, 0,533 в Tahoma, у Verdana — 0,604. Прежние 0,5 на
любой знак недооценивали длинное слово, и в узкой колонке оно рвалось
посередине: «масштабировани» / «е»."""

SPACE_WIDTH = 0.28
"""Ширина пробела в долях кегля: 0,274–0,313 в тех же гарнитурах."""

LINE_HEIGHT = 1.25
"""Межстрочное расстояние в долях кегля."""

POINTS_PER_INCH = 72
EMU_PER_INCH = 914400

ELLIPSIS = "…"


def text_fits(
    paragraphs: list[str],
    bounds: Bounds,
    canvas: Canvas,
    size_pt: float,
    frame: TextFrame | None = None,
) -> bool:
    """Помещается ли текст в прямоугольник при данном кегле.

    Вынесено из `fits` ради аудита: проверка `layout.text_overflow` обязана
    оценивать вместимость тем же расчётом, что и вёрстка. Две метрики
    разошлись бы, и аудит объявлял бы дефектом то, что вёрстка считает нормой.
    `frame` — то, что отнимает у текста место; без него считается вся рамка.
    """
    return text_height(paragraphs, bounds, canvas, size_pt, frame) <= room_height(bounds, canvas, frame)


def fits(paragraphs: list[str], slot: Slot, canvas: Canvas, size_pt: float | None = None) -> bool:
    """Помещается ли текст в слот при данном кегле."""
    size = size_pt or (slot.text_style.size_pt if slot.text_style else None) or 14.0
    return text_fits(paragraphs, slot.bounds, canvas, size, slot.frame)


def text_height(
    paragraphs: list[str],
    bounds: Bounds,
    canvas: Canvas,
    size_pt: float,
    frame: TextFrame | None = None,
) -> float:
    """Сколько пунктов высоты займёт текст: строки и отбивки абзацев."""
    frame = frame or TextFrame()
    line = _line_width(bounds, canvas, size_pt, frame)
    lines = sum(wrapped_lines(paragraph, line) for paragraph in paragraphs)
    spacing = len(paragraphs) * (frame.space_before_pt + frame.space_after_pt)
    return lines * _line_pitch(size_pt, frame) + spacing


def room_height(bounds: Bounds, canvas: Canvas, frame: TextFrame | None = None) -> float:
    """Сколько пунктов высоты есть у текста внутри рамки."""
    frame = frame or TextFrame()
    share = max(bounds.h - frame.inset_top - frame.inset_bottom, 0.0)
    return share * canvas.height_emu / EMU_PER_INCH * POINTS_PER_INCH


def wrapped_lines(paragraph: str, line_width: float) -> int:
    """Число строк абзаца при переносе по словам; `line_width` — ширина строки в кеглях.

    Слово, не влезшее в остаток строки, начинает следующую, и остаток
    пропадает. В широкой колонке это незаметно, в узкой — лишняя строка на
    каждые две-три: «аналитика (140» и «пар)» на рендере T-58 — две строки
    там, где деление числа знаков на ширину давало одну. Слово длиннее
    строки рвётся, как его рвёт программа просмотра.
    """
    words = paragraph.split()
    if not words:
        return 1
    if line_width <= 0:
        return sum(len(word) for word in words)
    lines, used = 1, 0.0
    for word in words:
        width = len(word) * LETTER_WIDTH
        need = width if not used else used + SPACE_WIDTH + width
        if need <= line_width:
            used = need
            continue
        if used:
            lines += 1
        if width <= line_width:
            used = width
            continue
        pieces = ceil(width / line_width)
        lines += pieces - 1
        used = width - (pieces - 1) * line_width
    return lines


def words_fit(
    paragraphs: list[str],
    bounds: Bounds,
    canvas: Canvas,
    size_pt: float,
    frame: TextFrame | None = None,
) -> bool:
    """Входит ли самое длинное слово в строку целиком.

    По высоте текст может помещаться, а слово — рваться посередине: колонка
    шириной 0,21 холста, «масштабировани» на одной строке и «е» на другой.
    Вёрстка такой кегль не берёт; проверка переполнения его не судит — текст
    в рамке, просто набран плохо.
    """
    line = _line_width(bounds, canvas, size_pt, frame or TextFrame())
    longest = max((len(word) for paragraph in paragraphs for word in paragraph.split()), default=0)
    return longest * LETTER_WIDTH <= line


def plan_compensations(
    paragraphs: list[str],
    slot: Slot,
    canvas: Canvas,
    scale: list[float],
) -> tuple[list[Compensation], list[TextRun]]:
    """Подобрать компенсации так, чтобы текст поместился.

    Возвращает применённые компенсации и готовые прогоны. Пустой список
    означает, что замысел не пострадал.
    """
    style = slot.text_style
    size = (style.size_pt if style else None) or 14.0
    font = style.font if style else None
    colour = style.color if style else None

    def runs_at(items: list[str], point_size: float) -> list[TextRun]:
        return [TextRun(text=item, font=font, size_pt=point_size, color=colour) for item in items]

    def suits(point_size: float) -> bool:
        return fits(paragraphs, slot, canvas, point_size) and words_fit(
            paragraphs, slot.bounds, canvas, point_size, slot.frame
        )

    if suits(size):
        return [], runs_at(paragraphs, size)

    compensations: list[Compensation] = []
    smaller = sorted((value for value in scale if value < size), reverse=True)
    reason = (
        "текст не помещался в слот при исходном кегле"
        if not fits(paragraphs, slot, canvas, size)
        else "самое длинное слово не помещалось в строку при исходном кегле"
    )

    for candidate in smaller:
        if suits(candidate):
            compensations.append(
                Compensation(
                    kind="fontScale",
                    slot_id=slot.id,
                    from_value=size,
                    to_value=candidate,
                    reason=reason,
                )
            )
            return compensations, runs_at(paragraphs, candidate)

    if fits(paragraphs, slot, canvas, size):
        # По высоте всё помещается, только слово длиннее строки при любом
        # кегле шкалы: резать содержание из-за переноса слова нельзя.
        return [], runs_at(paragraphs, size)

    # Шкала исчерпана: берём наименьший допустимый кегль и режем содержание.
    final_size = smaller[-1] if smaller else size
    if final_size != size:
        compensations.append(
            Compensation(
                kind="fontScale",
                slot_id=slot.id,
                from_value=size,
                to_value=final_size,
                reason="применён наименьший кегль шкалы шаблона",
            )
        )

    trimmed = _truncate(paragraphs, slot, canvas, final_size)
    compensations.append(
        Compensation(
            kind="truncate",
            slot_id=slot.id,
            from_value=float(sum(len(item) for item in paragraphs)),
            to_value=float(sum(len(item) for item in trimmed)),
            reason="кегли шкалы исчерпаны, содержимое сокращено",
        )
    )
    return compensations, runs_at(trimmed, final_size)


def _truncate(paragraphs: list[str], slot: Slot, canvas: Canvas, size: float) -> list[str]:
    """Сократить текст до вмещающегося: целые абзацы, пока помещаются, затем
    начало следующего с многоточием.

    Вместимость проверяется той же метрикой `text_fits`, а не отдельным
    подсчётом знаков: иначе сокращённый текст мог бы снова не поместиться.
    """
    kept: list[str] = []
    for paragraph in paragraphs:
        if text_fits([*kept, paragraph], slot.bounds, canvas, size, slot.frame):
            kept.append(paragraph)
            continue
        cut = _longest_prefix(kept, paragraph, slot, canvas, size)
        if cut:
            kept.append(cut)
        break
    return kept or [ELLIPSIS]


def _longest_prefix(kept: list[str], paragraph: str, slot: Slot, canvas: Canvas, size: float) -> str | None:
    """Самое длинное начало абзаца с многоточием, которое ещё помещается."""
    low, high, best = 1, len(paragraph) - 1, None
    while low <= high:
        middle = (low + high) // 2
        candidate = paragraph[:middle].rstrip() + ELLIPSIS
        if text_fits([*kept, candidate], slot.bounds, canvas, size, slot.frame):
            best, low = candidate, middle + 1
        else:
            high = middle - 1
    return best


def _line_width(bounds: Bounds, canvas: Canvas, size: float, frame: TextFrame) -> float:
    """Ширина строки в кеглях: за вычетом полей рамки и отступа абзаца."""
    share = max(bounds.w - frame.inset_left - frame.inset_right - frame.indent, 0.0)
    width_pt = share * canvas.width_emu / EMU_PER_INCH * POINTS_PER_INCH
    return width_pt / size


def _line_pitch(size: float, frame: TextFrame) -> float:
    """Шаг строки: точный интервал шаблона либо его доля от одинарного."""
    if frame.line_spacing_pt:
        return frame.line_spacing_pt
    return size * LINE_HEIGHT * frame.line_spacing
