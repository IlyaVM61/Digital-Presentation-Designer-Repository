"""Распознавание слотов макета: из плейсхолдеров, из фигур, из свободной области.

**Слот не равен плейсхолдеру.** В двух калибровочных шаблонах из трёх у
большинства макетов есть только плейсхолдер заголовка, и распознавание фигур
здесь — основной режим работы, а не фолбэк. Без него колода вырождается в
набор заголовков; это проверено прогоном вёрстки, а не предположением.

Порядок источников отражает убывающую надёжность:

1. `placeholder` — плейсхолдер макета, высокая надёжность;
2. `shape` — обычная фигура, предназначенная для текста, средняя;
3. `derived` — область, сконструированная в свободном месте, низкая.

Третий уровень понадобился по измерению: в VK WorkSpace текстовых рамок
всего восемь на пятнадцать макетов, а «обычные фигуры» там — в основном
картинки-декорации. Одними фигурами класть текст оказалось некуда, и
норматив схемы это предусматривает.

Слой детерминированный: моделей не вызывает.
"""

from __future__ import annotations

from pptx.enum.shapes import PP_PLACEHOLDER

from dpd.models import Bounds, Canvas, Slot, SlotKind

TABLE_PLACEHOLDERS = {PP_PLACEHOLDER.TABLE}
"""Плейсхолдеры таблицы: место под таблицу (T-62)."""

PICTURE_PLACEHOLDERS = {PP_PLACEHOLDER.PICTURE, PP_PLACEHOLDER.BITMAP}
"""Плейсхолдеры картинки: пока картинок нет, поле остаётся пустым (T-62)."""

MIN_SLOT_AREA = 0.01
"""Доля холста, ниже которой фигура считается декорацией.

Ложный слот хуже пропущенного: вёрстка положит в него текст, и он уедет в
угол поверх логотипа. Измерено: в VK WorkSpace подписи и значки занимают
0,2–1% холста, контентные рамки — от 3,7%."""

MAX_SLOT_AREA = 0.9
"""Выше этой доли фигура — фон, а не место для содержимого."""

MIN_OBSTACLE_AREA = 0.004
"""Доля холста, начиная с которой элемент макета мешает положить текст.

Порог ниже, чем у слота: логотип размером в полпроцента холста слотом не
станет, но текст поверх него класть нельзя."""

MIN_DERIVED_AREA = 0.08
"""Ниже этой доли конструировать слот бессмысленно: текст туда не поместится."""

WIDE_OBSTACLE = 0.5
"""Доля ширины холста, начиная с которой элемент перекрывает полосу целиком."""

MIN_DERIVED_WIDTH = 0.3
"""Уже этой доли слот бессмысленен: строка не поместится."""

DERIVED_MARGIN = 0.06
"""Горизонтальное поле сконструированной области в долях холста."""

GAP = 0.02
"""Зазор между сконструированным слотом и тем, что уже занято."""


def shape_slots(
    layout,
    canvas: Canvas,
    taken: list[Slot],
    counters: dict[str, int],
    resolver=None,
) -> list[Slot]:
    """Слоты, выведенные из обычных фигур макета.

    Берутся фигуры, предназначенные для текста (`_meant_for_text`): это
    заявление автора шаблона о том, что здесь предполагается текст.
    Плейсхолдеры пропускаются, они уже разобраны и надёжнее.
    """
    occupied = [slot.bounds for slot in taken]
    slots: list[Slot] = []

    for shape in _walk(layout.shapes):
        if _is_placeholder(shape) or not _meant_for_text(shape):
            continue
        bounds = _bounds_of(shape, canvas)
        if bounds is None or not _is_plausible_slot(bounds):
            continue
        if any(_overlaps(bounds, other) for other in occupied):
            continue

        kind: SlotKind = "body"
        slots.append(
            Slot(
                id=_next_id(kind, counters),
                kind=kind,
                origin="shape",
                bounds=bounds,
                text_style=resolver.resolve_slot(shape, layout) if resolver else None,
            )
        )
        occupied.append(bounds)

    return slots


def obstacles(layout, canvas: Canvas) -> list[Bounds]:
    """Постоянные элементы макета: логотипы, плашки, колонтитулы.

    Конструируемый слот обязан их обходить. Без этого текст ложился поверх
    логотипа «VK WorkSpace» в нижней части слайда — дефект, который в схеме
    не виден, а на рендере бросается в глаза.

    Фигуры мастера тоже препятствия, если макет их показывает: на слайде они
    видны так же, как свои. Без этого слот на макете из одного заголовка
    ложился поверх декоративной полосы мастера (найдено прогоном T-43).

    Поля таблицы и картинки — тоже препятствия, хотя это плейсхолдеры (T-62):
    место под текст строилось поверх них, и макет из таблицы и картинки
    брался под все текстовые слайды колоды.

    Фон (почти весь холст) препятствием не считается: обходить его некуда.
    """
    shapes = list(_walk(layout.shapes))
    if _shows_master_shapes(layout):
        shapes += _walk(layout.slide_master.shapes)
    found: list[Bounds] = []
    for shape in shapes:
        if _is_placeholder(shape) and not _is_visual_field(shape):
            continue
        bounds = _bounds_of(shape, canvas)
        if bounds is None:
            continue
        area = bounds.w * bounds.h
        if MIN_OBSTACLE_AREA <= area <= MAX_SLOT_AREA:
            found.append(bounds)
    return found


def derived_slot(
    canvas: Canvas,
    taken: list[Slot],
    counters: dict[str, int],
    blocked: list[Bounds] | None = None,
    text_style=None,
) -> Slot | None:
    """Слот, сконструированный в самой высокой свободной полосе холста.

    Нужен там, где автор шаблона не разметил место под содержимое вовсе —
    а это большинство макетов одного из калибровочных шаблонов. Надёжность
    низкая и отмечена в `origin`: мы предполагаем, а не читаем.

    Полоса ищется по вертикали: горизонтальную раскладку без понимания
    композиции угадать нельзя, а вертикальная — обычный порядок чтения.
    """
    if not taken:
        return None

    blocked = list(blocked or [])
    floor = _content_starts_at(taken)

    # Полосу перекрывает только то, что тянется через слайд. Картинка,
    # занимающая треть ширины сбоку, не повод отказываться от места: она
    # повод сузить слот. Без этого четыре макета VK WorkSpace оставались с
    # одним слотом из-за боковой иллюстрации.
    wide = [b for b in [*(slot.bounds for slot in taken), *blocked] if b.w > WIDE_OBSTACLE]
    free = _tallest_free_band(wide, floor)
    if free is None:
        return None

    top, bottom = free
    left, right = _free_columns(blocked, top, bottom)
    if right - left < MIN_DERIVED_WIDTH:
        return None

    height = bottom - top
    bounds = Bounds(x=left, y=top, w=right - left, h=height)
    if bounds.w * bounds.h < MIN_DERIVED_AREA:
        return None

    kind: SlotKind = "body"
    return Slot(
        id=_next_id(kind, counters),
        kind=kind,
        origin="derived",
        bounds=bounds,
        text_style=text_style,
    )


def _free_columns(blocked: list[Bounds], top: float, bottom: float) -> tuple[float, float]:
    """Горизонтальные границы полосы, свободные от боковых элементов.

    Слот прижимается к той стороне, где больше места: иллюстрация у края —
    обычный приём шаблона, и текст должен встать рядом с ней, а не поверх.

    Элементы, стоящие вплотную друг к другу, сдвигают границу цепочкой, и
    проход повторяется, пока граница движется: поля таблицы и картинки,
    поделившие ширину, иначе сдвигали её одно, и место строилось поверх
    второго — в зависимости от того, какое стоит в разметке первым (T-62).
    """
    left, right = DERIVED_MARGIN, 1 - DERIVED_MARGIN
    moved = True
    while moved:
        moved = False
        for bounds in blocked:
            if bounds.y >= bottom or bounds.y + bounds.h <= top:
                continue
            if bounds.x <= left and bounds.x + bounds.w < right and bounds.x + bounds.w + GAP > left:
                left, moved = bounds.x + bounds.w + GAP, True
            elif bounds.x + bounds.w >= right and bounds.x > left and bounds.x - GAP < right:
                right, moved = bounds.x - GAP, True
    return left, min(max(right, left), 1.0)


def _content_starts_at(taken: list[Slot]) -> float:
    """Граница, ниже которой ищется место под содержимое.

    Содержимое идёт после заголовка — это обычный порядок чтения, а не
    вкусовое решение. Без этого ограничения самая высокая свободная полоса
    оказывалась над заголовком: текст ложился в верх слайда поверх
    декоративного орнамента, а заголовок оставался под ним.
    """
    titles = [slot.bounds for slot in taken if slot.kind == "title"]
    return max((bounds.y + bounds.h for bounds in titles), default=0.0)


def _tallest_free_band(occupied: list[Bounds], floor: float = 0.0) -> tuple[float, float] | None:
    """Самый высокий вертикальный промежуток ниже заданной границы."""
    spans = sorted((bounds.y, bounds.y + bounds.h) for bounds in occupied)

    best: tuple[float, float] | None = None
    cursor = floor
    for start, end in [*spans, (1.0, 1.0)]:
        if end < floor:
            continue
        gap_top, gap_bottom = cursor + GAP, start - GAP
        if gap_bottom - gap_top > (best[1] - best[0] if best else 0):
            best = (gap_top, gap_bottom)
        cursor = max(cursor, end)

    if best is None or best[1] <= best[0]:
        return None
    return max(best[0], 0.0), min(best[1], 1.0)


def _walk(shapes):
    """Фигуры верхнего уровня. Внутрь групп намеренно не заходим.

    У фигуры внутри группы координаты заданы в системе координат группы
    (`chOff`/`chExt`), а не слайда. Измерено: рамка в макете VK WorkSpace
    отдаёт `y=1.554` — полторы высоты холста. Прочитанная как есть, она даёт
    слот, уехавший за нижний край, и текст в нём попросту не виден.

    Пересчёт системы координат группы вместе с её масштабом и поворотом
    возможен, но при вышедших сроках не оправдан: группа в шаблонах — почти
    всегда декоративный композит, а место под содержимое, если оно не
    размечено, надёжнее сконструировать (`origin="derived"`).
    """
    yield from shapes


def _meant_for_text(shape) -> bool:
    """Фигура предназначена для текста: это надпись или в ней уже есть текст.

    Текстовой рамки самой по себе мало: в python-pptx она есть у любой
    автофигуры, включая пустой декоративный параллелограмм. Пустой декор,
    принятый за слоты, делал макет «разделённым», вёрстка его не выбирала, и
    колода прогона T-43 осталась из одних заголовков.
    """
    if not shape.has_text_frame:
        return False
    if shape.text_frame.text.strip():
        return True
    return shape._element.xpath("./p:nvSpPr/p:cNvSpPr/@txBox") in (["1"], ["true"])


def _shows_master_shapes(layout) -> bool:
    """Видны ли на макете фигуры мастера: атрибут `showMasterSp`, по умолчанию да."""
    return layout._element.get("showMasterSp") not in ("0", "false")


def _is_visual_field(shape) -> bool:
    """Плейсхолдер таблицы или картинки: поле, куда ляжет не текст."""
    return shape.placeholder_format.type in TABLE_PLACEHOLDERS | PICTURE_PLACEHOLDERS


def _is_placeholder(shape) -> bool:
    try:
        return bool(shape.is_placeholder)
    except (AttributeError, ValueError):
        return False


def _bounds_of(shape, canvas: Canvas) -> Bounds | None:
    values = (shape.left, shape.top, shape.width, shape.height)
    if any(value is None for value in values):
        return None
    left, top, width, height = values
    return Bounds(
        x=_fraction(left, canvas.width_emu),
        y=_fraction(top, canvas.height_emu),
        w=_fraction(width, canvas.width_emu),
        h=_fraction(height, canvas.height_emu),
    )


def _is_plausible_slot(bounds: Bounds) -> bool:
    area = bounds.w * bounds.h
    return MIN_SLOT_AREA <= area <= MAX_SLOT_AREA


def _overlaps(first: Bounds, second: Bounds) -> bool:
    """Пересекаются ли прямоугольники существенно.

    Слот, наложенный на уже найденный, — не второе место для текста, а та же
    область, размеченная дважды.
    """
    horizontal = min(first.x + first.w, second.x + second.w) - max(first.x, second.x)
    vertical = min(first.y + first.h, second.y + second.h) - max(first.y, second.y)
    if horizontal <= 0 or vertical <= 0:
        return False
    smaller = min(first.w * first.h, second.w * second.h)
    return smaller > 0 and (horizontal * vertical) / smaller > 0.5


def _fraction(value: int, total: int) -> float:
    return min(max(value / total, 0.0), 1.0)


def _next_id(kind: SlotKind, counters: dict[str, int]) -> str:
    counters[kind] = counters.get(kind, 0) + 1
    if kind == "title" and counters[kind] == 1:
        return "title"
    return f"{kind}-{counters[kind]}"


def inherit_colour_from_title(slots: list[Slot]) -> None:
    """Дать слотам без своего цвета цвет заголовка того же макета.

    Заголовок — единственное место, где шаблон явно сказал, каким цветом
    писать **на этом фоне**. Мастер такого сказать не может: в VK WorkSpace
    он объявляет чёрный текст для всех макетов, включая тёмные, и слот,
    послушавший мастер, получил бы чёрный текст на чёрном фоне.

    Дефект был найден рендером, а не разбором данных: в схеме всё выглядело
    правдоподобно, а на изображении текста просто не было.
    """
    title = next(
        (slot for slot in slots if slot.kind == "title" and slot.text_style), None
    )
    if title is None or not title.text_style.color:
        return

    for index, slot in enumerate(slots):
        if slot.origin == "placeholder":
            continue
        if slot.text_style is None:
            slots[index] = slot.model_copy(update={"text_style": title.text_style.model_copy()})
        else:
            # Цвет заменяется, а не дополняется: свой цвет такие слоты
            # получают от мастера, а мастер объявляет один цвет на все
            # макеты шаблона и про тёмный фон конкретного макета не знает.
            slots[index] = slot.model_copy(
                update={
                    "text_style": slot.text_style.model_copy(
                        update={"color": title.text_style.color}
                    )
                }
            )
