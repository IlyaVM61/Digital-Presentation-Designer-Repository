"""Выбор макета под тип слайда с деградацией.

**Отказ здесь недопустим.** Шаблон комиссии может не иметь макета под нужный
тип: в VK WorkSpace нет ни одного макета с двумя местами под содержимое.
Вёрстка обязана собрать слайд на ближайшем подходящем и сказать об этом, а
не остановить прогон.

Деградация идёт по цепочкам предпочтений, а не «берём что попало»: для
контентного слайда ближайший — разделённый макет, затем титульный; для
титульного — разделитель, затем контентный. Порядок отражает, насколько
сильно пострадает замысел.

**В шаблоне с договорённостью об именах сначала решает назначение** (T-62,
ADR-0008): обложка, раздел, обычный слайд, слайд с таблицей, финал. Геометрия
обложки, раздела и финала одна, и без назначения варианты ставили обложку
на финальный макет. Внутри назначения выбор идёт по типу, как без имён.

Слой детерминированный: моделей не вызывает.
"""

from __future__ import annotations

from dpd.models import (
    Layout,
    LayoutDecision,
    LayoutFamily,
    LayoutPurpose,
    Slot,
    StructureSlide,
)

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


ROLE_TO_PURPOSE: dict[str, LayoutPurpose] = {
    "title": "cover",
    "cover": "cover",
    "section": "section",
    "divider": "section",
    "closing": "closing",
}
"""Роль слайда → назначение макета в шаблоне с договорённостью об именах.
Роль вне таблицы — обычный слайд."""

PURPOSE_FALLBACK: dict[LayoutPurpose, tuple[LayoutPurpose, ...]] = {
    "cover": ("section", "regular"),
    "section": ("regular",),
    "closing": ("section", "regular"),
    "table": ("regular",),
    "regular": ("table",),
}
"""Ближайшее назначение, если нужного в шаблоне нет. Обложка и финал друг
друга не подменяют: это и был дефект T-62."""

PURPOSE_NAMES: dict[LayoutPurpose, str] = {
    "cover": "обложка",
    "section": "раздел",
    "regular": "обычный слайд",
    "table": "слайд с таблицей",
    "closing": "финал",
}


def requested_purpose(slide: StructureSlide) -> LayoutPurpose:
    """Какое назначение макета нужно слайду.

    Таблице — слайд с таблицей. Содержимое важнее номинальной роли, как и в
    `requested_family`: раздел или финал со списком в строку подзаголовка не
    помещается и идёт на обычный слайд.
    """
    if _needs_table(slide):
        return "table"
    if bool(slide.body and slide.body.items) or slide.visualization is not None:
        return "regular"
    return ROLE_TO_PURPOSE.get(slide.role, "regular")


def select(
    slide: StructureSlide, layouts: list[Layout], offset: int = 0
) -> tuple[Layout, LayoutDecision]:
    """Выбрать макет и объяснить выбор.

    `offset` выбирает не первый подходящий макет, а следующий за ним —
    так три варианта получают разные макеты там, где шаблон предлагает
    несколько равноценных. Смещение циклическое: если подходящих меньше,
    берётся первый, а не пустота.
    """
    pool, note = _by_purpose(slide, layouts)
    chosen, decision = _by_family(slide, pool, offset)
    if note is None:
        return chosen, decision
    # Назначение сильнее типа: деградацию и её причину называет оно.
    return chosen, decision.model_copy(
        update={"degraded": bool(note), "reason": note or "макет нужного назначения найден по имени"}
    )


def _by_purpose(slide: StructureSlide, layouts: list[Layout]) -> tuple[list[Layout], str | None]:
    """Макеты нужного назначения и причина отступления от него.

    Причина — `None`, если назначения у макетов нет (шаблон без договорённости
    об именах), и пустая строка, если нужное назначение нашлось.

    Пока картинок нет, макет с полем картинки уступает макету того же
    назначения без него (решение владельца 2026-10-01): поле картинки
    осталось бы пустым, и половина слайда пустовала бы.
    """
    if not any(layout.purpose for layout in layouts):
        return layouts, None

    wanted = requested_purpose(slide)
    needs_content = bool(slide.body and slide.body.items) or slide.visualization is not None
    table = _needs_table(slide)
    for purpose in (wanted, *PURPOSE_FALLBACK[wanted]):
        matching = [layout for layout in layouts if layout.purpose == purpose]
        if needs_content:
            matching = [layout for layout in matching if _has_content_slot(layout, table)]
        matching = [layout for layout in matching if not _has_picture_slot(layout)] or matching
        if not matching:
            continue
        if purpose == wanted:
            return matching, ""
        return matching, _purpose_reason(layouts, wanted, purpose)

    return layouts, f"макета назначения «{PURPOSE_NAMES[wanted]}» не нашлось, выбран по геометрии"


def _purpose_reason(layouts: list[Layout], wanted: LayoutPurpose, used: LayoutPurpose) -> str:
    if any(layout.purpose == wanted for layout in layouts):
        return (
            f"у макетов назначения «{PURPOSE_NAMES[wanted]}» нет места под содержимое; "
            f"выбран ближайший — «{PURPOSE_NAMES[used]}»"
        )
    return f"в шаблоне нет макета назначения «{PURPOSE_NAMES[wanted]}»; выбран ближайший — «{PURPOSE_NAMES[used]}»"


def _by_family(
    slide: StructureSlide, layouts: list[Layout], offset: int = 0
) -> tuple[Layout, LayoutDecision]:
    """Выбор по типу макета с деградацией по цепочке типов."""
    wanted = requested_family(slide)
    needs_visual = slide.visualization is not None
    needs_content = bool(slide.body and slide.body.items) or needs_visual
    table = _needs_table(slide)

    # Слайду с содержимым макет, куда его некуда положить, не годится, даже
    # если тип совпал: прогон T-43 потерял так тело десяти слайдов из
    # двенадцати. Поэтому сначала вся цепочка типов проходится по пригодным
    # макетам, и только затем — по любым.
    families = (wanted, *FALLBACK_ORDER.get(wanted, ()))
    for strict in (True, False) if needs_content else (False,):
        for candidate in families:
            available = _ranked(layouts, candidate, needs_content, needs_visual, table)
            if strict:
                available = [layout for layout in available if _has_content_slot(layout, table)]
            if not available:
                continue
            chosen = available[offset % len(available)]
            degraded = candidate != wanted
            return chosen, LayoutDecision(
                requested_family=wanted,
                chosen_family=chosen.family,
                layout_id=chosen.id,
                degraded=degraded,
                reason=_reason(layouts, wanted, candidate) if degraded else "макет нужного типа найден",
            )

    # Тип не нашёлся вовсе: берём любой, где есть место под содержимое.
    with_room = [layout for layout in layouts if _has_content_slot(layout, table)]
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

MIN_VISUAL_HEIGHT = 0.4
"""Доля высоты холста, ниже которой диаграмма сплющивается (T-60).

Площади мало: полоса во всю ширину проходит порог площади, а диаграмма в
ней — полоска с подписями. Замер на двух калибровочных холстах разного
размера (`D:/dpd-out/t60/heights-*`): при высоте рамки 0,1–0,15 холста
остаётся один заголовок диаграммы, при 0,2–0,3 пропадает подпись категории,
с 0,4 диаграмма читается целиком. Так выглядели сплющенные диаграммы
вариантов B и C в T-59. Кегль подписей следует за кеглем шаблона, а тот —
за холстом, поэтому порог — доля холста, а не пункты."""


def _ranked(
    layouts: list[Layout],
    family: LayoutFamily,
    needs_content: bool,
    needs_visual: bool = False,
    table: bool = False,
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

    suitable = [layout for layout in candidates if _has_content_slot(layout, table)]
    cramped: list[Layout] = []
    if needs_visual:
        # Визуализации нужно место: в тесном слоте диаграмма схлопывается до
        # легенды, а таблица — до нечитаемой полоски. Макеты без простора
        # отбрасываются, но только если есть из чего выбирать.
        roomy = [layout for layout in suitable if _fits_visual(layout, table)]
        if roomy:
            # Отброшенные не возвращаются в хвост списка: смещение варианта
            # перебирает список по кругу и уводило визуализацию вариантов B
            # и C на тесный макет, когда просторный был один (T-60).
            cramped = [layout for layout in suitable if layout not in roomy]
            suitable = roomy
    # Среди равных вперёд идут макеты с большим местом под содержимое.
    # Без этого выбирался макет с крошечным слотом, текст ужимался до
    # нечитаемых семи пунктов, и формально всё было по правилам шаблона.
    # Таблице раньше площади — поле таблицы: его автор шаблона отвёл под
    # таблицу сам (T-62).
    suitable.sort(key=lambda layout: (table and _has_table_slot(layout), _content_area(layout, table)), reverse=True)
    return suitable + [layout for layout in candidates if layout not in suitable and layout not in cramped]


def _reason(layouts: list[Layout], wanted: LayoutFamily, candidate: LayoutFamily) -> str:
    if any(layout.family == wanted for layout in layouts):
        return f"у макетов типа «{wanted}» нет места под содержимое; выбран ближайший — «{candidate}»"
    return f"в шаблоне нет макета типа «{wanted}»; выбран ближайший — «{candidate}»"


def _has_content_slot(layout: Layout, table: bool = False) -> bool:
    """Есть ли место под содержимое. Номер слайда и колонтитулы — не оно.

    Таблице место — и поле таблицы (T-62)."""
    return any(slot.kind == "body" or (table and slot.kind == "table") for slot in layout.slots)


def _has_picture_slot(layout: Layout) -> bool:
    return any(slot.kind == "picture" for slot in layout.slots)


def _has_table_slot(layout: Layout) -> bool:
    return any(slot.kind == "table" for slot in layout.slots)


def _needs_table(slide: StructureSlide) -> bool:
    return slide.visualization is not None and slide.visualization.kind == "table"


def visual_slot(layout: Layout, kind: str) -> Slot | None:
    """Место под визуализацию: таблице — поле таблицы, если оно есть (T-62).

    Иначе — самое просторное место под содержимое. Без этого таблица ложилась
    в текстовое поле, а поле таблицы рядом оставалось пустым.
    """
    if kind == "table":
        fields = [slot for slot in layout.slots if slot.kind == "table"]
        if fields:
            return max(fields, key=lambda slot: slot.bounds.w * slot.bounds.h)
    return roomiest_slot(layout)


def roomiest_slot(layout: Layout) -> Slot | None:
    """Самое просторное место под содержимое; при равных — первое по разметке.

    Общее для выбора макета и вёрстки: макет оценивается по этому месту, и
    содержимое ложится в него же. Вёрстка клала в первое место под тело — на
    VK Tech это полоска надзаголовка высотой 5% холста, и тело резалось в
    ней до 7 pt, хотя макет выбран за просторную область рядом (T-60).
    """
    bodies = [slot for slot in layout.slots if slot.kind == "body"]
    if not bodies:
        return None
    return max(bodies, key=lambda slot: slot.bounds.w * slot.bounds.h)


def _content_area(layout: Layout, table: bool = False) -> float:
    """Площадь места под содержимое, в долях холста: самого просторного или, таблице, её поля."""
    slot = visual_slot(layout, "table") if table else roomiest_slot(layout)
    return slot.bounds.w * slot.bounds.h if slot else 0.0


def _fits_visual(layout: Layout, table: bool = False) -> bool:
    """Хватит ли места диаграмме или таблице: и по площади, и по высоте."""
    slot = visual_slot(layout, "table") if table else roomiest_slot(layout)
    return slot is not None and _content_area(layout, table) >= MIN_VISUAL_AREA and slot.bounds.h >= MIN_VISUAL_HEIGHT
