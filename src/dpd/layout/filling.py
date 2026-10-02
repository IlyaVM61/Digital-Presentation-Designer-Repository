"""Тело в рамке, которую создаёт вёрстка: маркер, кегль, высота (T-69).

Прогон T-66 на корпоративном шаблоне дал три строки мелким кеглем у
верхнего края слайда, заполненного на десятую часть, и без маркеров, хотя
шаблон размечает списки маркерами. У родного плейсхолдера всё это задаёт
шаблон; у рамки, которую создаёт вёрстка, — никто, и здесь это задаётся
правилами:

1. Список из двух пунктов и больше получает маркер шаблона (`BulletToken`).
2. Кегль, который шаблон рамке не задавал, растёт по шкале шаблона, пока
   текст свободно помещается, но остаётся заметно мельче заголовка.
3. Если и тогда текст занимает малую часть высоты, абзацы разводятся
   отбивками, а оставшийся блок встаёт по середине места.

Слой детерминированный: моделей не вызывает.
"""

from __future__ import annotations

from typing import Literal

from dpd.layout.overflow import room_height, text_height, words_fit
from dpd.layout.styling import WEAK_LEVELS
from dpd.models import Bounds, BulletToken, Canvas, Slot, TextFrame

TITLE_RATIO = 1.4
"""Во сколько раз заголовок крупнее тела, самое малое. Без потолка тело на
пустом слайде дорастало бы до кегля заголовка и спорило бы с ним."""

GROW_FILL = 0.7
"""Доля высоты места, которую тело может занять, вырастая: запас на
расхождение оценки вместимости с рендером."""

SPREAD_FILL = 0.6
"""Доля высоты, до которой абзацы разводятся отбивками."""

MAX_GAP = 2.0
"""Наибольшая отбивка между абзацами в долях кегля: шире пункты списка
перестают читаться как один список."""


def own_frame(slot: Slot) -> bool:
    """Рамку создаёт вёрстка, а не шаблон: у слота нет родного плейсхолдера."""
    return slot.origin != "placeholder"


def growable(slot: Slot) -> bool:
    """Кегль тела шаблон не задавал: рамка наша, а кегль унаследован от мастера или темы.

    Кегль, заданный автором шаблона у самой фигуры, — его решение, и
    вёрстка его не меняет.
    """
    style = slot.text_style
    inherited = style is None or style.resolved_from == "designTokens" or style.resolved_from.startswith(WEAK_LEVELS)
    return slot.kind == "body" and own_frame(slot) and inherited


def bullet_for(slot: Slot, paragraphs: list[str], bullet: BulletToken | None) -> BulletToken | None:
    """Маркер списка: только в своей рамке и только у перечня из двух пунктов и больше."""
    if bullet is None or not own_frame(slot) or slot.kind != "body" or len(paragraphs) < 2:
        return None
    return bullet


def with_bullet(frame: TextFrame | None, bullet: BulletToken | None) -> TextFrame:
    """Рамка с висячим отступом под маркер: вместимость считается за его вычетом."""
    frame = frame or TextFrame()
    if bullet is None:
        return frame
    return frame.model_copy(update={"indent": frame.indent + bullet.indent})


def ceiling(title_size: float | None, scale: list[float]) -> float | None:
    """Наибольший кегль тела рядом с заголовком данного кегля."""
    if not title_size:
        return None
    allowed = [value for value in scale if value * TITLE_RATIO <= title_size]
    return max(allowed) if allowed else None


def grown(
    paragraphs: list[str],
    bounds: Bounds,
    canvas: Canvas,
    frame: TextFrame,
    size: float,
    scale: list[float],
    limit: float | None,
) -> float:
    """Наибольший кегль шкалы не выше потолка, при котором текст свободно помещается."""
    if limit is None or size >= limit:
        return size
    room = room_height(bounds, canvas, frame)
    best = size
    for candidate in sorted(value for value in scale if size < value <= limit):
        if text_height(paragraphs, bounds, canvas, candidate, frame) > GROW_FILL * room:
            break
        if not words_fit(paragraphs, bounds, canvas, candidate, frame):
            break
        best = candidate
    return best


def spread(
    paragraphs: list[str], bounds: Bounds, canvas: Canvas, frame: TextFrame, size: float
) -> tuple[TextFrame, Literal["top", "middle"]]:
    """Развести абзацы отбивками и поставить блок по середине, если он мал для места."""
    room = room_height(bounds, canvas, frame)
    height = text_height(paragraphs, bounds, canvas, size, frame)
    if len(paragraphs) > 1 and height < SPREAD_FILL * room:
        gap = min((SPREAD_FILL * room - height) / len(paragraphs), MAX_GAP * size)
        frame = frame.model_copy(update={"space_before_pt": frame.space_before_pt + round(gap, 1)})
        height = text_height(paragraphs, bounds, canvas, size, frame)
    return frame, "middle" if height < SPREAD_FILL * room else "top"
