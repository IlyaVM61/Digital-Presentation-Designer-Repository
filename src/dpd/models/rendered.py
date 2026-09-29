"""`RenderedPresentation` — выход слоя вёрстки и вход экспорта.

**Минимальный срез задачи T-05.** Нормативное описание в
`docs/04-architecture/pipeline-architecture.md` шире: `appliedCompensations`
(T-22), элементы-диаграммы и таблицы (T-23, T-24), `layoutFamily` (T-14).
Здесь только текст — ровно то, что нужно вертикальному срезу.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from dpd.models.common import Bounds, Canvas, Contract, TextRun
from dpd.models.template import LayoutFamily

ElementKind = Literal["text"]


class RenderedElement(Contract):
    """Содержимое, уложенное в слот, с окончательной геометрией в долях."""

    slot_id: str
    kind: ElementKind
    bounds: Bounds
    runs: list[TextRun] = Field(default_factory=list)


class LayoutDecision(Contract):
    """Почему слайд собран именно на этом макете.

    `degraded` означает, что макета нужного типа в шаблоне не нашлось и взят
    ближайший. Скрывать это нельзя: пользователь должен понимать, что
    получил, а отчёт — объяснять, почему слайд выглядит не так, как задумано.
    """

    requested_family: LayoutFamily
    chosen_family: LayoutFamily
    layout_id: str
    degraded: bool = False
    reason: str = ""


class Compensation(Contract):
    """Отклонение от замысла, понадобившееся, чтобы содержимое поместилось.

    Поле обязательно по требованию FR-18: пользователь должен узнать, что
    его замысел изменён. Молчаливое уменьшение кегля — то самое поведение,
    за которое к презентациям возникают претензии.
    """

    kind: Literal["fontScale", "truncate"]
    slot_id: str
    from_value: float | None = None
    to_value: float | None = None
    reason: str = ""


class Slide(Contract):
    """Слайд, собранный на макете шаблона."""

    id: str
    layout_id: str
    layout_decision: LayoutDecision | None = None
    elements: list[RenderedElement] = Field(default_factory=list)
    applied_compensations: list[Compensation] = Field(default_factory=list)


class RenderedPresentation(Contract):
    """Колода, готовая к экспорту.

    Один из трёх вариантов вёрстки. Экспорт ничего не досчитывает: все решения
    о композиции, цвете и кегле приняты раньше, здесь доли лишь пересчитываются
    в EMU под холст шаблона.
    """

    variant: str
    template_hash: str
    canvas: Canvas
    slides: list[Slide] = Field(default_factory=list)
