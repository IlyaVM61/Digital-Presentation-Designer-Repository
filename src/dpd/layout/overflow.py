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

Слой детерминированный: моделей не вызывает.
"""

from __future__ import annotations

from dpd.models import Canvas, Compensation, Slot, TextRun

AVERAGE_GLYPH_WIDTH = 0.5
"""Ширина знака в долях кегля. Для пропорциональных гарнитур — около половины."""

LINE_HEIGHT = 1.25
"""Межстрочное расстояние в долях кегля."""

POINTS_PER_INCH = 72
EMU_PER_INCH = 914400

ELLIPSIS = "…"


def fits(paragraphs: list[str], slot: Slot, canvas: Canvas, size_pt: float | None = None) -> bool:
    """Помещается ли текст в слот при данном кегле."""
    size = size_pt or (slot.text_style.size_pt if slot.text_style else None) or 14.0
    return _lines_needed(paragraphs, slot, canvas, size) <= _lines_available(slot, canvas, size)


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
    """Сократить текст до вмещающегося, сохранив начало каждого абзаца."""
    available = _lines_available(slot, canvas, size)
    per_line = max(_chars_per_line(slot, canvas, size), 1)

    kept: list[str] = []
    used = 0
    for paragraph in paragraphs:
        if used >= available:
            break
        room = (available - used) * per_line
        if len(paragraph) <= room:
            kept.append(paragraph)
            used += max(1, -(-len(paragraph) // per_line))
        else:
            cut = max(int(room) - len(ELLIPSIS), 1)
            kept.append(paragraph[:cut].rstrip() + ELLIPSIS)
            used = available
    return kept or [ELLIPSIS]


def _chars_per_line(slot: Slot, canvas: Canvas, size: float) -> int:
    width_pt = slot.bounds.w * canvas.width_emu / EMU_PER_INCH * POINTS_PER_INCH
    return int(width_pt / (size * AVERAGE_GLYPH_WIDTH))


def _lines_available(slot: Slot, canvas: Canvas, size: float) -> int:
    height_pt = slot.bounds.h * canvas.height_emu / EMU_PER_INCH * POINTS_PER_INCH
    return max(int(height_pt / (size * LINE_HEIGHT)), 0)


def _lines_needed(paragraphs: list[str], slot: Slot, canvas: Canvas, size: float) -> int:
    per_line = max(_chars_per_line(slot, canvas, size), 1)
    return sum(max(1, -(-len(paragraph) // per_line)) for paragraph in paragraphs)
