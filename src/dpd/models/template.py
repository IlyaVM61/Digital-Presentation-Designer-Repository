"""`TemplateSchema` — единственный выход слоя парсинга и источник правил шаблона.

**Минимальный срез задачи T-05.** Нормативное описание в
`docs/04-architecture/template-schema.md` шире: `markupQuality` (T-15),
`designTokens` (T-12), `background` (T-17), `variantGroup` (T-18),
`fixedElements`, `guides` и `warnings` появятся своими задачами. Здесь ровно
то, без чего не собрать вертикальный срез T-06 … T-09.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import Field

from dpd.models.common import Bounds, Canvas, Contract
from dpd.models.tokens import DesignTokens

SlotKind = Literal["title", "body", "other"]

BackgroundKind = Literal["solid", "gradient", "image", "inherited"]

ColorScheme = Literal["light", "dark", "unknown"]


class VariantGroup(Contract):
    """Макеты, различающиеся только цветовой схемой.

    Готовый механизм визуального различения вариантов колоды: используются
    родные макеты шаблона. Единственной осью различий быть не может —
    в шаблоне вариаций может не оказаться вовсе (ADR-0004).
    """

    family: str
    siblings: list[str] = Field(default_factory=list)
    differs_by: str = "colorScheme"


class Background(Contract):
    """Фон макета и вычислимость контраста на нём.

    `contrast_computable` равно `False`, когда цвет под текстом статически
    неизвестен — фон-изображение или градиент. Тогда проверка 4.5:1 требует
    рендера и относится к классу `rendered`, а не `file` (решение фазы 6).
    """

    kind: BackgroundKind = "inherited"
    value: str | None = None
    source: str = "master.bg"
    contrast_computable: bool = True


ParsingStrategy = Literal["placeholder-first", "geometry-first", "examples-first"]
"""На что опирается разбор шаблона.

`placeholder-first` — макеты размечены плейсхолдерами и им можно верить;
`examples-first` — примеры ровно покрывают макеты и служат лучшим сигналом;
`geometry-first` — ни того, ни другого нет, тип и слоты выводятся из
геометрии. Последняя работает всегда, просто даёт меньше уверенности."""


class MarkupQuality(Contract):
    """Измерение того, на что в этом файле можно опереться.

    `score` — не для выбора стратегии, а для отчёта пользователю: по нему
    видно, насколько шаблон пригоден как набор правил.
    """

    layouts_total: int = 0
    layouts_with_multiple_slots: int = 0
    layouts_with_unique_name: int = 0
    layouts_with_examples: int = 0
    example_concentration: float = 0.0
    has_picture_placeholders: bool = False
    masters_empty: bool = False
    score: float = 0.0
    strategy: ParsingStrategy = "geometry-first"


LayoutFamily = Literal["title", "section", "content", "split", "blank"]
"""Тип макета, выведенный из структуры слотов.

`title` — заголовок в композиционном центре; `section` — только заголовок,
места под содержимое нет; `content` — заголовок-шапка и одно место под
содержимое; `split` — два и более места; `blank` — слотов нет вовсе."""

SlotOrigin = Literal["placeholder", "shape", "derived"]
"""Как найден слот. Надёжность убывает: плейсхолдер макета, обычная фигура,
сконструированная область. Распознавание из фигур — основной режим, а не
фолбэк: в двух калибровочных шаблонах из трёх у большинства макетов есть
только плейсхолдер заголовка."""


class TemplateSource(Contract):
    """Происхождение схемы: по хешу файла работает кеширование разбора (T-19)."""

    file: str
    hash: str
    parsed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    parser_version: str = "0.1.0"


class TextStyle(Contract):
    """Оформление текста, разрешённое по цепочке наследования.

    `resolved_from` указывает уровень, на котором найден **кегль**, а
    `font_resolved_from` — на котором найдена гарнитура. Нормативная схема
    предусматривает одно поле, но свойства приходят с разных уровней: кегль
    может быть задан явно в прогоне, а гарнитура унаследована от темы. Одно
    общее значение пришлось бы округлять до менее достоверного, и отчёт стал
    бы врать в безопасную сторону вместо того, чтобы говорить правду.

    Значения, взятые с уровня `default`, характеризуют не шаблон, а редактор,
    и в допустимый состав шаблона не попадают.
    """

    font: str | None = None
    size_pt: float | None = None
    color: str | None = None
    resolved_from: str
    font_resolved_from: str | None = None


class Slot(Contract):
    """Место в макете, куда вёрстка кладёт содержимое.

    `placeholder_idx` заполняется только при `origin="placeholder"`. По нему
    экспорт находит родной плейсхолдер макета и кладёт текст в него, а не
    рядом: тогда оформление — шрифт, кегль, цвет — применяется шаблоном
    автоматически. Для слотов, выведенных из обычных фигур (T-13),
    плейсхолдера не существует, и экспорт создаёт текстовую рамку по
    координатам.
    """

    id: str
    kind: SlotKind
    origin: SlotOrigin
    bounds: Bounds
    placeholder_idx: int | None = None
    text_style: TextStyle | None = None


class Layout(Contract):
    """Макет шаблона.

    `name` хранится, но доверия к нему нет: в одном из калибровочных шаблонов
    11 макетов из 15 называются одинаково. Тип макета выводится из структуры
    слотов — поле `family` появится задачей T-14.
    """

    id: str
    name: str
    family: LayoutFamily = "blank"
    family_source: str = "structure"
    background: Background = Field(default_factory=Background)
    color_scheme: ColorScheme = "unknown"
    variant_group: VariantGroup | None = None
    slots: list[Slot] = Field(default_factory=list)


class TemplateSchema(Contract):
    """Перевод произвольного `.pptx` в набор машинно применимых правил.

    Всё, что слои вёрстки и аудита знают о шаблоне, они знают отсюда: свойства,
    которого нет в схеме, для системы не существует.
    """

    schema_version: str = "1.0"
    source: TemplateSource
    canvas: Canvas
    markup_quality: MarkupQuality = Field(default_factory=MarkupQuality)
    design_tokens: DesignTokens | None = None
    layouts: list[Layout] = Field(min_length=1)
