"""Выбор макета под тип слайда с деградацией.

**Отказ здесь недопустим.** Шаблон комиссии может не иметь макета под нужный
тип: в VK WorkSpace нет ни одного макета с двумя местами под содержимое.
Вёрстка обязана собрать слайд на ближайшем подходящем и сказать об этом, а
не остановить прогон.

Деградация идёт по цепочкам предпочтений, а не «берём что попало»: для
контентного слайда ближайший — разделённый макет, затем титульный; для
титульного — разделитель, затем контентный. Порядок отражает, насколько
сильно пострадает замысел.

Слой детерминированный: моделей не вызывает.
"""

from __future__ import annotations

from dpd.models import Layout, LayoutDecision, LayoutFamily, StructureSlide

ROLE_TO_FAMILY: dict[str, LayoutFamily] = {
    "title": "title",
    "cover": "title",
    "agenda": "content",
    "section": "section",
    "divider": "section",
    "data": "content",
    "quote": "title",
    "process": "split",
    "comparison": "split",
    "summary": "content",
    "closing": "title",
}
"""Роль слайда из замысла → тип макета. Роль вне таблицы считается
контентной: это самый безобидный выбор по умолчанию."""

DEFAULT_FAMILY: LayoutFamily = "content"

FALLBACK_ORDER: dict[LayoutFamily, tuple[LayoutFamily, ...]] = {
    "content": ("split", "title", "section", "blank"),
    "split": ("content", "title", "section", "blank"),
    "title": ("section", "content", "split", "blank"),
    "section": ("title", "content", "split", "blank"),
    "blank": ("content", "split", "title", "section"),
}


def requested_family(slide: StructureSlide) -> LayoutFamily:
    """Какой тип макета нужен слайду по его роли и содержимому."""
    family = ROLE_TO_FAMILY.get(slide.role, DEFAULT_FAMILY)
    # Роль может просить титульный макет, но если контент есть, класть его
    # некуда: содержимое важнее номинального типа. Визуализация — тоже
    # содержимое: заставка с таблицей теряла её молча (найдено рендером T-50).
    has_content = bool(slide.body and slide.body.items) or slide.visualization is not None
    if family in ("title", "section") and has_content:
        return DEFAULT_FAMILY
    return family


def select(
    slide: StructureSlide, layouts: list[Layout], offset: int = 0
) -> tuple[Layout, LayoutDecision]:
    """Выбрать макет и объяснить выбор.

    `offset` выбирает не первый подходящий макет, а следующий за ним —
    так три варианта получают разные макеты там, где шаблон предлагает
    несколько равноценных. Смещение циклическое: если подходящих меньше,
    берётся первый, а не пустота.
    """
    wanted = requested_family(slide)
    needs_visual = slide.visualization is not None
    needs_content = bool(slide.body and slide.body.items) or needs_visual

    exact = _ranked(layouts, wanted, needs_content, needs_visual)
    if exact:
        chosen = exact[offset % len(exact)]
        return chosen, LayoutDecision(
            requested_family=wanted,
            chosen_family=chosen.family,
            layout_id=chosen.id,
            degraded=False,
            reason="макет нужного типа найден",
        )

    for candidate in FALLBACK_ORDER.get(wanted, ()):
        available = _ranked(layouts, candidate, needs_content, needs_visual)
        if available:
            chosen = available[offset % len(available)]
            return chosen, LayoutDecision(
                requested_family=wanted,
                chosen_family=chosen.family,
                layout_id=chosen.id,
                degraded=True,
                reason=(
                    f"в шаблоне нет макета типа «{wanted}»; "
                    f"выбран ближайший — «{candidate}»"
                ),
            )

    # Тип не нашёлся вовсе: берём любой, где есть место под содержимое.
    with_room = [layout for layout in layouts if _has_content_slot(layout)]
    chosen = (with_room or layouts)[0]
    return chosen, LayoutDecision(
        requested_family=wanted,
        chosen_family=chosen.family,
        layout_id=chosen.id,
        degraded=True,
        reason="подходящего типа не нашлось, взят первый макет шаблона",
    )


MIN_VISUAL_AREA = 0.12
"""Доля холста, ниже которой визуализация не читается.

Измерено: в слоте высотой 5,6% холста диаграмма схлопывалась до одной
легенды — столбцов не оставалось вовсе. Таблице и диаграмме нужно место,
которого абзацу текста хватило бы."""


def _ranked(
    layouts: list[Layout],
    family: LayoutFamily,
    needs_content: bool,
    needs_visual: bool = False,
) -> list[Layout]:
    """Макеты нужного типа, пригодные вперёд непригодных.

    Тип макета — не единственный критерий. Макет, помеченный контентным, но
    без места под содержимое, для слайда с текстом бесполезен: текст просто
    пропадёт. Поэтому при равном типе вперёд идут те, куда есть что класть.

    Порядок внутри групп сохраняется — он задан разбором шаблона, и
    перестановка сделала бы выбор невоспроизводимым.
    """
    candidates = [layout for layout in layouts if layout.family == family]
    if not needs_content:
        return candidates

    suitable = [layout for layout in candidates if _has_content_slot(layout)]
    if needs_visual:
        # Визуализации нужно место: в тесном слоте диаграмма схлопывается до
        # легенды, а таблица — до нечитаемой полоски. Макеты без простора
        # отбрасываются, но только если есть из чего выбирать.
        roomy = [layout for layout in suitable if _content_area(layout) >= MIN_VISUAL_AREA]
        suitable = roomy or suitable
    # Среди равных вперёд идут макеты с большим местом под содержимое.
    # Без этого выбирался макет с крошечным слотом, текст ужимался до
    # нечитаемых семи пунктов, и формально всё было по правилам шаблона.
    suitable.sort(key=_content_area, reverse=True)
    return suitable + [layout for layout in candidates if layout not in suitable]


def _has_content_slot(layout: Layout) -> bool:
    return any(slot.kind != "title" for slot in layout.slots)


def _content_area(layout: Layout) -> float:
    """Площадь самого просторного места под содержимое, в долях холста."""
    areas = [
        slot.bounds.w * slot.bounds.h for slot in layout.slots if slot.kind != "title"
    ]
    return max(areas, default=0.0)
