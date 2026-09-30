"""Проверки шаблона — раздел «Шаблон» Приложения 1 ТЗ (T-29).

Шесть проверок класса `file`: сверяют оформление слайда с правилами,
выведенными из фактической разметки шаблона. Седьмая проверка раздела,
контраст 4,5:1, гибридная и делается задачей T-32.

**Правила — не наши.** Состав гарнитур, палитра и типографическая шкала
извлечены частотным анализом разметки, а не взяты из головы и не списаны с
темы: во всех четырёх проверенных шаблонах `theme1.xml` расходится с
оформлением. Проверка предъявляет шаблону его собственные правила.

**Незаполненное свойство — не нарушение.** До 59% текстовых прогонов реальных
шаблонов не несут явной гарнитуры и кегля: их разрешает цепочка наследования.
Требовать их значило бы объявить дефектом нормальный шаблон — ровно та
ошибка, на которой падает presenton.

**Уверенность правила передаётся находке и может её понизить.** Уверенность
ведущего токена говорит, насколько единообразно размечен шаблон. Там, где
ведущая гарнитура набирает пятую часть разметки, шаблон о своих правилах
почти ничего не сообщает, и то же отклонение показывается рекомендацией, а
не нарушением. Иначе система обвиняла бы пользователя в нарушении правила,
которое сама вывела предположительно.
"""

from __future__ import annotations

from collections import Counter

from dpd.audit.fixer import elements_of, fixes, slide_of, slot_bounds
from dpd.audit.registry import CheckSpec, check, param
from dpd.models import (
    Bounds,
    DesignTokens,
    Finding,
    RenderedElement,
    RenderedPresentation,
    Severity,
    TemplateSchema,
)

FONT_NOT_IN_SET = CheckSpec(
    id="template.font_not_in_set",
    category="template",
    check_class="file",
    severity="warning",
    fixability="mechanical",
    sublayer="4b",
    title="Гарнитура не из фактического состава шаблона",
    plain="Шрифт не из шаблона",
)

TOO_MANY_FACES = CheckSpec(
    id="template.too_many_faces",
    category="template",
    check_class="file",
    severity="warning",
    fixability="mechanical",
    sublayer="4b",
    title="Больше двух гарнитур на слайде",
    plain="На слайде больше двух шрифтов",
)

SIZE_NOT_IN_SCALE = CheckSpec(
    id="template.size_not_in_scale",
    category="template",
    check_class="file",
    severity="warning",
    fixability="mechanical",
    sublayer="4b",
    title="Кегль не из типографической шкалы",
    plain="Размер текста не из тех, что есть в шаблоне",
)

COLOR_NOT_IN_PALETTE = CheckSpec(
    id="template.color_not_in_palette",
    category="template",
    check_class="file",
    severity="warning",
    fixability="mechanical",
    sublayer="4b",
    title="Цвет не из палитры шаблона",
)

LAYOUT_NOT_FROM_TEMPLATE = CheckSpec(
    id="template.layout_not_from_template",
    category="template",
    check_class="file",
    severity="critical",
    fixability="none",
    sublayer="4b",
    title="Слайд собран не на макете из шаблона",
)

FIXED_ELEMENT_MOVED = CheckSpec(
    id="template.fixed_element_moved",
    category="template",
    check_class="file",
    severity="advice",
    fixability="mechanical",
    sublayer="4b",
    title="Логотип или колонтитул потеряли своё место",
)


@check(FONT_NOT_IN_SET)
def check_font_not_in_set(
    deck: RenderedPresentation,
    template: TemplateSchema,
    confidence_floor: float | None = None,
) -> list[Finding]:
    """Найти гарнитуры, которых в шаблоне нет.

    Состав шаблона — то, чем он действительно набран, а не то, что объявляет
    тема: она объявляет Arial при фактическом Play во всех проверенных
    шаблонах.
    """
    confidence_floor = _threshold(confidence_floor, FONT_NOT_IN_SET.id, "confidence_floor")
    tokens = template.design_tokens
    families = {token.family for token in tokens.fonts} if tokens and tokens.fonts else set()
    if not families:
        return []

    confidence = _leading(tokens, "fonts")
    severity = _weakened(FONT_NOT_IN_SET.severity, confidence, confidence_floor)

    findings: list[Finding] = []
    for number, slide in enumerate(deck.slides, start=1):
        for element in slide.elements:
            for font in _fonts(element):
                if font in families:
                    continue
                findings.append(
                    FONT_NOT_IN_SET.finding(
                        f"Слайд {number}: гарнитура «{font}» не из состава шаблона "
                        f"({', '.join(sorted(families))}).",
                        severity=severity,
                        slide_number=number,
                        slot_id=element.slot_id,
                        confidence=confidence,
                        evidence={"actual": font, "expected": sorted(families)},
                    )
                )
    return findings


@check(TOO_MANY_FACES)
def check_too_many_faces(deck: RenderedPresentation, max_faces: int | None = None) -> list[Finding]:
    """Найти слайды, набранные больше чем двумя гарнитурами.

    Счёт идёт **по слайду, а не по шаблону**: шаблон вправе содержать хоть
    десять гарнитур — в VK Tech их три, и все его собственные, — а слайд,
    на котором сошлись три, выглядит собранным наспех.

    Шаблон здесь не нужен: правило наше и не зависит от его разметки.
    """
    max_faces = int(_threshold(max_faces, TOO_MANY_FACES.id, "max_faces"))

    findings: list[Finding] = []
    for number, slide in enumerate(deck.slides, start=1):
        faces = {font for element in slide.elements for font in _fonts(element)}
        if len(faces) <= max_faces:
            continue
        findings.append(
            TOO_MANY_FACES.finding(
                f"Слайд {number} набран {len(faces)} гарнитурами "
                f"({', '.join(sorted(faces))}) — это больше {max_faces}.",
                slide_number=number,
                evidence={"actual": len(faces), "expected": max_faces, "fonts": sorted(faces)},
            )
        )
    return findings


@check(SIZE_NOT_IN_SCALE)
def check_size_not_in_scale(
    deck: RenderedPresentation,
    template: TemplateSchema,
    tolerance_pt: float | None = None,
) -> list[Finding]:
    """Найти кегли вне типографической шкалы шаблона.

    Шкала уже очищена от значений, порождённых автоподгонкой (T-16): дробные
    кегли вида 8,12 или 6,75 — след того, что текст не помещался, а не
    решение дизайнера, и принимать их за правило нельзя.
    """
    tolerance_pt = _threshold(tolerance_pt, SIZE_NOT_IN_SCALE.id, "tolerance_pt")
    scale = template.design_tokens.type_scale.values if template.design_tokens else []
    if not scale:
        return []

    findings: list[Finding] = []
    for number, slide in enumerate(deck.slides, start=1):
        for element in slide.elements:
            for size in _sizes(element):
                if any(abs(size - value) <= tolerance_pt for value in scale):
                    continue
                findings.append(
                    SIZE_NOT_IN_SCALE.finding(
                        f"Слайд {number}: кегль {size:g} pt отсутствует в типографической "
                        f"шкале шаблона.",
                        slide_number=number,
                        slot_id=element.slot_id,
                        evidence={"actual": size, "expected": _nearest(scale, size)},
                    )
                )
    return findings


@check(COLOR_NOT_IN_PALETTE)
def check_color_not_in_palette(
    deck: RenderedPresentation,
    template: TemplateSchema,
    delta_e: float | None = None,
    confidence_floor: float | None = None,
) -> list[Finding]:
    """Найти цвета вне палитры шаблона.

    Сравнение идёт по ΔE в пространстве Lab, а не по равенству кодов:
    экспорт и редакторы округляют цвета, и различие, невидимое глазу,
    не должно порождать находку.

    Проверяются и цвета таблиц с диаграммами: визуализация в чужих цветах
    выдаёт слайд, собранный не по шаблону, даже если весь остальной слайд
    безупречен.
    """
    delta_e = _threshold(delta_e, COLOR_NOT_IN_PALETTE.id, "delta_e")
    confidence_floor = _threshold(confidence_floor, COLOR_NOT_IN_PALETTE.id, "confidence_floor")

    tokens = template.design_tokens
    palette = [token.value for token in tokens.colors] if tokens and tokens.colors else []
    if not palette:
        return []

    confidence = _leading(tokens, "colors")
    severity = _weakened(COLOR_NOT_IN_PALETTE.severity, confidence, confidence_floor)
    known = [_lab(value) for value in palette]

    findings: list[Finding] = []
    for number, slide in enumerate(deck.slides, start=1):
        for element in slide.elements:
            for colour in _colours(element):
                distance = min(_distance(_lab(colour), reference) for reference in known)
                if distance <= delta_e:
                    continue
                findings.append(
                    COLOR_NOT_IN_PALETTE.finding(
                        f"Слайд {number}: цвет {colour} не из палитры шаблона "
                        f"(ближайший отличается на ΔE {distance:.1f}).",
                        severity=severity,
                        slide_number=number,
                        slot_id=element.slot_id,
                        confidence=confidence,
                        evidence={"actual": colour, "expected": palette, "deltaE": round(distance, 2)},
                    )
                )
    return findings


@check(LAYOUT_NOT_FROM_TEMPLATE)
def check_layout_not_from_template(
    deck: RenderedPresentation,
    template: TemplateSchema,
) -> list[Finding]:
    """Найти слайды, собранные не на макете шаблона.

    Исправимость `none`: выбрать замену автоматически нельзя — это решение о
    композиции, а не о свойстве. Критичность высшая: слайд вне макетов
    шаблона не является слайдом этого шаблона, чего бы ни говорило его
    оформление.
    """
    known = {layout.id for layout in template.layouts}

    findings: list[Finding] = []
    for number, slide in enumerate(deck.slides, start=1):
        if slide.layout_id in known:
            continue
        findings.append(
            LAYOUT_NOT_FROM_TEMPLATE.finding(
                f"Слайд {number} собран на макете «{slide.layout_id}», которого в шаблоне нет.",
                slide_number=number,
                evidence={"actual": slide.layout_id, "expected": sorted(known)},
            )
        )
    return findings


@check(FIXED_ELEMENT_MOVED)
def check_fixed_element_moved(
    deck: RenderedPresentation,
    template: TemplateSchema,
    min_confidence: float | None = None,
    overlap_tolerance: float | None = None,
) -> list[Finding]:
    """Найти содержимое, занявшее место постоянного элемента макета.

    **Проверка читается не буквально, и это осознанно.** Постоянные элементы
    живут на макете, а не на слайде: сдвинуть логотип наша сборка не может в
    принципе — она добавляет слайды на готовые макеты шаблона. Что она может,
    так это положить содержимое поверх логотипа, и на рендере это тот же
    дефект: логотип пропал со своего места. Такой дефект у нас уже был (T-13).

    Проверка выполняется только при наличии данных: канонические позиции
    негде взять надёжно — оба мастера VK Tech пусты, а изображения разбросаны
    по макетам. Позиция с низкой уверенностью не может служить обвинением,
    отсюда порог; отсюда же критичность `advice`, понижённая решением фазы 6.
    """
    min_confidence = _threshold(min_confidence, FIXED_ELEMENT_MOVED.id, "min_confidence")
    overlap_tolerance = _threshold(overlap_tolerance, FIXED_ELEMENT_MOVED.id, "overlap_tolerance")

    layouts = {layout.id: layout for layout in template.layouts}

    findings: list[Finding] = []
    for number, slide in enumerate(deck.slides, start=1):
        layout = layouts.get(slide.layout_id)
        if layout is None:
            continue

        for fixed in layout.fixed_elements:
            if fixed.confidence < min_confidence:
                continue
            area = _area(fixed.bounds)
            for element in slide.elements:
                covered = _intersection(element.bounds, fixed.bounds)
                if area <= 0 or covered <= overlap_tolerance * area:
                    continue
                findings.append(
                    FIXED_ELEMENT_MOVED.finding(
                        f"Слайд {number}: блок «{element.slot_id}» занял {covered / area:.0%} "
                        f"места постоянного элемента макета ({fixed.kind}).",
                        slide_number=number,
                        slot_id=element.slot_id,
                        confidence=fixed.confidence,
                        evidence={"kind": fixed.kind, "covered": round(covered / area, 4)},
                    )
                )
    return findings


# --- Общее для проверок ---------------------------------------------------


def _threshold(value: float | None, check_id: str, name: str) -> float:
    """Порог из прогона, а при прямом вызове — из конфигурации."""
    return float(value) if value is not None else float(param(check_id, name))


def _leading(tokens: DesignTokens | None, kind: str) -> float:
    """Уверенность ведущего токена — мера единообразия разметки шаблона.

    Она и есть уверенность правила: шаблон, где ведущая гарнитура набирает
    0,99 разметки, говорит о своих правилах внятно, а шаблон, где она
    набирает 0,2, почти ничего не сообщает.
    """
    values = getattr(tokens, kind, None) if tokens else None
    return max((token.confidence for token in values), default=1.0) if values else 1.0


def _weakened(severity: Severity, confidence: float, floor: float) -> Severity:
    """Понизить находку до рекомендации, если правило выведено слабо."""
    return severity if confidence >= floor else "advice"


def _fonts(element: RenderedElement) -> list[str]:
    """Гарнитуры элемента. Пустая гарнитура — наследование, а не нарушение."""
    fonts = [run.font for run in element.runs if run.font]
    if element.table and element.table.font:
        fonts.append(element.table.font)
    if element.chart and element.chart.font:
        fonts.append(element.chart.font)
    return fonts


def _sizes(element: RenderedElement) -> list[float]:
    sizes = [run.size_pt for run in element.runs if run.size_pt]
    if element.table and element.table.size_pt:
        sizes.append(element.table.size_pt)
    if element.chart and element.chart.size_pt:
        sizes.append(element.chart.size_pt)
    return sizes


def _colours(element: RenderedElement) -> list[str]:
    colours = [run.color for run in element.runs if run.color]
    if element.table:
        colours.extend(value for value in (element.table.header_color, element.table.body_color) if value)
    if element.chart:
        colours.extend(element.chart.colors)
    return colours


def _nearest(scale: list[float], size: float, count: int = 3) -> list[float]:
    """Ближайшие ступени шкалы — чтобы находка подсказывала, чем заменить."""
    return sorted(sorted(scale, key=lambda value: abs(value - size))[:count])


def _area(bounds: Bounds) -> float:
    return bounds.w * bounds.h


def _intersection(first: Bounds, second: Bounds) -> float:
    width = min(first.x + first.w, second.x + second.w) - max(first.x, second.x)
    height = min(first.y + first.h, second.y + second.h) - max(first.y, second.y)
    return max(width, 0.0) * max(height, 0.0)


def _lab(colour: str) -> tuple[float, float, float]:
    """Перевести `#RRGGBB` в CIE Lab (D65).

    Сравнивать цвета по кодам нельзя: расстояние в RGB не соответствует
    видимому различию, и допуск, верный для синего, оказался бы неверным для
    зелёного.
    """
    red, green, blue = (int(colour[index : index + 2], 16) / 255 for index in (1, 3, 5))
    linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in (red, green, blue)]

    x = (0.4124 * linear[0] + 0.3576 * linear[1] + 0.1805 * linear[2]) / 0.95047
    y = 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
    z = (0.0193 * linear[0] + 0.1192 * linear[1] + 0.9505 * linear[2]) / 1.08883

    fx, fy, fz = (value ** (1 / 3) if value > 0.008856 else 7.787 * value + 16 / 116 for value in (x, y, z))
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def _distance(first: tuple[float, float, float], second: tuple[float, float, float]) -> float:
    """ΔE по CIE76: для допуска в пару единиц его точности достаточно."""
    return sum((a - b) ** 2 for a, b in zip(first, second, strict=True)) ** 0.5


# --- Исправления (T-33) ----------------------------------------------------
#
# Все значения берутся из самого шаблона: ближайший кегль его шкалы, ближайший
# цвет его палитры, его основная гарнитура. Придуманное значение было бы не
# исправлением, а второй ошибкой поверх первой.


@fixes(SIZE_NOT_IN_SCALE)
def fix_size_not_in_scale(deck, finding, template) -> bool:
    """Привести кегль к ближайшей ступени шкалы шаблона."""
    scale = template.design_tokens.type_scale.values if template and template.design_tokens else []
    actual = finding.evidence.get("actual")
    if not scale or actual is None:
        return False

    target = min(scale, key=lambda value: abs(value - float(actual)))
    changed = False
    for element in elements_of(deck, finding):
        for run in element.runs:
            if run.size_pt == actual:
                run.size_pt, changed = target, True
        for holder in (element.table, element.chart):
            if holder is not None and holder.size_pt == actual:
                holder.size_pt, changed = target, True
    return changed


@fixes(COLOR_NOT_IN_PALETTE)
def fix_color_not_in_palette(deck, finding, template) -> bool:
    """Заменить цвет ближайшим из палитры шаблона.

    Ближайший считается по ΔE в Lab — тем же расчётом, которым проверка
    нашла нарушение: иначе исправление уходило бы не туда, куда смотрела
    проверка.
    """
    palette = [token.value for token in template.design_tokens.colors] if template and template.design_tokens else []
    actual = finding.evidence.get("actual")
    if not palette or not actual:
        return False

    source = _lab(actual)
    target = min(palette, key=lambda value: _distance(source, _lab(value)))

    changed = False
    for element in elements_of(deck, finding):
        for run in element.runs:
            if run.color == actual:
                run.color, changed = target, True
        if element.table is not None:
            if element.table.header_color == actual:
                element.table.header_color, changed = target, True
            if element.table.body_color == actual:
                element.table.body_color, changed = target, True
        if element.chart is not None and actual in element.chart.colors:
            element.chart.colors = [target if value == actual else value for value in element.chart.colors]
            changed = True
    return changed


@fixes(FONT_NOT_IN_SET)
def fix_font_not_in_set(deck, finding, template) -> bool:
    """Заменить чужую гарнитуру основной гарнитурой шаблона."""
    main = _main_font(template)
    actual = finding.evidence.get("actual")
    if main is None or not actual:
        return False
    return _replace_font(elements_of(deck, finding), {actual}, main)


@fixes(TOO_MANY_FACES)
def fix_too_many_faces(deck, finding, template) -> bool:
    """Оставить на слайде две самые частые гарнитуры, остальные — к основной.

    Выбор по частоте, а не по порядку: реже всего встречающаяся гарнитура и
    есть случайная, а две ведущие — замысел, обычно заголовочная и текстовая.
    """
    main = _main_font(template)
    slide = slide_of(deck, finding)
    if main is None or slide is None:
        return False

    counts = Counter(font for element in slide.elements for font in _fonts(element))
    keep = {font for font, _ in counts.most_common(2)}
    extra = set(counts) - keep
    if not extra:
        return False
    return _replace_font(slide.elements, extra, main)


def _main_font(template) -> str | None:
    tokens = template.design_tokens if template else None
    return tokens.fonts[0].family if tokens and tokens.fonts else None


def _replace_font(elements, replaced: set[str], target: str) -> bool:
    changed = False
    for element in elements:
        for run in element.runs:
            if run.font in replaced:
                run.font, changed = target, True
        for holder in (element.table, element.chart):
            if holder is not None and holder.font in replaced:
                holder.font, changed = target, True
    return changed


@fixes(FIXED_ELEMENT_MOVED, touches="geometry")
def fix_fixed_element_moved(deck, finding, template) -> bool:
    """Вернуть блок на место, отведённое ему шаблоном.

    Исправляется только тот случай, когда блок с этого места уехал: тогда
    правильное положение известно из макета. Если блок стоит ровно в своём
    слоте, а слот накрывает логотип, двигать его некуда — это решение автора
    шаблона, и находка остаётся открытой.
    """
    slide = slide_of(deck, finding)
    if slide is None:
        return False

    changed = False
    for element in elements_of(deck, finding):
        place = slot_bounds(template, slide.layout_id, element.slot_id)
        if place is None or place == element.bounds:
            continue
        element.bounds = place.model_copy()
        changed = True
    return changed
