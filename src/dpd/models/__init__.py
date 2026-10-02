"""Контракты между слоями: pydantic-модели, общие для всего пайплайна.

`TemplateSchema`, `PresentationStructure`, `RenderedPresentation`,
`AuditReport`. Здесь же генерируются JSON Schema для валидации ответов
моделей — один инструмент вместо двух.

Координаты в `TemplateSchema` — нормализованные доли, а не EMU: холсты
шаблонов различаются (9144000 против 12192000 EMU при одном 16:9), и пересчёт
в EMU выполняется только при экспорте.

Реализованы контракты вертикального среза — `TemplateSchema`,
`RenderedPresentation`, `PresentationStructure` — и контракты аудита:
`Finding` и `AuditReport`.
"""

from dpd.models.audit import (
    AuditReport,
    Category,
    CheckClass,
    Finding,
    FindingStatus,
    Fixability,
    Severity,
)
from dpd.models.common import Bounds, Canvas, Contract, TextFrame, TextRun
from dpd.models.rendered import (
    Compensation,
    Decision,
    ElementKind,
    LayoutDecision,
    RenderedChart,
    RenderedElement,
    RenderedPresentation,
    RenderedTable,
    Slide,
)
from dpd.models.structure import (
    AxisTitles,
    BodyKind,
    ChartSeries,
    ChartSpec,
    PresentationStructure,
    SlideBody,
    StructureMeta,
    StructureSlide,
    TableSpec,
    Visualization,
)
from dpd.models.template import (
    Background,
    BackgroundKind,
    ColorScheme,
    FixedElement,
    Layout,
    LayoutFamily,
    LayoutPurpose,
    MarkupQuality,
    ParsingStrategy,
    Slot,
    SlotKind,
    SlotOrigin,
    TemplateSchema,
    TemplateSource,
    TextStyle,
    VariantGroup,
)
from dpd.models.tokens import (
    BulletToken,
    ColorToken,
    DesignTokens,
    FontToken,
    Token,
    TypeScale,
)

__all__ = [
    "AuditReport",
    "AxisTitles",
    "Background",
    "BackgroundKind",
    "BodyKind",
    "Bounds",
    "BulletToken",
    "Canvas",
    "Category",
    "ChartSeries",
    "ChartSpec",
    "CheckClass",
    "ColorScheme",
    "ColorToken",
    "Compensation",
    "Contract",
    "Decision",
    "DesignTokens",
    "ElementKind",
    "Finding",
    "FindingStatus",
    "Fixability",
    "FixedElement",
    "FontToken",
    "Layout",
    "LayoutDecision",
    "LayoutFamily",
    "LayoutPurpose",
    "MarkupQuality",
    "ParsingStrategy",
    "PresentationStructure",
    "RenderedChart",
    "RenderedElement",
    "RenderedPresentation",
    "RenderedTable",
    "Severity",
    "Slide",
    "SlideBody",
    "Slot",
    "SlotKind",
    "SlotOrigin",
    "StructureMeta",
    "StructureSlide",
    "TableSpec",
    "TemplateSchema",
    "TemplateSource",
    "TextFrame",
    "TextRun",
    "TextStyle",
    "Token",
    "TypeScale",
    "VariantGroup",
    "Visualization",
]
