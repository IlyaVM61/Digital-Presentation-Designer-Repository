"""Извлечение дизайн-токенов частотным анализом фактической разметки.

**Тема файла врёт.** Во всех четырёх проверенных шаблонах `theme1.xml`
расходится с оформлением: объявляет Arial, тогда как размечено Play; в одном
шаблоне тема содержит дефолтную палитру Office вместо брендовой. Парсер,
считающий тему источником истины, объявил бы дизайн-системой Arial для всех
трёх калибровочных шаблонов и забраковал бы корректный Play.

Поэтому основной сигнал — частота в фактической разметке, а тема лишь
сверяется с результатом: расхождение попадает в `conflicts_with`.

Слой детерминированный: моделей не вызывает. Здесь нет и не может быть
условий вида «если шаблон такой-то» — считаются вхождения, и побеждает
то, чего больше.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from statistics import median
from pathlib import Path

from pptx import Presentation
from pptx.oxml.ns import qn

from dpd.models.tokens import BulletToken, ColorToken, DesignTokens, FontToken, TypeScale
from dpd.parsing.inheritance import StyleResolver, _master_style_name, _read_theme_fonts

FREQUENCY = "shapes.frequency"
THEME = "theme.fontScheme"

SATURATION_THRESHOLD = 30
"""Разброс каналов RGB, выше которого цвет считается насыщенным, а не
нейтральным. Нужен, чтобы брендовый цвет не терялся за чёрным цветом текста:
по чистой частоте первым всегда оказывается `#000000`."""

MAX_COLORS = 12
MAX_FONTS = 8

MASTER_BODY = "master.txStyles"
"""Запасной источник маркера: стиль тела мастера — объявление, а не разметка."""

DEFAULT_BULLET_INDENT = 0.02
"""Висячий отступ под маркер в долях ширины, когда шаблон его не задал."""


def extract_design_tokens(source) -> DesignTokens:
    """Собрать токены шаблона по частоте их появления в разметке.

    Принимает путь к файлу или уже открытую презентацию: парсер открывает
    файл один раз и передаёт объект, а вызов по пути удобен отдельно.
    Проверяется путь, а не тип презентации, потому что `Presentation` в
    python-pptx — фабричная функция, а не класс.
    """
    presentation = Presentation(str(source)) if isinstance(source, str | Path) else source
    resolver = StyleResolver(presentation)

    fonts: Counter[str] = Counter()
    heading_fonts: Counter[str] = Counter()
    colors: Counter[str] = Counter()
    sizes: Counter[float] = Counter()

    for shape, style in _styles(presentation, resolver):
        if style.font:
            fonts[style.font] += 1
            if _master_style_name(shape) == "titleStyle":
                heading_fonts[style.font] += 1
        if style.color:
            colors[style.color] += 1
        if style.size_pt:
            sizes[style.size_pt] += 1

    theme_fonts = _theme_fonts(presentation)

    return DesignTokens(
        fonts=_font_tokens(fonts, heading_fonts, theme_fonts),
        colors=_color_tokens(colors),
        type_scale=_type_scale(sizes),
        bullet=_bullet_token(presentation),
    )


def _bullet_token(presentation) -> BulletToken | None:
    """Маркер, которым шаблон размечает перечни (T-69).

    Как и шрифт, берётся по частоте в разметке слайдов: мастер объявляет
    маркер для плейсхолдеров тела, но надписи на слайдах шаблона размечены
    своими, и побеждает то, чем шаблон действительно набран. Мастер — запасной
    источник для шаблона без маркированных абзацев на слайдах. Шаблон, не
    размечающий маркеров нигде, остаётся без маркера: навязывать его нельзя.
    """
    width = presentation.slide_width
    counted: Counter[tuple[str, str | None, str | None]] = Counter()
    indents: dict[tuple[str, str | None, str | None], list[float]] = defaultdict(list)
    for slide in presentation.slides:
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            for paragraph in shape.text_frame.paragraphs:
                marker = _marker(paragraph._p.pPr, width) if paragraph.text.strip() else None
                if marker is not None:
                    counted[marker[0]] += 1
                    indents[marker[0]].append(marker[1])

    source = FREQUENCY
    if not counted:
        styles = presentation.slide_master.element.find(qn("p:txStyles"))
        body = styles.find(qn("p:bodyStyle")) if styles is not None else None
        marker = _marker(body.find(qn("a:lvl1pPr")) if body is not None else None, width)
        if marker is None:
            return None
        counted[marker[0]], indents[marker[0]], source = 1, [marker[1]], MASTER_BODY

    (char, font, colour), occurrences = counted.most_common(1)[0]
    measured = [value for value in indents[(char, font, colour)] if value > 0]
    return BulletToken(
        role="bullet",
        sources=[source],
        occurrences=occurrences,
        confidence=round(occurrences / sum(counted.values()), 4),
        char=char,
        font=font,
        color=colour,
        indent=median(measured) if measured else DEFAULT_BULLET_INDENT,
    )


def _marker(properties, width: int) -> tuple[tuple[str, str | None, str | None], float] | None:
    """Маркер абзаца: символ, гарнитура, цвет — и висячий отступ в долях ширины."""
    if properties is None:
        return None
    char = properties.find(qn("a:buChar"))
    if char is None or not char.get("char"):
        return None
    font = properties.find(qn("a:buFont"))
    colour = properties.find(qn("a:buClr"))
    rgb = colour.find(qn("a:srgbClr")) if colour is not None else None
    indent = max(int(properties.get("marL", 0)), -int(properties.get("indent", 0)), 0)
    return (
        (
            char.get("char"),
            font.get("typeface") if font is not None else None,
            f"#{rgb.get('val').upper()}" if rgb is not None else None,
        ),
        indent / width if width else 0.0,
    )


AUTOFIT_REASON = "normAutofit"


def _type_scale(sizes) -> TypeScale:
    """Отделить шкалу шаблона от кеглей, порождённых автоподгонкой.

    PowerPoint при `normAutofit` умножает кегль на дробный коэффициент, и
    получается 6,75 или 8,12 — след того, что текст не поместился, а не
    решение дизайнера. Принять такое за шкалу значит разрешить вёрстке
    ставить кегль 6,75 как «родной для шаблона»: слайд выйдет нечитаемым и
    при этом формально соответствующим правилам.

    Критерий — кратность половине пункта. Дизайнер выбирает 12, 14, 16,
    иногда 10,5; коэффициент автоподгонки почти никогда не даёт такого
    значения. Измерено: в VK Tech отсеивается 20 кеглей из 40, в двух других
    шаблонах — ни одного, и это верно: автоподгонки там нет.
    """
    kept = sorted(value for value in sizes if _is_design_size(value))
    dropped = sorted(value for value in sizes if not _is_design_size(value))
    return TypeScale(
        values=kept,
        excluded=dropped,
        exclusion_reason=AUTOFIT_REASON if dropped else None,
    )


def _is_design_size(value: float) -> bool:
    """Кратен ли кегль половине пункта."""
    return abs(value * 2 - round(value * 2)) < 1e-9


def _styles(presentation, resolver):
    """Разрешённые стили всех текстовых прогонов.

    Основной источник — слайды-примеры: они показывают, как шаблон применяют
    на деле. Если примеров нет, берутся макеты: шаблон без единого примера —
    обычное дело, и отказываться от разбора нельзя.
    """
    found = False
    for slide in presentation.slides:
        for shape, paragraph, run in _runs(slide):
            found = True
            yield shape, resolver.resolve_run(run, shape, slide, paragraph)

    if found:
        return

    for master in presentation.slide_masters:
        for layout in master.slide_layouts:
            for placeholder in layout.placeholders:
                yield placeholder, resolver.resolve_slot(placeholder, layout)


def _runs(slide):
    for shape in slide.shapes:
        if not shape.has_text_frame:
            continue
        for paragraph in shape.text_frame.paragraphs:
            for run in paragraph.runs:
                if run.text.strip():
                    yield shape, paragraph, run


def _theme_fonts(presentation) -> dict[str, str]:
    for master in presentation.slide_masters:
        fonts = _read_theme_fonts(master)
        if fonts:
            return fonts
    return {}


def _font_tokens(
    fonts: Counter[str],
    heading_fonts: Counter[str],
    theme_fonts: dict[str, str],
) -> list[FontToken]:
    if not fonts:
        return []

    total = sum(fonts.values())
    leader = fonts.most_common(1)[0][0]
    heading_leader = heading_fonts.most_common(1)[0][0] if heading_fonts else None

    tokens: list[FontToken] = []
    for family, occurrences in fonts.most_common(MAX_FONTS):
        role = "body" if family == leader else "secondary"
        if family == heading_leader and family != leader:
            role = "heading"

        declared = {
            f"theme.{name}Font": value
            for name, value in theme_fonts.items()
            if value and value != family
        }
        sources = [FREQUENCY]
        if family in theme_fonts.values():
            sources.append(THEME)

        tokens.append(
            FontToken(
                role=role,
                family=family,
                sources=sources,
                conflicts_with=declared,
                occurrences=occurrences,
                confidence=_confidence(occurrences, total, confirmed=THEME in sources),
            )
        )
    return tokens


def _color_tokens(colors: Counter[str]) -> list[ColorToken]:
    if not colors:
        return []

    total = sum(colors.values())
    ordered = colors.most_common(MAX_COLORS)
    brand = next((value for value, _ in ordered if _is_saturated(value)), None)
    leader = ordered[0][0]

    tokens: list[ColorToken] = []
    accent = 0
    for value, occurrences in ordered:
        if value == brand:
            role = "brand.primary"
        elif value == leader:
            role = "text.primary"
        else:
            accent += 1
            role = f"accent.{accent}"
        tokens.append(
            ColorToken(
                role=role,
                value=value,
                sources=[FREQUENCY],
                occurrences=occurrences,
                confidence=_confidence(occurrences, total, confirmed=False),
            )
        )
    return tokens


def _is_saturated(value: str) -> bool:
    """Отличается ли цвет от серой шкалы.

    Чёрный, белый и серый текста встречаются чаще всего в любом шаблоне, и
    по чистой частоте брендовый цвет никогда не попал бы в лидеры.
    """
    red, green, blue = (int(value[index : index + 2], 16) for index in (1, 3, 5))
    return max(red, green, blue) - min(red, green, blue) > SATURATION_THRESHOLD


THEME_CONFIRMATION_BONUS = 1.15


def _confidence(occurrences: int, total: int, *, confirmed: bool) -> float:
    """Уверенность: доля вхождений, умеренно приподнятая согласием темы.

    Бонус за подтверждение темой **множитель, а не слагаемое**. Слагаемое
    ломает порядок: гарнитура с одним вхождением, случайно совпавшая с темой,
    обгоняла по уверенности гарнитуру с семьюдесятью. А тема — наименее
    надёжный источник из всех: во всех проверенных шаблонах она расходится с
    разметкой, и давать ей перевешивать факты нельзя.

    Полной уверенности не бывает: разметка могла быть случайной. Потолок ниже
    единицы, чтобы это было видно в отчёте.
    """
    share = occurrences / total if total else 0.0
    value = share * (THEME_CONFIRMATION_BONUS if confirmed else 1.0)
    return round(min(max(value, 0.01), 0.99), 2)
