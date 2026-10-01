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

from dpd.models import Bounds, Canvas, Compensation, Slot, TextFrame, TextRun

AVERAGE_GLYPH_WIDTH = 0.5
"""Ширина знака в долях кегля. Для пропорциональных гарнитур — около половины."""

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
    per_line = max(_chars_per_line(bounds, canvas, size_pt, frame), 1)
    lines = sum(wrapped_lines(paragraph, per_line) for paragraph in paragraphs)
    spacing = len(paragraphs) * (frame.space_before_pt + frame.space_after_pt)
    return lines * _line_pitch(size_pt, frame) + spacing


def room_height(bounds: Bounds, canvas: Canvas, frame: TextFrame | None = None) -> float:
    """Сколько пунктов высоты есть у текста внутри рамки."""
    frame = frame or TextFrame()
    share = max(bounds.h - frame.inset_top - frame.inset_bottom, 0.0)
    return share * canvas.height_emu / EMU_PER_INCH * POINTS_PER_INCH


def wrapped_lines(paragraph: str, per_line: int) -> int:
    """Число строк абзаца при переносе по словам.

    Слово, не влезшее в остаток строки, начинает следующую, и остаток
    пропадает. В широкой колонке это незаметно, в узкой — лишняя строка на
    каждые две-три: «аналитика (140» и «пар)» на рендере T-58 — две строки
    там, где деление числа знаков на ширину давало одну. Слово длиннее
    строки рвётся, как его рвёт программа просмотра.
    """
    words = paragraph.split()
    if not words:
        return 1
    lines, used = 1, 0
    for word in words:
        need = len(word) if used == 0 else used + 1 + len(word)
        if need <= per_line:
            used = need
            continue
        if used:
            lines += 1
        full, rest = divmod(len(word), per_line)
        lines += full - 1 if rest == 0 else full
        used = per_line if rest == 0 else rest
    return lines


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

    if fits(paragraphs, slot, canvas, size):
        return [], runs_at(paragraphs, size)

    compensations: list[Compensation] = []
    smaller = sorted((value for value in scale if value < size), reverse=True)

    for candidate in smaller:
        if fits(paragraphs, slot, canvas, candidate):
            compensations.append(
                Compensation(
                    kind="fontScale",
                    slot_id=slot.id,
                    from_value=size,
                    to_value=candidate,
                    reason="текст не помещался в слот при исходном кегле",
                )
            )
            return compensations, runs_at(paragraphs, candidate)

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


def _chars_per_line(bounds: Bounds, canvas: Canvas, size: float, frame: TextFrame) -> int:
    share = max(bounds.w - frame.inset_left - frame.inset_right - frame.indent, 0.0)
    width_pt = share * canvas.width_emu / EMU_PER_INCH * POINTS_PER_INCH
    return int(width_pt / (size * AVERAGE_GLYPH_WIDTH))


def _line_pitch(size: float, frame: TextFrame) -> float:
    """Шаг строки: точный интервал шаблона либо его доля от одинарного."""
    if frame.line_spacing_pt:
        return frame.line_spacing_pt
    return size * LINE_HEIGHT * frame.line_spacing
