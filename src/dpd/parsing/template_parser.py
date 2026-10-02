"""Минимальный разбор `.pptx`: холст, макеты, слоты из плейсхолдеров.

**Задачи T-06, T-11, T-12, T-13.** Разбираются холст, макеты и слоты трёх
происхождений; свойства текста разрешаются по цепочке наследования; токены
извлекаются частотным анализом разметки. Не делается: классификация макетов
(T-14), метрика качества разметки (T-15), очистка типографической шкалы
(T-16), вид фона (T-17) и группировка вариантов (T-18).

Слой детерминированный: моделей не вызывает.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from pptx import Presentation
from pptx.enum.shapes import PP_PLACEHOLDER

from dpd.models import (
    Bounds,
    Canvas,
    Layout,
    Slot,
    SlotKind,
    TemplateSchema,
    TemplateSource,
)
from dpd.parsing.background import detect
from dpd.parsing.cache import load as load_cached
from dpd.parsing.cache import store as store_cached
from dpd.parsing.families import classified, named
from dpd.parsing.frames import resolve_frame, text_box_frame
from dpd.parsing.inheritance import StyleResolver
from dpd.parsing.quality import measure
from dpd.parsing.slots import (
    PICTURE_PLACEHOLDERS,
    TABLE_PLACEHOLDERS,
    derived_slot,
    inherit_colour_from_title,
    obstacles,
    shape_slots,
)
from dpd.parsing.tokens import extract_design_tokens
from dpd.parsing.variants import colour_scheme, group

PARSER_VERSION = "0.4.1"

_TITLE_PLACEHOLDERS = {PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE}
_BODY_PLACEHOLDERS = {PP_PLACEHOLDER.BODY, PP_PLACEHOLDER.SUBTITLE, PP_PLACEHOLDER.OBJECT}


def parse_template(path: str | Path, use_cache: bool = True) -> TemplateSchema:
    """Разобрать шаблон презентации в `TemplateSchema`.

    Результат кешируется по хешу файла и версии парсера. `use_cache=False`
    нужен при отладке разбора: иначе правки кода не видны, пока кеш цел.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"шаблон не найден: {path}")

    if use_cache:
        cached = load_cached(path)
        if cached is not None:
            return cached

    presentation = Presentation(str(path))
    canvas = Canvas(
        width_emu=presentation.slide_width,
        height_emu=presentation.slide_height,
    )

    resolver = StyleResolver(presentation)
    layouts = [
        _parse_layout(layout, master_number, canvas, resolver)
        for master_number, master in enumerate(presentation.slide_masters, start=1)
        for layout in master.slide_layouts
    ]
    # Имя сильнее геометрии, только если договорённости следуют все макеты
    # шаблона (ADR-0008): решается по всем сразу, а не по одному.
    layouts = named(layouts)
    # Цветовые вариации связываются после разбора всех макетов: группа
    # существует только относительно других макетов того же типа.
    layouts = group([
        layout.model_copy(update={"color_scheme": colour_scheme(layout)}) for layout in layouts
    ])

    schema = TemplateSchema(
        markup_quality=measure(presentation),
        design_tokens=extract_design_tokens(presentation),
        source=TemplateSource(
            file=path.name,
            hash=f"sha256:{_file_hash(path)}",
            parser_version=PARSER_VERSION,
        ),
        canvas=canvas,
        layouts=layouts,
    )

    if use_cache:
        store_cached(path, schema)
    return schema


def _parse_layout(layout, master_number: int, canvas: Canvas, resolver: StyleResolver) -> Layout:
    """Собрать макет.

    Идентификатор строится из имени части пакета, а не из имени макета:
    имена дублируются массово — в одном калибровочном шаблоне 11 макетов
    из 15 называются одинаково, — и адресовать по ним нельзя.
    """
    master_placeholders = {
        placeholder.placeholder_format.idx: placeholder
        for placeholder in layout.slide_master.placeholders
    }

    slots: list[Slot] = []
    counters: dict[str, int] = {}
    for placeholder in layout.placeholders:
        bounds = _resolve_bounds(placeholder, master_placeholders, canvas)
        if bounds is None:
            continue
        kind = _slot_kind(placeholder)
        style = resolver.resolve_slot(placeholder, layout)
        slots.append(
            Slot(
                id=_slot_id(kind, counters),
                kind=kind,
                origin="placeholder",
                bounds=bounds,
                placeholder_idx=placeholder.placeholder_format.idx,
                text_style=style,
                frame=resolve_frame(placeholder, layout, canvas, style.size_pt),
            )
        )

    # Слот не равен плейсхолдеру: в двух шаблонах из трёх контент размечен
    # обычными фигурами, и это основной режим, а не фолбэк.
    slots.extend(shape_slots(layout, canvas, slots, counters, resolver))

    # Если места под содержимое так и не нашлось, оно конструируется: иначе
    # вёрстка соберёт слайд из одного заголовка. Номер слайда и колонтитулы
    # (`other`) местом под содержимое не считаются (найдено прогоном T-43).
    if not any(slot.kind == "body" for slot in slots):
        constructed = derived_slot(
            canvas,
            slots,
            counters,
            obstacles(layout, canvas),
            resolver.resolve_body_style(layout),
        )
        if constructed is not None:
            slots.append(constructed)

    # Цвет текста для слотов, которых нет в разметке, берётся от заголовка:
    # мастер объявляет один цвет на все макеты, включая тёмные.
    inherit_colour_from_title(slots)

    # Слоту без плейсхолдера экспорт создаёт свою текстовую рамку — с полями
    # по умолчанию и без отступа под маркер (T-60).
    slots = [
        slot if slot.frame is not None else slot.model_copy(update={"frame": text_box_frame(canvas)})
        for slot in slots
    ]

    # Тип выводится из структуры: имена макетов дублируются массово.
    return classified(
        Layout(
            id=layout_id(layout, master_number),
            name=layout.name,
            background=detect(layout, canvas, resolver.theme_colours(layout)),
            slots=slots,
        )
    )


def layout_id(layout, master_number: int) -> str:
    """Идентификатор макета: `master1/layout8`.

    Строится из имени части пакета, а не из имени макета: имена дублируются
    массово — в одном калибровочном шаблоне 11 макетов из 15 называются
    одинаково. Функция публичная, потому что экспорт открывает тот же файл и
    обязан получить те же идентификаторы; продублированное правило рано или
    поздно разошлось бы.
    """
    part_name = Path(str(layout.part.partname)).stem  # slideLayout8
    return f"master{master_number}/{part_name.replace('slideLayout', 'layout')}"


def _slot_kind(placeholder) -> SlotKind:
    placeholder_type = placeholder.placeholder_format.type
    if placeholder_type in _TITLE_PLACEHOLDERS:
        return "title"
    if placeholder_type in _BODY_PLACEHOLDERS:
        return "body"
    # Поля таблицы и картинки — не «прочее» (T-62): таблица ложится в своё
    # поле, а поле картинки, пока картинок нет, остаётся пустым, и выбор
    # макета это учитывает.
    if placeholder_type in TABLE_PLACEHOLDERS:
        return "table"
    if placeholder_type in PICTURE_PLACEHOLDERS:
        return "picture"
    return "other"


def _slot_id(kind: SlotKind, counters: dict[str, int]) -> str:
    counters[kind] = counters.get(kind, 0) + 1
    if kind == "title" and counters[kind] == 1:
        return "title"
    return f"{kind}-{counters[kind]}"


def _resolve_bounds(placeholder, master_placeholders: dict, canvas: Canvas) -> Bounds | None:
    """Геометрия плейсхолдера, при отсутствии — унаследованная от мастера.

    Плейсхолдер макета часто не несёт собственных координат и берёт их у
    одноимённого плейсхолдера мастера. Это не порча файла, а норма OOXML:
    парсер, требующий явных значений, откажет на обычном корпоративном
    шаблоне. Полное разрешение цепочки свойств — задача T-11, здесь
    наследуется только геометрия, без которой слот не выразить.
    """
    geometry = _geometry(placeholder)
    if geometry is None:
        inherited = master_placeholders.get(placeholder.placeholder_format.idx)
        geometry = _geometry(inherited) if inherited is not None else None
    if geometry is None:
        return None

    left, top, width, height = geometry
    return Bounds(
        x=_fraction(left, canvas.width_emu),
        y=_fraction(top, canvas.height_emu),
        w=_fraction(width, canvas.width_emu),
        h=_fraction(height, canvas.height_emu),
    )


def _geometry(shape) -> tuple[int, int, int, int] | None:
    values = (shape.left, shape.top, shape.width, shape.height)
    return None if any(value is None for value in values) else values


def _fraction(value: int, total: int) -> float:
    """Доля холста, прижатая к [0, 1].

    Прижатие нужно, потому что фигуры за краем холста встречаются в реальных
    шаблонах: элемент, уехавший влево, дал бы отрицательную долю, контракт
    отверг бы её, и разбор упал бы на файле, который PowerPoint открывает
    без жалоб. Терять слот целиком хуже, чем сместить его край.
    """
    return min(max(value / total, 0.0), 1.0)


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
