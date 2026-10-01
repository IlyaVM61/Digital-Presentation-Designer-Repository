"""Что отнимает у текста место внутри рамки слота (T-60).

Вместимость слота — не его рамка. Поля рамки (`bodyPr`), отступ текста
абзаца под маркер (`marL`), межстрочный интервал и отбивки абзацев шаблон
задаёт так же, как кегль, — по цепочке наследования:

    плейсхолдер макета (bodyPr, lstStyle) → плейсхолдер мастера того же типа
      → стили мастера (txStyles) → значение по умолчанию OOXML

Плейсхолдер мастера в этой цепочке обязателен: на отладочной Jessica
интервал 115% записан только там, и без него метрика недосчитывала высоту
каждой строки. Без всего этого вёрстка считала вместимость по всей рамке, и в
колонке шириной 0,21 холста список уходил за нижний край, а аудит молчал
(вопрос T22).

Слотам без плейсхолдера экспорт создаёт свою текстовую рамку с полями по
умолчанию и без отступа — их рамка известна и без разбора.

Слой детерминированный: моделей не вызывает.
"""

from __future__ import annotations

from dpd.models import Canvas, TextFrame
from dpd.parsing.inheritance import A, P, _master_style_name

DEFAULT_INSET_X_EMU = 91440
"""Поле рамки слева и справа по умолчанию OOXML — 0,1 дюйма."""

DEFAULT_INSET_Y_EMU = 45720
"""Поле рамки сверху и снизу по умолчанию OOXML — 0,05 дюйма."""

SINGLE_LINE = 1.2
"""Одинарный интервал в долях кегля — то, от чего считается отбивка в долях строки."""

_INSETS = ("lIns", "rIns", "tIns", "bIns")


def text_box_frame(canvas: Canvas) -> TextFrame:
    """Рамка текстового поля, которую экспорт создаёт для слота без плейсхолдера."""
    return TextFrame(
        inset_left=DEFAULT_INSET_X_EMU / canvas.width_emu,
        inset_right=DEFAULT_INSET_X_EMU / canvas.width_emu,
        inset_top=DEFAULT_INSET_Y_EMU / canvas.height_emu,
        inset_bottom=DEFAULT_INSET_Y_EMU / canvas.height_emu,
    )


def resolve_frame(placeholder, layout, canvas: Canvas, size_pt: float | None) -> TextFrame:
    """Рамка плейсхолдера макета, разрешённая по цепочке наследования.

    `size_pt` — кегль слота: отбивка, заданная в долях строки, переводится
    в пункты по нему.
    """
    master_placeholder = _master_placeholder(placeholder)
    bodies = [_body_properties(placeholder), _body_properties(master_placeholder)]
    paragraphs = [
        _first_level(_list_style(placeholder)),
        _first_level(_list_style(master_placeholder)),
        _first_level(_master_text_style(placeholder, layout)),
    ]

    defaults = {"lIns": DEFAULT_INSET_X_EMU, "rIns": DEFAULT_INSET_X_EMU,
                "tIns": DEFAULT_INSET_Y_EMU, "bIns": DEFAULT_INSET_Y_EMU}
    insets = {name: _first_int(bodies, name, defaults[name]) for name in _INSETS}
    indent = max(_first_int(paragraphs, "marL", 0), 0)

    size = size_pt or 18.0
    line_spacing, line_spacing_pt = _line_spacing(paragraphs)
    return TextFrame(
        inset_left=_share(insets["lIns"], canvas.width_emu),
        inset_right=_share(insets["rIns"], canvas.width_emu),
        inset_top=_share(insets["tIns"], canvas.height_emu),
        inset_bottom=_share(insets["bIns"], canvas.height_emu),
        indent=_share(indent, canvas.width_emu),
        line_spacing=line_spacing,
        line_spacing_pt=line_spacing_pt,
        space_before_pt=_spacing(paragraphs, "spcBef", size),
        space_after_pt=_spacing(paragraphs, "spcAft", size),
    )


def _master_placeholder(placeholder):
    """Плейсхолдер мастера, от которого наследует плейсхолдер макета."""
    try:
        return placeholder._base_placeholder
    except (AttributeError, KeyError, ValueError):
        return None


def _body_properties(shape):
    body = _text_body(shape)
    return None if body is None else body.find(A + "bodyPr")


def _list_style(shape):
    body = _text_body(shape)
    return None if body is None else body.find(A + "lstStyle")


def _text_body(shape):
    if shape is None:
        return None
    return shape._element.find(P + "txBody")


def _master_text_style(placeholder, layout):
    master = getattr(layout, "slide_master", None)
    styles = master.element.find(P + "txStyles") if master is not None else None
    return None if styles is None else styles.find(P + _master_style_name(placeholder))


def _first_level(list_style):
    """Свойства абзаца первого уровня: тело слайда пишется на нём."""
    return None if list_style is None else list_style.find(A + "lvl1pPr")


def _first_int(chain, name: str, default: int) -> int:
    for element in chain:
        if element is None:
            continue
        value = element.get(name)
        if value is not None:
            try:
                return int(value)
            except ValueError:
                continue
    return default


def _line_spacing(paragraphs) -> tuple[float, float | None]:
    """Межстрочный интервал: множитель либо точное значение в пунктах."""
    for paragraph in paragraphs:
        spacing = paragraph.find(A + "lnSpc") if paragraph is not None else None
        if spacing is None:
            continue
        percent = spacing.find(A + "spcPct")
        if percent is not None and percent.get("val"):
            return max(int(percent.get("val")) / 100000, 0.01), None
        points = spacing.find(A + "spcPts")
        if points is not None and points.get("val"):
            return 1.0, max(int(points.get("val")) / 100, 0.01)
    return 1.0, None


def _spacing(paragraphs, tag: str, size_pt: float) -> float:
    """Отбивка абзаца в пунктах: из пунктов прямо, из долей строки — по кеглю."""
    for paragraph in paragraphs:
        spacing = paragraph.find(A + tag) if paragraph is not None else None
        if spacing is None:
            continue
        points = spacing.find(A + "spcPts")
        if points is not None and points.get("val"):
            return max(int(points.get("val")) / 100, 0.0)
        percent = spacing.find(A + "spcPct")
        if percent is not None and percent.get("val"):
            return max(int(percent.get("val")) / 100000 * size_pt * SINGLE_LINE, 0.0)
    return 0.0


def _share(value: int, total: int) -> float:
    return min(max(value / total, 0.0), 1.0)
